"""Readiness is an offline installed-component contract, not a release label."""
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from libinithooks import hub_capabilities as capabilities

ROOT = Path(__file__).resolve().parents[1]


class CapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.helper = self.root / 'turnkey-hub'
        self.helper.write_text('class Mail:\n' + ''.join(
            '    def %s(self): raise AssertionError("must not run")\n' % name
            for name in ('enroll', 'retry', 'reset', 'poll', 'dns_update')))
        self.helper.chmod(0o755)
        self.config = self.root / 'component'
        self.config.write_text('installed component')
        self.modules = {}
        for name, attributes in capabilities.MODULES.items():
            module = types.SimpleNamespace()
            for attr in attributes:
                value = module
                path = attr.split('.')
                for part in path[:-1]:
                    if not hasattr(value, part):
                        setattr(value, part, types.SimpleNamespace())
                    value = getattr(value, part)
                setattr(value, path[-1], lambda *a: self.fail('probe called runtime function'))
            self.modules[name] = module
        self.modules['libinithooks.hub_launch'].PROTOCOL = capabilities.PROTOCOL
        self.modules['hubclient_lib.api'].Server.API_URL = 'https://hub.turnkeylinux.org/api/server/'
        self.package_output = 'installed\tok\t1.5.3+6\n'
        self.query_status = self.validation_status = self.comparison_status = 0
        self.package_calls = []
        self.patches = [patch.object(capabilities, 'HELPER', self.helper),
                        patch.object(capabilities, 'EXECUTABLES', (str(self.helper),)),
                        patch.object(capabilities, 'FILES', (str(self.config),)),
                        patch.object(capabilities.importlib, 'import_module', side_effect=self.modules.__getitem__),
                        patch.object(capabilities.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name),
                        patch.object(capabilities.subprocess, 'run', side_effect=self.package_command),
                        patch.object(Path, 'glob', return_value=[self.root / 'libplain.so'])]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.stat().st_mode, p.read_bytes())
                for p in self.root.rglob('*') if p.is_file()}

    def package_command(self, command, **kwargs):
        self.package_calls.append(command)
        self.assertEqual(subprocess.DEVNULL, kwargs['stdin'])
        self.assertEqual(subprocess.DEVNULL, kwargs['stderr'])
        self.assertEqual(2, kwargs['timeout'])
        if command == ['dpkg-query', '-W', '-f=${db:Status-Status}\t${db:Status-Eflag}\t${Version}\n', 'tklbam']:
            return subprocess.CompletedProcess(command, self.query_status, stdout=self.package_output)
        if command[:2] == ['dpkg', '--validate-version']:
            return subprocess.CompletedProcess(command, self.validation_status)
        if command[:2] == ['dpkg', '--compare-versions']:
            self.assertEqual(['ge', '1.5.3+6'], command[-2:])
            return subprocess.CompletedProcess(command, self.comparison_status)
        self.fail('probe ran a service or unrelated command: ' + repr(command))

    def test_ready_probe_has_no_writes_network_credentials_or_service_calls(self):
        before = self.snapshot()
        with patch.object(socket, 'socket', side_effect=AssertionError('network')), \
                patch.object(Path, 'read_text', side_effect=AssertionError('saved credentials')):
            result = capabilities.probe()
        self.assertEqual({'protocol': 'hub-services-v1', 'ready': True, 'missing': []}, result)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / '__pycache__').exists())
        self.assertEqual(3, len(self.package_calls))

    def test_old_tklbam_is_unsupported_even_when_all_commands_and_components_exist(self):
        self.package_output = 'installed\tok\t1.5.3+4+ga5a4f1d+qa.profile1\n'
        self.comparison_status = 1
        self.assertEqual(['tklbam>=1.5.3+6'], capabilities.probe()['missing'])
        self.assertEqual(['dpkg', '--compare-versions', '1.5.3+4+ga5a4f1d+qa.profile1', 'ge', '1.5.3+6'], self.package_calls[-1])

    def test_accepted_tklbam_version_keeps_complete_probe_ready(self):
        self.package_output = 'installed\tok\t1.5.3+6+gac6b342\n'
        self.assertTrue(capabilities.probe()['ready'])
        self.assertIn(['dpkg', '--validate-version', '1.5.3+6+gac6b342'], self.package_calls)

    def test_unavailable_package_metadata_is_unsupported(self):
        self.query_status = 1
        self.assertEqual(['tklbam>=1.5.3+6'], capabilities.probe()['missing'])
        self.assertEqual(1, len(self.package_calls))
        for error in (FileNotFoundError('dpkg-query'), subprocess.TimeoutExpired('dpkg-query', 2)):
            with patch.object(capabilities.subprocess, 'run', side_effect=error):
                self.assertFalse(capabilities.supported_tklbam())

    def test_corrupt_or_not_installed_package_metadata_is_unsupported(self):
        for value in ('', 'installed', 'installed\tok\t\n',
                      'installed\tok\t1.5.3+6\ninstalled\tok\t2.0\n',
                      'config-files\tok\t1.5.3+6\n', 'unpacked\tok\t1.5.3+6\n',
                      'installed\treinstreq\t1.5.3+6\n'):
            with self.subTest(value=value):
                self.package_calls.clear()
                self.package_output = value
                self.assertEqual(['tklbam>=1.5.3+6'], capabilities.probe()['missing'])
                self.assertEqual(1, len(self.package_calls))
        self.package_output = 'installed\tok\tnot-a-version\n'
        self.validation_status = 1
        self.package_calls.clear()
        self.assertEqual(['tklbam>=1.5.3+6'], capabilities.probe()['missing'])
        self.assertEqual(2, len(self.package_calls))

    def test_missing_module_or_protocol_function_is_unsupported(self):
        self.modules['websocket'] = types.SimpleNamespace()
        del self.modules['hubclient_lib.api']
        result = capabilities.probe()
        self.assertFalse(result['ready'])
        self.assertEqual(['hubclient_lib.api', 'websocket'], result['missing'])
        self.assertEqual([], self.package_calls)

    def test_partial_old_helper_is_not_ready_even_with_package_files(self):
        self.helper.write_text('class Mail:\n    def enroll(self): pass\n')
        result = capabilities.probe()
        self.assertEqual([str(self.helper)], result['missing'])
        self.helper.write_text('broken syntax (')
        self.assertEqual([str(self.helper)], capabilities.probe()['missing'])

    def test_registration_protocol_must_match_installed_contract(self):
        self.modules['libinithooks.hub_launch'].PROTOCOL = 'unknown-protocol'
        self.assertEqual(['libinithooks.hub_launch'], capabilities.probe()['missing'])

    def test_console_journal_dependency_is_required_without_instantiating_handler(self):
        del self.modules['systemd.journal']
        self.assertEqual(['systemd.journal'], capabilities.probe()['missing'])

    def test_actual_dialog_wrapper_import_cannot_open_its_log(self):
        original = self.modules.__getitem__

        def imported(name):
            if name != 'libinithooks.dialog_wrapper':
                return original(name)
            spec = capabilities.importlib.util.spec_from_file_location(
                '_readiness_dialog_wrapper', ROOT / 'libinithooks/dialog_wrapper.py')
            module = capabilities.importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        handlers = list(logging.getLogger().handlers)
        with patch.object(capabilities.importlib, 'import_module', side_effect=imported), \
                patch.dict(sys.modules, {'dialog': types.SimpleNamespace()}), \
                patch.object(logging.getLogger(), 'handlers', []), \
                patch.object(logging, 'FileHandler', side_effect=AssertionError('log write')):
            self.assertTrue(capabilities.probe()['ready'])
            self.assertEqual([], logging.getLogger().handlers)
        self.assertEqual(handlers, logging.getLogger().handlers)

    def test_missing_hook_command_yaml_or_sasl_is_explicit(self):
        self.helper.chmod(0o644)
        self.config.unlink()
        self.modules['yaml'] = types.SimpleNamespace()
        with patch.object(capabilities.shutil, 'which', side_effect=lambda name: None if name == 'tklbam-init' else name), \
                patch.object(Path, 'glob', return_value=[]):
            result = capabilities.probe()
        self.assertEqual(sorted([str(self.helper), str(self.config), 'yaml', 'libsasl2-modules', 'tklbam-init']), result['missing'])

    def test_json_exit_status_distinguishes_ready_from_incomplete(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, capabilities.main(['--json']))
        self.assertTrue(json.loads(out.getvalue())['ready'])
        self.config.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1, capabilities.main(['--json']))


