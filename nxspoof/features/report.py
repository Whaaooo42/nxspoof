"""
nxspoof.features.report - the problem report: what NXSPOOF knows about a problem on this Mac, in one Markdown text made to
be pasted into a GitHub issue (🐞 Report a problem on the dashboard, nx_spoof.py report, menu 6 → 4; app/reporting.py
gathers what goes in, this file writes it).

What goes in, each part from its source (records, never a log's wording):
  this Mac     what kind of Mac (macos/machine.py), NXSPOOF's version, where its folders are
  setup        setup's checks now: ready or not, and why
  runs         the last RUNS runs: how each went (start, end, how it stopped, its warnings and problems), the lines that
               frame it (version, this Mac, settings and consoles, hotspot, firewall, READY, the stop) and every warning or problem
               with the CONTEXT lines before it and its details (a traceback, when there is one)
  other logs   the newest setup / clean up / uninstall / dashboard log, when it has a warning or a problem
  full         when asked: the whole technical log of the newest run, folded

Everything personal is replaced before anyone sees it (Scrub): console and owner names, the hotspot's name, the user's
and the Mac's names, the home folder, e-mail addresses, and MAC addresses but their maker part (48:f1:eb is Nintendo).
The report is shown before it's copied: whoever shares it reads it first.
"""

import os
import re
import time

from nxspoof.features import runlog
from nxspoof.output.logfile import LOG_FILE_RX, records_path

RUNS = 3                                     # runs in a report
CONTEXT = 3                                  # lines kept before each warning or problem
MAX_LINES = 160                              # lines shown per log (the newest kept when there are more)
MAX_CHARS = 60000                            # a GitHub issue holds 65,536 characters: older parts go first
OTHER_LOGS = ("setup", "cleanup", "uninstall", "dashboard")
FRAMING = ("run.version", "run.mac", "run.privileges", "run.preflight", "run.hotspot", "firewall.on", "firewall.failed",
           "firewall.partial", "run.ready", "run.stop_requested", "run.stopping", "summary.run", "sharing.off", "run.stopped")
NOT_PERSONAL = {"switch", "switch 1", "switch 2", "nintendo switch", "nintendo switch 2", "console", "unknown console", "nxspoof",
                "mac", "friend's switch"}                    # model names and the suggested entry's name stay as they are
MAC_RX = re.compile(r"(?<![0-9A-Fa-f:])([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){2})(?::[0-9A-Fa-f]{2}){3}(?![0-9A-Fa-f:])")
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?![\w.-])")    # a real domain at the end: python@3.15 isn't one
LEVEL_MARK = {"warn": "⚠️", "problem": "😕", "fatal": "⛔"}

# ##############################################################################################################################################################
# ##  MAKING IT IMPERSONAL                                                                                                                                    ##
# ##############################################################################################################################################################

# Replaces what's personal in a text: names (consoles, owners, the hotspot, the Mac, you), the home folder, e-mail
# addresses, and MAC addresses but their maker part. The same name or address always gets the same stand-in.
class Scrub(object):
    # consoles: names that become "console 1", "console 2"...; people: names that become "<owner>"; words: {word: stand-in}.
    def __init__(self, consoles=(), people=(), words=None):
        table = {}
        for name in consoles:
            self._add(table, name, "console %d" % (len(set(v for v in table.values() if v.startswith("console "))) + 1))
        for name in people:
            self._add(table, name, "<owner>")
        for word, stand_in in (words or {}).items():
            self._add(table, word, stand_in)
        self.table = table
        words_rx = "|".join(re.escape(w) for w in sorted(table, key=len, reverse=True))
        self.rx = re.compile(r"(?<!\w)(?:%s)(?!\w)" % words_rx, re.IGNORECASE) if table else None
        self.macs = {}

    # One word to replace (case doesn't matter); the first stand-in given for a word wins. Generic and tiny names are kept.
    @staticmethod
    def _add(table, word, stand_in):
        word = (word or "").strip()
        if len(word) >= 3 and word.lower() not in NOT_PERSONAL and word.lower() not in table:
            table[word.lower()] = stand_in

    # A MAC address with its maker part kept: "48:f1:eb:xx:xx:01" (the number tells two consoles apart).
    def _mac(self, m):
        full = m.group(0).lower()
        if full not in self.macs:
            self.macs[full] = len(self.macs) + 1
        return "%s:xx:xx:%02d" % (m.group(1).lower(), self.macs[full])

    # The text, impersonal (e-mail addresses first: a name inside one mustn't leave the rest of it behind).
    def __call__(self, text):
        text = EMAIL_RX.sub("<e-mail>", text or "")
        if self.rx:
            text = self.rx.sub(lambda m: self.table[m.group(0).lower()], text)
        return MAC_RX.sub(self._mac, text)

