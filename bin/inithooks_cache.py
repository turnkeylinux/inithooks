#!/usr/bin/python
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

def fatal(e):
    print >> sys.stderr, "Error:", e
    sys.exit(1)

def usage(s=None):
    if s:
        print >> sys.stderr, "Error:", s
    print >> sys.stderr, "Syntax: %s <key> [value]" % sys.argv[0]
    print >> sys.stderr, __doc__
    sys.exit(1)

class KeyStore:
    def __init__(self, path):
        self.path = path
        if not os.path.exists(self.path):
            os.makedirs(self.path)

    def read(self, key):
        keypath = os.path.join(self.path, key)

        if os.path.exists(keypath):
            return file(keypath, 'r').read()

        return None

    def write(self, key, val):
        keypath = os.path.join(self.path, key)

        fh = file(keypath, 'w')
        fh.write(val)
        fh.close()

#convenience functions
CACHE_DIR = os.environ.get('INITHOOKS_CACHE', '/var/lib/inithooks/cache')

def read(key):
    return KeyStore(CACHE_DIR).read(key)

def write(key, value):
    return KeyStore(CACHE_DIR).write(key, value)


if __name__ == "__main__":
    import getopt

    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h", ["help"])
    except getopt.GetoptError, e:
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
            print val

    if len(args) == 2:
        write(args[0], args[1])

