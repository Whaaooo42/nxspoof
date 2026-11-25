"""
nxspoof.app.tasks - This Mac's buttons that need admin rights, and the crash check (control center stage 3, D22).

  MacTasks    one task at a time, through macOS's own password window (app/password.py), waited for:
                setup      🛠️ fix the steps you ticked       nx_spoof.py setup --yes --only <steps>
                undo       ↩ undo setup                      nx_spoof.py uninstall --yes   (back to before NXSPOOF, if recorded)
                undo_defaults  ↩ to macOS's defaults         nx_spoof.py uninstall --yes --to defaults
                cleanup    🧹 after a crash                  nx_spoof.py cleanup
              The page only names the task (and, for setup, step names checked against setup's own list): the command
              words are NXSPOOF's. Each command writes its own log and records with the ticket it's given
              (--task); the dashboard finds them by it and shows each step in Configure's This Mac column as it happens (records, never text).
  Leftovers   what a crash may have left behind: the last run never reached its end (its records have no "Stopped."), or
              Internet Sharing is on with no run. Checked every CHECK_EVERY seconds while nothing runs (no admin rights
              needed); the page offers 🧹 Clean up. A clean up newer than that run settles it.
Never while a run is active or starting, or while another task runs. Every step is a DASH line in the dashboard's log.
"""

import os
import secrets
import threading
import time

from nxspoof.app import password
from nxspoof.core.controls import ControlError
from nxspoof.features import runlog
from nxspoof.output import runfile
from nxspoof.output.logfile import RECORDS_EXT, TASK_OUTPUT, log_path_of, tail
from nxspoof.setup import steps

PROMPT = "NXSPOOF needs your password to %s (the dashboard's ⚙️ Configure)."
TASKS = {                                    # kind -> nx_spoof.py words, the label of the log it writes (None: no log), what it does
    "setup": (["setup", "--yes"], "setup", "set up this Mac"),
    "undo": (["uninstall", "--yes"], "uninstall", "undo setup"),
    "undo_defaults": (["uninstall", "--yes", "--to", "defaults"], "uninstall", "undo setup to macOS's defaults"),
    "cleanup": (["cleanup"], "cleanup", "clean up after the last run"),
}
STEP_KINDS = ("checklist.step", "checklist.undo", "checklist.cleanup")
BUSY = ("password", "running")               # a task's phases while it isn't over (then: done, cancelled, failed)
NEEDS_YOU = ("manual", "fixable", "skipped")
CHECK_EVERY = 10                             # seconds between two crash checks while nothing runs

# This Mac's tasks for one control center: the current (or last) task, followed from its records.
class MacTasks(object):
    # center: the Center (note, bus, system, places, logs_dir(), link, is_starting()).
    def __init__(self, center):
        self.center, self.task = center, None

    # True while a task isn't over (its password window, or the command itself).
    def busy(self):
        return self.task is not None and self.task["phase"] in BUSY

    # What the page shows: the current or last task ({kind, doing, phase, steps, todo, log, message}), or None.
    def state(self):
        t = self.task
        return None if t is None else {k: (list(v) if k == "steps" else v) for k, v in t.items() if k not in ("ticket", "since")}

    # The mac_task control: payload {"kind": one of TASKS, "steps": [step names] for setup}.
    def run(self, payload):
        p, c = payload or {}, self.center
        kind = str(p.get("kind", ""))
        if kind not in TASKS:
            raise ControlError("kind must be one of: " + ", ".join(sorted(TASKS)))
        if c.follow.present() or c.is_starting() or self.busy() or runfile.held(c.places.runtime):
            raise ControlError("NXSPOOF is running, starting, or busy with another task: wait for it, or stop it first")
        words, label, doing = TASKS[kind]
        words = words + (["--only", ",".join(chosen(p.get("steps")))] if kind == "setup" else [])
        ticket = secrets.token_hex(4)
        words += ["--task", ticket]
        handle, output = password.ask(c, words, TASK_OUTPUT, PROMPT % doing)
        task = {"kind": kind, "doing": doing, "phase": "password", "steps": [], "todo": None, "log": None, "message": "",
                "ticket": ticket, "since": time.time()}
        self.task = task
        c.note("%s: macOS is asking for your password (nx_spoof.py %s; no Terminal window)" % (kind, " ".join(words)),
               casual="🔑 To %s, type your Mac password in the macOS window." % doing)
        threading.Thread(target=self._follow, args=(task, label), daemon=True).start()
        password.when_answered(c, handle, lambda state, message: self._answered(task, label, state, message, output))
        return {"ok": True, "message": "Type your Mac password in the macOS window."}

    # While the task runs: find its log by the ticket, then show each step as it's written.
    def _follow(self, task, label):
        path = None
        while task["phase"] in BUSY:
            path = path or self._find(task, label)
            if path:
                self._read(task, path)
                task["phase"] = "running" if task["phase"] == "password" else task["phase"]
            time.sleep(0.5)

    # The task's log (data folder logs/<time>-<label>.log) found by its ticket, or None (not written yet).
    def _find(self, task, label):
        logs = self.center.logs_dir()
        for name in sorted(os.listdir(logs), reverse=True) if os.path.isdir(logs) else []:
            path = os.path.join(logs, name)
            if name.endswith("-%s%s" % (label, RECORDS_EXT)) and os.path.getmtime(path) >= task["since"] - 5:
                started = runlog.of_kind(runlog.load_records(log_path_of(path)), "task.started", 1)
                if started and started[0].get("data", {}).get("ticket") == task["ticket"]:
                    return log_path_of(path)
        return None

    # The task's steps and closing fact, from its records (a step said twice, e.g. fixable then fixed, shows once, as last said).
    def _read(self, task, log_path):
        records = runlog.load_records(log_path)
        shown = {}
        for r in records:
            if r.get("kind") in STEP_KINDS:
                d = r.get("data", {})
                shown[(r["kind"], d.get("step"))] = {k: d.get(k) for k in ("step", "title", "state", "says")}
        done = runlog.of_kind(records, "checklist.done", None)
        task["steps"] = list(shown.values())
        task["log"] = os.path.relpath(log_path, self.center.places.logs)
        if done:
            task["todo"] = done[-1].get("data", {}).get("todo", 0)

    # The password window answered (for a task: the command has finished): say how it went, from its records.
    def _answered(self, task, label, state, message, output):
        c = self.center
        if state != "started":
            task["phase"], task["message"] = state, message
            password.say_not_started(c, task["kind"], task["doing"], state, message)
            return
        path = self._find(task, label)
        if path:
            self._read(task, path)
        if task["todo"] is None:
            task["phase"], task["message"] = "failed", "it ended without saying how it went"
            c.bus.log("DASH", "%s: ended without its closing line - what it wrote:" % task["kind"], level="warn", details=tail(output) or ["(nothing)"],
                      casual="😕 Couldn't %s: it stopped half way. Its last words are in the dashboard's log; the Terminal way shows more." % task["doing"])
        else:
            task["phase"] = "done"
            c.note(summary(task), level="warn" if task["todo"] else "info", casual="%s %s: %s" % (
                "✋" if task["todo"] else "✅", task["doing"].capitalize(), "done, but something still needs you (see This Mac)" if task["todo"] else "done"))
        c.leftovers.checked = 0                                        # check again for leftovers right away
        c.hotspot_read = (0.0, None)                                   # and read the hotspot's name again (setup or undo may change it)

