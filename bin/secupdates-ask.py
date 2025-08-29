#!/usr/bin/python3
# Copyright (c) 2010 Alon Swartz <alon@turnkeylinux.org>
"""Install security updates"""

import sys
import getopt
import signal
import logging
import subprocess
from typing import NoReturn
from libinithooks.dialog_wrapper import Dialog

TEXT = (
    "By default, this system is configured to automatically install security"
    "  updates on a daily basis:\n\n"
    "https://www.turnkeylinux.org/security-updates\n\n"
    "For maximum protection, we also recommend installing the latest security"
    " updates right now.\n\n"
    "This can take a few minutes. You need to be online."
)

CONNECTIVITY_ERROR = (
    "Unable to connect to package archive.\n\n"
    "Please try again once your network settings are configured by using the"
    " following shell command:\n\n"
    "    turnkey-install-security-updates"
)


def usage(msg: str | getopt.GetoptError = "") -> NoReturn:
    if msg:
        print(f"Error: {msg}", file=sys.stderr)
    print(f"Syntax: {sys.argv[0]} [options]", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        opts, _ = getopt.gnu_getopt(sys.argv[1:], "h", ["help"])
    except getopt.GetoptError as e:
        usage(e)

    for opt, _ in opts:
        if opt in ("-h", "--help"):
            usage()

    d = Dialog("TurnKey GNU/Linux - First boot configuration")
    install = d.yesno("Security updates", TEXT, "Install", "Skip")
    logging.debug(f"secupdates.main()\n\tinstall:`{install}'\n")
    if not install:
        sys.exit(1)

    try:
        subprocess.run(
            ["host", "-W", "2", "archive.turnkeylinux.org"],
            check=True,
        )
    except subprocess.CalledProcessError:
        d.error(CONNECTIVITY_ERROR)
        sys.exit(1)


if __name__ == "__main__":
    main()
