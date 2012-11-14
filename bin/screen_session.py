#!/usr/bin/python
# Copyright (c) 2012 Liraz Siri <liraz@turnkeylinux.org>

"""Runs a command in a screen session. 

If the command is already running in an existing screen session, attach to that
session."""

import sys
import executil
import hashlib
import commands
import os

class Error(Exception):
    pass

def usage():
    print >> sys.stderr, "syntax: %s command" % sys.argv[0]
    print >> sys.stderr, __doc__.strip()
    sys.exit(1)

def screen_list():
    parsed = [ line.strip().split('\t')
               for line in commands.getoutput("screen -list").splitlines()
               if line.startswith('\t') ]
    return parsed

def make_session_key(command):
    return hashlib.md5(`command`).hexdigest()

def session_lookup(command):
    session_key = make_session_key(command)
    matches = [ session_id
                for session_id, session_time, session_status in screen_list()
                if session_id.split('.', 1)[1] == session_key ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        raise Error("too many session matches")

    return None

def session_attach(session_id):
    executil.system("screen -x", session_id)

def session_create(command):
    session_key = make_session_key(command)
    executil.system("screen -S ", session_key, "--", *command)

def screen_session(command):
    session_id = session_lookup(command)
    if session_id:
        session_attach(session_id)
    else:
        session_create(command)

def main():
    args = sys.argv[1:]
    if not args or args[0] == "-h":
        usage()

    command = args
    screen_session(command)

if __name__ == "__main__":
    main()


