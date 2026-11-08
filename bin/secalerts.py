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
    "Forward local system notifications (root@localhost) to your inbox. "
    "These include security update reports and system messages.\n\nEmail:"
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

    if not email:
        email = onboarding.email_default()

    if not email:
        d = Dialog("TurnKey Linux - First boot configuration")
        email = onboarding.email_default(email_placeholder)
        while 1:
            retcode, email = d.inputbox(TITLE, TEXT, email, "Enable", "Skip")

            if retcode == "cancel":
                email = ""
                onboarding.notification_choice("SKIP")
                break

            if not EMAIL_RE.match(email):
                d.error("Email is not valid")
                continue

            if d.yesno("Is your email correct?", email):
                break

    if email:
        onboarding.notification_choice(email)
        onboarding.observe("email_provided", email=email)
        cmd = os.path.join(os.path.dirname(__file__), "secalerts.sh")
        logging.debug(f"\tcmd:`{cmd}'")
        configure([cmd, email])

    # Hub-linked users manage announcements in their account. Only a deliberate
    # firstboot Skip offers the separate newsletter, with affirmative consent.
    if onboarding.skipped_unlinked():
        d = Dialog("TurnKey Linux - First boot configuration")
        newsletter_email = email or email_placeholder
        while True:
            status, newsletter_email = d.inputbox("Security announcements",
                "Optional: receive TurnKey security announcements and news. "
                "This is a low-traffic newsletter; unsubscribe at any time.\n\nEmail:",
                newsletter_email, "Subscribe", "No thanks")
            if status == "cancel":
                onboarding.observe("newsletter_choice", newsletter_consent=False)
                break
            newsletter_email = newsletter_email.strip()
            if not onboarding.valid_email(newsletter_email):
                d.error("Enter a valid email address, or choose No thanks.")
                continue
            onboarding.observe("newsletter_choice", newsletter_consent=True)
            onboarding.observe("email_provided", email=newsletter_email)
            configure([os.path.join(os.path.dirname(__file__), "secalerts.sh"),
                       newsletter_email, "--newsletter-only"])
            break


if __name__ == "__main__":
    main()
