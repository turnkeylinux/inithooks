"""Firstboot observation durability, privacy and generic email boundaries."""
import io
import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
import urllib.request

from libinithooks import onboarding as flow, hub_runtime

ROOT = Path(__file__).resolve().parents[1]


class OnboardingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        launcher = patch.object(hub_runtime, 'start_observations', create=True)
        self.launcher = launcher.start()
        self.addCleanup(launcher.stop)
        for name, value in (("STATE_DIR", self.root / "state"), ("CACHE_DIR", self.root / "cache"), ("HUB_STATE", self.root / "hub.json")):
            patcher = patch.object(flow, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_installation_and_firstboot_flow_survive_reboots_and_duplicate_begin(self):
        first = flow.begin()
        self.assertEqual(first, flow.begin())
        self.assertEqual([e['event_name'] for e in first['pending']], ['setup_started'])
        for key in ('installation_id', 'flow_id'):
            self.assertRegex(first[key], r'^[a-f0-9]{32}$')
        self.assertEqual((flow.STATE_DIR / 'state.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(flow.STATE_DIR.stat().st_mode & 0o777, 0o700)

    def test_fresh_start_launches_after_durable_save_and_unlock_before_return(self):
        def launched():
            self.assertEqual(flow.load()['pending'][0]['event_name'], 'setup_started')
            with (flow.STATE_DIR / 'lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.launcher.side_effect = launched
        first = flow.begin()
        self.launcher.assert_called_once_with()
        flow.record('link_completed')
        for unused in range(5):
            self.assertEqual(flow.begin()['installation_id'], first['installation_id'])
        self.launcher.assert_called_once_with()

    def linked_native(self):
        smtp = {'host': 'smtp.example.net', 'port': 587, 'username': 'test'}
        device = 'a' * 32
        return {'link': {'mode': 'hub', 'device_id': device, 'control_secret': 'private-test',
                         'flow': {key: flow.load()[key] for key in ('installation_id', 'flow_id', 'flow_kind')}},
                'active': {'mode': 'hub', 'device_id': device, 'smtp': smtp,
                           'sender': 'sender@example.net', 'domain': 'example.net'},
                'services': {'device_id': device, 'connection_status': 'linked',
                    'account_email': 'owner@example.net',
                    'mail': {'enabled': True, 'status': 'active', 'smtp': smtp,
                             'sender': 'sender@example.net', 'domain': 'example.net'},
                    'backup': {'status': 'disabled'}}}

    def recovered_setup(self):
        first = flow.begin()
        flow.record('setup_completed')
        self.native = self.linked_native()
        flow.HUB_STATE.write_text(json.dumps(self.native))
        flow.remember_email('owner@example.net')
        self.aliases = self.root / 'aliases'
        self.aliases.write_text('postmaster: root\nroot:  \n')
        patcher = patch.object(flow, 'ALIASES', self.aliases)
        patcher.start()
        self.addCleanup(patcher.stop)
        return first

    def test_recovered_link_reconciles_once_after_setup_with_original_ids_and_default(self):
        first = self.recovered_setup()
        def configure(command, **kwargs):
            self.assertEqual(command, [flow.ALERTS, 'owner@example.net'])
            self.aliases.write_text('root: owner@example.net\n')
            flow.notification_choice('owner@example.net')
        with patch.object(flow.subprocess, 'run', side_effect=configure) as run:
            flow.reconcile()
            flow.reconcile()
        run.assert_called_once()
        state = flow.load()
        self.assertEqual(state['decision'], 'linked')
        linked = [event for event in state['pending'] if event['event_name'] == 'link_completed']
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]['flow_id'], first['flow_id'])
        self.assertEqual(linked[0]['installation_id'], first['installation_id'])
        self.assertEqual(state['notification_choice'], 'owner@example.net')

    def test_incomplete_native_configuration_cannot_complete_observations_or_notifications(self):
        self.recovered_setup()
        for marker in ('pending', 'applying', 'guard', 'error', 'mail_error', 'active_missing', 'wrong_smtp'):
            native = self.linked_native()
            if marker == 'mail_error':
                native['services']['mail']['error'] = 'Apply failed'
            elif marker == 'active_missing':
                del native['active']
            elif marker == 'wrong_smtp':
                native['active']['smtp'] = {'host': 'old.example.net'}
            else:
                native[marker] = {'code': 'revoked'}
            flow.HUB_STATE.write_text(json.dumps(native))
            with self.subTest(marker=marker), patch.object(flow.subprocess, 'run') as run:
                flow.reconcile()
                run.assert_not_called()
                self.assertIsNone(flow.decision())
                self.assertNotIn('link_completed', [event['event_name'] for event in flow.load()['pending']])

    def test_backup_setup_required_and_manual_or_disabled_mail_do_not_block_account_link(self):
        self.recovered_setup()
        for manual in (True, False):
            native = self.linked_native()
            native['services']['backup'] = {'status': 'setup_required', 'error': 'Retry backup setup'}
            native.pop('active')
            if manual:
                native['mail_preference'] = 'manual'
            else:
                native['services']['mail'] = {'enabled': False, 'status': 'disabled'}
            flow.HUB_STATE.write_text(json.dumps(native))
            with patch.object(flow.subprocess, 'run'):
                flow.reconcile()
            self.assertEqual(flow.decision(), 'linked')

    def test_explicit_notification_skip_preseed_and_existing_alias_are_preserved(self):
        self.recovered_setup()
        for choice in ('SKIP', 'chosen@example.net'):
            flow.notification_choice(choice)
            with patch.object(flow.subprocess, 'run') as run:
                flow.reconcile()
            run.assert_not_called()
            self.assertEqual(flow.load()['notification_choice'], choice)
        state = flow.load()
        state.pop('notification_choice')
        flow.save(state)
        self.aliases.write_text('root: custom@example.net\n')
        with patch.object(flow.subprocess, 'run') as run:
            flow.reconcile()
        run.assert_not_called()
        self.assertEqual(self.aliases.read_text(), 'root: custom@example.net\n')

    def test_explicit_link_skip_is_not_reclassified_from_an_existing_native_connection(self):
        self.recovered_setup()
        flow.record('skipped')
        with patch.object(flow.subprocess, 'run') as run:
            flow.reconcile()
        run.assert_not_called()
        self.assertEqual(flow.decision(), 'skipped')
        self.assertNotIn('link_completed', [event['event_name'] for event in flow.load()['pending']])

    def test_skipped_firstboot_then_later_link_keeps_confconsole_flow_and_firstboot_skip(self):
        first = self.recovered_setup()
        flow.record('skipped')
        later = flow.start_flow('confconsole')
        with patch.object(flow.subprocess, 'run') as run:
            flow.reconcile()
        run.assert_not_called()
        self.assertIsNone(flow.decision())
        # A new approved native enrollment belongs to this later journey.
        self.native['link']['flow'] = {key: flow.load()[key]
                                      for key in ('installation_id', 'flow_id', 'flow_kind')}
        flow.HUB_STATE.write_text(json.dumps(self.native))
        with patch.object(flow.subprocess, 'run'):
            flow.reconcile()
            flow.reconcile()
        state = flow.load()
        linked = [event for event in state['pending'] if event['event_name'] == 'link_completed']
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]['flow_id'], later['flow_id'])
        self.assertNotEqual(linked[0]['flow_id'], first['flow_id'])
        self.assertEqual(linked[0]['flow_kind'], 'confconsole')
        self.assertEqual(state['firstboot_decision'], 'skipped')
        self.assertEqual(sum(event['event_name'] == 'setup_started' for event in state['pending']), 1)

    def test_old_or_missing_native_journey_cannot_complete_new_flow_before_or_during_pairing(self):
        self.recovered_setup()
        later = flow.start_flow('confconsole')
        for pairing in (None, {'device_code': 'pending'}):
            for proof in ('old', 'missing'):
                native = json.loads(json.dumps(self.native))
                if proof == 'missing':
                    native['link'].pop('flow')
                if pairing:
                    native['pairing'] = pairing
                flow.HUB_STATE.write_text(json.dumps(native))
                with patch.object(flow.subprocess, 'run') as run:
                    flow.reconcile()
                run.assert_not_called()
                self.assertIsNone(flow.decision())
                self.assertFalse(any(event['event_name'] == 'link_completed' and
                                     event['flow_id'] == later['flow_id'] for event in flow.load()['pending']))

    def test_flow_change_between_validation_and_record_cannot_complete_new_journey(self):
        self.recovered_setup()
        record = flow.record
        def changed(*args, **kwargs):
            flow.start_flow('confconsole')
            return record(*args, **kwargs)
        with patch.object(flow, 'record', side_effect=changed), patch.object(flow.subprocess, 'run') as run:
            flow.reconcile()
        run.assert_not_called()
        self.assertIsNone(flow.decision())
        self.assertNotIn('link_completed', [event['event_name'] for event in flow.load()['pending']])

    def skip_wins_at_commit(self, kind):
        self.recovered_setup()
        if kind == 'confconsole':
            flow.record('skipped')
            flow.start_flow(kind)
            self.native['link']['flow'] = {key: flow.load()[key]
                for key in ('installation_id', 'flow_id', 'flow_kind')}
            flow.HUB_STATE.write_text(json.dumps(self.native))
        record = flow.record
        def skipped(*args, **kwargs):
            record('skipped')
            result = record(*args, **kwargs)
            self.assertIsNone(result)
            return result
        original_aliases = self.aliases.read_text()
        with patch.object(flow, 'record', side_effect=skipped) as commit, \
                patch.object(flow.subprocess, 'run') as run:
            flow.reconcile()
        commit.assert_called_once_with('link_completed', expected_flow=self.native['link']['flow'])
        run.assert_not_called()
        state = flow.load()
        self.assertEqual(state['decision'], 'skipped')
        self.assertEqual(state['firstboot_decision'], 'skipped')
        self.assertEqual(self.aliases.read_text(), original_aliases)
        self.assertNotIn('link_completed', [event['event_name'] for event in state['pending']])

    def test_firstboot_skip_committed_after_validation_wins_over_link_completion(self):
        self.skip_wins_at_commit('firstboot')

    def test_confconsole_skip_committed_after_validation_wins_over_link_completion(self):
        self.skip_wins_at_commit('confconsole')

    def test_link_completion_committed_after_validation_remains_idempotent(self):
        self.recovered_setup()
        record = flow.record
        def linked(*args, **kwargs):
            record('link_completed')
            return record(*args, **kwargs)
        with patch.object(flow, 'record', side_effect=linked), patch.object(flow.subprocess, 'run'):
            flow.reconcile()
        state = flow.load()
        self.assertEqual(state['decision'], 'linked')
        self.assertEqual(sum(event['event_name'] == 'link_completed' for event in state['pending']), 1)

    def test_notification_skip_committed_after_validation_prevents_default(self):
        self.recovered_setup()
        record = flow.record
        def opted_out(*args, **kwargs):
            flow.notification_choice('SKIP')
            return record(*args, **kwargs)
        with patch.object(flow, 'record', side_effect=opted_out), patch.object(flow.subprocess, 'run') as run:
            flow.reconcile()
        run.assert_not_called()
        self.assertEqual(flow.decision(), 'linked')
        self.assertEqual(flow.load()['notification_choice'], 'SKIP')

    def test_relink_changes_flow_without_counting_another_installation(self):
        first = flow.begin()
        relink = flow.start_flow('confconsole')
        self.assertEqual(first['installation_id'], relink['installation_id'])
        self.assertNotEqual(first['flow_id'], relink['flow_id'])
        flow.record('link_completed')
        flow.record('link_completed')
        flow.record('setup_completed')
        flow.record('setup_completed')
        events = flow.load()['pending']
        self.assertEqual([e['event_name'] for e in events], ['setup_started', 'link_completed', 'setup_completed'])
        self.assertEqual(events[-1]['flow_id'], first['flow_id'])
        self.assertEqual(events[-1]['flow_kind'], 'firstboot')

    def test_network_error_does_not_mean_skip_or_enable_email_reporting(self):
        flow.record('error', error_code='link_start_failed')
        flow.record('email_provided', email='private@example.net')
        flow.record('newsletter_choice', newsletter_consent=True)
        self.assertIsNone(flow.decision())
        self.assertNotIn('private@example.net', json.dumps(flow.load()))
        self.assertEqual(len(flow.load()['pending']), 2)

    def test_email_and_consent_only_after_explicit_skip(self):
        flow.record('skipped')
        flow.record('email_provided', email='chosen@example.net')
        flow.record('newsletter_choice', newsletter_consent=False)
        events = flow.load()['pending']
        self.assertEqual(events[-2]['email'], 'chosen@example.net')
        self.assertEqual(events[-2]['link_decision'], 'skipped')
        self.assertFalse(events[-1]['newsletter_consent'])
        self.assertTrue(all('email' not in e for e in events if e['event_name'] != 'email_provided'))
        with self.assertRaises(ValueError):
            flow.record('error', error_code='network', email='private@example.net')
        with self.assertRaises(ValueError):
            flow.record('approved')

    def test_linked_account_cannot_report_email_or_newsletter_consent(self):
        flow.record('link_completed')
        flow.record('email_provided', email='private@example.net')
        flow.record('newsletter_choice', newsletter_consent=True)
        self.assertEqual([e['event_name'] for e in flow.load()['pending']], ['setup_started', 'link_completed'])

    def test_retry_keeps_exact_event_identity_and_posts_email_only_in_json(self):
        flow.record('skipped')
        flow.record('email_provided', email='private@example.net')
        original = flow.load()['pending']
        opener = Mock()
        opener.open.side_effect = URLError('offline')
        with patch.object(urllib.request, 'build_opener', return_value=opener):
            flow.flush()
            flow.flush()
            self.assertEqual(flow.load()['pending'], original)
            opener.open.side_effect = None
            opener.open.return_value.__enter__ = Mock(return_value=Mock(status=200))
            opener.open.return_value.__exit__ = Mock(return_value=False)
            flow.flush()
        self.assertEqual(flow.load()['pending'], [])
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(requests[0].data, requests[1].data)
        for request in requests:
            self.assertEqual(request.full_url, flow.ENDPOINT)
            self.assertEqual(request.method, 'POST')
            self.assertEqual(request.get_header('User-agent'), 'TurnKey-Onboarding/1')
            self.assertLess(len(request.data), 16384)
        self.assertEqual(json.loads(requests[-1].data)['email'], 'private@example.net')

    def test_http_retry_and_invalid_event_discard_boundaries(self):
        for code, retained in ((429, True), (503, True), (400, False), (403, False)):
            with self.subTest(code=code):
                flow.begin()
                opener = Mock()
                opener.open.side_effect = HTTPError(flow.ENDPOINT, code, '', {}, io.BytesIO())
                with patch.object(urllib.request, 'build_opener', return_value=opener):
                    flow.flush()
                self.assertEqual(bool(flow.load()['pending']), retained)
                (flow.STATE_DIR / 'state.json').unlink()

    def test_email_default_is_editable_and_preserves_custom_values(self):
        flow.remember_email('owner@example.net')
        self.assertEqual(flow.email_default(''), 'owner@example.net')
        self.assertEqual(flow.email_default('admin@example.com'), 'owner@example.net')
        self.assertEqual(flow.email_default('app-admin@example.net'), 'app-admin@example.net')
        self.assertFalse((flow.CACHE_DIR / 'APP_EMAIL').exists())
        self.assertEqual((flow.CACHE_DIR / 'HUB_ACCOUNT_EMAIL').stat().st_mode & 0o777, 0o600)
        flow.remember_email('invalid\naddress')
        self.assertEqual(flow.email_default(), 'owner@example.net')

    def test_context_reports_cgroup_limits_and_unknown_metadata_honestly(self):
        files = {'/etc/turnkey_version': 'turnkey-wordpress-19.0-trixie-amd64',
                 '/proc/meminfo': 'MemTotal: 8388608 kB', '/sys/fs/cgroup/memory.max': '1073741824',
                 '/sys/fs/cgroup/cpu.max': '200000 100000'}
        with patch.object(flow, 'read_text', side_effect=lambda p: files.get(str(p), '')), \
                patch.object(flow.os, 'sched_getaffinity', return_value=set(range(8))):
            context = flow.context()
        self.assertEqual(context['app'], 'wordpress')
        self.assertEqual(context['release'], '19.0')
        self.assertEqual(context['ram_bytes'], 1073741824)
        self.assertEqual(context['cpu_count'], 2)
        self.assertIsNone(context['image_name'])
        self.assertIsNone(context['app_version'])

    def test_context_reports_build_target_and_platform_for_proxmox_and_untagged_builds(self):
        version = 'turnkey-wordpress-18.0-bookworm-amd64'
        tag = 'Acquire::http::User-Agent "TurnKey APT-HTTP/1.3 (%s)";'
        for files, target, platform in (
                ({'/etc/apt/apt.conf.d/01turnkey': tag % (version + ' proxmox'), '/run/systemd/container': 'lxc'}, 'proxmox', 'lxc'),
                ({'/etc/apt/apt.conf.d/01turnkey': tag % version, '/sys/class/dmi/id/product_name': 'KVM'}, 'iso', 'KVM'),
                ({'/sys/class/dmi/id/product_name': 'KVM'}, None, 'KVM')):
            files = dict(files, **{'/etc/turnkey_version': version})
            with self.subTest(target=target), \
                    patch.object(flow, 'read_text', side_effect=lambda p, f=files: f.get(str(p), '')), \
                    patch.object(flow.Path, 'exists', return_value=False):
                context = flow.context()
            self.assertEqual(context['build_target'], target)
            self.assertEqual(context['platform'], platform)

    def test_explicit_clone_reset_creates_new_installation_and_clears_email(self):
        first = flow.begin()
        flow.remember_email('previous@example.net')
        with patch('sys.argv', ['turnkey-onboarding', 'reset-installation']), patch.object(flow.os, 'geteuid', return_value=0):
            flow.main()
        self.assertFalse((flow.CACHE_DIR / 'HUB_ACCOUNT_EMAIL').exists())
        self.assertNotEqual(first['installation_id'], flow.begin()['installation_id'])

    def test_optional_observation_failure_cannot_block_setup_or_replace_identity(self):
        flow.begin()
        (flow.STATE_DIR / 'state.json').write_text('corrupted')
        with self.assertLogs(level='WARNING'):
            self.assertEqual(flow.start_flow(), {})
            with patch('sys.argv', ['turnkey-onboarding', 'begin']), patch.object(flow.os, 'geteuid', return_value=0):
                flow.main()
        self.assertIsNone(flow.decision())
        self.assertEqual((flow.STATE_DIR / 'state.json').read_text(), 'corrupted')

    def test_hook_order_and_fence_replacement(self):
        hooks = sorted(p.name for p in (ROOT / 'firstboot.d').iterdir())
        self.assertEqual(hooks[0], '00setup-observation')
        self.assertLess(hooks.index('30hub-link'), hooks.index('30rootpass'))
        self.assertLess('30hub-link', '40wordpress')
        fence = (ROOT / 'firstboot.d/29tagid').read_text()
        self.assertNotIn('cat>>', fence)
        self.assertIn('systemctl reload turnkey-init-fence', fence)
        self.assertIn('@APP_NAME@', fence)
        final = (ROOT / 'firstboot.d/98finalize').read_text()
        self.assertLess(final.index('setup_completed'), final.index('echo >'))


if __name__ == '__main__':
    unittest.main()
