"""
nxspoof.app.runnet - a run's network side: Internet Sharing (on, off, the hotspot as macOS has it) and the firewall
(loaded at start, counted and removed at the end).

NetSteps is a part of Run (app/run.py): the same object, its methods kept here so run.py stays the story of a run (start,
wait, shutdown) and this file the Mac's network around it. It uses the Run's ctx, log, _protected and _fatal, and sets
sharing_auto / sharing_done, hotspot_name and firewall_loaded.
"""

import os

from nxspoof.core.fmt import error_text, error_trace
from nxspoof.core.text import compose, outcome
from nxspoof.features import runlog
from nxspoof.macos.firewall import FirewallError, short_name

# The network steps of a Run (mixed into it).
class NetSteps(object):

    # ##########################################################################################################################################################
    # ##  INTERNET SHARING                                                                                                                                    ##
    # ##########################################################################################################################################################

    # Turn Internet Sharing on by itself (no System Settings, no extra password).
    def _sharing_on(self):
        system = self.ctx.system
        if system.sharing_running():
            self.log("SHARING", "Internet Sharing is already ON")
        elif not system.is_root():
            self.log("SHARING", "not running as root - turn Internet Sharing ON by hand",
                     casual="😕 Please turn Internet Sharing ON yourself (System Settings > General > Sharing).", level="warn")
            return
        else:
            try:
                self.log("SHARING", "turning Internet Sharing ON: %s" % system.set_sharing(True))
            except Exception as e:
                self.log("SHARING", "could not turn it on automatically (%s) - opening System Settings, turn it ON there" % error_text(e),
                         casual="😕 I couldn't turn on Internet Sharing by myself, so I opened System Settings: please turn it ON there.",
                         level="warn", details=error_trace(e))
                self._protected(system.open_sharing_settings)
                return
        self.sharing_auto = True

    # The hotspot as macOS has it (read only: NXSPOOF never changes it, D25): its name, channel, security and whether it
    # has a password, in the log; a warning when its name isn't the last run's (each Switch has to join the new one).
    def _hotspot(self, system):
        try:
            now = system.hotspot_settings()
        except Exception as e:
            self.log("SHARING", "couldn't read the hotspot's settings (%s)" % error_text(e), level="warn", details=error_trace(e))
            now = {}
        self.hotspot_name = now.get("name") or None
        self.log("SHARING", "hotspot: \"%s\", channel %s, %s, password %s (macOS's: set in System Settings > Sharing > Wi-Fi Options)" % (
            now.get("name", "?"), now.get("channel", "?"), now.get("security") or "?", "set" if now.get("password") else "MISSING"),
            level="info" if now.get("password", True) else "warn", fact="run.hotspot", name=now.get("name"), channel=now.get("channel"),
            casual=None if now.get("password", True) else "⚠️ The hotspot has no password: set one in System Settings > Sharing > Internet Sharing > Wi-Fi Options.")
        last = runlog.last_hotspot_name(os.path.dirname(self.log_path), skip=os.path.basename(self.log_path)) if self.log_path else None
        if self.hotspot_name and last and last != self.hotspot_name:
            self.log("SHARING", "the hotspot's name changed: \"%s\" now, \"%s\" at the last run - each Switch has to join \"%s\" (and its password)" % (
                self.hotspot_name, last, self.hotspot_name), level="warn",
                casual="⚠️ Your hotspot is now called %s (it was %s last run): your Switches will need to join %s." % (self.hotspot_name, last, self.hotspot_name))

    # Turn Internet Sharing back off, once, if this run turned it on.
    def _sharing_off(self):
        if not self.sharing_auto or self.sharing_done:
            return
        self.sharing_done = True
        try:
            self.ctx.system.set_sharing(False)
            self.log("SHARING", "turned Internet Sharing OFF - your Mac goes back to your home Wi-Fi",
                     casual="🏠 Your Mac is back on your home Wi-Fi. Bye!", fact="sharing.off", ok=True)
        except Exception as e:
            self.log("SHARING", "could not turn it off (%s) - turn Internet Sharing OFF by hand" % error_text(e),
                     casual="😕 I couldn't turn Internet Sharing off. Please turn it off in System Settings > General > Sharing.",
                     level="problem", fact="sharing.off", ok=False, details=error_trace(e))

    # ##########################################################################################################################################################
    # ##  THE FIREWALL                                                                                                                                        ##
    # ##########################################################################################################################################################

    # Load the firewall (and the plan B redirect) on the real hotspot.
    def _load_firewall(self, alt):
        enabled = bool(self.ctx.config.get("spoof.firewall"))
        if not self.iface:
            self.log("NOTE", "dry run on %s: firewall not loaded (only used on the real hotspot)" % self.ip)
            return
        if not (alt or enabled):
            self.log("NOTE", "firewall is OFF for this run (--no-firewall or settings.json)",
                     casual="⚠️ The firewall is OFF for this run.", level="warn")
            return
        try:
            status = self.ctx.system.firewall_apply(self.iface, self.ip, alt, enabled)
        except FirewallError as e:
            self.log("pf", "FAILED to load rules: %s" % error_text(e), level="problem", fact="firewall.failed")
            if alt:
                self._fatal("plan B failed. See docs/troubleshooting.html#plan-b-failed.")
            return
        self.firewall_loaded = True
        if alt:
            self.log("pf", "DNS on %s port 53 redirected to port %d" % (self.iface, alt))
        if not enabled:
            return
        if status["ok"]:
            self.log("FIREWALL", "ON  console may only reach the Mac for DHCP, DNS and the web test; every other packet is dropped",
                     fact="firewall.on")
        else:
            self.log("FIREWALL", compose("%s  rules loaded=%s pf-enabled=%s hooked-in=%s - the DNS/web blocking still applies, "
                     "but the firewall may not be blocking: another firewall or VPN may be using pf (please report it: "
                     "🐞 Report a problem)", outcome("WARNING", "warning"), status["loaded"], status["enabled"], status["hooked"]),
                     level="warn", fact="firewall.partial", **status)

    # Firewall counters, then remove the rules; returns {short name: packets}.
    def _firewall_off(self):
        counts = {}
        if not self.firewall_loaded:
            return counts
        system = self.ctx.system
        try:
            for rule, packets in system.firewall_counters():
                self.log("pf", "%8s packets  %s" % (packets, rule))
                counts[short_name(rule)] = packets
        finally:
            system.firewall_flush()
            self.firewall_loaded = False
            self.log("pf", "firewall rules removed", fact="firewall.removed")
        return counts
