import subprocess

def is_interactive() -> bool:
    return len(subprocess.run(['stty', 'size'], capture_output=True).stdout) > 0
