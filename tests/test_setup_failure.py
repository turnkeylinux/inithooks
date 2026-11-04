"""A failed application hook must not expose an unfinished appliance."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class SetupFailureTest(unittest.TestCase):
    def test_failure_keeps_fence_and_retry_completes(self):
        self.check_retry(1)

    def test_cancel_keeps_fence_and_retry_completes(self):
        self.check_retry(130)

    def check_retry(self, status):
        loader = importlib.machinery.SourceFileLoader('setup_test', str(ROOT / 'turnkey-init'))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        with patch.dict(sys.modules, {'conffile': types.SimpleNamespace(ConfFile=object)}):
            loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hooks = root / 'firstboot.d'
            hooks.mkdir()
            app = hooks / '40application'
            app.write_text('#!/bin/sh\nexit %d\n' % status)
            app.chmod(0o755)
            fence = hooks / '97turnkey-init-fence-disable'
            fence.write_text('#!/bin/sh\ntouch ' + str(root / 'unfenced') + '\n')
            fence.chmod(0o755)
            runner = module.InitHooks()
            runner.conf = types.SimpleNamespace(inithooks_path=directory)
            with self.assertRaises(subprocess.CalledProcessError):
                runner.execute('firstboot.d')
            self.assertFalse((root / 'unfenced').exists())
            app.write_text('#!/bin/sh\nexit 0\n')
            runner.execute('firstboot.d')
            self.assertTrue((root / 'unfenced').exists())


if __name__ == '__main__':
    unittest.main()
