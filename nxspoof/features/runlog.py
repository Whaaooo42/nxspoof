"""
nxspoof.features.runlog - what a run did, from its records: the real-run checklist and a short description.

Used by the dashboard's log pane (its run picker) (every run in logs/, its summary, its checklist, its full log) and by
tools/verify_run.py (menu 6 → 1). Only reads log files; never changes anything.

A run's records come from its structured twin, logs/<run>.jsonl (output/logfile.py RecordSink): every event
with its kind (run.ready, console.address, service.up, run.stopping...), level, console and data. The checks
read those, never the wording, so a log line's text can change without breaking them. Runs logged before the
twin existed (2026-11-15) are read from their text by features/textlog.py, the one place that still knows
the text log's patterns (for those old files, whose wording never changes again).

  list_runs(logs_dir)          [run info], newest first: date, times, duration, consoles, how it stopped
  latest_run(logs_dir)         the newest run, and whether it stopped cleanly (the dashboard's crash check)
  run_details(logs_dir, name)  one run: its info, its checklist items, its log lines
  check_run(log_path)          the checklist (docs/real-runs.html) for one run: [Item(status, title, evidence)]
  check_records(records)       the same, from records already read
"""

import datetime
import json
import os
import re
import time

from nxspoof.features.textlog import STAMP_RX, plain_lines, records_from_text, stop_path  # noqa: F401  (old runs; tools/verify_run.py)
from nxspoof.core.events import PROBLEM_LEVELS  # noqa: F401  (runlog.PROBLEM_LEVELS for its readers)
from nxspoof.output.logfile import RUN_FILE_RX as RUN_LOG_RX, records_path

STOP_PATHS = {"dashboard": "the dashboard's Stop", "stop command": "nx_spoof.py stop (menu 2)",
              "SIGTERM": "a Stop (the dashboard's, menu 2 or nx_spoof.py stop)",
              "ctrl-c": "Ctrl+C", "SIGHUP": "closing the Terminal window",    # ctrl-c, SIGHUP: runs from before D30
              "slept": "the Mac slept: a sleep ends the run"}
MAX_LOG_LINES = 5000                                                                           # a run's log as shown on the page
LOOK_BACK = 10                                                                                 # runs looked at for the last hotspot name

# ##############################################################################################################################################################
# ##  RECORDS                                                                                                                                                 ##
# ##############################################################################################################################################################

# A record's line as the text log shows it (without the time): "TAG   text", or the text alone.
def line_of(r):
    tech = r.get("tech") or ""
    return ("%-5s %s" % (r["tag"], tech)) if r.get("tag") else tech

# The records of a run: its structured twin if there is one, else read from its text log ([] if neither can be read).
# last_run.log works too: the twin is found next to the file it points to.
def load_records(log_path):
    log_path = os.path.realpath(log_path)
    try:
        with open(records_path(log_path), encoding="utf-8") as f:
            records = [json.loads(l) for l in f if l.strip()]
        for r in records:
            r.setdefault("t", time.strftime("%H:%M:%S", time.localtime(r.get("ts", 0))))
        return records
    except (OSError, ValueError):
        return records_from_text(read_log(log_path) or "")

# Only the records with text (what the text log shows), in order.
def lines_only(records):
    return [r for r in records if r.get("tech") is not None]

# ##############################################################################################################################################################
# ##  THE CHECKLIST (PURE)                                                                                                                                    ##
# ##############################################################################################################################################################

# One checklist item's result: status "pass" / "fail" / "skip", what it checks, the evidence lines.
class Item(object):
    __slots__ = ("status", "title", "evidence")

    # evidence: a few log lines (or facts) that show the result.
    def __init__(self, status, title, evidence=None):
        self.status, self.title, self.evidence = status, title, list(evidence or [])

    # Short form for tests.
    def __repr__(self):
        return "Item(%s, %r)" % (self.status, self.title)

    # A plain-dict copy (for the dashboard).
    def as_dict(self):
        return {"status": self.status, "title": self.title, "evidence": list(self.evidence)}

