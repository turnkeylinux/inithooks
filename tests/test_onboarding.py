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
