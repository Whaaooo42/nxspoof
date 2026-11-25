"""
nxspoof.setup.checklist - runs the setup steps three ways:

  setup      check every step, fix what's missing (asks first), explain the rest; safe to re-run
  doctor     the checks only, nothing changed: one line per step and "ready" or "not ready"
  uninstall  undo what setup changed (Thunderbolt Bridge back to automatic, virtual cable out,
             sharing off); your files stay
  cleanup    after a run that didn't stop cleanly: leftover firewall rules out, Internet Sharing off

Terminal: the casual style (✅ fine, 🔧 fixed, ✋ needs you, ℹ️ good to know, ⏭️ skipped), or technical
with --console technical. setup, uninstall and cleanup also write a technical log file (logs/<time>-setup.log) and
its records twin (.jsonl: every step is a fact, checklist.step / .undo / .cleanup, then checklist.done), which the
dashboard's Configure (This Mac) follows. --yes answers every question with yes; --only / --skip choose what setup may fix.
"""

import os
import platform
import sys

from nxspoof.core.events import EventBus
from nxspoof.core.fmt import error_text, error_trace
from nxspoof.output import datafolder
from nxspoof.output.logfile import LogFileSink, RecordSink, create_run_log, records_path
from nxspoof.output.terminal import TerminalSink
from nxspoof.setup import steps as steps_module
from nxspoof.setup.repair import Repairs
from nxspoof.setup.steps import Result

ICONS = {"ok": "✅", "fixed": "🔧", "fixable": "✋", "manual": "✋", "info": "ℹ️", "skipped": "⏭️"}
STARTER_FIXES = ("cable",)                       # ▶ Start does these by itself on every start (the virtual cable)

# Ask a yes/no question on the Terminal; Enter = the default; no Terminal (EOF) = the default.
def ask_terminal(question, default=True, stream=None):
    out = stream or sys.stdout
    out.write("%s %s " % (question, "[Y/n]" if default else "[y/N]"))
    out.flush()
    try:
        answer = input().strip().lower()
    except EOFError:
        return default
    return default if not answer else answer.startswith("y")

# Runs the steps and reports them.
class Checklist(Repairs):
    # machine: steps.Machine; bus: where lines go; ask(question, default) -> bool; only / skip: step names setup may /
    # may not fix (--only, --skip).
    # undo_to: what Undo puts back ("before", "defaults", or None: "before" when NXSPOOF has a record).
    def __init__(self, machine, bus, ask, steps=None, only=(), skip=(), undo_to=None):
        self.m, self.bus, self.ask = machine, bus, ask
        self.steps = list(steps if steps is not None else steps_module.STEPS)
        self.only, self.skip, self.undo_to = set(only), set(skip), undo_to
        self.fixed = set()                       # the steps fixed in this setup (a step that depends on one is chosen too)

    # One step's line: technical (tag, name, state, text + explanation lines) and casual (icon, title, text).
    def report(self, tag, step, shown_state, result):
        casual = "%s %s: %s" % (ICONS[shown_state], step.title, result.casual)
        if result.explain and shown_state in ("fixable", "manual", "info", "skipped"):
            casual += "".join("\n   " + line for line in result.explain)
        level = "warn" if shown_state in ("manual", "fixable", "skipped") else "info"
        self.bus.log(tag, "%-12s %-8s %s" % (step.name, shown_state, result.tech), casual=casual, details=result.explain, level=level,
                     fact="checklist.step", step=step.name, title=step.title, state=shown_state, says=result.casual)

    # Check one step; a crashing check becomes a "manual" result instead of stopping everything.
    def check(self, step):
        result, e = steps_module.check_safely(step, self.m)
        if e is not None:
            self.bus.log("SETUP", "%-12s the check itself failed: %s" % (step.name, error_text(e)), level="warn", details=error_trace(e))
        return result

    # The header line: which Mac this is.
    def header(self, tag, casual):
        self.bus.log(tag, "macOS %s on %s, project %s, data folder %s, running as %s" % (
            platform.mac_ver()[0] or "?", platform.machine(), self.m.project_dir, self.m.places.describe(), "root" if self.m.is_root() else "you"),
            casual=casual)

    # Checks only; returns 0 when ready, 1 when something needs fixing.
    def doctor(self):
        self.header("DOCTOR", "🩺 Checking this Mac (nothing is changed)…")
        todo = 0
        for step in self.steps:
            if step.setup_only:
                continue
            r = self.check(step)
            if r.state == "fixable" and step.name in STARTER_FIXES:
                r = Result("info", r.tech + " (Start does it)", r.casual)
            if r.state in ("fixable", "manual"):
                todo += 1
            self.report("DOCTOR", step, r.state, r)
        if todo:
            self.bus.log("DOCTOR", "NOT READY: %d thing(s) to fix" % todo, level="warn",
                         casual="✋ Not ready yet: %d thing%s to fix. Set up this Mac (dashboard: ⚙️ Configure; or menu 7 → 2)." % (todo, "" if todo == 1 else "s"))
            return 1
        self.bus.log("DOCTOR", "READY", casual="✅ This Mac is ready for NXSPOOF.")
        return 0

    # Check, fix with your OK, explain; returns 0 when everything is fine, 1 otherwise. A step left out by --only / --skip
    # is checked and reported, not fixed.
    def setup(self):
        self.header("SETUP", "🧰 Setting up this Mac for NXSPOOF. I'll ask before changing anything.")
        if self.run_is_active("SETUP", "setup"):
            return 1
        todo = 0
        for step in self.steps:
            r = self.check(step)
            if r.state != "fixable":
                todo += r.state == "manual"
                self.report("SETUP", step, r.state, r)
                continue
            if step.needs_root and not self.m.is_root():
                todo += 1
                self.report("SETUP", step, "manual", Result("manual", r.tech + " (needs admin rights)", r.casual,
                                                           ["Run setup with admin rights: 🛠️ Fix selected in the dashboard's ⚙️ Configure, or menu 7 → 2 (both ask for your password)."]))
                continue
            if not self.chosen(step):
                todo += 1
                self.report("SETUP", step, "skipped", Result("info", "%s - not fixed (not chosen this time: --only / --skip)" % r.tech,
                                                            "%s - not fixed this time (not chosen)" % r.casual))
                continue
            self.report("SETUP", step, "fixable", r)
            if not self.ask("   Fix it now?", True):
                todo += 1
                self.report("SETUP", step, "skipped", Result("info", "skipped by you", "skipped (run setup again any time)"))
                continue
            todo += not self._fix(step)
        if todo:
            self.bus.log("SETUP", "NOT DONE: %d thing(s) still need you" % todo, level="warn", fact="checklist.done", command="setup", todo=todo,
                         casual="✋ %d thing%s still need%s you (see above). Run setup again when done." % (
                             todo, "" if todo == 1 else "s", "s" if todo == 1 else ""))
            return 1
        self.bus.log("SETUP", "DONE: this Mac is ready", fact="checklist.done", command="setup", todo=0,
                     casual="🎉 This Mac is ready. Press ▶ Start on the dashboard to begin.")
        return 0

    # True if setup may fix this step (--only names it, or there's no --only; --skip doesn't name it), or it depends on a
    # step fixed in this setup (the cable after the bridge: one Fix is enough).
    def chosen(self, step):
        if step.name not in self.skip and self.fixed & set(step.depends_on):
            return True
        return (not self.only or step.name in self.only) and step.name not in self.skip

    # Fix one step and check it again; True if it's fine now. A step that has a record notes how it was first (Undo's "before").
    def _fix(self, step):
        try:
            before = step.record(self.m) if hasattr(step, "record") else None        # read once: it asks macOS
            if before is not None and datafolder.remember_before(self.m.places.config, step.name, before, give=self.m.system.give_to_user):
                self.bus.log("SETUP", "%-12s noted how it was before NXSPOOF changed it (for Undo): %s" % (step.name, before))
            done = step.fix(self.m)
        except Exception as e:
            self.bus.log("SETUP", "%-12s the fix itself failed: %s" % (step.name, error_text(e)), level="warn", details=error_trace(e))
            self.report("SETUP", step, "manual", Result("manual", "fix failed: %s" % error_text(e), "I couldn't fix it (%s)" % e,
                                                       ["The log file has the details; docs/troubleshooting.html has the manual steps."]))
            return False
        after = self.check(step)
        if after.state == "ok":
            self.fixed.add(step.name)
            self.report("SETUP", step, "fixed", Result("ok", "fixed: %s" % done, "fixed - %s" % after.casual))
            return True
        self.report("SETUP", step, "manual", Result("manual", "still not right after: %s (%s)" % (done, after.tech),
                                                   "still not right after the fix: %s" % after.casual, after.explain))
        return False

