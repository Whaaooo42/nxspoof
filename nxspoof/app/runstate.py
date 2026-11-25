"""
nxspoof.app.runstate - the run's side of the files it shares with the dashboard (D30: the run talks to nobody).

  RunState   started with the run (Run.start), stopped last at its end:
               - every EVERY seconds it builds the dashboard's snapshot of the run (dashboard/state.py: phase, hotspot,
                 firewall, consoles, settings in force) and writes it to run_state.json in the runtime folder
                 (output/runfile.py) when it changed; the dashboard (app/runfollow.py) reads it. The run's lines and facts
                 reach the dashboard through its records (logs/<run>.jsonl);
               - settings.json, which the dashboard writes, is read again when it changes (features/settingswatch.py:
                 one of the run's monitors, stopped with them).
"""

import os
import threading

from nxspoof.dashboard.state import build_state
from nxspoof.features.settingswatch import SettingsWatch
from nxspoof.output import runfile

EVERY = 1.0                                  # seconds between two looks at the run's state

# No controls in the snapshot: the dashboard has its own (app/center.py).
class NoControls(object):
    # Nothing to press here.
    def available(self):
        return []

# The run's state file, kept up to date while the run lasts.
class RunState(object):
    # run: the Run (its context, hotspot, firewall, log file).
    def __init__(self, run):
        self.run, self.ctx = run, run.ctx
        self.stopped, self.written = threading.Event(), None
        self.settings = SettingsWatch(self.ctx.bus, self.ctx.store, self.ctx.registry)

    # What the dashboard shows about the run itself, plus its consoles and settings (dashboard/state.py).
    def state(self):
        run, ctx, c = self.run, self.ctx, self.ctx.config
        info = {"phase": ctx.phase, "started": ctx.started, "hotspot_ip": None if c.extra("ip") else run.ip, "hotspot_name": run.wifi_name(),
                "hotspot_down": bool(run.hotspot_down), "pid": os.getpid(),
                "dry_run": bool(c.extra("ip")), "firewall": bool(c.get("spoof.firewall")),
                "firewall_loaded": bool(run.firewall_loaded and c.get("spoof.firewall")), "auto_sharing": bool(c.get("spoof.auto_sharing")),
                "ftp_watch": bool(c.get("ftp.watch")), "console_mode": c.get("display.console"),
                "filezilla": bool(ctx.system.filezilla_installed()),
                "log_file": os.path.relpath(run.log_path, ctx.places.logs) if run.log_path else None}
        return build_state(info, ctx.registry, ctx.activity, ctx.store, NoControls(), in_force=ctx.setting, overridden_by=ctx.overridden_by)

    # Write the state now if it changed (the clock fields don't count); a failure is only skipped (tried again next time).
    def write(self):
        st = self.state()
        same = dict(st, run=dict(st["run"], now=None))
        if same == self.written:
            return
        try:
            runfile.write_state(self.ctx.places.runtime, st, give=self.ctx.system.give_to_user)
            self.written = same
        except OSError:
            pass

    # Write it now and every EVERY seconds until stopped; follow settings.json (a monitor of the run's: stopped with them).
    def start(self):
        self.write()
        self.settings.start()
        self.run.monitors.append(self.settings)

        # The loop on its own thread.
        def loop():
            while not self.stopped.wait(EVERY):
                self.write()
        threading.Thread(target=loop, daemon=True).start()

    # Last state written (phase "stopped"), then the file removed: the run has ended.
    def stop(self):
        self.stopped.set()
        self.write()
        runfile.clear_state(self.ctx.places.runtime)
