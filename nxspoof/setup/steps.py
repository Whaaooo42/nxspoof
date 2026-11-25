"""
nxspoof.setup.steps - what a Mac needs for NXSPOOF, one small step per requirement.

Each step has:
  check(m)  -> Result: "ok", "fixable" (setup can fix it, after asking), "manual" (you have to do
               something; the explanation says exactly what), or "info" (worth knowing, never blocks)
  fix(m)    -> does the fix (most need admin rights); the checklist checks again afterwards
  undo(m)   -> for uninstall: puts back what setup changed (only some steps)

Steps never print: they return results. Every command goes through m.run(cmd) -> (code, output), so
the tests replay real outputs of a fresh Mac, a half-configured one and the owner's.
Interfaces are found by device (bridge0, en0), never by display names (they follow the macOS language).
"""

import os
import socket
import sys

from nxspoof.core.fmt import error_text
from nxspoof.core.places import Places
from nxspoof.macos import appfirewall, cable, hotspot, netif, netservices, shell
from nxspoof.settings import schema
from nxspoof.settings.store import SettingsError, SettingsStore

# ##############################################################################################################################################################
# ##  RESULTS AND THE MACHINE                                                                                                                                 ##
# ##############################################################################################################################################################

STATES = ("ok", "fixable", "manual", "info")

# What a check found: a state, a technical text (log file), a casual text (Terminal), what to do (lines).
class Result(object):
    __slots__ = ("state", "tech", "casual", "explain")

    # explain: the lines shown when you have to do something (or for "info").
    def __init__(self, state, tech, casual=None, explain=None):
        if state not in STATES:
            raise ValueError("unknown state %r" % state)
        self.state, self.tech, self.casual, self.explain = state, tech, casual or tech, list(explain or [])

    # Short form for debugging and tests.
    def __repr__(self):
        return "Result(%s, %r)" % (self.state, self.tech)

# A step's check, run so that a check that crashes (a macOS it never met) becomes a "manual" result instead of stopping
# everything -> (Result, the error or None). Setup (setup/checklist.py) and ▶ Start / This Mac (app/starter.py) both use it.
def check_safely(step, machine):
    try:
        return step.check(machine), None
    except Exception as e:
        return Result("manual", "check failed: %s" % error_text(e), "I couldn't check this (%s)" % e), e

# True if something accepts TCP connections on 127.0.0.1:port.
def port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False

# The Mac being set up: how to run commands, where the project is, what's installed.
class Machine(object):
    # system: app's System (root?, sharing, FileZilla); run / port_check: replaceable for tests;
    # places: where NXSPOOF's files are (core/places.py; the project folder for every role when not given).
    def __init__(self, project_dir, system, run=None, port_check=None, settings_path=None, places=None):
        self.project_dir, self.system = project_dir, system
        self.places = places or Places.together(project_dir, project_dir)
        self.run = run or shell.run
        self.port_check = port_check or port_in_use
        self.settings_path = settings_path or self.places.settings_file()
        self.undo_to, self.before = "defaults", {}           # what Undo puts back (setup/repair.py sets them)
        self._ports, self._settings = None, None

    # A setting as settings.json says it (its default if the file is missing or broken).
    def setting(self, name):
        if self._settings is None:
            try:
                store = SettingsStore(self.settings_path)
                store.load()
                self._settings = store
            except (SettingsError, OSError):
                self._settings = False
        return self._settings.get(name) if self._settings else schema.field(name).default

    # The bridge's fixed address and mask (settings: bridge.address).
    def bridge_address(self):
        return self.setting("bridge.address"), netservices.BRIDGE_MASK

    # The network an address is in, if another interface of this Mac already uses it: (interface, its address) or None.
    def overlap(self, address):
        prefix = address.rsplit(".", 1)[0] + "."
        for iface, info in netif.parse_ifconfig(self.run([cable.IFCONFIG])[1]).items():
            if iface in (self.bridge_device(), "lo0"):
                continue
            for inet in info["inets"]:
                if inet.startswith(prefix):
                    return iface, inet
        return None

    # The hardware ports (asked once).
    def hardware_ports(self):
        if self._ports is None:
            self._ports = netif.parse_hardware_ports(netservices.networksetup("-listallhardwareports", run=self.run)[1])
        return self._ports

    # The Thunderbolt Bridge device (bridge0 on every Mac so far).
    def bridge_device(self):
        return netif.bridge_device(self.hardware_ports())

    # The Thunderbolt Bridge network service entry, or None.
    def bridge_service(self):
        return netservices.service_for_device(netservices.list_services(run=self.run), self.bridge_device())

    # True when running as root.
    def is_root(self):
        return self.system.is_root()

