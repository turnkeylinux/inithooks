"""Actual firstboot widgets with isolated state and synthetic Hub responses."""
import importlib.util
import logging
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.basicConfig(stream=sys.stderr, level=logging.CRITICAL)
spec = importlib.util.spec_from_file_location('hubservices', ROOT / 'bin/hubservices.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Dialog = module.Dialog
state = tempfile.TemporaryDirectory()
module.onboarding.STATE_DIR = Path(state.name)
from libinithooks import hub_runtime
hub_runtime.start_observations = lambda: None


def dialog(*args, **kwargs):
    result = Dialog(*args, **kwargs)
    result.console.add_persistent_args(['--ascii-lines'])
    return result


module.Dialog = dialog
mode = sys.argv[1] if len(sys.argv) > 1 else 'ready'
polls = 0
starts = 0


def invoke(*args):
    global polls, starts
    if args == ('connect', '--start'):
        starts += 1
        if mode == 'start-error':
            return {'status': 'error', 'message': 'The Hub is temporarily unavailable. Try again shortly.'}
        return {'verification_uri': 'https://hub.turnkeylinux.org/connect', 'user_code': 'AAAA-BBBB' if mode == 'expired' and starts > 1 else 'D34C-F5A9'}
    polls += 1
    if mode == 'pending' and polls == 1:
        return {'status': 'pending'}
    if mode in ('expired', 'denied'):
        return {'status': mode}
    if mode == 'error':
        return {'status': 'error', 'message': 'The Hub is temporarily unavailable. Try again shortly.'}
    return {'status': 'ready', 'connection_status': 'linked'}


module.mail_call = invoke
sys.argv = ['hubservices.py']
try:
    module.main()
except SystemExit as result:
    print('EXIT', result.code)
print('EVENTS', ','.join(e['event_name'] for e in module.onboarding.load()['pending']))
print('FINISHED')
