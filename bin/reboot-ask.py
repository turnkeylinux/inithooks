#!/usr/bin/python3
"""Reboot to install kernel upgrade"""

import sys
import getopt
import signal
from typing import NoReturn

from libinithooks.dialog_wrapper import Dialog

TEXT = """A security update to the kernel requires a reboot to go into effect.

For maximum protection, we recommend rebooting now.
"""


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

    d = Dialog("TurnKey GNU/Linux - Reboot after kernel update")
    reboot = d.yesno("Reboot now?", TEXT, "Reboot", "Skip")

    if not reboot:
        sys.exit(1)


if __name__ == "__main__":
    main()
