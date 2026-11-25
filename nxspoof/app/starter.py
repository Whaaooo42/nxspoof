"""
nxspoof.app.starter - the dashboard's ▶ Start: the same nx_spoof.py start as the menu's (D30), through macOS's password window.
  1. the quick check (setup's checks, read only, no password): not ready -> the page lists why and offers "Fix and start"
     when setup can fix all of it (start --fix: setup's fixes, then the start, in one password window, D30) and
     "Start anyway" (start --anyway)
  2-3. app/password.py: the program copy in NXSPOOF's data folder refreshed, then macOS's own password window, which runs
     nx_spoof.py start and answers when the start has its answer (the run READY, or why it couldn't)
  4. the run holds the lock afterwards: it's running (it registers with the dashboard like any run, and the page shows it);
     it doesn't: the start's own words (its output file) in the dashboard's log
     cancelled: "Start cancelled", the page offers ▶ Start again; failed: macOS's message
Every step is a DASH line in the dashboard's log.
"""

import argparse
import time

from nxspoof.app import password
from nxspoof.core.controls import ControlError
from nxspoof.core.fmt import error_text
from nxspoof.features import runlog
from nxspoof.output import runfile
from nxspoof.output.logfile import START_OUTPUT, tail
from nxspoof.setup import checklist, steps

PROMPT = ("NXSPOOF needs your password to start the hotspot: Internet Sharing, its firewall and the fake connection test. "
          "It stops when you press Stop on the dashboard.")
WAIT_FOR_RUN = 15                            # seconds after a started run's answer before one that hasn't shown up here is reported
NOT_READY = ("fixable", "manual")            # doctor's states that make this Mac "not ready" (info never blocks)

# This Mac: doctor's checks, read only -> [{name, title, state, casual, tech, explain}] (Configure's This Mac column, and ▶ Start's quick
# check); what ▶ Start fixes by itself (the virtual cable) is only "info" here. places: where settings.json is (core/places.py);
# logs: the logs folder (for the hotspot's "name changed since the last run" note).
def mac_checks(project_dir, system, places=None, logs=None):
    machine = steps.Machine(project_dir, system, places=places)
    rows = []
    for step in steps.STEPS:
        if step.setup_only:
            continue
        r, _ = steps.check_safely(step, machine)            # a crashing check is a row that says so, never a broken ▶ Start
        state = "info" if r.state == "fixable" and step.name in checklist.STARTER_FIXES else r.state
        rows.append({"name": step.name, "title": step.title, "state": state, "casual": r.casual, "tech": r.tech, "explain": list(r.explain),
                     "fixable": r.state == "fixable"})                     # setup can fix it (This Mac's 🛠️ ticks it)
    renamed_note(rows, system, logs)
    return rows

# Setup's fixes for these steps (names), as `setup --yes --only <names>` does, with its own log, on the Terminal (start --fix,
# D30); returns setup's exit code. system: the System; places: where NXSPOOF's files are (core/places.py).
def fix_mac(system, places, names):
    return checklist.main("setup", system, places.program, places=places, args=argparse.Namespace(yes=True, only=",".join(names)))

# The hotspot's name as macOS has it, or None when it can't be read (unreadable(why) is told why, with the error's kind).
def hotspot_name(system, unreadable):
    try:
        return system.hotspot_settings().get("name")
    except Exception as e:
        unreadable(error_text(e))
        return None

# On the hotspot's line: a note when its name isn't the one the last run had (each Switch has to join the new one).
# logs: the logs folder (none given: no note).
def renamed_note(rows, system, logs):
    row = next((r for r in rows if r["name"] == "hotspot"), None)
    if row is None or logs is None:
        return
    try:
        now = system.hotspot_settings().get("name")
    except Exception:
        return
    last = runlog.last_hotspot_name(logs)
    if now and last and now != last:
        row["explain"].append("Its name changed since the last run (it was \"%s\"): each Switch has to join \"%s\", with its password." % (last, now))

