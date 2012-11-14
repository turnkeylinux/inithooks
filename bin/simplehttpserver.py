#!/usr/bin/python
"""Simple HTTP server"""
import os
import sys
import SimpleHTTPServer
import SocketServer
import select
import ssl

from os.path import exists, abspath

class Error(Exception):
    pass

def fatal(e):
    print >> sys.stderr, "error: " + str(e)
    sys.exit(1)

def usage(e=None):
    print >> sys.stderr, "Error: " + str(e)
    print >> sys.stderr, "Syntax: %s /path/to/webroot [address:]http-port [ [ssl-address:]ssl-port /path/to/pem ]" % sys.argv[0]
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
        r,w,e = select.select([server1,server2],[],[],0)
        if server1 in r:
            server1.handle_request()
        if server2 in r:
            server2.handle_request()

def main():
    args = sys.argv[1:]
    if not args or "-h" in args:
        usage()

    if len(args) not in (2, 4):
        usage("incorrect number of arguments")

    webroot_path = abspath(args[0])

    address, port = parse_address(args[1])
    httpd = SocketServer.TCPServer((address, port), SimpleHTTPServer.SimpleHTTPRequestHandler)
    httpsd = None

    if len(args) > 2:
        address, port = parse_address(args[2])
        certfile = args[3]
        if not exists(certfile):
            fatal("no such file '%s'" % certfile)
        certfile = os.path.abspath(certfile)

        httpsd = SocketServer.TCPServer((address, port), SimpleHTTPServer.SimpleHTTPRequestHandler)
        httpsd.socket = ssl.wrap_socket (httpsd.socket, certfile=certfile, server_side=True)

    os.chdir(webroot_path)

    if httpsd:
        serve_forever(httpd, httpsd)
    else:
        httpd.serve_forever()

if __name__ == "__main__":
    main()
