#!/usr/bin/python3
"""Enable system alerts and notifications

Options:
    --email=                if not provided, will ask interactively
    --email-placeholder=    placeholder when asking interactively

"""

import os
import sys
import getopt
import signal
import logging
import subprocess
from typing import NoReturn

from libinithooks.dialog_wrapper import Dialog, EMAIL_RE

TITLE = "System Notifications and Critical Security Alerts"

TEXT = (
    "Enable local system notifications (root@localhost) to be forwarded to"
    " your regular inbox. Notifications include security updates and system"
    " messages.\n\n"
    "You will also be subscribed to receive critical security and bug alerts"
    " through a low-traffic Security and News announcements newsletter. You"
    " can unsubscribe at any time.\n\n"
    "https://www.turnkeylinux.org/security-alerts\n\n"
    "Email:"
)


def fatal(msg: str) -> NoReturn:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"Warning: {msg}", file=sys.stderr)


def usage(msg: str | getopt.GetoptError = "") -> NoReturn:
    if msg:
        print(f"Error: {msg}", file=sys.stderr)
    print(f"Syntax: {sys.argv[0]} [options]", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        l_opts = ["help", "email=", "email-placeholder="]
        opts, _ = getopt.gnu_getopt(sys.argv[1:], "h", l_opts)
    except getopt.GetoptError as e:
        usage(e)

    email = ""
    email_placeholder = ""
    for opt, val in opts:
        if opt in ("-h", "--help"):
            usage()
        elif opt == "--email":
            email = val
        elif opt == "--email-placeholder":
            email_placeholder = val

    if email and not EMAIL_RE.match(email):
        fatal("email is not valid")

    if not email:
        d = Dialog("TurnKey Linux - First boot configuration")
        email = email_placeholder
        while 1:
            retcode, email = d.inputbox(
                TITLE,
                TEXT,
                email,
                "Enable",
                "Skip")

            logging.debug(
                f"secalerts.main():\n\tretcode:`{retcode}'\n\temail:`{email}'"
            )
            if retcode == "cancel":
                email = ""
                break

            if not EMAIL_RE.match(email):
                d.error("Email is not valid")
                continue

            if d.yesno("Is your email correct?", email):
                break

    if email:
        cmd = os.path.join(os.path.dirname(__file__), "secalerts.sh")
        logging.debug(f"\tcmd:`{cmd}'")
        subprocess.run([cmd, email], check=True)


if __name__ == "__main__":
    main()
