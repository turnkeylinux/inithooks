#!/usr/bin/python3
"""Interface to inithooks cache

Arguments:

    key                 key name (required)
    value               if specified, will set as key value
                        if omitted, will return the value of key if set

Environment:

    INITHOOKS_CACHE     path to cache (default: /var/lib/inithooks/cache)
"""

import os
import sys
import getopt
from typing import NoReturn

CACHE_DIR = os.environ.get("INITHOOKS_CACHE", "/var/lib/inithooks/cache")


def fatal(e) -> NoReturn:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)


def usage(msg: str | getopt.GetoptError = "") -> NoReturn:
    if msg:
        print(f"Error: {msg}", file=sys.stderr)
    print(f"Syntax: {sys.argv[0]} <key> [value]", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    sys.exit(1)


class KeyStore:
    def __init__(self, cache_dir: str = CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, mode=0o755, exist_ok=True)

    def read(self, key, fallback: str = "") -> str:
        keypath = os.path.join(self.cache_dir, key)

        if os.path.exists(keypath):
            with open(keypath, "r") as fob:
                data = fob.read()
            return data

        return fallback

    def write(self, key: str, val: str) -> None:
        keypath = os.path.join(self.cache_dir, key)

        with open(keypath, "w") as fob:
            fob.write(val)


# convenience functions


def read(key, fallback: str = ""):
    return KeyStore(CACHE_DIR).read(key, fallback)


def write(key: str, value: str) -> None:
    return KeyStore(CACHE_DIR).write(key, value)


if __name__ == "__main__":
    opts = []
    args = []
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h", ["help"])
    except getopt.GetoptError as e:
        usage(e)

    for opt, val in opts:
        if opt in ("-h", "--help"):
            usage()

    if len(args) == 0:
        usage()

    if len(args) > 2:
        fatal("too many arguments")

    if len(args) == 1:
        val = read(args[0])
        if val:
            print(val)

    if len(args) == 2:
        write(args[0], args[1])
