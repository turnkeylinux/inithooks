"""Real owned dialog with controlled wake events and isolated local state."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import termios
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('hubservices', ROOT / 'bin/hubservices.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)
mode, control = sys.argv[1], Path(sys.argv[2])
control.unlink(missing_ok=True)
state = tempfile.TemporaryDirectory()
ui.onboarding.STATE_DIR = Path(state.name)
ui.onboarding.HUB_STATE = Path(state.name) / 'hub.json'
calls = []


class Wake:
    def __init__(self):
        self.reader, self.writer = os.pipe2(os.O_NONBLOCK)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopped.wait(.03):
            if control.exists():
                self.event = control.read_text().strip()
                control.unlink()
                os.write(self.writer, b'1')

    def take(self):
        os.read(self.reader, 4096)
        return self.event

    def close(self):
        self.stopped.set()
        self.thread.join()
        os.close(self.reader)
        os.close(self.writer)


def exchange(*arguments):
    calls.append(arguments)
    if arguments == ('connect', '--start'):
        return {'verification_uri': 'https://hub.turnkeylinux.org/link', 'user_code': 'D34C-F5A9', 'interval': 1 if mode == 'fallback-auto' else 5}
    if '--notified' in arguments or mode in ('manual', 'fallback-auto'):
        return {'connection_status': 'linked'}
    return {'status': 'pending'}


ui.mail_call = exchange
ui.hub_push.Notifications.open = lambda code: None if mode.startswith('fallback-') else Wake()
if mode == 'fallback-error':
    original = ui.hub_push.subprocess.Popen
    ui.hub_push.subprocess.Popen = lambda command, **kwargs: original(['/bin/sh', '-c', 'exit 2'], **kwargs)
before = termios.tcgetattr(0)
result = 0
try:
    ui.connect_mail(ui.Dialog('TurnKey GNU/Linux - Hub services', width=min(60, os.get_terminal_size().columns - 4)), 'confconsole')
except SystemExit as error:
    result = error.code
assert termios.tcgetattr(0) == before, 'Terminal modes were not restored'
assert not any(t.name == 'hub-pairing-notifications' for t in threading.enumerate())
print('RESULT', json.dumps({'exit': result, 'calls': calls,
    'events': [event['event_name'] for event in ui.onboarding.load()['pending']]}))
