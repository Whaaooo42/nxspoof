"""
nxspoof.core.events - the event bus: everything that is said or happens goes through here.

An Event carries a technical text (for the log file and the technical Terminal style), an optional
casual text (for the casual style and the dashboard's casual view), a level, and machine-readable
facts. Texts built with core/text.py compose() carry their console references (tech_names,
casual_names), which outputs color exactly. Outputs (Terminal, log file, dashboard) are *sinks*; things that react (the casual story,
service watchers, ...) are *listeners*.

  bus.log(tag, tech, casual=None, level="info", ...)   a log line, e.g. bus.log("DNS", "listening on ...")
  bus.log(..., fact="console.left", seconds=...)       a log line that is also a fact: ONE event, so the line
                                                       always comes before what listeners do about it
  bus.say(casual)                                      a casual-only line (never written to the log file)
  bus.fact(kind, mac=..., **data)                      something happened, no text (e.g. "console.connected")

When something both deserves a line and matters to listeners, it is one event (fact=...), never a line
plus a separate fact: two events can end up in either order, one event can't. Every event (line or fact)
also goes to the run's structured log (output/logfile.py RecordSink), so readers never parse the text.

Ordering: sinks run in emit order under one lock, so the Terminal, the log file and the dashboard
always show the same sequence. Listeners run after the lock is released. A sink must never emit.
"""

import sys
import threading
import time

from nxspoof.core.fmt import error_text, error_trace
from nxspoof.core.text import spans_of

LEVELS = ("info", "warn", "problem", "fatal")
PROBLEM_LEVELS = LEVELS[1:]                     # warnings and worse: what casual mode always shows, what a report collects
TAG_WIDTH = 5                                   # "%-5s %s" -> "DNS   x", "HTTP  x", "SETTINGS x"

# One thing that was said or happened.
class Event(object):
    __slots__ = ("kind", "tag", "tech", "casual", "level", "mac", "details", "data", "ts", "tech_names", "casual_names")

    # Build an event; details are continuation lines shown indented under the technical text.
    def __init__(self, kind, tag="", tech=None, casual=None, level="info", mac=None, details=None, data=None, ts=None):
        if level not in LEVELS:
            raise ValueError("unknown level %r (expected one of %s)" % (level, ", ".join(LEVELS)))
        self.kind = kind
        self.tag = tag or ""
        self.tech = tech
        self.casual = casual
        self.level = level
        self.mac = mac
        self.details = list(details or [])
        self.data = dict(data or {})
        self.ts = time.time() if ts is None else ts
        self.tech_names = list(spans_of(tech))      # console references in the texts (core/text.py), never guessed later
        self.casual_names = list(spans_of(casual))

    # The technical line exactly as the log file shows it (without the timestamp), or None.
    def technical_line(self):
        if self.tech is None:
            return None
        return ("%-*s %s" % (TAG_WIDTH, self.tag, self.tech)) if self.tag else self.tech

    # The console references in technical_line(): (start, end, who), shifted past the tag.
    def technical_names(self):
        if self.tech is None:
            return []
        shift = len(self.technical_line()) - len(self.tech)
        return [(s + shift, e + shift, w) for s, e, w in self.tech_names]

    # The continuation lines as the log file shows them (indented to line up under the text).
    def detail_lines(self):
        return ["      " + d for d in self.details]

    # True for events casual mode must always show (warnings and worse).
    def is_problem(self):
        return self.level in PROBLEM_LEVELS

    # A plain-dict copy (for the dashboard and tests).
    def as_dict(self):
        return {"kind": self.kind, "tag": self.tag, "tech": self.tech, "casual": self.casual, "level": self.level,
                "mac": self.mac, "details": list(self.details), "data": dict(self.data), "ts": self.ts,
                "tech_names": list(self.tech_names), "casual_names": list(self.casual_names)}

    # Short form for debugging.
    def __repr__(self):
        return "Event(%s, %r, %r)" % (self.kind, self.tag, self.tech if self.tech is not None else self.casual)

# Delivers events to sinks (outputs) and listeners (reactions).
class EventBus(object):
    # Start with no sinks and no listeners.
    def __init__(self):
        self._sinks = []
        self._listeners = []
        self._lock = threading.RLock()
        self._in_sink = threading.local()
        self._in_report = threading.local()

    # Add an output: called for every event, in order, under the bus lock. It must not emit.
    def add_sink(self, func):
        self._sinks.append(func)

    # Add a reaction: called after the sinks, only for the given kinds (None = every kind).
    def add_listener(self, func, kinds=None):
        self._listeners.append((func, frozenset(kinds) if kinds else None))

    # Deliver one event: sinks first (in order, protected), then the matching listeners.
    def emit(self, event):
        if getattr(self._in_sink, "active", False):
            raise RuntimeError("a sink tried to emit an event (sinks must never emit): %r" % (event,))
        with self._lock:
            self._in_sink.active = True
            try:
                for sink in list(self._sinks):
                    try:
                        sink(event)
                    except Exception as e:                  # one broken output must not silence the others
                        _report("sink %r failed on %r: %s" % (sink, event, e))
            finally:
                self._in_sink.active = False
        for func, kinds in list(self._listeners):
            if kinds is None or event.kind in kinds:
                try:
                    func(event)
                except Exception as e:                      # one broken reaction must not stop the others
                    _report("listener %r failed on %r: %s" % (func, event, e))
                    self._reaction_failed(event, e)
        return event

    # A reaction broke: said in the log and the records too (stderr may be nowhere in a background run), with its
    # traceback; never while already saying one (a reaction that breaks on its own report can't loop).
    def _reaction_failed(self, event, e):
        if getattr(self._in_report, "active", False):
            return
        self._in_report.active = True
        try:
            self.emit(Event("log", "PROBLEM", "internal: a reaction to %s failed (%s) - NXSPOOF goes on; please report it" % (
                event.kind, error_text(e)), None, "problem", None, error_trace(e, always=True)))
        finally:
            self._in_report.active = False

    # Emit a log line: technical text (+ optional casual text, level, console, details, data). fact: the line is
    # also that fact (its kind), for listeners and the structured log.
    def log(self, tag, tech, casual=None, level="info", mac=None, details=None, fact=None, **data):
        return self.emit(Event(fact or "log", tag, tech, casual, level, mac, details, data))

    # Emit a casual-only line (Terminal in casual style + dashboard; never the log file).
    def say(self, casual, mac=None, **data):
        return self.emit(Event("say", "", None, casual, "info", mac, None, data))

    # Emit a fact without text, for listeners (e.g. "console.joined", "service.up").
    def fact(self, kind, mac=None, **data):
        return self.emit(Event(kind, "", None, None, "info", mac, None, data))

# Last-resort report when an output or reaction itself breaks (stderr, never raises).
def _report(message):
    try:
        sys.stderr.write("nxspoof: %s\n" % message)
    except Exception:
        pass
