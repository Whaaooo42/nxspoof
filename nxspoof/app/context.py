"""
nxspoof.app.context - the objects of one run, created once and handed to whoever needs them.

  bus        the event bus (outputs are sinks, features are listeners)
  config     settings + options for this run (RunConfig)
  store      settings.json (live changes, console edits)
  registry   who each console is; activity: what each did; policy: what may be reached
  controls   dashboard buttons and other commands
  system     the one door to the Mac (app/system.py; a fake one in the simulator)

Stopping: anything may call request_stop(reason) (Ctrl+C, a closed window, SIGTERM, the dashboard).
The first request wins and later ones are ignored; the main thread waits on `stopping`.
"""

import threading
import time

from nxspoof.consoles.activity import ConsoleActivity
from nxspoof.consoles.registry import ConsoleRegistry
from nxspoof.net.policy import Policy
from nxspoof.settings import schema

PHASES = ("starting", "waiting", "running", "stopping", "stopped")

# One run's shared objects and its phase / stop flag.
class Context(object):
    # places: where NXSPOOF's files are (core/places.py: the logs, the dashboard's link file; one temporary folder in the
    # simulator); prepared: what preparing them did ([(level, text)], logged once the run's log is open).
    def __init__(self, bus, config, store, system, places, prepared=()):
        self.bus, self.config, self.store, self.system = bus, config, store, system
        self.places, self.prepared = places, list(prepared)
        overrides = {n: config.get(n) for n in config.values if config.source(n).startswith("--")}
        self.registry = ConsoleRegistry(store, overrides=overrides)
        self.activity = ConsoleActivity(leases_lookup=system.mac_for_ip)
        self.policy = Policy(probe_hosts=(config.extra("probe") or "").split(","))
        self.phase = "starting"
        self.started = time.time()
        self.stopping = threading.Event()
        self.stop_reason = None
        self._lock = threading.Lock()

    # Ask the run to stop (first request wins); returns True if this request is the one that counts.
    def request_stop(self, reason):
        with self._lock:
            if self.stop_reason is not None:
                return False
            self.stop_reason = reason
        self.stopping.set()
        return True

    # The value this run uses for a setting right now: an option given for this run wins; a "live" setting is
    # read from settings.json as it is now (the dashboard can change it); a "restart" one stays as it was at start; a
    # value with no setting (app/config.py RUN_ONLY) is its default or its --flag.
    def setting(self, name):
        if self.config.source(name).startswith("--") or name not in schema.BY_NAME:
            return self.config.get(name)
        if schema.field(name).apply == "live":
            return self.store.get(name)
        return self.config.get(name)

    # The option overriding a setting for this run (e.g. "--keep-sharing"), or None.
    def overridden_by(self, name):
        source = self.config.source(name)
        return source if source.startswith("--") else None

    # Move to another phase (starting, waiting, running, stopping, stopped).
    def set_phase(self, phase):
        if phase not in PHASES:
            raise ValueError("unknown phase %r" % phase)
        self.phase = phase
