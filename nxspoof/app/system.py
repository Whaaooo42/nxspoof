"""
nxspoof.app.system - the one door to the Mac: every macOS action a run needs, in one object.

The run never runs anything on the Mac itself; it asks its System (it may import macos/'s pure parts: a parser, an
error type, a name helper, which never touch the Mac). The real System delegates to macos/
(sharing, firewall, interfaces, kernel log, lease file, user). The simulator (tools/simulate.py)
passes a fake one, so the real start sequence and the real shutdown run without admin rights,
without a hotspot and without touching this Mac's network.

CONTRACT below is the platform contract (D23, docs/how-its-built.html, the platform contract): everything NXSPOOF needs from the machine it
runs on, by group. Another platform (Linux, say) would be another System answering the same methods; a guardrail checks
that this class, CONTRACT and the doc list the same methods.
"""

from nxspoof.core.places import Places
from nxspoof.macos import admin, cable, dhcp, dialog, firewall, leases, machine, netif, process, sharing, user, wifi
from nxspoof.macos import hotspot as macos_hotspot
from nxspoof.net import sockets

CONTRACT = (                                 # (group, its methods): what a platform must provide
    ("who and where", ("is_root", "privileges", "root_watch", "places", "machine_facts", "personal_words", "give_to_user", "give_link_to_user",
                       "mac_problems", "mac_fix")),
    ("a run's life", ("in_background", "answer", "wait_answer", "stop_run", "restart_from", "still_here", "keep_awake", "let_sleep")),
    ("the hotspot", ("hotspot_settings", "sharing_running", "set_sharing", "find_sharing_interface", "interfaces", "bind_address",
                     "open_socket", "plug_cable", "unplug_cable")),
    ("the firewall", ("firewall_apply", "firewall_counters", "firewall_flush", "firewall_leftovers")),
    ("who is on the hotspot", ("wifi_monitor", "dhcp_monitor", "mac_for_ip", "connected_to")),
    ("asking for the password", ("start_with_password", "password_outcome")),
    ("the desktop", ("open_sharing_settings", "open_url", "copy_text", "filezilla_installed", "open_filezilla", "ask_user")),
)