# ▶ Start for one control center: its state ("password" while the window is open, "waiting" until the run shows up here).
class PageStart(object):
    # center: the Center (note, system, project_dir, places, store, mac_checks, link, tasks).
    def __init__(self, center):
        self.center = center
        self.mode, self.attempt = None, 0

    # True while a start is under way (until the run shows up, or the start ends).
    def busy(self):
        return self.mode is not None

    # What the page shows while starting: "password", "waiting", or False.
    def shown(self):
        return self.mode or False

    # The run showed up (registered), or the start ended: over (a password answer still on its way only gets said).
    def done(self):
        self.mode = None
        self.attempt += 1

    # The ▶ Start control: payload {} (the check, then the password window), {"fix": true} or {"anyway": true}.
    def start(self, payload):
        p = payload or {}
        c = self.center
        if c.follow.present() or self.busy() or runfile.held(c.places.runtime):
            raise ControlError("NXSPOOF is already running or starting")
        if c.tasks.busy():
            raise ControlError("a This Mac task is running (setup, clean up...): wait for it to finish")
        problems = [r for r in c.mac_checks() if r["state"] in NOT_READY]
        if problems and not (p.get("anyway") or p.get("fix")):
            c.note("start: this Mac isn't ready (%s) - the page offers to fix it or start anyway" % "; ".join(r["tech"] for r in problems))
            return {"ok": True, "not_ready": [{"title": r["title"], "casual": r["casual"], "fixable": bool(r.get("fixable"))} for r in problems],
                    "message": "This Mac isn't ready yet."}
        how = "fix" if problems and p.get("fix") else "anyway" if problems else None
        return self._with_password(how)

    # The program copy, macOS's password window running nx_spoof.py start (app/password.py), answered when the start is.
    # how: None (ready), "fix" (setup's fixes first) or "anyway".
    def _with_password(self, how):
        c = self.center
        handle, output = password.ask(c, ["start"] + (["--" + how] if how else []), START_OUTPUT, PROMPT)
        self.attempt += 1
        self.mode, attempt = "password", self.attempt
        c.note("starting NXSPOOF: macOS is asking for your password (quick check: %s; no Terminal window)" % (
            {None: "this Mac is ready", "fix": "NOT ready, setup fixes it first", "anyway": "NOT ready, starting anyway"}[how]),
            casual="🔑 Type your Mac password in the macOS window.")
        password.when_answered(c, handle, lambda state, message: self._after_password(state, message, attempt, output))
        return {"ok": True, "message": "Type your Mac password in the macOS window."}

    # The window's answer (the start has finished): running -> wait for it to show up here; not -> the start's own words.
    def _after_password(self, state, message, attempt, output):
        c = self.center
        if state != "started":
            if attempt == self.attempt:
                self.done()
            password.say_not_started(c, "start", "start NXSPOOF", state, message)
            return
        if runfile.held(c.places.runtime):
            if attempt == self.attempt:
                self.mode = "waiting"
                self._watch(attempt, output)
            return
        if attempt == self.attempt:
            self.done()
        c.bus.log("DASH", "NXSPOOF didn't start - what the start said:", casual="😕 NXSPOOF didn't start. Why is in the dashboard's log.",
                  level="warn", details=tail(output) or ["(nothing)"])

    # The run is running: say so if it hasn't shown up here within WAIT_FOR_RUN seconds.
    def _watch(self, attempt, output):
        deadline = time.time() + WAIT_FOR_RUN
        while time.time() < deadline and attempt == self.attempt and self.mode == "waiting":
            time.sleep(0.5)
        if attempt == self.attempt and self.mode == "waiting":
            self.done()
            self.center.bus.log("DASH", "NXSPOOF is running but didn't show up here within %d s - what the start said:" % WAIT_FOR_RUN,
                                level="warn", details=tail(output) or ["(nothing)"])
