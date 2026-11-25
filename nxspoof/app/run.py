"""
nxspoof.app.run - one run of NXSPOOF: the start sequence, the wait, and the ordered shutdown.

Start: log file (and run.json, through app/start.py) -> the virtual cable -> settings -> state file -> sharing on -> wait
for the hotspot (HOTSPOT_WAIT at most) -> DNS (plan B if macOS holds port 53) -> firewall -> web test -> monitors ->
self-test -> READY. The start that's waiting (app/start.py) gets one answer: READY, or why it couldn't (D30).

Every ending (Ctrl+C, a closed window, SIGTERM, the dashboard, a FATAL error during start, a bug)
goes through shutdown(), whose steps each run only if their resource was acquired, and each on
its own: one failing step is logged and never skips the rest.

  1. phase -> stopping ("Stopping..." for a stop request)
  2. stop the monitors, the service watchers, the web and DNS servers
  3. firewall: counters, then remove the rules
  4. summaries (only if the run got as far as the self-test)
  5. Internet Sharing off and the virtual cable out, if this run put it in (unless keep_sharing)
  6. "Stopped." (run.json removed)
  7. phase -> stopped, the last state written and run_state.json removed (app/runstate.py)

The run talks to nobody (D30): the dashboard follows it from its files (run.json, run_state.json, its records) and
writes settings.json, which the run reads again when it changes (features/settingswatch.py).
"""

import errno
import os
import time

from nxspoof import VERSION
from nxspoof.app.runnet import NetSteps
from nxspoof.app.runstate import RunState
from nxspoof.consoles.tracker import ConsoleTracker
from nxspoof.core.fmt import error_text, error_trace, fmt_duration, mac_line
from nxspoof.features import filezilla, sensors, services, story, summary
from nxspoof.macos.wifi import WifiTracker
from nxspoof.net import selftest
from nxspoof.net.dns import DnsServer
from nxspoof.net.web import TestWebServer
from nxspoof.output.logfile import LogFileSink, RecordSink, create_run_log, records_path
from nxspoof.output.terminal import TerminalSink

ALT_DNS_PORT = 5354                                # plan B: DNS listens here, pf redirects port 53 to it
STOP_LINES = {                                     # how the run was asked to stop -> its NOTE line (technical, casual)
    "dashboard": ("stop requested from the dashboard", "🛑 Stop requested from the dashboard"),              # a Stop's note
    "stop command": ("stop requested by nx_spoof.py stop (menu 2)", "🛑 Stop requested from the menu"),        # a Stop's note
    "SIGTERM": ("stop requested (SIGTERM: the dashboard's Stop, menu 2 or nx_spoof.py stop) - cleaning up", "🛑 Stop requested"),
}
HOTSPOT_WAIT = 60                                  # seconds to wait for the hotspot before giving up (FATAL)
SLEPT_AFTER = 5                                    # seconds the wall clock may run ahead of the Mac's awake time between two looks
                                                   # (half a second apart) before it was a sleep: room for a time correction

# The Mac's awake time in seconds: it stands still while the Mac sleeps (the wall clock doesn't), so the gap is a sleep.
def awake_clock():
    return time.clock_gettime(time.CLOCK_UPTIME_RAW) if hasattr(time, "CLOCK_UPTIME_RAW") else time.monotonic()

# A FATAL problem during start (already logged): straight to the shutdown, exit code 1.
class Fatal(Exception):
    pass

# A stop request arrived during start: straight to the shutdown.
class StopRequested(Exception):
    pass

