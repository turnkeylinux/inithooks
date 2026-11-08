"""Register a capable appliance through the published native Hub client."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

PROTOCOL = 'hub-services-v1'
STATE_DIR = Path('/var/lib/turnkey-mail')
FAILURE = 'Hub registration could not finish. Check the network and Hub packages, then retry.'


@contextmanager
def registration_lock():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    with (STATE_DIR / 'registration.lock').open('a') as lock:
        os.chmod(STATE_DIR / 'registration.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def registration_nonce(server_id):
    """Commit the retry identity before any registration request can escape."""
    path = STATE_DIR / 'registration.json'
    try:
        saved = json.loads(path.read_text())
    except FileNotFoundError:
        pass
    else:
        if (not isinstance(saved, dict) or not isinstance(saved.get('server_id'), str)
                or not saved['server_id']
                or not isinstance(saved.get('nonce'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', saved['nonce'])):
            raise ValueError('Invalid registration receipt')
        if saved['server_id'] == server_id:
            os.chmod(path, 0o600)
            return saved['nonce']
    nonce = secrets.token_hex(32)
    fd, temporary = tempfile.mkstemp(prefix='.registration-', dir=STATE_DIR)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'server_id': server_id, 'nonce': nonce}, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return nonce


def register():
    if os.geteuid() != 0:
        raise PermissionError('Registration requires root')
    from hubclient_lib.conf import HubServerConf
    from hubclient_lib.api import Server
    with registration_lock():
        conf = HubServerConf()
        conf.validate_required(['serverid'])
        server_id = conf.serverid
        if not isinstance(server_id, str) or not server_id:
            raise ValueError('Missing server ID')
        nonce = registration_nonce(server_id)
        server = Server()
        response = server.api.request('POST', server.API_URL + 'register/finalize/',
            {'serverid': server_id, 'capability': PROTOCOL, 'registration_nonce': nonce})
        if not isinstance(response, dict) or any(
                not isinstance(response.get(key), str) or not response[key]
                for key in ('subkey', 'secret')):
            raise ValueError('Invalid registration response')
        # Retain the native file format, unrelated fields and persistence API.
        conf.update({'subkey': response['subkey'], 'secret': response['secret']})
        conf.write()


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        register()
    except KeyboardInterrupt:
        print('Hub registration interrupted. Retry to finish with the saved registration.', file=sys.stderr)
        return 130
    except Exception:
        # Native transport exceptions may contain HTTP bodies or credentials.
        print(FAILURE, file=sys.stderr)
        return 1
    return 0