# The records of these kinds (and matching data, if given), up to `limit`.
def of_kind(records, kinds, limit=3, **data):
    kinds = (kinds,) if isinstance(kinds, str) else kinds
    found = [r for r in records if r.get("kind") in kinds and all(r.get("data", {}).get(k) == v for k, v in data.items())]
    return found[:limit]

# Their lines, for evidence.
def evidence(records):
    return [line_of(r) for r in records]

# How the run was stopped (a STOP_PATHS key), or None if it didn't stop cleanly.
def stop_how(records):
    stopping = of_kind(records, "run.stopping", 1)
    if not stopping:
        return None
    how = stopping[0].get("data", {}).get("how") or "ctrl-c"
    return how if how in STOP_PATHS else "ctrl-c"

# Consoles that left the Wi-Fi and joined again later in the same run (by name).
def came_back(records):
    left, back, names = set(), set(), {}
    for r in of_kind(records, ("console.joined", "console.left"), limit=None):
        who = r.get("mac") or r.get("data", {}).get("name")
        names.setdefault(who, r.get("data", {}).get("name") or who)
        if r["kind"] == "console.left":
            left.add(who)
        elif who in left:
            back.add(who)
    return sorted(names[w] for w in back)

# Add one checklist item: passed if `ok`, else failed (or skipped when `optional`), with its proof lines.
def add_item(items, title, ok, proof, optional=False, missing=None):
    items.append(Item("pass" if ok else ("skip" if optional else "fail"), title, proof if ok else (missing or proof)))

# The start: which code wrote it, the self-test, the firewall.
def start_items(records, items):
    version = of_kind(records, "run.version", 1)
    items.append(Item("pass", "written by the new code (nxspoof %s)" % version[0]["data"]["version"]) if version
                 else Item("skip", "written by the previous code (before the switch to the nxspoof package)"))
    checks, ready = of_kind(records, "selftest.check", 10), of_kind(records, "run.ready", 1)
    failed = [r for r in checks if not r.get("data", {}).get("passed")]
    add_item(items, "self-test: 6 checks passed, then READY", len(checks) - len(failed) == 6 and ready, evidence(ready),
             missing=evidence(failed) or ["no READY line"])
    firewall = of_kind(records, "firewall.on", 1)
    trouble = [r for r in lines_only(records) if r.get("tag") == "FIREWALL" or r.get("kind") == "firewall.failed"
               or (r.get("tag") == "NOTE" and "firewall" in (r.get("tech") or ""))][:3]
    add_item(items, "firewall loaded", firewall, evidence(firewall), missing=evidence(trouble) or ["no FIREWALL line"])

# The consoles: an address, the online check, blocked lookups, FTP, leaving and coming back.
def console_items(records, items):
    dhcp = of_kind(records, "console.address", 2, via="dhcp")
    add_item(items, "a console got an address (DHCP)", dhcp, evidence(dhcp), missing=["no 'DHCP  gave address' line: no console joined the hotspot"])
    passed = [r for r in records if r.get("tag") == "HTTP" and r.get("data", {}).get("passed")][:2]
    add_item(items, "a console passed its online check", passed, evidence(passed), missing=["no 'HTTP ... -> 200 ok' line from a console"])
    blocked = [r for r in of_kind(records, "console.blocked", limit=None) if r.get("tag") == "DNS"][:2]
    add_item(items, "a console's lookups were blocked", blocked, evidence(blocked), optional=True,
             missing=["no blocked lookup from a console in this run"])
    up, down = of_kind(records, "service.up", 1), of_kind(records, "service.down", 1)
    add_item(items, "FTP server seen going up and down", up and down, evidence(up + down), optional=True,
             missing=evidence(up + down) or ["no FTP server started on a console in this run"])
    back = came_back(records)
    add_item(items, "a console left the Wi-Fi and came back", back, ["%s came back" % b for b in back], optional=True,
             missing=["no console left and rejoined in this run"])

