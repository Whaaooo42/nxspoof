"""
nxspoof.net.web - the fake connection test: "200 OK / X-Organization: Nintendo / ok" for the test
names (exactly like Nintendo's real servers), 403 for anything else.

It only answers and reports: every request goes to on_request(client_ip, method, host, path, ua,
decision, details). details is None, except for a probed name (--probe, refused all the same): every header line
and the start of the body, to see what the console wanted. Requests carrying the self-test header aren't reported.
Never logs.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nxspoof.net.sockets import open_socket

SELFTEST_HEADER = "X-NX-Selftest"
PROBE_BODY = 2048                            # bytes of a probed request's body kept for the log

# Build the request handler bound to one server's policy and report callback.
def _make_handler(policy, on_request):
    # Answers one HTTP request.
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Value of the Server header.
        def version_string(self):
            return "nx-spoof"

        # Answer (200 ok for test names, 403 otherwise) and report.
        def _reply(self, send_body=True):
            host = (self.headers.get("Host") or "").split(":")[0]
            decision = policy.decide(host)
            if decision.action == "test":
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Organization", "Nintendo")
                self.send_header("Cache-Control", "max-age=0, no-cache, no-store")
                self.send_header("Pragma", "no-cache")
                self.end_headers()
                if send_body:
                    self.wfile.write(body)
            else:
                details = self._details() if decision.action == "probe" else None
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
            if self.headers.get(SELFTEST_HEADER) != "1":
                on_request(self.client_address[0], self.command, host, self.path, self.headers.get("User-Agent", "-"), decision,
                           details if decision.action == "probe" else None)

        # A probed request in full, for the log: each header as "Name: value", then the start of its body (if any).
        def _details(self):
            lines = ["%s: %s" % (k, v) for k, v in self.headers.items()]
            try:
                size = min(int(self.headers.get("Content-Length") or 0), PROBE_BODY)
            except ValueError:
                size = 0
            if size > 0:
                body = self.rfile.read(size)
                lines.append("body (%d bytes): %r" % (len(body), body[:PROBE_BODY]))
            return lines

        # Handle GET.
        def do_GET(self):
            self._reply()

        # Handle HEAD (headers only).
        def do_HEAD(self):
            self._reply(send_body=False)

        # Handle POST (same answer as GET).
        def do_POST(self):
            self._reply()

        # Silence the default per-request output (requests are reported instead).
        def log_message(self, *args):
            pass

    return Handler

# The fake connection-test web server on ip:port.
class TestWebServer(object):
    # on_request(client_ip, method, host, path, ua, decision, details) is called for every non-self-test request;
    # opener(kind, ip, port): who opens the socket (System.open_socket: the keeper during a split run; net/sockets.py by default).
    def __init__(self, ip, port, policy, on_request, opener=None):
        self.ip, self.port, self.policy, self.on_request = ip, port, policy, on_request
        self.opener = opener or open_socket
        self.httpd = None

    # Open ip:port and start serving; raises OSError (e.g. the port is in use).
    def start(self):
        sock = self.opener("tcp", self.ip, self.port)
        httpd = ThreadingHTTPServer((self.ip, self.port), _make_handler(self.policy, self.on_request), bind_and_activate=False)
        httpd.socket.close()
        httpd.socket, httpd.server_address = sock, sock.getsockname()
        httpd.server_activate()
        httpd.daemon_threads = True
        self.httpd = httpd
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    # Stop serving and free the port.
    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
