#!/usr/bin/env python3
"""Exercise pre-enrollment mail with real Postfix in a disposable container.

Run with TURNKEY_MAIL_DISPOSABLE_FIXTURE=yes and the helper path as argument.
SMTP authentication is covered separately; this check keeps delivery offline
and inspects the real queue after the helper activates its sender policy.
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

if os.environ.get('TURNKEY_MAIL_DISPOSABLE_FIXTURE') != 'yes':
    raise SystemExit('Only run inside the disposable Postfix client fixture.')

loader = importlib.machinery.SourceFileLoader('mail', sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
mail = importlib.util.module_from_spec(spec)
loader.exec_module(mail)


def command(*args, data=None):
    return subprocess.run(args, input=data, check=True, capture_output=True).stdout


def queue():
    return [json.loads(line) for line in command('postqueue', '-j').splitlines()]


with tempfile.TemporaryDirectory(prefix='turnkey-guard-') as temp:
    mail.STATE_DIR = Path(temp)
    subprocess.run(['postfix', 'stop'], capture_output=True)
    command('postsuper', '-d', 'ALL')
    command('postconf', '-e', 'relayhost=', 'defer_transports=',
            'sender_canonical_maps=', 'sender_canonical_classes=envelope_sender',
            'smtp_header_checks=', 'myhostname=appliance.fixture.invalid',
            'inet_interfaces=loopback-only')
    # Pick up requeued mail promptly in this isolated fixture.
    command('postconf', '-M', 'pickup/unix=pickup unix n - n 1 1 pickup')
    command('postfix', 'start')
    client = mail.Mail()
    client.guard()
    for index, sender in enumerate(('root@localhost', 'www-data@wordpress')):
        recipient = 'recipient%d@fixture.invalid' % index
        command('/usr/sbin/sendmail', '-i', '-f', sender, '--', recipient,
                data=('From: App <%s>\nTo: %s\n'
                      'Reply-To: support@outside.invalid\n'
                      'Subject: Queued before verification %d\n\n'
                      'Guard regression %d.\n' % (sender, recipient, index, index)).encode())
    deadline = time.monotonic() + 10
    while len(queue()) != 2 and time.monotonic() < deadline:
        time.sleep(.1)
    before = queue()
    assert len(before) == 2 and {row['sender'] for row in before} == {'root@localhost', 'www-data@wordpress'}, before
    config = mail.checked_config({
        'version': 1, 'device_id': 'a' * 32, 'status': 'ready',
        'domain': 'sender.fixture.invalid', 'sender': 'noreply@sender.fixture.invalid',
        'smtp': {'host': 'localhost', 'port': 465, 'username': 'fixture'}}, 'fixture')
    client.apply(config, verify=False)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        after = queue()
        if len(after) == 2 and all(row['sender'] == config['sender'] for row in after):
            break
        time.sleep(.1)
    else:
        raise AssertionError('Queued sender was not rewritten: %r' % after)
    recipients = sorted(r['address'] for row in after for r in row['recipients'])
    assert recipients == ['recipient0@fixture.invalid', 'recipient1@fixture.invalid'], after
    for row in after:
        contents = command('postcat', '-q', row['queue_id'])
        index = recipients.index(row['recipients'][0]['address'])
        assert b'Reply-To: support@outside.invalid' in contents, contents
        assert ('Guard regression %d.' % index).encode() in contents, contents
    assert 'smtp' not in mail.cf_entries((mail.POSTFIX_DIR / 'main.cf').read_text()).get('defer_transports', '').split()
    assert 'guard' not in client.state
print('Real Postfix queued-before-activation sender, recipient and Reply-To: PASS')
