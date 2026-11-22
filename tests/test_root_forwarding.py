"""Exercise the root forwarding switch with an isolated aliases file."""
from pathlib import Path
import subprocess
import tempfile
import unittest

class RootForwardingTest(unittest.TestCase):
    def test_disable_enable_and_preserve_choice_during_reconfiguration(self):
        source = (Path(__file__).resolve().parents[1] / 'bin/secalerts.sh').read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aliases = root / 'aliases'
            aliases.write_text('root: owner@example.net\n')
            state = root / 'state'
            commands = root / 'bin'
            commands.mkdir()
            for name in ('newaliases', 'turnkey-onboarding', 'mail'):
                command = commands / name
                command.write_text('#!/bin/sh\nexit 0\n')
                command.chmod(0o755)
            script = source.replace('/etc/aliases', str(aliases)).replace('/var/lib/turnkey-onboarding', str(state)).replace('/etc/cron-apt/config', str(root / 'missing'))
            script = script.replace('"$(dirname "$0")/turnkey-onboarding"', str(commands / 'turnkey-onboarding'))
            executable = root / 'secalerts.sh'
            executable.write_text(script)
            env = dict(__import__('os').environ, PATH=str(commands) + ':' + __import__('os').environ['PATH'])
            def run(*args):
                subprocess.run(['bash', str(executable), *args], env=env, check=True, capture_output=True)
            run('--root-forward', 'off', 'owner@example.net')
            self.assertEqual(aliases.read_text().strip(), 'root:    root')
            run('new@example.net')
            self.assertEqual(aliases.read_text().strip(), 'root:    root')
            run('--root-forward', 'on', 'new@example.net')
            self.assertEqual(aliases.read_text().strip(), 'root:    new@example.net')
            self.assertFalse((state / 'root-forward-disabled').exists())

class RootForwardingDialogTest(unittest.TestCase):
    def test_turning_off_keeps_the_address_for_enabling_later(self):
        import importlib.util
        import logging
        logging.getLogger().addHandler(logging.NullHandler())
        from unittest.mock import Mock, patch
        from libinithooks import onboarding
        spec = importlib.util.spec_from_file_location('rootmail', Path(__file__).resolve().parents[1] / 'bin/rootmail.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        dialog = Mock()
        dialog.yesno.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            aliases = Path(directory) / 'aliases'
            aliases.write_text('root: owner@example.net\n')
            with patch.object(module, 'Dialog', return_value=dialog), \
                 patch.object(onboarding, 'ALIASES', aliases), \
                 patch.object(onboarding, 'load', return_value={}), \
                 patch.object(onboarding, 'notification_choice') as remember, \
                 patch.object(module, 'DISABLED', str(Path(directory) / 'disabled')), \
                 patch.object(module.subprocess, 'run') as run:
                module.main()
            remember.assert_called_once_with('owner@example.net')
            self.assertEqual(run.call_args.args[0][1:], ['--root-forward', 'off', 'owner@example.net'])


if __name__ == '__main__':
    unittest.main()
