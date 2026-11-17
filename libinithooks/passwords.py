"""Shared password generation using the same requirements as manual entry."""
import re
import secrets
import string


def complexity(password):
    return sum(bool(re.search(pattern, password)) for pattern in ('[a-z]', '[A-Z]', r'\d', r'\W'))


def valid(password, minimum=8, categories=3, blacklist=()):
    length_ok = len(password) >= minimum if isinstance(minimum, int) else bool(re.match(minimum, password))
    return length_ok and complexity(password) >= categories and not any(item in password for item in blacklist)


def generate(minimum=8, categories=3, blacklist=()):
    # Avoid quoting and shell interpolation characters used by legacy setters.
    alphabet = string.ascii_letters + string.digits + '-.'
    size = max(24, minimum) if isinstance(minimum, int) else 24
    for unused in range(128):
        password = ''.join(secrets.choice(alphabet) for unused in range(size))
        if valid(password, minimum, categories, blacklist):
            return password
    raise ValueError('Automatic generation cannot satisfy these password requirements. Enter a password.')
