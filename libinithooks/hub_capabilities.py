"""Offline readiness of the installed Hub integration, without saved state."""
import argparse
import importlib
import importlib.machinery
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import sys

PROTOCOL = 'hub-services-v1'
HELPER = Path('/usr/lib/inithooks/bin/turnkey-hub')
MODULES = {
    'libinithooks.hub_launch': ('register', 'registration_nonce'),
    'libinithooks.onboarding': ('begin', 'reconcile', 'start_flow', 'flush', 'email_default'),
    'libinithooks.hub_push': ('confirm', 'Notifications', 'validated_notification'),
    'libinithooks.hub_runtime': ('schedule', 'start_observations'),
    'libinithooks.hub_restore': ('restore_mail',),
    'libinithooks.dialog_wrapper': ('Dialog',),
    'hubclient_lib.conf': ('HubServerConf.validate_required', 'HubServerConf.update', 'HubServerConf.write'),
    'hubclient_lib.api': ('Server', 'API.request'),
    'hubclient_lib.messages': ('wrapper_callback',),
    'websocket': ('create_connection',),
    'yaml': ('safe_load',),
    'systemd.journal': ('JournalHandler',),
    'netinfo': (),
}
COMMANDS = ('postfix', 'postconf', 'postmap', 'sendmail', 'newaliases', 'dialog',
            'confconsole', 'tklbam-init', 'tklbam-backup', 'tklbam-restore',
            'hubclient-register-finalize', 'hubclient-get-messages')
EXECUTABLES = tuple('/usr/lib/inithooks/' + name for name in (
    'bin/turnkey-hub', 'bin/turnkey', 'bin/hubservices.py', 'bin/turnkey-onboarding',
    'bin/secalerts.sh', 'firstboot.d/00setup-observation', 'firstboot.d/30hub-link',
    'firstboot.d/80hub-services', 'firstboot.d/85secalerts', 'firstboot.d/98finalize',
)) + ('/etc/tklbam/hooks.d/turnkey-hub-restore',)
FILES = ('/usr/lib/confconsole/plugins.d/Hub_services/hub_services.py',
         '/lib/systemd/system/turnkey-onboarding.service',
         '/lib/systemd/system/turnkey-onboarding.timer',
         '/etc/ssl/certs/ca-certificates.crt')


def has_functions(module, names):
    for name in names:
        value = module
        for attribute in name.split('.'):
            value = getattr(value, attribute, None)
        if not callable(value):
            return False
    return True


def probe():
    # Imported on a clean filesystem during firstboot: imports must not create
    # __pycache__ even when no packaged bytecode has been generated yet.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    # dialog_wrapper configures a file logger at import. A standard handler
    # prevents basicConfig from opening that file in this read-only command.
    logger, handler = logging.getLogger(), logging.NullHandler()
    logger.addHandler(handler)
    missing = []
    try:
        for name, functions in MODULES.items():
            try:
                module = importlib.import_module(name)
                if not has_functions(module, functions):
                    missing.append(name)
                elif name == 'libinithooks.hub_launch' and getattr(module, 'PROTOCOL', None) != PROTOCOL:
                    missing.append(name)
                elif name == 'hubclient_lib.api' and not isinstance(getattr(module.Server, 'API_URL', None), str):
                    missing.append(name)
            except Exception:
                missing.append(name)
        try:
            loader = importlib.machinery.SourceFileLoader('_hub_capability_helper', str(HELPER))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            helper = importlib.util.module_from_spec(spec)
            loader.exec_module(helper)
            if not has_functions(helper, ('Mail.enroll', 'Mail.retry', 'Mail.reset',
                                          'Mail.poll', 'Mail.dns_update')):
                missing.append(str(HELPER))
        except Exception:
            missing.append(str(HELPER))
        missing.extend(name for name in COMMANDS if not shutil.which(name))
        missing.extend(name for name in EXECUTABLES if not os.access(name, os.X_OK))
        missing.extend(name for name in FILES if not Path(name).is_file())
        if not (list(Path('/usr/lib').glob('*/sasl2/libplain.so*'))
                or list(Path('/usr/lib/sasl2').glob('libplain.so*'))):
            missing.append('libsasl2-modules')
    finally:
        logger.removeHandler(handler)
        handler.close()
        sys.dont_write_bytecode = previous
    return {'protocol': PROTOCOL, 'ready': not missing, 'missing': sorted(set(missing))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.parse_args(argv)
    result = probe()
    print(json.dumps(result, sort_keys=True))
    return 0 if result['ready'] else 1
