#!/usr/bin/python3
# Copyright (c) 2011 Alon Swartz <alon@turnkeylinux.org>
"""Initialize independent Hub services (app email, DNS and backups)

Options:
    --apikey=    legacy unattended backup and DNS initialization
    --fqdn=      hostname for legacy unattended DNS initialization
"""

import sys
import getopt
import json
import os
import signal
import shutil
import subprocess
import time
from typing import NoReturn

from dialog import DialogError
from libinithooks.dialog_wrapper import Dialog
from libinithooks import hub_push, onboarding
from libinithooks.interrupt import dialog_interrupt

def usage(msg: str | getopt.GetoptError = "") -> NoReturn:
    if msg:
        print(f"Error: {msg}", file=sys.stderr)
    print(f"Syntax: {sys.argv[0]} [options]", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)


MAIL_HELPER = os.path.join(os.path.dirname(__file__), "turnkey-hub")


def mail_call(*arguments):
    try:
        result = subprocess.run([MAIL_HELPER, *arguments, "--json"],
                                capture_output=True, text=True, timeout=90)
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or (result.returncode and data.get("status") != "error"):
            raise ValueError()
        return data
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"status": "error", "message": "The Hub connection could not finish. Try again."}


def interactive_mail_call(d, *arguments):
    """Confirm Ctrl-C during connection preparation and retry when declined."""
    while True:
        with dialog_interrupt() as interrupted:
            result = mail_call(*arguments)
        if not interrupted[0]:
            return result
        if d.yesno('Quit setup', 'Do you really want to quit?'):
            raise SystemExit(130)
        d.infobox('Connecting to TurnKey Hub...')


def connect_mail(d, flow_kind="firstboot", progress_shown=False):
    try:
        return _connect_mail(d, flow_kind, progress_shown)
    except KeyboardInterrupt:
        print("Hub setup interrupted. Run turnkey-init to continue setup.", file=sys.stderr)
        raise SystemExit(130)
    except DialogError:
        onboarding.observe("error", error_code="link_dialog_failed")
        onboarding.observe("skipped")
        print("Hub setup was skipped. Run sudo turnkey connect later.", file=sys.stderr)


def _connect_mail(d, flow_kind="firstboot", progress_shown=False):
    if not progress_shown:
        d.infobox("Connecting to TurnKey Hub...")
    onboarding.start_flow(flow_kind)
    onboarding.observe("link_started")
    result = interactive_mail_call(d, "connect", "--start")
    if result.get("status") == "error":
        onboarding.observe("error", error_code="link_start_failed")
        onboarding.observe("skipped")
        d.msgbox("Hub connection skipped", "Could not connect to the Hub.\n\nTo connect later, run sudo turnkey connect.")
        return
    pairing = result
    pending = False
    new_code = False
    expires_at = time.monotonic() + pairing.get("expires_in", 900)
    onboarding.observe("prompt_shown")
    notifications = hub_push.Notifications.open(pairing['user_code'])
    try:
        while True:
            width = min(60, shutil.get_terminal_size((80, 24)).columns - 4)
            instructions = onboarding.link_instructions(pairing, pending, width)
            if new_code:
                instructions += '\n\nThe previous code expired. Enter this new code.'
            action = hub_push.confirm(d, instructions,
                                      notifications, relink=flow_kind == 'confconsole',
                                      pending=pending, expires_at=expires_at,
                                      poll=lambda: mail_call('connect', '--poll'))
            if action == 'skip':
                onboarding.observe('skipped')
                return
            if action == 'escape':
                if d.yesno('Quit setup', 'Do you really want to quit?'):
                    raise SystemExit(130)
                continue
            if action == 'error':
                onboarding.observe('error', error_code='link_dialog_failed')
                onboarding.observe('skipped')
                return
            if action == 'subscribed':
                continue
            if action == 'code_submitted':
                pending = True
                continue
            if action == 'check':
                onboarding.observe('approval_checked')
            if action == 'approved':
                d.infobox('Approved. Connecting to Hub...')
            elif pending and action == 'check':
                d.infobox('Checking Hub connection...')
            if isinstance(action, tuple) and action[0] == 'poll_result':
                result = action[1]
            else:
                arguments = ('connect', '--poll', '--notified') if action in hub_push.TERMINAL else ('connect', '--poll')
                result = mail_call(*arguments)
            if result.get('connection_status') == 'linked' or result.get('domain'):
                onboarding.observe('link_completed')
                message = 'CONNECTED: Manage email, DNS and backups in the Hub.'
                if result.get('dns_setup_error'):
                    message += '\n\n' + result['dns_setup_error']
                d.msgbox('Hub linked', message)
                return
            status = result.get('status')
            if status == 'pending':
                pending = pending or action == 'check'
            elif status == 'expired':
                onboarding.observe('error', error_code='expired')
                if notifications:
                    notifications.close()
                    notifications = None
                d.infobox('Connecting to TurnKey Hub...')
                result = interactive_mail_call(d, 'connect', '--start')
                if result.get('status') == 'error':
                    onboarding.observe('error', error_code='link_start_failed')
                    onboarding.observe('skipped')
                    d.msgbox('Hub connection skipped', 'Could not connect to the Hub.\n\nTo connect later, run sudo turnkey connect.')
                    return
                pairing = result
                expires_at = time.monotonic() + pairing.get('expires_in', 900)
                new_code = True
                pending = False
                notifications = hub_push.Notifications.open(pairing['user_code'])
            else:
                onboarding.observe('error', error_code=status if status == 'denied' else 'link_check_failed')
                onboarding.observe('skipped')
                message = 'The connection was not approved.' if status == 'denied' else 'Could not connect to the Hub.'
                d.msgbox('Hub connection skipped', message + '\n\nTo connect later, run sudo turnkey connect.')
                return
    finally:
        if notifications:
            notifications.close()