# The real Mac.
class System(object):
    # pfctl: the pf runner; leases_path: macOS's DHCP lease file.
    def __init__(self, pfctl=None, leases_path=leases.LEASES_FILE):
        self.pfctl = pfctl or firewall.pfctl
        self.leases_path = leases_path
        self.pf_token = None                     # pf's token from this run's `pfctl -E`, given back with the rules (firewall_flush)

    # True when running as root (through sudo).
    def is_root(self):
        return user.is_root()

    # What kind of Mac this is (macos/machine.py: macOS version and build, model, chip, Python, language, managed or not),
    # for the run's "this Mac" line and the problem report. Nothing personal.
    def machine_facts(self):
        return machine.facts()

    # What's personal on this Mac ({word: stand-in}: its names, your account and full name, your home folder), for the problem
    # report to replace (features/report.py).
    def personal_words(self):
        return machine.personal_words()

    # What keeps this Mac from being ready for a run (setup's checks, read only; app/starter.py): [{title, casual, tech}].
    # program: the nx-spoof folder; places: where settings.json is (core/places.py).
    def mac_problems(self, program, places):
        from nxspoof.app.starter import NOT_READY, mac_checks
        return [r for r in mac_checks(program, self, places) if r["state"] in NOT_READY]

    # Setup's fixes for these steps (names), as `setup --yes --only <names>` does: with its own log, on the Terminal (start
    # --fix, D30). Returns setup's exit code.
    def mac_fix(self, places, names):
        from nxspoof.app.starter import fix_mac
        return fix_mac(self, places, names)

    # ---- a run's life (macos/process.py; whether one is running is the lock's answer: output/runfile.py)

    # Fork into a waiter and a worker on its own, its output into `output` -> (role, fd, pid) (macos/process.py).
    def in_background(self, output):
        return process.background(output)

    # The worker's one answer to the waiter (macos/process.py).
    def answer(self, fd, obj):
        return process.answer(fd, obj)

    # The waiter: the worker's answer (dict), None (it ended without one) or "timeout" (macos/process.py).
    def wait_answer(self, fd, timeout):
        return process.wait_answer(fd, timeout)

    # Ask the run (its process id, from run.json) to stop -> True if asked, False if it's gone (PermissionError: not yours).
    def stop_run(self, pid):
        return process.stop(pid)

    # This command again from the program copy in `folder` (macos/process.py; never returns). argv: the command's words.
    def restart_from(self, folder, argv):
        return process.restart_from(folder, argv)

    # No idle sleep while process `pid` (the run) lasts: the hotspot sleeps with the Mac (macos/process.py) -> a handle for
    # let_sleep (raises OSError).
    def keep_awake(self, pid):
        return process.keep_awake(pid)

    # The Mac may sleep again (the run is stopping; keep_awake's handle).
    def let_sleep(self, handle):
        process.let_sleep(handle)

    # The run's heartbeat: nobody to tell in one process (a split run's KeeperSystem tells its keeper).
    def still_here(self):
        return None

    # Who this run is, for its log: here, one process (root throughout, or not root at all); a split run's KeeperSystem
    # (app/keeper.py) says the run is you and root stays in the keeper.
    def privileges(self):
        if user.is_root():
            return "one process, root throughout (--no-keeper, or nobody to become: not started through sudo)"
        return "one process, not root (a test, or started without admin rights)"

    # The run's log is log_path; on_lost(why) if the root part goes away. Nothing to watch in one process (a split run's
    # KeeperSystem watches its keeper).
    def root_watch(self, log_path, on_lost):
        return None

    # ---- Internet Sharing

    # True if the hotspot is up right now (a bridge1xx interface exists).
    def sharing_running(self):
        return netif.current_sharing_interface() is not None

    # Turn Internet Sharing on or off; returns what was written (raises SharingError or OSError).
    def set_sharing(self, enabled):
        return sharing.set_sharing(enabled)

    # The hotspot's name, channel and security now, and whether it has a password ({"name", "channel", "security",
    # "password"}; macos/hotspot.py, reading needs no root; {} parts when they can't be read).
    def hotspot_settings(self):
        return dict(macos_hotspot.read() or {}, password=macos_hotspot.password_set())

    # The hotspot interface and the Mac's address on it, as (iface, ip), or None if it isn't up.
    def find_sharing_interface(self):
        return netif.current_sharing_interface()

    # The hotspot-related network interfaces as macOS has them now, one line each ("bridge100: UP, active, 192.168.2.1, members
    # en0 feth1"): what the log shows when the hotspot doesn't come up (on another macOS its interface may be named otherwise).
    def interfaces(self):
        return netif.current_interfaces()

    # A server socket of kind "udp" (DNS) or "tcp" (web) bound to ip:port (net/sockets.py; raises OSError). A split run's
    # KeeperSystem asks the keeper, since a port under 1024 on one address needs root.
    def open_socket(self, kind, ip, port):
        return sockets.open_socket(kind, ip, port)

    # The address servers bind to for the hotspot address (the same on a real Mac).
    def bind_address(self, ip):
        return ip

    # ---- firewall

    # Build NXSPOOF's rules for the hotspot (interface iface, the Mac's address ip, plan B's DNS port or None) and load them;
    # returns firewall.status_from(...) (raises FirewallError). The rules are made here, not by the caller: a split run's
    # keeper only loads NXSPOOF's own rules. pf's token is kept here (never in a log), for firewall_flush to give back.
    def firewall_apply(self, iface, ip, alt_dns_port, enabled):
        status = firewall.apply(iface, firewall.build_rules(iface, ip, alt_dns_port, enabled), enabled, run=self.pfctl)
        self._hold_token(status.pop("token", None))
        return status

    # Keep pf's newest token: one held at a time (loaded again, the older one is given back).
    def _hold_token(self, token):
        if not token:
            return
        if self.pf_token and token != self.pf_token:
            firewall.release(self.pf_token, run=self.pfctl)
        self.pf_token = token

    # [(rule, packets)] for NXSPOOF's rules.
    def firewall_counters(self):
        return firewall.counters(run=self.pfctl)

    # Remove NXSPOOF's rules, and give pf's token back (pf goes off again unless something else holds one).
    def firewall_flush(self):
        firewall.flush(run=self.pfctl)
        if self.pf_token:
            token, self.pf_token = self.pf_token, None
            firewall.release(token, run=self.pfctl)

    # True if NXSPOOF rules are still loaded.
    def firewall_leftovers(self):
        return firewall.leftovers(run=self.pfctl)

    # ---- who is on the hotspot

    # A started-later monitor feeding the Wi-Fi tracker from the kernel log (start() raises OSError).
    def wifi_monitor(self, tracker):
        return wifi.WifiMonitor(tracker)

    # A started-later monitor announcing new DHCP leases through on_lease(ip, mac).
    def dhcp_monitor(self, on_lease):
        return dhcp.DhcpMonitor(on_lease, path=self.leases_path)

    # The MAC that has an IP in the lease file, or None.
    def mac_for_ip(self, ip):
        for lease in leases.read_leases(self.leases_path):
            if lease.ip == ip:
                return lease.mac
        return None

    # ---- acting as you

    # Hand a file or folder back to the real user.
    def give_to_user(self, path):
        return user.give_to_user(path)

    # Hand a symbolic link back to the real user.
    def give_link_to_user(self, path):
        return user.give_link_to_user(path)

    # Open System Settings > General > Sharing.
    def open_sharing_settings(self):
        return user.open_sharing_settings()

    # Open a URL in your browser.
    def open_url(self, url):
        return user.open_url(url)

    # Put text on your clipboard (macos/user.py) -> True if it worked.
    def copy_text(self, text):
        return user.copy_text(text)

    # A small macOS dialog with buttons, as you (macos/dialog.py) -> the button pressed, or None.
    def ask_user(self, text, buttons, default):
        return dialog.ask(text, buttons, default)

    # ▶ Start and This Mac's tasks: show macOS's password window, then run cmd as root in folder cwd, on your behalf, its
    # output appended to `output`, waited for. Returns at once with the window's process (see password_outcome).
    def start_with_password(self, cwd, cmd, output, prompt):
        return admin.ask(admin.foreground_line(cwd, cmd, user.me(), output), prompt)

    # Where NXSPOOF's files are for a program in folder `program` (core/places.py): on a Mac, settings, logs and runtime
    # files all in the data folder, ~/Library/Application Support/NXSPOOF.
    def places(self, program):
        return Places.together(program, user.data_folder())

    # Wait for the password window -> ("started" | "cancelled" | "failed", what to say).
    def password_outcome(self, handle):
        return admin.finish(handle)

    # Every run plugs the virtual cable in itself (root) -> ("already" | "plugged", cable.Status).
    def plug_cable(self):
        found = cable.status()
        if found.ok:
            return "already", found
        return "plugged", cable.up()

    # The run plugged the virtual cable in: out again at its Stop, the Mac as it was (root).
    def unplug_cable(self):
        cable.down()

    # True if a program on this Mac (e.g. FileZilla) is connected to a console's ip:port right now.
    def connected_to(self, ip, port):
        return netif.connected_to(ip, port)

    # True if FileZilla is installed.
    def filezilla_installed(self):
        return user.filezilla_installed()

    # Open FileZilla on an ftp:// URL.
    def open_filezilla(self, url):
        return user.open_filezilla(url)
