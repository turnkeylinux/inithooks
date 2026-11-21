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
from libinithooks import onboarding

TITLE = "System Notifications and Critical Security Alerts"

TEXT = (
    "Receive system notifications, including security update reports and "
    "system messages, in your inbox.\n\nYour Email:"
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


def configure(command):
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=45)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        message = "Notification setup could not finish. Check the local mail service, then run setup again."
        if sys.stdin.isatty():
            Dialog("TurnKey Linux - First boot configuration").error(message)
            raise SystemExit(1)
        fatal(message)


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

    reconfigure = bool(os.environ.get("_TURNKEY_RECONFIGURE"))
    offer_newsletter = not reconfigure and onboarding.skipped_unlinked()
    if reconfigure:
        current = onboarding.load().get("notification_choice", "")
        email_placeholder = current if onboarding.valid_email(current) else onboarding.email_default(email_placeholder)
        email = ""
    elif not email:
        email = onboarding.email_default()

    if not email:
        d = Dialog("TurnKey Linux - First boot configuration")
        email = onboarding.email_default(email_placeholder)
        while 1:
            retcode, email = d.inputbox(TITLE, TEXT, email, "Enable", "Cancel" if reconfigure else "Skip")

            if retcode == "cancel":
                if reconfigure:
                    return
                email = ""
                onboarding.notification_choice("SKIP")
                break

            if not EMAIL_RE.match(email):
                d.error("Email is not valid")
                continue

            if offer_newsletter or d.yesno("Is your email correct?", email):
                break

    if email:
        cmd = os.path.join(os.path.dirname(__file__), "secalerts.sh")
        logging.debug(f"\tcmd:`{cmd}'")
        configure([cmd, email])
        onboarding.observe("email_provided", email=email)

    # Hub-linked users manage announcements in their account. Ask once for
    # the local recipient, then request separate consent for announcements.
    if offer_newsletter and email:
        d = Dialog("TurnKey Linux - First boot configuration")
        subscribe = d.yesno(
            "Security announcements",
            "Also receive TurnKey security announcements and news at:\n\n"
            f"{email}\n\nUnsubscribe at any time.",
            "Subscribe", "No thanks",
        )
        onboarding.observe("newsletter_choice", newsletter_consent=subscribe)
        if subscribe:
            configure([os.path.join(os.path.dirname(__file__), "secalerts.sh"),
                       email, "--newsletter-only"])


if __name__ == "__main__":
    main()
