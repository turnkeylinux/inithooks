#!/usr/bin/python
"""Reboot to install kernel upgrade"""

import sys
import getopt
import signal

from executil import ExecError, getoutput
from dialog_wrapper import Dialog

TEXT = """A security update to the kernel requires a reboot to go into effect.

For maximum protection, we recommend rebooting now.
"""

def usage(s=None):
    if s:
        print >> sys.stderr, "Error:", s
    print >> sys.stderr, "Syntax: %s [options]" % sys.argv[0]
    print >> sys.stderr, __doc__
    sys.exit(1)

def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h", ['help'])
    except getopt.GetoptError, e:
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

