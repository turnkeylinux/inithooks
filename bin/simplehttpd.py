#!/usr/bin/python3
# Copyright (c) 2012-2015 Liraz Siri <liraz@turnkeylinux.org>
# Copyright (c) 2015-2020 TurnKey GNU/Linux - https://www.turnkeylinux.org

"""
Simple HTTP server

Arguments:

        http-port          Port to bind HTTP web server to.
                           0 = disable HTTP

Options:

    --runas=username
    --daemonize=/path/to/pidfile
    --logfile=/path/to/logfile

Known bugs:

- Invalid cert.pem && cert.key will break SSL silently

"""
import os
from os.path import abspath
from pathlib import Path
from tempfile import NamedTemporaryFile

import sys
import getopt

import http.server
import socketserver
import ssl

import pwd
import grp

import signal


class SimpleWebServerError(Exception):
    pass


def fatal(e):
    print("error: " + str(e), file=sys.stderr)
    sys.exit(1)


def usage(e=None):
    print("Error: " + str(e), file=sys.stderr)
    print(("Syntax: %s [ -options ] path/to/webroot [address:]http-port ["
           " [ssl-address:]ssl-port path/to/cert.pem [ path/to/cert.key ] ]"
           ) % sys.argv[0], file=sys.stderr)
    print(__doc__.strip(), file=sys.stderr)
    sys.exit(1)


def is_writeable(path):
    if not Path(path).exists():
        path = os.path.dirname(path)

    return os.access(path, os.W_OK)


def daemonize(pidfile, logfile=None):
    if logfile is None:
        logfile = "/dev/null"

    pid = os.fork()
    if pid != 0:
        print("%d" % pid, file=open(pidfile, "w"))
        sys.exit(0)

    os.chdir("/")
    os.setsid()

    logfile = open(logfile, "w")
    os.dup2(logfile.fileno(), sys.stdout.fileno())
    os.dup2(logfile.fileno(), sys.stderr.fileno())

    devnull = open("/dev/null", "r")
    os.dup2(devnull.fileno(), sys.stdin.fileno())


class SecureHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    ALLOWED_EXTS = []

    def list_directory(self, path):
        self.send_error(404, "No permission to list directory")
        return None

    def translate_path(self, path):
        path = Path(path)
        if not path.isdir():
            ext = path.suffix.lower()
            if ext[1:] not in self.ALLOWED_EXTS:
                return '/dev/null/doesntexist'

        return http.server.SimpleHTTPRequestHandler.translate_path(self, path)


