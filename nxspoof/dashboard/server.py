"""
nxspoof.dashboard.server - the live dashboard's web server, for THIS Mac only (http://localhost:8800).

  GET  /                    the page (web/index.html, with this run's secret token inside)
  GET  /css/..., /js/...    the page's files, only from web/ (nothing else on disk is reachable)
  GET  /docs/...            the docs pages (📖 Help), only from the docs folder it was given (read only, no token inside)
  GET  /api/state           the snapshot the page shows (built by dashboard/state.py)
  GET  /api/history         recent events, so a reloaded page isn't empty
  GET  /api/events          live events (Server-Sent Events), "bye" when the dashboard closes
  GET  /api/data/<name>     read-only data added with add_reader() (the control center: runs, run, mac)
  POST /api/action/<name>   a control from the Controls registry: unknown -> 404, bad input or not
                            possible now (ControlError / ValueError) -> 400 with the reason,
                            missing or wrong token -> 403, a bug -> 500

Safety: listens on 127.0.0.1 only (consoles on the hotspot can't reach it), only answers
Host: localhost / 127.0.0.1 (stops other websites from talking to it through DNS tricks), controls
need the dashboard's token (only the page served here knows it), and no inline scripts are allowed.
Port 0 means a free port (tests); .port and .url follow.

DashboardSink turns bus events into the page's events (one per line, like the log file), each with its
console references (tech_names, casual_names: [start, end, who], counted the way JavaScript counts).
"""

import collections
import json
import os
import queue
import secrets
import select
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nxspoof.core.controls import ControlError
from nxspoof.core.fmt import error_text, error_trace

PING = 2                                     # seconds between keep-alive pings on the live stream
LOOK = 0.25                                  # seconds between two looks at whether the page hung up (a closed page is noticed at once)
MAX_BODY = 1000000                           # bytes a control's request may carry (a settings import is a few kilobytes)
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'"

# ##############################################################################################################################################################
# ##  EVENTS FOR THE PAGE                                                                                                                                     ##
# ##############################################################################################################################################################

# Spans counted in characters (Python) -> counted in UTF-16 units (JavaScript: an emoji like 🎮 counts as 2).
def js_spans(text, spans):
    units = lambda n: len(text[:n].encode("utf-16-le")) // 2
    return [[units(s), units(e), who] for s, e, who in spans]

# A bus sink feeding the dashboard: one page event per log line (details as their own lines), casual lines too.
class DashboardSink(object):
    # server: the DashboardServer (it may not be listening yet: events are kept in its history).
    def __init__(self, server):
        self.server = server

    # Called by the bus for every event.
    def __call__(self, event):
        for item in self.items(event):
            self.server.publish(item)

    # The page events for one bus event: {"ts", "t", "kind", "tag", "tech", "casual", "problem", "mac", "tech_names", "casual_names"}
    # (kind: what it is, e.g. "run.log", a run's first line, before which the page draws a divider).
    @staticmethod
    def items(event):
        line = event.technical_line()
        if line is None and not event.casual:
            return []
        stamp = time.strftime("%H:%M:%S", time.localtime(event.ts))
        problem = event.is_problem()
        out = [{"ts": event.ts, "t": stamp, "kind": event.kind, "tag": event.tag, "tech": line, "casual": event.casual, "problem": problem, "mac": event.mac,
                "tech_names": js_spans(line, event.technical_names()) if line else [],
                "casual_names": js_spans(event.casual, event.casual_names) if event.casual else []}]
        for d in event.detail_lines():
            out.append({"ts": event.ts, "t": stamp, "tag": "", "tech": d, "casual": None, "problem": problem, "mac": event.mac})
        return out

# ##############################################################################################################################################################
# ##  THE SERVER                                                                                                                                              ##
# ##############################################################################################################################################################

