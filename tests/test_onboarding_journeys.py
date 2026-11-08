"""Exercise setup decisions and shared app email defaults without a real terminal."""
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from libinithooks import onboarding

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'bin' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    dialog = types.ModuleType('libinithooks.dialog_wrapper')
    dialog.Dialog = Mock()
    dialog.EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+$')
    with patch.dict(sys.modules, {'libinithooks.dialog_wrapper': dialog}):
        spec.loader.exec_module(module)
    return module


class JourneysTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(onboarding, 'STATE_DIR', root / 'state').start()
        patch.object(onboarding, 'CACHE_DIR', root / 'cache').start()
        patch.object(onboarding, 'HUB_STATE', root / 'hub.json').start()
        self.hub = load('hubservices')
        self.alerts = load('secalerts')

    def names(self):
        return [e['event_name'] for e in onboarding.load()['pending']]

    def test_unattended_path_does_not_invoke_helper_or_dialog(self):
        with patch.object(self.hub.sys, 'stdin', io.StringIO('')), patch.object(self.hub, 'mail_call') as call:
            self.assertTrue(self.hub.early(unattended=True))
        call.assert_not_called()
        self.hub.Dialog.assert_not_called()
        self.assertIsNone(onboarding.decision())

    def test_headless_enrollment_precedes_default_and_never_opens_dialog(self):
        def enroll(command, **kwargs):
            self.assertEqual(kwargs['input'], 'token-private')
            self.assertNotIn('token-private', command)
            onboarding.remember_email('owner@example.net')
            return subprocess.CompletedProcess(command, 0, stdout='{"connection_status":"linked"}')
        with patch.object(self.hub.sys, 'stdin', io.StringIO('token-private')), \
                patch.object(self.hub.subprocess, 'run', side_effect=enroll):
            self.assertTrue(self.hub.early())
        self.assertEqual(onboarding.email_default('admin@example.com'), 'owner@example.net')
        self.assertEqual(onboarding.decision(), 'linked')
        self.hub.Dialog.assert_not_called()

    def test_offline_auto_enrollment_keeps_retry_state_without_skip(self):
        with patch.object(self.hub.sys, 'stdin', io.StringIO('token-private')), \
                patch.object(self.hub.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, stdout='{}')):
            self.assertTrue(self.hub.early())
        self.assertIsNone(onboarding.decision())
        self.assertIn('error', self.names())
        self.hub.Dialog.assert_not_called()

    def test_auto_enrollment_timeout_is_an_error_not_skip(self):
        with patch.object(self.hub.sys, 'stdin', io.StringIO('token-private')), \
                patch.object(self.hub.subprocess, 'run', side_effect=subprocess.TimeoutExpired('helper', 90)):
            self.assertTrue(self.hub.early())
        self.assertIsNone(onboarding.decision())
        self.assertEqual(self.names()[-1], 'error')

    def test_registration_command_uses_shared_ui_without_full_setup(self):
        source = (ROOT / 'bin/turnkey').read_text()
        self.assertIn('hubservices.py --relink', source)
        self.assertNotIn('turnkey-init', source)
        self.assertIn('usr/lib/inithooks/bin/turnkey usr/bin/turnkey', (ROOT / 'debian/rules').read_text())
        dialog = Mock()
        dialog.yesno.return_value = False
        with patch.object(sys, 'argv', ['hubservices.py', '--relink']), \
                patch.object(self.hub, 'Dialog', return_value=dialog), \
                patch.object(self.hub, 'mail_call', return_value={
                    'verification_uri': 'https://hub.turnkeylinux.org/link', 'user_code': 'D34C-F5A9'}):
            self.hub.main()
        self.assertEqual(onboarding.load()['flow_kind'], 'confconsole')

    def test_existing_link_uses_only_local_status(self):
        with patch.object(self.hub.sys, 'stdin', io.StringIO('')), \
                patch.object(self.hub, 'mail_call', return_value={'connection_status': 'linked'}) as call:
            self.assertTrue(self.hub.early())
        call.assert_called_once_with('status', '--local')
        self.assertEqual(onboarding.decision(), 'linked')

    def test_explicit_skip_is_recorded_and_does_not_poll(self):
        d = Mock()
        d.yesno.return_value = False
        with patch.object(self.hub, 'mail_call', return_value={
                'verification_uri': 'https://hub.turnkeylinux.org/link', 'user_code': 'D34C-F5A9'}) as call:
            self.hub.connect_mail(d)
        call.assert_called_once_with('connect', '--start')
        self.assertEqual(onboarding.decision(), 'skipped')
        self.assertEqual(d.yesno.call_args.args[-2:], ("I've entered the code", 'Skip'))

    def test_failed_link_check_stops_setup_without_recording_skip(self):
        d = Mock()
        d.yesno.return_value = True
        with patch.object(self.hub, 'mail_call', side_effect=[
                {'verification_uri': 'https://hub.turnkeylinux.org/link', 'user_code': 'D34C-F5A9'},
                {'status': 'error', 'message': 'Unavailable'}]), self.assertRaises(SystemExit) as error:
            self.hub.connect_mail(d)
        self.assertEqual(error.exception.code, 1)
        self.assertIsNone(onboarding.decision())
        self.assertEqual(self.names()[-2:], ['approval_checked', 'error'])

    def test_linked_user_gets_local_notifications_without_newsletter_prompt(self):
        onboarding.record('link_completed')
        with patch.object(sys, 'argv', ['secalerts.py', '--email=owner@example.net']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.alerts.Dialog.assert_not_called()
        self.assertEqual(run.call_args.args[0][-1], 'owner@example.net')
        self.assertNotIn('newsletter_choice', self.names())
        self.assertNotIn('email_provided', self.names())

    def test_skipped_user_can_decline_newsletter_without_affecting_local_notifications(self):
        onboarding.record('skipped')
        self.alerts.Dialog.return_value.inputbox.return_value = ('cancel', 'chosen@example.net')
        with patch.object(sys, 'argv', ['secalerts.py', '--email=chosen@example.net']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(onboarding.load()['pending'][-1]['newsletter_consent'])

    def test_skipped_relink_preserves_linked_account_newsletter_exclusion(self):
        onboarding.record('skipped')
        onboarding.HUB_STATE.write_text('{"link":{"control_secret":"existing-link"}}')
        onboarding.start_flow('confconsole')
        onboarding.record('skipped')
        with patch.object(sys, 'argv', ['secalerts.py', '--email=owner@example.net']), \
                patch.object(self.alerts.subprocess, 'run'):
            self.alerts.main()
        self.alerts.Dialog.assert_not_called()
        self.assertNotIn('email_provided', self.names())
        self.assertNotIn('newsletter_choice', self.names())

    def test_skipped_user_affirmatively_subscribes_separately(self):
        onboarding.record('skipped')
        self.alerts.Dialog.return_value.inputbox.return_value = ('ok', 'chosen@example.net')
        with patch.object(sys, 'argv', ['secalerts.py', '--email=chosen@example.net']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][-2:], ['chosen@example.net', '--newsletter-only'])

    def test_newsletter_shows_editable_email_and_recovers_from_empty_or_invalid_input(self):
        onboarding.record('skipped')
        dialog = self.alerts.Dialog.return_value
        dialog.inputbox.side_effect = [('ok', ''), ('ok', 'invalid'), ('ok', 'different@example.net')]
        with patch.object(sys, 'argv', ['secalerts.py', '--email=local@example.net']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.assertEqual(dialog.inputbox.call_args_list[0].args[-3:], ('local@example.net', 'Subscribe', 'No thanks'))
        self.assertEqual(dialog.error.call_count, 2)
        self.assertEqual(run.call_args.args[0][-2:], ['different@example.net', '--newsletter-only'])
        self.assertTrue(run.call_args.kwargs['capture_output'])
        self.assertEqual(sum(event['event_name'] == 'newsletter_choice' for event in onboarding.load()['pending']), 1)

    def test_linked_account_email_configures_local_notifications_without_reasking(self):
        onboarding.record('link_completed')
        onboarding.remember_email('owner@example.net')
        with patch.object(sys, 'argv', ['secalerts.py', '--email-placeholder=admin@example.com']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.alerts.Dialog.assert_not_called()
        self.assertEqual(run.call_args.args[0][-1], 'owner@example.net')
        self.assertNotIn('email_provided', self.names())
        self.assertNotIn('newsletter_choice', self.names())

    def test_linked_old_api_without_email_offers_only_optional_local_recipient(self):
        onboarding.record('link_completed')
        dialog = self.alerts.Dialog.return_value
        dialog.inputbox.return_value = ('ok', 'chosen@example.net')
        dialog.yesno.return_value = True
        with patch.object(sys, 'argv', ['secalerts.py', '--email-placeholder=custom@example.net']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.assertEqual(dialog.inputbox.call_args.args[2], 'custom@example.net')
        self.assertEqual(run.call_args.args[0][-1], 'chosen@example.net')
        self.assertEqual(dialog.yesno.call_count, 1)  # email confirmation, not newsletter
        self.assertEqual(onboarding.decision(), 'linked')



if __name__ == '__main__':
    unittest.main()
