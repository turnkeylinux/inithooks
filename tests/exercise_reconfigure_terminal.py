"""Audit the actual terminal layout and recovery paths at three widths."""
import os
from pathlib import Path
import sys
import time
import pexpect
import pyte

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)


def capture(child, screen, stream, name):
    until = time.monotonic() + .3
    while time.monotonic() < until:
        try:
            stream.feed(child.read_nonblocking(65536, timeout=.05))
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass
    text = '\n'.join(screen.display)
    (OUT / (name + '.txt')).write_text(text + '\n')
    return text


for cols in (60, 80, 120):
    for mode in ('password', 'normal', 'error', 'cancel', 'empty', 'pending'):
        child = pexpect.spawn(sys.executable, [str(ROOT / 'tests/reconfigure_terminal_fixture.py'), mode],
            encoding='utf8', dimensions=(24, cols), timeout=5, env={**os.environ, 'TERM': 'linux'})
        screen = pyte.Screen(cols, 24); stream = pyte.Stream(screen)
        text = capture(child, screen, stream, f'{cols}-{mode}-start')
        if mode == 'password':
            assert 'Leave blank' in text, text
            child.send('\r')
            text = capture(child, screen, stream, f'{cols}-{mode}-generated')
            assert 'Example4Review-OnlyABC123' in text and 'Reconfigure' in text, text
            child.send('\r'); child.expect('PASSWORD_RETURNED')
        elif mode in ('empty', 'pending'):
            assert ('no settings' if mode == 'empty' else 'Finish initial setup') in text, text
            child.send('\r'); child.expect('MENU_RETURNED')
        else:
            assert 'Example App' in text and 'Details' in text and 'Back' in text, text
            if mode == 'normal':
                child.send('\t\t\r')
                text = capture(child, screen, stream, f'{cols}-{mode}-details')
                assert 'site address' in text, text
                child.send('\r')
                capture(child, screen, stream, f'{cols}-{mode}-back')
            child.send('\r')
            text = capture(child, screen, stream, f'{cols}-{mode}-result')
            if mode == 'error':
                assert 'could not finish' in text, text
                child.send('\r'); capture(child, screen, stream, f'{cols}-{mode}-recovered')
            else:
                assert 'Choose what' in text, text
            child.send('\x1b'); child.expect('MENU_RETURNED')
        child.expect(pexpect.EOF); child.close(); assert child.exitstatus == 0
        print('PASS', cols, mode, flush=True)
