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
from libinithooks import onboarding

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
        if not d.yesno("Link to TurnKey Hub",
                "App email delivery, dynamic DNS, linked backups and migration, and "
                "security announcements are available through the Hub.\n\n"
                + result["message"] + "\n\nSkip leaves these Hub services unconfigured.",
                "Retry", "Skip"):
            onboarding.observe("skipped")
            return
        result = mail_call("connect", "--start")
    pairing = result
    pending = False
    onboarding.observe("prompt_shown")
    while True:
        if not d.yesno("Link to TurnKey Hub", onboarding.link_instructions(pairing, pending),
                       "I've entered the code", "Skip"):
            onboarding.observe("skipped")
            return
        onboarding.observe("approval_checked")
        result = mail_call("connect", "--poll")
        if result.get("connection_status") == "linked" or result.get("domain"):
            onboarding.observe("link_completed")
            d.msgbox("Hub linked", "Your appliance is linked to your Hub account.\n\n"
                     "Manage app email, dynamic DNS, backups and migration in the Hub.")
            return
        status = result.get("status")
        if status == "pending":
            pending = True
        else:
            onboarding.observe("error", error_code=status if status in ("denied", "expired") else "link_check_failed")
            d.error(result.get("message") or "This connection request %s. Run setup again to reconnect." % status)
            raise SystemExit(1)


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
            ["help", "apikey=", "fqdn=", "early", "unattended", "relink"],
        )
    except getopt.GetoptError as e:
        usage(e)

    early_mode = unattended = relink = False
    apikey = ""
    fqdn = ""
    for opt, val in opts:
        if opt in ("-h", "--help"):
            usage()
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

    d = Dialog("TurnKey GNU/Linux - Hub services" if relink else "TurnKey GNU/Linux - First boot configuration",
               width=min(60, shutil.get_terminal_size((80, 24)).columns - 4), height=min(18, shutil.get_terminal_size((80, 24)).lines - 2))
    connect_mail(d, "confconsole" if relink else "firstboot")



if __name__ == "__main__":
    main()
