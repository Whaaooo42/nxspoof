"""
nxspoof.app.runfollow - the dashboard finds the run and follows it from its files (D30: no web API in the run, no relay).

  RunFollow   checked about twice a second by the control center (app/center.py):
                - a run holds the lock (output/runfile.py) and has said where it is (run.json): the dashboard shows it;
                  its records (logs/<run>.jsonl) are read from the first line on, then as they grow, into the page's live
                  log; the page's snapshot of the run is run_state.json (app/runstate.py);
                - the lock is free again: the rest of its records, then the run has ended.
              Its two buttons: ■ Stop (a Stop's note, stop.json, plus a signal to the run named in run.json, like
              nx_spoof.py stop: no password) and
              Open in FileZilla (a console's FTP address, from the run's state). Everything else the page changes goes to
              settings.json, which the run reads again (features/settingswatch.py).
"""

import json
import os

from nxspoof.core.controls import ControlError
from nxspoof.core.fmt import norm_mac
from nxspoof.dashboard.server import js_spans
from nxspoof.features import runlog
from nxspoof.output import runfile

# One control center's view of the running run.
class RunFollow(object):
    # center: the Center (places, server, system, note, run_appeared, run_ended).
    def __init__(self, center):
        self.center = center
        self.info, self.pos, self.rest = None, 0, ""

    # True while a run is shown here.
    def present(self):
        return self.info is not None

    # The run's process id (from run.json), or None.
    def pid(self):
        return (self.info or {}).get("pid")

    # The page's snapshot of the run ({} until the run has written it).
    def state(self):
        return runfile.read_state(self.center.places.runtime)

    # Look now: a run that appeared is shown, its new records go to the page, a run whose lock is free has ended.
    def check(self):
        runtime = self.center.places.runtime
        held = runfile.held(runtime)
        if self.info is None:
            info = runfile.read(runtime) if held else {}
            if not info.get("records"):
                return                       # no run, or one that hasn't opened its log yet
            self.info, self.pos, self.rest = info, 0, ""
            self.center.run_appeared(info)
        self._read()
        if not held:
            self.info = None
            self.center.run_ended()

    # The run's records written since the last look, as the page's live events.
    def _read(self):
        path = os.path.join(self.center.places.logs, self.info["records"])
        try:
            with open(path, encoding="utf-8") as f:
                f.seek(self.pos)
                self.rest += f.read()
                self.pos = f.tell()
        except OSError:
            return
        *complete, self.rest = self.rest.split("\n")
        for line in (l for l in complete if l.strip()):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            for ev in runlog.record_events(record):
                ev["tech_names"] = js_spans(ev["tech"], ev["tech_names"]) if ev["tech"] else []
                ev["casual_names"] = js_spans(ev["casual"], ev["casual_names"]) if ev["casual"] else []
                self.center.server.publish(ev)

    # The page's buttons for the run: ■ Stop, Open in FileZilla.
    def register_controls(self, controls):
        controls.add("stop", self.stop, available=self.present)
        controls.add("open_filezilla", self.open_filezilla, available=lambda: self.present() and self.center.system.filezilla_installed())

    # ■ Stop: a Stop's note for the run (stop.json: it looks every half second), and a signal too, like nx_spoof.py stop (no
    # password: the run is you; a root run, --no-keeper, takes the note all the same).
    def stop(self, payload=None):
        pid = self.pid()
        if not pid:
            raise ControlError("NXSPOOF isn't running")
        runfile.ask_stop(self.center.places.runtime, "dashboard")
        try:
            self.center.system.stop_run(pid)
        except PermissionError:
            pass
        self.center.note("stop requested (process %s: a note in stop.json, and a signal)" % pid, casual="🛑 Stopping NXSPOOF…")
        return {"ok": True, "message": "Stopping…"}

    # Open in FileZilla: {"mac": ...} -> FileZilla on that console's FTP address (as the run's state has it).
    def open_filezilla(self, payload):
        mac = norm_mac(str((payload or {}).get("mac", "")))
        url = next((c.get("ftp", {}).get("url") for c in self.state().get("consoles", []) if c.get("mac") == mac), None)
        if not url:
            raise ControlError("no FTP address known for that console")
        self.center.system.open_filezilla(url)
        self.center.note("FileZilla requested for %s (%s)" % (mac, url))
        return {"ok": True, "message": "Opening FileZilla on %s" % url}
