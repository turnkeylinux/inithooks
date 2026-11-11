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
from typing import NoReturn

from libinithooks.dialog_wrapper import Dialog
from libinithooks import hub_push, onboarding

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
        if result.returncode or not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"status": "error", "message": "The Hub connection could not finish. Check the network, then try again."}


def connect_mail(d, flow_kind="firstboot"):
    onboarding.start_flow(flow_kind)
    onboarding.observe("link_started")
    result = mail_call("connect", "--start")
    if result.get("status") == "error":
        onboarding.observe("prompt_shown")
    while result.get("status") == "error":
        onboarding.observe("error", error_code="link_start_failed")
        if not d.yesno("Connect to TurnKey Hub",
                result["message"],
                "Retry", "Skip"):
            onboarding.observe("skipped")
            return
        result = mail_call("connect", "--start")
    pairing = result
    pending = False
    onboarding.observe("prompt_shown")
    notifications = hub_push.Notifications.open(pairing['user_code'])
    try:
        while True:
            width = min(60, shutil.get_terminal_size((80, 24)).columns - 4)
            action = hub_push.confirm(d, onboarding.link_instructions(pairing, pending, width),
                                      notifications, relink=flow_kind == 'confconsole',
                                      pending=pending, interval=pairing.get('interval', 5))
            if action == 'skip':
                onboarding.observe('skipped')
                return
            if action == 'escape':
                if d.yesno('Quit setup', 'Do you really want to quit?'):
                    raise SystemExit(130)
                continue
            if action == 'error':
                d.error('The connection dialog could not continue. Run turnkey-hub to try again.')
                raise SystemExit(1)
            if action == 'code_submitted':
                pending = True
                continue
            if action == 'check':
                onboarding.observe('approval_checked')
            if action == 'approved':
                d.infobox('Approved. Connecting to Hub...')
            elif pending and action in ('check', 'poll'):
                d.infobox('Checking Hub connection...')
            arguments = ('connect', '--poll', '--notified') if action in hub_push.TERMINAL else ('connect', '--poll')
            result = mail_call(*arguments)
            if result.get('connection_status') == 'linked' or result.get('domain'):
                onboarding.observe('link_completed')
                message = 'Connected. Manage email delivery, DNS and backups in the Hub.'
                if result.get('dns_setup_error'):
                    message += '\n\n' + result['dns_setup_error']
                d.msgbox('Hub linked', message)
                return
            status = result.get('status')
            if status == 'pending':
                pending = pending or action == 'check'
            else:
                onboarding.observe('error', error_code=status if status in ('denied', 'expired') else 'link_check_failed')
                d.error(result.get('message') or 'This connection request %s. Run setup again to reconnect.' % status)
                raise SystemExit(1)
    finally:
        if notifications:
            notifications.close()


def early(unattended=False):
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
    current = mail_call("status", "--local")
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

    if early_mode and early(unattended):
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

    d = Dialog("TurnKey GNU/Linux - Hub services" if relink else "TurnKey GNU/Linux - First boot configuration",
               width=min(60, shutil.get_terminal_size((80, 24)).columns - 4), height=min(18, shutil.get_terminal_size((80, 24)).lines - 2))
    connect_mail(d, "confconsole" if relink else "firstboot")



if __name__ == "__main__":
    main()
