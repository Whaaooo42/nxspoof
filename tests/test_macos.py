"""
test_macos.py - unit tests for nxspoof.macos, on real samples from the owner's Mac
(tests/fixtures/macos/, see its README for which are real and which are constructed).

Each piece is also compared with what it answered at the cutoff (tests/fixtures/golden.json), so the pieces behave
exactly like the old ones. Nothing here needs root; nothing changes the Mac.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from nxspoof.core.paths import PROJECT_DIR
from nxspoof.macos import admin, dhcp, firewall, hotspot, leases, machine, netif, process, sharing, user, wifi
from nxspoof.output import runfile
from tests import golden

FIX = os.path.join(PROJECT_DIR, "tests", "fixtures", "macos")
S1, S2 = "dc:68:eb:5a:11:c1", "48:f1:eb:5a:22:c2"

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# A sample file's text.
def sample(name):
    return read_text(os.path.join(FIX, name))

# The DHCP lease file.
class Leases(unittest.TestCase):
    # The real lease file: both Switches, as recorded.
    def test_real_file_same_as_recorded(self):
        found = leases.parse_leases(sample("dhcpd_leases"))
        self.assertEqual(sorted(l.mac for l in found), sorted([S1, S2]))
        self.assertEqual([[l.ip, l.mac] for l in found], golden("leases"))

    # Dropped leading zeros are restored; incomplete blocks are skipped; a missing file is empty.
    def test_edges(self):
        text = "{\n\tip_address=192.168.2.9\n\thw_address=1,a:1:2:3:4:5\n\tlease=0x1\n}\n{\n\tip_address=192.168.2.10\n}\n"
        self.assertEqual(leases.parse_leases(text), [leases.Lease("192.168.2.9", "0a:01:02:03:04:05", "0x1")])
        self.assertEqual(leases.read_leases(os.path.join(FIX, "nope")), [])
        self.assertIsNone(leases.lease_mtime(os.path.join(FIX, "nope")))

# Interfaces and hardware ports.
class Interfaces(unittest.TestCase):
    # The hotspot interface: none with sharing off, bridge100 with it on.
    def test_find_sharing_interface(self):
        for name, expected in (("ifconfig_sharing_off.txt", None), ("ifconfig_sharing_on.txt", ("bridge100", "192.168.2.1"))):
            self.assertEqual(netif.find_sharing_interface(sample(name)), expected)

    # The interfaces a hotspot needs, one line each (what the log shows when the hotspot doesn't come up).
    def test_summarize_interfaces(self):
        lines = netif.summarize_interfaces(sample("ifconfig_sharing_on.txt"))
        self.assertTrue(any(l.startswith("bridge100: UP") and "192.168.2.1" in l for l in lines), lines)
        self.assertTrue(any(l.startswith("bridge0: UP") and "members" in l for l in lines), lines)
        self.assertFalse(any(l.startswith(("lo0", "utun", "awdl")) for l in lines))

    # Real ifconfig details: Wi-Fi has an address, the Thunderbolt Bridge has its fixed address and members.
    def test_parse_ifconfig_real(self):
        info = netif.parse_ifconfig(sample("ifconfig_sharing_off.txt"))
        self.assertIn("bridge0", info)
        self.assertIn("172.16.0.1", info["bridge0"]["inets"])
        self.assertTrue(info["bridge0"]["members"])
        self.assertIn("UP", info["lo0"]["flags"])

    # netstat: FileZilla's connection to the console's FTP port counts only while ESTABLISHED, and only to that port.
    def test_has_connection_to(self):
        text = ("Active Internet connections (including servers)\n"
                "Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)\n"
                "tcp4       0      0  192.168.2.1.62133      192.168.2.4.5000       ESTABLISHED\n"
                "tcp4       0      0  192.168.2.1.62130      192.168.2.5.5000       TIME_WAIT\n"
                "tcp4       0      0  *.80                   *.*                    LISTEN\n")
        self.assertTrue(netif.has_connection_to(text, "192.168.2.4", 5000))
        self.assertFalse(netif.has_connection_to(text, "192.168.2.5", 5000))
        self.assertFalse(netif.has_connection_to(text, "192.168.2.4", 50))
        self.assertFalse(netif.has_connection_to("", "192.168.2.4", 5000))

    # Real hardware ports: Wi-Fi is en0, the bridge is bridge0; no wired uplink connected.
    def test_hardware_ports_real(self):
        ports = netif.parse_hardware_ports(sample("hardware_ports.txt"))
        self.assertEqual(netif.wifi_device(ports), "en0")
        self.assertEqual(netif.bridge_device(ports), "bridge0")
        self.assertEqual(netif.active_uplinks(ports, sample("ifconfig_sharing_off.txt")), [])

    # A connected USB-C Ethernet adapter is found as an uplink (constructed sample).
    def test_uplink_found(self):
        ports = [{"port": "USB 10/100/1000 LAN", "device": "en7", "address": "x"}, {"port": "Wi-Fi", "device": "en0", "address": "y"}]
        text = "en7: flags=8863<UP,RUNNING> mtu 1500\n\tinet 10.0.0.50 netmask 0xffffff00\n\tstatus: active\n"
        self.assertEqual(netif.active_uplinks(ports, text), [{"port": "USB 10/100/1000 LAN", "device": "en7", "ip": "10.0.0.50"}])

    # Defaults when a port isn't listed.
    def test_defaults(self):
        self.assertEqual((netif.wifi_device([]), netif.bridge_device([])), ("en0", "bridge0"))

    # The bridge is also found by device when the port name is in another language.
    def test_bridge_by_device(self):
        self.assertEqual(netif.bridge_device([{"port": "Pont Thunderbolt", "device": "bridge1", "address": None}]), "bridge1")

# What kind of Mac this is (macos/machine.py).
class Machine(unittest.TestCase):
    # What kind of Mac this is, from macOS's own tools (a fake runner here); what can't be read is "?".
    def test_facts(self):
        answers = {"-productVersion": "15.7.7\n", "-buildVersion": "24G720\n", "hw.model": "Mac16,13\n", "machdep.cpu.brand_string": "Apple M4\n",
                   "sysctl.proc_translated": "0\n", "enrollment": "Enrolled via DEP: No\nMDM enrollment: Yes (User Approved)\n"}

        # macOS's answer to each command, by its last word.
        def run(cmd):
            return 0, answers[cmd[-1]]
        f = machine.facts(run=run)
        self.assertEqual((f["macos"], f["build"], f["model"], f["chip"], f["rosetta"], f["managed"]), ("15.7.7", "24G720", "Mac16,13", "Apple M4", "no", "yes"))
        self.assertEqual(sorted(f), sorted(machine.FACTS))
        broken = machine.facts(run=lambda cmd: (127, ""))
        self.assertEqual((broken["macos"], broken["model"], broken["managed"], broken["rosetta"]), ("?", "?", "?", "no"))

# The pf firewall rules.
class Firewall(unittest.TestCase):
    # Exactly these rules (normal, and with the plan-B redirect).
    def test_rules(self):
        self.assertEqual(firewall.build_rules("bridge100", "192.168.2.1"), [
            "block drop in quick on bridge100 inet6 all",
            "pass in quick on bridge100 inet proto udp from any port 68 to any port 67",
            "pass in quick on bridge100 inet proto udp from any to 192.168.2.1 port 53",
            "pass in quick on bridge100 inet proto tcp from any to 192.168.2.1 port 80",
            "block drop in quick on bridge100 all"])
        planb = firewall.build_rules("bridge100", "192.168.2.1", alt_dns_port=5354)
        self.assertEqual(planb[0], "rdr pass on bridge100 inet proto udp from any to 192.168.2.1 port 53 -> 192.168.2.1 port 5354")
        self.assertIn("pass in quick on bridge100 inet proto udp from any to 192.168.2.1 port { 53, 5354 }", planb)
        self.assertEqual(firewall.build_rules("bridge100", "192.168.2.1", firewall=False), [])

    # Counters from pfctl -vsr; short names as recorded.
    def test_counters_and_names(self):
        counts = firewall.parse_counters(sample("pfctl_vsr.txt"))
        self.assertEqual([n for _, n in counts], [0, 6, 14, 42, 0])
        self.assertEqual([firewall.short_name(r) for r, _ in counts], golden("pf_short_names"))
        self.assertEqual([firewall.short_name(r) for r, _ in counts],
                         ["IPv6 (blocked)", "DHCP", "DNS", "web test", "everything else (blocked)"])

    # pf's token: read from `pfctl -E` (macOS writes it on stderr), handed back by apply(), given back by release().
    def test_token(self):
        self.assertEqual(firewall.parse_token("No ALTQ support in kernel\npf enabled\nToken : 9366978311978833231\n"), "9366978311978833231")
        self.assertIsNone(firewall.parse_token("pf enabled\n"))
        calls = []

        # pfctl, recorded; -E answers with a token.
        class Done(object):
            returncode, stdout = 0, ""

            # The answer to one call.
            def __init__(self, args):
                self.stderr = "pf enabled\nToken : 42\n" if args == ("-E",) else ""

        # Record every call.
        def run(*args, stdin=None):
            calls.append(args)
            return Done(args)
        status = firewall.apply("bridge100", firewall.build_rules("bridge100", "192.168.2.1"), run=run)
        self.assertEqual(status["token"], "42")
        firewall.release("42", run=run)
        self.assertEqual(calls[-1], ("-X", "42"))

    # The "is it really in place" check.
    def test_status(self):
        ok = firewall.status_from("bridge100", "block drop in quick on bridge100 all\n", "Status: Enabled for 0 days", 'anchor "com.apple/*" all')
        self.assertEqual(ok, {"loaded": True, "enabled": True, "hooked": True, "ok": True})
        bad = firewall.status_from("bridge100", "", "Status: Disabled", "")
        self.assertFalse(bad["ok"])

    # apply / counters / flush / leftovers through a replaceable pfctl runner (no root needed).
    def test_runner(self):
        calls = []

        # A pfctl that records calls and answers like a working pf.
        def run(*args, stdin=None):
            calls.append(args)
            out = {("-a", firewall.ANCHOR, "-sr"): "block drop in quick on bridge100 all\n", ("-s", "info"): "Status: Enabled",
                   ("-sr",): 'anchor "com.apple/*" all', ("-a", firewall.ANCHOR, "-vsr"): sample("pfctl_vsr.txt")}.get(args, "")
            return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
        status = firewall.apply("bridge100", firewall.build_rules("bridge100", "192.168.2.1"), run=run)
        self.assertTrue(status["ok"])
        self.assertEqual(calls[0], ("-a", firewall.ANCHOR, "-f", "-"))
        self.assertEqual(len(firewall.counters(run=run)), 5)
        self.assertTrue(firewall.leftovers(run=run))
        firewall.flush(run=run)
        self.assertEqual(calls[-1], ("-a", firewall.ANCHOR, "-F", "all"))

# Internet Sharing.
class Sharing(unittest.TestCase):
    # The NAT settings written are exactly the recorded ones, on and off.
    def test_new_nat_same_as_recorded(self):
        base = {"Enabled": 0, "PrimaryInterface": {"Device": "bridge0", "Enabled": 0, "HardwareKey": "", "PrimaryUserReadable": "Thunderbolt Bridge"},
                "PrimaryService": "OLD", "SharingDevices": [], "AirPort": {"Channel": 0}}
        for enabled in (True, False):
            new = sharing.new_nat(base, enabled, "60F93F48-F4AB-4633-9ACC-A6C7E4EB7CF5", "Thunderbolt Bridge", "bridge0", "en0")
            self.assertEqual(json.loads(json.dumps(new)), golden("nat")[str(enabled)]["written"])
            self.assertEqual(sharing.describe(new), golden("nat")[str(enabled)]["describe"])
        self.assertEqual(base["Enabled"], 0)

    # Finding the bridge's service from real scutil output.
    def test_service_lookup_real(self):
        keys = sharing.parse_service_keys(sample("scutil_list.txt"))
        self.assertTrue(keys)
        self.assertEqual(sharing.parse_service_interface(sample("scutil_show_bridge.txt"), "bridge0"), "Thunderbolt Bridge")
        self.assertIsNone(sharing.parse_service_interface(sample("scutil_show_bridge.txt"), "en0"))

    # The Mac's real Internet Sharing settings can be read and described (reading needs no root).
    def test_live_read(self):
        self.assertIn("enabled=", sharing.describe(sharing.read_nat()))

# Wi-Fi presence from the kernel log.
class Wifi(unittest.TestCase):
    # Replay a timeline of (time, line) through a tracker; returns its join/leave events.
    def replay(self, tracker_class, timeline, end):
        events = []
        t = tracker_class(lambda m, n: events.append(("join", m, round(n, 3))),
                          lambda m, r, j, n: events.append(("leave", m, r, round(n, 3))))
        clock = timeline[0][0]
        for ts, line in timeline:
            while clock + 1 <= ts:
                clock += 1
                t.tick(clock)
            t.feed(line, ts)
        while clock < end:
            clock += 1
            t.tick(clock)
        return events

    # The real airplane-mode session: same joins and leaves as recorded.
    def test_real_session_same_as_recorded(self):
        timeline = []
        for line in sample("kernel_softap.log").splitlines():
            ts = time.mktime(time.strptime(line[:19], "%Y-%m-%d %H:%M:%S")) + float("0." + line[20:23])
            timeline.append((ts, line))
        end = timeline[-1][0] + 20
        new = self.replay(wifi.WifiTracker, timeline, end)
        start = timeline[0][0]
        self.assertEqual([[e[0], e[1]] + list(e[2:-1]) + [round(e[-1] - start, 3)] for e in new], golden("wifi_session"))
        # As logged live that day: S2 joins, leaves, comes back; S1 joins and leaves; S2 leaves.
        self.assertEqual([(e[0], e[1]) for e in new],
                         [("join", S2), ("leave", S2), ("join", S2), ("join", S1), ("leave", S1), ("leave", S2)])
        self.assertTrue(all(e[2] == "disconnected" for e in new if e[0] == "leave"))

    # The monitor feeds the tracker from any command's output (here: the sample file).
    def test_monitor_feeds_tracker(self):
        joined = []
        tracker = wifi.WifiTracker(lambda m, n: joined.append(m), lambda *a: None)
        mon = wifi.WifiMonitor(tracker, cmd=["/bin/cat", os.path.join(FIX, "kernel_softap.log")])
        mon.start()
        mon.process.wait(timeout=5)
        time.sleep(0.3)
        mon.stop()
        self.assertIn(S2, joined)
        self.assertIn(S2, tracker.present_macs())

# DHCP leases appearing.
class Dhcp(unittest.TestCase):
    # Leases in the file at start aren't announced; new ones are, once.
    def test_monitor(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-test-")
        path = os.path.join(folder, "dhcpd_leases")
        shutil.copy(os.path.join(FIX, "dhcpd_leases"), path)
        got = []
        mon = dhcp.DhcpMonitor(lambda ip, mac: got.append((ip, mac)), path=path)
        mon._mtime = leases.lease_mtime(path)
        dhcp.new_leases(mon.seen, leases.read_leases(path))
        self.assertEqual(mon.check(), [])
        time.sleep(0.01)
        with open(path, "a") as f:
            f.write("{\n\tip_address=192.168.2.7\n\thw_address=1,a:1:2:3:4:5\n\tlease=0x99\n}\n")
        os.utime(path, (time.time() + 1, time.time() + 1))
        self.assertEqual([(l.ip, l.mac) for l in mon.check()], [("192.168.2.7", "0a:01:02:03:04:05")])
        self.assertEqual(got, [("192.168.2.7", "0a:01:02:03:04:05")])
        self.assertEqual(mon.check(), [])
        shutil.rmtree(folder)

# Acting as the real user.
class User(unittest.TestCase):
    # Without sudo nothing is prefixed and nothing changes ownership.
    def test_without_sudo(self):
        saved = {k: os.environ.pop(k) for k in ("SUDO_USER", "SUDO_UID", "SUDO_GID") if k in os.environ}
        try:
            self.assertIsNone(user.sudo_user())
            self.assertEqual(user.as_user_command(["open", "x"]), ["open", "x"])
            self.assertFalse(user.give_to_user("/nonexistent"))
        finally:
            os.environ.update(saved)

    # FileZilla's location is the recorded one.
    def test_filezilla_path(self):
        self.assertEqual(user.FILEZILLA_BIN, golden("filezilla_bin"))
        self.assertEqual(user.filezilla_installed(), os.path.exists(user.FILEZILLA_BIN))

# macOS's password window (control center stage 2): the shell line, the osascript command, how it ended.
class Admin(unittest.TestCase):
    # The line runs in the folder, on behalf of the user (as sudo would say), waited for, everything quoted.
    def test_foreground_line(self):
        line = admin.foreground_line("/Users/me/my folder", ["/usr/bin/python3", "-B", "./nx_spoof.py", "start"],
                                     ("me", 501, 20), "/Users/me/my folder/logs/.page_start.txt")
        self.assertEqual(line, "cd '/Users/me/my folder' || exit 1; /usr/bin/env SUDO_USER=me SUDO_UID=501 SUDO_GID=20 "
                               "/usr/bin/python3 -B ./nx_spoof.py start >>'/Users/me/my folder/logs/.page_start.txt' 2>&1 </dev/null; exit 0")
        self.assertNotIn("nohup", line)              # nohup refuses to run without a terminal ("can't detach from console")

    # The line really runs its command in the folder, as `who` says, and answers when it has finished, whatever its exit
    # code (run here with sh as you, no password: the same shape the password window runs).
    def test_foreground_line_runs(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-admin-")
        try:
            out = os.path.join(folder, "out.txt")
            line = admin.foreground_line(folder, ["/bin/sh", "-c", "pwd; echo $SUDO_USER $SUDO_UID $SUDO_GID; exit 3"], ("me", 501, 20), out)
            self.assertEqual(subprocess.call(["/bin/sh", "-c", line]), 0)
            with open(out) as f:
                where, *who = f.read().split()
            self.assertEqual((os.path.realpath(where), who), (os.path.realpath(folder), ["me", "501", "20"]))
            self.assertNotEqual(subprocess.call(["/bin/sh", "-c", admin.foreground_line(os.path.join(folder, "missing"), ["true"], ("me", 1, 1), out)],
                                                stderr=subprocess.DEVNULL), 0, "a folder it can't enter ends the line with an error")
        finally:
            shutil.rmtree(folder)

    # The line and the sentence go in as arguments: nothing is pasted into the AppleScript.
    def test_command(self):
        cmd = admin.command("echo 'it\"s'", "Your password, please")
        self.assertEqual(cmd[0], "/usr/bin/osascript")
        self.assertEqual(cmd[-2:], ["echo 'it\"s'", "Your password, please"])
        self.assertNotIn("echo", " ".join(cmd[1:-2]))
        self.assertIn("with administrator privileges", " ".join(cmd))

    # Started, cancelled (AppleScript error -128), or failed with macOS's last line.
    def test_outcome(self):
        self.assertEqual(admin.outcome(0, ""), ("started", ""))
        self.assertEqual(admin.outcome(1, "0:120: execution error: User canceled. (-128)")[0], "cancelled")
        self.assertEqual(admin.outcome(1, "line 1\n0:5: execution error: The administrator user name or password was incorrect. (-60007)"),
                         ("failed", "0:5: execution error: The administrator user name or password was incorrect. (-60007)"))
        self.assertEqual(admin.outcome(2, ""), ("failed", "osascript ended with code 2"))

    # The dashboard hands a run who it runs as: this program's own user.
    def test_me(self):
        name, uid, gid = user.me()
        self.assertEqual((uid, gid), (os.getuid(), os.getgid()))
        self.assertTrue(name)

    # Your home is the person behind sudo's (a run as root still finds your data folder), and the data folder is in it.
    def test_home_and_data_folder(self):
        import pwd
        from tests import REAL_DATA_FOLDER
        mine = pwd.getpwuid(os.getuid()).pw_dir
        saved = {k: os.environ.get(k) for k in ("SUDO_USER", "SUDO_UID", "SUDO_GID")}
        try:
            os.environ.update({"SUDO_USER": "someone", "SUDO_UID": str(os.getuid()), "SUDO_GID": str(os.getgid())})
            self.assertEqual(user.home(), mine)
            self.assertEqual(REAL_DATA_FOLDER(), os.path.join(mine, "Library", "Application Support", "NXSPOOF"))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

# A run's life (macos/process.py): a worker on its own that answers its waiter once; stop by signal.
class Process(unittest.TestCase):
    # No idle sleep: caffeinate -i -w <the run's pid> (it ends with the run); let_sleep ends it now, or does nothing if it's gone.
    def test_keep_awake(self):
        started = []

        # A caffeinate that's still running, then stops when told.
        class Proc(object):
            done = None

            # None while it runs.
            def poll(self):
                return self.done

            # Stop it.
            def terminate(self):
                self.done = 0

            # It has stopped.
            def wait(self, timeout=None):
                return 0
        proc = process.keep_awake(4242, start=lambda cmd, **kw: started.append(cmd) or Proc())
        self.assertEqual(started, [["/usr/bin/caffeinate", "-i", "-w", "4242"]])
        process.let_sleep(proc)
        self.assertEqual(proc.done, 0)
        process.let_sleep(proc)                                     # already gone: nothing
        process.let_sleep(None)

    # The worker (a real fork) answers, the waiter gets it; a worker that ends without answering: None; nobody: "timeout".
    def test_background_and_answer(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-process-")
        self.addCleanup(shutil.rmtree, folder)
        out = os.path.join(folder, "out.txt")
        for answer, expected in (({"ok": True, "text": "READY"}, {"ok": True, "text": "READY"}), (None, None)):
            with open(out, "a") as f:
                role, fd, pid = process.background(f)
            if role == "worker":                                # the new process: answer (or not), then leave at once
                try:
                    print("from the worker", flush=True)
                    process.answer(fd, answer)
                finally:
                    os._exit(0)
            self.assertEqual(process.wait_answer(fd, 5), expected)
            os.waitpid(pid, 0)
        with open(out) as f:
            self.assertIn("from the worker", f.read())
        read_end, write_end = os.pipe()
        self.assertEqual(process.wait_answer(read_end, 0.2), "timeout")
        os.close(write_end)

    # stop: a process that's gone is False; one that's there gets SIGTERM.
    def test_stop(self):
        child = subprocess.Popen(["/bin/sleep", "5"])
        self.assertTrue(process.stop(child.pid))
        self.assertEqual(child.wait(5), -15)
        self.assertFalse(process.stop(child.pid))

# The lock and run.json (output/runfile.py): one run at a time, believed only while held.
class RunFile(unittest.TestCase):
    # Taken once; a second take fails while held; read only while held; free again once let go (however the holder ends).
    def test_lock(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-runfile-")
        self.addCleanup(shutil.rmtree, folder)
        self.assertFalse(runfile.held(folder))
        held = runfile.take(folder)
        self.assertIsNotNone(held)
        self.assertIsNone(runfile.take(folder))
        self.assertTrue(runfile.held(folder))
        runfile.write(folder, {"pid": 4242, "ready": False})
        self.assertEqual(runfile.read(folder), {"pid": 4242, "ready": False})
        held.close()
        self.assertEqual((runfile.held(folder), runfile.read(folder)), (False, {}))
        child = subprocess.Popen(["/usr/bin/python3", "-c", "import fcntl, sys, time; f = open(sys.argv[1], 'a'); "
                                  "fcntl.flock(f, fcntl.LOCK_EX); print(1, flush=True); time.sleep(5)", os.path.join(folder, runfile.LOCK)],
                                 stdout=subprocess.PIPE)
        child.stdout.readline()
        self.assertTrue(runfile.held(folder))
        child.kill()
        child.wait(5)
        self.assertFalse(runfile.held(folder), "a killed holder frees the lock")
        runfile.clear(folder)
        self.assertFalse(os.path.exists(os.path.join(folder, runfile.INFO)))

# The reminder dialog (macos/dialog.py): text, title and buttons go in as arguments; an unknown answer is None.
class Dialog(unittest.TestCase):
    # The command, and the answers it maps.
    def test_ask(self):
        from nxspoof.macos import dialog
        cmd = dialog.command("Still running", ("No", "Yes"), "Yes", wait=30)
        self.assertEqual(cmd[3:], ["Still running", "NXSPOOF", "No", "Yes", "Yes"])
        self.assertIn("giving up after 30", cmd[2])
        self.assertNotIn("Still running", cmd[2], "the text is never pasted into the script")
        self.assertEqual(dialog.ask("t", ("No", "Yes"), "Yes", run=lambda c: (0, "Yes\n")), "Yes")
        self.assertIsNone(dialog.ask("t", ("No", "Yes"), "Yes", run=lambda c: (0, "\n")))
        self.assertIsNone(dialog.ask("t", ("No", "Yes"), "Yes", run=lambda c: (1, "error")))

# The hotspot's name and channel, where macOS 15 keeps them (airport preferences, InternetSharing; macos/hotspot.py).
class Hotspot(unittest.TestCase):
    # The SSID is bytes; a missing section reads as None.
    def test_from_prefs(self):
        section = {"SSID": b"NXSPOOF", "Channel": 11, "SecurityType": "WPA2/WPA3 Personal"}
        self.assertEqual(hotspot.from_prefs({"InternetSharing": section}), {"name": "NXSPOOF", "channel": 11, "security": "WPA2/WPA3 Personal"})
        self.assertIsNone(hotspot.from_prefs({}))

    # The password is only checked for (its keychain entry exists), never read.
    def test_password_set(self):
        asked = []
        self.assertTrue(hotspot.password_set(run=lambda cmd: asked.append(cmd) or (0, "")))
        self.assertNotIn("-w", asked[0])
        self.assertFalse(hotspot.password_set(run=lambda cmd: (44, "could not be found")))

if __name__ == "__main__":
    unittest.main()
