import subprocess
import sys
from os import path, environ

def is_interactive() -> bool:
    return len(subprocess.run(['stty', 'size'], capture_output=True).stdout) > 0

# logging is done this way to ensure it's done the same as with inithooks run
def _log(level: str, message: str) -> None:
    args = ['logger', '-t', 'inithooks']

    level = level.lower()
    if not level in ('err', 'warn', 'info', 'debug'):
        m = sys.modules['__main__']
        err_msg = 'unknown log level in main'
        if hasattr(m, '__file__'):
            err_msg += f' ({path.abspath(m.__file__}))'
        error(err_msg)
    else:
        args.extend(['-p', level])
    args.append(message)

    subprocess.run(args)
    if 'INITHOOKS_LOGFILE' in environ:
        logfile = environ['INITHOOKS_LOGFILE']
        with open(logfile, 'a') as fob:
            fob.write(f'{level.upper()}: {message}')

def debug(message: str) -> None:
    _log('debug', message)
def info(message: str) -> None:
    _log('info', message)
def warn(message: str) -> None:
    _log('warn', message)
def error(message: str) -> None:
    _log('error', message)