# ##############################################################################################################################################################
# ##  THE STEPS                                                                                                                                               ##
# ##############################################################################################################################################################

# One requirement: check, and optionally fix and undo.
class Step(object):
    name = "step"                                  # short id (log file, tests)
    title = "Step"                                 # what you see
    needs_root = True                              # fix/undo need admin rights
    setup_only = False                             # shown by setup, not by doctor
    depends_on = ()                                # steps whose fix may undo this one: fixing one of them chooses this one too

    # What's there now.
    def check(self, m):
        raise NotImplementedError

    # Make it right (only called for "fixable"); returns what was done.
    def fix(self, m):
        raise NotImplementedError

    # Put back what setup changed (None = nothing to undo); returns what was done.
    undo = None

# settings.json exists and is valid.
class SettingsFile(Step):
    name, title, needs_root = "settings", "settings.json", False

    # Present and readable, with any warnings.
    def check(self, m):
        if not os.path.exists(m.settings_path):
            return Result("fixable", "no settings file at %s" % m.settings_path, "there's no settings.json yet")
        store = SettingsStore(m.settings_path)
        try:
            message = store.load()
        except SettingsError as e:
            return Result("manual", str(e).splitlines()[0], "settings.json has a typo",
                          [str(e).splitlines()[0], "Fix that line (or rename the file to start from a clean one) and run setup again."])
        if store.warnings:
            return Result("info", "%s, %d warning(s): %s" % (message, len(store.warnings), "; ".join(store.warnings)),
                          "settings.json works, with remarks", store.warnings)
        return Result("ok", message, message.replace("loaded ", "", 1))

    # Write a clean settings.json (defaults, no consoles; consoles get added when they first connect).
    def fix(self, m):
        store = SettingsStore(m.settings_path, on_saved=m.system.give_to_user)
        store.load()
        store.save()
        return "created %s with the defaults (no consoles yet)" % m.settings_path

# Python 3.9 or newer (Apple's comes with the command-line tools; every .command file offers them if missing).
class Python(Step):
    name, title, needs_root = "python", "Python", False

    # The Python running this is the one the starter uses.
    def check(self, m):
        v = sys.version_info
        version = "%d.%d.%d" % (v[0], v[1], v[2])
        if v < (3, 9):
            return Result("manual", "Python %s is too old (3.9 or newer needed)" % version, "Python is too old",
                          ["Install Apple's command-line tools: run  xcode-select --install  in Terminal."])
        return Result("ok", "Python %s (%s)" % (version, sys.executable), "Python %s" % version)

# How a part of this Mac was before NXSPOOF changed it, when Undo puts that back (m.undo_to == "before"), else None.
def recorded(m, part):
    return m.before.get(part) if m.undo_to == "before" else None

# Put the bridge's service back: its recorded manual address (was: {"mode", "ip", "mask"}), else automatic (DHCP).
def restore_bridge(m, name, was):
    if was.get("mode") == "manual" and was.get("ip"):
        netservices.set_manual(name, was["ip"], was.get("mask") or netservices.BRIDGE_MASK, run=m.run)
        return "service '%s' back to manual %s, as before NXSPOOF" % (name, was["ip"])
    netservices.set_dhcp(name, run=m.run)
    return "service '%s' back to automatic (DHCP)%s" % (name, ", as before NXSPOOF" if was else "")