def early(unattended=False, progress=None, interactive_call=None):
    """Run before application prompts, without a network call on local no-op paths."""
    token = sys.stdin.read(256).strip() if not sys.stdin.isatty() else ""
    if token:
        onboarding.observe("link_started")
        try:
            result = subprocess.run([MAIL_HELPER, "enroll", "--token-stdin", "--json"],
                                    input=token, capture_output=True, text=True, timeout=90)
            current = json.loads(result.stdout)
            if not isinstance(current, dict):
                current = {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            current = {}
        if current.get("connection_status") == "linked":
            onboarding.observe("link_completed")
        else:
            onboarding.observe("error", error_code="automatic_enrollment_pending")
        return True
    if unattended or onboarding.decision() == "skipped":
        return True
    if progress is not None:
        progress()
    current = (interactive_call or mail_call)("status", "--local")
    if current.get("connection_status") == "linked":
        onboarding.observe("link_completed")
        return True
    return False


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        opts, _ = getopt.gnu_getopt(
            sys.argv[1:],
            "h",
            ["help", "apikey=", "fqdn=", "early", "unattended", "relink", "setup"],
        )
    except getopt.GetoptError as e:
        usage(e)

    early_mode = unattended = relink = setup = False
    apikey = ""
    fqdn = ""
    for opt, val in opts:
        if opt in ("-h", "--help"):
            usage()
        elif opt == "--setup":
            setup = relink = True
        elif opt == "--relink":
            relink = True
        elif opt == "--early":
            early_mode = True
        elif opt == "--unattended":
            unattended = True
        elif opt == "--apikey":
            apikey = val
        elif opt == "--fqdn":
            fqdn = val

    early_dialog = None
    if early_mode:
        def show_progress():
            nonlocal early_dialog
            early_dialog = Dialog("TurnKey GNU/Linux - First boot configuration",
                                  width=min(60, shutil.get_terminal_size((80, 24)).columns - 4),
                                  height=min(18, shutil.get_terminal_size((80, 24)).lines - 2))
            early_dialog.infobox("Connecting to TurnKey Hub...")

        try:
            if early(unattended, show_progress,
                     lambda *args: interactive_mail_call(early_dialog, *args)):
                return
        except DialogError:
            onboarding.observe("error", error_code="link_dialog_failed")
            onboarding.observe("skipped")
            print("Hub setup was skipped. Run sudo turnkey connect later.", file=sys.stderr)
            return

    if apikey:
        # Backup and DNS are independent. Failure in one must not skip the other.
        errors = []
        tklbam_init = subprocess.run(
            ["tklbam-init", apikey], stderr=subprocess.PIPE, text=True,
        )
        already_initialized = (
            tklbam_init.returncode == 1
            and tklbam_init.stderr.strip() == "error: already initialized"
        )
        if tklbam_init.returncode and not already_initialized:
            print(tklbam_init.stderr.replace(apikey, "[redacted]"), file=sys.stderr, end="")
            errors.append("TKLBAM initialization failed")
        if fqdn:
            hubdns_init = subprocess.run(
                ["hubdns-init", apikey, fqdn], stderr=subprocess.PIPE, text=True,
            )
            if hubdns_init.returncode:
                print(hubdns_init.stderr.replace(apikey, "[redacted]"), file=sys.stderr, end="")
                errors.append("HubDNS initialization failed")
            else:
                update = subprocess.run(["hubdns-update"], stderr=subprocess.PIPE, text=True)
                if update.returncode:
                    errors.append("HubDNS update failed")
        if errors:
            sys.exit("; ".join(errors) + ". Other Hub services were attempted independently.")
        return

    if setup:
        if os.geteuid() != 0:
            sys.exit("Run: sudo turnkey-hub")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            sys.exit("Run turnkey-hub in an interactive terminal to connect.")
        if mail_call("status", "--local").get("connection_status") == "linked":
            print("Connected to TurnKey Hub.")
            return

    d = early_dialog or Dialog("TurnKey GNU/Linux - Hub services" if relink else "TurnKey GNU/Linux - First boot configuration",
               width=min(60, shutil.get_terminal_size((80, 24)).columns - 4), height=min(18, shutil.get_terminal_size((80, 24)).lines - 2))
    connect_mail(d, "confconsole" if relink else "firstboot", progress_shown=early_dialog is not None)



if __name__ == "__main__":
    main()
