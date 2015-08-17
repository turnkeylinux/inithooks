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
import getopt

INITHOOKS_CACHE = os.environ.get('INITHOOKS_CACHE', '/var/lib/inithooks/cache')

def fatal(e):
    print >> sys.stderr, "Error:", e
    sys.exit(1)

def warn(e):
    print >> sys.stderr, "Warning:", e

def usage(s=None):
    if s:
        print >> sys.stderr, "Error:", s
    print >> sys.stderr, "Syntax: %s <key> [value]" % sys.argv[0]
    print >> sys.stderr, __doc__
    sys.exit(1)

def write(key, value):
    if not os.path.exists(INITHOOKS_CACHE):
        os.makedirs(INITHOOKS_CACHE)

    fh = file(os.path.join(INITHOOKS_CACHE, key), 'w')
    fh.write(value)
    fh.close()

def read(key):
    if os.path.exists(os.path.join(INITHOOKS_CACHE, key)):
        return file(os.path.join(INITHOOKS_CACHE, key), 'r').read()

    return None

def main():
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], "h", ["help"])
    except getopt.GetoptError, e:
        usage(e)

    for opt, val in opts:
        if opt in ("-h", "--help"):
            usage()

    if not len(args) in (1, 2):
        usage()

    if len(args) == 1:
        val = read(args[0])
        if val:
            print val

    if len(args) == 2:
        write(args[0], args[1])


if __name__ == "__main__":
    main()