# The dashboard: local web server + live event fan-out; controls come from a Controls registry.
class DashboardServer(object):
    # get_state() -> dict for /api/state; controls: core.controls.Controls; web_dir: where the page lives; problem(tech, details):
    # told of every internal error, worded (the page only gets its message; the dashboard's log gets the traceback).
    def __init__(self, port, get_state, controls, web_dir=WEB_DIR, history=600, docs_dir=None, problem=None):
        self.port, self.get_state, self.controls, self.web_dir, self.docs_dir = port, get_state, controls, web_dir, docs_dir
        self.problem = problem or (lambda tech, details: None)
        self.token = secrets.token_urlsafe(24)
        self.history = collections.deque(maxlen=history)
        self.subscribers = []
        self.lock = threading.Lock()
        self.httpd = None
        self.url = "http://localhost:%d/" % port
        self.readers = {}                           # name -> func(query dict) -> JSON data   (GET /api/data/<name>)

    # An internal error while answering `what` (a request): said through problem(), with its traceback.
    def on_error(self, what, e):
        self.problem("internal error on %s: %s - please report it" % (what, error_text(e)), error_trace(e, always=True))

    # Start listening on 127.0.0.1:port (0 = a free port); returns the page's URL. Raises OSError if the port is taken.
    def start(self):
        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _make_handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = "http://localhost:%d/" % self.port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.url

    # Add read-only data for the page: GET /api/data/<name>?... -> func(query) (a dict of the query's values).
    def add_reader(self, name, func):
        self.readers[name] = func

    # Keep one event in the history and push it to every open page.
    def publish(self, item):
        with self.lock:
            self.history.append(item)
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    # Tell open pages the run is over ("bye"), give them a moment, then stop serving.
    def stop(self, linger=0.6):
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        if self.httpd is not None:
            time.sleep(linger if subs else 0)
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    # How many pages follow the live stream right now (the control center closes when none has for a while).
    def viewers(self):
        with self.lock:
            return len(self.subscribers)

    # A page opened the live stream: its own queue.
    def subscribe(self):
        q = queue.Queue(maxsize=5000)
        with self.lock:
            self.subscribers.append(q)
        return q

    # A page went away.
    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    # The folder a URL path is served from, and the path inside it: /docs/... from the docs folder (None if there's none), the rest from web/.
    def _where(self, path):
        rel = path.lstrip("/") or "index.html"
        if rel == "docs" or rel.startswith("docs/"):
            return self.docs_dir, rel[len("docs/"):] or "index.html"
        return self.web_dir, rel

    # The file under web/ (or, for /docs/..., under the docs folder) for a URL path, or None (anything outside them, hidden
    # files, unknown types; /docs/ with no docs folder).
    def static_file(self, path):
        folder, rel = self._where(path)
        if not folder or any(part in ("", ".", "..") or part.startswith(".") for part in rel.split("/")):
            return None
        full = os.path.realpath(os.path.join(folder, *rel.split("/")))
        root = os.path.realpath(folder)
        if not full.startswith(root + os.sep) or os.path.splitext(full)[1] not in CONTENT_TYPES or not os.path.isfile(full):
            return None
        return full

# ##############################################################################################################################################################
# ##  HTTP HANDLER                                                                                                                                            ##
# ##############################################################################################################################################################