# The console names a log's records carry (a console known then, maybe not now), for Scrub: the names its lines mark (a mark
# whose "who" is a MAC address) and the names its console facts give.
def names_in(records):
    kinds = ("console.joined", "console.left", "summary.console")
    out = [r["data"]["name"] for r in records if r.get("kind") in kinds and isinstance(r.get("data", {}).get("name"), str)]
    for r in runlog.lines_only(records):
        line = runlog.line_of(r)
        out += [line[start:end] for start, end, who in r.get("marks") or [] if MAC_RX.fullmatch(str(who))]
    return out

# The hotspot names a log's records carry (the run's "hotspot" line), for Scrub.
def hotspots_in(records):
    return [r["data"]["name"] for r in records if r.get("kind") == "run.hotspot" and r.get("data", {}).get("name")]

# ##############################################################################################################################################################
# ##  THE LOGS                                                                                                                                                ##
# ##############################################################################################################################################################

# The logs that have records, newest first, as (name, kind): kind "run" or one of OTHER_LOGS.
def logs_with_records(logs_dir):
    try:
        names = sorted(os.listdir(logs_dir), reverse=True)
    except OSError:
        return []
    out = []
    for name in names:
        m = LOG_FILE_RX.match(name)
        if m and (m.group(2) or "run") in ("run",) + OTHER_LOGS and os.path.isfile(records_path(os.path.join(logs_dir, name))):
            out.append((name, m.group(2) or "run"))
    return out

# The logs a report shows, with their records: the last `runs` runs, and the newest of each other kind when it has a
# problem -> [(name, kind, records)].
def chosen_logs(logs_dir, runs=RUNS):
    chosen, seen = [], set()
    for name, kind in logs_with_records(logs_dir):
        if kind == "run" and sum(1 for c in chosen if c[1] == "run") < runs:
            chosen.append((name, kind, runlog.load_records(os.path.join(logs_dir, name))))
        elif kind != "run" and kind not in seen:
            seen.add(kind)
            records = runlog.load_records(os.path.join(logs_dir, name))
            if problems_in(records):
                chosen.append((name, kind, records))
    return chosen

# The warnings and problems in a log's records.
def problems_in(records):
    return [r for r in runlog.lines_only(records) if r.get("level") in runlog.PROBLEM_LEVELS]

# A record as its log shows it: "[16:17:12] LOG   text", then its details indented.
def record_lines(r, scrub):
    first = "[%s] %s" % (r.get("t", "--:--:--"), scrub(runlog.line_of(r)))
    return [first] + ["           " + scrub(d) for d in r.get("details") or []]

# The lines a log shows in a report: its framing lines (framing=True) and each warning or problem with the CONTEXT lines
# before it; "…" where lines are left out; at most MAX_LINES (the newest).
def picked_lines(records, scrub, framing=True):
    lines = runlog.lines_only(records)
    keep = set()
    for i, r in enumerate(lines):
        if r.get("level") in runlog.PROBLEM_LEVELS:
            keep.update(range(max(0, i - CONTEXT), i + 1))
        elif framing and (r.get("kind") in FRAMING or r.get("tag") == "SETTINGS"):
            keep.add(i)
    out, last = [], -1
    for i in sorted(keep):
        if i > last + 1:
            out.append("…")
        out += record_lines(lines[i], scrub)
        last = i
    if lines and last < len(lines) - 1:
        out.append("…")
    return out[-MAX_LINES:]

