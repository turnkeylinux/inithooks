#!/usr/bin/python
"""Simple HTTP server"""
import os
import sys
import SimpleHTTPServer
import SocketServer

class Error(Exception):
    pass

def usage(e=None):
    print >> sys.stderr, "Error: " + str(e)
    print >> sys.stderr, "Syntax: %s /path/to/webroot [address:]http-port" % sys.argv[0]
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

def main():
    args = sys.argv[1:]
    if not args or "-h" in args:
        usage()

    if len(args) < 2:
        usage("not enough arguments")

    webroot_path = args[0]
    address, port = parse_address(args[1])

    os.chdir(webroot_path)
    Handler = SimpleHTTPServer.SimpleHTTPRequestHandler
    httpd = SocketServer.TCPServer((address, port), Handler)
    httpd.serve_forever()

if __name__ == "__main__":
    main()
