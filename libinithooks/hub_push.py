"""Optional pairing notifications and an owned, interruptible connection dialog."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import json
import os
from pathlib import Path
import re
import select
import shutil
import ssl
import subprocess
import sys
import termios
import textwrap
import threading
import time
from libinithooks.interrupt import dialog_interrupt

URL = 'wss://ajax.turnkeylinux.org/initfence/v2/pairing'
STATE = Path('/var/lib/turnkey-mail/state.json')
TERMINAL = {'approved', 'denied', 'expired'}


def validated_notification(value):
    if not isinstance(value, dict) or value.get('url') != URL:
        return None
    token, expiry = value.get('token'), value.get('expires_at')
    match = re.fullmatch(r'v1\.[a-f0-9]{32}\.([0-9]{1,12})\.[a-f0-9]{64}', token or '') if isinstance(token, str) else None
    if not match or type(expiry) is not int or int(match[1]) != expiry or not time.time() < expiry <= time.time() + 900:
        return None
    return {'url': URL, 'token': token, 'expires_at': expiry}


def saved_notification(user_code):
    try:
        pairing = json.loads(STATE.read_text()).get('pairing', {})
        if pairing.get('user_code') == user_code:
            return validated_notification(pairing.get('notification'))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


class Notifications:
    """Read-only WSS transport. The UI thread owns all dialogs and HTTPS actions."""
    def __init__(self, capability, websocket_module):
        self.capability = capability
        self.websocket = websocket_module
        self.reader, self.writer = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        self.events = deque()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.closed = False
        self.socket = None
        self.thread = threading.Thread(target=self._run, daemon=True, name='hub-pairing-notifications')
        self.thread.start()

    @classmethod
    def open(cls, user_code):
        capability = saved_notification(user_code)
        if capability:
            try:
                import websocket
            except ImportError:
                return None
            return cls(capability, websocket)
        return None

    def _signal(self, event):
        with self.lock:
            if not self.closed:
                self.events.append(event)
                with suppress(BlockingIOError):
                    os.write(self.writer, b'1')

    def take(self):
        with self.lock:
            with suppress(BlockingIOError):
                os.read(self.reader, 4096)
            events = list(self.events)
            self.events.clear()
        return next((event for event in events if event in TERMINAL),
                    'subscribed' if 'subscribed' in events else events[-1] if events else None)

    def _run(self):
        submitted = False
        delay = 1
        while not self.stopped.is_set() and time.time() < self.capability['expires_at']:
            client = self.websocket.WebSocket(sslopt={'cert_reqs': ssl.CERT_REQUIRED, 'check_hostname': True})
            with self.lock:
                self.socket = client
            try:
                client.connect(URL, header={'Authorization': 'Bearer ' + self.capability['token']},
                               timeout=3, redirect_limit=0, suppress_origin=True)
                if client.getstatus() != 101:
                    raise OSError('Notification subscription failed')
                if self.stopped.is_set():
                    return
                self._signal('subscribed')
                client.settimeout(1)
                while not self.stopped.is_set() and time.time() < self.capability['expires_at']:
                    try:
                        frame = client.recv()
                    except self.websocket.WebSocketTimeoutException:
                        continue
                    if not isinstance(frame, str) or not frame or len(frame) > 256:
                        break
                    value = json.loads(frame)
                    if not isinstance(value, dict) or set(value) != {'state'}:
                        break
                    state = value['state']
                    if not isinstance(state, str):
                        break
                    if state in TERMINAL:
                        self._signal(state)
                        return
                    if state == 'code_submitted' and not submitted:
                        submitted = True
                        self._signal(state)
                    elif state != 'code_submitted':
                        break
            except (OSError, ValueError, self.websocket.WebSocketException):
                # Notifications are optional. Never expose capability-bearing errors.
                pass
            finally:
                with suppress(OSError):
                    client.shutdown()
                with self.lock:
                    self.socket = None
            self.stopped.wait(min(delay, max(0, self.capability['expires_at'] - time.time())))
            delay = min(30, delay * 2)
        if not self.stopped.is_set():
            self._signal('expired')

    def close(self):
        self.stopped.set()
        with self.lock:
            if self.socket:
                with suppress(OSError):
                    self.socket.abort()
            if not self.closed:
                self.closed = True
                os.close(self.reader)
                os.close(self.writer)
        # A connection attempt must not delay an explicit Skip. Its daemon thread
        # checks stopped before signaling and closes its own bounded connection.
        self.thread.join(timeout=.2)


def _action(returncode):
    return {0: 'check', 1: 'skip', 255: 'escape'}.get(returncode, 'error')


def confirm(dialog, instructions, notifications=None, *, relink=False, pending=False, expires_at=None, poll=None):
    with dialog_interrupt() as interrupted:
        result = _confirm(dialog, instructions, notifications, relink=relink, pending=pending,
                          expires_at=expires_at, poll=poll)
    return 'escape' if interrupted[0] else result


def _confirm(dialog, instructions, notifications=None, *, relink=False, pending=False, expires_at=None, poll=None):
    terminal = shutil.get_terminal_size((80, 24))
    width = min(dialog.width, terminal.columns - 4)
    lines = sum(max(1, len(textwrap.wrap(line, max(20, width - 6)))) for line in instructions.splitlines())
    label = "I've approved"
    deadline = expires_at
    command = ['dialog', '--no-collapse', '--no-mouse', '--backtitle',
               'TurnKey GNU/Linux - Hub services' if relink else 'TurnKey GNU/Linux - First boot configuration',
               '--title', 'Connect to TurnKey Hub', '--yes-label', label, '--no-label', 'Skip',
               '--yesno', '\n' + instructions, str(min(terminal.lines - 2, lines + 6)), str(width)]
    try:
        descriptor = sys.stdin.fileno()
        modes = termios.tcgetattr(descriptor)
    except (OSError, ValueError, termios.error):
        return 'error'
    child = None
    process_fd = None
    executor = ThreadPoolExecutor(max_workers=1) if poll else None
    future = None
    try:
        child = subprocess.Popen(command, stderr=subprocess.DEVNULL)
        if hasattr(os, 'pidfd_open'):
            with suppress(OSError):
                process_fd = os.pidfd_open(child.pid)
        while True:
            code = child.poll()
            if code is not None:
                if future:
                    result = future.result()
                    if result.get('status') != 'pending':
                        return ('poll_result', result)
                return _action(code)
            if future and future.done():
                result = future.result()
                future = None
                if result.get('status') != 'pending':
                    return ('poll_result', result)
                deadline = None
            remaining = None if deadline is None else deadline - time.monotonic()
            if not future and remaining is not None and remaining <= 0:
                if not executor:
                    return 'poll'
                future = executor.submit(poll)
            readers = ([notifications.reader] if notifications else []) + ([process_fd] if process_fd is not None else [])
            timeout = .1 if future else None if remaining is None else max(0, remaining)
            if process_fd is None:
                timeout = .1 if timeout is None else min(.1, timeout)
            readable, unused, unused2 = select.select(readers, [], [], timeout)
            # A user exit already observed wins over a concurrent notification.
            code = child.poll()
            if code is not None:
                if future:
                    result = future.result()
                    if result.get('status') != 'pending':
                        return ('poll_result', result)
                return _action(code)
            if notifications and notifications.reader in readable:
                event = notifications.take()
                if event:
                    if future:
                        result = future.result()
                        if result.get('status') != 'pending':
                            return ('poll_result', result)
                    return event
    except OSError:
        return 'error'
    finally:
        if executor:
            executor.shutdown(wait=True)
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if process_fd is not None:
            os.close(process_fd)
        with suppress(OSError, termios.error):
            termios.tcsetattr(descriptor, termios.TCSANOW, modes)
