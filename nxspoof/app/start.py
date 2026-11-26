"""
nxspoof.app.start - nx_spoof.py start, the one way to start NXSPOOF (D30).

  start_command   from either door (the menu or Terminal with sudo,
                  the dashboard's ▶ Start through macOS's password window):
                    1. the program copy (every start runs it, D21);
                    2. this Mac's readiness (setup's checks; --fix: setup fixes what it can first; --anyway starts all
                       the same), before the lock: setup waits while the lock is held;
                    3. the lock (output/runfile.py): one run at a time;
                    4. the background: a waiter here, and the worker, a brand-new process on its own (macos/process.py).
                       The worker splits (app/keeper.py: it stays root as the keeper, and starts the run as you, D24), the
                       run starts, and answers once: READY, or the exact reason it couldn't (the Mac put back as it was).
                       The waiter prints that answer and ends: exit 0 = running. Nothing that closes afterwards (a window,
                       the menu) stops the run: only a Stop (or a sleep, D32).

Every process here is a brand-new one (D33): the worker and the run are this same command started again with
"--stage worker|run <spec>" (app/cli.py split_stage), handed what they need (the answer pipe, the lock, the keeper's
socket, what the start knew in memory). Never a fork that carries on: macOS supports nothing else, and from macOS 26.1 its
frameworks crash in such a child (FB21364061; two users on macOS 27, Nov 25).
"""

import json
import os
import sys

from nxspoof.app import keeper, signals
from nxspoof.app.commands import load_store
from nxspoof.app.config import build_config
from nxspoof.app.context import Context
from nxspoof.app.hotspotwatch import HotspotWatch
from nxspoof.app.run import Run
from nxspoof.core.events import EventBus
from nxspoof.output import datafolder, runfile
from nxspoof.output.logfile import START_OUTPUT, open_output, start_output, tail
from nxspoof.settings.store import SettingsError

START_WAIT = 90                              # seconds the start waits for the run's answer (the run itself gives up on the hotspot sooner)
NOT_READY = 2                                # start's exit code when this Mac isn't ready (the menu then offers --anyway)
WATCH_HINT = "Watch it: nx_spoof.py follow (menu 3) · stop it: nx_spoof.py stop (menu 2) or the dashboard's ■ Stop."

# The run's one answer to the start that's waiting for it (nothing to tell when there's no waiter: the fake Mac, tests).
class Answer(object):
    # system: the System (its answer()); fd: the end to answer on, or None.
    def __init__(self, system, fd):
        self.system, self.fd = system, fd

    # Say it, once: ok, what to print, and any data (the hotspot, the log).
    def send(self, ok, text, **data):
        if self.fd is not None:
            self.system.answer(self.fd, dict(data, ok=ok, text=text))
            self.fd = None

    # Let go without answering (the keeper: only the run answers, and the start must see the end close if the run dies).
    def drop(self):
        if self.fd is not None:
            self.system.answer(self.fd, None)
            self.fd = None

# nx_spoof.py start. prepared: what preparing the folders did. A started stage (args.stage) goes to staged().
def start_command(args, system, places, stream=None, prepared=()):
    if getattr(args, "stage", None):
        return staged(args, system, places, stream)
    try:
        store, message = load_store(args, places, on_saved=system.give_to_user)
        config = build_config(store, args)
    except (SettingsError, ValueError) as e:
        print(str(e))
        return 1
    if runfile.held(places.runtime):         # said at once, before copying anything (owner, 2026-11-22)
        return already_running(places)
    run_from_copy(system, places, getattr(args, "argv", []))
    if not ready(args, config, system, places):                  # before the lock: start --fix runs setup, which waits while it's held
        return NOT_READY
    lock = runfile.take(places.runtime, give=system.give_to_user)
    if lock is None:                         # one run at a time: two would fight over the firewall rules and the ports
        return already_running(places)
    runfile.forget_before(places.runtime)    # what a run that never tidied up left (a killed run's run.json) is stale now
    output = start_output(places.logs, name=START_OUTPUT)
    system.give_to_user(output)
    print(end="", flush=True)                # nothing of ours left in a buffer the run would inherit
    with open_output(output) as out:
        role, fd, _ = system.in_background(lambda spec: stage_words(places, args, "worker", spec), out,
                                           keep={"lock": lock.fileno()}, handover={"prepared": list(prepared)})
    if role == "waiter":
        lock.close()                         # the worker holds it now (and its keeper, until the run's clean up is done)
        return report(system.wait_answer(fd, START_WAIT), output)
    return run_in_background(args, (store, message, config), system, places, stream, prepared, Answer(system, fd))

# This command started again as a stage (a brand-new process): the program copy's nx_spoof.py, "--stage", the stage, its spec
# (the handed descriptors' numbers and values, as JSON), then the command's own words.
def stage_words(places, args, stage, spec):
    return [sys.executable, "-B", os.path.join(places.program, "nx_spoof.py"), "--stage", stage, json.dumps(spec)] + list(args.argv)

