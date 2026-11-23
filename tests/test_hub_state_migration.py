"""Verify the package migration preserves live Hub identity and locks."""
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HubStateMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root / 'turnkey-mail'
        self.new = self.root / 'turnkey-hub'
        source = (ROOT / 'debian/preinst').read_text()
        self.script = self.root / 'preinst'
        self.script.write_text(source.replace('/var/lib/turnkey-mail', str(self.old))
                               .replace('/var/lib/turnkey-hub', str(self.new)))

    def run_migration(self, action='upgrade'):
        return subprocess.run(['sh', str(self.script), action], capture_output=True, text=True)

    def test_upgrade_preserves_live_files_and_lock_inodes(self):
        self.old.mkdir(mode=0o700)
        for name in ('state.json', 'registration.json', 'provision-pending', 'lock', 'registration.lock'):
            (self.old / name).write_text(name)
        original = self.old.stat().st_ino
        lock = (self.old / 'lock').open('r+')
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = self.run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.old.is_symlink())
        self.assertEqual(os.readlink(self.old), 'turnkey-hub')
        self.assertEqual(self.new.stat().st_ino, original)
        for name in ('state.json', 'registration.json', 'provision-pending'):
            self.assertEqual((self.new / name).read_text(), name)
            self.assertEqual((self.old / name).read_text(), name)
        with (self.new / 'lock').open('r+') as other:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(self.new.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.run_migration().returncode, 0)

    def test_restart_after_interruption_before_exchange(self):
        self.old.mkdir()
        (self.old / 'state.json').write_text('saved')
        self.new.symlink_to('turnkey-hub')
        self.assertEqual(self.run_migration().returncode, 0)
        self.assertEqual((self.new / 'state.json').read_text(), 'saved')
        self.assertTrue(self.old.is_symlink())

    def test_conflicting_state_is_not_overwritten(self):
        self.old.mkdir()
        self.new.mkdir()
        (self.old / 'state.json').write_text('old')
        (self.new / 'state.json').write_text('new')
        result = self.run_migration()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.old / 'state.json').read_text(), 'old')
        self.assertEqual((self.new / 'state.json').read_text(), 'new')

    def test_fresh_install_leaves_no_state_and_abort_does_not_move(self):
        self.assertEqual(self.run_migration('install').returncode, 0)
        self.assertFalse(self.old.exists())
        self.assertFalse(self.new.exists())
        self.old.mkdir()
        self.assertEqual(self.run_migration('abort-upgrade').returncode, 0)
        self.assertTrue(self.old.is_dir())
        self.assertFalse(self.new.exists())

    def test_package_requires_python_before_migration_and_protected_restore_version(self):
        paragraphs = (ROOT / 'debian/control').read_text().split('\n\n')
        package = next(part for part in paragraphs if part.startswith('Package: inithooks\n'))
        self.assertIn('Pre-Depends: python3 (>= 3.13~)', package)
        self.assertIn(' tklbam (<< 1.5.8~),', package)
        self.assertEqual(subprocess.run(
            ['dpkg', '--compare-versions', '1.5.7', 'lt', '1.5.8~']).returncode, 0)
        self.assertEqual(subprocess.run(
            ['dpkg', '--compare-versions', '1.5.8', 'ge', '1.5.8~']).returncode, 0)


if __name__ == '__main__':
    unittest.main()
