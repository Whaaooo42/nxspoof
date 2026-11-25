"""
nxspoof.macos.process - a run's life as a process: started in the background with an answer, stopped by a signal, started
again from the program copy. Never logs: returns values or raises.

  background(output)        fork: here ("waiter", reading end, the worker's pid); in the new process ("worker", writing end,
                            None), on its own (setsid: no Terminal window or hang-up reaches it), its output into `output`
                            (an open file, output/logfile.py's)
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

# Fork into a waiter (here) and a worker (the new process, alone in its own session, stdin from /dev/null and its output
# into the open file `output`). Returns ("waiter", fd to read the answer from, the worker's pid) or ("worker", fd to answer on, None).
def background(output):
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid:
        os.close(write_end)
        return "waiter", read_end, pid
    os.close(read_end)
    os.setsid()
    with open(os.devnull) as nothing:
        os.dup2(nothing.fileno(), 0)
    os.dup2(output.fileno(), 1)
    os.dup2(output.fileno(), 2)
    return "worker", write_end, None

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