# The request handler class bound to one DashboardServer.
def _make_handler(dash):

    # Answers the page and its files, the JSON endpoints, the live stream and the controls.
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Server header value.
        def version_string(self):
            return "nx-spoof-dashboard"

        # Silence the default per-request output.
        def log_message(self, *args):
            pass

        # Refuse requests not addressed to localhost (blocks DNS-rebinding tricks).
        def _host_ok(self):
            if self.headers.get("Host", "") in ("localhost:%d" % dash.port, "127.0.0.1:%d" % dash.port):
                return True
            self._send(403, b"forbidden", "text/plain")
            return False

        # Send a complete response.
        def _send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        # Send JSON.
        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        # GET: the page and its files, state, history, live events.
        def do_GET(self):
            if not self._host_ok():
                return
            path, _, query = self.path.partition("?")
            if path.startswith("/api/data/"):
                return self._data(path[len("/api/data/"):], query)
            if path == "/api/state":
                try:
                    return self._json(200, dash.get_state())
                except Exception as e:
                    dash.on_error("/api/state", e)
                    return self._json(500, {"error": str(e)})
            if path == "/api/history":
                with dash.lock:
                    return self._json(200, list(dash.history))
            if path == "/api/events":
                return self._stream()
            full = dash.static_file("/index.html" if path == "/" else path)
            if full is None:
                return self._send(404, b"not found", "text/plain")
            with open(full, "rb") as f:
                data = f.read()
            ext = os.path.splitext(full)[1]
            if ext == ".html":
                if not path.startswith("/docs"):                 # only the dashboard's own page gets the token
                    data = data.replace(b"{{TOKEN}}", dash.token.encode())
                return self._send(200, data, CONTENT_TYPES[ext], {"Content-Security-Policy": CSP})
            return self._send(200, data, CONTENT_TYPES[ext])

        # POST /api/action/<name>: a control, protected by the run's token.
        def do_POST(self):
            if not self._host_ok():
                return
            if self.headers.get("X-NXSPOOF-Token") != dash.token:
                return self._json(403, {"error": "bad or missing token"})
            if not self.path.startswith("/api/action/"):
                return self._json(404, {"error": "not found"})
            payload, refused = self._body()
            if refused:
                return self._json(*refused)
            name = self.path[len("/api/action/"):]
            try:
                return self._json(200, dash.controls.call(name, payload))
            except KeyError:
                return self._json(404, {"error": "no control called '%s'" % name})
            except (ControlError, ValueError) as e:          # your input or the situation: say why
                return self._json(400, {"error": str(e)})
            except Exception as e:                           # a real bug
                dash.on_error("control %s" % name, e)
                return self._json(500, {"error": "internal error: %s" % e})

        # A control's request body -> (the JSON object, None), or (None, (status, error)) when it's too big or not an object.
        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None, (400, {"error": "bad Content-Length"})
            if not 0 <= n <= MAX_BODY:
                return None, (413, {"error": "the request is too big (%d bytes at most)" % MAX_BODY})
            try:
                payload = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                return None, (400, {"error": "body must be JSON"})
            if not isinstance(payload, dict):
                return None, (400, {"error": "body must be a JSON object"})
            return payload, None

        # GET /api/data/<name>: read-only data from a reader (404 if there's no such reader, 400 for a bad query).
        def _data(self, name, query):
            func = dash.readers.get(name)
            if func is None:
                return self._json(404, {"error": "no data called '%s'" % name})
            try:
                return self._json(200, func(dict(urllib.parse.parse_qsl(query))))
            except (ControlError, ValueError) as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                dash.on_error("data %s" % name, e)
                return self._json(500, {"error": "internal error: %s" % e})

        # Server-Sent Events: each event as it happens, a ping every PING seconds, "bye" at the end. A closed page is noticed
        # within LOOK seconds, when the browser hangs up (the control center closes once none is left, app/pagewatch.py).
        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = dash.subscribe()
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                quiet = 0.0
                while True:
                    try:
                        item = q.get(timeout=LOOK)
                    except queue.Empty:
                        if self._hung_up():
                            break
                        quiet += LOOK
                        if quiet >= PING:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            quiet = 0.0
                        continue
                    quiet = 0.0
                    if item is None:
                        self.wfile.write(b"event: bye\ndata: {}\n\n")
                        self.wfile.flush()
                        break
                    self.wfile.write(b"data: " + json.dumps(item).encode() + b"\n\n")
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                dash.unsubscribe(q)
                self.close_connection = True

        # Whether the page hung up: its end of the connection closed (a live stream's page sends nothing after asking, so
        # anything to read means it's gone; the close of a tab, a window or the browser all show here at once).
        def _hung_up(self):
            try:
                if not select.select([self.connection], [], [], 0)[0]:
                    return False
                return self.connection.recv(1, socket.MSG_PEEK) == b""
            except OSError:
                return True

    return Handler
