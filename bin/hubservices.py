#!/usr/bin/python3
# Copyright (c) 2011 Alon Swartz <alon@turnkeylinux.org>
"""Initialize Hub Services (TKLBAM, HubDNS)

Options:
    --apikey=    if not provided, will ask interactively
    --fqdn=      if not provided, will ask interactively
"""

import sys
import getopt
import signal
import subprocess
from subprocess import check_output, CalledProcessError, PIPE

from dialog_wrapper import Dialog

TEXT_SERVICES = ("1) TurnKey Backup and Migration: saves changes to files,\n"
                 "   databases and package management to encrypted storage\n"
                 "   which servers can be automatically restored from.\n"
                 "   https://www.turnkeylinux.org/tklbam\n\n"
                 "2) TurnKey Domain Management and Dynamic DNS:\n"
                 "   https://www.turnkeylinux.org/dns\n\n"
                 "You can start using these services immediately if you"
                 " initialize now. Or you can do this manually later (e.g.,"
                 " from the command line / Webmin)\n\n"
                 "API Key: (see https://hub.turnkeylinux.org/profile)")

TEXT_HUBDNS = ("TurnKey supports dynamic DNS configuration, powered by Amazon"
               " Route 53, a robust cloud DNS service:"
               " https://www.turnkeylinux.org/dns\n\n"
               "You can assign a hostname under:\n\n"
               "1) Any custom domain you are managing with the Hub.\n"
               "   For example: myhostname.mydomain.com\n\n"
               "2) The tklapp.com domain, if the hostname is untaken.\n"
               "   For example: myhostname.tklapp.com\n\n"
               "Set hostname (or press Enter to skip):")

SUCCESS_TKLBAM = ("Now that TKLBAM is initialized, you can backup using the"
                  " following shell command (no arguments required):\n\n"
                  "    tklbam-backup\n\n"
                  "You can enable daily automatic backup updates with this"
                  " command:\n\n"
                  "    chmod +x /etc/cron.daily/tklbam-backup\n\n"
                  "Documentation: https://www.turnkeylinux.org/tklbam\n"
                  "Manage your backups: https://hub.turnkeylinux.org")

SUCCESS_HUBDNS = ("You can enable hourly automatic updates with this"
                  " command:\n\n"
                  "    chmod +x /etc/cron.hourly/hubdns-update\n\n"
                  "Documentation: https://www.turnkeylinux.org/dns\n"
                  "Manage your hostnames: https://hub.turnkeylinux.org")

CONNECTIVITY_ERROR = ("Unable to connect to the Hub.\n\n"
                      "Please try again once your network settings are"
                      " configured, either via the Webmin interface, or by"
                      " using the following shell commands:\n\n"
                      "    tklbam-init APIKEY\n\n"
                      "    hubdns-init APIKEY FQDN\n"
                      "    hubdns-update")


def usage(s=None):
    if s:
        print("Error:", s, file=sys.stderr)
    print("Syntax: %s [options]" % sys.argv[0], file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h",
                                       ['help', 'apikey=', 'fqdn='])
    except getopt.GetoptError as e:
        usage(e)

    apikey = ""
    fqdn = ""
    for opt, val in opts:
        if opt in ('-h', '--help'):
            usage()
        elif opt == '--apikey':
            apikey = val
        elif opt == '--fqdn':
            fqdn = val

    if apikey:
        check_output(['tklbam-init', apikey], encoding=sys.stdin.encoding)

        if fqdn:
            check_output(['hubdns-init', apikey, fqdn],
                         encoding=sys.stdin.encoding)
            check_output(['hubdns-update'], encoding=sys.stdin.encoding)

        return

    initialized_tklbam = False
    d = Dialog('TurnKey GNU/Linux - First boot configuration')
    while 1:
        retcode, apikey = d.inputbox("Initialize Hub services", TEXT_SERVICES,
                                     apikey, "Apply", "Skip")

        if not apikey or retcode == 1:
            break

        d.infobox("Linking TKLBAM to the TurnKey Hub...")

        try:
            check_output(["host", "-W", "2", "hub.turnkeylinux.org"],
                         encoding=sys.stdin.encoding)
        except CalledProcessError as e:
            d.error(CONNECTIVITY_ERROR)
            break

        proc = subprocess.run(['tklbam-init', apikey],
                              stderr=PIPE,
                              encoding=sys.stdin.encoding)
        if proc.returncode == 0:
            d.msgbox('Success! Linked TKLBAM to Hub', SUCCESS_TKLBAM)
            initialized_tklbam = True
            break
        else:
            d.msgbox('Failure', proc.stderr)
            continue

    if initialized_tklbam:
        while 1:
            retcode, fqdn = d.inputbox("Assign TurnKey DNS hostname",
                                       TEXT_HUBDNS, fqdn, "Apply", "Skip")

            if not fqdn or retcode == 1:
                break

            d.infobox("Linking HubDNS to the TurnKey Hub...")

            proc1 = subprocess.run(['hubdns-init', apikey, fqdn],
                                   stderr.PIPE,
                                   encoding=sys.stdin.encoding)
            proc2 = subprocess.run(['hubdns-update'],
                                   stderr.PIPE,
                                   encoding=sys.stdin.encoding)
            if proc1 != 0:
                d.msgbox('Failure', proc1.stderr)
                continue
            elif proc2 != 0:
                d.msgbox('Failure', proc2.stderr)
                continue
            else:
                d.msgbox('Success! Assigned %s' % fqdn, SUCCESS_HUBDNS)


if __name__ == "__main__":
    main()
