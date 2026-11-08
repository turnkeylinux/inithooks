"""Exercise firstboot linking, failure and Skip at real terminal dimensions."""
import os
from pathlib import Path
import sys
import time
import pexpect
import pyte

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)


def capture(child, screen, stream, name):
    end = time.monotonic() + .4
    while time.monotonic() < end:
        try:
            stream.feed(child.read_nonblocking(65536, timeout=.05))
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass
    text = '\n'.join(screen.display)
    (OUT / (name + '.txt')).write_text(text + '\n')
    assert "Can't make new window" not in text and 'Terminal too small' not in text
    return text


for cols in (60, 80, 120):
    for mode in ('ready', 'pending', 'expired', 'denied', 'error', 'skip', 'start-error', 'escape'):
        child = pexpect.spawn('python3', [str(ROOT / 'tests/hub_services_terminal_fixture.py'), mode],
                              encoding='utf-8', dimensions=(24, cols), timeout=10,
                              env={**os.environ, 'TERM': 'linux', 'NCURSES_NO_UTF8_ACS': '1'})
        screen = pyte.Screen(cols, 24)
        stream = pyte.Stream(screen)
        text = capture(child, screen, stream, f'{cols}-firstboot-{mode}-approval')
        if mode == 'start-error':
            assert 'Retry' in text and 'Skip' in text
        else:
            assert 'D34C-F5A9' in text and "I've entered the code" in text
            assert 'App email delivery' in text and 'security' in text and 'announcements' in text and 'migration' in text
        if mode in ('skip', 'start-error'):
            child.send('\t\r')
            child.expect('EVENTS ([^\r\n]+)')
            assert 'skipped' in child.match.group(1)
        elif mode == 'escape':
            child.send('\x1b')
            time.sleep(1.1)  # dialog distinguishes ESC from an escape-key sequence
            text = capture(child, screen, stream, f'{cols}-firstboot-quit-confirmation')
            assert 'really want to' in text and 'quit?' in text
            child.send('\r')
            child.expect('EXIT 130')
            child.expect('EVENTS ([^\r\n]+)')
            assert 'skipped' not in child.match.group(1)
        else:
            child.send('\r')
            text = capture(child, screen, stream, f'{cols}-firstboot-{mode}-result')
            if mode == 'pending':
                assert 'Waiting for approval' in text
                child.send('\r')
                text = capture(child, screen, stream, f'{cols}-firstboot-{mode}-connected')
            assert ('Hub linked' in text) if mode in ('ready', 'pending') else ('Error' in text)
            child.send('\r')
            child.expect('EVENTS ([^\r\n]+)')
            events = child.match.group(1)
            assert 'approval_checked' in events and 'skipped' not in events
            assert ('link_completed' in events) if mode in ('ready', 'pending') else ('error' in events)
        child.expect('FINISHED')
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
print('Firstboot link/Skip/pending/expired/denied/error/ESC: PASS at 60/80/120 columns')