# A started stage: what it was handed taken over, the settings loaded again; the worker splits (or is the run itself, with
# --no-keeper), the run checks it isn't root and plays. A settings problem here ends it at once (the waiter shows its words).
def staged(args, system, places, stream):
    got = system.take_over(args.stage_spec)
    prepared = [tuple(p) for p in (got.get("handover") or {}).get("prepared", [])]
    try:
        store, message = load_store(args, places, on_saved=system.give_to_user)
        loaded = (store, message, build_config(store, args))
    except (SettingsError, ValueError) as e:
        print(str(e))
        return 1
    if args.stage == "worker":
        return run_in_background(args, loaded, system, places, stream, prepared, Answer(system, got["answer"]))
    system.check_not_root()                  # the run, first thing: started as you, it can't become root again
    run_system = keeper.run_system(got)
    return play_run(loaded, run_system, places, stream, prepared, Answer(run_system, got["answer"]))

# One run at a time: say so (with the running one's process id) -> exit code 1.
def already_running(places):
    info = runfile.read(places.runtime)
    print("NXSPOOF is already running%s: nothing done. %s" % (" (process %s)" % info["pid"] if info.get("pid") else "", WATCH_HINT))
    return 1

# Every start runs the program copy (D21: macOS keeps a program started through its password window out of Documents, and
# both doors run the same files): already it -> nothing; otherwise the copy is refreshed (output/datafolder.py) and this
# command runs again from it, the same process (never returns). argv: the command's words.
def run_from_copy(system, places, argv):
    copy = os.path.join(places.runtime, datafolder.PROGRAM)
    if os.path.realpath(places.program) != os.path.realpath(copy):
        program, _, _ = datafolder.copy_program(places.program, places.runtime, give=system.give_to_user)
        system.restart_from(program, argv)

# This Mac ready for a run (setup's checks)? --fix: setup fixes what it can first (D30). Not ready: the reasons on the Terminal,
# False (exit NOT_READY), unless --anyway. A dry run (--ip) needs nothing from the Mac.
def ready(args, config, system, places):
    if config.extra("ip") or config.extra("anyway"):
        return True
    problems = fix_first(config, system, places, system.mac_problems(places.program, places))
    if not problems:
        return True
    print("This Mac isn't ready for NXSPOOF yet:")
    for p in problems:
        print("  - %s: %s" % (p["title"], p["tech"]))
    can_fix = any(p.get("fixable") for p in problems) and not config.extra("fix")
    print("Fix it with setup (menu 7 → 2, or the dashboard's ⚙️ Configure)%s, or start all the same: sudo python3 nx_spoof.py start --anyway" % (
        ", or let start fix it: sudo python3 nx_spoof.py start --fix" if can_fix else ""))
    return False

# start --fix: setup fixes what it can first, then the checks again -> what's still not ready (problems as they are without --fix).
def fix_first(config, system, places, problems):
    fixable = [p["name"] for p in problems if p.get("fixable")]
    if not (fixable and config.extra("fix")):
        return problems
    print("This Mac isn't ready: setup fixes %s first." % ", ".join(fixable))
    system.mac_fix(places, fixable)
    return system.mac_problems(places.program, places)

# The waiter: the run's answer on the Terminal -> the exit code (0 = running).
def report(found, output):
    if found == "timeout":
        print("No answer from NXSPOOF after %d s: it may still be starting. %s" % (START_WAIT, WATCH_HINT))
        return 1
    if found is None:
        print("NXSPOOF ended before it was ready. Its last words:")
        for line in tail(output) or ["(nothing)"]:
            print("   " + line)
        print("If it crashed, macOS keeps its crash report in ~/Library/Logs/DiagnosticReports (Python-....ips): 🐞 Report and that file tell what happened.")
        return 1
    if found.get("ok"):
        print("✅ NXSPOOF is running. %s\n%s" % (found.get("text", ""), WATCH_HINT))
        return 0
    print("❌ NXSPOOF couldn't start: %s\nThe Mac was put back as it was. The whole story is in its log (%s)." % (
        found.get("text", "?"), found.get("log") or "the data folder's logs/"))
    return 1

# The worker, on its own: the split (the keeper starts the run as you, a brand-new process, D24 + D33), or the run here (no
# split: --no-keeper, not root, the fake Mac); returns its exit code. loaded: (the settings store, what loading it said, the
# RunConfig).
def run_in_background(args, loaded, system, places, stream, prepared, answer):
    store, message, config = loaded
    start_run = None if answer.fd is None else (lambda spec: stage_words(places, args, "run", spec), {"answer": answer.fd}, {},
                                                 {"prepared": [list(p) for p in prepared]})
    part = keeper.split(system, bool(config.get("spoof.keep_sharing")), wanted=not args.no_keeper, in_keeper=answer.drop,
                        run=start_run)
    if part.role == "keeper":
        return part.code
    return play_run(loaded, part.system, places, stream, prepared, answer)

# The run itself (app/run.py) until a Stop, with this System (the keeper's, or the Mac's in one process); returns its exit code.
def play_run(loaded, system, places, stream, prepared, answer):
    store, message, config = loaded
    bus = EventBus()
    ctx = Context(bus, config, store, system, places, prepared=prepared)
    run = Run(ctx, message, stream, answer=answer.send, where=lambda info: where(places, system, info),
              stop_asked=lambda: runfile.stop_asked(places.runtime))
    HotspotWatch(run)                        # D29: notices when the hotspot vanishes, and puts it back
    previous = signals.install(ctx)
    try:
        return run.execute()
    finally:
        signals.restore(previous)

# run.json (output/runfile.py), as the run tells it where it is: info written, or None (it has stopped) removed.
def where(places, system, info):
    if info is None:
        runfile.clear(places.runtime)
    else:
        runfile.write(places.runtime, info, give=system.give_to_user)
