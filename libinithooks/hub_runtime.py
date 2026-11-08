"""Schedule Hub maintenance on the ordinary TurnKey Docker runtime."""
from pathlib import Path
from .onboarding import atomic

RUNTIME = Path('/etc/turnkey-runtime')
CRON = Path('/etc/cron.d')
JOBS = {
    'retry': ('turnkey-mail-retry', '* * * * *', '/usr/lib/inithooks/bin/turnkey-hub retry'),
    'dns-update': ('turnkey-mail-dns', '17 * * * *', '/usr/lib/inithooks/bin/turnkey-hub dns-update'),
    'observe': ('turnkey-onboarding', '*/5 * * * *', '/usr/lib/inithooks/bin/turnkey-onboarding flush'),
}


def enabled():
    try:
        return RUNTIME.read_text().strip() == 'docker'
    except FileNotFoundError:
        return False


def schedule(command, active=True):
    name, interval, executable = JOBS[command]
    path = CRON / name
    if not active:
        path.unlink(missing_ok=True)
        return
    text = 'SHELL=/bin/sh\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n'
    text += interval + ' root ' + executable + ' >/dev/null 2>&1\n'
    if not path.exists() or path.read_text() != text:
        atomic(path, text)