# The end: a clean stop, the firewall removed, the summary, sharing off, "Stopped.", and no problems on the way.
def stop_items(records, items):
    how, stopping = stop_how(records), of_kind(records, ("run.stop_requested", "run.stopping"), 2)
    add_item(items, "stopped cleanly (%s)" % STOP_PATHS.get(how, how) if how else "stopped cleanly", how is not None, evidence(stopping),
             missing=["no 'Stopping...' line: the run didn't end cleanly"])
    removed = of_kind(records, "firewall.removed", 1)
    add_item(items, "firewall rules removed at stop", removed, evidence(removed), missing=["no 'pf    firewall rules removed' line"])
    summary = of_kind(records, "summary.run", 1)
    add_item(items, "summary written", summary, evidence(summary))
    sharing = of_kind(records, "sharing.off", 1)
    add_item(items, "Internet Sharing turned off", sharing and sharing[0].get("data", {}).get("ok"), evidence(sharing),
             missing=evidence(sharing) or ["no SHARING ... OFF line"])
    last = lines_only(records)[-1:]
    add_item(items, "ended with 'Stopped.'", last and last[0].get("kind") == "run.stopped", evidence(last))
    problems = [r for r in lines_only(records) if r.get("level") in PROBLEM_LEVELS]
    add_item(items, "no FATAL / PROBLEM / WARNING lines", not problems, ["none"], missing=evidence(problems[:5]))

# The checklist for one run's records -> [Item].
def check_records(records):
    items = []
    for group in (start_items, console_items, stop_items):
        group(records, items)
    return items

# The checklist for one run's text log (old runs, and tests) -> [Item].
def check_log(text):
    return check_records(records_from_text(text))

# The checklist for one run, from its records (its twin when there is one) -> [Item].
def check_run(log_path):
    return check_records(load_records(log_path))

# ##############################################################################################################################################################
# ##  THE RUNS IN logs/                                                                                                                                       ##
# ##############################################################################################################################################################

