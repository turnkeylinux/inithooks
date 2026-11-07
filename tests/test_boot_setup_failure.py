"""Exercise the Bash boot runner without system services or host state changes."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BootSetupFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.firstboot = self.root / "hooks/firstboot.d"
        self.everyboot = self.root / "hooks/everyboot.d"
        self.commands = self.root / "commands"
        for directory in (self.firstboot, self.everyboot, self.commands):
            directory.mkdir(parents=True)
        self.defaults = self.root / "defaults"
        self.preseeds = self.root / "preseeds"
        self.preseeds.write_text("FIXTURE_PRESEED=retained\n")
        self.log = self.root / "inithooks.log"
        self.info = self.root / "info"
        self.info.mkdir()
        (self.info / "xen").touch()  # Normal tests do not need a console tail.
        self.completed = self.root / "everyboot-complete"
        # Redirect only the fixed volatile marker; execute the real Bash logic.
        self.runner = self.root / "run"
        self.runner.write_text((ROOT / "run").read_text().replace(
            "/run/inithooks-complete", str(self.completed)))
        self.write_defaults()
        for command in ("logger", "sleep"):
            self.command(command, "exit 0\n")
        for command in ("systemctl", "confconsole"):
            self.command(command, 'printf "%s\\n" "$*" >> ' +
                         shlex.quote(str(self.root / (command + ".calls"))) +
                         '\nexit 0\n')
        self.hook(self.firstboot, "80optional", "exit 0\n")
        self.hook(self.firstboot, "97turnkey-init-fence-disable",
                  "touch " + shlex.quote(str(self.root / "unfenced")) + "\n")
        self.hook(self.firstboot, "98finalize",
                  "touch " + shlex.quote(str(self.root / "finalized")) + "\n"
                  "sed -i 's/RUN_FIRSTBOOT=true/RUN_FIRSTBOOT=false/' " +
                  shlex.quote(str(self.defaults)) + "\n"
                  ": > " + shlex.quote(str(self.preseeds)) + "\n")
        self.hook(self.everyboot, "01everyboot",
                  "touch " + shlex.quote(str(self.root / "everyboot-ran")) + "\n")

    def executable(self, path, content):
        path.write_text("#!/bin/bash\n" + content)
        path.chmod(0o755)

    def command(self, name, content):
        self.executable(self.commands / name, content)

    def hook(self, directory, name, content):
        self.executable(directory / name, content)

    def write_defaults(self, *, firstboot=True, redirect=True):
        values = {
            "INITHOOKS_PATH": str(self.firstboot.parent),
            "INITHOOKS_CONF": str(self.preseeds),
            "INITHOOKS_LOGFILE": str(self.log),
            "TKLINFO": str(self.info),
            "RUN_FIRSTBOOT": str(firstboot).lower(),
            "REDIRECT_OUTPUT": str(redirect).lower(),
        }
        self.defaults.write_text("".join(
            name + "=" + shlex.quote(value) + "\n"
            for name, value in values.items()))

    def run_boot(self):
        environment = os.environ.copy()
        environment.update({
            "INITHOOKS_DEFAULT": str(self.defaults),
            "PATH": str(self.commands) + os.pathsep + environment["PATH"],
        })
        return subprocess.run(["bash", str(self.runner)], env=environment,
                              capture_output=True, text=True, timeout=10)

    def test_application_failure_or_cancel_stops_finalization_and_can_retry(self):
        for status in (1, 130):
            with self.subTest(status=status):
                self.hook(self.firstboot, "40application", f"exit {status}\n")
                result = self.run_boot()
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn("Setup stopped at 40application", result.stdout)
                self.assertIn("run turnkey-init again", result.stdout)
                self.assertIn(f"failed - exit code {status}", self.log.read_text())
                self.assertNotIn("Inithooks run completed", self.log.read_text())
                self.assertFalse((self.root / "unfenced").exists())
                self.assertFalse((self.root / "finalized").exists())
                self.assertFalse(self.completed.exists())
                self.assertIn("RUN_FIRSTBOOT=true", self.defaults.read_text())
                self.assertEqual(self.preseeds.read_text(), "FIXTURE_PRESEED=retained\n")
                self.assertFalse((self.root / "confconsole.calls").exists())
        self.hook(self.firstboot, "40application", "exit 0\n")
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "unfenced").exists())
        self.assertTrue((self.root / "finalized").exists())
        self.assertTrue(self.completed.exists())
        self.assertIn("RUN_FIRSTBOOT=false", self.defaults.read_text())
        self.assertEqual(self.preseeds.read_text(), "")

    def test_optional_skip_zero_and_nonexecutable_hook_allow_completion(self):
        self.hook(self.firstboot, "40application", "exit 0\n")
        skipped = self.firstboot / "41disabled"
        skipped.write_text("#!/bin/bash\nexit 1\n")
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "finalized").exists())
        self.assertIn("[80optional] successfully completed", self.log.read_text())
        self.assertIn("[41disabled] skipping", self.log.read_text())

    def test_live_media_security_update_status_allows_completion(self):
        self.hook(self.firstboot, "95secupdates", "exit 2\n")
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "finalized").exists())
        self.assertIn("detected live system - skipping", self.log.read_text())

    def test_security_update_reboot_status_finalizes_before_reboot(self):
        self.hook(self.firstboot, "95secupdates", "exit 42\n")
        self.command("systemctl",
                     "test -e " + shlex.quote(str(self.root / "finalized")) +
                     " || exit 23\n"
                     'printf "%s\\n" "$*" > ' +
                     shlex.quote(str(self.root / "systemctl.calls")) + "\n")
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "finalized").exists())
        self.assertEqual((self.root / "systemctl.calls").read_text(), "reboot\n")
        self.assertIn("reboot is required", self.log.read_text())

    def test_other_security_update_failure_stops_completion(self):
        self.hook(self.firstboot, "95secupdates", "exit 1\n")
        result = self.run_boot()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.root / "unfenced").exists())

    def test_everyboot_failure_keeps_existing_continue_behavior(self):
        self.write_defaults(firstboot=False)
        self.hook(self.everyboot, "01everyboot", "exit 1\n")
        self.hook(self.everyboot, "02later",
                  "touch " + shlex.quote(str(self.root / "later-everyboot")) + "\n")
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "later-everyboot").exists())
        self.assertTrue(self.completed.exists())

    def test_failed_firstboot_cleans_existing_headless_log_tail(self):
        (self.info / "xen").unlink()
        self.command("cat", 'if [[ "$1" == /sys/devices/virtual/tty/tty0/active ]]; then\n'
                     '    echo null\nelse\n    exec /bin/cat "$@"\nfi\n')
        tail_pid = self.root / "tail.pid"
        self.command("tail", "echo $$ > " + shlex.quote(str(tail_pid)) +
                     "\nexec /bin/sleep 60\n")
        self.hook(self.firstboot, "40application",
                  "for attempt in {1..100}; do\n"
                  "    test -f " + shlex.quote(str(tail_pid)) + " && exit 1\n"
                  "    /bin/sleep 0.01\ndone\nexit 24\n")
        result = self.run_boot()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(tail_pid.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(tail_pid.read_text()), 0)
        self.assertIn("Killing inithooks pid", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
