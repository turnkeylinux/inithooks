"""Generic appliance mail boundaries, independent of any application."""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("turnkey_mail", str(ROOT / "bin/turnkey-mail"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mail = importlib.util.module_from_spec(spec)
loader.exec_module(mail)

TOKEN = "t" * 43
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
        self.fail = None

        def run(command, data=None, timeout=30):
            self.calls.append((command, data))
            if command[0] == "postmap":
                Path(command[1][5:] + ".db").write_bytes(b"compiled-map")
            if command == self.fail:
                raise mail.Error("command failed", retryable=True)
            return b""

        self.addCleanup(patch.stopall)
        patch.object(mail, "run", side_effect=run).start()
        patch.object(mail, "smtp_check").start()
        patch.object(mail.Mail, "legacy_dns", return_value=True).start()
        self.client = mail.Mail()

    def configured(self):
        config = mail.checked_config(CONFIG, PASSWORD)
        config["dns_password"] = DNS_PASSWORD
        self.client.apply(config)
        return config

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

    def test_completed_enrollment_is_a_noop_when_firstboot_repeats_token(self):
        with patch.object(mail, "request", return_value=CONFIG):
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
        self.fail = ["postfix", "reload"]
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
            self.assertEqual(saved["password"], payload["password"])
            raise mail.Error("network unavailable", retryable=True)

        with patch.object(mail, "request", side_effect=unavailable):
            with self.assertRaises(mail.Error):
                self.client.enroll(TOKEN)
        # Firstboot's preseed file is absent; a new process uses only state.
        retry = mail.Mail()
        with patch.object(mail, "request", return_value=CONFIG) as request:
            result = retry.retry()
        self.assertEqual(request.call_args.args, ("enroll", first[0]))
        self.assertEqual(result["status"], "ready")
        self.assertNotEqual(first[0]["password"], first[0]["dns_password"])
        self.assertNotIn("pending", retry.state)
        self.assertNotIn(TOKEN, json.dumps(retry.state))

    def test_new_direct_sender_is_held_during_failed_enrollment(self):
        self.main.write_text("myhostname = original.example\n")
        with patch.object(mail, "request", side_effect=mail.Error("offline", retryable=True)):
            with self.assertRaises(mail.Error):
                self.client.enroll(TOKEN)
        self.assertEqual(mail.cf_entries(self.main.read_text())["defer_transports"], "smtp")
        with patch.object(mail, "request", return_value=CONFIG):
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
        response = {"device_code": TOKEN, "user_code": "ABCD&1234", "expires_in": 900, "interval": 5,
                    "verification_uri": mail.HUB + "/link"}
        with patch.object(mail, "request", return_value=response):
            public = self.client.start()
        self.assertNotIn(TOKEN, json.dumps(public))
        with patch.object(mail, "request", return_value={"status": "pending"}):
            self.assertEqual(self.client.poll()["status"], "pending")
        self.assertEqual(self.main.read_bytes(), original)

    def test_pairing_enforces_poll_interval_without_network(self):
        self.client.state["pairing"] = {"expires": mail.time.time() + 900, "next_poll": mail.time.time() + 30,
                                       "interval": 5, "device_code": TOKEN}
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
