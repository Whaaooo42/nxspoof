"""
tests.test_keeper - privilege separation (D24): the messages between the keeper and the run, what the keeper accepts,
the run's KeeperSystem talking to a real Keeper over a socket pair (no fork, no root needed), and the keeper's clean up
after a run that ended without its own.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from nxspoof.app import keeper
from nxspoof.app.keeper import CHECKS, Keeper, KeeperSystem
from nxspoof.app.system import System
from nxspoof.core.events import Event
from nxspoof.features import runlog
from nxspoof.macos import cable, privsep
from nxspoof.macos.firewall import FirewallError
from nxspoof.output.logfile import append_as_root, records_path

# The root side's System, faked: records every call; firewall_apply can be told to fail.
class RootFake(object):
    # Nothing called yet.
    def __init__(self):
        self.calls, self.fail = [], None

    # Record and "work".
    def set_sharing(self, enabled):
        self.calls.append(("sharing", enabled))
        return "enabled=%s [test]" % enabled

    # Record; fails when the test says so.
    def firewall_apply(self, iface, ip, alt, enabled):
        self.calls.append(("apply", iface, ip, alt, enabled))
        if self.fail:
            raise self.fail
        return {"loaded": True, "enabled": True, "hooked": True, "ok": True}

    # Two rules with their packets.
    def firewall_counters(self):
        return [("block drop in quick on bridge100 all", 3), ("pass in quick on bridge100 inet proto udp", 7)]

    # Record the flush.
    def firewall_flush(self):
        self.calls.append(("flush",))

    # Nothing left.
    def firewall_leftovers(self):
        return False

    # Already plugged in (or, when the test says so, plugged in just now).
    def plug_cable(self):
        self.calls.append(("cable in",))
        return getattr(self, "cable", "already"), cable.Status(True, True, "172.16.0.1")

    # Record it.
    def unplug_cable(self):
        self.calls.append(("cable out",))

    # A real socket on 127.0.0.1 (the port given by the test).
    def open_socket(self, kind, ip, port):
        return System.open_socket(self, kind, ip, port)

# A free TCP port on 127.0.0.1.
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

# The messages between the two processes, with descriptors alongside.
class Messages(unittest.TestCase):
    # A message and a descriptor go across; a closed side reads as (None, []).
    def test_send_receive(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        r, w = os.pipe()
        try:
            privsep.send(a, {"call": "x", "args": [1, "é"]}, [w])
            obj, fds = privsep.receive(b)
            self.assertEqual(obj, {"call": "x", "args": [1, "é"]})
            self.assertEqual(len(fds), 1)
            os.write(fds[0], b"ok")
            self.assertEqual(os.read(r, 2), b"ok")
            os.close(fds[0])
            a.close()
            self.assertEqual(privsep.receive(b), (None, []))
        finally:
            for fd in (r, w):
                os.close(fd)
            b.close()

    # The keeper passes a stop signal on to the run instead of stopping (sudo may hand it to the keeper only).
    def test_forward_stop_signals(self):
        import signal
        import sys
        child = subprocess.Popen([sys.executable, "-c", "import signal, sys, time\n"
                                  "signal.signal(signal.SIGTERM, lambda s, f: (print('run got SIGTERM', flush=True), sys.exit(0)))\n"
                                  "print('ready', flush=True)\ntime.sleep(10)"], stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM)}
        try:
            privsep.forward_stop_signals(child.pid)
            os.kill(os.getpid(), signal.SIGTERM)
            self.assertEqual(child.communicate(timeout=5)[0].strip(), "run got SIGTERM")
        finally:
            for sig, handler in saved.items():
                signal.signal(sig, handler)

    # Without real root (the tests), a run doesn't split: one process, as before.
    def test_no_split_without_root(self):
        system = System()
        self.assertEqual(keeper.split(system, False), ("run", system, None))
        self.assertIn("one process", system.privileges())

# What the keeper accepts: only its list, and only well-formed arguments.
class Checks(unittest.TestCase):
    # Good requests pass; anything odd is refused before reaching the real System.
    def test_arguments(self):
        good = [("set_sharing", [True]), ("firewall_apply", ["bridge100", "192.168.2.1", None, True]), ("firewall_apply", ["bridge100", "192.168.2.1", 5354, False]),
                ("open_socket", ["udp", "192.168.2.1", 53]), ("plug_cable", []), ("unplug_cable", []), ("still_here", []), ("log_file", ["/tmp/x.log"])]
        bad = [("set_sharing", [1]), ("set_sharing", ["yes"]), ("firewall_apply", ["bridge100; rm -rf /", "192.168.2.1", None, True]),
               ("firewall_apply", ["bridge100", "192.168.2", None, True]), ("firewall_apply", ["bridge100", "192.168.2.1", 53, True]),
               ("open_socket", ["raw", "192.168.2.1", 53]), ("open_socket", ["udp", "192.168.2.1", 0]), ("open_socket", ["udp", "192.168.2.1", True]),
               ("firewall_flush", ["now"]), ("unplug_cable", ["bridge0"]), ("still_here", [30]), ("log_file", ["/etc/sudoers"])]
        self.assertNotIn("set_hotspot", CHECKS)                              # the hotspot is macOS's: root never touches it (D25)
        for call, args in good:
            self.assertTrue(CHECKS[call](args), (call, args))
        for call, args in bad:
            self.assertFalse(CHECKS[call](args), (call, args))
        k = Keeper(RootFake(), None, None, os.getuid())
        for request in ({"call": "system", "args": ["rm -rf /"]}, {"call": "set_sharing", "args": "True"}, "nonsense", {"call": "set_sharing", "args": [1]}):
            answer, sock = k.handle(request)
            self.assertEqual((answer["error"]["type"], sock), ("ValueError", None), request)
        self.assertEqual(k.system.calls, [])

# The run's KeeperSystem against a real Keeper, over a socket pair (the keeper in a thread; a finished process stands in
# for the run it waits for).
class AcrossThePair(unittest.TestCase):
    # The pair, the keeper serving in a thread, a temporary log (and twin) for its note.
    def setUp(self):
        self.root = RootFake()
        run_side, keeper_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.child = subprocess.Popen(["/usr/bin/true"])
        self.k = Keeper(self.root, keeper_side, self.child.pid, os.getuid(), keep_sharing=False)
        self.code = []
        self.thread = threading.Thread(target=lambda: self.code.append(self.k.serve()), daemon=True)
        self.thread.start()
        self.system = KeeperSystem(run_side, os.getppid())
        self.dir = tempfile.mkdtemp(prefix="nxspoof-keeper-")
        self.log = os.path.join(self.dir, "2026-11-16_22-00-00.log")
        open(self.log, "w").close()
        open(records_path(self.log), "w").close()

    # Remove the folder; the stand-in run was already waited for by the keeper (Popen is told so).
    def tearDown(self):
        shutil.rmtree(self.dir)
        self.end_run()
        self.child.wait(5)

    # Close the run's side and wait for the keeper to finish.
    def end_run(self):
        self.system.channel.close()
        self.thread.join(5)

    # Every root call goes through, with its result as the run expects it (tuples, a cable Status, a working socket).
    def test_calls(self):
        self.assertEqual(self.system.set_sharing(True), "enabled=True [test]")
        self.assertTrue(self.system.firewall_apply("bridge100", "192.168.2.1", None, True)["ok"])
        self.assertEqual(self.system.firewall_counters()[1], ("pass in quick on bridge100 inet proto udp", 7))
        self.assertEqual(self.system.plug_cable(), ("already", cable.Status(True, True, "172.16.0.1")))
        port = free_port()
        sock = self.system.open_socket("tcp", "127.0.0.1", port)
        self.assertEqual(sock.getsockname(), ("127.0.0.1", port))
        sock.close()
        self.assertTrue(self.system.is_root())
        self.system.firewall_flush()
        self.system.set_sharing(False)
        self.end_run()
        self.assertEqual(self.code, [0])
        self.assertEqual(self.root.calls[-2:], [("flush",), ("sharing", False)])

    # An unexpected kind (a different macOS, say) comes back as a RuntimeError that names it, with the keeper's traceback.
    def test_unknown_error_keeps_its_kind(self):
        self.root.fail = KeyError("Wi-Fi")
        with self.assertRaises(RuntimeError) as caught:
            self.system.firewall_apply("bridge100", "192.168.2.1", None, True)
        self.assertEqual(str(caught.exception), "KeyError: 'Wi-Fi'")
        self.assertEqual(caught.exception.remote_trace[-1], "KeyError: 'Wi-Fi'")

    # The keeper's errors come back as the same kind (FirewallError; OSError with its errno, e.g. a port in use).
    def test_errors(self):
        self.root.fail = FirewallError("pfctl said no")
        with self.assertRaises(FirewallError):
            self.system.firewall_apply("bridge100", "192.168.2.1", None, True)
        taken = socket.socket()
        taken.bind(("127.0.0.1", 0))
        try:
            with self.assertRaises(OSError) as caught:
                self.system.open_socket("tcp", "127.0.0.1", taken.getsockname()[1])
            self.assertTrue(caught.exception.errno)
        finally:
            taken.close()
        self.end_run()

    # The run ends without its clean up (a crash): the keeper removes the rules, turns sharing off, and says so in the log.
    def test_clean_up_after_a_crash(self):
        self.system.root_watch(self.log, lambda why: None)
        self.system.firewall_apply("bridge100", "192.168.2.1", None, True)
        self.system.set_sharing(True)
        self.end_run()
        self.assertEqual(self.root.calls[-2:], [("flush",), ("sharing", False)])
        with open(self.log, encoding="utf-8") as f:
            self.assertIn("NOTE  the run ended without cleaning up (it crashed or was stopped by force) - the keeper did: "
                          "firewall rules removed; Internet Sharing turned off", f.read())
        with open(records_path(self.log), encoding="utf-8") as f:
            kinds = [json.loads(l)["kind"] for l in f]
        self.assertEqual(kinds, ["run.keeper_cleanup"])
        self.assertTrue(runlog.latest_run(self.dir)["clean"])

    # The cable the run put in comes out with the clean up (D30); one that was already in stays.
    def test_clean_up_takes_the_cable_out(self):
        self.system.root_watch(self.log, lambda why: None)
        self.root.cable = "plugged"
        self.system.plug_cable()
        self.system.set_sharing(True)
        self.end_run()
        self.assertEqual(self.root.calls[-2:], [("sharing", False), ("cable out",)])
        with open(self.log, encoding="utf-8") as f:
            self.assertIn("the keeper did: Internet Sharing turned off; virtual cable out", f.read())

    # The heartbeat: once the run beats, a run silent for FROZEN_AFTER seconds is ended (SIGKILL) and cleaned up after, said
    # in its log.
    def test_frozen_run(self):
        from nxspoof.app import keeper
        killed = []
        old_after, old_kill = keeper.FROZEN_AFTER, keeper.privsep.kill
        keeper.FROZEN_AFTER, keeper.privsep.kill = 0.3, killed.append
        try:
            self.system.root_watch(self.log, lambda why: None)
            self.system.set_sharing(True)
            self.system.still_here()
            self.thread.join(5)                  # silent from here on: the keeper gives up on the run
        finally:
            keeper.FROZEN_AFTER, keeper.privsep.kill = old_after, old_kill
        self.assertEqual((killed, self.root.calls[-1]), ([self.child.pid], ("sharing", False)))
        with open(self.log, encoding="utf-8") as f:
            self.assertIn("NOTE  the run froze (no heartbeat for 0 s), so the keeper ended it - the keeper did: Internet Sharing turned off", f.read())

    # A normal end (the run flushed and turned sharing off itself): nothing more to do, no note.
    def test_normal_end(self):
        self.system.root_watch(self.log, lambda why: None)
        self.system.firewall_apply("bridge100", "192.168.2.1", None, True)
        self.system.set_sharing(True)
        self.system.firewall_flush()
        self.system.set_sharing(False)
        self.end_run()
        self.assertEqual(os.path.getsize(self.log), 0)

    # A run that loses its keeper is told (root_watch's on_lost), once.
    def test_keeper_gone(self):
        lost = []
        gone = KeeperSystem(None, 999999)
        gone._ask = lambda *a: (None, [])
        gone.root_watch(None, lost.append)
        end = time.time() + 3
        while not lost and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(lost, ["NXSPOOF's root part (the keeper, process 999999) went away"])
        self.end_run()

# The keeper only appends to your own regular files: never through a link, never someone else's file.
class AppendAsRoot(unittest.TestCase):
    # A link and a wrong owner are refused; your own log and twin get the line.
    def test_only_your_own_files(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-append-")
        try:
            log = os.path.join(folder, "run.log")
            for path in (log, records_path(log)):
                open(path, "w").close()
            event = Event("run.keeper_cleanup", "NOTE", "cleaned", data={"complete": True})
            self.assertFalse(append_as_root(log, os.getuid() + 1, event))
            self.assertEqual(os.path.getsize(log), 0)
            link = os.path.join(folder, "link.log")
            os.symlink(log, link)
            os.symlink(records_path(log), records_path(link))
            self.assertFalse(append_as_root(link, os.getuid(), event))
            self.assertTrue(append_as_root(log, os.getuid(), event))
            with open(log, encoding="utf-8") as f:
                self.assertTrue(f.read().endswith("NOTE  cleaned\n"))
            pipe = os.path.join(folder, "pipe.log")                       # a pipe nobody reads: refused at once, root never waits
            os.mkfifo(pipe)
            open(records_path(pipe), "w").close()
            started = time.time()
            self.assertFalse(append_as_root(pipe, os.getuid(), event))
            self.assertLess(time.time() - started, 2)
        finally:
            shutil.rmtree(folder)

if __name__ == "__main__":
    unittest.main()