# Read a log file (None if it can't be read).
def read_log(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None

# A short description of one run from its file name and records.
def run_info(name, records):
    m = RUN_LOG_RX.match(name)
    stamps = [r["t"] for r in lines_only(records) if r.get("t")]
    summary = of_kind(records, "summary.run", 1)
    data = summary[0].get("data", {}) if summary else {}
    duration = data.get("duration") or (str(datetime.timedelta(seconds=int(data["seconds"]))) if "seconds" in data else "")
    consoles = [(r["data"]["name"], r.get("mac") or r["data"].get("mac")) for r in of_kind(records, "summary.console", limit=None)]
    how = stop_how(records)
    items = check_records(records)
    return {"name": name, "date": "%s-%s-%s" % m.group(1, 2, 3) if m else "", "dry_run": bool(m and m.group(7)),
            "start": stamps[0] if stamps else "", "end": stamps[-1] if stamps else "", "duration": duration, "consoles": consoles,
            "stopped": STOP_PATHS.get(how, how) if how else "did not stop cleanly", "new_code": bool(of_kind(records, "run.version", 1)),
            "checks": {s: sum(1 for i in items if i.status == s) for s in ("pass", "fail", "skip")}}

# A run as the live log's own events, so the page draws a past run like the live one (casual or technical, either order):
# [{"t", "ts", "tag", "tech", "casual", "problem", "mac", "tech_names", "casual_names"}], details as lines of their own.
# Marks are as the run wrote them (an old run's casual lines have none); an old run read from its text is technical only.
def run_events(records, text):
    if not any("ts" in r for r in records):
        return [old_event(l) for l in text.splitlines()[-MAX_LOG_LINES:]]
    out = []
    for r in records:
        out += record_events(r)
    return out[-MAX_LOG_LINES:]

# One record as the live log's events (its line, then its details); [] for a fact with nothing to show. The marks are in
# Python's characters (the dashboard turns them into the page's). The dashboard follows a running run this way too.
def record_events(r):
    tech = line_of(r) if r.get("tech") is not None else None
    if tech is None and not r.get("casual"):
        return []
    t = r.get("t") or time.strftime("%H:%M:%S", time.localtime(r.get("ts", 0)))
    base = {"t": t, "ts": r.get("ts", 0), "problem": r.get("level") in PROBLEM_LEVELS, "mac": r.get("mac")}
    out = [dict(base, kind=r.get("kind"), tag=r.get("tag", ""), tech=tech, casual=r.get("casual"), tech_names=r.get("marks", []) if tech else [],
                casual_names=r.get("casual_marks", []))]
    return out + [dict(base, tag="", tech="      " + d, casual=None, tech_names=[], casual_names=[]) for d in r.get("details", [])]

# One line of an old run's text log as a page event ("[HH:MM:SS] TAG   text"; technical only).
def old_event(line):
    m = re.match(r"^\[(\d\d:\d\d:\d\d)\] (.*)$", line)
    t, tech = (m.group(1), m.group(2)) if m else ("", line)
    return {"t": t, "ts": 0, "tag": "", "tech": tech, "casual": None, "problem": False, "mac": None, "tech_names": [], "casual_names": []}

# Every run in a logs folder, newest first (real runs only: not setup, uninstall or dashboard logs).
def list_runs(logs_dir):
    names = sorted((n for n in (os.listdir(logs_dir) if os.path.isdir(logs_dir) else []) if RUN_LOG_RX.match(n)), reverse=True)
    out = []
    for name in names:
        path = os.path.join(logs_dir, name)
        if os.path.isfile(path):
            out.append(run_info(name, load_records(path)))
    return out

# One run in detail: its info, its checklist, its log lines; None if there's no such run (only real run names are accepted).
def run_details(logs_dir, name):
    if not RUN_LOG_RX.match(name or ""):
        return None
    path = os.path.join(logs_dir, name)
    text = read_log(path)
    if text is None:
        return None
    records = load_records(path)
    return {"info": run_info(name, records), "items": [i.as_dict() for i in check_records(records)], "events": run_events(records, text)}

# The newest real run in a logs folder: {"name", "time" (its log's modification time), "clean" (it reached its end: a
# FATAL one too, since every ending cleans up and says Stopped.; or its keeper cleaned up after it: run.keeper_cleanup)},
# or None when there's none. Reads only that run's records (the dashboard asks every few seconds).
def latest_run(logs_dir):
    names = sorted((n for n in (os.listdir(logs_dir) if os.path.isdir(logs_dir) else []) if RUN_LOG_RX.match(n)), reverse=True)
    if not names:
        return None
    path = os.path.join(logs_dir, names[0])
    try:
        when = os.path.getmtime(path)
    except OSError:
        return None
    records = load_records(path)
    keeper = of_kind(records, "run.keeper_cleanup", 1, complete=True)        # D24: its root part cleaned up after it
    return {"name": names[0], "time": when, "clean": bool(of_kind(records, "run.stopped", 1) or keeper)}

# The hotspot's name in the newest run that said it (its run.hotspot fact), leaving out the run named skip (the current
# one); None if none of the last LOOK_BACK runs did.
def last_hotspot_name(logs_dir, skip=None):
    names = sorted((n for n in (os.listdir(logs_dir) if os.path.isdir(logs_dir) else []) if RUN_LOG_RX.match(n) and n != skip), reverse=True)
    for name in names[:LOOK_BACK]:
        said = of_kind(load_records(os.path.join(logs_dir, name)), "run.hotspot", 1)
        if said and said[0].get("data", {}).get("name"):
            return said[0]["data"]["name"]
    return None

# The new code's run logs in logs/, oldest first: [(name, stop path or None)].
def new_code_runs(logs_dir):
    out = []
    for name in sorted(os.listdir(logs_dir)) if os.path.isdir(logs_dir) else []:
        if not RUN_LOG_RX.match(name):
            continue
        records = load_records(os.path.join(logs_dir, name))
        if of_kind(records, "run.version", 1):
            out.append((name, stop_how(records)))
    return out
