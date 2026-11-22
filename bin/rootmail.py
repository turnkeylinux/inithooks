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
        dialog.msgbox('System mail forwarding', 'Set a security contact first using Reconfigure.')
        return
    enabled = not os.path.exists(DISABLED)
    current = re.search(r'^root:\s*([^\n#]+)', onboarding.read_text(onboarding.ALIASES), re.M)
    current = current.group(1).strip() if current else ''
    if enabled and current.lower() != address.lower():
        dialog.msgbox('System mail forwarding', 'The root mail alias has a custom configuration. Edit /etc/aliases to change it.')
        return
    if not enabled and current not in ('', 'root'):
        dialog.msgbox('System mail forwarding', 'The root mail alias has a custom configuration. Edit /etc/aliases to change it.')
        return
    if enabled:
        text = (f'This server forwards system mail to {address}. Depending on your settings, '
                'this may include security update reports, backup failure notices, and low disk space alerts.\n\n'
                'If you turn off forwarding, these messages will stay in the server’s local mailbox.')
        action, label = 'off', 'Turn off'
    else:
        text = f'System mail stays in the server’s local mailbox.\n\nForward it to {address}?'
        action, label = 'on', 'Enable'
    if dialog.yesno('System mail forwarding', text, label, 'Back'):
        try:
            onboarding.notification_choice(address)
            subprocess.run([HELPER, '--root-forward', action, address], check=True, timeout=45)
        except (OSError, subprocess.SubprocessError):
            dialog.error('Could not change system mail forwarding. Check the local mail service and try again.')


if __name__ == '__main__':
    main()
