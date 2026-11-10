"""Notification authority, transport and owned-dialog lifecycle boundaries."""
import json
import os
from pathlib import Path
import select
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from libinithooks import hub_push as push


def capability(expiry=None):
    expiry = expiry or int(time.time()) + 600
    return {'url': push.URL, 'token': 'v1.' + 'a' * 32 + '.' + str(expiry) + '.' + 'b' * 64,
            'expires_at': expiry}


class NotificationTest(unittest.TestCase):
    def test_capability_is_fixed_origin_bounded_and_consistent(self):
        valid = capability()
        self.assertEqual(push.validated_notification({**valid, 'ignored': 'value'}), valid)
        for change in ({'url': push.URL + '?token=secret'}, {'url': 'wss://other.example/'},
                       {'token': 'invalid'}, {'expires_at': True}, {'expires_at': valid['expires_at'] + 1}):
            self.assertIsNone(push.validated_notification({**valid, **change}))
        for expiry in (int(time.time()) - 1, int(time.time()) + 1000):
            self.assertIsNone(push.validated_notification(capability(expiry)))

    def test_saved_capability_is_bound_to_displayed_pairing(self):
        with tempfile.TemporaryDirectory() as root, patch.object(push, 'STATE', Path(root) / 'state.json'):
            push.STATE.write_text(json.dumps({'pairing': {'user_code': 'ABCD-1234', 'notification': capability()}}))
            self.assertIsNotNone(push.saved_notification('ABCD-1234'))
            self.assertIsNone(push.saved_notification('DIFF-1234'))
            push.STATE.write_text('broken')
            self.assertIsNone(push.saved_notification('ABCD-1234'))

    def watcher(self, frames, status=101):
        socket = Mock()
        socket.getstatus.return_value = status
        socket.recv.side_effect = frames
        module = Mock(WebSocketException=OSError, WebSocketTimeoutException=TimeoutError)
        module.WebSocket.return_value = socket
        watcher = push.Notifications(capability(), module)
        self.addCleanup(watcher.close)
        return watcher, socket, module

    def test_transport_verifies_tls_bearer_header_no_redirect_and_terminal_priority(self):
        watcher, socket, module = self.watcher(['{"state":"code_submitted"}', '{"state":"approved"}'])
        watcher.thread.join(2)
        self.assertFalse(watcher.thread.is_alive())
        module.WebSocket.assert_called_once_with(sslopt={'cert_reqs': ssl.CERT_REQUIRED, 'check_hostname': True})
        args, kwargs = socket.connect.call_args
        self.assertEqual(args, (push.URL,))
        self.assertEqual(kwargs['header'], {'Authorization': 'Bearer ' + watcher.capability['token']})
        self.assertEqual(kwargs['redirect_limit'], 0)
        self.assertEqual(kwargs['timeout'], 3)
        self.assertEqual(watcher.take(), 'approved')
        self.assertIsNone(watcher.take())
        socket.shutdown.assert_called_once()

    def test_redirect_response_cannot_subscribe_or_receive(self):
        watcher, socket, module = self.watcher([], status=302)
        watcher.thread.join(2)
        socket.recv.assert_not_called()
        self.assertIsNone(watcher.take())

    def test_initial_subscribe_is_not_lost_when_code_submitted_arrives_together(self):
        with patch.object(threading.Thread, 'start'):
            watcher = push.Notifications(capability(), Mock())
        self.addCleanup(lambda: (os.close(watcher.reader), os.close(watcher.writer)))
        watcher._signal('subscribed')
        watcher._signal('code_submitted')
        self.assertEqual(watcher.take(), 'subscribed')

    def test_close_wakes_live_receive_and_leaves_no_transport_thread(self):
        receiving = threading.Event()
        closed = threading.Event()
        socket = Mock()
        socket.getstatus.return_value = 101
        def receive():
            receiving.set()
            closed.wait(2)
            return ''
        socket.recv.side_effect = receive
        socket.abort.side_effect = closed.set
        module = Mock(WebSocketException=OSError, WebSocketTimeoutException=TimeoutError)
        module.WebSocket.return_value = socket
        watcher = push.Notifications(capability(), module)
        self.assertTrue(receiving.wait(1))
        watcher.close()
        self.assertFalse(watcher.thread.is_alive())
        self.assertTrue(watcher.closed)
        watcher._signal('approved')  # Closed descriptors must never be reused.


