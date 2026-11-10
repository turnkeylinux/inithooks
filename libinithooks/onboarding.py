"""Local firstboot decisions, email defaults and asynchronous setup observations."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import tempfile

STATE_DIR = Path('/var/lib/turnkey-onboarding')
CACHE_DIR = Path('/var/lib/inithooks/cache')
HUB_STATE = Path('/var/lib/turnkey-mail/state.json')
ALIASES = Path('/etc/aliases')
ALERTS = '/usr/lib/inithooks/bin/secalerts.sh'
ENDPOINT = 'https://ajax.turnkeylinux.org/initfence/v2/events'
EVENTS = {'setup_started', 'setup_completed', 'prompt_shown', 'link_started', 'approval_checked',
          'link_completed', 'skipped', 'error', 'email_provided', 'newsletter_choice'}
MAX_PENDING = 512


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ''


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE_DIR / 'lock').open('a') as stream:
        os.chmod(STATE_DIR / 'lock', 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def load():
    try:
        return json.loads((STATE_DIR / 'state.json').read_text())
    except FileNotFoundError:
        return {}


def save(state):
    atomic(STATE_DIR / 'state.json', json.dumps(state, sort_keys=True) + '\n')


def context():
    value = {key: None for key in ('app', 'release', 'app_version', 'image_name',
                                  'build_target', 'platform', 'architecture', 'ram_bytes', 'cpu_count')}
    version = read_text('/etc/turnkey_version')
    match = re.fullmatch(r'turnkey-(.+)-(\d[^-]*)-([^-]+)-([^-]+)', version)
    if match:
        value.update(app=match[1], release=match[2])
    architecture = platform.machine()[:200]
    value['architecture'] = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(architecture, architecture) or None
    tags = read_text('/etc/apt/apt.conf.d/01turnkey')
    build = re.search(r'\(([^)]+)\)', tags)
    if build:
        words = build[1].split()[1:]
        value['build_target'] = ' '.join(words)[:200] or 'iso'
    container = read_text('/run/systemd/container')
    if Path('/run/.containerenv').exists():
        value['platform'] = 'podman'
    elif Path('/.dockerenv').exists():
        value['platform'] = 'docker'
    elif container:
        value['platform'] = container[:200]
    else:
        value['platform'] = read_text('/sys/class/dmi/id/product_name')[:200] or None
    memory = re.search(r'^MemTotal:\s+(\d+) kB$', read_text('/proc/meminfo'), re.M)
    if memory:
        value['ram_bytes'] = int(memory[1]) * 1024
    for path in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        limit = read_text(path)
        if limit.isdigit() and int(limit) > 0:
            value['ram_bytes'] = min(value['ram_bytes'] or int(limit), int(limit))
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count()
    quota = read_text('/sys/fs/cgroup/cpu.max').split()
    if len(quota) == 2 and all(item.isdigit() for item in quota) and int(quota[1]):
        cpus = min(cpus or 65536, max(1, (int(quota[0]) + int(quota[1]) - 1) // int(quota[1])))
    value['cpu_count'] = min(cpus, 65536) if cpus else None
    try:
        metadata = json.loads(read_text('/etc/turnkey-build.json') or '{}')
        for key in ('app', 'release', 'app_version', 'image_name', 'build_target'):
            field = metadata.get(key)
            if isinstance(field, str) and 0 < len(field) <= 200 and not any(ord(c) < 32 for c in field):
                value[key] = field
    except (ValueError, TypeError, AttributeError):
        pass
    return value


def new_state():
    return {'installation_id': secrets.token_hex(16), 'flow_id': secrets.token_hex(16),
            'flow_kind': 'firstboot', 'context': context(), 'pending': [], 'decision': None}


def append(state, name, **fields):
    if name not in EVENTS:
        raise ValueError('Unknown setup event')
    if len(state['pending']) >= MAX_PENDING:
        # Preserve the denominator and existing durable observations while offline.
        return
    event = {'schema_version': 1, 'source': 'appliance', 'event_name': name,
             'installation_id': state['installation_id'], 'flow_id': state['flow_id'],
             'flow_kind': state['flow_kind'], 'event_id': secrets.token_hex(16),
             'occurred_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
             'context': state['context']}
    event.update(fields)
    state['pending'].append(event)


def begin():
    with locked():
        state = load()
        if state.get('installation_id'):
            return state
        state = new_state()
        state['firstboot_flow_id'] = state['flow_id']
        append(state, 'setup_started')
        save(state)
    from . import hub_runtime
    hub_runtime.start_observations()
    return state


def decision():
    try:
        return load().get('decision')
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def start_flow(kind='firstboot'):
    try:
        return _start_flow(kind)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        import logging
        logging.warning('Setup journey could not be saved')
        return {}


def _start_flow(kind):
    begin()
    with locked():
        state = load()
        if kind == 'firstboot':
            state.update(flow_id=state['firstboot_flow_id'], flow_kind=kind)
            save(state)
        elif kind == 'confconsole':
            state.update(flow_id=secrets.token_hex(16), flow_kind=kind, decision=None)
            save(state)
        return {key: state[key] for key in ('installation_id', 'flow_id')}


def record(name, error_code=None, newsletter_consent=None, email=None, expected_flow=None):
    begin()
    with locked():
        state = load()
        if expected_flow is not None and (state.get('decision') == 'skipped' or
                any(state.get(key) != expected_flow.get(key)
                    for key in ('installation_id', 'flow_id', 'flow_kind'))):
            return
        fields = {}
        if name == 'email_provided':
            if not skipped_unlinked(state) or not valid_email(email):
                return
            fields.update(email=email, link_decision='skipped')
        elif email is not None:
            raise ValueError('Email is only valid on its dedicated event')
        if name == 'error':
            if not error_code or not re.fullmatch(r'[a-z0-9_]{1,64}', error_code):
                raise ValueError('Invalid setup error code')
            fields['error_code'] = error_code
        if name == 'newsletter_choice':
            if not skipped_unlinked(state) or not isinstance(newsletter_consent, bool):
                return
            fields['newsletter_consent'] = newsletter_consent
        if name == 'skipped':
            state['decision'] = 'skipped'
            if state['flow_kind'] == 'firstboot':
                state['firstboot_decision'] = 'skipped'
        if name == 'link_completed':
            if state.get('linked_flow_id') == state['flow_id']:
                return state
            state['linked_flow_id'] = state['flow_id']
            state['decision'] = 'linked'
            if state['flow_kind'] == 'firstboot':
                state['firstboot_decision'] = 'linked'
        if name == 'setup_completed':
            if state.get('completed'):
                return
            state.update(completed=True, flow_id=state['firstboot_flow_id'], flow_kind='firstboot')
        append(state, name, **fields)
        save(state)
        return state


def skipped_unlinked(state=None):
    try:
        state = load() if state is None else state
    except (OSError, ValueError, TypeError):
        return False
    if state.get('decision') != 'skipped' or state.get('firstboot_decision') != 'skipped':
        return False
    try:
        return not json.loads(HUB_STATE.read_text()).get('link', {}).get('control_secret')
    except FileNotFoundError:
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def valid_email(value):
    return isinstance(value, str) and len(value) <= 254 and bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value))


def email_default(initial=''):
    # Real app-specific values and explicit preseeds remain authoritative.
    if initial and initial != 'admin@example.com':
        return initial
    value = read_text(CACHE_DIR / 'HUB_ACCOUNT_EMAIL')
    return value if valid_email(value) else initial


def observe(name, **fields):
    # Setup still works when the optional observation spool is unavailable.
    try:
        record(name, **fields)
    except (OSError, ValueError, TypeError):
        import logging
        logging.warning('Setup observation could not be saved')


def link_instructions(result, pending=False, width=60):
    content_width = max(1, width - 4)
    action = 'Approve in your browser.' if pending else 'Enter this code:'
    return (
        'Connect email delivery, DNS, backups and migration.\n'
        'Get security alerts by email.\n\n'
        'Open %s\n\n%s\n\n%s'
    ) % (result['verification_uri'].split('?', 1)[0],
         action.center(content_width), result['user_code'].center(content_width))


def remember_email(value):
    if valid_email(value):
        atomic(CACHE_DIR / 'HUB_ACCOUNT_EMAIL', value)


def notification_choice(value):
    """Keep the selected local notification policy after firstboot answers clear."""
    if value != 'SKIP' and not valid_email(value):
        return
    begin()
    with locked():
        state = load()
        state['notification_choice'] = value
        save(state)


def reconcile():
    """Converge firstboot observations and defaults after a native Hub retry."""
    try:
        state = load()
        if not state.get('installation_id') or state.get('decision') == 'skipped':
            return
        if state.get('decision') == 'linked' and (not state.get('completed') or state.get('notification_choice')):
            return
        native = json.loads(HUB_STATE.read_text())
        link = native.get('link', native.get('active', {}))
        services = native.get('services', {})
        mail = services.get('mail', {})
        if (link.get('flow') != {key: state[key] for key in ('installation_id', 'flow_id', 'flow_kind')} or
                link.get('mode') != 'hub' or not link.get('control_secret') or
                not link.get('device_id') or services.get('device_id') != link['device_id'] or
                services.get('connection_status') != 'linked' or
                any(native.get(key) for key in ('pending', 'applying', 'guard', 'error')) or mail.get('error')):
            return
        if native.get('mail_preference', 'hub') == 'hub' and mail.get('enabled'):
            active = native.get('active', {})
            if (mail.get('status') not in ('active', 'pending_verification') or
                    active.get('mode') != 'hub' or active.get('device_id') != link['device_id'] or
                    any(active.get(key) != mail.get(key) for key in ('smtp', 'sender', 'domain'))):
                return
        state = record('link_completed', expected_flow=link['flow'])
        if not state or not state.get('completed') or state.get('notification_choice'):
            return
        if re.search(r'^root:[ \t]*[^ \t\r\n#]', read_text(ALIASES), re.M):
            return
        email = email_default()
        if valid_email(email) and email == services.get('account_email'):
            subprocess.run([ALERTS, email], check=True, capture_output=True, timeout=45)
    except (OSError, ValueError, TypeError, KeyError, AttributeError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Optional local convergence retries on the existing maintenance schedule.
        return


def flush():
    reconcile()
    import urllib.error
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    # Do not hold the local state lock across network requests.
    opener = urllib.request.build_opener(NoRedirect())
    for unused in range(8):
        with locked():
            state = load()
            if not state.get('pending'):
                return
            event = state['pending'][0]
        request = urllib.request.Request(ENDPOINT, json.dumps(event).encode(),
                    headers={'Content-Type': 'application/json', 'User-Agent': 'TurnKey-Onboarding/1'}, method='POST')
        try:
            with opener.open(request, timeout=3) as response:
                acknowledged = 200 <= response.status < 300
        except urllib.error.HTTPError as error:
            acknowledged = 400 <= error.code < 500 and error.code != 429
        except (OSError, urllib.error.URLError):
            return
        if not acknowledged:
            return
        with locked():
            state = load()
            state['pending'] = [item for item in state.get('pending', [])
                                if item['event_id'] != event['event_id']]
            save(state)


def main():
    try:
        command_line()
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        import logging
        logging.warning("Setup observation storage is unavailable; setup can continue")


def command_line():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['begin', 'flow', 'event', 'flush', 'reset-installation', 'decision', 'notifications'])
    parser.add_argument('--kind', choices=['firstboot', 'confconsole'], default='firstboot')
    parser.add_argument('--name', choices=sorted(EVENTS))
    parser.add_argument('--error-code')
    parser.add_argument('--email')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run this command as root')
    if args.command == 'begin':
        begin()
    elif args.command == 'flow':
        print(json.dumps(start_flow(args.kind)))
    elif args.command == 'event':
        record(args.name, error_code=args.error_code)
    elif args.command == 'flush':
        flush()
    elif args.command == 'reset-installation':
        with locked():
            (STATE_DIR / 'state.json').unlink(missing_ok=True)
        (CACHE_DIR / 'HUB_ACCOUNT_EMAIL').unlink(missing_ok=True)
    elif args.command == 'notifications':
        notification_choice(args.email)
    elif args.command == 'decision':
        print(decision() or '')
