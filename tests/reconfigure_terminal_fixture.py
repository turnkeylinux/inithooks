"""Real terminal widgets with harmless hooks and a disposable credential."""
import logging
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.basicConfig(stream=sys.stderr, level=logging.CRITICAL)
from libinithooks import reconfigure, dialog_wrapper, passwords

mode = sys.argv[1]
if mode == 'password':
    passwords.generate = lambda *args: 'Example4Review-OnlyABC123'
    d = dialog_wrapper.Dialog('TurnKey GNU/Linux - First boot configuration')
    d.get_password('System Password', 'Enter a password for the root account.')
    print('PASSWORD_RETURNED')
else:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        reconfigure.setup_pending = lambda: mode == 'pending'
        if mode != 'empty':
            p = root / '40example'
            p.write_text('#!/bin/sh\n# reconfigure-title: Example App\n# reconfigure-description: Change the administrator password, recovery email and site address.\nexit ' + ('1' if mode == 'error' else '130' if mode == 'cancel' else '0') + '\n')
            p.chmod(0o755)
        reconfigure.menu(root)
    print('MENU_RETURNED')
