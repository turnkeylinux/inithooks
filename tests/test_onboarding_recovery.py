"""Existing native retries and observation maintenance converge local setup."""
import json
import time
import unittest
from unittest.mock import Mock, patch

from libinithooks import onboarding, hub_runtime
import test_turnkey_hub as fixture

mail = fixture.mail


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        fixture.MailTest.setUp(self)
        for name, value in (('STATE_DIR', self.root / 'onboarding'),
                            ('HUB_STATE', mail.STATE_DIR / 'state.json'),
                            ('CACHE_DIR', mail.HUB_EMAIL_CACHE.parent),
                            ('ALIASES', self.root / 'aliases')):
            patch.object(onboarding, name, value).start()
        patch.object(hub_runtime, 'start_observations').start()
        patch.object(mail, 'ONBOARDING_STATE', onboarding.STATE_DIR / 'state.json').start()
        onboarding.ALIASES.write_text('postmaster: root\n')

    hub = fixture.MailTest.hub

    def recover_after(self, failed_route):
        first = onboarding.begin()
        onboarding.record('link_started')
        failed = False

        def response(route, payload):
            nonlocal failed
            if route == failed_route and not failed:
                failed = True
                raise mail.Error('Controlled temporary failure', code='unavailable', retryable=True)
            value = self.hub(route, payload)
            if route == 'configure':
                value['account_email'] = 'authenticated@example.net'
            return value

        delivered = []
        opener = Mock()
        def acknowledged(request, **kwargs):
            delivered.append(json.loads(request.data))
            reply = Mock()
            reply.__enter__ = Mock(return_value=Mock(status=200))
            reply.__exit__ = Mock(return_value=False)
            return reply
        opener.open.side_effect = acknowledged

        def configure(command, **kwargs):
            self.assertEqual(command, [onboarding.ALERTS, 'authenticated@example.net'])
            onboarding.ALIASES.write_text('root: authenticated@example.net\n')
            onboarding.notification_choice('authenticated@example.net')

        with patch.object(mail, 'request', side_effect=response):
            try:
                self.client.enroll(fixture.TOKEN)
            except mail.Error:
                pass
            onboarding.record('error', error_code='link_start_failed')
            onboarding.record('setup_completed')
            self.assertIsNone(onboarding.decision())
            with patch.object(onboarding.subprocess, 'run') as alerts:
                onboarding.reconcile()
            alerts.assert_not_called()
            self.assertIsNone(onboarding.decision())
            if failed_route == 'configure':
                self.assertIn('error', self.client.state)
                self.assertIn('link', self.client.state)
                self.assertNotIn('pending', self.client.state)
            self.client.retry()
        self.assertNotIn('error', self.client.state)
        with patch.object(onboarding.subprocess, 'run', side_effect=configure) as alerts, \
                patch('urllib.request.build_opener', return_value=opener):
            onboarding.flush()
            onboarding.flush()
        alerts.assert_called_once()
        state = onboarding.load()
        self.assertEqual(state['decision'], 'linked')
        self.assertEqual(state['installation_id'], first['installation_id'])
        self.assertEqual(state['flow_id'], first['flow_id'])
        self.assertEqual(onboarding.ALIASES.read_text(), 'root: authenticated@example.net\n')
        linked = [event for event in delivered if event['event_name'] == 'link_completed']
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]['installation_id'], first['installation_id'])
        self.assertEqual(linked[0]['flow_id'], first['flow_id'])
        self.assertEqual(state['pending'], [])

    def test_retry_after_network_failure_before_enrollment_converges_on_existing_maintenance(self):
        self.recover_after('enroll')

    def test_retry_after_grant_before_configuration_clears_stale_error_and_converges(self):
        self.recover_after('configure')

    def test_old_connection_and_old_pairing_cannot_approve_current_journey_but_new_approval_can(self):
        first = onboarding.begin()
        with patch.object(mail, 'request', side_effect=self.hub):
            self.client.enroll(fixture.TOKEN)
        onboarding.record('link_completed')
        onboarding.record('setup_completed')
        onboarding.notification_choice('SKIP')
        second = onboarding.start_flow('confconsole')
        pairing = {'verification_uri': mail.HUB + '/link', 'user_code': 'ABCD-EFGH',
                   'device_code': fixture.DEVICE_CODE, 'expires_in': 600, 'interval': 5}
        token = 'c'*32 + '.' + 'd'*64
        before_pairing_saved = []
        def response(route, payload):
            if route == 'device/start':
                self.assertNotIn('pairing', self.client.state)
                onboarding.reconcile()
                before_pairing_saved.append(onboarding.decision())
                return pairing.copy()
            if route == 'device/poll':
                return {'status': 'approved', 'enrollment_token': token}
            return self.hub(route, payload)
        with patch.object(mail, 'request', side_effect=response):
            self.client.start()
            self.assertEqual(before_pairing_saved, [None])
            self.assertEqual(self.client.state['pairing']['flow']['flow_id'], second['flow_id'])
            onboarding.reconcile()
            self.assertIsNone(onboarding.decision())
            third = onboarding.start_flow('confconsole')
            self.client.poll(notified=True)
            self.assertEqual(self.client.state['link']['flow']['flow_id'], second['flow_id'])
            onboarding.reconcile()
            self.assertIsNone(onboarding.decision())
            token = 'e'*32 + '.' + 'f'*64
            self.client.start()
            self.client.poll(notified=True)
        onboarding.reconcile()
        onboarding.reconcile()
        self.assertEqual(self.client.state['link']['flow']['flow_id'], third['flow_id'])
        self.assertEqual(onboarding.decision(), 'linked')
        links = [event for event in onboarding.load()['pending'] if event['event_name'] == 'link_completed']
        self.assertEqual([event['flow_id'] for event in links], [first['flow_id'], third['flow_id']])
        self.assertNotEqual(second['flow_id'], third['flow_id'])

    def test_old_pending_retry_retains_origin_when_a_new_flow_starts_before_pairing(self):
        first = onboarding.begin()
        with patch.object(mail, 'request', side_effect=mail.Error('offline', retryable=True)):
            with self.assertRaises(mail.Error):
                self.client.enroll(fixture.TOKEN)
        self.assertEqual(self.client.state['pending']['flow']['flow_id'], first['flow_id'])
        later = onboarding.start_flow('confconsole')
        with patch.object(mail, 'request', side_effect=self.hub):
            self.client.retry()
        self.assertEqual(self.client.state['link']['flow']['flow_id'], first['flow_id'])
        onboarding.reconcile()
        self.assertIsNone(onboarding.decision())
        self.assertNotIn('link_completed', [event['event_name'] for event in onboarding.load()['pending']])
        self.assertNotEqual(first['flow_id'], later['flow_id'])

    def test_unmarked_old_pairing_approval_does_not_capture_the_current_global_flow(self):
        onboarding.begin()
        self.client.state['pairing'] = {'device_code': fixture.DEVICE_CODE,
            'expires': time.time()+300, 'interval': 5, 'next_poll': 0}
        self.client.save()
        def response(route, payload):
            if route == 'device/poll':
                return {'status': 'approved', 'enrollment_token': fixture.TOKEN}
            return self.hub(route, payload)
        with patch.object(mail, 'request', side_effect=response):
            self.client.poll(notified=True)
        self.assertEqual(self.client.state['link']['flow'], {})
        onboarding.reconcile()
        self.assertIsNone(onboarding.decision())

    def test_old_post_grant_retry_does_not_relabel_connection_with_new_global_flow(self):
        first = onboarding.begin()
        failed = False
        def response(route, payload):
            nonlocal failed
            if route == 'configure' and not failed:
                failed = True
                raise mail.Error('offline after enrollment', retryable=True)
            return self.hub(route, payload)
        with patch.object(mail, 'request', side_effect=response):
            self.client.enroll(fixture.TOKEN)
            self.assertIn('error', self.client.state)
            self.assertEqual(self.client.state['link']['flow']['flow_id'], first['flow_id'])
            onboarding.start_flow('confconsole')
            self.client.retry()
        self.assertNotIn('error', self.client.state)
        self.assertEqual(self.client.state['link']['flow']['flow_id'], first['flow_id'])
        onboarding.reconcile()
        self.assertIsNone(onboarding.decision())

    def test_unmarked_old_pending_retry_does_not_borrow_new_onboarding_state(self):
        with patch.object(mail, 'request', side_effect=mail.Error('offline', retryable=True)):
            with self.assertRaises(mail.Error):
                self.client.enroll(fixture.TOKEN)
        self.assertEqual(self.client.state['pending']['flow'], {})
        onboarding.begin()
        with patch.object(mail, 'request', side_effect=self.hub):
            self.client.retry()
        self.assertEqual(self.client.state['link']['flow'], {})
        onboarding.reconcile()
        self.assertIsNone(onboarding.decision())
