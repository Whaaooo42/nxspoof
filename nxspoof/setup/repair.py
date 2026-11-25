"""
nxspoof.setup.repair - the checklist's undoing side (setup/checklist.py's Checklist inherits it):

  uninstall  undo what setup changed (asks first): Thunderbolt Bridge back to automatic, virtual cable out, sharing off
  cleanup    after a run that didn't stop cleanly: NXSPOOF's leftover firewall rules out, Internet Sharing off, the virtual
             cable out (a power cut can leave it in; every start plugs it in again)
Each step is a line and a fact (checklist.undo / checklist.cleanup), and each ends with checklist.done, so the
dashboard's Configure (This Mac) follows them from the records. All three need admin rights.
"""

from nxspoof.core.fmt import error_text, error_trace
from nxspoof.output import datafolder, runfile

# Undo and clean up, for the Checklist (self.m: the Machine, self.bus, self.ask, self.steps).
class Repairs(object):
    # Undo what setup changed (asks first); returns 0 if everything was undone (or you said no: nothing changed).
    def uninstall(self):
        self.header("UNDO", "🧹 Undoing what setup changed (your files stay).")
        if not self._root_for("UNDO", "uninstall") or self.run_is_active("UNDO", "uninstall"):
            return 1
        if not self.ask("   Put Thunderbolt Bridge back on automatic, unplug the virtual cable and turn Internet Sharing off?", False):
            self.bus.log("UNDO", "cancelled", casual="⏭️ Nothing changed.", fact="checklist.done", command="uninstall", todo=0, cancelled=True)
            return 0
        return self._undo_all("uninstall")

    # Every step's undo, last step first; command: what the closing fact says ("uninstall").
    # What is put back: this Mac as it was before NXSPOOF (its record, data folder) or macOS's defaults (self.undo_to).
    def _undo_all(self, command):
        failed, notes = 0, datafolder.before(self.m.places.config)
        self.m.before = notes
        self.m.undo_to = self.undo_to or ("before" if notes else "defaults")
        back = self.m.undo_to == "before" and notes
        self.bus.log("UNDO", "putting back: %s" % ("this Mac as it was before NXSPOOF changed it (%s)" % ", ".join(sorted(notes))
                                                   if back else "macOS's defaults"),
                     casual="↩️ Putting back %s." % ("how this Mac was before NXSPOOF" if back else "macOS's defaults"))
        for step in reversed(self.steps):
            if step.undo is None:
                continue
            try:
                done = step.undo(self.m)
                self.bus.log("UNDO", "%-12s %s" % (step.name, done), casual="🧹 %s: %s" % (step.title, done),
                             fact="checklist.undo", step=step.name, title=step.title, state="undone", says=done)
            except Exception as e:
                failed += 1
                self.bus.log("UNDO", "%-12s FAILED: %s" % (step.name, error_text(e)), level="problem", casual="✋ %s: couldn't undo (%s)" % (step.title, e),
                             fact="checklist.undo", step=step.name, title=step.title, state="failed", says=str(e), details=error_trace(e))
        extra = {"fact": "checklist.done", "command": command, "todo": failed} if command else {}
        self.bus.log("UNDO", "done" if not failed else "%d step(s) failed" % failed, level="info" if not failed else "warn",
                     casual="✅ Done. NXSPOOF's changes are undone." if not failed else "✋ Some steps couldn't be undone (see above).", **extra)
        return 1 if failed else 0

    # After a run that didn't stop cleanly: NXSPOOF's leftover firewall rules out, Internet Sharing off; 0 when both are done.
    def cleanup(self):
        self.header("CLEAN", "🧹 Cleaning up after a run that didn't stop cleanly.")
        if not self._root_for("CLEAN", "cleanup"):
            return 1
        system = self.m.system
        if self.run_is_active("CLEAN", "cleanup"):
            return 1
        failed = self._clean("firewall", "leftover firewall rules", lambda: firewall_out(system))
        failed += self._clean("sharing", "Internet Sharing", lambda: sharing_out(system))
        failed += self._clean("cable", "the virtual cable", lambda: cable_out(system))
        self.bus.log("CLEAN", "done" if not failed else "%d part(s) failed" % failed, level="info" if not failed else "warn",
                     casual="✅ All clean." if not failed else "✋ Something couldn't be cleaned up (see above).",
                     fact="checklist.done", command="cleanup", todo=failed)
        return 1 if failed else 0

    # One part of the clean up: do() says what happened; returns 1 if it failed (said, with what to do by hand).
    def _clean(self, part, title, do):
        try:
            done = do()
            self.bus.log("CLEAN", "%-12s %s" % (part, done), casual="🧹 %s: %s" % (title, done),
                         fact="checklist.cleanup", step=part, title=title, state="done", says=done)
            return 0
        except Exception as e:
            self.bus.log("CLEAN", "%-12s FAILED: %s" % (part, error_text(e)), level="problem", casual="✋ %s: couldn't clean up (%s)" % (title, e),
                         fact="checklist.cleanup", step=part, title=title, state="failed", says=str(e), details=error_trace(e))
            return 1

    # True (said, with the closing fact for command) while a run is active: setup, undo and clean up wait until it stops
    # (they change the bridge, the cable and sharing the run uses; its own stop cleans up).
    def run_is_active(self, tag, command):
        if not runfile.held(self.m.places.runtime):
            return False
        self.bus.log(tag, "a run is active: nothing done (stop it first: menu 2, or the dashboard's Stop)", level="warn",
                     casual="🙂 NXSPOOF is running: stop it first, then try again.", fact="checklist.done", command=command, todo=1)
        return True

    # True when running as root; otherwise says how to get admin rights (tag's line, closing fact for command).
    def _root_for(self, tag, command):
        if self.m.is_root():
            return True
        self.bus.log(tag, "needs admin rights", level="warn", fact="checklist.done", command=command, todo=1,
                     casual="✋ This needs admin rights: sudo python3 -B nx_spoof.py %s (or the dashboard's ⚙️ Configure)" % command)
        return False

# Internet Sharing off (made sure of even when the hotspot isn't up); says which.
def sharing_out(system):
    was_on = system.sharing_running()
    done = system.set_sharing(False)
    return ("turned off (%s)" if was_on else "was already off (made sure: %s)") % done

# The virtual cable out (it may already be: taking it out again is harmless); says so.
def cable_out(system):
    system.unplug_cable()
    return "out (taken out, or it already was)"

# NXSPOOF's leftover firewall rules out, if there are any; says which.
def firewall_out(system):
    if not system.firewall_leftovers():
        return "none left"
    system.firewall_flush()
    return "leftover rules removed"
