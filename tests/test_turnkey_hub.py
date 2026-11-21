"""Generic appliance mail boundaries, independent of any application."""
import importlib.machinery
import importlib.util
import io
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("turnkey_hub", str(ROOT / "bin/turnkey-hub"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mail = importlib.util.module_from_spec(spec)
loader.exec_module(mail)
LEGACY_DNS = mail.Mail.legacy_dns

# Hub service.enrollment_token emits device.pk + a dot + SHA256 hexdigest.
TOKEN = "a" * 32 + "." + "b" * 64
DEVICE_CODE = "t" * 43
PASSWORD = "p" * 43
DNS_PASSWORD = "d" * 43
BACKUP_PASSWORD = "b" * 43
DAILY_PAIRING = {"control_secret": "c" * 43, "backup_v2_offered": True}
LEGACY_OFFERS = dict(mail.SUPPORTED_SERVICES, backup=[1])
CONFIG = {"version": 1, "device_id": "a" * 32,
          "smtp": {"host": "smtp.hub.turnkeylinux.org", "port": 587, "username": "tkl-" + "a" * 32},
          "sender": "noreply@fixture.tklapp.com", "domain": "fixture.tklapp.com", "status": "ready",
          "dyndns": {"hostname": "fixture.tklapp.com", "update_uri": mail.HUB + "/api/appmail/dns/update"}}


class MailTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("state", "postfix", "systemd"):
            (self.root / name).mkdir()
        self.patches = [patch.object(mail, "HUB_EMAIL_CACHE", self.root / "cache/HUB_ACCOUNT_EMAIL"),
                        patch.object(mail, "ONBOARDING_STATE", self.root / "onboarding.json"),
                        patch.object(mail, "TURNKEY_VERSION", self.root / "turnkey_version"),
                        patch.object(mail, "BUILD_TAGS", self.root / "01turnkey"),
                        patch.object(mail, "STATE_DIR", self.root / "state"),
                        patch.object(mail, "BACKUP_DIR", self.root / "backup"),
                        patch.object(mail, "BACKUP_CRON", self.root / "cron.daily/tklbam-backup"),
                        patch.object(mail, "BACKUP_CRON_REFERENCE", self.root / "packaged-backup"),
                        patch.object(mail, "POSTFIX_DIR", self.root / "postfix"),
                        patch.object(mail, "SYSTEMD_DIR", self.root / "systemd")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.main = self.root / "postfix/main.cf"
        self.main.write_text("myhostname = original.example\nrelayhost = [existing.example]:587\n")
        self.calls = []
        self.fail_command = None

        def run(command, data=None, timeout=30):
            self.calls.append((command, data))
            if command[0] == "postmap":
                Path(command[1][5:] + ".db").write_bytes(b"compiled-map")
            if command == self.fail_command:
                raise mail.Error("command failed", retryable=True)
            return b""

        self.addCleanup(patch.stopall)
        patch.object(mail, "run", side_effect=run).start()
        patch.object(mail, "smtp_check").start()
        patch.object(mail.Mail, "legacy_dns", return_value=True).start()
        self.hub_active = False
        self.client = mail.Mail()

    def hub(self, route, payload):
        if route == "enroll":
            return {"version": 2, "device_id": CONFIG["device_id"], "connection_status": "linked"}
        if route == "configure":
            if "smtp_password" in payload:
                self.hub_active = True
            return {"device_id": CONFIG["device_id"], "connection_status": "linked",
                    "dns": dict(enabled=True, **CONFIG["dyndns"]),
                    "mail": {"enabled": True, "status": "active" if self.hub_active else "secret_required",
                             "grant_id": CONFIG["device_id"], "smtp": CONFIG["smtp"],
                             "domain": CONFIG["domain"], "sender": CONFIG["sender"]},
                    "backup": {"requested": False, "status": "disabled"}}
        return {"status": "updated"}

    def configured(self):
        config = mail.checked_config(CONFIG, PASSWORD)
        config["dns_password"] = DNS_PASSWORD
        self.client.apply(config)
        return config

    def start_connection(self):
        with patch.object(mail, "request", return_value={
                "verification_uri": mail.HUB + "/connect", "user_code": "D34C-F5A9",
                "device_code": DEVICE_CODE, "expires_in": 600, "interval": 5}):
            self.client.start()
        return self.client.state["pairing"]["control_secret"]

    def completion(self, backup=True):
        device = CONFIG["device_id"]
        services = {
            "email": dict(version=1, enabled=True, status="active", grant_id=device, smtp=CONFIG["smtp"],
                          domain=CONFIG["domain"], sender=CONFIG["sender"], credential=PASSWORD),
            "dns": dict(version=1, enabled=True, credential=f"hubdns2.{device}.{DNS_PASSWORD}", **CONFIG["dyndns"]),
        }
        if backup:
            services["backup"] = dict(version=1, requested=True, status="active",
                                      credential=f"tklbam2.{device}.{BACKUP_PASSWORD}")
        return dict(status="linked", device_id=device, account_email="owner@example.net", services=services)

    def stock_backup_job(self, mode=0o644):
        mail.BACKUP_CRON.parent.mkdir(exist_ok=True)
        stock = b"#!/bin/sh\n" + mail.BACKUP_CRON_CAPABILITY + b"\n"
        mail.BACKUP_CRON_REFERENCE.write_bytes(stock)
        mail.BACKUP_CRON.write_bytes(stock)
        mail.BACKUP_CRON.chmod(mode)

    def backup_completion(self, device=CONFIG["device_id"], version=2, enabled=True, account_id=None):
        if enabled:
            entry = dict(version=version, requested=True, status="active",
                         credential=f"tklbam2.{device}.{BACKUP_PASSWORD}")
        else:
            entry = dict(version=version, requested=False, status="disabled")
        response = dict(status="linked", device_id=device, services={"backup": entry})
        if account_id is not None:
            response["backup_account_id"] = account_id
        return response

    def initialized_daily_backup(self, account_id=10, mode=0o644):
        self.stock_backup_job(mode=mode)
        self.client.complete(DAILY_PAIRING, self.backup_completion(account_id=account_id))
        registry = mail.BACKUP_DIR
        (registry / "profile").mkdir(exist_ok=True)
        records = {"secret": "existing encryption secret", "key": "existing key packet",
                   "hbr": "existing backup record", "profile/profile_id": "existing profile",
                   "profile/stamp": "", "credentials": "existing AWS credential cache",
                   "hub-daily-initialized": CONFIG["device_id"],
                   "sub_apikey": f"tklbam2.{CONFIG['device_id']}.{BACKUP_PASSWORD}"}
        for key, value in records.items():
            (registry / key).write_text(value + "\n" if value else "")
        if not mode & 0o111:
            mail.BACKUP_CRON.chmod(mode | 0o100)
        return registry, records

    def test_daily_backup_offer_requires_compatible_stock_cron_and_no_other_frequency(self):
        response = {"verification_uri": mail.HUB + "/connect", "user_code": "D34C-F5A9",
                    "device_code": DEVICE_CODE, "expires_in": 600, "interval": 5}

        def offered(expected):
            with patch.object(mail, "request", return_value=response) as request:
                self.client.start()
            self.assertEqual(request.call_args.args[1]["supported_services"]["backup"], expected)

        offered([1])
        self.stock_backup_job()
        offered([1, 2])

        mail.BACKUP_CRON.write_text("# Local schedule\n")
        offered([1])
        self.stock_backup_job()
        mail.BACKUP_CRON.unlink()
        mail.BACKUP_CRON.symlink_to(mail.BACKUP_CRON_REFERENCE)
        offered([1])
        mail.BACKUP_CRON.unlink()

        for frequency in ("hourly", "weekly"):
            with self.subTest(frequency=frequency):
                self.stock_backup_job()
                alternate = self.root / ("cron." + frequency) / mail.BACKUP_CRON.name
                alternate.parent.mkdir()
                alternate.write_bytes(mail.BACKUP_CRON_REFERENCE.read_bytes())
                alternate.chmod(0o755)
                offered([1])
                alternate.chmod(0o644)
                offered([1, 2])

        for path in (mail.BACKUP_CRON, mail.BACKUP_CRON_REFERENCE):
            path.write_text("#!/bin/sh\nexec tklbam-backup --quiet\n")
        offered([1])

    def test_daily_backup_preparation_uses_existing_completion_and_leaves_cron_dormant(self):
        self.stock_backup_job()
        control = self.start_connection()
        device = CONFIG["device_id"]
        marker = mail.BACKUP_DIR / "hub-managed-daily"
        with patch.object(mail, "request", return_value=self.backup_completion()) as request:
            result = self.client.poll()
        request.assert_called_once_with("complete", {"device_code": DEVICE_CODE, "control_secret": control}, "connections")
        self.assertEqual(result["services"]["backup"]["version"], 2)
        self.assertEqual(marker.read_text(), f"{device} managed\n")
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o644)
        self.assertEqual(list(mail.SYSTEMD_DIR.iterdir()), [])

        with patch.object(mail, "request") as request:
            self.assertEqual(mail.Mail().credential("backup")["credential"], f"tklbam2.{device}.{BACKUP_PASSWORD}")
        request.assert_not_called()

    def test_daily_pairing_rechecks_stock_job_before_completion_request(self):
        marker = mail.BACKUP_DIR / "hub-managed-daily"
        saved = mail.STATE_DIR / "state.json"
        for change in ("edit", "remove", "alternate"):
            with self.subTest(change=change):
                self.stock_backup_job()
                self.client.complete(DAILY_PAIRING, self.backup_completion())
                self.start_connection()
                state_before = saved.read_bytes()
                if change == "edit":
                    mail.BACKUP_CRON.write_text("# Administrator-owned job\n")
                elif change == "remove":
                    mail.BACKUP_CRON.unlink()
                else:
                    alternate = self.root / "cron.hourly/tklbam-backup"
                    alternate.parent.mkdir()
                    alternate.write_bytes(mail.BACKUP_CRON_REFERENCE.read_bytes())
                    alternate.chmod(0o744)

                with patch.object(mail, "request") as request, \
                        self.assertRaisesRegex(mail.Error, "Start a new connection"):
                    self.client.poll()

                request.assert_not_called()
                self.assertEqual(saved.read_bytes(), state_before)
                self.assertEqual(marker.read_text(), CONFIG["device_id"] + " managed\n")

    def test_daily_pairing_rechecks_stock_job_after_completion_response(self):
        self.stock_backup_job()
        self.client.complete(DAILY_PAIRING, self.backup_completion())
        self.start_connection()
        active_link = copy.deepcopy(self.client.state["link"])
        active_services = copy.deepcopy(self.client.state["services"])

        def complete(*args):
            mail.BACKUP_CRON.write_text("# Administrator-owned job\n")
            mail.BACKUP_CRON.chmod(0o640)
            return self.backup_completion(device="b" * 32)

        with patch.object(mail, "request", side_effect=complete) as request, \
                self.assertRaisesRegex(mail.Error, "Start a new connection"):
            self.client.poll()

        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], "complete")
        saved = mail.Mail().state
        self.assertEqual(saved["link"], active_link)
        self.assertEqual(saved["services"], active_services)
        marker = mail.BACKUP_DIR / "hub-managed-daily"
        self.assertEqual(marker.read_text(), CONFIG["device_id"] + " managed\n")
        self.assertEqual(mail.BACKUP_CRON.read_text(), "# Administrator-owned job\n")
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o640)

    def test_daily_pairing_keeps_prior_link_if_preparation_cannot_be_saved(self):
        self.stock_backup_job()
        self.client.complete(DAILY_PAIRING, self.backup_completion())
        marker = mail.BACKUP_DIR / "hub-managed-daily"
        saved = mail.STATE_DIR / "state.json"
        state_before = saved.read_bytes()
        with patch.object(mail, "atomic", side_effect=PermissionError), self.assertRaises(PermissionError):
            self.client.complete(DAILY_PAIRING, self.backup_completion(device="b" * 32))
        self.assertEqual(saved.read_bytes(), state_before)
        self.assertEqual(marker.read_text(), CONFIG["device_id"] + " managed\n")
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o644)

    def test_malformed_daily_backup_service_does_not_prepare_cron(self):
        self.stock_backup_job()
        response = self.backup_completion()
        response["services"]["backup"] = {"version": 2, "requested": True}
        with self.assertRaises(mail.Error):
            self.client.complete(DAILY_PAIRING, response)
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o644)
        self.assertFalse((mail.BACKUP_DIR / "hub-managed-daily").exists())

    def test_daily_backup_controller_preserves_version_while_backup_access_is_off(self):
        self.stock_backup_job()
        control = self.start_connection()
        device = CONFIG["device_id"]
        with patch.object(mail, "request", return_value=self.backup_completion(enabled=False)):
            self.client.poll()
        self.assertEqual((mail.BACKUP_DIR / "hub-managed-daily").read_text(), f"{device} managed\n")
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o644)

        def hub(route, value, namespace="appmail"):
            if route == "configure":
                return dict(device_id=device, connection_status="linked", dns={"enabled": False},
                            mail={"enabled": False}, backup={"requested": False, "status": "disabled"})
            return dict(version=2, requested=False, status="disabled")

        with patch.object(mail, "request", side_effect=hub) as request:
            self.assertEqual(self.client.sync()["services"]["backup"]["version"], 2)
            with self.assertRaises(mail.Error) as caught:
                self.client.credential("backup")
        self.assertEqual(caught.exception.code, "disabled")
        request.assert_called_with("service", {"device_id": device, "control_secret": control,
                                                "service": "backup", "version": 2}, "connections")
        self.assertEqual(request.call_count, 2)

    def test_daily_backup_relink_retains_original_mode_ownership_and_rotates_device(self):
        self.stock_backup_job()
        marker = mail.BACKUP_DIR / "hub-managed-daily"
        proof = DAILY_PAIRING
        self.client.complete(proof, self.backup_completion())
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o644)
        # Simulate a successful TKLBAM initialization enabling the job.
        mail.BACKUP_CRON.chmod(0o744)
        self.client.complete(proof, self.backup_completion())
        next_device = "b" * 32
        self.client.complete(proof, self.backup_completion(device=next_device))
        self.assertEqual(marker.read_text(), f"{next_device} managed\n")
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o744)

        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == marker:
                self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o644)
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            self.client.complete(proof, self.backup_completion(device="e" * 32, version=1))
        self.assertFalse(marker.exists())
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o644)

    def test_same_account_relink_rotates_ready_registry_with_one_request(self):
        registry, records = self.initialized_daily_backup()
        device = "b" * 32
        control = self.start_connection()
        atomic = mail.atomic
        sequence = []

        def write(path, contents, mode=0o600):
            if path.parent == registry:
                sequence.append(path.name)
                if path.name == "sub_apikey":
                    self.assertEqual((registry / "hub-managed-daily").read_text(), f"{device} managed\n")
                    self.assertEqual((registry / "hub-daily-initialized").read_text(), CONFIG["device_id"] + "\n")
            atomic(path, contents, mode)

        with patch.object(mail, "request", return_value=self.backup_completion(device, account_id=10)) as request, \
                patch.object(mail, "atomic", side_effect=write):
            result = self.client.poll()
        request.assert_called_once_with("complete", {"device_code": DEVICE_CODE, "control_secret": control}, "connections")
        self.assertEqual(sequence, ["hub-managed-daily", "sub_apikey", "hub-daily-initialized"])
        self.assertEqual((registry / "sub_apikey").read_text(), f"tklbam2.{device}.{BACKUP_PASSWORD}\n")
        self.assertEqual((registry / "hub-daily-initialized").read_text(), device + "\n")
        self.assertFalse((registry / "credentials").exists())
        for name in ("secret", "key", "hbr", "profile/profile_id", "profile/stamp"):
            self.assertEqual((registry / name).read_text().rstrip(), records[name])
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o744)
        self.assertEqual(mail.Mail().linked()["backup_account_id"], 10)
        self.assertNotIn("backup_account_id", json.dumps(result))

    def test_same_account_relink_replays_after_interruption_during_rotation(self):
        registry, _ = self.initialized_daily_backup()
        device = "b" * 32
        self.start_connection()
        atomic = mail.atomic

        def interrupt(path, contents, mode=0o600):
            if path == registry / "hub-daily-initialized":
                raise OSError("simulated interrupted marker write")
            atomic(path, contents, mode)

        response = self.backup_completion(device, account_id=10)
        with patch.object(mail, "request", return_value=response) as request:
            with patch.object(mail, "atomic", side_effect=interrupt), self.assertRaises(OSError):
                self.client.poll()
            self.assertEqual((registry / "hub-managed-daily").read_text(), f"{device} managed\n")
            self.assertEqual((registry / "sub_apikey").read_text(), f"tklbam2.{device}.{BACKUP_PASSWORD}\n")
            self.assertEqual((registry / "hub-daily-initialized").read_text(), CONFIG["device_id"] + "\n")
            self.client = mail.Mail()
            self.assertEqual(self.client.linked()["device_id"], CONFIG["device_id"])
            self.client.poll(notified=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])
        self.assertEqual((registry / "hub-daily-initialized").read_text(), device + "\n")
        self.assertEqual(mail.Mail().linked()["device_id"], device)

    def test_same_device_replay_disarms_readiness_during_token_change(self):
        registry, _ = self.initialized_daily_backup()
        device = CONFIG["device_id"]
        self.start_connection()
        response = self.backup_completion(account_id=10)
        token = f"tklbam2.{device}.{'n' * 43}"
        response["services"]["backup"]["credential"] = token
        atomic = mail.atomic

        def write(path, contents, mode=0o600):
            if path == registry / "sub_apikey":
                self.assertFalse((registry / "hub-daily-initialized").exists())
            atomic(path, contents, mode)

        with patch.object(mail, "request", return_value=response), patch.object(mail, "atomic", side_effect=write):
            self.client.poll()
        self.assertEqual(mail.backup_file("sub_apikey"), token)
        self.assertEqual(mail.backup_file("hub-daily-initialized"), device)

    def test_relink_stays_dormant_without_same_account_and_initialized_registry(self):
        for changed in ("account", "absent-account", "old-hub", "invalid-account", "grant-off", "ready", "key",
                        "profile", "token", "no-record", "empty-record"):
            with self.subTest(changed=changed):
                registry, _ = self.initialized_daily_backup(account_id=None if changed == "old-hub" else 10)
                next_account = {"account": 11, "absent-account": None, "invalid-account": True}.get(changed, 10)
                paths = {"ready": "hub-daily-initialized", "key": "key", "profile": "profile/stamp",
                         "token": "sub_apikey", "no-record": "hbr"}
                if changed in paths:
                    (registry / paths[changed]).unlink()
                if changed == "empty-record":
                    (registry / "hbr").write_text("\n")
                device = "b" * 32
                self.start_connection()
                result = self.backup_completion(device, account_id=next_account, enabled=changed != "grant-off")
                with patch.object(mail, "request", return_value=result):
                    self.client.poll()
                self.assertEqual((registry / "hub-managed-daily").read_text(), f"{device} managed\n")
                self.assertNotEqual(mail.backup_file("hub-daily-initialized"), device)
                self.assertNotEqual(mail.backup_file("sub_apikey"), f"tklbam2.{device}.{BACKUP_PASSWORD}")
                if type(next_account) is not int:
                    self.assertNotIn("backup_account_id", self.client.linked())
                self.client.reset()

    def test_daily_backup_reset_removes_stale_initialized_device_auth_after_relink(self):
        registry, records = self.initialized_daily_backup(mode=0o750)
        self.client.complete(DAILY_PAIRING, self.backup_completion(device="b" * 32, account_id=11))
        self.assertEqual(mail.backup_file("sub_apikey"), records["sub_apikey"])
        self.client.reset()
        for name in ("hub-managed-daily", "hub-daily-initialized", "sub_apikey", "credentials"):
            self.assertFalse((registry / name).exists())
        self.assertEqual(mail.backup_file("hbr"), records["hbr"])
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o750)

    def test_reconnect_without_backup_grant_clears_old_scoped_credentials(self):
        registry, records = self.initialized_daily_backup()
        next_device = "b" * 32
        self.start_connection()
        response = self.backup_completion(device=next_device, account_id=10, enabled=False)
        with patch.object(mail, "request", return_value=response) as request:
            self.client.poll()
        request.assert_called_once()
        self.assertNotIn("backup_secret", mail.Mail().state)
        for name in ("sub_apikey", "credentials", "hub-daily-initialized"):
            self.assertFalse((registry / name).exists(), name)
        for name in ("hbr", "secret", "key", "profile/profile_id"):
            self.assertEqual(mail.backup_file(name), records[name], name)
        self.assertEqual(mail.backup_file("hub-managed-daily"), f"{next_device} managed")

    def test_reconnect_without_backup_grant_preserves_legacy_backup_credentials(self):
        mail.BACKUP_DIR.mkdir()
        (mail.BACKUP_DIR / "sub_apikey").write_text("independent-legacy-subkey\n")
        (mail.BACKUP_DIR / "credentials").write_text("existing AWS credential cache\n")
        self.stock_backup_job()
        self.client.complete(DAILY_PAIRING, self.backup_completion(enabled=False))
        self.assertEqual(mail.backup_file("sub_apikey"), "independent-legacy-subkey")
        self.assertEqual(mail.backup_file("credentials"), "existing AWS credential cache")

    def test_daily_backup_reset_preserves_an_existing_executable_job_and_history(self):
        self.stock_backup_job(mode=0o750)
        device = CONFIG["device_id"]
        proof = DAILY_PAIRING
        self.client.complete(proof, self.backup_completion())
        marker = mail.BACKUP_DIR / "hub-managed-daily"
        self.assertEqual(marker.read_text(), f"{device} local\n")
        token = mail.BACKUP_DIR / "sub_apikey"
        token.write_text(f"tklbam2.{device}.{BACKUP_PASSWORD}")
        history = mail.BACKUP_DIR / "hbr"
        history.write_text("existing backup record")
        self.client.reset(mail_only=True)
        self.assertTrue(marker.exists())
        self.assertTrue(token.exists())
        self.client.reset()
        self.assertFalse(marker.exists())
        self.assertFalse(token.exists())
        self.assertEqual(history.read_text(), "existing backup record")
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o750)

    def test_daily_backup_reset_preserves_a_job_edited_after_activation(self):
        self.stock_backup_job()
        self.client.complete(DAILY_PAIRING, self.backup_completion())
        mail.BACKUP_CRON.chmod(0o744)
        mail.BACKUP_CRON.write_text("# Administrator-owned job\n")
        self.client.reset()
        self.assertEqual(mail.BACKUP_CRON.read_text(), "# Administrator-owned job\n")
        self.assertEqual(mail.BACKUP_CRON.stat().st_mode & 0o777, 0o744)
        self.assertFalse((mail.BACKUP_DIR / "hub-managed-daily").exists())

    def test_modern_completion_and_background_setup_do_not_discover_or_register_ready_services(self):
        control = self.start_connection()
        self.main.write_text("myhostname = original.example\n")
        self.assertTrue(mail.SERVICE_SECRET.fullmatch(control))
        self.assertEqual(mail.Mail().state["pairing"]["control_secret"], control)
        with patch.object(mail, "request", return_value=self.completion()) as request:
            public = self.client.poll(notified=True, background=True)
        request.assert_called_once_with("complete", {"device_code": DEVICE_CODE, "control_secret": control}, "connections")
        self.assertEqual(public["connection_status"], "linked")
        self.assertTrue(public["setup_pending"])
        self.assertEqual(self.calls, [])
        mail.smtp_check.assert_not_called()
        self.assertEqual(mail.HUB_EMAIL_CACHE.read_text().strip(), "owner@example.net")
        saved = mail.STATE_DIR / "state.json"
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        for secret in (control, PASSWORD, DNS_PASSWORD, BACKUP_PASSWORD, DEVICE_CODE):
            self.assertNotIn(secret, json.dumps(public))

        def initialize_dns(result):
            self.assertTrue(result["services"]["dns"]["enabled"])
            self.assertEqual(mail.Mail().credential("dns")["credential"],
                             f"hubdns2.{CONFIG['device_id']}.{DNS_PASSWORD}")

        with patch.object(mail, "request") as request, \
                patch.object(mail, "initialize_linked_dns", side_effect=initialize_dns) as dns, \
                patch.object(mail.os, "geteuid", return_value=0), \
                patch.object(mail.sys, "argv", ["turnkey-hub", "provision", "--json"]), \
                patch.object(mail.sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(mail.main(), 0)
            saved_client = mail.Mail()
            self.assertFalse(saved_client.status(refresh=False)["setup_pending"])
            self.assertEqual(saved_client.credential("backup")["credential"],
                             f"tklbam2.{CONFIG['device_id']}.{BACKUP_PASSWORD}")
        request.assert_not_called()
        dns.assert_called_once()
        mail.smtp_check.assert_called_once()

    def test_modern_completion_resends_saved_proof_after_loss(self):
        control = self.start_connection()
        with patch.object(mail, "request", side_effect=[
                mail.Error("lost response", retryable=True), self.completion()]) as request:
            with self.assertRaises(mail.Error):
                self.client.poll()
            self.assertEqual(mail.Mail().poll()["connection_status"], "linked")
        self.assertEqual(len(request.call_args_list), 2)
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])
        self.assertEqual(request.call_args.args[1]["control_secret"], control)

    def test_later_backup_approval_fetches_one_scoped_credential(self):
        control = self.start_connection()
        value = self.completion()
        backup = value["services"].pop("backup")
        with patch.object(mail, "request", return_value=value):
            self.client.poll()
        with patch.object(mail, "request", return_value=backup) as request:
            expected = {"credential": f"tklbam2.{CONFIG['device_id']}.{BACKUP_PASSWORD}"}
            self.assertEqual(mail.Mail().credential("backup"), expected)
            self.assertEqual(mail.Mail().credential("backup"), expected)
        request.assert_called_once_with("service", {"device_id": CONFIG["device_id"], "control_secret": control,
                                                    "service": "backup", "version": 1}, "connections")

    def test_modern_explicit_refresh_reads_status_without_registering_credentials(self):
        control = self.start_connection()
        with patch.object(mail, "request", return_value=self.completion()):
            self.client.poll()
        self.hub_active = True
        with patch.object(mail, "request", side_effect=self.hub) as request:
            result = self.client.sync()
        request.assert_called_once_with("configure", {"device_id": CONFIG["device_id"], "control_secret": control})
        self.assertEqual(result["services"]["backup"]["status"], "disabled")
        self.assertEqual(self.client.state["backup_secret"], BACKUP_PASSWORD)
        self.assertEqual(self.client.state["smtp_secret"]["password"], PASSWORD)

    def test_modern_unknown_services_and_versions_are_not_initialized(self):
        self.start_connection()
        value = self.completion()
        value["services"] = {
            "email": dict(version=99, enabled=True, credential=PASSWORD),
            "future": dict(version=1, enabled=True, credential="new-format"),
        }
        with patch.object(mail, "request", return_value=value):
            result = self.client.poll()
        self.assertEqual(result["connection_status"], "linked")
        self.assertFalse(result["setup_pending"])
        self.assertNotIn("mail", result["services"])
        self.assertNotIn("future", result["services"])
        self.assertNotIn("smtp_secret", self.client.state)

    def test_wrong_scope_on_known_service_does_not_overwrite_an_existing_link(self):
        self.client.state["link"] = {"mode": "hub", "device_id": "f" * 32, "control_secret": "c" * 43}
        self.client.save()
        self.start_connection()
        value = self.completion()
        value["services"]["backup"]["credential"] = f"hubdns2.{CONFIG['device_id']}.{DNS_PASSWORD}"
        with patch.object(mail, "request", return_value=value), self.assertRaises(mail.Error):
            self.client.poll()
        self.assertEqual(mail.Mail().linked()["device_id"], "f" * 32)
        self.assertFalse((mail.STATE_DIR / "provision-pending").exists())

    def test_modern_dns_proceeds_when_email_recovery_is_unavailable(self):
        control = self.start_connection()
        value = self.completion()
        available = value["services"]["email"]
        value["services"]["email"] = dict(version=1, enabled=True, status="unavailable")
        with patch.object(mail, "request", return_value=value):
            self.client.poll()
        with patch.object(mail, "request", side_effect=[
                mail.Error("offline", retryable=True), available]) as request, \
                patch.object(mail, "initialize_linked_dns") as dns, \
                patch.object(mail.os, "geteuid", return_value=0), \
                patch.object(mail.sys, "argv", ["turnkey-hub", "provision", "--json"]), \
                patch.object(mail.sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(mail.main(), 1)
            dns.assert_called_once()
            self.assertTrue(mail.Mail().status(refresh=False)["setup_pending"])
            self.assertEqual(mail.main(), 0)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args, (
            "service", {"device_id": CONFIG["device_id"], "control_secret": control,
                        "service": "email", "version": 1}, "connections"))
        self.assertFalse(mail.Mail().status(refresh=False)["setup_pending"])

    def test_modern_email_disable_releases_queue_without_overwriting_local_settings(self):
        self.main.write_text("myhostname = original.example\ndefer_transports = local\n")
        self.start_connection()
        value = self.completion()
        value["services"]["email"] = dict(version=1, enabled=True, status="unavailable")
        with patch.object(mail, "request", return_value=value):
            self.client.poll()
        with patch.object(mail, "request", side_effect=mail.Error("offline", retryable=True)), \
                self.assertRaises(mail.Error):
            self.client.provision_connection_mail()
        self.assertEqual(mail.cf_entries(self.main.read_text())["defer_transports"], "local smtp")
        with self.main.open("a") as config:
            config.write("message_size_limit = 99999\n")
        with patch.object(mail, "request", return_value={"version": 1, "enabled": False, "status": "disabled"}):
            result = mail.Mail().provision_connection_mail()
        self.assertEqual(result["services"]["mail"]["status"], "disabled")
        self.assertEqual(mail.cf_entries(self.main.read_text()), {
            "myhostname": "original.example", "defer_transports": "local", "message_size_limit": "99999"})
        self.assertNotIn("guard", mail.Mail().state)
        mail.smtp_check.assert_not_called()

    def test_modern_mail_recovery_refreshes_a_rejected_cached_credential(self):
        self.start_connection()
        value = self.completion()
        current = {**value["services"]["email"], "credential": "n" * 43}
        with patch.object(mail, "request", return_value=value):
            self.client.poll()
        with patch.object(mail, "request", return_value=current) as request:
            mail.smtp_check.side_effect = [mail.Error("old grant rejected", retryable=True), None]
            self.assertIn("error", mail.Mail().provision_connection_mail()["services"]["mail"])
            request.assert_not_called()
            self.assertNotIn("error", mail.Mail().provision_connection_mail()["services"]["mail"])
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1]["service"], "email")
        self.assertEqual(mail.Mail().state["active"]["password"], "n" * 43)

    def test_modern_relink_retries_after_postfix_rolls_back_to_the_previous_relay(self):
        previous = mail.checked_config(dict(CONFIG, device_id="f" * 32), "o" * 43)
        self.client.apply(previous)
        self.start_connection()
        value = self.completion()
        with patch.object(mail, "request", return_value=value):
            self.client.poll()
        self.fail_command = ["postfix", "reload"]
        with patch.object(mail, "request", return_value=value["services"]["email"]) as request, \
                patch.object(mail, "initialize_linked_dns"), \
                patch.object(mail.os, "geteuid", return_value=0), \
                patch.object(mail.sys, "argv", ["turnkey-hub", "provision", "--json"]), \
                patch.object(mail.sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(mail.main(), 1)
            saved = mail.Mail()
            self.assertTrue(saved.status(refresh=False)["setup_pending"])
            self.assertIn("error", saved.state["services"]["mail"])
            self.assertEqual(saved.state["active"], previous)
            request.assert_not_called()
            self.fail_command = None
            self.assertEqual(mail.main(), 0)
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1]["service"], "email")
        saved = mail.Mail()
        self.assertFalse(saved.status(refresh=False)["setup_pending"])
        self.assertEqual(saved.state["active"]["device_id"], CONFIG["device_id"])
        self.assertNotIn("error", saved.state["services"]["mail"])

    def test_modern_rejected_dns_clears_the_job_if_hub_no_longer_authorizes_dns(self):
        for response in ({"version": 1, "enabled": False, "status": "disabled"},
                         mail.Error("connection revoked", code="invalid_credentials")):
            with self.subTest(disabled=isinstance(response, dict)):
                self.start_connection()
                with patch.object(mail, "request", return_value=self.completion()):
                    self.client.poll()
                reply = {"return_value": response} if isinstance(response, dict) else {"side_effect": response}
                with patch.object(mail, "request", **reply) as request, \
                        patch.object(mail, "initialize_linked_dns",
                                     side_effect=lambda status: status.update(dns_setup_error="DNS denied")), \
                        patch.object(mail.os, "geteuid", return_value=0), \
                        patch.object(mail.sys, "argv", ["turnkey-hub", "provision", "--json"]), \
                        patch.object(mail.sys, "stdout", new_callable=io.StringIO):
                    self.assertEqual(mail.main(), 0)
                request.assert_called_once()
                self.assertEqual(request.call_args.args[1]["service"], "dns")
                saved = mail.Mail()
                self.assertFalse(saved.status(refresh=False)["setup_pending"])
                self.assertFalse(saved.state["services"]["dns"]["enabled"])
                self.assertNotIn("dns_password", saved.linked())
                self.client = saved

    def test_background_enrollment_saves_connection_and_defers_services(self):
        self.main.write_text("myhostname = original.example\n")
        def response(route, payload):
            value = self.hub(route, payload)
            if route == "enroll":
                value["account_email"] = "owner@example.net"
            return value
        with patch.object(mail, "request", side_effect=response) as request:
            result = self.client.enroll(TOKEN, background=True)
        self.assertTrue(result["setup_pending"])
        self.assertEqual(result["connection_status"], "linked")
        self.assertEqual([c.args[0] for c in request.call_args_list], ["enroll"])
        self.assertTrue((mail.STATE_DIR / "provision-pending").exists())
        self.assertEqual(mail.HUB_EMAIL_CACHE.read_text().strip(), "owner@example.net")
        self.assertTrue(mail.Mail().state["provision_pending"])
        self.assertIn("defer_transports", mail.cf_entries(self.main.read_text()))
        with patch.object(mail, "request", side_effect=self.hub):
            result = mail.Mail().sync()
        self.assertEqual(result["mode"], "hub")
        self.assertNotIn("defer_transports", mail.cf_entries(self.main.read_text()))

    def test_provision_retries_failures_and_clears_marker_after_dns(self):
        self.main.write_text("myhostname = original.example\n")
        with patch.object(mail, "request", side_effect=self.hub):
            self.client.enroll(TOKEN, background=True)
        self.fail_command = ["postfix", "check"]
        with patch.object(mail, "request", side_effect=self.hub), \
                patch.object(mail, "initialize_linked_dns") as dns, \
                patch.object(mail.os, "geteuid", return_value=0), \
                patch.object(mail.sys, "argv", ["turnkey-hub", "provision", "--json"]), \
                patch.object(mail.sys, "stdout", new_callable=io.StringIO):
            self.assertEqual(mail.main(), 1)
            dns.assert_not_called()
            self.assertTrue((mail.STATE_DIR / "provision-pending").exists())
            self.fail_command = None
            dns.side_effect = lambda result: result.update(dns_setup_error="DNS failed")
            self.assertEqual(mail.main(), 1)
            self.assertTrue((mail.STATE_DIR / "provision-pending").exists())
            dns.side_effect = None
            self.assertEqual(mail.main(), 0)
        self.assertFalse((mail.STATE_DIR / "provision-pending").exists())
        self.assertFalse(mail.Mail().state.get("provision_pending"))

    def test_authenticated_email_cache_is_private_optional_and_reset_scoped(self):
        def response(route, payload):
            value = self.hub(route, payload)
            if route == "configure":
                value["account_email"] = "owner@example.net"
            return value
        with patch.object(mail, "request", side_effect=response):
            self.client.enroll(TOKEN)
        self.assertEqual(mail.HUB_EMAIL_CACHE.read_text().strip(), "owner@example.net")
        self.assertEqual(mail.HUB_EMAIL_CACHE.stat().st_mode & 0o777, 0o600)
        self.client.reset_mail()
        self.assertTrue(mail.HUB_EMAIL_CACHE.exists())
        self.client.reset()
        self.assertFalse(mail.HUB_EMAIL_CACHE.exists())

    def test_pairing_uses_only_valid_anonymous_correlation_ids(self):
        response = {"verification_uri": mail.HUB + "/connect", "user_code": "D34C&F5A9",
                    "device_code": DEVICE_CODE, "expires_in": 600, "interval": 5}
        for state, expected in (({"installation_id": "1" * 32, "flow_id": "2" * 32,
                                 "email": "private@example.net", "pending": [], "flow_kind": "firstboot"},
                                {"installation_id": "1" * 32, "flow_id": "2" * 32, "flow_kind": "firstboot"}),
                               ({"installation_id": "invalid", "flow_id": "private@example.net"}, {}),
                               ([], {})):
            mail.ONBOARDING_STATE.write_text(json.dumps(state))
            with patch.object(mail, "request", return_value=response.copy()) as request:
                self.client.start()
            request.assert_called_once_with("start", dict(expected, supported_services=LEGACY_OFFERS),
                                            "connections")

    def test_pairing_describes_the_appliance_when_its_build_metadata_is_known(self):
        response = {"verification_uri": mail.HUB + "/connect", "user_code": "D34C&F5A9",
                    "device_code": DEVICE_CODE, "expires_in": 600, "interval": 5}
        version = "turnkey-wordpress-18.0-bookworm-amd64"
        tagged = 'Acquire::http::User-Agent "TurnKey APT-HTTP/1.3 (%s %s)";'
        for files, expected in (
                ({"turnkey_version": version, "01turnkey": tagged % (version, "proxmox")},
                 {"app": "wordpress", "release": "18.0", "build_target": "proxmox"}),
                ({"turnkey_version": version, "01turnkey": tagged % (version, "ec2 hub")},
                 {"app": "wordpress", "release": "18.0", "build_target": "ec2 hub"}),
                ({"turnkey_version": version, "01turnkey": 'Acquire::http::User-Agent "TurnKey APT-HTTP/1.3 (%s)";' % version},
                 {"app": "wordpress", "release": "18.0", "build_target": "iso"}),
                ({"turnkey_version": version}, {"app": "wordpress", "release": "18.0"}),
                ({"turnkey_version": "not a turnkey version"}, {}),
                ({}, {})):
            with self.subTest(files=files):
                for name in ("turnkey_version", "01turnkey"):
                    (self.root / name).unlink(missing_ok=True)
                for name, text in files.items():
                    (self.root / name).write_text(text + "\n")
                with patch.object(mail, "request", return_value=response.copy()) as request:
                    self.client.start()
                request.assert_called_once_with("start", dict(expected, supported_services=LEGACY_OFFERS),
                                                "connections")

    def test_sync_persists_exact_grant_secret_before_registration_and_retries(self):
        self.client.state["link"] = {"mode": "hub", "device_id": CONFIG["device_id"],
                                     "control_secret": "c" * 43, "dns_password": DNS_PASSWORD}
        self.client.save()
        sent = []
        def timeout(route, payload):
            if "smtp_password" in payload:
                saved = json.loads((mail.STATE_DIR / "state.json").read_text())["smtp_secret"]
                self.assertEqual(saved["grant_id"], payload["smtp_grant_id"])
                self.assertEqual(saved["password"], payload["smtp_password"])
                sent.append(payload.copy())
                raise mail.Error("lost response", retryable=True)
            return self.hub(route, payload)
        with patch.object(mail, "request", side_effect=timeout):
            for unused in range(2):
                with self.assertRaises(mail.Error):
                    mail.Mail().sync()
        self.assertEqual(sent[0], sent[1])
        self.assertNotEqual(sent[0]["smtp_password"], DNS_PASSWORD)

    def test_manual_mail_survives_sender_change_sync_and_restart(self):
        with patch.object(mail, "request", side_effect=self.hub):
            self.client.enroll(TOKEN)
        self.client.reset_mail()
        before = self.main.read_text()
        original = self.hub
        def changed(route, payload):
            value = original(route, payload)
            if route == "configure":
                value["mail"].update(grant_id="b" * 32, domain="new.tklapp.com", status="secret_required")
            return value
        with patch.object(mail, "request", side_effect=changed) as request:
            restored = mail.Mail()
            restored.retry()
        self.assertEqual(before, self.main.read_text())
        self.assertEqual("manual", restored.state["mail_preference"])
        self.assertFalse(any("smtp_password" in call.args[1] for call in request.call_args_list))
        self.assertTrue(restored.linked()["control_secret"])
        with patch.object(mail, "request", side_effect=self.hub):
            restored.use_hub()
        self.assertEqual("hub", restored.state["mail_preference"])
        self.assertIn("relayhost", self.main.read_text())

    def test_sync_preserves_legacy_backup_and_dns_configuration(self):
        backup = self.root / "backup"
        backup.mkdir()
        for name in ("sub_apikey", "secret", "key", "hbr", "credentials"):
            (backup / name).write_text("original-" + name)
        before = {f.name: f.read_bytes() for f in backup.iterdir()}
        self.client.state.update(link={"mode": "hub", "device_id": CONFIG["device_id"], "control_secret": "c" * 43})
        def active(route, value):
            result = self.hub(route, value)
            result["backup"] = {"status": "secret_required"}
            return result
        with patch.object(mail, "request", side_effect=active) as request, patch.object(mail, "BACKUP_DIR", backup):
            self.client.sync()
        self.assertEqual(before, {f.name: f.read_bytes() for f in backup.iterdir()})
        self.assertFalse(any("backup_secret" in call.args[1] for call in request.call_args_list))
        self.assertFalse(any(call[0][0].startswith(("tklbam", "hubdns")) for call in self.calls))

    def test_backup_credentials_are_exported_only_on_explicit_request(self):
        self.client.state.update(link={"mode": "hub", "device_id": CONFIG["device_id"], "control_secret": "c" * 43})
        def active(route, value):
            result = self.hub(route, value)
            result["backup"] = {"status": "active" if "backup_secret" in value else "secret_required"}
            return result
        with patch.object(mail, "request", side_effect=active):
            result = self.client.credential("backup")
        self.assertRegex(result["credential"], r"^tklbam2\.[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$")
        self.assertFalse(self.calls)

    def test_idle_retry_performs_no_hub_requests(self):
        self.client.state.update(link={"mode": "hub", "device_id": CONFIG["device_id"], "control_secret": "c" * 43})
        with patch.object(mail, "request") as request:
            self.client.retry()
        request.assert_not_called()

    def test_backup_prerequisites_exit_without_registering_or_initializing(self):
        self.client.state.update(link={"mode": "hub", "device_id": CONFIG["device_id"], "control_secret": "c" * 43})
        for status in ("disabled", "setup_required", "migration_required"):
            def unavailable(route, value):
                result = self.hub(route, value)
                result["backup"] = {"status": status}
                return result
            with patch.object(mail, "request", side_effect=unavailable) as request:
                with self.assertRaises(mail.Error) as error:
                    self.client.credential("backup")
            self.assertEqual(error.exception.code, status)
            if status != "disabled":
                message = str(error.exception)
                self.assertIn("in the Hub:\n\n  https://", message)
                self.assertIn("\n\nThen retry your command.", message)
            self.assertEqual(request.call_count, 1)
        self.assertNotIn("backup_secret", self.client.state)

    def test_custom_sender_without_dns_is_a_valid_independent_service(self):
        original = self.hub
        def custom(route, payload):
            value = original(route, payload)
            if route == "configure":
                value["dns"].update(enabled=False, hostname="")
                value["mail"].update(domain="custom.example.test", sender="noreply@custom.example.test")
            return value
        with patch.object(mail, "request", side_effect=custom):
            result = self.client.enroll(TOKEN)
        self.assertEqual("ready", result["status"])
        self.assertEqual("custom.example.test", result["domain"])
        self.assertIsNone(self.client.linked()["dyndns"])
        self.assertFalse((mail.SYSTEMD_DIR / "turnkey-mail-dns.timer").exists())

    def test_sync_keeps_legacy_dns_credentials(self):
        legacy = self.root / "dns"
        legacy.mkdir()
        (legacy / "sub_apikey").write_text("legacy")
        (legacy / "fqdn").write_text(CONFIG["dyndns"]["hostname"])
        self.client.state.update(link={"mode": "hub", "device_id": CONFIG["device_id"], "control_secret": "c" * 43})
        with patch.object(mail, "HUBDNS_DIR", legacy), patch.object(mail, "request", side_effect=self.hub):
            self.client.sync()
        self.assertEqual("legacy", (legacy / "sub_apikey").read_text())

    def test_full_local_reset_removes_only_owned_backup_auth_preserving_history(self):
        backup = self.root / "reset-backup"
        backup.mkdir()
        (backup / "sub_apikey").write_text("tklbam2.%s.%s" % (CONFIG["device_id"], "s" * 43))
        (backup / "credentials").write_text("cached-session")
        (backup / "secret").write_text("preserve-secret")
        (backup / "hbr").write_text("preserve-history")
        self.client.state["link"] = {"mode": "hub", "device_id": CONFIG["device_id"]}
        with patch.object(mail, "BACKUP_DIR", backup):
            self.client.reset()
        self.assertFalse((backup / "sub_apikey").exists())
        self.assertFalse((backup / "credentials").exists())
        self.assertEqual("preserve-secret", (backup / "secret").read_text())
        self.assertEqual("preserve-history", (backup / "hbr").read_text())
        (backup / "sub_apikey").write_text("explicit-legacy-key")
        self.client.state["link"] = {"mode": "hub", "device_id": CONFIG["device_id"]}
        with patch.object(mail, "BACKUP_DIR", backup):
            self.client.reset()
        self.assertEqual("explicit-legacy-key", (backup / "sub_apikey").read_text())

    def test_new_pairing_rotates_service_auth_without_erasing_backup_history(self):
        with patch.object(mail, "request", side_effect=self.hub):
            self.client.enroll(TOKEN)
        self.client.state["backup_secret"] = "old-revoked-backup-secret"
        previous = self.client.state["smtp_secret"]["password"]
        original = self.hub
        self.hub_active = False
        def fresh(route, payload):
            value = copy.deepcopy(original(route, payload))
            value["device_id"] = "b" * 32
            if route == "configure":
                value["mail"]["grant_id"] = "b" * 32
                value["mail"]["smtp"]["username"] = "tkl-" + "b" * 32
            return value
        with patch.object(mail, "request", side_effect=fresh):
            self.client.enroll("c" * 32 + "." + "d" * 64)
        self.assertEqual("b" * 32, self.client.linked()["device_id"])
        self.assertNotEqual(previous, self.client.state["smtp_secret"]["password"])
        self.assertNotIn("backup_secret", self.client.state)

    def test_sync_does_not_create_polling_timers(self):
        with patch.object(mail, "request", side_effect=self.hub):
            self.client.enroll(TOKEN)
        self.assertEqual(list(mail.SYSTEMD_DIR.iterdir()), [])

    def test_interrupted_apply_restores_config_before_reusing_state(self):
        original = self.main.read_text()
        artifact = mail.POSTFIX_DIR / "turnkey-mail-interrupted.sasl"
        artifact.write_text("test credential")
        self.main.write_text("relayhost = [partially-applied.example]:587\n")
        self.client.state["applying"] = {"original": original, "files": [str(artifact)]}
        self.client.save()
        recovered = mail.Mail()
        self.assertEqual(self.main.read_text(), original)
        self.assertFalse(artifact.exists())
        self.assertNotIn("applying", recovered.state)

    def test_automatic_enrollment_accepts_hub_signed_token_shape(self):
        with patch.object(mail, "request", side_effect=self.hub) as request:
            result = self.client.enroll(TOKEN)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(request.call_args_list[0].args[0], "enroll")
        self.assertEqual(request.call_args_list[0].args[1]["token"], TOKEN)

    def test_approved_pairing_enrolls_with_hub_signed_token_unchanged(self):
        self.client.state["pairing"] = {"expires": mail.time.time() + 900,
                                       "interval": 5, "device_code": DEVICE_CODE}
        with patch.object(mail, "request", side_effect=[
                {"status": "approved", "enrollment_token": TOKEN}, self.hub("enroll", {}),
                self.hub("configure", {}), self.hub("configure", {"smtp_password": "registered"})]) as request:
            result = self.client.poll()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(request.call_args_list[0].args,
                         ("device/poll", {"device_code": DEVICE_CODE}))
        self.assertEqual(request.call_args_list[1].args[0], "enroll")
        self.assertEqual(request.call_args_list[1].args[1]["token"], TOKEN)
        self.assertNotIn("pairing", self.client.state)

    def test_malformed_enrollment_tokens_are_rejected_before_enrollment(self):
        invalid = (DEVICE_CODE, TOKEN.replace(".", "_"), TOKEN.replace(".", ".."),
                   TOKEN[1:], TOKEN + "b", TOKEN.upper(), TOKEN.replace("b", "g"),
                   TOKEN + "\n")
        for token in invalid:
            with self.subTest(token=token):
                with patch.object(mail, "request") as request:
                    with self.assertRaises(mail.Error):
                        self.client.enroll(token)
                request.assert_not_called()
                self.assertNotIn("pending", self.client.state)
                self.client.state["pairing"] = {"expires": mail.time.time() + 900,
                                               "interval": 5, "device_code": DEVICE_CODE}
                with patch.object(mail, "request", return_value={
                        "status": "approved", "enrollment_token": token}) as request:
                    with self.assertRaises(mail.Error):
                        self.client.poll()
                self.assertEqual(request.call_count, 1)
                self.assertNotIn("pending", self.client.state)

    def test_pairing_device_code_does_not_accept_enrollment_token(self):
        response = {"device_code": TOKEN, "user_code": "ABCD&1234", "expires_in": 900,
                    "interval": 5, "verification_uri": mail.HUB + "/connect"}
        with patch.object(mail, "request", return_value=response):
            with self.assertRaises(mail.Error):
                self.client.start()
        self.assertNotIn("pairing", self.client.state)

    def test_completed_enrollment_is_a_noop_when_firstboot_repeats_token(self):
        with patch.object(mail, "request", side_effect=self.hub):
            self.client.enroll(TOKEN)
        secret = self.client.state["active"]["password"]
        with patch.object(mail, "request") as request:
            result = mail.Mail().enroll(TOKEN)
        request.assert_not_called()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(mail.Mail().state["active"]["password"], secret)

    def test_dns_credential_uses_only_its_independent_secret(self):
        self.client.state.update(link={"mode": "hub", "device_id": CONFIG["device_id"], "control_secret": "c" * 43,
                                       "dns_password": DNS_PASSWORD})
        with patch.object(mail, "request", side_effect=self.hub):
            result = self.client.credential("dns")
        self.assertEqual("hubdns2.%s.%s" % (CONFIG["device_id"], DNS_PASSWORD), result["credential"])
        self.assertEqual(CONFIG["dyndns"]["hostname"], result["hostname"])

    def assert_dns_updates(self):
        with patch.object(mail, "request", side_effect=self.hub):
            result = mail.Mail().credential("dns")
        self.assertTrue(result["credential"].endswith("." + DNS_PASSWORD))

    def test_custom_smtp_preserves_linked_dns(self):
        self.configured()
        custom = mail.checked_config({"smtp": {"host": "custom.example.net", "port": 587,
                                               "username": "custom-user"}}, "custom-secret", custom=True)
        self.client.apply(custom)
        self.assertEqual(mail.Mail().state["active"]["mode"], "custom")
        self.assert_dns_updates()

    def test_mail_only_reset_preserves_link_and_dns_timer(self):
        self.configured()
        (mail.SYSTEMD_DIR / "turnkey-mail-dns.timer").write_text("existing schedule")
        original_identity = CONFIG["device_id"]
        self.client.reset_mail()
        self.assertNotIn("active", self.client.state)
        self.assertEqual(self.client.state["link"]["device_id"], original_identity)
        self.assertTrue((mail.SYSTEMD_DIR / "turnkey-mail-dns.timer").exists())
        self.assert_dns_updates()
        with patch.object(mail, "request") as request:
            mail.Mail().retry()
        request.assert_not_called()

    def test_existing_combined_state_survives_custom_and_mail_reset(self):
        self.configured()
        self.client.state.pop("link", None)
        self.client.save()
        previous = mail.Mail()
        custom = mail.checked_config({"smtp": {"host": "custom.example.net", "port": 587,
                                               "username": "custom-user"}}, "custom-secret", custom=True)
        previous.apply(custom)
        previous.reset_mail()
        self.assert_dns_updates()
        with patch.object(mail, "request") as request:
            previous.reset()
        request.assert_not_called()
        self.assertEqual(previous.state, {})
        self.assertNotIn(DNS_PASSWORD, (mail.STATE_DIR / "state.json").read_text())

    def test_repeated_completed_bootstrap_preserves_mail_only_choices(self):
        with patch.object(mail, "request", side_effect=self.hub):
            self.client.enroll(TOKEN)
        custom = {"smtp": {"host": "custom.example.net", "port": 587, "username": "custom-user"},
                  "password": "custom-secret"}
        original_umask = os.umask(0o077)
        try:
            with patch.object(mail.sys, "argv", ["turnkey-hub", "configure", "--stdin", "--json"]), \
                    patch.object(mail.sys, "stdin", io.StringIO(json.dumps(custom))), \
                    patch.object(mail.sys, "stdout", io.StringIO()), \
                    patch.object(mail.os, "geteuid", return_value=0):
                self.assertEqual(mail.main(), 0)
        finally:
            os.umask(original_umask)
        client = mail.Mail()
        with patch.object(mail, "request") as request:
            self.assertEqual(client.enroll(TOKEN)["mode"], "custom")
            client.reset_mail()
            self.assertEqual(client.enroll(TOKEN)["status"], "linked")
        request.assert_not_called()

    def test_enrollment_leaves_dns_operations_to_hubdns(self):
        with patch.object(mail, "request", side_effect=self.hub):
            self.client.enroll(TOKEN)
        self.assertFalse(any("hubdns" in str(call[0]) for call in self.calls))

    def test_invalid_ip_discovery_does_not_change_dns(self):
        self.configured()
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"invalid\n"
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(mail.Mail, "legacy_dns", return_value=False), \
                patch.object(mail.urllib.request, "build_opener", return_value=opener), \
                patch.object(mail, "request") as request:
            with self.assertRaises(mail.Error):
                self.client.dns_update()
        request.assert_not_called()

    def test_apply_keeps_unrelated_settings_and_protects_both_sasl_files(self):
        self.configured()
        values = mail.cf_entries(self.main.read_text())
        self.assertEqual(values["myhostname"], "original.example")
        self.assertEqual(values["smtp_tls_security_level"], "secure")
        self.assertEqual(values["smtp_fallback_relay"], "")
        self.assertEqual(values["sender_canonical_classes"], "envelope_sender")
        files = self.client.state["owned_files"]
        for path in files:
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
        self.assertEqual((mail.STATE_DIR / "state.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(PASSWORD, repr(self.calls))
        self.assertNotIn(DNS_PASSWORD, repr(self.calls))
        self.assertNotIn(DNS_PASSWORD, Path(files[0]).read_text())

    def test_reload_failure_restores_config_and_removes_new_credentials(self):
        before = self.main.read_bytes()
        self.fail_command = ["postfix", "reload"]
        with self.assertRaises(mail.Error):
            self.configured()
        self.assertEqual(self.main.read_bytes(), before)
        self.assertFalse(list(mail.POSTFIX_DIR.glob("turnkey-mail-*")))
        self.assertNotIn("active", self.client.state)

    def test_initial_guard_requeues_with_sender_policy_and_smtp_still_held(self):
        self.main.write_text("myhostname = original.example\ndefer_transports = lmtp\n")
        self.client.guard()
        original_run = mail.run.side_effect
        observed = []
        def run(command, *args, **kwargs):
            if command == ["postsuper", "-r", "ALL"]:
                values = mail.cf_entries(self.main.read_text())
                self.assertEqual(values["defer_transports"], "lmtp smtp")
                self.assertTrue(values["sender_canonical_maps"].startswith("regexp:"))
                observed.append(command)
            return original_run(command, *args, **kwargs)
        with patch.object(mail, "run", side_effect=run):
            self.configured()
        self.assertEqual(observed, [["postsuper", "-r", "ALL"]])
        self.assertEqual(mail.cf_entries(self.main.read_text())["defer_transports"], "lmtp")
        self.assertNotIn("guard", self.client.state)

    def test_requeue_failure_keeps_guard_and_retries_activation(self):
        self.main.write_text("myhostname = original.example\n")
        self.client.guard()
        held = self.main.read_bytes()
        self.fail_command = ["postsuper", "-r", "ALL"]
        with self.assertRaises(mail.Error):
            self.configured()
        self.assertEqual(self.main.read_bytes(), held)
        self.assertIn("guard", self.client.state)
        self.assertNotIn("active", self.client.state)
        self.assertFalse(list(mail.POSTFIX_DIR.glob("turnkey-mail-*")))
        self.fail_command = None
        self.configured()
        self.assertNotIn("guard", self.client.state)

    def test_guard_release_reload_failure_retains_retryable_guard(self):
        self.main.write_text("myhostname = original.example\n")
        self.client.guard()
        held = self.main.read_bytes()
        original_run = mail.run.side_effect
        reloads = []
        def run(command, *args, **kwargs):
            if command == ["postfix", "reload"]:
                reloads.append(command)
                if len(reloads) == 2:
                    raise mail.Error("release failed", retryable=True)
            return original_run(command, *args, **kwargs)
        with patch.object(mail, "run", side_effect=run):
            with self.assertRaises(mail.Error):
                self.configured()
        self.assertEqual(self.main.read_bytes(), held)
        self.assertIn("guard", self.client.load())
        self.assertNotIn("active", self.client.state)
        self.configured()
        self.assertNotIn("guard", self.client.state)

    def test_existing_hub_and_custom_smtp_leave_queue_untouched(self):
        self.configured()
        self.configured()
        custom = mail.checked_config({"smtp": CONFIG["smtp"]}, PASSWORD, custom=True)
        self.client.apply(custom)
        self.assertFalse(any(call[0][0] == "postsuper" for call in self.calls))
        self.client.reset_mail()
        self.main.write_text("myhostname = original.example\n")
        self.client.guard()
        self.client.apply(custom)
        self.assertFalse(any(call[0][0] == "postsuper" for call in self.calls))

    def test_interrupted_guard_release_recovers_and_retries(self):
        self.main.write_text("myhostname = original.example\n")
        self.client.guard()
        held = self.main.read_bytes()
        original_run = mail.run.side_effect
        reloads = []
        def run(command, *args, **kwargs):
            if command == ["postfix", "reload"]:
                reloads.append(command)
                if len(reloads) == 2:
                    raise KeyboardInterrupt()
            return original_run(command, *args, **kwargs)
        with patch.object(mail, "run", side_effect=run):
            with self.assertRaises(KeyboardInterrupt):
                self.configured()
        self.client = mail.Mail()
        self.assertEqual(self.main.read_bytes(), held)
        self.assertIn("guard", self.client.state)
        self.assertNotIn("applying", self.client.state)
        self.assertFalse(list(mail.POSTFIX_DIR.glob("turnkey-mail-*")))
        self.configured()
        self.assertNotIn("guard", self.client.state)
        self.assertEqual(2, sum(call[0][0] == "postsuper" for call in self.calls))

    def test_reset_preserves_private_registration_retry_receipt(self):
        receipt = mail.STATE_DIR / "registration.json"
        payload = json.dumps({"server_id": "snapshot-destination", "nonce": "a" * 64})
        receipt.write_text(payload)
        receipt.chmod(0o600)
        self.client.reset()
        self.assertEqual(payload, receipt.read_text())
        self.assertEqual(0o600, receipt.stat().st_mode & 0o777)

    def test_reset_restores_only_unchanged_owned_parameters(self):
        original = self.main.read_text()
        self.configured()
        text = self.main.read_text().replace("myhostname = original.example", "myhostname = edited.example")
        self.main.write_text(text)
        files = self.client.state["owned_files"][:]
        self.client.reset()
        self.assertEqual(self.main.read_text(), original.replace("original.example", "edited.example"))
        self.assertTrue(all(not Path(path).exists() for path in files))
        self.assertEqual(self.client.state, {})

    def test_custom_settings_changed_outside_helper_survive_reset(self):
        self.configured()
        self.main.write_text(mail.change_cf(self.main.read_text(), {"relayhost": "[new-owner.example]:587"}))
        self.client.reset()
        self.assertEqual(mail.cf_entries(self.main.read_text())["relayhost"], "[new-owner.example]:587")

    def test_failed_enrollment_survives_firstboot_preseed_removal_and_reuses_secrets(self):
        first = []

        def unavailable(route, payload):
            first.append(payload.copy())
            saved = json.loads((mail.STATE_DIR / "state.json").read_text())["pending"]
            self.assertEqual(saved["token"], TOKEN)
            self.assertEqual(saved["control_secret"], payload["control_secret"])
            raise mail.Error("network unavailable", retryable=True)

        with patch.object(mail, "request", side_effect=unavailable):
            with self.assertRaises(mail.Error):
                self.client.enroll(TOKEN)
        # Firstboot's preseed file is absent; a new process uses only state.
        retry = mail.Mail()
        with patch.object(mail, "request", side_effect=self.hub) as request:
            result = retry.retry()
        self.assertEqual(request.call_args_list[0].args, ("enroll", first[0]))
        self.assertEqual(result["status"], "ready")
        self.assertNotEqual(first[0]["control_secret"], first[0]["dns_password"])
        self.assertNotIn("pending", retry.state)
        self.assertNotIn(TOKEN, json.dumps(retry.state))

    def test_new_direct_sender_is_held_during_failed_enrollment(self):
        self.main.write_text("myhostname = original.example\n")
        with patch.object(mail, "request", side_effect=mail.Error("offline", retryable=True)):
            with self.assertRaises(mail.Error):
                self.client.enroll(TOKEN)
        self.assertEqual(mail.cf_entries(self.main.read_text())["defer_transports"], "smtp")
        with patch.object(mail, "request", side_effect=self.hub):
            self.client.retry()
        self.assertNotIn("defer_transports", mail.cf_entries(self.main.read_text()))

    def test_existing_relay_is_preserved_when_hub_is_unavailable(self):
        original = self.main.read_text()
        with patch.object(mail, "request", side_effect=mail.Error("offline", retryable=True)):
            with self.assertRaises(mail.Error):
                self.client.enroll(TOKEN)
        self.assertEqual(self.main.read_text(), original)

    def test_expired_token_stops_background_network_retry(self):
        with patch.object(mail, "request", side_effect=mail.Error("expired", code="expired")):
            with self.assertRaises(mail.Error):
                self.client.enroll(TOKEN)
        with patch.object(mail, "request") as request:
            self.client.retry()
        request.assert_not_called()
        self.assertEqual(self.client.status(refresh=False)["status"], "connecting")

    def test_idle_retry_does_not_contact_services(self):
        with patch.object(mail, "request") as request:
            self.client.retry()
        request.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_status_never_discloses_credentials(self):
        self.configured()
        output = json.dumps(self.client.status(refresh=False))
        for secret in (PASSWORD, DNS_PASSWORD, TOKEN):
            self.assertNotIn(secret, output)

    def test_read_only_local_status_preserves_mail_journal_and_old_status_recovers(self):
        original = self.main.read_text()
        interrupted = original + "# incomplete update\n"
        transient = self.main.parent / "turnkey-mail-interrupted.sasl"
        transient.write_text("incomplete settings")
        self.client.state["applying"] = {"original": original, "files": [str(transient)]}
        self.client.save()
        self.main.write_text(interrupted)
        state_path = mail.STATE_DIR / "state.json"
        before = state_path.read_bytes()

        def status(*options):
            output = io.StringIO()
            argv = ["turnkey-hub", "status", "--local", "--json", *options]
            with patch.object(mail.sys, "argv", argv), \
                    patch.object(mail.os, "geteuid", return_value=0), \
                    patch.object(mail.os, "umask"), patch.object(mail.sys, "stdout", output):
                self.assertEqual(mail.main(), 0)
            return json.loads(output.getvalue())

        with patch.object(mail, "request") as request:
            snapshot = status("--read-only")
            self.assertEqual(state_path.read_bytes(), before)
            self.assertEqual(self.main.read_text(), interrupted)
            self.assertTrue(transient.exists())
            self.assertFalse((mail.STATE_DIR / "lock").exists())
            self.assertEqual(self.calls, [])

            self.assertEqual(status(), snapshot)
        request.assert_not_called()
        self.assertEqual(self.main.read_text(), original)
        self.assertNotIn("applying", json.loads(state_path.read_text()))
        self.assertFalse(transient.exists())
        self.assertEqual(self.calls, [(["postfix", "reload"], None)])

    def test_read_only_status_requires_local(self):
        with patch.object(mail.sys, "argv", ["turnkey-hub", "status", "--read-only"]), \
                patch.object(mail.sys, "stderr", io.StringIO()), self.assertRaises(SystemExit) as error:
            mail.main()
        self.assertEqual(error.exception.code, 2)

    def test_pairing_only_returns_public_fields_and_pending_keeps_current_relay(self):
        original = self.main.read_bytes()
        response = {"device_code": DEVICE_CODE, "user_code": "ABCD&1234", "expires_in": 900, "interval": 5,
                    "verification_uri": mail.HUB + "/connect"}
        with patch.object(mail, "request", return_value=response):
            public = self.client.start()
        self.assertNotIn(DEVICE_CODE, json.dumps(public))
        with patch.object(mail, "request", return_value={"status": "pending"}):
            self.assertEqual(self.client.poll()["status"], "pending")
        self.assertEqual(self.main.read_bytes(), original)

    def browser_connect(self, *arguments):
        output = io.StringIO()
        with patch.object(mail.sys, "argv", ["turnkey-hub", "connect", *arguments, "--json"]), \
                patch.object(mail.os, "geteuid", return_value=0), \
                patch.object(mail.os, "umask"), patch.object(mail.sys, "stdout", output):
            self.assertEqual(mail.main(), 0)
        return json.loads(output.getvalue())

    def test_browser_refresh_and_concurrent_start_resume_one_public_request(self):
        response = dict(device_code=DEVICE_CODE, user_code="D34C&F5A9", expires_in=600,
                        interval=5, verification_uri=mail.HUB + "/connect")
        with patch.object(mail, "request", return_value=response) as request, \
                patch.object(mail.time, "time", return_value=100):
            self.assertEqual(self.browser_connect("--pending"), {"status": "idle"})
            started = self.browser_connect("--start", "--resume")
            self.assertEqual(self.browser_connect("--pending"), started)
            resumed = self.browser_connect("--start", "--resume")
        request.assert_called_once_with("start", {"supported_services": LEGACY_OFFERS}, "connections")
        self.assertEqual(started, resumed)
        self.assertEqual(started, {"status": "pending", "user_code": "D34C-F5A9",
                                  "verification_uri": mail.HUB + "/connect",
                                  "expires_in": 600, "interval": 5})
        # The fixture server's private proof stays on the appliance.
        state = mail.Mail().state["pairing"]
        for secret in (DEVICE_CODE, state["control_secret"]):
            self.assertNotIn(secret, json.dumps(resumed))

    def test_browser_pending_status_does_not_recover_an_interrupted_mail_change(self):
        self.start_connection()
        transient = self.main.parent / "turnkey-mail-interrupted.sasl"
        transient.write_text("incomplete settings")
        self.client.state["applying"] = {"original": self.main.read_text(), "files": [str(transient)]}
        self.main.write_text("myhostname = interrupted.example\n")
        self.client.save()
        state_path = mail.STATE_DIR / "state.json"
        before = state_path.read_bytes()
        with patch.object(mail, "request") as request:
            pending = self.browser_connect("--pending")
        self.assertEqual(pending["user_code"], "D34C-F5A9")
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(self.main.read_text(), "myhostname = interrupted.example\n")
        self.assertTrue(transient.exists())
        self.assertEqual(self.calls, [])
        request.assert_not_called()

    def test_browser_poll_rejects_replaced_code_and_current_code_starts_provision(self):
        self.start_connection()
        new_code = "ABCD-1234"
        response = dict(device_code="u" * 43, user_code=new_code, expires_in=600,
                        interval=5, verification_uri=mail.HUB + "/connect")
        with patch.object(mail, "request", return_value=response):
            self.browser_connect("--start")
        with patch.object(mail, "request", return_value=self.completion()) as request, \
                patch("libinithooks.hub_runtime.start_provision") as start_provision:
            self.assertEqual(self.browser_connect("--poll", "--expected-code", "D34C-F5A9", "--background"),
                             {"status": "replaced"})
            request.assert_not_called()
            start_provision.assert_not_called()
            linked = self.browser_connect("--poll", "--expected-code", new_code, "--background")
        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], "complete")
        self.assertEqual(request.call_args.args[1]["device_code"], response["device_code"])
        start_provision.assert_called_once_with()
        self.assertEqual(linked["connection_status"], "linked")
        self.assertTrue(linked["setup_pending"])
        self.assertTrue((mail.STATE_DIR / "provision-pending").exists())
        self.assertEqual(self.browser_connect("--pending"), {"status": "idle"})

    def test_browser_denial_and_remote_expiry_end_the_saved_request(self):
        for result in ("expired", "denied"):
            with self.subTest(result=result):
                self.start_connection()
                with patch.object(mail, "request", return_value={"status": result}) as request:
                    ended = self.browser_connect("--poll", "--expected-code", "D34C-F5A9", "--background")
                    self.assertEqual(ended["status"], result)
                    self.assertEqual(self.browser_connect("--pending"), {"status": "idle"})
                request.assert_called_once()

    def test_pairing_accepts_legacy_and_new_wire_codes_with_one_display_format(self):
        for code in ("D34C&F5A9", "D34C-F5A9"):
            with self.subTest(code=code):
                response = {"device_code": DEVICE_CODE, "user_code": code,
                            "expires_in": 900, "interval": 5,
                            "verification_uri": mail.HUB + "/connect"}
                with patch.object(mail, "request", return_value=response) as request:
                    public = self.client.start()
                request.assert_called_once_with("start", {"supported_services": LEGACY_OFFERS}, "connections")
                self.assertEqual(public["user_code"], "D34C-F5A9")
                saved = json.loads((mail.STATE_DIR / "state.json").read_text())["pairing"]
                self.assertEqual(saved["user_code"], "D34C-F5A9")
                self.assertEqual(saved["device_code"], DEVICE_CODE)
                self.assertEqual(set(public), {"verification_uri", "user_code", "expires_in", "interval"})

    def test_invalid_wire_code_preserves_saved_pairing(self):
        self.client.state["pairing"] = {"device_code": DEVICE_CODE, "user_code": "D34C-F5A9"}
        self.client.save()
        before = (mail.STATE_DIR / "state.json").read_bytes()
        for code in ("D34CF5A9", "d34c-f5a9", "D34C--F5A9", "D34C&F5A9\n",
                     "D34C/F5A9", "D34C-F5A", "D34C-F5A99", "Ｄ34C-F5A9", None, 12345678):
            with self.subTest(code=code):
                response = {"device_code": DEVICE_CODE, "user_code": code,
                            "expires_in": 900, "interval": 5,
                            "verification_uri": mail.HUB + "/connect"}
                with patch.object(mail, "request", return_value=response):
                    with self.assertRaises(mail.Error):
                        self.client.start()
                self.assertEqual((mail.STATE_DIR / "state.json").read_bytes(), before)

    def test_resumed_legacy_pairing_polls_by_device_secret_without_redisplaying_code(self):
        self.client.state["pairing"] = {"device_code": DEVICE_CODE, "user_code": "D34C&F5A9",
                                       "expires": mail.time.time() + 900, "interval": 5, "next_poll": 0}
        self.client.save()
        with patch.object(mail, "request", return_value={"status": "pending"}) as request:
            result = mail.Mail().poll()
        request.assert_called_once_with("device/poll", {"device_code": DEVICE_CODE})
        self.assertEqual(result, {"status": "pending", "interval": 5})

    def test_connect_cli_displays_dash_for_unchanged_legacy_api_response(self):
        response = {"device_code": DEVICE_CODE, "user_code": "D34C&F5A9",
                    "expires_in": 900, "interval": 5, "verification_uri": mail.HUB + "/connect"}
        output = io.StringIO()
        with patch.object(mail.sys, "argv", ["turnkey-hub", "connect"]), \
                patch.object(mail.os, "geteuid", return_value=0), \
                patch.object(mail.os, "umask"), \
                patch.object(mail.time, "sleep"), \
                patch.object(mail, "request", side_effect=[response, {"status": "denied"}]), \
                patch.object(mail.sys, "stdout", output), patch.object(mail.sys, "stderr"):
            self.assertEqual(mail.main(), 1)
        self.assertIn("Connection code: D34C-F5A9", output.getvalue())
        self.assertNotIn("D34C&F5A9", output.getvalue())
        self.assertNotIn(DEVICE_CODE, output.getvalue())

    def test_pairing_enforces_poll_interval_without_network(self):
        self.client.state["pairing"] = {"expires": mail.time.time() + 900, "next_poll": mail.time.time() + 30,
                                       "interval": 5, "device_code": DEVICE_CODE}
        with patch.object(mail, "request") as request:
            self.assertEqual(self.client.poll()["status"], "pending")
        request.assert_not_called()

    def test_notified_poll_bypasses_only_local_delay_and_still_enrolls_signed_approval(self):
        self.client.state['pairing'] = {'expires': mail.time.time() + 600, 'next_poll': 0,
                                        'interval': 5, 'device_code': DEVICE_CODE}
        with patch.object(mail, 'request', side_effect=[{'status': 'pending'},
                {'status': 'approved', 'enrollment_token': TOKEN}]) as request, \
                patch.object(self.client, 'enroll', return_value={'connection_status': 'linked'}) as enroll:
            self.assertEqual(self.client.poll()['status'], 'pending')
            self.assertEqual(self.client.poll()['status'], 'pending')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(self.client.poll(notified=True)['connection_status'], 'linked')
        enroll.assert_called_once_with(TOKEN, {}, background=False)
        self.assertEqual(request.call_args.args, ('device/poll', {'device_code': DEVICE_CODE}))

    def test_notified_denial_and_local_expiry_cannot_enroll(self):
        self.client.state['pairing'] = {'expires': mail.time.time() + 600,
                'next_poll': mail.time.time() + 30, 'interval': 5, 'device_code': DEVICE_CODE}
        with patch.object(mail, 'request', return_value={'status': 'denied'}) as request, \
                patch.object(self.client, 'enroll') as enroll:
            self.assertEqual(self.client.poll(notified=True)['status'], 'denied')
            self.client.state['pairing']['expires'] = 0
            self.assertEqual(self.client.poll(notified=True)['status'], 'expired')
        request.assert_called_once()
        enroll.assert_not_called()

    def test_notified_cli_flag_requires_poll(self):
        for arguments in (['connect', '--notified'], ['connect', '--start', '--notified']):
            with patch.object(mail.sys, 'argv', ['turnkey-hub', *arguments]), \
                    patch.object(mail.sys, 'stderr'), self.assertRaises(SystemExit) as result:
                mail.main()
            self.assertEqual(result.exception.code, 2)

    def test_optional_notification_is_private_and_invalid_metadata_is_ignored(self):
        expiry = int(mail.time.time()) + 600
        notification = {'url': 'wss://ajax.turnkeylinux.org/initfence/v2/pairing',
                        'token': 'v1.' + 'a' * 32 + '.' + str(expiry) + '.' + 'b' * 64,
                        'expires_at': expiry}
        response = {'device_code': DEVICE_CODE, 'user_code': 'ABCD&1234', 'expires_in': 600,
                    'interval': 5, 'verification_uri': mail.HUB + '/connect', 'notification': notification}
        with patch.object(mail, 'request', return_value=response):
            public = self.client.start()
        self.assertNotIn('notification', public)
        self.assertNotIn(notification['token'], json.dumps(public))
        self.assertEqual(self.client.state['pairing']['notification'], notification)
        self.assertEqual((mail.STATE_DIR / 'state.json').stat().st_mode & 0o777, 0o600)
        response['notification'] = {**notification, 'url': 'wss://foreign.example/'}
        with patch.object(mail, 'request', return_value=response):
            self.client.start()
        self.assertNotIn('notification', self.client.state['pairing'])

    def test_pairing_rejects_foreign_verification_origin(self):
        with patch.object(mail, "request", return_value={"verification_uri": "https://attacker.example/"}):
            with self.assertRaises(mail.Error):
                self.client.start()

    def test_sender_rules_do_not_name_recipient_or_reply_headers(self):
        envelope, header = mail.sender_maps(CONFIG["domain"], CONFIG["sender"])
        self.assertIn("$1", envelope)
        self.assertIn("From|Sender", header)
        for name in ("Reply-To", "To:", "Cc:", "Bcc:"):
            self.assertNotIn(name, header)

    def test_test_message_uses_local_sendmail_and_reports_only_queue_acceptance(self):
        self.configured()
        result = self.client.test("recipient@example.net")
        self.assertEqual(result["status"], "queued")
        command, body = self.calls[-1]
        self.assertEqual(command[-2:], ["--", "recipient@example.net"])
        self.assertIn(b"To: recipient@example.net", body)
        with self.assertRaises(mail.Error):
            self.client.test("-X/tmp/evil")

    def test_config_injection_and_foreign_dns_update_are_rejected(self):
        for change in ({"sender": "noreply@fixture.tklapp.com\nBcc: victim@example.net"},
                       {"sender": "noreply@other.example"},
                       {"domain": "fixture.tklapp.com\n"},
                       {"dyndns": {"hostname": "fixture.tklapp.com", "update_uri": "https://attacker.example/"}}):
            with self.subTest(change=change), self.assertRaises(mail.Error):
                mail.checked_config(dict(CONFIG, **change), PASSWORD)

    def test_http_redirect_does_not_forward_credentials(self):
        self.assertIsNone(mail.NoRedirect().redirect_request(None, None, 302, "", {}, "https://attacker.example/"))


class PostfixTextTest(unittest.TestCase):
    def test_exact_parameter_replacement_preserves_comments_and_unrelated_keys(self):
        source = "# relayhost is explained here\nrelayhost = old\n  continued\nsender_relayhost = untouched\n"
        changed = mail.change_cf(source, {"relayhost": "new"})
        self.assertEqual(changed, "# relayhost is explained here\nsender_relayhost = untouched\nrelayhost = new\n")


if __name__ == "__main__":
    unittest.main()
