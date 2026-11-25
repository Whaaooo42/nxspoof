"""
test_setup.py - unit tests for nxspoof.setup (steps and checklist) and the macOS helpers it uses
(netservices, appfirewall).

Commands are answered from real outputs of the owner's Mac (tests/fixtures/macos/) and constructed
ones in the same format: a fresh Mac (no Thunderbolt Bridge service), a half-configured one (bridge
on automatic) and the owner's (ready). Nothing here changes this Mac.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from nxspoof.app import cli, starter
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.macos import appfirewall, cable, netservices
from nxspoof.setup import checklist, steps

MACOS = os.path.join(PROJECT_DIR, "tests", "fixtures", "macos")
FIXTURE_SETTINGS = os.path.join(PROJECT_DIR, "tests", "fixtures", "settings_current.json")
NS = netservices.NETWORKSETUP
FW = appfirewall.SOCKETFILTERFW

# Read a sample and close it.
def sample(name):
    with open(os.path.join(MACOS, name), encoding="utf-8") as f:
        return f.read()

# A Mac answering commands from samples; records every command it was asked to run.
class FakeMac(object):
    # kind: "fresh", "half" or "ready".
    def __init__(self, kind):
        self.kind = kind
        self.commands = []
        self.cable_up = kind == "ready"
        self.ifconfig = ""                  # what plain `ifconfig` shows (a test can add a network that overlaps the bridge's)
        self.firewall = ("Firewall is disabled. (State = 0)", "Firewall has block all state set to disabled.")

    # Answer one command -> (code, output); text: what was typed into it (scutil's questions).
    def run(self, cmd, text=None):
        self.commands.append(tuple(cmd))
        if cmd[:2] == [NS, "-listallhardwareports"]:
            return 0, sample("hardware_ports.txt")
        if cmd[:2] == [NS, "-listnetworkserviceorder"]:
            return 0, sample("networksetup_order_no_bridge.txt" if self.kind == "fresh" else "networksetup_order.txt")
        if cmd[:2] == [NS, "-getinfo"]:
            return 0, sample("networksetup_info_manual.txt" if self.kind == "ready" else "networksetup_info_dhcp.txt")
        if cmd[:2] in ([NS, "-createnetworkservice"], [NS, "-setmanual"], [NS, "-setdhcp"], [NS, "-setnetworkserviceenabled"]):
            if cmd[1] in ("-createnetworkservice", "-setmanual"):
                self.kind = "half" if cmd[1] == "-createnetworkservice" else "ready"
            return 0, ""
        if cmd == [cable.SCUTIL, "--get", "ComputerName"]:
            return 0, "Test Mac\n"
        if cmd == [cable.IFCONFIG]:
            return 0, self.ifconfig
        if cmd[0] in (cable.IFCONFIG, cable.SCUTIL):
            return self.cable(cmd, text)
        if cmd[0] == FW:
            return 0, self.firewall[0] if cmd[1] == "--getglobalstate" else self.firewall[1]
        raise AssertionError("unexpected command %r" % (cmd,))

    # The virtual cable's commands: ifconfig (the bridge, the feth pair) and scutil (the bridge's live address).
    def cable(self, cmd, text):
        if cmd[0] == cable.SCUTIL:
            if text.startswith("list"):
                return 0, "  subKey [0] = State:/Network/Service/ABCD/IPv4\n" if self.cable_up else ""
            return 0, "<dictionary> {\n  Addresses : <array> {\n    0 : 172.16.0.1\n  }\n}\n"
        if cmd[1:] == ["bridge0"]:
            return 0, "bridge0: flags=8863<UP>\n\tstatus: %s\n%s" % ("active" if self.cable_up else "inactive",
                                                                    "\tmember: feth0 flags=3<LEARNING,DISCOVER>\n" if self.cable_up else "")
        if cmd[2:] in (["addm", "feth0"], ["deletem", "feth0"]):
            self.cable_up = cmd[2] == "addm"
        return (1, "") if len(cmd) == 2 and not self.cable_up else (0, "")

# The few System calls setup uses.
class FakeSystem(object):
    # Root or not; FileZilla or not.
    def __init__(self, root=True, filezilla=True):
        self.root, self.filezilla, self.calls = root, filezilla, []
        self.hotspot_now = {"name": "NXSPOOF", "channel": 11, "security": "WPA2/WPA3 Personal", "password": True}

    # As set.
    def is_root(self):
        return self.root

    # The hotspot isn't up.
    def find_sharing_interface(self):
        return None

    # Record.
    def set_sharing(self, enabled):
        self.calls.append(("sharing", enabled))
        return "enabled=%s" % enabled

    # As set.
    def filezilla_installed(self):
        return self.filezilla

    # The hotspot as the default settings want it, with a password (a test can change hotspot_now).
    def hotspot_settings(self):
        return dict(self.hotspot_now)

    # Nothing to hand back in tests.
    def give_to_user(self, path):
        return False

# A temporary project folder with settings.json (the virtual cable settles at once on the fake Mac).
class SetupTest(unittest.TestCase):
    # The folder.
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-test-")
        shutil.copy(FIXTURE_SETTINGS, os.path.join(self.dir, "settings.json"))
        self.settle, cable.SETTLE_SECONDS = cable.SETTLE_SECONDS, 0

    # Remove it.
    def tearDown(self):
        cable.SETTLE_SECONDS = self.settle
        shutil.rmtree(self.dir)

    # A Machine on a fake Mac.
    def machine(self, mac, system=None, busy=()):
        return steps.Machine(self.dir, system or FakeSystem(), run=mac.run, port_check=lambda p: p in busy)

    # Run the checklist; returns (exit code, Terminal text). answers: what "you" reply to each question.
    def run_checklist(self, command, mac, system=None, answers=(), style="casual", busy=(), options=()):
        out, replies = io.StringIO(), list(answers)
        system = system or FakeSystem()
        code = checklist.main(command, system, self.dir, out, style=style, ask=lambda q, d: replies.pop(0),
                              machine=self.machine(mac, system, busy), args=cli.parse_args([command] + list(options)))
        return code, out.getvalue()

# Parsing macOS's outputs.
class Parsers(unittest.TestCase):
    # Services in order with their device; disabled ones marked.
    def test_service_order(self):
        services = netservices.parse_service_order(sample("networksetup_order.txt"))
        self.assertEqual([(s["name"], s["device"], s["enabled"]) for s in services],
                         [("Thunderbolt Bridge", "bridge0", True), ("Wi-Fi", "en0", True), ("iPhone USB", "en5", True)])
        other = netservices.parse_service_order(sample("networksetup_order_no_bridge.txt"))
        self.assertIsNone(netservices.service_for_device(other, "bridge0"))
        self.assertEqual([s["enabled"] for s in other], [True, False])

    # Address settings: manual with the fixed address, or automatic.
    def test_info(self):
        self.assertEqual(netservices.parse_info(sample("networksetup_info_manual.txt")),
                         {"mode": "manual", "ip": "172.16.0.1", "mask": "255.255.255.0"})
        self.assertEqual(netservices.parse_info(sample("networksetup_info_dhcp.txt"))["mode"], "dhcp")
        self.assertEqual(netservices.parse_info("")["mode"], "other")

    # macOS firewall: off, on, blocking everything.
    def test_appfirewall(self):
        self.assertEqual(appfirewall.parse_state("Firewall is disabled. (State = 0)", "Firewall has block all state set to disabled."),
                         {"enabled": False, "block_all": False})
        self.assertEqual(appfirewall.parse_state("Firewall is enabled. (State = 1)", "Firewall has block all state set to enabled."),
                         {"enabled": True, "block_all": True})
        self.assertEqual(appfirewall.parse_state("Firewall is enabled. (State = 2)", ""), {"enabled": True, "block_all": True})

    # A failed networksetup change raises with its own message.
    def test_change_errors(self):
        with self.assertRaises(RuntimeError) as e:
            netservices.set_manual("Nope", "1.2.3.4", "255.0.0.0", run=lambda cmd: (4, "** Error: The parameters were not valid."))
        self.assertIn("parameters were not valid", str(e.exception))

# Each step on the three kinds of Mac.
class Steps(SetupTest):
    # The owner's Mac: everything fine.
    def test_ready_mac(self):
        m = self.machine(FakeMac("ready"))
        states = {s.name: s.check(m).state for s in steps.STEPS}
        self.assertEqual(states, {"settings": "ok", "python": "ok", "bridge": "ok", "cable": "ok", "sharing": "ok", "hotspot": "ok",
                                  "ports": "ok", "macfirewall": "ok", "filezilla": "ok", "consoles": "info"})

    # A fresh Mac: the bridge service is created and given its address.
    def test_fresh_mac_bridge(self):
        mac = FakeMac("fresh")
        m = self.machine(mac)
        step = steps.BridgeService()
        self.assertEqual(step.check(m).state, "fixable")
        self.assertEqual(steps.InternetSharing().check(m).state, "manual")
        step.fix(m)
        self.assertIn((NS, "-createnetworkservice", "Thunderbolt Bridge", "bridge0"), mac.commands)
        self.assertIn((NS, "-setmanual", "Thunderbolt Bridge", "172.16.0.1", "255.255.255.0"), mac.commands)

    # A half-configured Mac: the bridge is on automatic -> fixable with a fixed address; undo puts it back.
    def test_half_mac_bridge(self):
        mac = FakeMac("half")
        m = self.machine(mac)
        r = steps.BridgeService().check(m)
        self.assertEqual(r.state, "fixable")
        self.assertIn("dhcp 169.254.12.34", r.tech)
        steps.BridgeService().fix(m)
        self.assertEqual(steps.BridgeService().check(m).state, "ok")
        steps.BridgeService().undo(m)
        self.assertEqual(mac.commands[-1], (NS, "-setdhcp", "Thunderbolt Bridge"))

    # The virtual cable (macos/cable.py): status / up / down, with what each says.
    def test_cable(self):
        mac = FakeMac("half")
        m = self.machine(mac)
        r = steps.VirtualCable().check(m)
        self.assertEqual((r.state, r.tech), ("fixable", "virtual cable: FAIL - not plugged in yet (setup and Start plug it in)"))
        self.assertEqual(steps.VirtualCable().fix(m), "virtual cable plugged in")
        self.assertIn((cable.IFCONFIG, "bridge0", "addm", "feth0"), mac.commands)
        r = steps.VirtualCable().check(m)
        self.assertEqual((r.state, r.tech), ("ok", "virtual cable: PASS - macOS sees Thunderbolt Bridge as up with its address"))
        steps.VirtualCable().undo(m)
        self.assertEqual(steps.VirtualCable().check(m).state, "fixable")

    # Busy ports: exactly how to find the program using them.
    def test_ports(self):
        r = steps.Ports().check(self.machine(FakeMac("ready"), busy=(80,)))
        self.assertEqual(r.state, "manual")
        self.assertEqual(r.explain, ["Find it with:  sudo lsof -nP -iTCP:80 -sTCP:LISTEN   and quit it."])

    # The dashboard's port is never checked (D30: it finds a free one itself): only port 80 counts.
    def test_ports_only_80(self):
        m = steps.Machine(self.dir, FakeSystem(), run=FakeMac("ready").run, port_check=lambda p: p == 8800)
        self.assertEqual(steps.Ports().check(m).state, "ok")

    # macOS's firewall blocking everything must be turned off by you.
    def test_mac_firewall(self):
        mac = FakeMac("ready")
        mac.firewall = ("Firewall is enabled. (State = 1)", "Firewall has block all state set to enabled.")
        self.assertEqual(steps.MacFirewall().check(self.machine(mac)).state, "manual")

    # settings.json: missing -> created (defaults, no consoles); broken -> the line and column.
    def test_settings(self):
        path = os.path.join(self.dir, "settings.json")
        os.remove(path)
        m = self.machine(FakeMac("ready"))
        self.assertEqual(steps.SettingsFile().check(m).state, "fixable")
        steps.SettingsFile().fix(m)
        self.assertEqual(steps.SettingsFile().check(m).tech, "loaded settings.json (0 consoles)")
        with open(path, "w") as f:
            f.write('{"spoof": {"firewall": true,}}')
        r = steps.SettingsFile().check(m)
        self.assertEqual(r.state, "manual")
        self.assertIn("line 1", r.tech)

# A check that crashes (a macOS it never met) is a row that says so: never a broken ▶ Start or This Mac panel.
class CrashingCheck(unittest.TestCase):
    # One step's check raises: check_safely makes it "manual" with the error's kind; mac_checks still answers every row.
    def test_crashing_check(self):
        # A step whose check raises.
        class Broken(object):
            name, title, setup_only = "broken", "Broken step", False

            # It crashes.
            def check(self, machine):
                raise KeyError("Wi-Fi")
        result, error = steps.check_safely(Broken(), None)
        self.assertEqual((result.state, result.tech), ("manual", "check failed: KeyError: 'Wi-Fi'"))
        self.assertIsInstance(error, KeyError)
        folder = tempfile.mkdtemp(prefix="nxspoof-crash-")
        try:
            with mock.patch.object(steps, "STEPS", [Broken()]):
                rows = starter.mac_checks(folder, FakeSystem())
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.assertEqual([(r["name"], r["state"]) for r in rows], [("broken", "manual")])

# The checklist: doctor, setup, uninstall.
class Checklist(SetupTest):
    # Doctor on the owner's Mac: ready, nothing changed.
    def test_doctor_ready(self):
        mac = FakeMac("ready")
        code, text = self.run_checklist("doctor", mac)
        self.assertEqual(code, 0)
        self.assertTrue(text.rstrip().endswith("✅ This Mac is ready for NXSPOOF."))
        self.assertFalse([c for c in mac.commands if c[1:2] in (("-setmanual",), ("-createnetworkservice",))])

    # Doctor after a restart: the unplugged cable is only noted (Start plugs it in).
    def test_doctor_cable_is_not_a_problem(self):
        mac = FakeMac("ready")
        mac.cable_up = False
        code, text = self.run_checklist("doctor", mac)
        self.assertEqual(code, 0)
        self.assertIn("ℹ️ Virtual cable:", text)

    # Doctor on a fresh Mac: not ready, and says where to go.
    def test_doctor_fresh(self):
        code, text = self.run_checklist("doctor", FakeMac("fresh"))
        self.assertEqual(code, 1)
        self.assertIn("Not ready yet: 2 things to fix. Set up this Mac (dashboard: ⚙️ Configure; or menu 7 → 2).", text)

    # Setup on a fresh Mac, saying yes: the bridge gets fixed, then the cable; everything ends fine.
    def test_setup_fresh_yes(self):
        mac = FakeMac("fresh")
        code, text = self.run_checklist("setup", mac, answers=[True, True])
        self.assertEqual(code, 0, text)
        self.assertIn("🔧 Thunderbolt Bridge: fixed", text)
        self.assertIn("🔧 Virtual cable: fixed", text)
        self.assertIn("🎉 This Mac is ready.", text)
        self.assertTrue([f for f in os.listdir(os.path.join(self.dir, "logs")) if f.endswith("-setup.log")])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "last_run.log")))

    # Setup, saying no: nothing changed, and it says what's left.
    def test_setup_no(self):
        mac = FakeMac("half")
        code, text = self.run_checklist("setup", mac, answers=[False, False])
        self.assertEqual(code, 1)
        self.assertIn("⏭️ Thunderbolt Bridge: skipped", text)
        self.assertNotIn((NS, "-setmanual", "Thunderbolt Bridge", "172.16.0.1", "255.255.255.0"), mac.commands)

    # Setup without admin rights: the fixes that need them point to the setup buttons and menu 7 → 2.
    def test_setup_not_root(self):
        code, text = self.run_checklist("setup", FakeMac("half"), system=FakeSystem(root=False))
        self.assertEqual(code, 1)
        self.assertIn("🛠️ Fix selected in the dashboard's ⚙️ Configure, or menu 7 → 2", text)
        self.assertEqual(text.count("Run setup with admin rights"), 2, "once per step that needs them, not twice (casual: the hint is in the line)")

    # Technical style: one line per step, the same as the log file.
    def test_technical_style(self):
        _, text = self.run_checklist("doctor", FakeMac("ready"), style="technical")
        self.assertIn("DOCTOR bridge       ok       service 'Thunderbolt Bridge' (bridge0): manual 172.16.0.1/255.255.255.0", text)

    # Uninstall: asks first; undoes sharing, cable, bridge (in that order).
    def test_uninstall(self):
        mac, system = FakeMac("ready"), FakeSystem()
        code, _ = self.run_checklist("uninstall", mac, system=system, answers=[False])
        self.assertEqual((code, system.calls), (0, []))
        code, text = self.run_checklist("uninstall", mac, system=system, answers=[True])
        self.assertEqual(code, 0, text)
        self.assertEqual(system.calls, [("sharing", False)])                                    # the hotspot is never touched
        changes = [c for c in mac.commands if c[2:3] == ("deletem",) or c[1:2] == ("-setdhcp",)]
        self.assertEqual([c[-1] for c in changes], ["feth0", "Thunderbolt Bridge"])         # the cable out, the bridge back to automatic

    # The records twin of a setup log: [(kind, data)] of its facts.
    def facts(self, label):
        logs = os.path.join(self.dir, "logs")
        name = [n for n in sorted(os.listdir(logs)) if n.endswith("-%s.jsonl" % label)][-1]
        with open(os.path.join(logs, name), encoding="utf-8") as f:
            return [(r["kind"], r.get("data", {})) for r in map(json.loads, f) if r["kind"] != "log"]

    # --yes --only cable: no questions; the bridge is checked and reported, not fixed; every step is a fact; the ticket too.
    def test_setup_only(self):
        mac = FakeMac("fresh")
        code, text = self.run_checklist("setup", mac, options=["--yes", "--only", "cable", "--task", "t123"])
        self.assertEqual(code, 1, text)
        self.assertIn("⏭️ Thunderbolt Bridge: ", text)
        self.assertIn("not fixed this time (not chosen)", text)
        self.assertNotIn((NS, "-setmanual", "Thunderbolt Bridge", "172.16.0.1", "255.255.255.0"), mac.commands)
        facts = self.facts("setup")
        self.assertEqual(facts[0], ("task.started", {"ticket": "t123"}))
        self.assertIn(("bridge", "skipped"), [(d["step"], d["state"]) for k, d in facts if k == "checklist.step"])
        self.assertEqual(facts[-1], ("checklist.done", {"command": "setup", "todo": 2}))       # the bridge, and sharing (needs the bridge)

    # --only bridge on a fresh Mac: the cable (it depends on the bridge) is plugged in too, in the same setup; --skip still wins.
    def test_setup_only_bridge_takes_the_cable(self):
        mac = FakeMac("fresh")
        self.run_checklist("setup", mac, options=["--yes", "--only", "bridge"])
        states = {d["step"]: d["state"] for k, d in self.facts("setup") if k == "checklist.step"}
        self.assertEqual((states["bridge"], states["cable"]), ("fixed", "fixed"))
        self.assertTrue(mac.cable_up)
        mac = FakeMac("fresh")
        self.run_checklist("setup", mac, options=["--yes", "--only", "bridge", "--skip", "cable"])
        self.assertFalse(mac.cable_up)

    # --skip with a name setup doesn't know: stops before anything is checked, with the list of steps.
    def test_setup_unknown_step(self):
        mac = FakeMac("fresh")
        code, text = self.run_checklist("setup", mac, options=["--skip", "cable,nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown step(s) for --only / --skip: nope (the steps: settings, python, bridge", text)
        self.assertEqual(mac.commands, [])

    # Setup and undo wait while a run is active (the lock is held): said, nothing changed (clean up too).
    def test_waits_while_a_run_is_active(self):
        from nxspoof.output import runfile
        held = runfile.take(self.dir)
        try:
            for command in ("setup", "uninstall", "cleanup"):
                mac, system = FakeMac("fresh"), FakeSystem()
                code, text = self.run_checklist(command, mac, system=system, options=["--yes"])
                self.assertEqual((code, system.calls), (1, []), command)
                self.assertIn("NXSPOOF is running: stop it first, then try again.", text)
        finally:
            held.close()

    # Settings for this test's folder (merged into the copy of settings.json).
    def settings(self, **sections):
        path = os.path.join(self.dir, "settings.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.update(sections)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    # The hotspot is read only: any name with a password -> ✅ (said as it is); no password, or an open network -> needs you.
    def test_hotspot_step(self):
        system, step = FakeSystem(), steps.Hotspot()
        m = self.machine(FakeMac("ready"), system)
        system.hotspot_now["name"] = "Living room"
        r = step.check(m)
        self.assertEqual((r.state, r.casual), ("ok", '"Living room", channel 11, with a password'))
        system.hotspot_now["security"] = "None"
        self.assertEqual(step.check(m).state, "manual")
        system.hotspot_now.update(security="WPA2/WPA3 Personal", password=False)
        self.assertEqual(step.check(m).state, "manual")
        self.assertIsNone(steps.Hotspot.undo)

    # The bridge uses the chosen address, and refuses one in a network this Mac already uses.
    def test_bridge_address_setting(self):
        self.settings(bridge={"address": "10.254.0.1"})
        mac = FakeMac("ready")                                  # its service has the old 172.16.0.1
        r = steps.BridgeService().check(self.machine(mac))
        self.assertEqual(r.state, "fixable")
        self.assertIn("needs manual 10.254.0.1/255.255.255.0", r.tech)
        mac.ifconfig = "en7: flags=8863<UP>\n\tinet 10.254.0.20 netmask 0xffffff00\n"
        r = steps.BridgeService().check(self.machine(mac))
        self.assertEqual((r.state, r.tech), ("manual", "bridge address 10.254.0.1 overlaps en7 (10.254.0.20)"))

    # The bridge's before-note: how it is (DHCP), but never one of NXSPOOF's own addresses (NXSPOOF set it: Undo goes automatic).
    def test_bridge_record_skips_own_address(self):
        self.assertEqual(steps.BridgeService().record(self.machine(FakeMac("ready"))), None)
        self.assertEqual(steps.BridgeService().record(self.machine(FakeMac("half")))["mode"], "dhcp")
        self.assertEqual(steps.BridgeService().record(self.machine(FakeMac("fresh"))), None)

    # Uninstall without admin rights changes nothing.
    def test_uninstall_not_root(self):
        code, _ = self.run_checklist("uninstall", FakeMac("ready"), system=FakeSystem(root=False))
        self.assertEqual(code, 1)

if __name__ == "__main__":
    unittest.main()
