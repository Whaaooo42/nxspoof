"""
tests/test_forksafe.py - the run's ways of touching the Mac, done in a forked child: none may use macOS's frameworks there.

Until D33 NXSPOOF's background run and its keeper were forked without exec, and macOS doesn't support CoreFoundation,
Objective-C or SystemConfiguration in such a child: from macOS 26.1 it crashes (FB21364061, seen by two users on
macOS 27, Nov 25). Since D33 every NXSPOOF process starts fresh (macos/process.py started) and the guardrail "no fork
that carries on" keeps it that way; this test is the second net: the run-side paths must stay safe even in a fork.
On macOS 15 a forked child only crashes when its parent had another thread alive at the fork, so this test starts
one first (the canary): a run-side path that reaches those frameworks then aborts here too, on the developer's Mac.
It would have caught both crashes found on Nov 25.
"""

import os
import threading
import time
import unittest
import warnings

from nxspoof.app.system import System
from nxspoof.macos import machine, sharing, user
from nxspoof.net import policy, selftest, web

PORT = 8091
WAIT = 30                                    # seconds the child gets for every path (each starts a short helper process)

# The run's Mac-touching paths, in a forked child of a parent with another thread alive.
class ForkSafe(unittest.TestCase):
    # Every path in the child: it must exit 0, not die from a signal (SIGABRT, SIGSEGV) or hang.
    def test_run_paths_in_a_threaded_fork(self):
        server = web.TestWebServer("127.0.0.1", PORT, policy.Policy(), lambda *a: None)
        server.start()                                               # its thread is alive at the fork, like the run's
        canary_stop = threading.Event()
        canary = threading.Thread(target=canary_stop.wait, daemon=True)
        canary.start()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)   # Python 3.12+ warns about forking with threads: that's the point
                pid = os.fork()
            if pid == 0:
                os._exit(child_paths())
            status = wait_for(pid, WAIT)
        finally:
            canary_stop.set()
            server.stop()
        self.assertIsNotNone(status, "the forked child hung (more than %d s)" % WAIT)
        self.assertFalse(os.WIFSIGNALED(status), "the forked child died from signal %d: a run-side path used macOS's frameworks "
                         "(look at ~/Library/Logs/DiagnosticReports for the Python crash report)" % (os.WTERMSIG(status) if os.WIFSIGNALED(status) else 0))
        self.assertEqual(os.WEXITSTATUS(status), 0, "a run-side path failed in the forked child")

# In the child: each path the run takes to the Mac -> exit code 0 if all worked (a crash never returns).
def child_paths():
    try:
        system = System()
        system.hotspot_settings()                                    # the hotspot's name: SCPreferences, through scprefs.py
        sharing.read_nat()                                           # Internet Sharing's settings, the same way
        machine.facts()                                              # the "this Mac" line
        machine.personal_words()                                     # the problem report's names
        user.home()
        user.account_names()
        if selftest.ask_web("127.0.0.1", PORT, "example.com")[0] != 403:   # the self-test's web check (no proxy lookup)
            return 3
        time.strftime("%H:%M:%S", time.localtime())                  # every log line's time
        return 0
    except Exception:
        return 2

# Wait up to `seconds` for a child -> its status, or None if it's still running (then it's killed).
def wait_for(pid, seconds):
    end = time.time() + seconds
    while time.time() < end:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return status
        time.sleep(0.05)
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    return None
