"""
nxspoof.features.settingswatch - a run follows settings.json while it runs (D30: the dashboard writes it, the run reads it).

  SettingsWatch   one of the run's monitors. Every EVERY seconds it looks at settings.json (its time and size); changed:
                  the store reads it again, and each difference is said once, as the fact the rest of the run listens for:
                    a setting   SETTINGS line, fact settings.changed (name, value, apply: live now, or at the next start)
                    a console   fact console.added / console.changed (which fields) / console.removed
                  A file that doesn't read (a typo while someone edits it by hand) is said, and the settings in force stay.
"""

import os
import threading

from nxspoof.core.text import compose
from nxspoof.settings import schema
from nxspoof.settings.store import SettingsError

EVERY = 2.0                                  # seconds between two looks at settings.json

# The file's identity right now (time and size), or None when it's missing.
def stamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size

# The settings and consoles as the store has them now: ({setting: value}, {mac: console entry}).
def snapshot(store):
    return {f.name: store.get(f.name) for f in schema.FIELDS}, {c["mac"]: dict(c) for c in store.consoles()}

# Watches settings.json for one run.
class SettingsWatch(object):
    # bus: the run's bus; store: its SettingsStore; registry: names for the lines; every: seconds between looks (tests).
    def __init__(self, bus, store, registry, every=EVERY):
        self.bus, self.store, self.registry, self.every = bus, store, registry, every
        self.stopped = threading.Event()
        self.seen = stamp(store.path)

    # Start looking, on its own thread.
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    # Stop looking (the run is stopping).
    def stop(self):
        self.stopped.set()

    # Look every `every` seconds until stopped.
    def _loop(self):
        while not self.stopped.wait(self.every):
            self.check()

    # One look: read the file again if it changed, and say what's different. Returns the number of differences.
    def check(self):
        now = stamp(self.store.path)
        if now == self.seen or now is None:
            return 0
        self.seen = now
        before = snapshot(self.store)
        try:
            self.store.load()
        except (SettingsError, OSError) as e:
            self.bus.log("SETTINGS", "settings.json changed but doesn't read (%s) - the settings in force stay" % e, level="warn",
                         casual="⚠️ settings.json changed but has a mistake: I keep the settings I had.")
            return 0
        after = snapshot(self.store)
        return self._settings(before[0], after[0]) + self._consoles(before[1], after[1])

    # Each setting that changed: one SETTINGS line and its fact.
    def _settings(self, old, new):
        changed = [f for f in schema.FIELDS if old[f.name] != new[f.name]]
        for f in changed:
            when = "applies now" if f.apply == "live" else "applies at the next start"
            self.bus.log("SETTINGS", "settings.json changed: %s %s -> %s (%s)" % (f.name, old[f.name], new[f.name], when),
                         fact="settings.changed", name=f.name, value=new[f.name], apply=f.apply)
        return len(changed)

    # Each console added, changed or removed: its fact (the services follow these).
    def _consoles(self, old, new):
        count = 0
        for mac in sorted(set(old) | set(new)):
            if mac not in old:
                self.bus.log("SETTINGS", compose("settings.json changed: console %s added", self.registry.name(mac)), mac=mac,
                             fact="console.added", name=str(self.registry.name(mac)))
            elif mac not in new:
                self.bus.log("SETTINGS", "settings.json changed: console %s removed" % str(old[mac].get("name") or mac), mac=mac,
                             fact="console.removed")
            elif old[mac] != new[mac]:
                fields = sorted(k for k in set(old[mac]) | set(new[mac]) if old[mac].get(k) != new[mac].get(k))
                self.bus.log("SETTINGS", compose("settings.json changed: console %s (%s)", self.registry.name(mac), ", ".join(fields)),
                             mac=mac, fact="console.changed", changed=fields)
            else:
                continue
            count += 1
        return count
