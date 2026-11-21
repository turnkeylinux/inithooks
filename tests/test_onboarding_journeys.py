"""Exercise setup decisions and shared app email defaults without a real terminal."""
import importlib.util
import io
import os
import signal
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
        launcher = patch('libinithooks.hub_runtime.start_observations')
        launcher.start()
        self.addCleanup(launcher.stop)
        patch.object(onboarding, 'STATE_DIR', root / 'state').start()
        patch.object(onboarding, 'CACHE_DIR', root / 'cache').start()
        patch.object(onboarding, 'HUB_STATE', root / 'hub.json').start()
        self.hub = load('hubservices')
        self.confirm = patch.object(self.hub.hub_push, 'confirm', return_value='check').start()
        self.alerts = load('secalerts')

    def names(self):
        return [e['event_name'] for e in onboarding.load()['pending']]

    def test_interruption_exits_and_dialog_failure_skips(self):
        with patch.object(self.hub, '_connect_mail', side_effect=KeyboardInterrupt), patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as result:
                self.hub.connect_mail(Mock())
            self.assertEqual(result.exception.code, 130)
        with patch.object(self.hub, '_connect_mail', side_effect=self.hub.DialogError("backend failed")), patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.hub.connect_mail(Mock())
            self.assertEqual(onboarding.decision(), 'skipped')
            self.assertIn('sudo turnkey connect', stderr.getvalue())

    def test_unattended_path_does_not_invoke_helper_or_dialog(self):
        progress = Mock()
        with patch.object(self.hub.sys, 'stdin', io.StringIO('')), patch.object(self.hub, 'mail_call') as call:
            self.assertTrue(self.hub.early(unattended=True, progress=progress))
        progress.assert_not_called()
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
        source = (ROOT / 'bin/turnkey-hub').read_text()
        self.assertIn('["hubservices.py", "--setup"]', source)
        self.assertNotIn('turnkey-init', source)
        self.assertIn('usr/lib/inithooks/bin/turnkey-hub usr/bin/turnkey-hub', (ROOT / 'debian/turnkey-hub.links').read_text())
        dialog = Mock()
        dialog.yesno.return_value = False
        self.confirm.return_value = 'skip'
        with patch.object(sys, 'argv', ['hubservices.py', '--relink']), \
                patch.object(self.hub, 'Dialog', return_value=dialog), \
                patch.object(self.hub, 'mail_call', return_value={
                    'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'}):
            self.hub.main()
        self.assertEqual(onboarding.load()['flow_kind'], 'confconsole')

    def test_setup_skips_an_existing_connection_without_a_network_request(self):
        with patch.object(sys, 'argv', ['hubservices.py', '--setup']), \
                patch.object(self.hub.os, 'geteuid', return_value=0), \
                patch.object(sys.stdin, 'isatty', return_value=True), \
                patch.object(sys.stdout, 'isatty', return_value=True), \
                patch.object(self.hub, 'mail_call', return_value={'connection_status': 'linked'}) as call:
            self.hub.main()
        call.assert_called_once_with('status', '--local')
        self.hub.Dialog.assert_not_called()

    def test_setup_reopens_after_a_previous_skip(self):
        onboarding.observe('skipped')
        self.confirm.return_value = 'skip'
        with patch.object(sys, 'argv', ['hubservices.py', '--setup']), \
                patch.object(self.hub.os, 'geteuid', return_value=0), \
                patch.object(sys.stdin, 'isatty', return_value=True), \
                patch.object(sys.stdout, 'isatty', return_value=True), \
                patch.object(self.hub, 'mail_call', side_effect=[{}, {
                    'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'}]) as call:
            self.hub.main()
        self.assertEqual(call.call_args_list[-1].args, ('connect', '--start'))
        self.hub.Dialog.assert_called_once()

    def test_existing_link_uses_only_local_status(self):
        with patch.object(self.hub.sys, 'stdin', io.StringIO('')), \
                patch.object(self.hub, 'mail_call', return_value={'connection_status': 'linked'}) as call:
            self.assertTrue(self.hub.early())
        call.assert_called_once_with('status', '--local')
        self.assertEqual(onboarding.decision(), 'linked')

    def test_early_progress_appears_before_local_connection_check(self):
        progress = Mock()
        def check(*args):
            progress.assert_called_once_with()
            return {"connection_status": "linked"}
        with patch.object(self.hub.sys, 'stdin', io.StringIO('')), \
                patch.object(self.hub, 'mail_call', side_effect=check):
            self.assertTrue(self.hub.early(progress=progress))

    def test_ctrl_c_during_connection_can_retry_or_quit(self):
        d = Mock()
        d.yesno.return_value = False
        calls = iter([True, False])
        def request(*args):
            if next(calls):
                os.kill(os.getpid(), signal.SIGINT)
                return {'status': 'error'}
            return {'status': 'pending'}
        with patch.object(self.hub, 'mail_call', side_effect=request):
            self.assertEqual(self.hub.interactive_mail_call(d, 'connect', '--start'), {'status': 'pending'})
        d.yesno.assert_called_once_with('Quit setup', 'Do you really want to quit?')
        d.infobox.assert_called_once_with('Connecting to TurnKey Hub...')

        d.yesno.return_value = True
        def interrupted(*args):
            os.kill(os.getpid(), signal.SIGINT)
            return {'status': 'error'}
        with patch.object(self.hub, 'mail_call', side_effect=interrupted), self.assertRaises(SystemExit) as caught:
            self.hub.interactive_mail_call(d, 'connect', '--start')
        self.assertEqual(caught.exception.code, 130)

    def test_progress_appears_before_request(self):
        dialog = Mock()
        def start(*args):
            dialog.infobox.assert_called_once_with("Connecting to TurnKey Hub...")
            return {'status': 'error'}
        with patch.object(self.hub, 'mail_call', side_effect=start):
            self.hub.connect_mail(dialog)
        self.assertEqual(onboarding.decision(), 'skipped')

    def test_explicit_skip_is_recorded_and_does_not_poll(self):
        d = Mock()
        d.yesno.return_value = False
        self.confirm.return_value = 'skip'
        with patch.object(self.hub, 'mail_call', return_value={
                'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'}) as call:
            self.hub.connect_mail(d)
        call.assert_called_once_with('connect', '--start')
        self.assertEqual(onboarding.decision(), 'skipped')
        self.assertIn('D34C-F5A9', self.confirm.call_args.args[1])

    def test_failed_link_check_skips_and_continues_setup(self):
        d = Mock()
        with patch.object(self.hub, 'mail_call', side_effect=[
                {'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'},
                {'status': 'error', 'message': 'Hub unavailable'}]):
            self.hub.connect_mail(d)
        self.assertEqual(onboarding.decision(), 'skipped')
        self.assertIn('sudo turnkey connect', d.msgbox.call_args.args[1])
        self.assertEqual(self.names()[-3:], ['approval_checked', 'error', 'skipped'])

    def test_lost_completion_can_retry_manually_with_the_same_code(self):
        dialog = Mock()
        self.confirm.side_effect = ['approved', 'check']
        with patch.object(self.hub, 'mail_call', side_effect=[
                {'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'},
                {'status': 'error', 'message': 'Hub unavailable', 'retryable': True},
                {'connection_status': 'linked', 'setup_pending': True}]) as call:
            self.hub.connect_mail(dialog)
        self.assertEqual([item.args[1] for item in call.call_args_list], ['--start', '--poll', '--poll'])
        self.assertEqual(onboarding.decision(), 'linked')
        self.assertIn('D34C-F5A9', self.confirm.call_args.args[1])
        self.assertIn('Try again with this code', self.confirm.call_args.args[1])

    def test_link_continues_without_confirmation_and_reports_dns_errors(self):
        for flow, error in ((flow, error) for flow in ('firstboot', 'confconsole')
                            for error in (None, 'DNS setup failed. Try again.')):
            with self.subTest(flow=flow, error=error):
                dialog = Mock()
                self.confirm.return_value = 'approved'
                result = {'connection_status': 'linked', 'setup_pending': True}
                if error:
                    result['dns_setup_error'] = error
                with patch.object(self.hub, 'mail_call', side_effect=[
                        {'verification_uri': 'https://hub.turnkeylinux.org/connect',
                         'user_code': 'D34C-F5A9'}, result]):
                    self.hub.connect_mail(dialog, flow_kind=flow)
                if error:
                    dialog.msgbox.assert_called_once_with('DNS setup', error)
                else:
                    dialog.msgbox.assert_not_called()

    def test_start_failure_skips_and_continues_setup(self):
        d = Mock()
        with patch.object(self.hub, 'mail_call', return_value={'status': 'error', 'message': 'Unavailable'}):
            self.hub.connect_mail(d)
        self.assertEqual(onboarding.decision(), 'skipped')
        self.assertIn('sudo turnkey connect', d.msgbox.call_args.args[1])

    def test_expiry_displays_new_code(self):
        with patch.object(self.hub.hub_push, 'confirm', side_effect=[('poll_result', {'status': 'expired'}), 'skip']) as confirm, \
                patch.object(self.hub, 'mail_call', side_effect=[
                    {'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'},
                    {'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'AAAA-BBBB'}]):
            self.hub.connect_mail(Mock())
        self.assertIn('AAAA-BBBB', confirm.call_args.args[1])
        self.assertIn('previous code expired', confirm.call_args.args[1])

    def test_helper_error_json_survives_nonzero_exit(self):
        result = subprocess.CompletedProcess([], 1, stdout='{"status":"error","message":"Hub unavailable","retryable":true}')
        with patch.object(self.hub.subprocess, 'run', return_value=result):
            self.assertEqual(self.hub.mail_call('connect', '--poll')['message'], 'Hub unavailable')

    def test_push_reconciles_then_approves_without_manual_check(self):
        watcher = Mock()
        with patch.object(self.hub.hub_push.Notifications, 'open', return_value=watcher), \
                patch.object(self.hub.hub_push, 'confirm', side_effect=['subscribed', 'code_submitted', 'approved']) as confirm, \
                patch.object(self.hub, 'mail_call', side_effect=[
                    {'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'},
                    {'connection_status': 'linked'}]) as call:
            self.hub.connect_mail(Mock())
        self.assertEqual([c.args for c in call.call_args_list], [
            ('connect', '--start'), ('connect', '--poll', '--notified', '--background')])
        self.assertEqual([c.kwargs['pending'] for c in confirm.call_args_list], [False, False, True])
        self.assertNotIn('approval_checked', self.names())
        self.assertEqual(self.names()[-1], 'link_completed')
        watcher.close.assert_called_once_with()

    def test_poll_that_completes_linking_replaces_browser_approval_prompt(self):
        dialog = Mock()
        with patch.object(self.hub.hub_push, 'confirm', side_effect=['code_submitted', ('poll_result', {'connection_status': 'linked'})]), \
                patch.object(self.hub, 'mail_call', side_effect=[
                    {'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'}]):
            self.hub.connect_mail(dialog)
        dialog.infobox.assert_called_once_with("Connecting to TurnKey Hub...")
        self.assertEqual(self.names()[-1], 'link_completed')

    def test_push_skip_and_escape_close_watcher_without_exchange(self):
        for action in ('skip', 'escape'):
            with self.subTest(action=action), \
                    patch.object(self.hub.hub_push.Notifications, 'open') as open_watcher, \
                    patch.object(self.hub.hub_push, 'confirm', return_value=action), \
                    patch.object(self.hub, 'mail_call', return_value={
                        'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'D34C-F5A9'}) as call:
                if action == 'escape':
                    with self.assertRaises(SystemExit) as result:
                        self.hub.connect_mail(Mock())
                    self.assertEqual(result.exception.code, 130)
                    self.assertNotEqual(self.names()[-1], 'skipped')
                else:
                    self.hub.connect_mail(Mock())
                    self.assertEqual(self.names()[-1], 'skipped')
                call.assert_called_once_with('connect', '--start')
                open_watcher.return_value.close.assert_called_once_with()

    def test_reconfigure_prompts_with_current_recipient_and_saves_change(self):
        onboarding.notification_choice('old@example.net')
        dialog = self.alerts.Dialog.return_value
        dialog.inputbox.return_value = ('ok', 'new@example.net')
        dialog.yesno.return_value = True
        with patch.dict(os.environ, {'_TURNKEY_RECONFIGURE': '1'}), \
                patch.object(sys, 'argv', ['secalerts.py']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.assertEqual(dialog.inputbox.call_args.args[2], 'old@example.net')
        self.assertEqual(run.call_args.args[0][-1], 'new@example.net')
        # The shell helper records the choice after updating the alias.
        self.assertEqual(onboarding.load()['notification_choice'], 'old@example.net')

    def test_reconfigure_cancel_preserves_recipient(self):
        onboarding.notification_choice('old@example.net')
        self.alerts.Dialog.return_value.inputbox.return_value = ('cancel', '')
        with patch.dict(os.environ, {'_TURNKEY_RECONFIGURE': '1'}), \
                patch.object(sys, 'argv', ['secalerts.py']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        run.assert_not_called()
        self.assertEqual(onboarding.load()['notification_choice'], 'old@example.net')

    def test_linked_user_gets_local_notifications_without_newsletter_prompt(self):
        onboarding.record('link_completed')
        with patch.object(sys, 'argv', ['secalerts.py', '--email=owner@example.net']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.alerts.Dialog.assert_not_called()
        self.assertEqual(run.call_args.args[0][-1], 'owner@example.net')
        self.assertNotIn('newsletter_choice', self.names())
        self.assertNotIn('email_provided', self.names())

    def test_interactive_notification_skip_is_durable_without_newsletter_for_linked_user(self):
        onboarding.record('link_completed')
        self.alerts.Dialog.return_value.inputbox.return_value = ('cancel', '')
        with patch.object(sys, 'argv', ['secalerts.py']), patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.assertEqual(onboarding.load()['notification_choice'], 'SKIP')
        run.assert_not_called()

    def test_skipped_user_can_decline_newsletter_without_affecting_local_notifications(self):
        onboarding.record('skipped')
        self.alerts.Dialog.return_value.yesno.return_value = True
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
        self.alerts.Dialog.return_value.yesno.return_value = False
        with patch.object(sys, 'argv', ['secalerts.py', '--email=chosen@example.net']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][-2:], ['chosen@example.net', '--newsletter-only'])

    def test_skipped_hub_asks_once_for_email_and_separate_consent(self):
        onboarding.record('skipped')
        dialog = self.alerts.Dialog.return_value
        dialog.inputbox.return_value = ('ok', 'chosen@example.net')
        dialog.yesno.return_value = False  # Subscribe is the second button.
        with patch.object(sys, 'argv', ['secalerts.py']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        dialog.inputbox.assert_called_once()
        self.assertEqual(dialog.yesno.call_count, 1)
        self.assertIn('chosen@example.net', dialog.yesno.call_args.args[1])
        self.assertEqual(dialog.yesno.call_args.args[2:], ('No thanks', 'Subscribe'))
        self.assertEqual(run.call_args_list[0].args[0][-1], 'chosen@example.net')
        self.assertEqual(run.call_args_list[1].args[0][-2:], ['chosen@example.net', '--newsletter-only'])
        self.assertTrue(onboarding.load()['pending'][-1]['newsletter_consent'])

    def test_skip_email_does_not_offer_or_subscribe_to_newsletter(self):
        onboarding.record('skipped')
        dialog = self.alerts.Dialog.return_value
        dialog.inputbox.return_value = ('cancel', '')
        with patch.object(sys, 'argv', ['secalerts.py']), \
                patch.object(self.alerts.subprocess, 'run') as run:
            self.alerts.main()
        dialog.inputbox.assert_called_once()
        dialog.yesno.assert_not_called()
        run.assert_not_called()

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
