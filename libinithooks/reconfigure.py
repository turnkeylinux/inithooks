"""Discover and run the installed, user-facing initialization hooks."""
import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

HOOKS = Path('/usr/lib/inithooks/firstboot.d')
DEFAULTS = Path('/etc/default/inithooks')
LEGACY = Path(__file__).with_name('reconfigure_legacy.json')
ANNOTATION = re.compile(r'^#\s*reconfigure(?:-(title|description))?:\s*(.*?)\s*$', re.I)


@dataclass(frozen=True)
class Hook:
    name: str
    title: str
    description: str


def header(path):
    metadata, comments = {}, []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith('#!'):
            continue
        if not line.startswith('#'):
            break
        match = ANNOTATION.fullmatch(line)
        if match:
            key = match[1] or 'enabled'
            metadata[key] = match[2]
        else:
            comment = line.lstrip('#').strip()
            if comment and not re.match(r'(copyright|spdx|shellcheck|coding[:=]|-\*-)', comment, re.I):
                comments.append(comment)
    return metadata, comments


def discover(directory=HOOKS, legacy_path=LEGACY):
    legacy = json.loads(legacy_path.read_text()) if legacy_path.exists() else {}
    found = []
    if not directory.exists():
        return found
    for path in sorted(directory.iterdir()):
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        try:
            metadata, comments = header(path)
        except (OSError, UnicodeError):
            continue
        defaults = legacy.get(path.name, {})
        if 'enabled' in metadata:
            # A malformed explicit decision must not accidentally expose a hook.
            enabled = metadata['enabled'].lower() == 'yes'
        else:
            enabled = defaults.get('enabled', True)
        if not enabled:
            continue
        title = metadata.get('title') or defaults.get('title') or re.sub(r'^\d+[-_]*', '', path.name).replace('-', ' ').replace('_', ' ').title()
        description = metadata.get('description') or defaults.get('description') or (comments[0] if comments else '')
        found.append(Hook(path.name, title, description))
    return found


def setup_pending(defaults=DEFAULTS):
    if not defaults.exists():
        return False
    return bool(re.search(r'^\s*RUN_FIRSTBOOT\s*=\s*[\"\']?true[\"\']?\s*(?:#.*)?$', defaults.read_text(), re.M | re.I))


def execute(hook, directory=HOOKS):
    # Preserve terminal/locale settings, but not inherited first-boot preseeds.
    env = {key: value for key, value in os.environ.items()
           if key in ('TERM', 'LANG', 'LANGUAGE', 'TZ') or key.startswith('LC_')}
    env.update(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
               HOME='/root', USER='root', LOGNAME='root',
               _TURNKEY_INIT='1', _TURNKEY_RECONFIGURE='1')
    # env retains the historical execvp handling of shell hooks without a shebang.
    process = subprocess.Popen(['env', str(directory / hook.name)], env=env)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # Password helpers own cancellation and may ignore SIGINT. Keep the
            # menu off the terminal until their parent hook has actually exited.
            continue


def message(dialog, title, text):
    columns, rows = shutil.get_terminal_size((80, 24))
    width = min(76, columns - 4)
    lines = sum(max(1, len(textwrap.wrap(line, width - 6))) for line in text.splitlines())
    # Escape closes a menu-owned message and returns to the list.
    return dialog.console.msgbox(text, title=title, width=width, height=min(rows - 2, lines + 6))


def menu(directory=HOOKS):
    from libinithooks.dialog_wrapper import Dialog
    from dialog import DialogError
    d = Dialog('TurnKey GNU/Linux - Reconfigure')
    try:
        if setup_pending():
            message(d, 'Complete setup', 'Finish initial setup before reconfiguring this server.\n\nRun turnkey-init to continue.')
            return 1
        while True:
            hooks = discover(directory)
            columns, rows = shutil.get_terminal_size((80, 24))
            d.width = min(76, columns - 4)
            if not hooks:
                message(d, 'Reconfigure', 'There are no settings available to reconfigure.')
                return 0
            choices = [(str(i + 1), textwrap.shorten(h.title + (' - ' + h.description if h.description else ''),
                                                    width=max(20, d.width - 12), placeholder='...'))
                       for i, h in enumerate(hooks)]
            # Use the dialog directly: Escape here means Back, not quit firstboot.
            status, value = d.console.menu('Choose what to reconfigure.', title='Reconfigure',
                width=d.width, height=min(rows - 2, len(hooks) + 8),
                menu_height=min(len(hooks), max(1, rows - 10)), choices=choices,
                ok_label='Open', cancel_label='Back', help_button=True, help_label='Details')
            if status not in ('ok', 'help'):
                return 0
            try:
                hook = hooks[int(value) - 1]
            except (ValueError, IndexError):
                continue
            if status == 'help':
                message(d, hook.title, hook.description or 'Open this setting to reconfigure it.')
                continue
            try:
                code = execute(hook, directory)
            except OSError:
                code = 1
            except KeyboardInterrupt:
                continue
            if code not in (0, 130):
                message(d, hook.title, 'Configuration could not finish. You can try again or choose another setting.')
    except DialogError:
        print('Could not display Reconfigure. Run sudo turnkey reconfigure in an interactive terminal.', file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='list available settings as JSON')
    args = parser.parse_args()
    if args.list:
        print(json.dumps([asdict(h) for h in discover()], indent=2))
        return 0
    if os.geteuid() != 0:
        sys.exit('Run: sudo turnkey reconfigure')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.exit('Run sudo turnkey reconfigure in an interactive terminal.')
    return menu()
