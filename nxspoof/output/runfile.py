"""
nxspoof.output.runfile - "is NXSPOOF running, and where?", answered by files in the runtime folder (core/places.py):

  run.lock    held (an exclusive lock, flock) by the start and handed down to the run and its keeper: one run at a time.
              The lock goes away with the last of them, however they end (a Stop, a crash, a forced quit), so a free lock
              always means no run, and a stale file means nothing.
  run.json    the run's "here I am": its process ids, its log, when it started, and once READY, its hotspot. Written by the
              run, read by the dashboard, nx_spoof.py stop and follow; only believed while the lock is held.
  run_state.json  what the dashboard shows about the run (its phase, hotspot, consoles: dashboard/state.py's snapshot),
              rewritten by the run every second while it changes (app/runstate.py), read by the dashboard (app/runfollow.py).
  stop.json   a Stop's note to the run: who asked ("dashboard", "stop command"). Written by the dashboard's ■ Stop and
              nx_spoof.py stop, picked up by the run within half a second (then removed). A signal goes too, but a signal can
              be lost on the way (macOS's password launcher, a run that changed user); a file can't (found on the Mac, 2026-11-22).

  take(folder, give)    the lock for a new run: its open file (keep it open: closing it frees the lock), or None if a run holds it
  held(folder)          True while a run holds the lock (from any process: it never takes it)
  write(folder, info, give) / read(folder) / clear(folder)    run.json (read: {} when missing, unreadable, or no run holds the lock)
  forget_before(folder) the new run's start, once the lock is its own: a gone run's run.json, run_state.json, Stop note removed
  write_state / read_state / clear_state                      run_state.json, the same way
  ask_stop(folder, by, give) / stop_asked(folder)              stop.json: leave the note; the run takes it (who asked, or None)
"""

import fcntl
import json
import os

LOCK = "run.lock"
INFO = "run.json"
STATE = "run_state.json"
STOP = "stop.json"

# The lock for a new run, or None if a run already holds it. The open file is the lock: keep it open (a new process
# inherits it), close it to let go. give(path): hand the file back to the real user (it's created by root).
def take(folder, give=None):
    path = os.path.join(folder, LOCK)
    f = open(path, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    if give:
        give(path)
    return f

# True while a run holds the lock (tried and let go at once, never kept).
def held(folder):
    try:
        f = open(os.path.join(folder, LOCK))
    except OSError:
        return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        f.close()
    return False

# Once the lock is ours: remove what a run that never tidied up left (killed by the keeper's heartbeat check, a crash):
# its run.json, run_state.json and a Stop's note would be taken for the new run's (Run I: the dashboard followed a dead
# run's log and showed its lines as live).
def forget_before(folder):
    for name in (INFO, STATE, STOP):
        clear(folder, name=name)

# Write run.json (whole, then moved into place, so a reader never sees half of it).
def write(folder, info, give=None, name=INFO):
    path = os.path.join(folder, name)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1, sort_keys=True)
    os.replace(path + ".tmp", path)
    if give:
        give(path)

# The running run's run.json -> dict; {} when no run holds the lock, or the file is missing or unreadable.
def read(folder, name=INFO):
    if not held(folder):
        return {}
    try:
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}

# Remove run.json (the run has stopped).
def clear(folder, name=INFO):
    try:
        os.remove(os.path.join(folder, name))
    except OSError:
        pass

# Write run_state.json (the dashboard's snapshot of the run).
def write_state(folder, state, give=None):
    write(folder, state, give, name=STATE)

# The running run's run_state.json -> dict ({} as read()).
def read_state(folder):
    return read(folder, name=STATE)

# Remove run_state.json (the run has stopped).
def clear_state(folder):
    clear(folder, name=STATE)

# Leave a Stop's note for the run (by: "dashboard", "stop command").
def ask_stop(folder, by, give=None):
    write(folder, {"by": by}, give, name=STOP)

# The run takes the Stop's note: who asked (the note removed), or None when there's none.
def stop_asked(folder):
    path = os.path.join(folder, STOP)
    try:
        with open(path, encoding="utf-8") as f:
            note = json.load(f)
    except (OSError, ValueError):
        return None
    clear(folder, name=STOP)
    return str(note.get("by") or "a stop note") if isinstance(note, dict) else "a stop note"
