"""
nxspoof.dashboard.center - "is a dashboard open, and where?": DASH_FILE in the runtime folder (the control center,
app/center.py, keeps it).

  remembered_port(folder)   the dashboard's port: last time's (8800 the first time). Not a setting (D30): the same port
                            each time, so the page keeps its remembered choices (they're per address); another one only
                            when something else holds it, and that one is remembered from then on
  opened(folder, port, give) / closed(folder)    while open the file also has the dashboard's process id; closed keeps the port
  find(folder)              the open dashboard as the file's dict, or None (closed, or nobody answers: a crash's leftover)
  http_json(port, path...)  one request to an NXSPOOF page server on this Mac, with the same Host check as the page

Runs never talk to it (D30: the dashboard finds the run, from the run's files: app/runfollow.py).
"""

import json
import os
import urllib.error
import urllib.request

DASH_FILE = "dash.json"
DEFAULT_PORT = 8800

# ##############################################################################################################################################################
# ##  THE DASHBOARD'S FILE                                                                                                                                    ##
# ##############################################################################################################################################################

# The file as a dict ({} when missing or unreadable).
def read_file(folder):
    try:
        with open(os.path.join(folder, DASH_FILE), encoding="utf-8") as f:
            info = json.load(f)
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}

# Write the file (whole, then moved into place).
def _write(folder, info, give=None):
    path = os.path.join(folder, DASH_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.replace(path + ".tmp", path)
    if give:
        give(path)

# The dashboard's port: the one in the file, DEFAULT_PORT when there's none.
def remembered_port(folder):
    port = read_file(folder).get("port")
    return port if isinstance(port, int) and 1024 <= port <= 65535 else DEFAULT_PORT

# The dashboard is open on `port`: remembered, with its process id.
def opened(folder, port, give=None):
    _write(folder, {"port": port, "pid": os.getpid()}, give)

# The dashboard has closed: its port stays remembered.
def closed(folder):
    info = read_file(folder)
    if info:
        try:
            _write(folder, {"port": info.get("port", DEFAULT_PORT)})
        except OSError:
            pass

# ##############################################################################################################################################################
# ##  TALKING HTTP ON THIS MAC                                                                                                                                ##
# ##############################################################################################################################################################

# One request to an NXSPOOF server on 127.0.0.1:port -> (status, JSON answer). Raises OSError if nobody answers.
def http_json(port, path, data=None, token=None, timeout=3):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=body, method="POST" if body is not None else "GET",
                                 headers={"Host": "localhost:%d" % port, "Content-Type": "application/json", "X-NXSPOOF-Token": token or ""})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {"error": "HTTP %d" % e.code}
        finally:
            e.close()

# The open dashboard as its file's dict, or None (closed, or nobody answers: a leftover from a crash).
def find(folder):
    info = read_file(folder)
    if not info.get("pid"):
        return None
    try:
        status, answer = http_json(int(info["port"]), "/api/data/hello", timeout=1.5)
    except (OSError, ValueError, KeyError):
        return None
    return info if status == 200 and answer.get("center") else None