# Setup's steps the page ticked, checked against setup's own list, in setup's order (ControlError if none, or unknown).
def chosen(names):
    known = [s.name for s in steps.STEPS]
    if not isinstance(names, list) or not names:
        raise ControlError("tick at least one thing for setup to fix")
    unknown = [str(n) for n in names if n not in known]
    if unknown:
        raise ControlError("unknown step(s): %s" % ", ".join(unknown))
    return [n for n in known if n in names]

# The dashboard's closing line for a task: "setup finished: 2 fixed (bridge, cable), 1 needs you (hotspot), 5 fine; its log: ...".
def summary(task):
    groups = {}
    for s in task["steps"]:
        word = "need you" if s["state"] in NEEDS_YOU else "fine" if s["state"] in ("ok", "info") else s["state"]
        groups.setdefault(word, []).append(str(s["step"]))
    parts = ["%d %s" % (len(v), k) + ("" if k == "fine" else " (%s)" % ", ".join(v)) for k, v in groups.items()]
    return "%s finished: %s; its log: %s" % (task["kind"], ", ".join(parts) or "nothing to do", task["log"])

# What a crash may have left behind, for the page's banner (checked every CHECK_EVERY seconds while nothing runs).
class Leftovers(object):
    # center: the Center (note, system, logs_dir(), link, is_starting(), tasks).
    def __init__(self, center):
        self.center, self.found, self.checked, self.said = center, [], 0.0, set()

    # The current findings [{kind, text}] (checked again when the last check is older than CHECK_EVERY).
    def state(self):
        if time.time() - self.checked >= CHECK_EVERY:
            self.check()
        return self.found

    # Look now: the last run's end, Internet Sharing. A finding is said once in the dashboard's log.
    def check(self):
        self.checked = time.time()
        c = self.center
        if c.follow.present() or c.is_starting() or c.tasks.busy() or runfile.held(c.places.runtime):
            self.found = []
            return self.found
        found, logs = [], c.logs_dir()
        last = runlog.latest_run(logs)
        if last and not last["clean"] and not cleaned_since(logs, last["time"]):
            found.append({"kind": "crash", "text": "The last run (%s) never reached its end (a crash, a restart, or it was stopped by force): "
                          "its firewall rules or the hotspot may still be on." % shown_run(last["name"]),
                          "casual": "⚠️ The last run didn't stop cleanly: press 🧹 Clean up at the top of the page."})
        if c.system.sharing_running():
            found.append({"kind": "sharing", "text": "Internet Sharing is on, but NXSPOOF isn't running: your Mac's Wi-Fi is still a hotspot.",
                          "casual": "⚠️ Internet Sharing is still on: press 🧹 Clean up at the top of the page."})
        for f in found:
            if f["text"] not in self.said:
                self.said.add(f["text"])
                c.note("leftovers: %s - the page offers 🧹 Clean up" % f["text"], casual=f["casual"], level="warn")
        self.found = found
        return found

# True if a clean up ran after `when` (a -cleanup.log newer than it, from the dashboard or menu 7 → 4).
def cleaned_since(logs, when):
    for name in os.listdir(logs) if os.path.isdir(logs) else []:
        if name.endswith("-cleanup.log") and os.path.getmtime(os.path.join(logs, name)) > when:
            return True
    return False

# A run's log name as people say it: "2026-11-16_17-41-12.log" -> "2026-11-16 17:41".
def shown_run(name):
    stem = name.split(".")[0]
    day, _, clock = stem.partition("_")
    return "%s %s" % (day, clock[:5].replace("-", ":"))
