"""Installed hook discovery and isolated reconfiguration execution."""
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import time
import tempfile
import unittest
from unittest.mock import Mock, patch

from libinithooks import reconfigure as rc


class ReconfigureTest(unittest.TestCase):
    def setUp(self):
        logging_patch = patch('logging.basicConfig')
        logging_patch.start()
        self.addCleanup(logging_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.legacy = self.root / 'legacy.json'
        self.legacy.write_text('{}')

    def hook(self, name, body):
        p = self.root / name
        p.write_text('#!/bin/sh\n' + body)
        p.chmod(0o755)
        return p

    def discover(self):
        return rc.discover(self.root, self.legacy)

    def test_unannotated_hook_defaults_to_title_and_opening_comment(self):
        self.hook('40test-app', '# Copyright example\n# shellcheck disable=SC1091\n# Change the app password.\nexit 0\n')
        self.assertEqual(self.discover(), [rc.Hook('40test-app', 'Test App', 'Change the app password.')])

    def test_annotations_override_legacy_and_opt_out_hides_app(self):
        self.legacy.write_text(json.dumps({'40wordpress': {'enabled': False, 'title': 'Old'}}))
        self.hook('40wordpress', '# reconfigure: yes\n# reconfigure-title: WordPress\n# reconfigure-description: Password and domain.\nexit 0\n')
        self.hook('41private', '# reconfigure: no\nexit 0\n')
        self.assertEqual(self.discover(), [rc.Hook('40wordpress', 'WordPress', 'Password and domain.')])

    def test_legacy_exclusion_applies_only_without_explicit_annotation(self):
        self.legacy.write_text(json.dumps({'30rootpass': {'enabled': False}}))
        root = self.hook('30rootpass', 'exit 0\n')
        self.assertEqual(self.discover(), [])
        root.write_text('#!/bin/sh\n# reconfigure: yes\nexit 0\n')
        self.assertEqual(len(self.discover()), 1)

    def test_comments_inside_program_are_not_metadata_and_discovery_never_executes(self):
        marker = self.root / 'should-not-exist'
        self.hook('40custom', f'touch {marker}\n# reconfigure: no\n')
        self.assertEqual(len(self.discover()), 1)
        self.assertFalse(marker.exists())

    def test_nonexecutables_and_malformed_opt_in_are_hidden(self):
        self.hook('40disabled', 'exit 0').chmod(0o644)
        self.hook('41invalid', '# reconfigure: maybe\nexit 0')
        self.assertEqual(self.discover(), [])

    def test_explicit_opt_in_runs_container_disabled_hook_without_chmod(self):
        marker = self.root / 'changed'
        path = self.hook('30rootpass', '# reconfigure: yes\n' + f'touch {marker}\n')
        path.chmod(0o644)
        hooks = self.discover()
        self.assertEqual(len(hooks), 1)
        self.assertEqual(rc.execute(hooks[0], self.root), 0)
        self.assertTrue(marker.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_execute_runs_only_selected_hook_without_inherited_preseeds(self):
        marker = self.root / 'result'
        self.hook('40selected', f'printf "%s %s %s" "$_TURNKEY_INIT" "$_TURNKEY_RECONFIGURE" "${{ROOT_PASS-unset}}" > {marker}\n')
        self.hook('41other', 'exit 91')
        with patch.dict(os.environ, {'ROOT_PASS': 'must-not-pass'}):
            self.assertEqual(rc.execute(rc.Hook('40selected', '', ''), self.root), 0)
        self.assertEqual(marker.read_text(), '1 1 unset')

    def test_interrupt_waits_for_password_helper_before_returning(self):
        ready, done = self.root / 'ready', self.root / 'done'
        helper = self.root / 'helper.py'
        helper.write_text('import signal,time\nfrom pathlib import Path\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n'
                          + f'Path({str(ready)!r}).touch()\ntime.sleep(.3)\nPath({str(done)!r}).touch()\n')
        self.hook('40wait', f'{sys.executable} {helper}\n')
        script = ('from pathlib import Path; from libinithooks.reconfigure import execute,Hook; '
                  + f'execute(Hook("40wait", "", ""),Path({str(self.root)!r}))')
        child = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists())
            child.send_signal(signal.SIGINT)
            stdout, stderr = child.communicate(timeout=3)
            self.assertEqual(child.returncode, 0, stderr.decode())
            self.assertTrue(done.exists())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_setup_pending_understands_quoted_setting(self):
        config = self.root / 'defaults'
        for value, expected in [('true', True), ('"true"', True), ('false', False)]:
            config.write_text('RUN_FIRSTBOOT=' + value + '\n')
            self.assertEqual(rc.setup_pending(config), expected)

    def test_packaged_core_optouts_and_legacy_app_exclusions(self):
        source = Path(__file__).resolve().parents[1]
        names = {h.name for h in rc.discover(source / 'firstboot.d')}
        self.assertIn('30rootpass', names)
        self.assertNotIn('98finalize', names)
        self.assertNotIn('30hub-link', names)
        self.assertNotIn('01ipconfig', names)
        legacy = json.loads(rc.LEGACY.read_text())
        for name in ('29preseed', '40openldap', '40openvpn', '40wireguard', '50jellyfin-server'):
            self.assertFalse(legacy[name]['enabled'])
        for name in ('40wordpress', '40nextcloud', '35adminer-mysqlpass'):
            self.assertTrue(legacy[name]['enabled'])
        self.assertNotIn('98finalize', legacy)

    def test_failure_and_cancel_return_to_menu(self):
        self.hook('40example', '# Change an example setting.\nexit 1')
        d = Mock()
        d.console.menu.side_effect = [('ok', '1'), ('ok', '1'), ('cancel', '')]
        with patch('libinithooks.dialog_wrapper.Dialog', return_value=d), patch.object(rc, 'setup_pending', return_value=False), patch.object(rc, 'execute', side_effect=[1, 130]):
            self.assertEqual(rc.menu(self.root), 0)
        self.assertEqual(d.console.menu.call_count, 3)
        d.console.msgbox.assert_called_once()

    def test_empty_menu_and_pending_setup_do_not_execute(self):
        for pending in (True, False):
            d = Mock()
            with patch('libinithooks.dialog_wrapper.Dialog', return_value=d), patch.object(rc, 'setup_pending', return_value=pending), patch.object(rc, 'execute') as execute:
                rc.menu(self.root)
            execute.assert_not_called()
            d.console.msgbox.assert_called_once()


if __name__ == '__main__':
    unittest.main()
