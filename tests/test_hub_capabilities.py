"""Readiness is an offline installed-component contract, not a release label."""
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import socket
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
        self.patches = [patch.object(capabilities, 'HELPER', self.helper),
                        patch.object(capabilities, 'EXECUTABLES', (str(self.helper),)),
                        patch.object(capabilities, 'FILES', (str(self.config),)),
                        patch.object(capabilities.importlib, 'import_module', side_effect=self.modules.__getitem__),
                        patch.object(capabilities.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name),
                        patch.object(Path, 'glob', return_value=[self.root / 'libplain.so'])]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.stat().st_mode, p.read_bytes())
                for p in self.root.rglob('*') if p.is_file()}

    def test_ready_probe_has_no_writes_network_credentials_or_service_calls(self):
        before = self.snapshot()
        with patch.object(socket, 'socket', side_effect=AssertionError('network')), \
                patch.object(subprocess, 'run', side_effect=AssertionError('service')), \
                patch.object(Path, 'read_text', side_effect=AssertionError('saved credentials')):
            result = capabilities.probe()
        self.assertEqual({'protocol': 'hub-services-v1', 'ready': True, 'missing': []}, result)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / '__pycache__').exists())

    def test_missing_module_or_protocol_function_is_unsupported(self):
        self.modules['websocket'] = types.SimpleNamespace()
        del self.modules['hubclient_lib.api']
        result = capabilities.probe()
        self.assertFalse(result['ready'])
        self.assertEqual(['hubclient_lib.api', 'websocket'], result['missing'])

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