# One run, from "Starting" to "Stopped.".
class Run(NetSteps):
    POLL = 2.0                                     # seconds between looks for the hotspot

    # ctx: the Context; settings_message: what loading settings.json said; stream: the Terminal (a file in the background);
    # answer(ok, text, **data): the one answer to the start that's waiting; where(info or None): run.json written or removed;
    # stop_asked(): who left a Stop's note (stop.json: "dashboard", "stop command"), or None (app/start.py).
    def __init__(self, ctx, settings_message, stream=None, is_tty=None, answer=None, where=None, stop_asked=None):
        self.ctx = ctx
        c = ctx.config
        if is_tty is None:
            is_tty = bool(getattr(stream, "isatty", lambda: False)()) if stream is not None else os.isatty(1)
        self.terminal = TerminalSink(c.get("display.console"), bool(c.get("display.colors")) and is_tty,
                                     ctx.registry.color_of, stream)
        self.logfile, self.records = LogFileSink(), RecordSink()
        ctx.bus.add_sink(self.terminal)
        ctx.bus.add_sink(self.logfile)
        ctx.bus.add_sink(self.records)
        self.settings_message = settings_message
        self.answer = answer or (lambda ok, text, **data: None)
        self.where = where or (lambda info: None)
        self.stop_asked = stop_asked or (lambda: None)
        self.log_path = None
        self.info = {}                           # what run.json says (output/runfile.py)
        self.cable_plugged = False               # this run put the virtual cable in (it takes it out at the end)
        self.iface = self.ip = None
        self.hotspot_name = None                 # the hotspot's name as macOS has it (read before sharing starts), shown and said
        self.dns = self.web = None
        self.dns_port = None
        self.monitors = []
        self.awake = None                        # the "no idle sleep" this run holds (System.keep_awake), let go at the end
        self.clocks = (time.time, awake_clock)   # (wall, awake): a gap between them is a sleep (replaceable for tests)
        self.wifi_watched = False                # the Wi-Fi watcher started (the end-of-run sensor check, features/sensors.py)
        self.mac_facts = {}                      # what kind of Mac this is (the "this Mac" line)
        self.on_ready = []                       # called once READY (app/hotspotwatch.py attaches its start here, D29)
        self.hotspot_down = False                # the hotspot vanished during the run and isn't back yet (the 📡 chip says DOWN)
        self.bind = self.http_port = self.answer_ip = None   # where the servers answer (the watch runs the self-test again)
        self.firewall_loaded = False
        self.sharing_auto = self.sharing_done = False
        self.reached_selftest = False
        self.exit_code = 0
        self.tracker = ConsoleTracker(ctx.bus, ctx.registry, ctx.activity)
        self.story = story.Story(ctx.bus, ctx.registry, ctx.activity)
        self.filezilla = filezilla.FileZilla(ctx.bus, ctx.system.filezilla_installed, ctx.system.open_filezilla)
        self.services = services.Services(ctx.bus, ctx.registry, ctx.activity, enabled=bool(c.get("ftp.watch")),
                                          open_filezilla=self.filezilla.open, in_use=ctx.system.connected_to)
        self.statefile = RunState(self)                 # run_state.json for the dashboard; settings.json read again when it changes

    # Shorthand for logging on the run's bus.
    def log(self, tag, tech, casual=None, level="info", **kw):
        return self.ctx.bus.log(tag, tech, casual, level, **kw)

    # Run until stopped; returns the exit code (0, or 1 after a FATAL error).
    def execute(self):
        try:
            self.start()
            self._wait()
        except StopRequested:
            self._answer_no("it was stopped before it was ready")
        except Fatal as e:
            self.exit_code = 1
            self._answer_no(str(e))
        except Exception as e:                     # a bug: say so, then still clean up properly
            self.log("FATAL", "unexpected error: %s" % error_text(e), level="fatal", details=error_trace(e, always=True))
            self.exit_code = 1
            self._answer_no("unexpected error: %s" % error_text(e))
        finally:
            self.shutdown()
        return self.exit_code

    # The wait until a stop request (a signal, a Stop's note: stop.json, or a sleep), saying "still here" each half second (a
    # split run's keeper paces the heartbeats: app/keeper.py BEAT_EVERY).
    def _wait(self):
        wall, awake = self.clocks[0](), self.clocks[1]()
        while not self.ctx.stopping.wait(0.5):
            who = self.stop_asked()
            if who:
                self.ctx.request_stop(who)
            now_wall, now_awake = self.clocks[0](), self.clocks[1]()
            slept = (now_wall - wall) - (now_awake - awake)
            if slept > SLEPT_AFTER:
                self._slept(slept)
            wall, awake = now_wall, now_awake
            self._protected(self.ctx.system.still_here, failed="NOTE  heartbeat failed: %s")

    # The Mac slept (the lid closed, or idle with --allow-sleep): the run stops cleanly now, at the wake, and resumes nothing.
    # The consoles went with the hotspot, and on waking macOS rejoins your home Wi-Fi (Nov 24): bringing the hotspot back
    # would take that away by surprise. ▶ Start is one click.
    def _slept(self, seconds):
        self.log("NOTE", "this Mac slept (about %s) - stopping: a sleep ends the run, nothing is resumed (the hotspot and the "
                 "consoles went with it)" % fmt_duration(seconds), fact="run.slept", seconds=round(seconds),
                 casual="😴 Your Mac went to sleep, so I stopped. ▶ Start again whenever you need me.")
        self.ctx.request_stop("slept")

    # The start's answer when the run couldn't get going (after the shutdown, the Mac is as it was).
    def _answer_no(self, why):
        self.answer(False, why, log=self.info.get("log"))

    # Raise StopRequested if a stop request arrived.
    def _check_stop(self):
        if self.ctx.stopping.is_set():
            raise StopRequested()

    # ##########################################################################################################################################################
    # ##  START                                                                                                                                               ##
    # ##########################################################################################################################################################

    # The whole start sequence, up to READY.
    def start(self):
        ctx, c = self.ctx, self.ctx.config
        ctx.bus.say("🚀 Starting NXSPOOF…")
        self._open_log()
        self._here_i_am()
        ctx.system.root_watch(self.log_path, self._root_lost)
        self._keep_awake()
        self._plug_cable()
        self._describe_settings()
        self.story.attach(ticker=False)
        self.services.attach()
        self.statefile.start()
        dns_port, http_port = c.extra("dns_port") or 53, c.extra("http_port") or 80
        if not ctx.system.is_root() and (dns_port < 1024 or http_port < 1024):
            self.log("NOTE", "not running as root. If you get a permission error, re-run with sudo.")
        self._check_stop()
        self._find_hotspot()
        answer_ip = c.extra("answer_ip") or self.ip
        ctx.registry.mac_ip = self.ip
        self.tracker.answer_ip = answer_ip
        bind = ctx.system.bind_address(self.ip)
        alt = self._start_dns(bind, dns_port, answer_ip)
        self._load_firewall(alt)
        self._start_web(bind, http_port)
        self._start_monitors()
        self._check_stop()
        ctx.set_phase("running")
        self.reached_selftest = True
        self.bind, self.http_port, self.answer_ip = bind, http_port, answer_ip
        self._selftest(bind, http_port, answer_ip)
        self.story.start_ticker()
        for ready in self.on_ready:
            ready()

    # Create this run's log file (screen only if that fails).
    def _open_log(self):
        c, system = self.ctx.config, self.ctx.system
        try:
            self.log_path = create_run_log(self.ctx.places.logs, keep_last=c.get("logs.keep_last"),
                                           dry_run=bool(c.extra("ip")), give=system.give_to_user, give_link=system.give_link_to_user)
            self.logfile.path, self.records.path = self.log_path, records_path(self.log_path)
            name = os.path.relpath(self.log_path, self.ctx.places.logs)
            self.log("LOG", "this run: %s" % name, fact="run.log", log=name)       # a run's first line (the page draws a divider)
            self.log("LOG", "data folder: %s" % self.ctx.places.describe())
            for level, text in self.ctx.prepared:                                   # the first time: old files moved over
                self.log("LOG", text, casual="📁 " + text[0].upper() + text[1:] + "." if level == "warn" else None, level=level)
            self.log("LOG", "NXSPOOF %s (the nxspoof package)" % VERSION, fact="run.version", version=VERSION)
            self.log("LOG", "privileges: %s" % system.privileges(), fact="run.privileges")
            self._this_mac(system)
            for tech, casual, level, data in self.ctx.config.preflight():           # what the start found before the run
                self.log("START", tech, casual=casual, level=level, fact="run.preflight", **data)
        except Exception as e:
            self.log("NOTE", "could not create the log file (%s) - screen output only" % error_text(e),
                     casual="⚠️ I couldn't create a log file this time.", level="warn", details=error_trace(e))

    # The "this Mac" line: what kind of Mac the run is on (the first thing to know when NXSPOOF meets a Mac it was never tried on).
    def _this_mac(self, system):
        try:
            facts = system.machine_facts()
        except Exception as e:
            self.log("LOG", "this Mac: couldn't tell (%s)" % error_text(e), level="warn", details=error_trace(e))
            return
        self.mac_facts = facts
        self.log("LOG", "this Mac: %s" % mac_line(facts), fact="run.mac", **facts)

    # Where this run is, for the dashboard, nx_spoof.py stop and follow (run.json, output/runfile.py; believed while the lock
    # is held). Updated once READY.
    def _here_i_am(self, **more):
        ctx = self.ctx
        if not self.info:
            self.info = {"pid": os.getpid(), "keeper": getattr(ctx.system, "keeper_pid", None), "started": ctx.started, "ready": False,
                         "log": os.path.relpath(self.log_path, ctx.places.logs) if self.log_path else None,
                         "records": os.path.relpath(records_path(self.log_path), ctx.places.logs) if self.log_path else None}
        self.info.update(more)
        self._protected(lambda: self.where(self.info), failed="NOTE  couldn't write run.json: %s")

    # No idle sleep while the run lasts: a run you started is a run you want, and the hotspot sleeps with the Mac (Nov 24).
    # Not with --allow-sleep, nor for a dry run (--ip). A Mac that can't be kept awake is only a warning.
    def _keep_awake(self):
        c = self.ctx.config
        if c.extra("ip"):
            return
        if c.extra("allow_sleep"):
            self.log("LOG", "this Mac may sleep during the run (--allow-sleep): if it does, the hotspot sleeps with it")
            return
        try:
            self.awake = self.ctx.system.keep_awake(os.getpid())
        except Exception as e:
            self.log("LOG", "couldn't keep this Mac awake (%s) - if it sleeps, the hotspot sleeps with it" % error_text(e),
                     level="warn", details=error_trace(e))
            return
        self.log("LOG", "keeping this Mac awake while NXSPOOF runs (no idle sleep; the screen can still turn off, closing the lid "
                 "still sleeps it)", casual="☕ I'll keep your Mac awake while I run (the screen can still turn off).", fact="run.awake")

    # Every real run plugs the virtual cable in (D30: after a reboot it's out); a cable that can't go in stops the start
    # (Internet Sharing would never come up). Not for a dry run (--ip).
    def _plug_cable(self):
        if self.ctx.config.extra("ip"):
            return
        try:
            found, status = self.ctx.system.plug_cable()
        except OSError as e:
            self._fatal("virtual cable: couldn't plug it in (%s) - Internet Sharing can't start without it; try Set up this Mac" % error_text(e),
                        casual="😕 I couldn't plug in the virtual cable. Try 🛠️ Fix selected in ⚙️ Configure, then ▶ Start again.")
        self.cable_plugged = found == "plugged"
        tech, casual, level, data = self.ctx.config.cable_line(found, status)
        self.log("START", tech, casual=casual, level=level, fact="run.preflight", **data)

    # The SETTINGS lines: what was loaded, problems found in it, what's in force, the consoles.
    def _describe_settings(self):
        ctx = self.ctx
        self.log("SETTINGS", self.settings_message)
        for line in ctx.store.warning_lines():
            self.log("SETTINGS", line, level="warn")
        for line in ctx.config.describe():
            self.log("SETTINGS", line)
        for line, mac in ctx.config.console_lines(ctx.store, ctx.registry):
            self.log("SETTINGS", line, mac=mac)
        if ctx.policy.probe_hosts:
            self.log("SETTINGS", "probe (--probe): %s get the Mac's address this run, to see what a console asks next; their web "
                     "requests are refused and logged in full, anything but port 80 is dropped (the summary's firewall counts)"
                     % ", ".join(ctx.policy.probe_hosts), level="warn")

    # Find (and if needed, turn on) the hotspot; sets self.iface and self.ip.
    def _find_hotspot(self):
        ctx, c = self.ctx, self.ctx.config
        if c.extra("ip"):
            self.ip = c.extra("ip")
            return
        self._hotspot(ctx.system)
        if c.get("spoof.auto_sharing"):
            self._sharing_on()
        ctx.set_phase("waiting")
        self.log("", "Looking for the Internet Sharing Wi-Fi network (bridge100)...")
        waited = 0
        while True:
            found = ctx.system.find_sharing_interface()
            if found:
                self.iface, self.ip = found
                break
            if waited == 30 and self.sharing_auto:
                self.log("SHARING", "still not up after 30 s - opening System Settings; check that Internet Sharing is ON "
                         "(from Thunderbolt Bridge, to Wi-Fi)",
                         casual="😕 The %s Wi-Fi is taking a while. I opened System Settings: check that Internet Sharing is ON." % self.wifi_name(),
                         level="warn")
                self._protected(ctx.system.open_sharing_settings)
            if waited >= HOTSPOT_WAIT:
                self._fatal("the hotspot didn't come up within %d s - is Internet Sharing ON (from Thunderbolt Bridge, to Wi-Fi)?" % HOTSPOT_WAIT,
                            casual="😕 The %s Wi-Fi didn't come up. Check Internet Sharing in System Settings, then ▶ Start again." % self.wifi_name(),
                            details=["the network interfaces now (a hotspot is a bridge1xx with an address):"] + (
                                self._protected(ctx.system.interfaces, failed="NOTE  couldn't list the interfaces: %s") or ["(none found)"]))
            if waited % 10 == 0:
                self.log("", "...not found yet. Is Internet Sharing ON with Wi-Fi ticked? (still waiting, up to %d s)" % HOTSPOT_WAIT,
                         casual="⏳ Waiting for the %s Wi-Fi to come up…" % self.wifi_name() if waited == 0 else None)
            if ctx.stopping.wait(self.POLL):
                raise StopRequested()
            waited += 2
        self.log("", "Found Internet Sharing on %s, Mac address on that network is %s" % (self.iface, self.ip),
                 casual="📡 The %s Wi-Fi is on. Your Mac is offline while it runs." % self.wifi_name())

    # The hotspot's name for people, as macOS has it (read before sharing starts); `unknown` when it couldn't be read.
    def wifi_name(self, unknown="hotspot"):
        return self.hotspot_name or unknown

    # Start the DNS server (plan B on port 5354 if macOS holds 53); returns the plan B port or None.
    def _start_dns(self, bind, port, answer_ip):
        alt = None
        try:
            self.dns = self._dns_server(bind, port, answer_ip)
        except OSError as e:
            if e.errno == errno.EADDRINUSE and port == 53 and self.iface:
                self.log("DNS", "port 53 on %s is taken (by macOS, or another program on this Mac); switching to plan B (pf redirect)" % self.ip)
                port = alt = ALT_DNS_PORT
                try:
                    self.dns = self._dns_server(bind, port, answer_ip)
                except OSError as e2:
                    self._fatal("cannot start DNS on %s:%d: %s" % (self.ip, port, error_text(e2)))
            elif e.errno == errno.EACCES:
                self._fatal("permission denied on port %d. Run with sudo." % port,
                            casual="😕 I need admin rights: start me with ▶ Start on the dashboard (NXSPOOF.app), or menu 1.")
            else:
                self._fatal("cannot start DNS on %s:%d: %s" % (self.ip, port, error_text(e)))
        self.dns_port = port
        self.log("DNS", "listening on %s:%d (only the 2 test names answered, everything else BLOCKED)" % (self.ip, port))
        return alt

    # A started DNS server reporting to the tracker (raises OSError).
    def _dns_server(self, bind, port, answer_ip):
        server = DnsServer(bind, port, answer_ip, self.ctx.policy, self.tracker.on_dns,
                           on_error=lambda text: self.log("DNS", "error: %s" % text, level="warn"), opener=self.ctx.system.open_socket)
        server.start()
        return server

    # Start the fake connection test.
    def _start_web(self, bind, port):
        try:
            web = TestWebServer(bind, port, self.ctx.policy, self.tracker.on_http, opener=self.ctx.system.open_socket)
            web.start()
        except OSError as e:
            details = ["Something else uses port %d. Find it with: sudo lsof -nP -iTCP:%d" % (port, port)] if e.errno == errno.EADDRINUSE else None
            self._fatal("cannot start web server on %s:%d: %s" % (self.ip, port, error_text(e)), details=details)
        self.web = web
        self.log("HTTP", "listening on %s:%d (only the 2 test names answered)" % (self.ip, port))

    # Watch the Wi-Fi (kernel log) and DHCP leases, on the real hotspot only.
    def _start_monitors(self):
        if not self.iface:
            return
        system = self.ctx.system
        wifi = system.wifi_monitor(WifiTracker(self.tracker.on_wifi_join, self.tracker.on_wifi_leave))
        try:
            wifi.start()
            self.monitors.append(wifi)
            self.wifi_watched = True
        except OSError as e:
            self.log("WIFI", "monitor unavailable: %s" % error_text(e),
                     casual="⚠️ I can't see consoles joining or leaving this time (everything else works).", level="warn")
        leases = system.dhcp_monitor(self.tracker.on_dhcp)
        leases.start()
        self.monitors.append(leases)
        self.log("WIFI", "watching for consoles joining the Wi-Fi and getting an address")

    # The self-test: CHECK lines, then READY or PROBLEM.
    def _selftest(self, bind, http_port, answer_ip):
        checks = selftest.run_selftest(bind, self.dns_port, http_port, answer_ip, on_check=lambda ch: self.log(
            "CHECK", selftest.check_line(ch), fact="selftest.check", passed=ch.passed))
        passed = all(ch.passed for ch in checks)
        if passed:
            tech, casual = self.ctx.config.stop_hint()
            self.log("READY", "Everything works on the Mac side. Now connect the console. " + tech,
                     casual="✅ All set! Turn on a Switch and connect it to %s.\n   " % self.wifi_name("the hotspot") + casual, fact="run.ready")
        else:
            self.log("PROBLEM", "self-test failed - see docs/troubleshooting.html.", level="problem",
                     casual="😕 My self-check failed, so the consoles may not pass their online test.\n   Details are in the log file.")
        self._here_i_am(ready=True, hotspot=self.hotspot_name, ip=self.ip)
        self.answer(True, "Connect a Switch to %s (Mac address %s)%s." % (
            '"%s"' % self.hotspot_name if self.hotspot_name else "the hotspot", self.ip,
            "" if passed else " - but the self-test FAILED: the consoles may not pass their test (see the log)"),
            hotspot=self.hotspot_name, log=self.info.get("log"), selftest=passed)

    # Log a FATAL line and stop starting.
    def _fatal(self, text, casual=None, details=None):
        self.log("FATAL", text, casual=casual, level="fatal", details=details)
        raise Fatal(text)

    # The keeper (the run's root part, app/keeper.py) went away: say so and stop; what it held can't be undone from here.
    def _root_lost(self, why):
        self.log("PROBLEM", "%s - stopping (the firewall rules and Internet Sharing can't be undone from here: the dashboard offers "
                 "🧹 Clean up, or menu 7 → 4)" % why, level="problem",
                 casual="😕 NXSPOOF's root part went away, so it's stopping. The dashboard will offer 🧹 Clean up.")
        self.ctx.request_stop("the keeper went away")

    # ##########################################################################################################################################################
    # ##  THE ORDERED SHUTDOWN                                                                                                                                ##
    # ##########################################################################################################################################################

    # Every ending comes here; each step is protected on its own.
    def shutdown(self):
        ctx = self.ctx
        ctx.set_phase("stopping")
        self._protected(self._say_stopping)
        self._protected(self.story.stop)
        self._protected(self.services.stop_all)
        for monitor in self.monitors:
            self._protected(monitor.stop)
        if self.web is not None:
            self._protected(self.web.stop)
        if self.dns is not None:
            self._protected(self.dns.stop)
        pf_counts = self._protected(self._firewall_off)
        if self.reached_selftest and self.iface:
            self._protected(lambda: sensors.check(ctx.bus, self.tracker.sensed, self.wifi_watched, self.mac_facts.get("macos", "?")),
                            failed="NOTE  sensor check failed: %s")
        if self.reached_selftest:
            self._protected(lambda: summary.report(ctx.bus, ctx.activity, ctx.registry, ctx.started, time.time(), pf_counts),
                            failed="NOTE  summary failed: %s")
        if not ctx.setting("spoof.keep_sharing"):          # a --flag, as the run started (app/config.py RUN_ONLY)
            self._protected(self._sharing_off)
            self._protected(self._cable_out)
        elif self.sharing_auto:
            ctx.bus.say("📡 NXSPOOF stays on (keep_sharing is on).")
        self._protected(lambda: self.log("", "Stopped.", casual=(
            "   (full details: %s)" % os.path.relpath(self.log_path, ctx.places.logs)) if self.log_path else None, fact="run.stopped"))
        self._protected(lambda: self.ctx.system.let_sleep(self.awake), failed="NOTE  couldn't let the Mac sleep again: %s")
        self._protected(lambda: self.where(None))
        ctx.set_phase("stopped")
        self._protected(self.statefile.stop)

    # The virtual cable out again, if this run put it in (the Mac as NXSPOOF found it).
    def _cable_out(self):
        if not self.cable_plugged:
            return
        self.cable_plugged = False
        self.ctx.system.unplug_cable()
        self.log("SHARING", "virtual cable taken out (this run had put it in)", fact="cable.out")

    # "Stopping..." for a stop request (a stop signal is named first, as today).
    def _say_stopping(self):
        reason = self.ctx.stop_reason
        if reason is None:
            return
        if reason == "SIGTERM":                        # a Stop's signal arrived before its note: the note says who
            reason = self.stop_asked() or reason
        line = STOP_LINES.get(reason) or (("stop signal received (%s) - cleaning up" % reason, None) if reason.startswith("SIG") else None)
        if line:
            self.log("NOTE", line[0], casual=line[1], fact="run.stop_requested", how=reason)
        self.log("", "Stopping...", casual="🛑 Stopping…", fact="run.stopping", how=reason)

    # Run one shutdown step; a failure is logged (never raised) and the next step still runs.
    def _protected(self, func, failed="NOTE  cleanup step failed: %s"):
        try:
            return func()
        except Exception as e:
            tag, _, text = (failed % error_text(e)).partition(" ")
            self.log(tag, text.lstrip(" "), level="warn", details=error_trace(e))
            return None