# One log's title line: "Run 2026-11-24 16:17:12 → 16:22:26 (0:05:13), stopped from the dashboard's Stop · 2 warnings".
def log_title(name, kind, records):
    info = runlog.run_info(name, records)
    when = "%s %s" % (name[:10], info["start"]) + (" → %s" % info["end"] if info["end"] else "")
    counts = {}
    for r in problems_in(records):
        counts[r["level"]] = counts.get(r["level"], 0) + 1
    said = ", ".join("%d %s" % (n, {"warn": "warning", "problem": "problem", "fatal": "fatal"}[lvl] + ("s" if n > 1 else ""))
                     for lvl, n in sorted(counts.items(), key=lambda kv: runlog.PROBLEM_LEVELS.index(kv[0])))
    if kind == "run":
        how = info["stopped"] if not info["dry_run"] else "a dry run"
        return "Run %s%s, %s · %s" % (when, " (%s)" % info["duration"] if info["duration"] else "", how, said or "no problems")
    return "%s log %s · %s" % (kind.capitalize(), when, said or "no problems")

# ##############################################################################################################################################################
# ##  THE REPORT                                                                                                                                              ##
# ##############################################################################################################################################################

# The report's top: this Mac, NXSPOOF, and setup's checks.
def head_lines(about, setup_rows, scrub, now):
    lines = ["## NXSPOOF problem report", "",
             "> Made by NXSPOOF on %s. Console names, MAC addresses (all but their maker part), the hotspot's name, your name "
             "and your Mac's name are replaced. Please read it before you share it." % time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
             "", "**What happened:** _(in your words: what you did, what you expected, what you saw)_", "",
             "**This Mac:** %s" % scrub(about.get("mac", "?")),
             "**NXSPOOF:** %s · folder %s · data folder %s" % (about.get("version", "?"), scrub(about.get("program", "?")), scrub(about.get("data", "?"))),
             "", "### This Mac's setup", ""]
    marks = {"ok": "✅", "info": "ℹ️", "fixable": "🛠️", "manual": "✋"}
    for row in setup_rows or []:
        lines.append("- %s %s: %s" % (marks.get(row.get("state"), "•"), row.get("title", row.get("name", "?")), scrub(row.get("tech") or row.get("casual") or "")))
    if not setup_rows:
        lines.append("- (setup's checks couldn't run)")
    return lines

# One log's block: its title, then its picked lines in a text block.
def log_block(name, kind, records, scrub):
    return ["", "#### " + scrub(log_title(name, kind, records)), "", "```text"] + picked_lines(records, scrub, framing=kind == "run") + ["```"]

# The whole technical log of the newest run, folded (only when asked: `full`).
def full_block(name, records, scrub):
    lines = [l for r in runlog.lines_only(records) for l in record_lines(r, scrub)]
    return ["", "<details><summary>The whole technical log of %s (%d lines)</summary>" % (name, len(lines)), "", "```text"] + \
        lines[-runlog.MAX_LOG_LINES:] + ["```", "", "</details>"]

# The Scrub for these logs: the names given, plus the console and hotspot names the logs' records carry.
def scrub_for(logs, consoles=(), people=(), words=None):
    seen_names = [n for _, _, records in logs for n in names_in(records)]
    hotspots = {h: "<hotspot>" for _, _, records in logs for h in hotspots_in(records)}
    return Scrub(list(consoles) + seen_names, people, dict(words or {}, **hotspots))

# The parts put together; older log blocks go first (said) while it's longer than MAX_CHARS.
def assemble(head, blocks, tail):
    blocks, left_out = list(blocks), []
    while True:
        text = "\n".join(head + ["", "### Logs (newest first)"] + [l for b in blocks for l in b] + left_out + tail) + "\n"
        if len(text) <= MAX_CHARS or len(blocks) <= 1:
            return text
        blocks.pop()
        left_out = ["", "_(older logs left out: too long)_"]

# The report as Markdown. logs_dir: logs/; about: {"mac", "version", "program", "data"}; setup_rows: setup's checks
# (app/starter.py mac_checks); consoles, people, words: what Scrub replaces (the names in the logs are added); full: the
# newest run's whole log too.
def build(logs_dir, about, setup_rows, consoles=(), people=(), words=None, full=False, now=None):
    logs = chosen_logs(logs_dir)
    scrub = scrub_for(logs, consoles, people, words)
    head = head_lines(about, setup_rows, scrub, time.time() if now is None else now)
    blocks = [log_block(name, kind, records, scrub) for name, kind, records in logs] or \
        [["", "_No logs with records yet: run NXSPOOF once (or setup), then make the report again._"]]
    newest_run = next(((name, records) for name, kind, records in logs if kind == "run"), None)
    return assemble(head, blocks, full_block(newest_run[0], newest_run[1], scrub) if full and newest_run else [])
