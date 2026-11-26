"""
nxspoof.app.keeper - a run's root part: privilege separation (D24, docs/how-its-built.html).

A real run starts as root (sudo in Terminal, or ▶ Start's password window). Before anything else it splits
(macos/privsep.py): this process stays root as the keeper, and the run is a brand-new process started as you (exec, never
a fork that carries on: macOS's frameworks crash in those from macOS 26.1, D33). The run
reads the consoles' packets (DNS, the web test), so it's never trusted with root: the keeper answers only these
requests, and checks every argument:
  set_sharing(on)                              Internet Sharing on / off
  firewall_apply(iface, ip, alt_port, on)      NXSPOOF's own rules for that hotspot (built here, never sent by the run)
  firewall_counters(), firewall_flush(), firewall_leftovers()
  plug_cable(), unplug_cable()                 the virtual cable (in at the start, out at the end when the run put it in)
  still_here()                                 the run's heartbeat: once it has begun, a run silent for FROZEN_AFTER seconds is
                                               frozen; the keeper ends it (SIGKILL) and cleans up
  open_socket(kind, ip, port)                  the DNS / web server socket, handed to the run (a port under 1024 needs root)
  log_file(path)                               where the run's log is (for the keeper's own line after a crash)
The run reaches it through KeeperSystem: a System whose root methods become these requests.
The keeper doesn't stop on Ctrl+C, a closed Terminal window or SIGTERM: it passes them on to the run (sudo may deliver
them to the keeper only), which stops, cleans up and ends; then the keeper ends too, with the run's exit code. If the run ended without cleaning up (it crashed, or was stopped by force),
the keeper does it: NXSPOOF's firewall rules out, Internet Sharing off and the virtual cable out (unless keep_sharing), and
a NOTE in the run's log (fact run.keeper_cleanup). --no-keeper keeps the whole run root in one process, as before.
"""

import collections
import os
import re
import select
import socket
import threading
import time

from nxspoof.app.system import System
from nxspoof.core.events import Event
from nxspoof.core.fmt import error_text, error_trace
from nxspoof.macos import cable, privsep, user
from nxspoof.macos.firewall import FirewallError
from nxspoof.macos.sharing import SharingError
from nxspoof.output.logfile import append_as_root

WATCH_EVERY = 2                              # seconds between two looks at the keeper (from the run)
BEAT_EVERY = 10                              # seconds between two heartbeats (KeeperSystem.still_here paces them; the run just asks)
FROZEN_AFTER = 30                            # seconds of silence after which a run that has begun beating is frozen
IFACE_RX = re.compile(r"^[a-z]{2,8}[0-9]{1,3}$")
ERRORS = {"FirewallError": FirewallError, "SharingError": SharingError, "OSError": OSError, "RuntimeError": RuntimeError,
          "ValueError": ValueError}

Split = collections.namedtuple("Split", "role system code")       # role "run" (system: the one to use) or "keeper" (code: exit code)

# Split a real root run (macos/privsep.py): the run started as you, a brand-new process (run: (command(spec), fds, values,
# handover) to start it with; its own process makes its KeeperSystem with run_system), while this one stays root and serves
# until the run has ended -> Split("keeper", None, its exit code). Not root, nobody to become, nothing to start the run with
# (the fake Mac) or wanted=False (--no-keeper): Split("run", system, None), one process as before. in_keeper(): called once
# the run has started (the keeper lets go of what only the run should hold: the start's answer).
def split(system, keep_sharing, wanted=True, in_keeper=None, run=None):
    if not wanted or run is None or not privsep.can_split():
        return Split("run", system, None)
    uid = user.sudo_user()[1]
    command, fds, values, handover = run
    channel, pid = privsep.spawn_run(command, fds, dict(values, keeper=os.getpid()), handover)
    if in_keeper:
        in_keeper()
    privsep.forward_stop_signals(pid)
    return Split("keeper", None, Keeper(system, channel, pid, uid, keep_sharing).serve())

# In the run's own process: its System, talking to the keeper over the handed side of the pair (spec: what take_over read).
def run_system(spec):
    return KeeperSystem(privsep.channel_from(spec["channel"]), spec["keeper"])

# True for a dotted IPv4 address ("192.168.2.1").
def is_ipv4(text):
    try:
        return isinstance(text, str) and text.count(".") == 3 and bool(socket.inet_aton(text))
    except OSError:
        return False