# The Thunderbolt Bridge network service, with the fixed address Internet Sharing shares from (settings: bridge.address).
class BridgeService(Step):
    name, title = "bridge", "Thunderbolt Bridge"

    # A service for the bridge device, turned on, with the chosen manual address (one no other network of this Mac uses).
    def check(self, m):
        device, svc = m.bridge_device(), m.bridge_service()
        ip, mask = m.bridge_address()
        clash = m.overlap(ip)
        if clash:
            return Result("manual", "bridge address %s overlaps %s (%s)" % (ip, clash[0], clash[1]),
                          "the bridge's address %s is in a network this Mac already uses (%s)" % (ip, clash[0]),
                          ["Pick another bridge address on Thunderbolt Bridge's line in the dashboard's ⚙️ Configure, then set up again."])
        if svc is None:
            return Result("fixable", "no network service for %s" % device, "the Thunderbolt Bridge network service is missing")
        if not svc["enabled"]:
            return Result("fixable", "service '%s' (%s) is turned off" % (svc["name"], device), "the Thunderbolt Bridge service is turned off")
        info = netservices.get_info(svc["name"], run=m.run) or {}
        if info.get("mode") == "manual" and info.get("ip") == ip:
            return Result("ok", "service '%s' (%s): manual %s/%s" % (svc["name"], device, info.get("ip"), info.get("mask")),
                          "Thunderbolt Bridge has its fixed address (%s)" % ip)
        return Result("fixable", "service '%s' (%s): %s %s - needs manual %s/%s" % (
            svc["name"], device, info.get("mode"), info.get("ip"), ip, mask), "Thunderbolt Bridge needs its fixed address (%s)" % ip)

    # Create / turn on the service and give it the chosen address.
    def fix(self, m):
        device, svc = m.bridge_device(), m.bridge_service()
        ip, mask = m.bridge_address()
        done = []
        if svc is None:
            netservices.create_service(netservices.BRIDGE_SERVICE_NAME, device, run=m.run)
            svc = {"name": netservices.BRIDGE_SERVICE_NAME, "enabled": True}
            done.append("created service '%s' for %s" % (svc["name"], device))
        if not svc["enabled"]:
            netservices.set_enabled(svc["name"], True, run=m.run)
            done.append("turned service '%s' on" % svc["name"])
        netservices.set_manual(svc["name"], ip, mask, run=m.run)
        done.append("set '%s' to manual %s/%s" % (svc["name"], ip, mask))
        return "; ".join(done)

    # How it is now, for Undo's "before" record: {"mode", "ip", "mask"}; None without a service, or when it already has one
    # of NXSPOOF's own addresses (netservices.BRIDGE_ADDRESSES: NXSPOOF set it, so it's no "before"; Undo then goes automatic).
    def record(self, m):
        svc = m.bridge_service()
        info = (netservices.get_info(svc["name"], run=m.run) or {}) if svc else None
        if info is None or (info.get("mode") == "manual" and info.get("ip") in netservices.BRIDGE_ADDRESSES):
            return None
        return {k: info.get(k) for k in ("mode", "ip", "mask")}

    # Back to how it was before NXSPOOF (m.undo_to == "before", when recorded), else macOS's default: automatic (DHCP).
    def undo(self, m):
        svc = m.bridge_service()
        if svc is None:
            return "no Thunderbolt Bridge service - nothing to undo"
        return restore_bridge(m, svc["name"], recorded(m, self.name) or {})

# The virtual cable (macos/cable.py) that makes Thunderbolt Bridge look plugged in.
class VirtualCable(Step):
    name, title = "cable", "Virtual cable"
    depends_on = ("bridge",)                       # a new bridge address unplugs it: plugged in again in the same setup

    # Plugged in, and macOS shows the bridge's address?
    def check(self, m):
        st = cable.status(m.run)
        if st.ok:
            return Result("ok", "virtual cable: %s" % st.text(), "the virtual cable is plugged in")
        return Result("fixable", "virtual cable: %s" % st.text(),
                      "the virtual cable isn't plugged in (normal after a restart; Start plugs it in too)")

    # Plug it in (then it must check out).
    def fix(self, m):
        st = cable.up(m.run)
        if not st.ok:
            raise RuntimeError(st.text())
        return "virtual cable plugged in"

    # Take it out.
    def undo(self, m):
        cable.down(m.run)
        return "virtual cable removed"

# Internet Sharing: NXSPOOF turns it on and off by itself at every run; it only needs the bridge service.
class InternetSharing(Step):
    name, title = "sharing", "Internet Sharing"

    # Possible as soon as the bridge service exists; says whether the hotspot is up right now.
    def check(self, m):
        if m.bridge_service() is None:
            return Result("manual", "can't share from Thunderbolt Bridge: no service yet", "needs the Thunderbolt Bridge step first",
                          ["Let setup fix Thunderbolt Bridge first, then run setup again."])
        up = m.system.find_sharing_interface()
        return Result("ok", "turned on/off by NXSPOOF at each run (from %s to Wi-Fi); hotspot up now: %s" % (
            m.bridge_device(), "yes, %s %s" % up if up else "no"), "NXSPOOF turns it on and off by itself")

    # Turn it off (uninstall).
    def undo(self, m):
        return "Internet Sharing off: %s" % m.system.set_sharing(False)

