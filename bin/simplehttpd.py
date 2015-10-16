#!/usr/bin/python
# Copyright (c) 2012 Liraz Siri <liraz@turnkeylinux.org>

"""
Simple HTTP server

Options:

    --runas=username
    --daemonize=/path/to/pidfile
    --logfile=/path/to/logfile

"""
import os
from os.path import exists, abspath

import sys
import getopt

import SimpleHTTPServer
import SocketServer
import select
import ssl

import pwd
import grp
import temp

import signal

class Error(Exception):
    pass

def fatal(e):
    print >> sys.stderr, "error: " + str(e)
    sys.exit(1)

def usage(e=None):
    print >> sys.stderr, "Error: " + str(e)
    print >> sys.stderr, "Syntax: %s [ -options ] /path/to/webroot [address:]http-port [ [ssl-address:]ssl-port /path/to/pem /path/to/key ]" % sys.argv[0]
    print >> sys.stderr, __doc__.strip()
    sys.exit(1)

def parse_address(arg):
    if ':' in arg:
        address, port = arg.split(':', 1)
    else:
        address = '0.0.0.0'
        port = arg

    try:
        port = int(port)
        if not 0 < port < 65535:
            raise Exception
    except:
        raise Error("illegal port")

    return address, port

def serve_forever(server1,server2):
    while True:
        r, w, e = select.select([server1,server2],[],[],0)
        if server1 in r:
            server1.handle_request()
        if server2 in r:
            server2.handle_request()

def is_writeable(path):
    if not os.path.exists(path):
        path = os.path.dirname(path)

    return os.access(path, os.W_OK)

def daemonize(pidfile, logfile=None):
    if logfile is None:
        logfile = "/dev/null"

    pid = os.fork()
    if pid != 0:
        print >> file(pidfile, "w"), "%d" % pid
        sys.exit(1)

    os.chdir("/")
    os.setsid()

    logfile = file(logfile, "w")
    os.dup2(logfile.fileno(), sys.stdout.fileno())
    os.dup2(logfile.fileno(), sys.stderr.fileno())

    devnull = file("/dev/null", "r")
    os.dup2(devnull.fileno(), sys.stdin.fileno())

def drop_privileges(user):
    pwent = pwd.getpwnam(user)
    uid, gid, home = pwent[2], pwent[3], pwent[5]
    os.unsetenv("XAUTHORITY")
    os.putenv("USER", user)
    os.putenv("HOME", home)

    usergroups = []
    groups = grp.getgrall()
    for group in groups:
        if user in group[3]:
            usergroups.append(group[2])

    os.setgroups(usergroups)
    os.setgid(gid)
    os.setuid(uid)

def simplewebserver(webroot, http_address=None, https_address=None, certfile=None, keyfile=None, runas=None):
    if https_address and not certfile or not keyfile:
        raise Error("certfile and keyfile needed to use HTTPS")

    if runas and certfile and keyfile:
        # copy over keyfile and certfile to a temporary file owned by runas

        paths = [certfile, keyfile]
        temps = [temp.TempFile(), temp.TempFile()]
        for i, afile in enumerate(paths):
                tempfile = temps[i].path
                file(tempfile, "w").write(file(afile).read())

        pwent = pwd.getpwnam(runas)
        os.chown(tempfile, pwent.pw_uid, pwent.pw_gid)
        os.chmod(tempfile, 0600)

        certfile = temps[0].path
        keyfile = temps[1].path

    httpd = None
    if http_address:
        httpd = SocketServer.TCPServer(http_address, SimpleHTTPServer.SimpleHTTPRequestHandler)

    httpsd = None
    if https_address:
        httpsd = SocketServer.TCPServer(https_address, SimpleHTTPServer.SimpleHTTPRequestHandler)
        httpsd.socket = ssl.wrap_socket(httpsd.socket, certfile=certfile, keyfile=keyfile, server_side=True, ssl_version=ssl.PROTOCOL_TLSv1, ciphers='ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES256-GCM-SHA384:DHE-RSA-AES128-GCM-SHA256:DHE-DSS-AES128-GCM-SHA256:kEDH+AESGCM:ECDHE-RSA-AES128-SHA256:ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA:ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES256-SHA384:ECDHE-ECDSA-AES256-SHA384:ECDHE-RSA-AES256-SHA:ECDHE-ECDSA-AES256-SHA:DHE-RSA-AES128-SHA256:DHE-RSA-AES128-SHA:DHE-DSS-AES128-SHA256:DHE-RSA-AES256-SHA256:DHE-DSS-AES256-SHA:DHE-RSA-AES256-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA256:AES256-SHA256:AES128-SHA:AES256-SHA:AES:CAMELLIA:DES-CBC3-SHA:!aNULL:!eNULL:!EXPORT:!DES:!RC4:!MD5:!PSK:!aECDH:!EDH-DSS-DES-CBC3-SHA:!EDH-RSA-DES-CBC3-SHA:!KRB5-DES-CBC3-SHA')

    orig_cwd = os.getcwd()
    os.chdir(webroot)

    if runas:
        drop_privileges(runas)

    if httpsd and httpd:
        serve_forever(httpd, httpsd)
    elif httpd:
        httpd.serve_forever()
    elif httpsd:
        httpsd.serve_forever()

    os.chdir(orig_cwd)

def main():
    args = sys.argv[1:]
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], 'h', ["daemonize=", "logfile=", "runas="])
    except getopt.GetoptError, e:
        usage(e)

    daemonize_pidfile = None
    logfile = None
    runas = None

    for opt, val in opts:
        if opt == '-h':
            usage()

        if opt == '--daemonize':
            daemonize_pidfile = abspath(val)

        if opt == '--logfile':
            logfile = abspath(val)

        if opt == '--runas':
            try:
                pwd.getpwnam(val)
            except KeyError:
                fatal("no such user '%s'" % val)

            runas = val

    if not args:
        usage()

    if len(args) not in (2, 5):
        usage("incorrect number of arguments")

    if daemonize_pidfile and not is_writeable(daemonize_pidfile):
        fatal("pidfile '%s' not writeable" % daemonize_pidfile)

    if logfile:
        if not daemonize_pidfile:
            fatal("--logfile can only be used with --daemonize")

        if not is_writeable(logfile):
            fatal("logfile '%s' not writeable" % logfile)

    webroot = abspath(args[0])

    http_address = None
    if args[1] not in ("", "0"):
        http_address = parse_address(args[1])

    certfile = None
    https_address = None
    if len(args) > 2:
        https_address = parse_address(args[2])
        certfile = args[3]
        if not exists(certfile):
            fatal("no such file '%s'" % certfile)
        certfile = os.path.abspath(certfile)
        keyfile = args[4]
        if not exists(keyfile):
            fatal("no such file '%s'" % keyfile)
        keyfile = os.path.abspath(keyfile)

    if daemonize_pidfile:
        daemonize(daemonize_pidfile, logfile)

    def handler(signum, stack):
        print "caught signal (%d), exiting" % signum
        sys.exit(1)
    signal.signal(signal.SIGTERM, handler)

    simplewebserver(webroot, http_address, https_address, certfile, keyfile, runas)

if __name__ == "__main__":
    main()
