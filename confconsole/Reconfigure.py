"""Change passwords and application settings."""
import subprocess


def run():
    subprocess.run(['/usr/lib/inithooks/bin/turnkey-reconfigure'])
    return 'advanced'
