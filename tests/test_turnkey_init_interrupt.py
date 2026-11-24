"""The console interrupt should not produce a parent-process traceback."""
import os
from pathlib import Path
import runpy
import signal
import sys
import types
import unittest
from unittest.mock import patch


class ConsoleInterruptTests(unittest.TestCase):
    def test_setup_only_returns_without_starting_a_nested_console(self):
        conffile = types.ModuleType('conffile')
        conffile.ConfFile = type('ConfFile', (), {})
        with patch.dict(sys.modules, {'conffile': conffile}):
            namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'turnkey-init'))
        main = namespace['main']
        calls = []
        hooks = types.SimpleNamespace(execute=lambda name: calls.append(name))
        with patch.dict(main.__globals__, {'InitHooks': lambda: hooks}), \
             patch('os.geteuid', return_value=0), \
             patch('sys.argv', ['turnkey-init', '--setup-only']), \
             patch('subprocess.run') as run:
            self.assertIsNone(main())
        self.assertEqual(calls, ['firstboot.d'])
        run.assert_not_called()

    def test_setup_only_stops_on_failed_initialization(self):
        import subprocess
        conffile = types.ModuleType('conffile')
        conffile.ConfFile = type('ConfFile', (), {})
        with patch.dict(sys.modules, {'conffile': conffile}):
            namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'turnkey-init'))
        main = namespace['main']
        def fail(_name):
            raise subprocess.CalledProcessError(1, ['/tmp/40example'])
        hooks = types.SimpleNamespace(execute=fail)
        with patch.dict(main.__globals__, {'InitHooks': lambda: hooks}), \
             patch('os.geteuid', return_value=0), \
             patch('sys.argv', ['turnkey-init', '--setup-only']), \
             patch('subprocess.run') as run, \
             patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                main()
        run.assert_not_called()

    def test_interrupt_during_console_returns_interrupt_status(self):
        conffile = types.ModuleType('conffile')
        conffile.ConfFile = type('ConfFile', (), {})
        with patch.dict(sys.modules, {'conffile': conffile}):
            namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'turnkey-init'))
        main = namespace['main']
        hooks = types.SimpleNamespace(execute=lambda name: None)

        def interrupt(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGINT)
            return types.SimpleNamespace(returncode=130)

        with patch.dict(main.__globals__, {'InitHooks': lambda: hooks}), \
             patch('os.geteuid', return_value=0), \
             patch('sys.argv', ['turnkey-init']), \
             patch('subprocess.run', side_effect=interrupt):
            self.assertEqual(main(), 130)


if __name__ == '__main__':
    unittest.main()
