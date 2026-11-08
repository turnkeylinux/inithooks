"""Exercise the packaged transport against separately provisioned live read caps.

Only the fixture controller holds publication credentials. Synchronize its
fixed-channel actions with the ready messages below; capabilities never print.
"""
import json
from pathlib import Path
import select
import sys
import time

import websocket
from libinithooks.hub_push import Notifications, validated_notification


def event(watcher, expected, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if select.select([watcher.reader], [], [], min(1, until - time.monotonic()))[0]:
            value = watcher.take()
            if value == expected:
                return
            assert value == 'subscribed', (expected, value)
    raise AssertionError('No ' + expected + ' event received')


def open_watcher(path, name):
    capability = validated_notification(json.loads(Path(path).read_text())[name]['notification'])
    assert capability, 'Fixture capability expired or invalid'
    return Notifications(capability, websocket)


path = sys.argv[1]
if len(sys.argv) > 2 and sys.argv[2] == 'expiry':
    watcher = open_watcher(path, 'C')
    try:
        event(watcher, 'subscribed')
        event(watcher, 'expired', 12)
        print('PASS: packaged client observed live expiry', flush=True)
    finally:
        watcher.close()
else:
    first, other = open_watcher(path, 'A'), open_watcher(path, 'B')
    try:
        event(first, 'subscribed')
        event(other, 'subscribed')
        print('READY: publish A code_submitted', flush=True)
        event(first, 'code_submitted', 60)
        assert not select.select([other.reader], [], [], 1)[0], 'Cross-channel event leaked'
        old_socket = first.socket
        old_socket.abort()
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if first.socket is not None and first.socket is not old_socket and first.socket.connected:
                break
            time.sleep(.05)
        else:
            raise AssertionError('Transport did not reconnect')
        print('READY: publish A approved after reconnect', flush=True)
        event(first, 'approved', 60)
        assert not select.select([other.reader], [], [], 1)[0], 'Cross-channel approval leaked'
        replay = open_watcher(path, 'A')
        try:
            event(replay, 'approved')
        finally:
            replay.close()
        print('READY: publish B denied', flush=True)
        event(other, 'denied', 60)
        print('PASS: packaged subscribe, code submission, reconnect, replay, isolation and denial', flush=True)
    finally:
        first.close()
        other.close()
