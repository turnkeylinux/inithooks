#!/usr/bin/python
"""Enable system alerts and notifications

Options:
    --email=                if not provided, will ask interactively
    --email-placeholder=    placeholder when asking interactively

"""

import os
import sys
import getopt
import signal

from dialog_wrapper import Dialog, email_re
from executil import system

TITLE = "System Notifications and Critical Security Alerts"

TEXT = """Enable local system notifications (root@localhost) to be forwarded to your regular inbox. These will include details about auto security updates and system messages.

Enabling this option will also sign you up to receive critical security and bug alerts via TurnKey's low-traffic Security and News announcements newsletter. You can unsubscribe at any time.

http://www.turnkeylinux.org/security-alerts

Email:
"""

def fatal(e):
    print >> sys.stderr, "Error:", e
    sys.exit(1)

def warn(e):
    print >> sys.stderr, "Warning:", e

def usage(s=None):
    if s:
        print >> sys.stderr, "Error:", s
    print >> sys.stderr, "Syntax: %s [options]" % sys.argv[0]
    print >> sys.stderr, __doc__
    sys.exit(1)

def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        l_opts = ["help", "email=", "email-placeholder="]
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h", l_opts)
    except getopt.GetoptError, e:
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

    if email and not email_re.match(email):
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

            if retcode == 1:
                email = ""
                break

            if not email_re.match(email):
                d.error('Email is not valid')
                continue

            if d.yesno("Is your email correct?", email):
                break

    if email:
        cmd = os.path.join(os.path.dirname(__file__), 'secalerts.sh')
        system(cmd, email)


if __name__ == "__main__":
    main()

