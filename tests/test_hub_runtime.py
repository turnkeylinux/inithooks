"""The ordinary Docker scheduler owns real cron jobs; VMs retain systemd."""
from pathlib import Path
import tempfile
import subprocess
import sys
import os
import unittest
from unittest.mock import Mock, patch

from libinithooks import hub_restore, hub_runtime, onboarding
import test_turnkey_hub as fixture
mail = fixture.mail


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(hub_runtime, 'RUNTIME', self.root / 'runtime').start()
        patch.object(hub_runtime, 'CRON', self.root / 'cron').start()

    def test_only_explicit_docker_runtime_enables_adapter(self):
        self.assertFalse(hub_runtime.enabled())
        hub_runtime.RUNTIME.write_text('vm\n')
        self.assertFalse(hub_runtime.enabled())
        hub_runtime.RUNTIME.write_text('docker\n')
        self.assertTrue(hub_runtime.enabled())

    def test_fresh_vm_uses_existing_nonblocking_bounded_service(self):
        with patch.object(hub_runtime.subprocess, 'run') as run:
            hub_runtime.start_observations()
        self.assertEqual(run.call_args.args[0],
                         ['systemctl', 'start', '--no-block', 'turnkey-onboarding.service'])
        self.assertEqual(run.call_args.kwargs['timeout'], 1)
        service = Path(__file__).resolve().parents[1] / 'debian/inithooks.turnkey-onboarding.service'
        self.assertIn('TimeoutStartSec=30', service.read_text())

    def test_docker_detaches_bounded_existing_sender_without_matching_all_python_or_timeout(self):
        hub_runtime.RUNTIME.write_text('docker\n')
        with patch.object(hub_runtime.subprocess, 'run') as run:
            hub_runtime.start_observations()
        self.assertEqual(run.call_args.args[0], [
            '/usr/sbin/start-stop-daemon', '--start', '--background',
            '--exec', '/usr/lib/inithooks/bin/turnkey-onboarding',
            '--startas', '/usr/bin/timeout', '--', '--kill-after=5', '30',
            '/usr/lib/inithooks/bin/turnkey-onboarding', 'flush'])
        self.assertEqual(run.call_args.kwargs, dict(stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1))

    def test_launch_failure_is_nonfatal_and_retries_preserve_event_identity(self):
        with patch.object(onboarding, 'STATE_DIR', self.root / 'state'):
            for error in (OSError('missing launcher'), subprocess.TimeoutExpired('launcher', 1)):
                with self.subTest(error=type(error).__name__):
                    (onboarding.STATE_DIR / 'state.json').unlink(missing_ok=True)
                    with patch.object(hub_runtime.subprocess, 'run', side_effect=error) as launch:
                        state = onboarding.begin()
                        self.assertEqual(onboarding.begin(), state)
                        launch.assert_called_once()
                    opener = Mock()
                    opener.open.side_effect = OSError('offline')
                    with patch('urllib.request.build_opener', return_value=opener):
                        onboarding.flush()
                        onboarding.flush()
                    self.assertEqual(onboarding.load(), state)
                    self.assertEqual(opener.open.call_args_list[0].args[0].data,
                                     opener.open.call_args_list[1].args[0].data)

    def test_jobs_are_real_private_idempotent_and_removed_on_disable(self):
        for command, (name, schedule, executable) in hub_runtime.JOBS.items():
            hub_runtime.schedule(command)
            path = hub_runtime.CRON / name
            self.assertIn(schedule + ' root ' + executable, path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            modified = path.stat().st_mtime_ns
            hub_runtime.schedule(command)
            self.assertEqual(path.stat().st_mtime_ns, modified)
            hub_runtime.schedule(command, False)
            self.assertFalse(path.exists())
        with self.assertRaises(KeyError):
            hub_runtime.schedule('arbitrary-command')

    def test_build_metadata_cannot_override_runtime_resource_observations(self):
        original = onboarding.read_text
        metadata = '{"app":"core","image_name":"local/core:review","build_target":"docker","ram_bytes":1,"platform":"invented","release":"bad\\nrelease","app_version":42}'
        with patch.object(onboarding, 'read_text', side_effect=lambda p: metadata if str(p) == '/etc/turnkey-build.json' else original(p)):
            value = onboarding.context()
        self.assertEqual(value['image_name'], 'local/core:review')
        self.assertEqual(value['app'], 'core')
        self.assertEqual(value['build_target'], 'docker')
        self.assertNotEqual(value['ram_bytes'], 1)
        self.assertNotEqual(value['platform'], 'invented')
        self.assertIsNone(value['app_version'])
        self.assertNotEqual(value['release'], 'bad\nrelease')

    def test_podman_marker_reports_the_actual_container_runtime(self):
        with patch.object(Path, 'exists', lambda path: str(path) == '/run/.containerenv'):
            self.assertEqual(onboarding.context()['platform'], 'podman')


class DockerMailTest(unittest.TestCase):
    setUp = fixture.MailTest.setUp

    def test_restore_apply_repairs_owned_options_without_network_and_retains_user_settings(self):
        config = mail.checked_config(fixture.CONFIG, fixture.PASSWORD)
        self.client.apply(config)
        mail.smtp_check.reset_mock()
        self.main.write_text('myhostname = restored-app.example\nmessage_size_limit = 123456\n'
                             'relayhost = [source.example]:587\nsmtp_sasl_password_maps = hash:/etc/postfix/source.sasl\n')
        self.client.apply(config, verify=False)
        mail.smtp_check.assert_not_called()
        values = mail.cf_entries(self.main.read_text())
        self.assertEqual(values['myhostname'], 'restored-app.example')
        self.assertEqual(values['message_size_limit'], '123456')
        self.assertEqual(values['relayhost'], '[' + config['smtp']['host'] + ']:587')
        self.assertNotIn('source.sasl', self.main.read_text())
        self.assertEqual(self.client.state['active']['password'], fixture.PASSWORD)

    def test_docker_schedule_and_reset_avoid_systemd_and_preserve_observations(self):
        with patch.object(mail, 'RUNTIME', hub_runtime), patch.object(hub_runtime, 'CRON', self.root / 'cron'):
            hub_runtime.schedule("retry")
            hub_runtime.schedule("dns-update")
            hub_runtime.schedule('observe')
            self.assertEqual(self.calls, [])
            self.client.reset_mail()
            self.assertTrue((hub_runtime.CRON / 'turnkey-mail-retry').exists())
            self.client.reset()
            self.assertFalse((hub_runtime.CRON / 'turnkey-mail-retry').exists())
            self.assertFalse((hub_runtime.CRON / 'turnkey-mail-dns').exists())
            self.assertTrue((hub_runtime.CRON / 'turnkey-onboarding').exists())
            self.assertFalse(any(command[0] == 'systemctl' for command, _ in self.calls))

    def test_custom_restore_removes_only_generated_source_sender_policy_atomically(self):
        config = mail.checked_config(fixture.CONFIG, fixture.PASSWORD, custom=True)
        self.client.apply(config)
        source = mail.change_cf(self.main.read_text(), {
            'sender_canonical_classes': 'envelope_sender',
            'sender_canonical_maps': 'regexp:' + str(mail.POSTFIX_DIR / 'turnkey-mail-aaaaaaaaaaaaaaaa.sender'),
            'smtp_header_checks': 'regexp:' + str(mail.POSTFIX_DIR / 'turnkey-mail-aaaaaaaaaaaaaaaa.headers'),
            'message_size_limit': '123456'})
        self.main.write_text(source)
        policy = hub_restore.custom_sender_policy(mail, self.client.state)
        self.assertEqual(policy, {'sender_canonical_maps': None, 'sender_canonical_classes': None, 'smtp_header_checks': None})
        self.fail_command = ['postfix', 'check']
        with self.assertRaises(mail.Error):
            self.client.apply(config, verify=False, restore_sender_policy=policy)
        self.assertEqual(self.main.read_text(), source)
        self.fail_command = None
        mail.smtp_check.reset_mock()
        self.client.apply(config, verify=False, restore_sender_policy=policy)
        mail.smtp_check.assert_not_called()
        values = mail.cf_entries(self.main.read_text())
        self.assertFalse(set(policy) & set(values))
        self.assertEqual(values['message_size_limit'], '123456')

    def test_custom_restore_uses_preserved_policy_and_leaves_arbitrary_maps(self):
        generated = 'regexp:' + str(mail.POSTFIX_DIR / 'turnkey-mail-aaaaaaaaaaaaaaaa.sender')
        self.main.write_text(mail.change_cf(self.main.read_text(), {
            'sender_canonical_maps': generated, 'smtp_header_checks': 'regexp:/etc/postfix/custom.headers'}))
        state = {'baseline': {'sender_canonical_maps': 'hash:/etc/postfix/baseline'},
                 'owned_options': {'sender_canonical_maps': 'hash:/etc/postfix/destination',
                                   'sender_canonical_classes': 'envelope_sender'}}
        self.assertEqual(hub_restore.custom_sender_policy(mail, state), {
            'sender_canonical_maps': 'hash:/etc/postfix/destination',
            'sender_canonical_classes': 'envelope_sender'})
        for arbitrary in ('hash:/etc/postfix/custom', generated + '.backup', generated + ', hash:/etc/postfix/custom'):
            self.main.write_text(mail.change_cf(self.main.read_text(), {'sender_canonical_maps': arbitrary}))
            self.assertEqual(hub_restore.custom_sender_policy(mail, state), {})



class RestoreHookModeTest(unittest.TestCase):
    def test_only_explicit_real_file_restore_invokes_reconciliation(self):
        hook = Path(__file__).resolve().parents[1] / 'tklbam-hooks/turnkey-hub-restore'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'libinithooks'
            package.mkdir()
            (package / '__init__.py').touch()
            (package / 'hub_restore.py').write_text('def restore_mail():\n    print("RECONCILED")\n')
            for phase, marker, skipped, expected in ((['restore','post'], 'no', '', True),
                    (['restore','post'], 'yes', '', False), (['restore','post'], '', '', False),
                    (['restore','post'], 'no', 'yes', False), (['restore','pre'], 'no', '', False),
                    (['backup','post'], 'no', '', False)):
                result = subprocess.run([sys.executable, str(hook), *phase], capture_output=True, text=True,
                    env=dict(os.environ, PYTHONPATH=str(root), TKLBAM_RESTORE_SIMULATE=marker,
                             TKLBAM_RESTORE_SKIP_FILES=skipped))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual('RECONCILED' in result.stdout, expected)
