#!/usr/bin/python3
"""Reboot to install kernel upgrade"""

import sys
import getopt
import signal

from dialog_wrapper import Dialog

TEXT = """A security update to the kernel requires a reboot to go into effect.

For maximum protection, we recommend rebooting now.
"""

def usage(s=None):
    if s:
        print("Error:", s, file=sys.stderr)
    print("Syntax: %s [options]" % sys.argv[0], file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)

def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h", ['help'])
    except getopt.GetoptError as e:
        usage(e)

    for opt, val in opts:
        if opt in ('-h', '--help'):
            usage()

    d = Dialog("TurnKey GNU/Linux - Reboot after kernel update")
    reboot  = d.yesno("Reboot now?", TEXT, "Reboot", "Skip")

    if not reboot:
        sys.exit(1)

if __name__ == "__main__":
    main()