class DialogLifecycleTest(unittest.TestCase):
    def run_dialog(self, polls, signal='approved'):
        child = Mock(pid=12345)
        child.poll.side_effect = polls
        notifications = Mock(reader=99)
        notifications.take.return_value = signal
        with patch.object(push.subprocess, 'Popen', return_value=child), \
                patch.object(push.os, 'pidfd_open', side_effect=OSError), \
                patch.object(push.sys, 'stdin') as stdin, \
                patch.object(push.termios, 'tcgetattr', return_value=['saved']), \
                patch.object(push.termios, 'tcsetattr') as restore, \
                patch.object(push.select, 'select', return_value=([99], [], [])):
            stdin.fileno.return_value = 0
            result = push.confirm(Mock(width=60), 'Instructions', notifications)
        restore.assert_called_once_with(0, push.termios.TCSANOW, ['saved'])
        return result, child, notifications

    def test_already_observed_skip_wins_over_readable_approval(self):
        result, child, notification = self.run_dialog([None, 1, 1])
        self.assertEqual(result, 'skip')
        notification.take.assert_not_called()
        child.terminate.assert_not_called()

    def test_notification_terminates_and_reaps_only_owned_dialog(self):
        result, child, notification = self.run_dialog([None, None, None])
        self.assertEqual(result, 'approved')
        child.terminate.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=2)

    def test_escape_and_backend_failure_are_not_skip(self):
        for code, expected in ((255, 'escape'), (2, 'error'), (0, 'check')):
            result, child, notification = self.run_dialog([code, code])
            self.assertEqual(result, expected)
            child.terminate.assert_not_called()

    def test_periodic_check_keeps_working_without_notifications(self):
        for notifications in (None, Mock(reader=99)):
            child = Mock(pid=12345)
            child.poll.return_value = None
            with patch.object(push.subprocess, 'Popen', return_value=child) as spawn, \
                    patch.object(push.os, 'pidfd_open', side_effect=OSError), \
                    patch.object(push.sys, 'stdin') as stdin, \
                    patch.object(push.termios, 'tcgetattr', return_value=['saved']), \
                    patch.object(push.termios, 'tcsetattr'), \
                    patch.object(push.time, 'monotonic', side_effect=[0, 5]):
                stdin.fileno.return_value = 0
                result = push.confirm(Mock(width=60), 'Instructions', notifications, pending=True)
            self.assertEqual(result, 'poll')
            command = spawn.call_args.args[0]
            self.assertEqual(command[command.index('--yes-label') + 1], "I've approved")
            child.terminate.assert_called_once_with()

    def test_manual_fallback_preserves_three_way_result_and_backend_error(self):
        for code, expected in ((255, 'escape'), (1, 'skip'), (2, 'error'), (0, 'check')):
            child = Mock(pid=12345)
            child.wait.return_value = child.poll.return_value = code
            with patch.object(push.subprocess, 'Popen', return_value=child), \
                    patch.object(push.sys, 'stdin') as stdin, \
                    patch.object(push.termios, 'tcgetattr', return_value=['saved']), \
                    patch.object(push.termios, 'tcsetattr') as restore:
                stdin.fileno.return_value = 0
                self.assertEqual(push.confirm(Mock(width=60), 'Instructions'), expected)
            restore.assert_called_once_with(0, push.termios.TCSANOW, ['saved'])
            child.wait.assert_not_called()


if __name__ == '__main__':
    unittest.main()