LOGGED = ("setup", "uninstall", "cleanup")       # the commands with their own log (and records twin)

# Run setup, doctor, uninstall or cleanup; returns the exit code.
#   ask(question, default) -> bool replaces the Terminal question (tests); machine: a ready Machine (tests: a fake Mac);
#   places: where NXSPOOF's files are (core/places.py; the project folder for every role when not given);
#   args: the command line's options (settings, yes, only, skip, task), if any.
def main(command, system, project_dir, stream=None, style="casual", ask=None, machine=None, places=None, args=None):
    out = stream or sys.stdout
    opt = lambda name: getattr(args, name, None)
    machine = machine or steps_module.Machine(project_dir, system, settings_path=opt("settings"), places=places)
    names = {s.name for s in steps_module.STEPS}
    only, skip = split_names(opt("only")), split_names(opt("skip"))
    unknown = sorted((only | skip) - names)
    if unknown:
        out.write("unknown step(s) for --only / --skip: %s (the steps: %s)\n" % (", ".join(unknown), ", ".join(s.name for s in steps_module.STEPS)))
        return 2
    bus = EventBus()
    bus.add_sink(TerminalSink(style or "casual", bool(getattr(out, "isatty", lambda: False)()), None, out))
    logfile = open_log(command, bus, machine.places.logs, system) if command in LOGGED else None
    if opt("task"):
        bus.log("LOG", "started by the dashboard (task %s)" % opt("task"), fact="task.started", ticket=opt("task"))
    asker = (lambda question, default: True) if opt("yes") else (ask or (lambda question, default: ask_terminal(question, default, out)))
    checklist = Checklist(machine, bus, asker, only=only, skip=skip, undo_to=opt("to"))
    code = getattr(checklist, "uninstall" if command == "uninstall" else command)()
    if logfile is not None and logfile.path:
        out.write("   (full details: %s in the data folder)\n" % os.path.relpath(logfile.path, machine.places.logs))
    return code

# A comma-separated list of step names ("bridge,cable") as a set; empty for none.
def split_names(text):
    return {n.strip() for n in (text or "").split(",") if n.strip()}

# This command's log file and its records twin (logs/<time>-<command>.log + .jsonl in the logs role), on the bus; returns
# the log sink (its path None if the file couldn't be made).
def open_log(command, bus, logs_base, system):
    logfile, records = LogFileSink(), RecordSink()
    bus.add_sink(logfile)
    bus.add_sink(records)
    try:
        logfile.path = create_run_log(logs_base, "logs", label=command, give=system.give_to_user, records=True)
        records.path = records_path(logfile.path)
    except OSError:
        pass
    return logfile