class SimpleWebServer:

    class TCPServer(socketserver.ForkingTCPServer):
        allow_reuse_address = True

    class HTTPRequestHandler(SecureHTTPRequestHandler):
        ALLOWED_EXTS = ['css', 'gif', 'html', 'js', 'png', 'jpg', 'txt']

    class Address:
        @staticmethod
        def parse_address(address):
            if ':' in address:
                host, port = address.split(':', 1)
            else:
                host = '0.0.0.0'
                port = address

            try:
                port = int(port)
                assert port > 0 and port < 65535
            except (ValueError, AssertionError):
                raise SimpleWebServerError("Illegal port: '{}' - must be"
                                           " integer between 1 -> 65534"
                                           "".format(port))
            return host, port

        def __init__(self, address):
            host, port = self.parse_address(address)
            self.host = host
            self.port = port

    class HTTPSConf(Address):
        def __init__(self, address, certfile, keyfile=None):
            SimpleWebServer.Address.__init__(self, address)
            if keyfile is None:
                keyfile = certfile

            self.certfile = self._validate_path(certfile)
            self.keyfile = self._validate_path(keyfile)

        @staticmethod
        def _validate_path(fpath):
            if not Path(fpath).exists:
                raise SimpleWebServerError("No such file '{}'.".format(fpath))
            return str(Path(fpath).resolve())

        CIPHERS = 'ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES256-GCM-SHA384:DHE-RSA-AES128-GCM-SHA256:DHE-DSS-AES128-GCM-SHA256:kEDH+AESGCM:ECDHE-RSA-AES128-SHA256:ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA:ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES256-SHA384:ECDHE-ECDSA-AES256-SHA384:ECDHE-RSA-AES256-SHA:ECDHE-ECDSA-AES256-SHA:DHE-RSA-AES128-SHA256:DHE-RSA-AES128-SHA:DHE-DSS-AES128-SHA256:DHE-RSA-AES256-SHA256:DHE-DSS-AES256-SHA:DHE-RSA-AES256-SHA:AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA256:AES256-SHA256:AES128-SHA:AES256-SHA:AES:CAMELLIA:DES-CBC3-SHA:!aNULL:!eNULL:!EXPORT:!DES:!RC4:!MD5:!PSK:!aECDH:!EDH-DSS-DES-CBC3-SHA:!EDH-RSA-DES-CBC3-SHA:!KRB5-DES-CBC3-SHA'  # noqa

    class TempOwnedAs:
        def __init__(self, fpath, owner, chmod=0o600):
            fpath = Path(fpath)
            if not fpath.exists():
                raise SimpleWebServerError("No such file '{}'.".format(fpath))
            tempfile = NamedTemporaryFile(prefix='tmp')
            with open(fpath) as fob:
                tempfile.write(fob.read().encode())
            tempfile.seek(0)

            pwent = pwd.getpwnam(owner)

            os.chown(tempfile.name, pwent.pw_uid, pwent.pw_gid)
            if chmod:
                os.chmod(tempfile.name, chmod)

            self.tempfile = tempfile
            self._hack_delete()

        def name(self):
            return str(self.tempfile.name)

        def _hack_delete(self):
            """temp files are not always being cleaned up. This hack creates a
            file in /var/lib/inithooks/.tmp_files/ with the same name as the
            temp file, so the daemon manager can clean them all up afterwards.
            """
            _tmp_hack = '/var/lib/inithooks/.tmp_files/'
            _hack_dir = Path(_tmp_hack)
            _hack_dir.mkdir(parents=True, exist_ok=True)
            _hack_file = _hack_dir / self.name
            _hack_file.open(mode='w')

        def close(self):
            self.__del__()

        def __del__(self):
            self.tempfile.close()

    def __init__(self, webroot, http_address=None,
                 https_conf=None, runas=None):

        self.httpd = self.TCPServer((http_address.host, http_address.port),
                                    self.HTTPRequestHandler) \
                     if http_address else None

        httpsd = None
        if https_conf:

            certfile = https_conf.certfile
            keyfile = https_conf.keyfile

            if runas:
                _certfile = self.TempOwnedAs(certfile, runas)
                _keyfile = self.TempOwnedAs(keyfile, runas)
                certfile = _certfile.name()
                keyfile = _keyfile.name()

            httpsd = self.TCPServer((https_conf.host, https_conf.port),
                                    self.HTTPRequestHandler)

            httpsd.socket = ssl.wrap_socket(httpsd.socket, certfile=certfile,
                                            keyfile=keyfile, server_side=True,
                                            ssl_version=ssl.PROTOCOL_TLSv1_2,
                                            ciphers=https_conf.CIPHERS)

        if runas:
            self.drop_privileges(runas)

        self.httpsd = httpsd
        self.webroot = webroot

    @staticmethod
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

    def serve_forever(self):

        os.chdir(self.webroot)
        httpd = self.httpd
        httpsd = self.httpsd

        if not httpsd and not httpd:
            raise SimpleWebServerError("Nothing to serve")

        if not httpsd and httpd:
            return httpd.serve_forever()

        if not httpd and httpsd:
            return httpsd.serve_forever()

        pid = os.fork()
        if pid == 0:
            return httpsd.serve_forever()
        else:
            return httpd.serve_forever()


def main():
    args = sys.argv[1:]
    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], 'h',
                                       ["daemonize=", "logfile=", "runas="])
    except getopt.GetoptError as e:
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
                fatal("No such user '{}'".format(val))

            runas = val

    if not args:
        usage()

    if len(args) not in (2, 4, 5):
        usage("incorrect number of arguments")

    if daemonize_pidfile and not is_writeable(daemonize_pidfile):
        fatal("pidfile '%s' not writeable" % daemonize_pidfile)

    if logfile:
        if not daemonize_pidfile:
            fatal("--logfile can only be used with --daemonize")

        if not is_writeable(logfile):
            fatal("logfile '%s' not writeable" % logfile)

    webroot, http_address = args[:2]

    if http_address in ("", "0"):
        http_address = None
    else:
        http_address = SimpleWebServer.Address(http_address)

    https_conf = None
    if len(args[2:]):
        address, certfile = args[2:4]

        try:
            keyfile = args[4]
        except IndexError:
            keyfile = None

        https_conf = SimpleWebServer.HTTPSConf(address, certfile, keyfile)

    def sighandler(signum, stack):
        if signum == signal.SIGTERM:
            os.killpg(os.getpgrp(), signal.SIGHUP)
        sys.exit(1)

    signal.signal(signal.SIGHUP, sighandler)
    signal.signal(signal.SIGTERM, sighandler)

    server = SimpleWebServer(webroot, http_address, https_conf, runas)
    if daemonize_pidfile:
        daemonize(daemonize_pidfile, logfile)

    server.serve_forever()


if __name__ == "__main__":
    main()
