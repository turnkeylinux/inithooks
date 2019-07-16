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

from os import system
from subprocess import check_output, CalledProcessError

from dialog_wrapper import Dialog

TEXT_SERVICES = """1) TurnKey Backup and Migration: saves changes to files,
   databases and package management to encrypted storage
   which servers can be automatically restored from.
   https://www.turnkeylinux.org/tklbam

2) TurnKey Domain Management and Dynamic DNS:
   https://www.turnkeylinux.org/dns

You can start using these services immediately if you initialize now. Or you can do this manually later (e.g., from the command line / Webmin)

API Key: (see https://hub.turnkeylinux.org/profile)
"""

TEXT_HUBDNS = """TurnKey supports dynamic DNS configuration, powered by Amazon Route 53, a robust cloud DNS service: https://www.turnkeylinux.org/dns

You can assign a hostname under:

1) Any custom domain you are managing with the Hub.
   For example: myhostname.mydomain.com

2) The tklapp.com domain, if the hostname is untaken.
   For example: myhostname.tklapp.com

Set hostname (or press Enter to skip):
"""

SUCCESS_TKLBAM = """Now that TKLBAM is initialized, you can backup using the following shell command (no arguments required):

    tklbam-backup

You can enable daily automatic backup updates with this command:

    chmod +x /etc/cron.daily/tklbam-backup

Documentation: https://www.turnkeylinux.org/tklbam
Manage your backups: https://hub.turnkeylinux.org
"""

SUCCESS_HUBDNS = """You can enable hourly automatic updates with this command:

    chmod +x /etc/cron.hourly/hubdns-update

Documentation: https://www.turnkeylinux.org/dns
Manage your hostnames: https://hub.turnkeylinux.org
"""

CONNECTIVITY_ERROR = """Unable to connect to the Hub.

Please try again once your network settings are configured, either via the Webmin interface, or by using the following shell commands:

    tklbam-init APIKEY

    hubdns-init APIKEY FQDN
    hubdns-update
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
        system('tklbam-init', apikey)

        if fqdn:
            system('hubdns-init', apikey, fqdn)
            system('hubdns-update')

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
            check_output(["host", "-W", "2", "hub.turnkeylinux.org"])
        except CalledProcessError as e:
            d.error(CONNECTIVITY_ERROR)
            break

        try:
            check_output(['tklbam-init', apikey])
            d.msgbox('Success! Linked TKLBAM to Hub', SUCCESS_TKLBAM)
            initialized_tklbam = True
            break

        except CalledProcessError as e:
            d.msgbox('Failure', e.output)
            continue

    if initialized_tklbam:
        while 1:
            retcode, fqdn = d.inputbox("Assign TurnKey DNS hostname", TEXT_HUBDNS,
                                       fqdn, "Apply", "Skip")

            if not fqdn or retcode == 1:
                break

            d.infobox("Linking HubDNS to the TurnKey Hub...")

            try:
                check_output(['hubdns-init', apikey, fqdn])
                check_output(['hubdns-update'])
                d.msgbox('Success! Assigned %s' % fqdn, SUCCESS_HUBDNS)
                break

            except CalledProcessError as e:
                d.msgbox('Failure', e.output)
                continue

if __name__ == "__main__":
    main()

