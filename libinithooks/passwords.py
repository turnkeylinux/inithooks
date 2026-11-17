"""Shared password generation using the same requirements as manual entry."""
import re
import secrets
from functools import lru_cache
from pathlib import Path


def complexity(password):
    return sum(bool(re.search(pattern, password)) for pattern in ('[a-z]', '[A-Z]', r'\d', r'\W'))


def valid(password, minimum=8, categories=3, blacklist=()):
    length_ok = len(password) >= minimum if isinstance(minimum, int) else bool(re.match(minimum, password))
    return length_ok and complexity(password) >= categories and not any(item in password for item in blacklist)


@lru_cache(maxsize=1)
def wordlist():
    path = Path(__file__).with_name("eff_large_wordlist.txt")
    return tuple(line.split()[1] for line in path.read_text(encoding="utf-8").splitlines())


def generate(minimum=8, categories=3, blacklist=()):
    # Prefer visible dashes; a period supports hooks that forbid dashes.
    separator = next((c for c in ("-", ".") if not any(item in c for item in blacklist)), None)
    if separator is None:
        raise ValueError("Automatic generation cannot satisfy these password requirements. Enter a password.")
    words = wordlist()
    for unused in range(128):
        selected = [secrets.choice(words) for _ in range(5)]
        selected[0] = selected[0].capitalize()
        digits = f"{secrets.randbelow(100):02d}"
        password = separator.join([*selected, digits])
        if valid(password, minimum, categories, blacklist):
            return password
    raise ValueError("Automatic generation cannot satisfy these password requirements. Enter a password.")
