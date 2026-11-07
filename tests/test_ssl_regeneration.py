"""Run the real certificate hook with private service and generator boundaries."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HOOK = Path(__file__).resolve().parents[1] / "firstboot.d/15regen-sslcert"

class CertificateReloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = self.root / "calls"
        self.commands = self.root / "commands"
        self.commands.mkdir()
        self.env = dict(os.environ, PATH=str(self.commands) + ":" + os.environ["PATH"],
                        INITHOOKS_CONF=str(self.root / "absent-preseed"),
                        CALLS=str(self.calls), ACTIVE_SERVICES="apache2 webmin")
        self.env.pop("_TURNKEY_INIT", None)
        for name, body in {
            "turnkey-make-ssl-cert": 'echo "generate $*" >> "$CALLS"',
            "openssl": 'echo fixture-matching-modulus',
            "logger": "exit 0",
            "sleep": "exit 0",
            "update-ca-certificates": 'echo update-ca >> "$CALLS"',
            "systemctl": '''if [ "$1" = is-active ]; then
  case " $ACTIVE_SERVICES " in *" ${3%.service} "*) exit 0;; *) exit 3;; esac
fi
printf '%s\n' "$*" >> "$CALLS"
[ "${RESTART_FAILURE:-}" != "${3:-}" ] || exit 7
''',
        }.items():
            path = self.commands / name
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)

    def run_hook(self):
        return subprocess.run([str(HOOK)], env=self.env,
                              capture_output=True, text=True, timeout=3)

    def test_running_webmin_and_existing_consumer_restart_after_generation(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(), [
            "generate --default --force", "restart --quiet apache2",
            "restart --quiet webmin", "update-ca"])

    def test_inactive_webmin_is_not_started(self):
        self.env["ACTIVE_SERVICES"] = "apache2"
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertNotIn("webmin", self.calls.read_text())
        self.assertIn("restart --quiet apache2", self.calls.read_text())

    def test_manual_initialization_keeps_existing_certificate_untouched(self):
        self.env["_TURNKEY_INIT"] = "1"
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_restart_failure_is_not_silenced(self):
        self.env["RESTART_FAILURE"] = "webmin"
        self.assertEqual(self.run_hook().returncode, 7)
        self.assertNotIn("update-ca", self.calls.read_text())

if __name__ == "__main__":
    unittest.main()
