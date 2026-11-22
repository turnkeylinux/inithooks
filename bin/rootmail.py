#!/usr/bin/python3
"""Configure forwarding of local root mail to the security contact."""
import os
import re
import subprocess

from libinithooks import onboarding
from libinithooks.dialog_wrapper import Dialog

DISABLED = '/var/lib/turnkey-onboarding/root-forward-disabled'
HELPER = '/usr/lib/inithooks/bin/secalerts.sh'


def main():
    dialog = Dialog('TurnKey Linux - Reconfigure')
    address = onboarding.load().get('notification_choice', '')
    if not onboarding.valid_email(address):
        match = re.search(r'^root:\s*([^\s#]+)', onboarding.read_text(onboarding.ALIASES), re.M)
        address = match.group(1) if match else ''
    if not onboarding.valid_email(address):
        dialog.msgbox('Root mail forwarding', 'Set a security contact first using Reconfigure.')
        return
    enabled = not os.path.exists(DISABLED)
    current = re.search(r'^root:\s*([^\n#]+)', onboarding.read_text(onboarding.ALIASES), re.M)
    current = current.group(1).strip() if current else ''
    if enabled and current.lower() != address.lower():
        dialog.msgbox('Root mail forwarding', 'The root mail alias has a custom configuration. Edit /etc/aliases to change it.')
        return
    if not enabled and current not in ('', 'root'):
        dialog.msgbox('Root mail forwarding', 'The root mail alias has a custom configuration. Edit /etc/aliases to change it.')
        return
    if enabled:
        text = ('Root mail is sent to %s.\n\nTurn off forwarding? '
                'Server alerts will remain in the local root mailbox.' % address)
        action, label = 'off', 'Turn off'
    else:
        text = 'Root mail stays in the local root mailbox.\n\nSend it to %s?' % address
        action, label = 'on', 'Enable'
    if dialog.yesno('Root mail forwarding', text, label, 'Back'):
        try:
            onboarding.notification_choice(address)
            subprocess.run([HELPER, '--root-forward', action, address], check=True, timeout=45)
        except (OSError, subprocess.SubprocessError):
            dialog.error('Could not change root mail forwarding. Check the local mail service and try again.')


if __name__ == '__main__':
    main()
