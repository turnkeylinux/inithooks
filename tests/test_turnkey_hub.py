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
        self.patches = [patch.object(mail, "STATE_DIR", self.root / "state"),
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

    def test_backup_auth_conversion_preserves_encryption_and_history(self):
        backup_dir = self.root / "backup"
        (backup_dir / "profile").mkdir(parents=True)
        originals = {"secret": "keep-secret", "key": "keep-key", "hbr": "keep-history", "profile/profile_id": "custom"}
        for name, content in originals.items():
            (backup_dir / name).write_text(content)
        (backup_dir / "sub_apikey").write_text("old-account-key")
        (backup_dir / "credentials").write_text("old-cached-provider-creds")
        self.client.state.update(link={"mode": "hub", "device_id": CONFIG["device_id"]}, backup_secret="s" * 43)
        with patch.object(mail, "BACKUP_DIR", backup_dir):
            self.client.backup_init()
        for name, content in originals.items():
            self.assertEqual(content, (backup_dir / name).read_text())
        self.assertTrue((backup_dir / "sub_apikey").read_text().startswith("tklbam2."))
        self.assertFalse((backup_dir / "credentials").exists())
        self.assertFalse(any(call[0][0] == "tklbam-init" for call in self.calls))

    def test_failed_backup_init_retries_after_token_is_saved(self):
        backup_dir = self.root / "uninitialized-backup"
        self.client.state.update(link={"mode": "hub", "device_id": CONFIG["device_id"]}, backup_secret="s" * 43)
        self.fail_command = ["tklbam-init"]
        with patch.object(mail, "BACKUP_DIR", backup_dir):
            with self.assertRaises(mail.Error) as caught:
                self.client.backup_init()
            self.assertIn("Backup initialization", str(caught.exception))
            self.assertTrue((backup_dir / "sub_apikey").exists())
            self.fail_command = None
            self.client.backup_init()
        self.assertEqual(2, sum(call[0] == ["tklbam-init"] for call in self.calls))

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

    def test_legacy_dns_retires_only_after_same_hostname_scoped_success(self):
        legacy = self.root / "legacy-dns"
        legacy.mkdir()
        (legacy / "fqdn").write_text(CONFIG["domain"])
        key = legacy / "sub_apikey"
        key.write_text("legacy-key")
        cron = self.root / "legacy-cron"
        cron.write_text("updater")
        cron.chmod(0o755)
        self.client.state["link"] = {"mode": "hub", "device_id": CONFIG["device_id"], "dyndns": CONFIG["dyndns"]}
        def path(value):
            return cron if str(value) == "/etc/cron.hourly/hubdns-update" else Path(value)
        with patch.object(mail, "HUBDNS_DIR", legacy), patch.object(mail, "Path", side_effect=path), \
                patch.object(self.client, "dns_update", side_effect=[{}, mail.Error("offline", retryable=True)]):
            with self.assertRaises(mail.Error):
                self.client.migrate_dns()
        self.assertTrue(key.exists())
        self.assertTrue(cron.stat().st_mode & 0o111)
        self.assertEqual(self.calls[-1][0], ["systemctl", "enable", "--now", "hubdns.service"])
        self.assertIn("updater was restored", self.client.state["legacy_dns"])
        with patch.object(mail, "HUBDNS_DIR", legacy), patch.object(mail, "Path", side_effect=path), \
                patch.object(self.client, "dns_update", return_value={}) as update:
            self.client.migrate_dns()
        self.assertEqual(update.call_count, 2)
        self.assertFalse(key.exists())
        self.assertFalse(cron.stat().st_mode & 0o111)

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

    def test_sync_timer_noop_does_not_reload_systemd(self):
        self.client.timers()
        service = (mail.SYSTEMD_DIR / "turnkey-mail-retry.service").read_text()
        self.assertIn("ExecStart=/usr/lib/inithooks/bin/turnkey-hub retry\n", service)
        self.assertFalse((ROOT / "bin/turnkey-mail").exists())
        self.assertTrue(os.access(ROOT / "bin/turnkey-hub", os.X_OK))
        self.client.timers(dns=True)
        self.assertIn("ExecStart=/usr/lib/inithooks/bin/turnkey-hub dns-update\n",
                      (mail.SYSTEMD_DIR / "turnkey-mail-dns.service").read_text())
        before = len(self.calls)
        self.client.timers()
        self.assertEqual(before, len(self.calls))

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
                    "interval": 5, "verification_uri": mail.HUB + "/link"}
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

    def test_dns_update_uses_only_its_independent_capability(self):
        self.configured()
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"203.0.113.8\n"
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(mail.Mail, "legacy_dns", return_value=False), \
                patch.object(mail.urllib.request, "build_opener", return_value=opener), \
                patch.object(mail, "request", return_value={"status": "updated"}) as request:
            self.client.dns_update()
        route, payload = request.call_args.args
        self.assertEqual(route, "dns/update")
        self.assertEqual(payload["dns_password"], DNS_PASSWORD)
        self.assertNotIn("password", payload)
        self.assertNotIn("hostname", payload)
        self.assertEqual(payload["ipv4"], "203.0.113.8")

    def assert_dns_updates(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"203.0.113.8\n"
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(mail.Mail, "legacy_dns", return_value=False), \
                patch.object(mail.urllib.request, "build_opener", return_value=opener), \
                patch.object(mail, "request", return_value={"status": "updated"}) as request:
            result = mail.Mail().dns_update()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(request.call_args.args, ("dns/update", {
            "username": CONFIG["smtp"]["username"], "dns_password": DNS_PASSWORD,
            "ipv4": "203.0.113.8"}))

    def test_custom_smtp_preserves_linked_dns(self):
        self.configured()
        custom = mail.checked_config({"smtp": {"host": "custom.example.net", "port": 587,
                                               "username": "custom-user"}}, "custom-secret", custom=True)
        self.client.apply(custom)
        self.assertEqual(mail.Mail().state["active"]["mode"], "custom")
        self.assert_dns_updates()

    def test_mail_only_reset_preserves_link_and_dns_timer(self):
        self.configured()
        self.client.timers(dns=True)
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

    def test_legacy_dns_initialized_after_enrollment_owns_later_updates(self):
        self.configured()
        ready = [False]
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"203.0.113.8\n"
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(mail.Mail, "legacy_dns", new=LEGACY_DNS), \
                patch.object(mail.Path, "exists", autospec=True,
                             side_effect=lambda path: ready[0] and str(path) == "/var/lib/hubdns/sub_apikey"), \
                patch.object(mail.os, "access", return_value=True), \
                patch.object(mail.urllib.request, "build_opener", return_value=opener), \
                patch.object(mail, "request", return_value={"status": "updated"}) as request:
            self.assertEqual(self.client.dns_update()["status"], "updated")
            ready[0] = True  # Existing hook80 initializes HubDNS after enrollment.
            request.reset_mock()
            opener.reset_mock()
            self.assertEqual(self.client.dns_update()["status"], "unchanged")
            request.assert_not_called()
            opener.open.assert_not_called()

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

    def test_pairing_only_returns_public_fields_and_pending_keeps_current_relay(self):
        original = self.main.read_bytes()
        response = {"device_code": DEVICE_CODE, "user_code": "ABCD&1234", "expires_in": 900, "interval": 5,
                    "verification_uri": mail.HUB + "/link"}
        with patch.object(mail, "request", return_value=response):
            public = self.client.start()
        self.assertNotIn(DEVICE_CODE, json.dumps(public))
        with patch.object(mail, "request", return_value={"status": "pending"}):
            self.assertEqual(self.client.poll()["status"], "pending")
        self.assertEqual(self.main.read_bytes(), original)

    def test_pairing_accepts_legacy_and_new_wire_codes_with_one_display_format(self):
        for code in ("D34C&F5A9", "D34C-F5A9"):
            with self.subTest(code=code):
                response = {"device_code": DEVICE_CODE, "user_code": code,
                            "expires_in": 900, "interval": 5,
                            "verification_uri": mail.HUB + "/link"}
                with patch.object(mail, "request", return_value=response) as request:
                    public = self.client.start()
                request.assert_called_once_with("device/start", {})
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
                            "verification_uri": mail.HUB + "/link"}
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
                    "expires_in": 900, "interval": 5, "verification_uri": mail.HUB + "/link"}
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
