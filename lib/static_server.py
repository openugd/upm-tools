#!/usr/bin/env python3
"""A static file server on 127.0.0.1 for the IL2CPP smoke page.

`python3 -m http.server` resolves its own address with socket.getfqdn() before it starts listening; on hosts with
slow reverse DNS (GitHub's macOS runners) that delays the first answer past any sensible wait. This is the same
handler without that lookup, and it always serves .wasm as application/wasm, which Unity's loader needs.

    python3 lib/static_server.py --port 8000 --directory Build/WebGL
"""
import argparse
import functools
import http.server
import mimetypes
import socketserver
import sys


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)   # no socket.getfqdn(): the name is only used in logs
        self.server_name, self.server_port = self.server_address[:2]


def main(argv):
    ap = argparse.ArgumentParser(prog='static_server.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, required=True)
    ap.add_argument('--directory', required=True)
    a = ap.parse_args(argv)
    mimetypes.add_type('application/wasm', '.wasm')
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=a.directory)
    with Server(('127.0.0.1', a.port), handler) as httpd:
        print('serving %s on http://127.0.0.1:%d/' % (a.directory, a.port), flush=True)
        httpd.serve_forever()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
