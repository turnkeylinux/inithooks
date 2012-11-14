#!/usr/bin/python
"""
Simple HTTP server

Options:

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

import stdtrap

class Error(Exception):
    pass

def fatal(e):
    print >> sys.stderr, "error: " + str(e)
    sys.exit(1)

def usage(e=None):
    print >> sys.stderr, "Error: " + str(e)
    print >> sys.stderr, "Syntax: %s [ -options ] /path/to/webroot [address:]http-port [ [ssl-address:]ssl-port /path/to/pem ]" % sys.argv[0]
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

def simplewebserver(webroot, http_address, https_address, certfile):

    httpd = None
    if http_address:
        httpd = SocketServer.TCPServer(http_address, SimpleHTTPServer.SimpleHTTPRequestHandler)

    httpsd = None
    if https_address:
        httpsd = SocketServer.TCPServer(https_address, SimpleHTTPServer.SimpleHTTPRequestHandler)
        httpsd.socket = ssl.wrap_socket (httpsd.socket, certfile=certfile, server_side=True)

    orig_cwd = os.getcwd()
    os.chdir(webroot)

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
        opts, args = getopt.gnu_getopt(sys.argv[1:], 'h', ["daemonize=", "logfile="])
    except getopt.GetoptError, e:
        usage(e)

    daemonize_pidfile = None
    logfile = None
    for opt, val in opts:
        if opt == '-h':
            usage()

        if opt == '--daemonize':
            daemonize_pidfile = abspath(val)

        if opt == '--logfile':
            logfile = abspath(val)

    if not args:
        usage()

    if len(args) not in (2, 4):
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

    if daemonize_pidfile:
        daemonize(daemonize_pidfile, logfile)

    simplewebserver(webroot, http_address, https_address, certfile)

if __name__ == "__main__":
    main()
