"""Exercise the installed maintainer script's narrowly owned unit migration."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
OLD = "/usr/lib/inithooks/bin/turnkey-mail"
NEW = "/usr/lib/inithooks/bin/turnkey-hub"


class ScheduledHelperUpgradeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.units = self.root / "etc/systemd/system"
        self.units.mkdir(parents=True)
        cache = self.root / "usr/lib/python3/dist-packages/libinithooks/inithooks_cache.py"
        cache.parent.mkdir(parents=True)
        cache.write_text("# fixture\n")
        (self.root / "run").mkdir()
        self.calls = self.root / "calls"
        systemctl = self.root / "systemctl"
        systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
        systemctl.chmod(0o755)
        source = (ROOT / "debian/postinst").read_text()
        for directory in ("/etc/systemd/system", "/usr/lib/python3", "/run"):
            source = source.replace(directory + "/", str(self.root) + directory + "/")
        self.script = self.root / "postinst"
        self.script.write_text(source)

    def run_postinst(self, *arguments):
        result = subprocess.run(
            ["sh", str(self.script), *arguments], capture_output=True, text=True,
            env=dict(os.environ, PATH=str(self.root) + ":/usr/bin:/bin",
                     CALL_LOG=str(self.calls)))
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.calls.read_text().splitlines()

    def unit(self, name, command):
        path = self.units / (name + ".service")
        path.write_text("[Unit]\nDescription=Retain this customization\n"
                        "[Service]\nType=oneshot\nExecStart=" + command +
                        "\nTimeoutStartSec=90\nUMask=0077\n")
        path.chmod(0o640)
        return path

    def test_migrates_only_owned_commands_and_preserves_other_state(self):
        paths = [(self.unit("turnkey-mail-retry", OLD + " retry"), "retry"),
                 (self.unit("turnkey-mail-dns", OLD + " dns-update"), "dns-update")]
        protected = [self.units / "turnkey-mail-retry.timer", self.units / "turnkey-mail-dns.timer",
                     self.root / "etc/cron.hourly/hubdns-update", self.root / "etc/postfix/main.cf",
                     self.root / "var/lib/turnkey-mail/state.json"]
        for path in protected:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("preserved private configuration\n")
        before = {path: (path.read_bytes(), path.stat()) for path, _ in paths}
        protected_before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected}
        calls = self.run_postinst("configure", "2.3.6+14")
        self.assertEqual(calls, ["disable --now turnkey-mail-retry.timer", "daemon-reload", "enable turnkey-onboarding.timer"])
        for path, command in paths:
            content, stat = before[path]
            self.assertEqual(path.read_bytes(), content.replace(
                ("ExecStart=" + OLD + " " + command + "\n").encode(),
                ("ExecStart=" + NEW + " " + command + "\n").encode()))
            self.assertEqual(path.stat().st_mode & 0o777, stat.st_mode & 0o777)
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (stat.st_uid, stat.st_gid))
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected},
                         protected_before)

    def test_repeat_upgrade_leaves_migrated_units_untouched(self):
        path = self.unit("turnkey-mail-retry", OLD + " retry")
        self.run_postinst("configure", "2.3.6+14")
        before = path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns
        self.run_postinst("configure", "2.3.6+25")
        self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), before)

    def test_unrelated_units_and_custom_commands_are_untouched(self):
        paths = [self.unit("unrelated", OLD + " retry"),
                 self.unit("turnkey-mail-retry", OLD + " retry --custom"),
                 self.unit("turnkey-mail-dns", "/opt/private/turnkey-mail dns-update")]
        before = {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in paths}
        self.run_postinst("configure", "2.3.6+14")
        self.assertEqual({path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                          for path in paths}, before)

    def test_symlinked_unit_is_not_rewritten(self):
        target = self.unit("custom-target", OLD + " retry")
        link = self.units / "turnkey-mail-retry.service"
        link.symlink_to(target)
        before = target.read_bytes(), target.stat().st_ino
        self.run_postinst("configure", "2.3.6+14")
        self.assertTrue(link.is_symlink())
        self.assertEqual((target.read_bytes(), target.stat().st_ino), before)

    def test_abort_upgrade_does_not_migrate_units(self):
        path = self.unit("turnkey-mail-retry", OLD + " retry")
        before = path.read_bytes(), path.stat().st_ino
        self.assertEqual(self.run_postinst("abort-upgrade", "2.3.6+14"), ["daemon-reload"])
        self.assertEqual((path.read_bytes(), path.stat().st_ino), before)

    def test_new_install_does_not_create_mail_schedules(self):
        self.assertEqual(self.run_postinst("configure"), [
            "daemon-reload", "enable turnkey-onboarding.timer", "enable inithooks.service",
            "enable turnkey-init-fence.service"])
        self.assertEqual(list(self.units.iterdir()), [])

    def test_metapackage_requires_migration_version(self):
        control = (ROOT / "debian/control").read_text().split("Package: turnkey-hub\n", 1)[1]
        self.assertIn("inithooks (= ${binary:Version})", control)


if __name__ == "__main__":
    unittest.main()
