"""
nxspoof.macos.process - a run's life as a process: started in the background with an answer, stopped by a signal, started
again from the program copy. Never logs: returns values or raises.

  background(command, output, keep, handover)   a brand-new process (fork + exec in one go, nothing run in between: macOS
                            supports nothing else after a fork, and from macOS 26.1 the frameworks crash in a forked child,
                            FB21364061), on its own (its own session: no Terminal window or hang-up reaches it), its output
                            into `output`, handed the write end of its answer pipe, the descriptors `keep` and a hand-over
                            pipe (what the start knew in memory) -> ("waiter", reading end, its pid)
  started(command, fds, values, handover, **popen)   the same start, for any brand-new process (the keeper's run too)
  take_over(spec)           in the new process: its handed descriptors made private again (never handed further), the
                            hand-over read -> {"answer", "lock", "channel": their numbers, "handover": what was handed over}
  reap(pid)                 wait for a process started here -> its exit code (128 + a signal when a signal ended it)
  check_not_root()          the run, first thing: it isn't root and can't become root again (raises PermissionError)
  answer(fd, obj)           the worker's one answer to the waiter (a JSON line), then its end closed
  wait_answer(fd, timeout)  the waiter: the answer (dict), None when the worker ended without one, "timeout"
  stop(pid)                 SIGTERM to a run: True if sent, False if it's gone (PermissionError: it isn't yours)
  restart_from(folder, argv)   this command again, from another copy of the program (os.execv: the same process)
  keep_awake(pid)           no idle sleep until that process ends (/usr/bin/caffeinate -i -w <pid>), like a video player
                            during a film: on battery an idle Mac sleeps and the hotspot with it (Nov 24: ten minutes into a
                            run, and on waking macOS rejoined the home Wi-Fi). The screen can still turn off; closing the
                            lid still sleeps the Mac (macOS's rule). Returns the running caffeinate (raises OSError)
  let_sleep(proc)           end it now (the run is stopping); nothing if it's already gone
"""

import json
import os
import select
import signal
import subprocess
import sys
import time

CAFFEINATE = "/usr/bin/caffeinate"
FD_NAMES = ("answer", "lock", "channel", "handover")     # the descriptors a started process may be handed
_STARTED = {}                                            # pid -> its Popen (reap() waits through it, so it's reaped once)

# Start a brand-new process on its own (its own session, stdin from /dev/null, its output into the open file `output`), handed
# the write end of its answer pipe, the descriptors `keep` ({name: fd}) and `handover` (JSON-able: what the start knew in
# memory). command(spec) gives its words once the handed descriptors' numbers are known. -> ("waiter", fd to read its answer
# from, its pid).
def background(command, output, keep=None, handover=None):
    read_end, write_end = os.pipe()
    try:
        pid = started(command, dict(keep or {}, answer=write_end), {}, handover, stdin=subprocess.DEVNULL, stdout=output,
                      stderr=output, start_new_session=True)
    finally:
        os.close(write_end)
    return "waiter", read_end, pid

# Start a brand-new process (exec, nothing in between): handed the descriptors `fds` ({name: fd}), the plain `values`
# ({name: value}) and a hand-over pipe holding `handover` (JSON); command(spec) gives its words from the spec ({names: numbers
# and values}), and popen's options are subprocess's (user=, group=, env=...). Returns its pid (reap(pid) waits for it).
def started(command, fds, values, handover, **popen):
    hand_r, hand_w = os.pipe()
    try:
        os.write(hand_w, json.dumps(handover).encode("utf-8"))
    finally:
        os.close(hand_w)
    try:
        spec = dict(fds, handover=hand_r, **values)
        proc = subprocess.Popen(command(spec), pass_fds=tuple(fds.values()) + (hand_r,), close_fds=True, **popen)
    finally:
        os.close(hand_r)
    _STARTED[proc.pid] = proc
    return proc.pid

# In a process started by background() or started(): its handed descriptors made private again (never handed further: a lock
# that leaked into another program would look like a run forever), the hand-over read and closed -> {name: number or value,
# "handover": what was handed over}.
def take_over(spec):
    out = dict(spec)
    for name, value in spec.items():
        if name in FD_NAMES and isinstance(value, int):
            os.set_inheritable(value, False)
    data = b""
    while True:
        chunk = os.read(spec["handover"], 65536)
        if not chunk:
            break
        data += chunk
    os.close(spec["handover"])
    out["handover"] = json.loads(data.decode("utf-8")) if data else None
    return out

# The run, first thing: really not root, and unable to become root again (its user, group and groups were set before its
# exec). Raises PermissionError otherwise (the run then stops before doing anything).
def check_not_root():
    if 0 in (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) or 0 in os.getgroups():
        raise PermissionError("the run is still root (uid %d, gid %d): it refuses to go on" % (os.getuid(), os.getgid()))
    try:
        os.setuid(0)
    except PermissionError:
        return
    raise PermissionError("the run could become root again: it refuses to go on")

# Wait for a process started here -> its exit code (128 + the signal's number when a signal ended it).
def reap(pid):
    proc = _STARTED.pop(pid, None)
    if proc is not None:
        code = proc.wait()
        return 128 - code if code < 0 else code
    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return 128 + os.WTERMSIG(status) if os.WIFSIGNALED(status) else 1

# The worker's answer (a JSON-able dict), once; the end is closed after it (obj None: closed without answering). A gone
# waiter is fine (nobody's listening).
def answer(fd, obj):
    try:
        if obj is not None:
            os.write(fd, (json.dumps(obj) + "\n").encode("utf-8"))
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

# The waiter: read the worker's answer -> the dict; None if the worker (and everyone holding the end) closed it without
# answering; "timeout" after `timeout` seconds. The end is closed either way.
def wait_answer(fd, timeout):
    data, deadline = b"", time.time() + timeout
    try:
        while b"\n" not in data:
            left = deadline - time.time()
            if left <= 0:
                return "timeout"
            if not select.select([fd], [], [], left)[0]:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                return None
            data += chunk
    finally:
        os.close(fd)
    try:
        found = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
    except ValueError:
        return None
    return found if isinstance(found, dict) else None

# Ask a run to stop (SIGTERM: the dashboard's Stop, nx_spoof.py stop). True if sent; False if no such process.
# PermissionError when it isn't yours (a single root process, --no-keeper).
def stop(pid):
    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True

# Run this command again from the program copy in `folder` (its nx_spoof.py, the same Python, the same words); never returns.
def restart_from(folder, argv):
    os.chdir(folder)
    os.execv(sys.executable, [sys.executable, "-B", os.path.join(folder, "nx_spoof.py")] + list(argv))

# Hold "no idle sleep" until process `pid` ends -> the running caffeinate (raises OSError).
def keep_awake(pid, start=subprocess.Popen):
    return start([CAFFEINATE, "-i", "-w", str(int(pid))], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Let the Mac sleep again: end the caffeinate keep_awake started (nothing if it's already gone).
def let_sleep(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
