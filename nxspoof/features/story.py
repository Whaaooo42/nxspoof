"""
nxspoof.features.story - the casual narrator: turns what consoles do into friendly lines.

It listens to the tracker's facts and says (casual-only lines, never in the log file):

  console.connected   "🎮 Switch 2 connected" / "🎮 Switch 2 is back" / "🙋 A console I don't know..."
  console.online      "🌐 Switch 2 thinks it's online, all good"
  console.left        "👋 Switch 2 left (it was here 34 seconds)"

and every few seconds, at most once a minute per console, how many attempts were blocked so far.
"""

import threading
import time

from nxspoof.core.fmt import casual_duration, plural
from nxspoof.core.text import compose

KINDS = ("console.connected", "console.online", "console.left")

# Says the friendly lines for console facts.
class Story(object):
    CHECK_EVERY = 5.0                              # how often the blocked counts are looked at
    TELL_EVERY = 60.0                              # at most one "blocked so far" line per console per minute

    # registry: who is who; activity: blocked counts; clock: replaceable for tests.
    def __init__(self, bus, registry, activity, clock=time.time):
        self.bus, self.registry, self.activity, self.clock = bus, registry, activity, clock
        self.told = {}                             # mac -> blocked total last told
        self.told_at = {}                          # mac -> when it was told
        self._stop = threading.Event()

    # Listen for console facts (and start the "blocked so far" ticker unless ticker=False).
    def attach(self, ticker=True):
        self.bus.add_listener(self.on_fact, kinds=KINDS)
        if ticker:
            self.start_ticker()

    # Start the "blocked so far" ticker (the run starts it once it's READY).
    def start_ticker(self):
        threading.Thread(target=self._loop, daemon=True).start()

    # One console fact -> one friendly line.
    def on_fact(self, event):
        mac, name = event.mac, self.registry.name(event.mac)
        if event.kind == "console.connected":
            if not event.data.get("known", True):
                self.bus.say("🙋 A console I don't know just connected (%s)" % mac, mac=mac)
            elif event.data.get("visit", 1) > 1:
                self.bus.say(compose("🎮 %s is back", name), mac=mac)
            else:
                self.bus.say(compose("🎮 %s connected", name), mac=mac)
        elif event.kind == "console.online":
            self.bus.say(compose("🌐 %s thinks it's online, all good", name), mac=mac)
        elif event.kind == "console.left":
            self.bus.say(compose("👋 %s left (it was here %s)", name, casual_duration(event.data.get("seconds", 0))), mac=mac)

    # Tell new blocked totals (at most once a minute per console); called every CHECK_EVERY seconds.
    def tick(self, now=None):
        now = self.clock() if now is None else now
        with self.activity.lock:
            records = list(self.activity.records.values())
        for r in records:
            total = self.activity.blocked_total(r.mac)
            if total > self.told.get(r.mac, 0) and now - self.told_at.get(r.mac, 0) >= self.TELL_EVERY:
                self.told[r.mac], self.told_at[r.mac] = total, now
                self.bus.say(compose("🛡️ %s: %s to reach the internet blocked so far", self.registry.name(r.mac), plural(total, "attempt")),
                             mac=r.mac)

    # Tick until stopped.
    def _loop(self):
        while not self._stop.wait(self.CHECK_EVERY):
            self.tick()

    # Stop the ticker.
    def stop(self):
        self._stop.set()
