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
import signal
from typing import NoReturn


def fatal(
    msg: str | subprocess.TimeoutExpired | subprocess.CalledProcessError,
) -> NoReturn:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def usage(msg: str | getopt.GetoptError = "") -> NoReturn:
    if msg:
        print(f"Error: {msg}", file=sys.stderr)
    print(f"Syntax: {sys.argv[0]} <username> [options]", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "hp:", ["help", "pass="])
    except getopt.GetoptError as e:
        usage(e)

    if len(args) != 1:
        usage()

    username = args[0]
    password = ""
    for opt, val in opts:
        if opt in ("-h", "--help"):
            usage()
        elif opt in ("-p", "--pass"):
            password = val

    if not password:
        from libinithooks.dialog_wrapper import Dialog

        d = Dialog("TurnKey GNU/Linux - First boot configuration")
        password = d.get_password(
            f"{username.capitalize()} Password",
            f"Please enter new password for the {username} account.",
        )

    assert password
    command = ["chpasswd"]
    std_input = ":".join([username, password])

    try:
        p = subprocess.Popen(command, stdin=subprocess.PIPE, shell=False)
        p.communicate(std_input.encode(sys.stdin.encoding))
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        fatal(e)


if __name__ == "__main__":
    main()
