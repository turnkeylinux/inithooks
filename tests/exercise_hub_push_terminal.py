"""Host PTY audit of the packaged connection screen in an owned Podman fixture."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--container', required=True)
parser.add_argument('--source', default='/opt/push-qa/inithooks')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--pyte-path')
args = parser.parse_args()
if args.pyte_path:
    sys.path.insert(0, args.pyte_path)
import pyte
from PIL import Image, ImageDraw, ImageFont

args.output.mkdir(parents=True, exist_ok=True)
colors = {'black':'#000000','red':'#aa0000','green':'#00aa00','brown':'#aa5500',
          'blue':'#0000aa','magenta':'#aa00aa','cyan':'#00aaaa','white':'#aaaaaa'}
font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 16)


def exercise(mode, columns):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, columns, 0, 0))
    control = '/opt/push-qa/wake-' + mode
    process = subprocess.Popen(['podman', 'exec', '-it', '-e', 'TERM=linux',
        '-e', 'PYTHONPATH=' + args.source, args.container, 'python3',
        args.source + '/tests/hub_push_terminal_fixture.py', mode, control],
        stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    raw = bytearray()
    screen = pyte.Screen(columns, 24)
    stream = pyte.Stream(screen)

    def drain(seconds=.2):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if select.select([master], [], [], .03)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return
                raw.extend(data)
                stream.feed(data.decode('utf-8', 'replace'))

    def capture(label):
        drain()
        path = args.output / ('%s-%s-%s' % (columns, mode, label))
        path.with_suffix('.ansi').write_bytes(raw)
        text = '\n'.join(screen.display)
        path.with_suffix('.txt').write_text(text + '\n')
        bitmap = Image.new('RGB', (columns * 10, 480))
        draw = ImageDraw.Draw(bitmap)
        for y in range(24):
            for x in range(columns):
                cell = screen.buffer[y][x]
                fg = colors.get(cell.fg, '#cccccc' if cell.fg == 'default' else '#' + cell.fg)
                bg = colors.get(cell.bg, '#000000' if cell.bg == 'default' else '#' + cell.bg)
                if cell.bold and cell.fg == 'white':
                    fg = '#ffffff'
                if cell.reverse:
                    fg, bg = bg, fg
                draw.rectangle((x*10, y*20, (x+1)*10, (y+1)*20), fill=bg)
                draw.text((x*10, y*20), cell.data, font=font, fill=fg)
        bitmap.save(path.with_suffix('.png'))
        return text

    def wait(text, label):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            drain()
            if text in '\n'.join(screen.display):
                return capture(label)
            if process.poll() is not None:
                break
        raise AssertionError(text + '\n' + capture('failure'))

    def signal(event):
        subprocess.run(['podman', 'exec', args.container, 'python3', '-c',
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2])',
            control, event], check=True)

    try:
        if mode == 'fallback-error':
            wait('could not continue', 'backend-error')
        else:
            initial = wait('D34C-F5A9', 'code')
            assert "I've entered the code" in initial and 'Skip' in initial
            assert 'hub.turnkeylinux.org/link' in initial and 'Connect email delivery' in initial
            assert 'Open https://hub.turnkeylinux.org/link' in initial
            assert 'Get security alerts by email.' in initial
            row = next(line for line in initial.splitlines() if 'D34C-F5A9' in line)
            center = row.index('D34C-F5A9') + len('D34C-F5A9') / 2
            dialog_center = (row.index('│') + row.rindex('│') + 1) / 2
            assert abs(center - dialog_center) <= 1, (columns, row)

        if mode == 'auto':
            signal('subscribed')
            wait('D34C-F5A9', 'reconciled')
            signal('code_submitted')
            browser = wait("I've approved", 'browser')
            assert 'Approve in your browser.' in browser
            signal('approved')
            wait('Hub linked', 'success')
            os.write(master, b'\r')
        elif mode == 'fallback-auto':
            wait('Hub linked', 'success')
            os.write(master, b'\r')
        elif mode == 'manual':
            os.write(master, b'\r')
            wait('Hub linked', 'success')
            os.write(master, b'\r')
        elif mode in ('skip', 'fallback-skip'):
            os.write(master, b'\t\r')
        elif mode == 'fallback-error':
            os.write(master, b'\r')
        else:
            os.write(master, b'\x1b')
            wait('really want to quit', 'escape')
            os.write(master, b'\r')
        process.wait(timeout=8)
        drain()
        capture('returned')
        text = raw.decode('utf-8', 'replace')
        assert process.returncode == 0, text
        assert 'Traceback' not in text and 'Warning:' not in text and 'warning:' not in text
        result = json.JSONDecoder().raw_decode(text.split('RESULT ', 1)[1])[0]
        if mode == 'auto':
            assert result['calls'] == [['connect', '--start'], ['connect', '--poll'], ['connect', '--poll', '--notified']], result
            assert 'approval_checked' not in result['events'] and 'link_completed' in result['events']
        if mode == 'fallback-auto':
            assert result['calls'] == [['connect', '--start'], ['connect', '--poll']], result
            assert 'approval_checked' not in result['events'] and 'link_completed' in result['events']
        if mode in ('skip', 'escape', 'fallback-skip', 'fallback-escape', 'fallback-error'):
            assert result['calls'] == [['connect', '--start']], result
            assert ('skipped' in result['events']) == mode.endswith('skip'), result
            assert result['exit'] == (1 if mode == 'fallback-error' else 130 if mode.endswith('escape') else 0), result
        print('PASS', columns, mode, flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        os.close(master)


for columns in (60, 80, 120):
    exercise('auto', columns)
for mode in ('manual', 'skip', 'escape', 'fallback-auto', 'fallback-skip', 'fallback-escape', 'fallback-error'):
    exercise(mode, 80)