class EarlyCommandTests(unittest.TestCase):
    def test_probe_dispatch_precedes_root_checks_mail_and_bytecode(self):
        # Run the real executable with only a package entrypoint stub. Any
        # fallthrough would import the mail/runtime code or require root.
        program = '''import sys,types,runpy
module=types.ModuleType('libinithooks.hub_capabilities')
def main(argv):
 assert sys.dont_write_bytecode
 assert argv==['--json']
 print('{"protocol":"hub-services-v1","ready":true,"missing":[]}')
 return 0
module.main=main
sys.modules['libinithooks.hub_capabilities']=module
sys.argv=[sys.argv[1],'capabilities','--json']
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        result = subprocess.run([sys.executable, '-c', program, str(ROOT / 'bin/turnkey-hub')],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ready'])
        self.assertEqual('', result.stderr)

    def test_broken_package_produces_quiet_unsupported_json(self):
        program = '''import sys,runpy
sys.modules['libinithooks.hub_capabilities']=None
sys.argv=[sys.argv[1],'capabilities','--json']
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        result = subprocess.run([sys.executable, '-c', program, str(ROOT / 'bin/turnkey-hub')],
                                capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        self.assertFalse(json.loads(result.stdout)['ready'])
        self.assertEqual('', result.stderr)

    def test_native_register_dispatch_does_not_construct_mail(self):
        program = '''import sys,types,runpy
module=types.ModuleType('libinithooks.hub_launch')
module.main=lambda argv: 0 if argv==[] else 9
sys.modules['libinithooks.hub_launch']=module
sys.argv=[sys.argv[1],'register']
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        result = subprocess.run([sys.executable, '-c', program, str(ROOT / 'bin/turnkey-hub')],
                                capture_output=True, text=True)
        self.assertEqual((0, '', ''), (result.returncode, result.stdout, result.stderr))


@unittest.skipUnless(shutil.which('dpkg-query') and shutil.which('dpkg'), 'requires Debian query tools')
class NativeBackupVersionTests(unittest.TestCase):
    def test_real_dpkg_status_and_ordering_with_isolated_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'status'
            real_run = subprocess.run

            def query(command, **kwargs):
                if command[0] == 'dpkg-query':
                    command = [command[0], '--admindir=' + directory] + command[1:]
                return real_run(command, **kwargs)

            for status, version, expected in (
                    ('install ok installed', '1.5.3+4+ga5a4f1d+qa.profile1', False),
                    ('install ok installed', '1.5.3+6', True),
                    ('hold ok installed', '1.5.3+6+gac6b342', True),
                    ('install ok installed', '1.5.3+6~pre1', False),
                    ('deinstall ok config-files', '1.5.3+6', False),
                    ('install ok unpacked', '1.5.3+6', False),
                    ('install reinstreq installed', '1.5.3+6', False),
                    ('install ok installed', 'not-a-version', False)):
                with self.subTest(status=status, version=version):
                    path.write_text('Package: tklbam\nStatus: %s\nArchitecture: all\n'
                                    'Version: %s\nMaintainer: Fixture <fixture@example.test>\n'
                                    'Description: Isolated query fixture\n\n' % (status, version))
                    before = path.read_bytes()
                    with patch.object(capabilities.subprocess, 'run', side_effect=query):
                        self.assertEqual(expected, capabilities.supported_tklbam())
                    self.assertEqual(before, path.read_bytes())
            for value in ('', 'unparseable dpkg metadata\n'):
                path.write_text(value)
                with patch.object(capabilities.subprocess, 'run', side_effect=query):
                    self.assertFalse(capabilities.supported_tklbam())
