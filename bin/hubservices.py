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

def usage(msg: str | getopt.GetoptError = "") -> NoReturn:
    if msg:
        print(f"Error: {msg}", file=sys.stderr)
    print(f"Syntax: {sys.argv[0]} [options]", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)


MAIL_HELPER = os.path.join(os.path.dirname(__file__), "turnkey-mail")


def mail_call(*arguments):
    try:
        result = subprocess.run([MAIL_HELPER, *arguments, "--json"],
                                capture_output=True, text=True, timeout=90)
        data = json.loads(result.stdout)
        if result.returncode or not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"status": "error", "message": "The Hub connection could not finish. Try again from Confconsole's App email menu."}


def connect_mail(d):
    result = mail_call("connect", "--start")
    if result.get("status") == "error":
        d.error(result["message"])
        return
    message = (
        "Open this address on your phone or computer:\n\n%s\n\n"
        "Link code: %s\n\nSign in or create a free Hub account and "
        "enter this code to link your appliance. Then select Check connection."
    ) % (result["verification_uri"].split("?", 1)[0], result["user_code"])
    while d.yesno("Link to TurnKey Hub", message, "Check connection", "Skip"):
        result = mail_call("connect", "--poll")
        if result.get("domain"):
            state = ("Your appliance is linked. App email and dynamic DNS are configured." if result["status"] == "ready" else
                     "Mail is connected and waiting for domain verification. Messages will queue until it is ready.")
            d.msgbox("Hub linked", "%s\n\nHostname: %s\n\nManage the connection at https://hub.turnkeylinux.org/email/" % (state, result["domain"]))
            return
        status = result.get("status")
        if status == "pending":
            message = "Waiting for approval in your browser.\n\n" + message.split("Waiting for approval in your browser.\n\n")[-1]
        else:
            d.error(result.get("message") or "This connection request %s. Start a new connection in Confconsole." % status)
            return


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        opts, _ = getopt.gnu_getopt(
            sys.argv[1:],
            "h",
            ["help", "apikey=", "fqdn="],
        )
    except getopt.GetoptError as e:
        usage(e)

    apikey = ""
    fqdn = ""
    for opt, val in opts:
        if opt in ("-h", "--help"):
            usage()
        elif opt == "--apikey":
            apikey = val
        elif opt == "--fqdn":
            fqdn = val

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

    d = Dialog("TurnKey GNU/Linux - First boot configuration",
               width=min(60, shutil.get_terminal_size((80, 24)).columns - 4), height=min(18, shutil.get_terminal_size((80, 24)).lines - 2))
    choice = d.menu(
        "Link to TurnKey Hub",
        "A free Hub account includes a tklapp.com hostname, dynamic DNS and "
        "app email delivery. Sign in or create an account in your browser. "
        "No card or AWS account is needed for these services.",
        [("Link", "Free app email and dynamic DNS"),
         ("Skip", "Set up later in Confconsole")],
    )
    if choice == "Skip" or not choice:
        return
    if choice == "Link":
        connect_mail(d)
        return



if __name__ == "__main__":
    main()
