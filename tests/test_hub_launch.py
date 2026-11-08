"""Native registration retry identity, transport and secret boundaries."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from libinithooks import hub_launch as launch


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / 'private'
        self.values = {'serverid': 'server-one', 'unrelated': 'keep'}
        self.calls, self.writes = [], []
        self.request_error = None
        self.write_error = None
        owner = self

        class Config(dict):
            def __init__(self):
                super().__init__(owner.values)

            @property
            def serverid(self):
                return self['serverid']

            def validate_required(self, fields):
                for field in fields:
                    if not self.get(field):
                        raise ValueError(field)

            def write(self):
                if owner.write_error:
                    raise owner.write_error
                owner.values.update(self)
                owner.writes.append(dict(self))

        class API:
            def request(self, method, url, attrs):
                receipt = json.loads((owner.state / 'registration.json').read_text())
                owner.assertEqual(attrs['registration_nonce'], receipt['nonce'])
                owner.assertEqual(attrs['serverid'], receipt['server_id'])
                owner.assertEqual(0o700, stat.S_IMODE(owner.state.stat().st_mode))
                for name in ('registration.json', 'registration.lock'):
                    owner.assertEqual(0o600, stat.S_IMODE((owner.state / name).stat().st_mode))
                owner.calls.append((method, url, dict(attrs)))
                if owner.request_error:
                    raise owner.request_error
                return {'subkey': 'native-subkey-private', 'secret': 'native-secret-private',
                        'apikey': 'unused-legacy-response-field'}

        class Server:
            API_URL = 'https://hub.turnkeylinux.org/api/server/'

            def __init__(self):
                self.api = API()

        self.API = API
        modules = {'hubclient_lib': types.ModuleType('hubclient_lib'),
                   'hubclient_lib.conf': types.SimpleNamespace(HubServerConf=Config),
                   'hubclient_lib.api': types.SimpleNamespace(Server=Server)}
        self.patches = [patch.object(launch, 'STATE_DIR', self.state),
                        patch.object(launch.os, 'geteuid', return_value=0),
                        patch.dict('sys.modules', modules)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_native_request_preserves_config_and_is_quiet(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(0, launch.main([]))
        self.assertEqual('', out.getvalue() + err.getvalue())
        method, url, attrs = self.calls[0]
        self.assertEqual(('POST', 'https://hub.turnkeylinux.org/api/server/register/finalize/'), (method, url))
        self.assertEqual({'serverid', 'capability', 'registration_nonce'}, set(attrs))
        self.assertEqual('hub-services-v1', attrs['capability'])
        self.assertRegex(attrs['registration_nonce'], r'^[0-9a-f]{64}$')
        self.assertEqual('keep', self.values['unrelated'])
        self.assertEqual('native-subkey-private', self.values['subkey'])
        self.assertNotIn('apikey', self.values)

    def test_http_response_loss_reuses_nonce_before_retry(self):
        self.request_error = OSError('private HTTP body native-secret-private')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(1, launch.main([]))
        self.assertEqual('', out.getvalue())
        self.assertEqual(launch.FAILURE + '\n', err.getvalue())
        self.assertEqual([], self.writes)
        first = (self.state / 'registration.json').read_bytes()
        self.request_error = None
        launch.register()
        self.assertEqual(first, (self.state / 'registration.json').read_bytes())
        self.assertEqual(self.calls[0][2], self.calls[1][2])

    def test_native_write_failure_keeps_the_same_recovery_identity(self):
        self.write_error = OSError('disk full')
        with self.assertRaises(OSError):
            launch.register()
        self.assertNotIn('subkey', self.values)
        self.write_error = None
        launch.register()
        self.assertEqual(self.calls[0][2], self.calls[1][2])

    def test_interrupted_request_keeps_receipt_and_has_no_traceback(self):
        self.request_error = KeyboardInterrupt()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(130, launch.main([]))
        self.assertEqual('', out.getvalue())
        self.assertNotIn('Traceback', err.getvalue())
        self.assertTrue((self.state / 'registration.json').is_file())

    def test_snapshot_server_id_gets_new_nonce(self):
        launch.register()
        old = json.loads((self.state / 'registration.json').read_text())
        self.values['serverid'] = 'snapshot-destination'
        launch.register()
        new = json.loads((self.state / 'registration.json').read_text())
        self.assertNotEqual(old['nonce'], new['nonce'])
        self.assertEqual('snapshot-destination', new['server_id'])

    def test_corrupt_receipt_never_rotates_a_pinned_nonce(self):
        self.state.mkdir()
        receipt = self.state / 'registration.json'
        for value in ('{', 'null', '{"server_id":"server-one","nonce":"short"}'):
            receipt.write_text(value)
            with self.assertRaises(ValueError):
                launch.register()
            self.assertEqual(value, receipt.read_text())
        self.assertEqual([], self.calls)

    def test_failed_atomic_replace_retains_old_snapshot_receipt(self):
        launch.register()
        old = (self.state / 'registration.json').read_bytes()
        self.values['serverid'] = 'new-snapshot'
        with patch.object(launch.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                launch.register()
        self.assertEqual(old, (self.state / 'registration.json').read_bytes())
        self.assertEqual(1, len(self.calls))
        self.assertEqual([], list(self.state.glob('.registration-*')))

    def test_existing_receipt_permissions_are_private_before_http(self):
        launch.register()
        self.state.chmod(0o755)
        (self.state / 'registration.json').chmod(0o644)
        launch.register()  # The transport verifies modes before accepting it.

    def test_nonroot_or_missing_server_id_never_calls_http(self):
        with patch.object(launch.os, 'geteuid', return_value=1000):
            with self.assertRaises(PermissionError):
                launch.register()
        self.assertFalse(self.state.exists())
        self.values.pop('serverid')
        with self.assertRaises(ValueError):
            launch.register()
        self.assertEqual([], self.calls)
        self.assertFalse((self.state / 'registration.json').exists())

    def test_invalid_response_never_changes_native_config(self):
        for response in (None, {}, {'subkey': 'key', 'secret': 3}):
            with patch.object(self.API, 'request', return_value=response):
                with self.assertRaises(ValueError):
                    launch.register()
        self.assertEqual([], self.writes)

    def test_concurrent_native_requests_share_one_private_nonce(self):
        started, release = threading.Event(), threading.Event()
        request = self.API.request

        def blocked(api, *args):
            started.set()
            self.assertTrue(release.wait(3))
            return request(api, *args)

        with patch.object(self.API, 'request', blocked), ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(launch.register)
            self.assertTrue(started.wait(3))
            second = workers.submit(launch.register)
            release.set()
            first.result(3)
            second.result(3)
        self.assertEqual(2, len(self.calls))
        self.assertEqual(self.calls[0][2]['registration_nonce'], self.calls[1][2]['registration_nonce'])
