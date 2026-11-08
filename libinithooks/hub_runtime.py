"""Schedule Hub maintenance on the ordinary TurnKey Docker runtime."""
from pathlib import Path
import subprocess
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


def start_observations():
    """Kick the existing sender once without waiting for its network work."""
    try:
        if enabled():
            command = ['/usr/sbin/start-stop-daemon', '--start', '--background',
                       '--exec', '/usr/lib/inithooks/bin/turnkey-onboarding',
                       '--startas', '/usr/bin/timeout', '--', '--kill-after=5', '30',
                       '/usr/lib/inithooks/bin/turnkey-onboarding', 'flush']
        else:
            command = ['systemctl', 'start', '--no-block', 'turnkey-onboarding.service']
        subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        # The durable spool and ordinary timer/cron retries remain authoritative.
        pass