# The hotspot (macOS's, macos/hotspot.py): its name, channel and security as System Settings has them, and that it has a
# password. Read only: NXSPOOF never changes it (D25, revised): all of it is set in System Settings' Wi-Fi Options.
class Hotspot(Step):
    name, title, needs_root = "hotspot", "Hotspot", False

    # It has a password (and isn't an open network)? Its name and channel are said as they are.
    def check(self, m):
        now = m.system.hotspot_settings()
        had = '"%s", channel %s, %s' % (now.get("name") or "?", now.get("channel") or "?", now.get("security") or "?")
        if not now.get("password") or now.get("security") in hotspot.OPEN:
            return Result("manual", "hotspot %s - no password in the System keychain" % had, "the hotspot has no password yet",
                          ["System Settings > General > Sharing > the (i) next to Internet Sharing > Wi-Fi Options…: a password of at "
                           "least 8 characters (you type it once on each Switch)."])
        return Result("ok", "hotspot %s, password set" % had, "\"%s\", channel %s, with a password" % (now.get("name") or "?", now.get("channel") or "?"))

# Port 80 (the web test) must be free (DNS port 53 is handled by plan B on its own; the dashboard finds a free port itself).
class Ports(Step):
    name, title, needs_root = "ports", "Ports", False

    # Nothing else answering on it.
    def check(self, m):
        if not m.port_check(80):
            return Result("ok", "port 80 free", "the port NXSPOOF uses is free")
        return Result("manual", "in use: 80", "another program uses port 80",
                      ["Find it with:  sudo lsof -nP -iTCP:80 -sTCP:LISTEN   and quit it."])

# macOS's own firewall must not block all incoming connections.
class MacFirewall(Step):
    name, title, needs_root = "macfirewall", "macOS firewall", False

    # Off, or on without "Block all incoming connections".
    def check(self, m):
        st = appfirewall.state(run=m.run)
        if st["block_all"]:
            return Result("manual", "macOS firewall blocks all incoming connections", "macOS's firewall blocks every incoming connection",
                          ["System Settings > Network > Firewall > Options…: turn off \"Block all incoming connections\"."])
        if st["enabled"]:
            return Result("ok", "macOS firewall on (not blocking all)", "macOS's firewall is on, which is fine",
                          ["If macOS asks whether python3 may accept incoming connections, click Allow."])
        if st["enabled"] is None:
            return Result("info", "macOS firewall state unknown", "I couldn't read macOS's firewall setting (usually fine)")
        return Result("ok", "macOS firewall off", "macOS's firewall is off, which is fine")

# FileZilla, optional: to browse a console's files when its FTP server is on.
class FileZilla(Step):
    name, title, needs_root = "filezilla", "FileZilla (optional)", False

    # Installed or not; never blocks.
    def check(self, m):
        if m.system.filezilla_installed():
            return Result("ok", "FileZilla installed", "FileZilla is installed")
        return Result("info", "FileZilla not installed (optional)", "FileZilla isn't installed (optional)",
                      ["Only needed to browse a console's files: filezilla-project.org, then drag it to Applications."])

# How to join NXSPOOF from a Switch (setup only).
class ConsoleChecklist(Step):
    name, title, needs_root, setup_only = "consoles", "On the Switch", False, True

    # The screen-by-screen steps.
    def check(self, m):
        return Result("info", "console checklist shown", "joining NXSPOOF from a Switch", [
            "Start NXSPOOF (▶ Start on the dashboard, NXSPOOF.app) and wait for \"All set!\".",
            "On the Switch: System Settings > Internet > Internet Settings > NXSPOOF > type the password.",
            "It says Connected. A new console shows up on the dashboard: \"Add this console\" saves it to settings.json."])

STEPS = [SettingsFile(), Python(), BridgeService(), VirtualCable(), InternetSharing(), Hotspot(), Ports(), MacFirewall(),
         FileZilla(), ConsoleChecklist()]