# True for a whole number in [low, high] (not a bool).
def is_int(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high

CHECKS = {                                   # call -> is this argument list acceptable?
    "set_sharing": lambda a: len(a) == 1 and isinstance(a[0], bool),
    "firewall_apply": lambda a: (len(a) == 4 and isinstance(a[0], str) and bool(IFACE_RX.match(a[0])) and is_ipv4(a[1])
                                 and (a[2] is None or is_int(a[2], 1024, 65535)) and isinstance(a[3], bool)),
    "firewall_counters": lambda a: a == [],
    "firewall_flush": lambda a: a == [],
    "firewall_leftovers": lambda a: a == [],
    "plug_cable": lambda a: a == [],
    "unplug_cable": lambda a: a == [],
    "still_here": lambda a: a == [],
    "open_socket": lambda a: len(a) == 3 and a[0] in ("udp", "tcp") and is_ipv4(a[1]) and is_int(a[2], 1, 65535),
    "log_file": lambda a: len(a) == 1 and isinstance(a[0], str) and a[0].endswith(".log"),
}

# The root process of a split run: answers the run's requests, then cleans up after it if it had to.
class Keeper(object):
    # system: the real System (root); channel: its side of the pair; child: the run's pid; uid: you (the log's owner);
    # keep_sharing: leave Internet Sharing on even after a crash (the setting at the start).
    def __init__(self, system, channel, child, uid, keep_sharing=False):
        self.system, self.channel, self.child, self.uid, self.keep_sharing = system, channel, child, uid, keep_sharing
        self.firewall_loaded, self.sharing_on, self.cable_plugged, self.log_path = False, False, False, None
        self.beating, self.frozen = False, False

    # Answer requests until the run closes its side (or freezes), clean up if it had to, then wait for it; returns its exit code.
    def serve(self):
        while True:
            if self.beating and not select.select([self.channel], [], [], FROZEN_AFTER)[0]:
                self._end_frozen()
                break
            try:
                request, fds = privsep.receive(self.channel)
            except (OSError, ValueError):
                break
            for fd in fds:                                             # the run never sends descriptors
                socket.socket(fileno=fd).close()
            if request is None:
                break
            answer, sock = self.handle(request)
            try:
                privsep.send(self.channel, answer, [sock.fileno()] if sock else [])
            except OSError:
                break
            finally:
                if sock:
                    sock.close()
        self.channel.close()
        code = privsep.wait(self.child)                                # how it ended first: the note says if it crashed
        self.clean_up(code)
        return code

    # One request -> (answer, socket to hand over or None). Anything not in CHECKS, or with wrong arguments, is refused.
    def handle(self, request):
        call = request.get("call") if isinstance(request, dict) else None
        args = request.get("args") if isinstance(request, dict) else None
        if call not in CHECKS or not isinstance(args, list) or not CHECKS[call](args):
            return {"error": {"type": "ValueError", "message": "the keeper refuses %s(%s)" % (call, args)}}, None
        try:
            if call == "log_file":
                self.log_path = args[0]
                return {"result": None}, None
            if call == "still_here":
                self.beating = True
                return {"result": None}, None
            if call == "open_socket":
                return {"result": None}, self.system.open_socket(*args)
            result = getattr(self.system, call)(*args)
        except Exception as e:                                         # said to the run; the keeper itself goes on
            known = type(e).__name__ in ERRORS                         # an unknown kind travels in the message, with its traceback
            return {"error": {"type": type(e).__name__ if known else "RuntimeError", "message": str(e) if known else error_text(e),
                              "errno": getattr(e, "errno", None), "trace": error_trace(e)}}, None
        self._track(call, args, result)
        return {"result": [result[0], list(result[1])] if call == "plug_cable" else result}, None

    # Remember what would need undoing if the run ended without cleaning up (result: what the call answered).
    def _track(self, call, args, result=None):
        if call == "plug_cable":                                       # in just now (not "already"): out again at the end
            self.cable_plugged = self.cable_plugged or result[0] == "plugged"
            return
        flags = {"firewall_apply": ("firewall_loaded", True), "firewall_flush": ("firewall_loaded", False),
                 "set_sharing": ("sharing_on", bool(args and args[0])), "unplug_cable": ("cable_plugged", False)}
        if call in flags:
            setattr(self, *flags[call])

    # The run stopped answering (no heartbeat for FROZEN_AFTER seconds): end it for good, so the clean up can run.
    def _end_frozen(self):
        self.frozen = True
        privsep.kill(self.child)

    # The run has ended (code: its exit code, 128 + a signal when one ended it): undo what it left (firewall rules, Internet
    # Sharing, the virtual cable), and say so in its log; a run that died from a signal is said even with nothing to undo (which
    # signal, and where macOS keeps its crash report). Returns what was done.
    def clean_up(self, code=None):
        left = not self.keep_sharing
        parts = [(self.firewall_loaded, "firewall rules removed", "remove the firewall rules", lambda: self.system.firewall_flush()),
                 (self.sharing_on and left, "Internet Sharing turned off", "turn Internet Sharing off", lambda: self.system.set_sharing(False)),
                 (self.cable_plugged and left, "virtual cable out", "take the virtual cable out", lambda: self.system.unplug_cable())]
        done, complete = [], True
        for needed, did, doing, do in parts:
            if not needed:
                continue
            try:
                do()
                done.append(did)
            except Exception as e:
                done.append("couldn't %s (%s)" % (doing, error_text(e)))
                complete = False
        died = None if self.frozen else privsep.signal_name(code)
        if (done or self.frozen or died) and self.log_path:
            why, said = self._end_words(died)
            did = "; ".join(done) or "nothing was left to undo"
            append_as_root(self.log_path, self.uid, Event(
                "run.keeper_cleanup", "NOTE", "the run %s - the keeper did: %s" % (why, did),
                casual="🧹 NXSPOOF %s; its root part cleaned up: %s." % (said, did),
                level="warn" if complete and not died else "problem",
                data={"done": done, "complete": complete, "frozen": self.frozen, "signal": died}))
        return done

    # How the run ended, in words for the note: (technical, casual) - frozen, died from a signal (died), or just gone.
    def _end_words(self, died):
        if self.frozen:
            return "froze (no heartbeat for %d s), so the keeper ended it" % FROZEN_AFTER, "froze"
        if died:
            return ("died from %s (a crash: macOS keeps its crash report in ~/Library/Logs/DiagnosticReports, Python-....ips)" % died,
                    "crashed (%s)" % died)
        return "ended without cleaning up (it crashed or was stopped by force)", "stopped without cleaning up"

# The run's System in a split run: root methods are requests to the keeper; everything else is done here, as you.
class KeeperSystem(System):
    # channel: the run's side of the pair; keeper_pid: the keeper (to notice it going away).
    def __init__(self, channel, keeper_pid):
        System.__init__(self)
        self.channel, self.keeper_pid, self.lock = channel, keeper_pid, threading.Lock()
        self.last_beat = 0.0                                           # when the last heartbeat went out

    # One request to the keeper -> (its result, descriptors); its error raised here as the same kind of exception.
    def _ask(self, call, *args):
        with self.lock:
            privsep.send(self.channel, {"call": call, "args": list(args)})
            answer, fds = privsep.receive(self.channel)
        if answer is None:
            raise OSError("NXSPOOF's root part (the keeper, process %d) is gone" % self.keeper_pid)
        error = answer.get("error")
        if error:
            cls = ERRORS.get(error.get("type"), RuntimeError)
            e = cls(error["errno"], error["message"]) if cls is OSError and error.get("errno") else cls(error.get("message"))
            e.remote_trace = error.get("trace")                      # the keeper's traceback, for the run's log (core/fmt.py)
            raise e
        return answer.get("result"), fds

    # Root is there (in the keeper).
    def is_root(self):
        return True

    # For the run's log.
    def privileges(self):
        who = user.sudo_user()
        return "the run as %s; root only in the keeper (process %d): firewall, Internet Sharing, the virtual cable, the servers' ports" % (
            who[0] if who else "you", self.keeper_pid)

    # Tell the keeper where the run's log is; watch it, and call on_lost(why) once if it goes away.
    def root_watch(self, log_path, on_lost):
        if log_path:
            self._ask("log_file", log_path)

        # Every WATCH_EVERY seconds: is the keeper still there?
        def watch():
            while privsep.alive(self.keeper_pid):
                time.sleep(WATCH_EVERY)
            on_lost("NXSPOOF's root part (the keeper, process %d) went away" % self.keeper_pid)
        threading.Thread(target=watch, daemon=True).start()

    # Internet Sharing on / off (the keeper).
    def set_sharing(self, enabled):
        return self._ask("set_sharing", bool(enabled))[0]

    # NXSPOOF's rules for this hotspot (built and loaded by the keeper).
    def firewall_apply(self, iface, ip, alt_dns_port, enabled):
        return self._ask("firewall_apply", iface, ip, alt_dns_port, bool(enabled))[0]

    # [(rule, packets)] (the keeper).
    def firewall_counters(self):
        return [tuple(item) for item in self._ask("firewall_counters")[0]]

    # Remove NXSPOOF's rules (the keeper).
    def firewall_flush(self):
        self._ask("firewall_flush")

    # True if NXSPOOF rules are still loaded (the keeper).
    def firewall_leftovers(self):
        return self._ask("firewall_leftovers")[0]

    # Plug the virtual cable in (the keeper) -> ("already" | "plugged", cable.Status).
    def plug_cable(self):
        found, status = self._ask("plug_cable")[0]
        return found, cable.Status(*status)

    # Take the virtual cable out (the keeper).
    def unplug_cable(self):
        self._ask("unplug_cable")

    # The run's heartbeat (the keeper ends a run that stops beating): the run asks as often as it likes, one goes out
    # every BEAT_EVERY seconds (the first at once).
    def still_here(self):
        if time.time() - self.last_beat < BEAT_EVERY:
            return
        self.last_beat = time.time()
        self._ask("still_here")

    # A server socket opened by the keeper and handed over.
    def open_socket(self, kind, ip, port):
        _, fds = self._ask("open_socket", kind, ip, int(port))
        return socket.socket(fileno=fds[0])
