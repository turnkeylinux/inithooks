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

from dialog_wrapper import Dialog, EMAIL_RE, dia_log
import subprocess

TITLE = "System Notifications and Critical Security Alerts"

TEXT = ("Enable local system notifications (root@localhost) to be forwarded"
        " to your regular inbox. Notifications include security updates and"
        " system messages.\n\n"
        "You will also be subscribed to receive critical security and bug"
        " alerts through a low-traffic Security and News announcements"
        " newsletter. You can unsubscribe at any time.\n\n"
        "https://www.turnkeylinux.org/security-alerts\n\n"
        "Email:")


def fatal(e):
    print("Error:", e, file=sys.stderr)
    sys.exit(1)


def warn(e):
    print("Warning:", e, file=sys.stderr)


def usage(s=None):
    if s:
        print("Error:", s, file=sys.stderr)
    print("Syntax: %s [options]" % sys.argv[0], file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        l_opts = ["help", "email=", "email-placeholder="]
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h", l_opts)
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

            dia_log(("secalerts.main():\n\tretcode:`{}'\n\temail:`{}'"
                    ).format(retcode, email))
            if retcode == 'cancel':
                email = ""
                break

            if not EMAIL_RE.match(email):
                d.error('Email is not valid')
                continue

            if d.yesno("Is your email correct?", email):
                break

    if email:
        cmd = os.path.join(os.path.dirname(__file__), 'secalerts.sh')
        dia_log("\tcmd:`{}'".format(cmd))
        subprocess.run([cmd, email], check=True)


if __name__ == "__main__":
    main()
