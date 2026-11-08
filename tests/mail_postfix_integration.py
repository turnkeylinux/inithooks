#!/usr/bin/env python3
"""Destructive integration check, only for the isolated disposable mail client.

Usage: python3 mail_postfix_integration.py /qa/turnkey-hub /root/client.json
The fixture needs Postfix, SMTP SASL modules and the relay CA in system trust.
The credential file is root-only and never printed. Uses real Postfix config,
maps, reload and queue; systemd scheduling is covered by the ordinary suite.
"""
import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from unittest.mock import MagicMock, Mock, patch

if os.environ.get('TURNKEY_MAIL_DISPOSABLE_FIXTURE') != 'yes':
    raise SystemExit('Only run inside the disposable Postfix client fixture.')
loader = importlib.machinery.SourceFileLoader('mail', sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
mail = importlib.util.module_from_spec(spec)
loader.exec_module(mail)
raw = json.loads(Path(sys.argv[2]).read_text())
password = raw['smtp'].pop('password')
config = mail.checked_config(raw, password)
config['dns_password'] = 'fixture-independent-dns-secret'
config['dyndns'] = {'hostname': config['domain'], 'update_uri': mail.HUB + '/api/appmail/dns/update'}
# Reset this disposable fixture after an interrupted previous run.
command = mail.run
mail.run = lambda argv, *args, **kwargs: b'' if argv[0] == 'systemctl' else command(argv, *args, **kwargs)
with mail.locked():
    previous = mail.Mail()
    if previous.state:
        previous.reset()
mail.run = command
subprocess.run(['postsuper', '-d', 'ALL'], check=True, capture_output=True)
subprocess.run(['postfix', 'stop'], capture_output=True)
subprocess.run(['postconf', '-e', 'myhostname=appliance.client.tklapp.com',
                'inet_interfaces=loopback-only', 'smtp_host_lookup=native', 'maillog_file=/var/log/client-mail.log'], check=True)
master = Path('/etc/postfix/master.cf')
master.write_text('\n'.join('# ' + line if line.startswith('smtp ') and 'inet' in line else line
                            for line in master.read_text().splitlines()) + '\n')
subprocess.run(['postfix', 'start'], check=True)
with mail.locked():
    client = mail.Mail()
    baseline = mail.cf_entries((mail.POSTFIX_DIR / 'main.cf').read_text())
    client.apply(config)
    active = (mail.POSTFIX_DIR / 'main.cf').read_bytes()
    for filename in client.state['owned_files']:
        info = Path(filename).stat()
        assert stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == 0
    settings = mail.cf_entries(active.decode())
    assert settings['smtp_tls_security_level'] == 'secure'
    assert settings['sender_canonical_classes'] == 'envelope_sender'
    assert 'recipient_canonical_maps' not in settings
    broken = copy.deepcopy(config)
    broken['password'] = 'incorrect-fixture-password'
    try:
        client.apply(broken)
    except mail.Error:
        pass
    else:
        raise AssertionError('Bad credentials were accepted')
    assert (mail.POSTFIX_DIR / 'main.cf').read_bytes() == active
    command = mail.run
    failures = [False]
    def fail_once(argv, *args, **kwargs):
        if argv == ['postfix', 'reload'] and not failures[0]:
            failures[0] = True
            raise mail.Error('fixture reload failure')
        return command(argv, *args, **kwargs)
    mail.run = fail_once
    try:
        try:
            client.apply(config)
        except mail.Error:
            pass
        else:
            raise AssertionError('Reload failure was hidden')
    finally:
        mail.run = command
    assert failures[0] and (mail.POSTFIX_DIR / 'main.cf').read_bytes() == active
    assert all(Path(name).exists() for name in client.state['owned_files'])
    assert client.test('recipient@example.net')['status'] == 'queued'
    message = (b'From: Fixture App <root@localhost>\nTo: recipient@example.net\n'
               b'Cc: another@example.net\nReply-To: support@outside.example\n'
               b'Subject: Shared helper integration\n\nGeneric app mail fixture.\n')
    mail.run(['/usr/sbin/sendmail', '-i', '-f', 'root@localhost', '--',
              'recipient@example.net', 'another@example.net', 'hidden@example.net'], message)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        queue = mail.run(['postqueue', '-p']).decode()
        if queue.strip() == 'Mail queue is empty':
            break
        time.sleep(.25)
    else:
        raise AssertionError('Real mail queue did not drain; inspect fixture log')
    log = Path('/var/log/client-mail.log').read_text()
    assert 'status=sent' in log and password not in log
    # The container has no systemd; exercise real reset config/reload/map removal
    # while stubbing only its service-manager call.
    mail.run = lambda argv, *args, **kwargs: b'' if argv[0] == 'systemctl' else command(argv, *args, **kwargs)
    client.timers(dns=True)
    custom = mail.checked_config({'smtp': config['smtp']}, password, custom=True)
    client.apply(custom)
    assert client.state['active']['mode'] == 'custom'
    assert client.state['link']['dns_password'] == config['dns_password']
    client.reset_mail()
    assert 'active' not in client.state
    assert (mail.SYSTEMD_DIR / 'turnkey-mail-dns.timer').exists()
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'203.0.113.8\n'
    opener = Mock()
    opener.open.return_value = response
    with patch.object(mail.Mail, 'legacy_dns', return_value=False), \
            patch.object(mail.urllib.request, 'build_opener', return_value=opener), \
            patch.object(mail, 'request', return_value={'status': 'updated'}) as request:
        assert mail.Mail().dns_update()['status'] == 'updated'
    assert request.call_args.args == ('dns/update', {
        'username': config['smtp']['username'], 'dns_password': config['dns_password'], 'ipv4': '203.0.113.8'})
    assert mail.cf_entries((mail.POSTFIX_DIR / 'main.cf').read_text()) == baseline
    assert not list(mail.POSTFIX_DIR.glob('turnkey-mail-*'))
    client.reset()
    assert client.state == {}
    assert not (mail.SYSTEMD_DIR / 'turnkey-mail-dns.timer').exists()
print('Real Postfix apply, auth failure, rollback, map ownership, queue, independent DNS, mail-only and full reset: PASS')
