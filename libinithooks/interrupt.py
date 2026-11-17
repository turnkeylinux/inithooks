"""Let an active dialog handle Ctrl-C without interrupting its Python parent."""
from contextlib import contextmanager
import signal


@contextmanager
def dialog_interrupt():
    interrupted = [False]
    previous = signal.getsignal(signal.SIGINT)

    def mark_interrupt(signum, frame):
        interrupted[0] = True

    signal.signal(signal.SIGINT, mark_interrupt)
    try:
        yield interrupted
    finally:
        signal.signal(signal.SIGINT, previous)
