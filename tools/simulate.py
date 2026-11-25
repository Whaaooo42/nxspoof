#!/usr/bin/env python3
"""
simulate.py - play a scripted NXSPOOF session with fake consoles, no Switch and no admin rights.

It runs NXSPOOF's real main(), start to shutdown, on a fake Mac and records the technical log, the casual transcript
and the dashboard snapshots. --compare checks a recording against the reference run of today's code,
tests/fixtures/reference/run.* (and run_dashboard_stop.*): a recording that still matches means nothing visible
changed by accident; an intended change is recorded again as the new reference:
  python3 -B tools/simulate.py new --out tests/fixtures/reference/run
(The previous code's driver and its "agreed differences" went into the history zip at the cutoff, 2026-11-16.)

The session runs in a temporary copy of the code, so your real logs/ and settings.json are never
touched. Network traffic stays on this Mac (127.0.0.1, high ports).

The fake Mac: sharing "turns on", the hotspot is "found" at once, pf "loads" (counters from a real sample), the
servers bind 127.0.0.1 while the code believes it's on the hotspot, the scenario feeds the Wi-Fi tracker and the
lease file, and the run is stopped with a real Ctrl+C signal (or the dashboard's Stop). --keeper sends the run's root
calls through a real keeper over a socket pair, as in a split run (D24).

Usage (from the nx-spoof folder):
  python3 -B tools/simulate.py new --out /tmp/new           # writes new.log, new.casual.txt, new.state.json
  python3 -B tools/simulate.py --compare tests/fixtures/reference/run /tmp/new
  options: --stop dashboard (end with the dashboard's Stop), --hold 120 (keep the run open to look at it), --keeper

Scenario: Switch 2 (the only console with real traffic, from 127.0.0.1) gets an address, joins the
Wi-Fi, passes its connection test, gets blocked lookups and a blocked web request, starts and stops
an FTP server, leaves and comes back; Switch 1 joins and leaves; an unknown console joins; stop.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ##############################################################################################################################################################
# ##  SCENARIO                                                                                                                                                ##
# ##############################################################################################################################################################

S1, S2, UNKNOWN = "dc:68:eb:5a:11:c1", "48:f1:eb:5a:22:c2", "0a:01:02:03:04:05"
S2_IP = "127.0.0.1"
MAC_IP = "192.168.2.1"                         # what the code believes is the Mac's hotspot address
DNS_PORT, HTTP_PORT, FTP_PORT, DASH_PORT = 5399, 8098, 5021, 8897
S2_UA = "libcurl (nnNifm; bf4812d9-d89b-4804-b53a-d08e0bed634f; SDK 23.3.0.0; Add-on 23.3.0.0)"
BLOCKED_NAMES = ["dauth.p01.lp1.ndas.srv.nintendo.net", "sun.p01.lp1.d4c.srv.nintendo.net",
                 "dauth.p01.lp1.ndas.srv.nintendo.net", "api.p01.lp1.ctest.srv.nintendo.net"]

# Kernel log line the Wi-Fi tracker sees every 5 s while a console is connected.
def softap_line(mac):
    return "kernel[0:1] () [dk] printPeerCommonStats@3448:<-------> SoftAP Client [%s] interface 1" % mac

# Kernel log line the Wi-Fi tracker sees when a console leaves.
def delete_line(mac):
    return "kernel[0:1] () wlan0:com.apple.p2p: deletePeerFromFWCache:462 Deleting %s from cache" % mac.upper()

# ##############################################################################################################################################################
# ##  FAKE CONSOLE TRAFFIC (REAL PACKETS ON 127.0.0.1)                                                                                                        ##
# ##############################################################################################################################################################

# Send one DNS A query to the spoof's DNS server, like a console would.
def dns_query(name):
    q = struct.pack(">HHHHHH", 0x4242, 0x0100, 1, 0, 0, 0)
    for part in name.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", 1, 1)
    c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    c.settimeout(3)
    try:
        c.sendto(q, ("127.0.0.1", DNS_PORT))
        c.recvfrom(512)
    except OSError:
        pass
    finally:
        c.close()

# Send one HTTP GET with a given Host header, like a console's connection test.
def http_get(host, ua, path="/"):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (HTTP_PORT, path), headers={"Host": host, "User-Agent": ua})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except urllib.error.HTTPError:
        pass
    except OSError:
        pass

# A tiny FTP-like server: accepts and closes connections, enough for the FTP watcher to see "up".
class FakeFtp:
    # Start listening on the FTP port.
    def __init__(self, port):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(16)
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    # Accept and close connections until stopped.
    def _loop(self):
        while self.running:
            try:
                self.sock.accept()[0].close()
            except OSError:
                return

    # Stop listening (the watcher then sees "down").
    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

# ##############################################################################################################################################################
# ##  THE FAKE MAC                                                                                                                                            ##
# ##############################################################################################################################################################

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
PF_ANCHOR = "com.apple/999.nxspoof"
FAKE_SHARING = "enabled=%s  from=Thunderbolt Bridge (bridge0)  to=en0  [simulated]"
RUN_ARGS = ["--console", "casual", "--no-color", "--dns-port", str(DNS_PORT), "--http-port", str(HTTP_PORT)]

# A finished pfctl call (what subprocess.run returns, as far as the code reads it).
class PfResult:
    # returncode 0, the given output, no errors.
    def __init__(self, stdout=""):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""

# pfctl without root: the rules "load", pf is "on", and the counters come from a real sample.
def fake_pfctl(*args, stdin=None):
    with open(os.path.join(FIXTURES, "macos", "pfctl_vsr.txt"), encoding="utf-8") as f:
        counters = f.read()
    if args == ("-a", PF_ANCHOR, "-vsr"):
        return PfResult(counters)
    if args == ("-a", PF_ANCHOR, "-sr"):
        return PfResult("".join(l + "\n" for l in counters.splitlines() if not l.startswith(" ")))
    if args == ("-s", "info"):
        return PfResult("Status: Enabled for 0 days 00:00:01           Debug: Urgent\n")
    if args == ("-sr",):
        return PfResult('anchor "com.apple/*" all\n')
    return PfResult()

# A temporary folder with the given files from this project, the simulation's settings.json (from the frozen copy in
# tests/fixtures, so renaming your consoles never changes a comparison) and an empty lease file.
def make_work_dir(kind, files):
    work = tempfile.mkdtemp(prefix="nxspoof-sim-%s-" % kind)
    for f in files:
        shutil.copy(os.path.join(ROOT, f), work)
    with open(os.path.join(FIXTURES, "settings_current.json"), encoding="utf-8") as f:     # frozen: your live names don't matter
        settings = json.load(f)
    settings.setdefault("ftp", {}).update(port=FTP_PORT, check_every_seconds=1, recheck_when_up_seconds=1)
    for c in settings.get("consoles", []):
        c["ftp"] = {"port": FTP_PORT}
    with open(os.path.join(work, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    open(os.path.join(work, "fake_dhcpd_leases"), "w").close()
    with open(os.path.join(work, "dash.json"), "w", encoding="utf-8") as f:   # the dashboard's remembered port (--dashboard)
        json.dump({"port": DASH_PORT}, f)
    return work

# Wait until the run's log file (behind last_run.log) has a READY or PROBLEM line; False after the timeout.
def wait_until_ready(work, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(os.path.join(work, "last_run.log"), encoding="utf-8") as f:
                text = f.read()
            if "] READY " in text or "] PROBLEM self-test" in text:
                return True
        except OSError:
            pass
        time.sleep(0.1)
    return False

# Press the dashboard's Stop: since D30 it's a Stop's note in the run's folder (stop.json), which the run picks up.
def press_dashboard_stop(work):
    from nxspoof.output import runfile
    runfile.ask_stop(work, "dashboard")

# Play the scenario in the background once the run is READY, taking dashboard snapshots, then stop it:
# stop="ctrl-c" sends a real SIGINT, stop="dashboard" presses the dashboard's Stop (a note in stop.json, D30).
def play_in_background(work, trackers, add_lease_func, snapshots, stop="ctrl-c", hold=0):
    # Keep the dashboard's view of the run at one moment of the scenario: run_state.json, what the dashboard reads (D30).
    def snapshot(name):
        from nxspoof.output import runfile
        snapshots[name] = runfile.read_state(work) or {"error": "no run_state.json"}

    # The background part: wait for READY and a Wi-Fi tracker, play, stop.
    def body():
        if wait_until_ready(work):
            while not trackers:
                time.sleep(0.05)
            play_scenario(feed=trackers[0].feed, tick=trackers[0].tick, add_lease=add_lease_func, snapshot=snapshot)
            end = time.time() + hold                     # --hold: keep the run open (e.g. to look at the dashboard)
            while time.time() < end:
                for mac in (S2, UNKNOWN):
                    trackers[0].feed(softap_line(mac), time.time())
                time.sleep(1)
        if stop == "dashboard":
            press_dashboard_stop(work)
            return
        os.kill(os.getpid(), signal.SIGINT)
    threading.Thread(target=body, daemon=True).start()

# Write the dashboard snapshots next to the recordings.
def save_snapshots(snapshots, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, indent=1, sort_keys=True)

# Copy the run's technical log out of the work folder.
def save_log(work, log_path):
    shutil.copy(os.path.realpath(os.path.join(work, "last_run.log")), log_path)

# ##############################################################################################################################################################
# ##  NEW DRIVER (THE nxspoof PACKAGE, ITS REAL main())                                                                                                       ##
# ##############################################################################################################################################################

# Run nxspoof.app.main (the real run, start to shutdown) on the fake Mac, play the scenario, record.
# keeper=True: the run's root calls go through a real Keeper (app/keeper.py) over a socket pair, as in a split run (D24).
# dashboard=True: the real dashboard (app/center.py) follows the run from its files on port DASH_PORT, as it would on the Mac (D30).
def run_new(out_prefix, keep_dir=False, stop="ctrl-c", hold=0, keeper=False, dashboard=False):
    sys.path.insert(0, ROOT)
    from nxspoof.app.keeper import Keeper, KeeperSystem
    from nxspoof.app.main import main as new_main
    from nxspoof.app.system import System
    from nxspoof.macos import cable

    # The fake Mac: sharing, the hotspot, pf and the Wi-Fi tracker answered without touching this Mac.
    class FakeSystem(System):
        # Fake pfctl, the work folder's lease file, a list for the Wi-Fi tracker.
        def __init__(self, leases_path):
            System.__init__(self, pfctl=fake_pfctl, leases_path=leases_path)
            self.trackers = []

        # As if started with sudo.
        def is_root(self):
            return True

        # The interfaces of a made-up Mac whose hotspot is up.
        def interfaces(self):
            return ["bridge0: UP, members en1 feth0", "bridge100: UP, active, 192.168.2.1, members en0 feth1", "en0: UP, active"]

        # No idle sleep, as if held (nothing started: the Mac running the tests may sleep as it likes).
        def keep_awake(self, pid):
            return "awake"

        # Let go of it.
        def let_sleep(self, handle):
            return None

        # A made-up Mac (the recording must not depend on the Mac it's played on).
        def machine_facts(self):
            return {"macos": "15.0", "build": "24A000", "model": "Mac00,0", "chip": "Apple M0", "arch": "arm64", "rosetta": "no",
                    "python": "3.9.6", "python_path": "/usr/bin/python3", "language": "en (region en_US)", "managed": "no"}

        # Sharing is off at start.
        def sharing_running(self):
            return False

        # Turning sharing on/off "works".
        def set_sharing(self, enabled):
            return FAKE_SHARING % bool(enabled)

        # The hotspot is there at once.
        def find_sharing_interface(self):
            return "bridge100", MAC_IP

        # The virtual cable is already in (this Mac's own is never looked at or touched).
        def plug_cable(self):
            return "already", cable.Status(True, True, "172.16.0.1")

        # Nothing to take out.
        def unplug_cable(self):
            return None

        # This Mac counts as ready (setup's checks never run on this Mac in a simulation).
        def mac_problems(self, program, places):
            return []

        # Stay in this process: the simulated start never runs again from a program copy.
        def restart_from(self, folder, argv):
            return None

        # No fork: the simulated run happens here, and nobody waits for its answer.
        def in_background(self, output):
            return "worker", None, None

        # The hotspot as the default settings want it (never this Mac's own).
        def hotspot_settings(self):
            return {"name": "NXSPOOF", "channel": 11, "security": "WPA2/WPA3 Personal", "password": True}

        # Servers bind 127.0.0.1 (the code believes it's on the hotspot address).
        def bind_address(self, ip):
            return "127.0.0.1"

        # The scenario feeds the tracker instead of the kernel log stream.
        def wifi_monitor(self, tracker):
            self.trackers.append(tracker)
            return NoMonitor()

        # Nothing to open.
        def open_sharing_settings(self):
            return None

        # Nothing to open (the menu opens the browser itself).
        def open_url(self, url):
            return None

        # Nothing to open.
        def open_filezilla(self, url):
            return None

        # No program of this Mac is using a console's server (the fake FTP server just goes away).
        def connected_to(self, ip, port):
            return False

    # The run's side of a split run on the fake Mac: root calls go to the keeper over the pair, the rest is the fake Mac's.
    class ThroughKeeper(KeeperSystem, FakeSystem):
        # leases_path: as FakeSystem; channel: the run's side of the pair (the keeper counts as alive: it's this process's parent).
        def __init__(self, leases_path, channel):
            FakeSystem.__init__(self, leases_path)
            self.channel, self.keeper_pid, self.lock = channel, os.getppid(), threading.Lock()

    work = make_work_dir("new", [])
    casual_path, log_path = out_prefix + ".casual.txt", out_prefix + ".log"
    leases = os.path.join(work, "fake_dhcpd_leases")
    system, served = FakeSystem(leases), None
    if keeper:
        run_side, keeper_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        stand_in = subprocess.Popen(["/usr/bin/true"])           # the keeper waits for "the run" at the end
        served = threading.Thread(target=Keeper(FakeSystem(leases), keeper_side, stand_in.pid, os.getuid()).serve, daemon=True)
        served.start()
        system = ThroughKeeper(leases, run_side)
    casual_out = open(casual_path, "w", encoding="utf-8")
    snapshots = {}
    center = open_dashboard(work, FakeSystem(leases)) if dashboard else None
    try:
        play_in_background(work, system.trackers, lambda ip, mac: add_lease(leases, ip, mac), snapshots, stop, hold)
        new_main(["--settings", os.path.join(work, "settings.json")] + RUN_ARGS, system=system, project_dir=work, stream=casual_out)
    finally:
        if center:
            center.request_stop("the simulation is over")
        if served:
            system.channel.close()
            served.join(5)
            stand_in.wait(5)
        casual_out.close()
        save_log(work, log_path)
        save_snapshots(snapshots, out_prefix + ".state.json")
        if not keep_dir:
            shutil.rmtree(work, ignore_errors=True)
    return log_path, casual_path

# The real dashboard on the work folder (it finds the simulated run by its lock and follows its files), in a thread.
def open_dashboard(work, system):
    from nxspoof.app import reporting
    from nxspoof.app.center import Center
    from nxspoof.core.places import Places
    from nxspoof.settings.store import SettingsStore
    store = SettingsStore(os.path.join(work, "settings.json"))
    center = Center(store, store.load(), system, Places.together(work, work), stream=open(os.devnull, "w"))
    reporting.attach(center)                                      # 🐞 Report, as app/launch.py does
    threading.Thread(target=center.run, kwargs={"signals_on": False}, daemon=True).start()
    return center

# A monitor that does nothing (the scenario drives the Wi-Fi tracker itself).
class NoMonitor:
    # Nothing to start.
    def start(self):
        pass

    # Nothing to stop.
    def stop(self):
        pass

# Append one DHCP lease to the fake lease file (the DHCP monitor notices the change).
def add_lease(path, ip, mac):
    with open(path, "a") as f:
        f.write("{\n\tip_address=%s\n\thw_address=1,%s\n\tidentifier=1,%s\n\tlease=0x%x\n}\n"
                % (ip, mac, mac, int(time.time())))

# ##############################################################################################################################################################
# ##  THE SCENARIO, PLAYED THROUGH WHICHEVER DRIVER                                                                                                           ##
# ##############################################################################################################################################################

# Run the scripted session; feed/tick drive the Wi-Fi tracker, add_lease writes DHCP leases.
def play_scenario(feed, tick, add_lease, snapshot=None):
    snapshot = snapshot or (lambda name: None)
    clock = {"t": time.time()}

    # Let real time pass while ticking the Wi-Fi tracker once a second (and showing present consoles).
    def wait(seconds, present=()):
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(0.25)
            now = time.time()
            if now - clock["t"] >= 1:
                clock["t"] = now
                for mac in present:
                    feed(softap_line(mac), now)
                tick(now)

    add_lease(S2_IP, S2)
    wait(2.5)
    feed(softap_line(S2), time.time())
    dns_query("ctest.p01.ctest.srv.nintendo.net")
    http_get("ctest.p01.ctest.srv.nintendo.net", S2_UA)
    wait(1, [S2])
    for name in BLOCKED_NAMES:
        dns_query(name)
        wait(0.3, [S2])
    http_get("api.github.com", "Mozilla/5.0 (Nintendo Switch)", "/repos/x")
    wait(6, [S2])                                         # casual "blocked so far" notice
    snapshot("after_blocked")
    ftp = FakeFtp(FTP_PORT)
    wait(3, [S2])
    ftp.stop()
    wait(3, [S2])
    feed(softap_line(S1), time.time())
    wait(2, [S1, S2])
    feed(delete_line(S1), time.time())
    wait(8, [S2])                                         # S1 leaves (confirmed after ~6.5 s)
    feed(softap_line(UNKNOWN), time.time())
    wait(2, [S2, UNKNOWN])
    feed(delete_line(S2), time.time())
    wait(8, [UNKNOWN])                                    # S2 leaves
    feed(softap_line(S2), time.time())                    # S2 comes back
    dns_query("ctest.p01.ctest.srv.nintendo.net")
    http_get("ctest.p01.ctest.srv.nintendo.net", S2_UA)
    wait(2, [S2, UNKNOWN])
    snapshot("before_stop")

# ##############################################################################################################################################################
# ##  COMPARING TWO RECORDINGS                                                                                                                                ##
# ##############################################################################################################################################################

NORMALIZE = [
    (re.compile(r"^\[\d\d:\d\d:\d\d\] "), ""),                                      # timestamps
    (re.compile(r"\b\d\d:\d\d:\d\d\b"), "HH:MM:SS"),                                 # times inside lines
    (re.compile(r"\(\d+:\d\d:\d\d\)"), "(H:MM:SS)"),                                 # run duration
    (re.compile(r"\b\d+h \d\dm \d\ds\b|\b\d+m \d\ds\b|\b\d+s\b"), "<dur>"),          # technical durations
    (re.compile(r"\b\d+ min \d+ s\b|\b\d+ minutes?\b|\b\d+ seconds?\b|\b\d+ h \d+ min\b"), "<dur>"),
    (re.compile(r"lease=0x[0-9a-f]+"), "lease=<n>"),
    (re.compile(r"logs/[^ )]+\.log"), "logs/<run>.log"),
]

# Lines left out of the comparison on both sides, each with the reason.
SET_ASIDE = [
    (re.compile(r"^DNS   127\.0\.0\.1 asked "), "the self-test's own lookups: where they land among the CHECK lines depends on thread timing"),
    (re.compile(r"^LOG   data folder: "), "the data folder line names a temporary folder"),
]
STATE_SET_ASIDE = "run.now, run.started, joined/left times, FTP event times - clock times (their counts are compared); run.pid - a new process each time"

# Make a dashboard snapshot comparable: drop the clock times.
def normalize_state(snapshots):
    out = {}
    for moment, st in sorted(snapshots.items()):
        if "run" not in st:
            out[moment] = st
            continue
        run = {k: v for k, v in st["run"].items() if k not in ("now", "started", "pid")}
        if run.get("log_file"):
            run["log_file"] = "logs/<run>.log"
        consoles = []
        for c in st.get("consoles", []):
            c = dict(c, joined=len(c["joined"]), left=len(c["left"]))
            c["ftp"] = dict(c["ftp"], events=[what for _, what in c["ftp"]["events"]])
            consoles.append(c)
        out[moment] = dict(st, run=run, consoles=consoles)
    return json.dumps(out, indent=1, sort_keys=True).splitlines()

# Make two recordings comparable: remove times, durations and run-specific names, and the set-aside lines.
def normalize(text):
    out = []
    for line in text.splitlines():
        for rx, repl in NORMALIZE:
            line = rx.sub(repl, line)
        if not any(rx.search(line) for rx, _ in SET_ASIDE):
            out.append(line.rstrip())
    return out

# Print one comparison's result ("identical" or how many lines differ, then the diff); True if identical.
def print_diff(title, diff):
    print("== %s: %s" % (title, "identical (after normalizing)" if not diff else "%d differing lines" % sum(
        1 for d in diff if d[:1] in "+-" and not d.startswith(("+++", "---")))))
    for d in diff:
        print("   " + d)
    return not diff

# Compare the dashboard snapshots of two recordings (if both have them); True if identical or not there.
def compare_states(a, b):
    left, right = a + ".state.json", b + ".state.json"
    if not (os.path.exists(left) and os.path.exists(right)):
        return True
    with open(left, encoding="utf-8") as f:
        la = normalize_state(json.load(f))
    with open(right, encoding="utf-8") as f:
        lb = normalize_state(json.load(f))
    same = print_diff(".state.json (dashboard)", list(difflib.unified_diff(la, lb, left, right, lineterm="", n=2)))
    print("(dashboard fields set aside: %s)" % STATE_SET_ASIDE)
    return same

# Print a normalized diff of the technical logs, casual transcripts and dashboard snapshots of two recordings; True if all identical.
def compare(a, b):
    same = True
    for suffix in (".log", ".casual.txt"):
        la = normalize(open(a + suffix, encoding="utf-8").read())
        lb = normalize(open(b + suffix, encoding="utf-8").read())
        same = print_diff(suffix, list(difflib.unified_diff(la, lb, a + suffix, b + suffix, lineterm="", n=1))) and same
    same = compare_states(a, b) and same
    print("(set aside on both sides: clock times, durations, temporary names; %s)" % "; ".join(why for _, why in SET_ASIDE))
    return same

# ##############################################################################################################################################################
# ##  COMMAND LINE                                                                                                                                            ##
# ##############################################################################################################################################################

# Parse arguments and run a driver or a comparison.
def main():
    ap = argparse.ArgumentParser(description="Play a scripted NXSPOOF session with fake consoles.")
    ap.add_argument("driver", nargs="?", choices=["new"], help="new: play the session through nxspoof/ (the only driver)")
    ap.add_argument("--out", help="recording prefix (writes <out>.log and <out>.casual.txt)")
    ap.add_argument("--keep", action="store_true", help="keep the temporary working copy")
    ap.add_argument("--stop", choices=["ctrl-c", "dashboard"], default="ctrl-c", help="how the session ends (default: a real Ctrl+C)")
    ap.add_argument("--keeper", action="store_true", help="new driver: root calls go through a real keeper over a socket pair (privilege separation, D24)")
    ap.add_argument("--hold", type=int, default=0, metavar="SECONDS", help="keep the run open this long after the scenario (to look at the dashboard)")
    ap.add_argument("--dashboard", action="store_true", help="open the real dashboard on the simulated run, http://localhost:%d/ (D30)" % DASH_PORT)
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two recording prefixes")
    args = ap.parse_args()
    if args.compare:
        sys.exit(0 if compare(*args.compare) else 1)
    if not args.driver or not args.out:
        ap.error("give a driver and --out, or --compare A B")
    log_path, casual_path = run_new(args.out, keep_dir=args.keep, stop=args.stop, hold=args.hold, keeper=args.keeper, dashboard=args.dashboard)
    print("recorded: %s (technical, %d lines)" % (log_path, sum(1 for _ in open(log_path, encoding="utf-8"))))
    print("recorded: %s (casual, %d lines)" % (casual_path, sum(1 for _ in open(casual_path, encoding="utf-8"))))

if __name__ == "__main__":
    main()
