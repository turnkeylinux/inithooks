#!/usr/bin/python3
# Copyright (c) 2010 Alon Swartz <alon@turnkeylinux.org>
"""Set account password

Arguments:
    username      username of account to set password for

Options:
    -p --pass=    if not provided, will ask interactively
"""

import sys
import getopt
import subprocess
from subprocess import PIPE
import signal

def fatal(s):
    print("Error:", s, file=sys.stderr)
    sys.exit(1)

def usage(s=None):
    if s:
        print("Error:", s, file=sys.stderr)
    print("Syntax: %s <username> [options]" % sys.argv[0], file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)

def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "hp:", ['help', 'pass='])
    except getopt.GetoptError as e:
        usage(e)

    if len(args) != 1:
        usage()

    username = args[0]
    password = ""
    for opt, val in opts:
        if opt in ('-h', '--help'):
            usage()
        elif opt in ('-p', '--pass'):
            password = val

    if not password:
        from dialog_wrapper import Dialog
        d = Dialog('TurnKey GNU/Linux - First boot configuration')
        password = d.get_password(
            "%s Password" % username.capitalize(),
            "Please enter new password for the %s account." % username)


    command = ["chpasswd"]
    input = ":".join([username, password])

    p = subprocess.Popen(command, stdin=PIPE, shell=False)
    p.stdin.write(input)
    p.stdin.close()
    err = p.wait()
    if err:
        fatal(err)

if __name__ == "__main__":
    main()

