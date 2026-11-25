"""
nxspoof.app.hotspotwatch - the hotspot can vanish under a run (D29). On Nov 18 at 02:39 macOS lost the virtual cable,
Internet Sharing stopped by itself, the Switch left, and the dashboard still said "running". This watch notices, says
so, and puts the hotspot back.

  HotspotWatch   attached by app/launch.py, started once the run is READY (Run.on_ready). Every CHECK_EVERY seconds: is
                 the hotspot's network interface still there, with the Mac's address? When it isn't:
                   - a warning (fact hotspot.down, with why), and the 📡 chip says DOWN (Run.hotspot_down);
                   - recovery, through the keeper (it can already do both, D24): the virtual cable back in, and Internet
                     Sharing on again when the run turned it on; then it waits WAIT_UP seconds for the hotspot;
                   - back: the self-test again (CHECK lines), then hotspot.back. The firewall needs nothing: its rules
                     name the interface, and pf applies them as soon as it's back;
                   - not back: tried again RETRY_AFTER seconds later, then every THEN_EVERY seconds, until it's back or a
                     Stop (D30: only a Stop stops NXSPOOF). After the listed tries, a PROBLEM once (hotspot.lost: still
                     down, trying every minute; the 📡 chip stays DOWN). The Mac asleep, the cable lost, Internet Sharing
                     turned off in System Settings: all put back (to turn the hotspot off, Stop NXSPOOF).
                 Not for dry runs (--ip). Stopped first when the run stops (it's one of the run's monitors), so turning
                 sharing off at the end never looks like a drop.
"""

import itertools
import threading
import time

from nxspoof.core.fmt import error_text, error_trace
from nxspoof.core.text import outcome
from nxspoof.net import selftest

CHECK_EVERY = 5                              # seconds between two looks at the hotspot
WAIT_UP = 20                                 # seconds to wait for the hotspot after putting it back
RETRY_AFTER = (5, 15, 30)                    # seconds before trying again, one try per entry...
THEN_EVERY = 60                              # ...then every this many seconds, until it's back or a Stop (it never gives up)

# Watches one run's hotspot and puts it back when it vanishes.
class HotspotWatch(object):
    # run: the Run (ctx, ip, log, monitors, on_ready, sharing_auto, hotspot_down, bind / dns_port / http_port / answer_ip);
    # sleep: replaceable for tests.
    def __init__(self, run, sleep=time.sleep):
        self.run, self.sleep = run, sleep
        self.stopped = threading.Event()
        self.look_failed = False                 # a failed look was said once (a watch that can't look shouldn't be silent)
        run.on_ready.append(self.start)

    # Start watching (called once the run is READY); nothing for a dry run.
    def start(self):
        if self.run.ctx.config.extra("ip"):
            return
        self.run.monitors.append(self)
        threading.Thread(target=self._loop, daemon=True).start()

    # Stop watching (the run is stopping).
    def stop(self):
        self.stopped.set()

    # Look every CHECK_EVERY seconds until stopped.
    def _loop(self):
        while not self.stopped.wait(CHECK_EVERY):
            if not self.run.ctx.stopping.is_set() and not self.up():
                self.recover()

    # True while the hotspot's interface is there with the Mac's address.
    def up(self):
        try:
            found = self.run.ctx.system.find_sharing_interface()
        except Exception as e:
            if not self.look_failed:         # can't tell: no alarm, but said once
                self.look_failed = True
                self.run.log("SHARING", "the hotspot watch couldn't look at the hotspot (%s) - it can't tell if it goes down" % error_text(e),
                             level="warn", details=error_trace(e))
            return True
        return bool(found) and found[1] == self.run.ip

    # Why it went down, in words for the log.
    def _why(self):
        try:
            return "its network interface vanished" if self.run.ctx.system.sharing_running() else "Internet Sharing went off"
        except Exception:
            return "its network interface vanished"

    # The hotspot is gone: say so, then put it back until it is (or a Stop); returns True if it's back. max_tries: give up
    # after that many tries (tests only; None: never).
    def recover(self, max_tries=None):
        run, why = self.run, self._why()
        run.hotspot_down = True
        run.log("SHARING", "the hotspot %s (%s) - putting it back" % (outcome("went DOWN", "down"), why), level="warn",
                fact="hotspot.down", why=why, casual="⚠️ The hotspot stopped (%s). Putting it back…" % why)
        for attempt in itertools.count(1):
            if self.stopped.is_set() or run.ctx.stopping.is_set() or (max_tries and attempt > max_tries):
                return False
            self._put_back(attempt)
            if self._wait_up():
                self._back(attempt)
                return True
            if attempt == len(RETRY_AFTER):
                self._still_down(attempt)
            if self.stopped.wait(RETRY_AFTER[attempt - 1] if attempt <= len(RETRY_AFTER) else THEN_EVERY):
                return False

    # Still down after the listed tries: said once (fact hotspot.lost); the tries go on every THEN_EVERY seconds.
    def _still_down(self, attempt):
        self.run.log("PROBLEM", "the hotspot is still down after %d tries - trying again every %d s until it's back (Stop NXSPOOF to give up)"
                     % (attempt, THEN_EVERY), level="problem", fact="hotspot.lost", attempt=attempt,
                     casual="😕 The hotspot is still down. I keep trying every minute (■ Stop to give up).")

    # One try: the virtual cable back in (needs the keeper, or root), Internet Sharing on again if the run turned it on.
    def _put_back(self, attempt):
        run, system = self.run, self.run.ctx.system
        if system.is_root():
            try:
                found, _ = system.plug_cable()
                run.log("SHARING", "try %d: virtual cable %s" % (attempt, "still in" if found == "already" else "plugged back in"))
            except Exception as e:
                run.log("SHARING", "try %d: couldn't plug the virtual cable back in (%s)" % (attempt, error_text(e)), level="warn",
                        details=error_trace(e))
        if run.sharing_auto:
            try:
                system.set_sharing(True)
                run.log("SHARING", "try %d: Internet Sharing turned ON again" % attempt)
            except Exception as e:
                run.log("SHARING", "try %d: couldn't turn Internet Sharing on again (%s)" % (attempt, error_text(e)), level="warn",
                        details=error_trace(e))

    # Wait up to WAIT_UP seconds for the hotspot; True if it's back.
    def _wait_up(self):
        for _ in range(WAIT_UP):
            if self.up():
                return True
            if self.stopped.is_set():
                return False
            self.sleep(1)
        return self.up()

    # Back: the self-test again, then say so (fact hotspot.back).
    def _back(self, attempt):
        run = self.run
        checks = selftest.run_selftest(run.bind, run.dns_port, run.http_port, run.answer_ip, on_check=lambda ch: run.log(
            "CHECK", selftest.check_line(ch), fact="selftest.check", passed=ch.passed))
        passed = all(ch.passed for ch in checks)
        run.hotspot_down = False
        run.log("SHARING", "the hotspot %s again (%s, try %d)%s" % (outcome("is UP", "up"), run.ip, attempt,
                "; the self-test passed" if passed else " - but the self-test FAILED, see the CHECK lines"),
                level="info" if passed else "warn", fact="hotspot.back", attempt=attempt, selftest=passed,
                casual="✅ The hotspot is back. Your consoles usually rejoin by themselves." if passed else
                       "😕 The hotspot is back, but my self-check failed: the consoles may not pass their test. Stop and start NXSPOOF.")
