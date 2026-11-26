"""
nxspoof.macos.privsep - splitting a root run into two processes (privilege separation, D24).

The keeper stays root; the run is a brand-new process (exec, never a fork that carries on: macOS's frameworks crash in
those from macOS 26.1) started as you: your groups, then your group id, then your user id, set by the launch itself before
the exec (in that order, so it can never become root again), and HOME set to your home. The two talk over a pair of connected
Unix sockets: one length-prefixed JSON message at a time, request then answer, with file descriptors passed alongside
(the sockets the keeper opens for the run). Never logs: returns values or raises.

  can_split()                  True when this process is really root on behalf of someone (sudo's variables)
  spawn_run(command, fds, values, handover)   the run started as you, handed the run's side of a socket pair -> (the
                               keeper's side, the run's pid)
  send(channel, obj, fds=())   one message, with descriptors (raises OSError when the other side is gone)
  receive(channel)             (obj, fds), or (None, []) when the other side has closed
  forward_stop_signals(pid)    the keeper doesn't stop on Ctrl+C, a closed window or SIGTERM: it passes them on to the run
                               (sudo may hand them to its own child only: the keeper), which stops, then the keeper follows
  alive(pid)                   is the keeper still this process's parent (the run watching it)
  kill(pid)                    end a frozen run for good (SIGKILL: it can't refuse), so the keeper can clean up
  wait(pid)                    the run's exit code, once it has ended
"""

import json
import os
import pwd
import signal
import socket
import struct
import subprocess

from nxspoof.macos import process, user

HEADER = struct.Struct(">I")                 # each message: its length (4 bytes), then its JSON
MAX_MESSAGE = 1 << 20                        # a message bigger than this is refused (the keeper's answers are small)
MAX_FDS = 4

# True when this process is really root and knows who it acts for (sudo, or ▶ Start's password window, sets SUDO_*).
def can_split():
    return os.geteuid() == 0 and user.sudo_user() is not None

# Start the run as you, a brand-new process (macos/process.py started): your groups (as many as macOS takes), your group id
# and your user id set by the launch before the exec, HOME your home; handed the run's side of a new socket pair ("channel"),
# the descriptors `fds`, the plain `values` and the hand-over. This process stays root (the keeper). -> (the keeper's side,
# the run's pid).
def spawn_run(command, fds, values, handover, start=process.started):
    name, uid, gid = user.sudo_user()
    keeper_side, run_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    groups = os.getgrouplist(name, gid)[:os.sysconf("SC_NGROUPS_MAX")]
    try:
        pid = start(command, dict(fds, channel=run_side.fileno()), values, handover, user=uid, group=gid, extra_groups=groups,
                    env=dict(os.environ, HOME=pwd.getpwuid(uid).pw_dir), stdin=subprocess.DEVNULL)
    finally:
        run_side.close()
    return keeper_side, pid

# The run's side of the pair, from its handed descriptor -> a socket.
def channel_from(fd):
    return socket.socket(fileno=fd)

# Send one message (a JSON-able object), with file descriptors alongside; raises OSError when the other side is gone.
def send(channel, obj, fds=()):
    body = json.dumps(obj, default=str).encode("utf-8")
    data = HEADER.pack(len(body)) + body
    sent = socket.send_fds(channel, [data], list(fds)) if fds else channel.send(data)
    if sent < len(data):
        channel.sendall(data[sent:])

# Receive one message: (obj, fds), or (None, []) when the other side has closed (ValueError for a broken message).
def receive(channel):
    data, fds, _, _ = socket.recv_fds(channel, 65536, MAX_FDS)
    while data and len(data) < HEADER.size:
        more = channel.recv(HEADER.size - len(data))
        if not more:
            break
        data += more
    if len(data) < HEADER.size:
        return None, []
    size = HEADER.unpack(data[:HEADER.size])[0]
    if size > MAX_MESSAGE:
        raise ValueError("a message of %d bytes is too big" % size)
    while len(data) < HEADER.size + size:
        more = channel.recv(HEADER.size + size - len(data))
        if not more:
            return None, []
        data += more
    return json.loads(data[HEADER.size:HEADER.size + size].decode("utf-8")), list(fds)

# The keeper passes Ctrl+C, a closed Terminal window and SIGTERM on to the run (pid) instead of stopping: the run stops,
# cleans up and ends, then the keeper. (The run may get them directly too: its first stop request wins.)
def forward_stop_signals(pid):
    # Pass one signal on to the run (it may have ended already).
    def forward(sig, frame):
        try:
            os.kill(pid, sig)
        except OSError:
            pass
    for sig in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, forward)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, (signal.SIGINT, signal.SIGHUP, signal.SIGTERM))

# True while the keeper (pid) is still this process's parent (it's gone when the run has been handed to launchd).
def alive(pid):
    return os.getppid() == pid

# End a frozen run (pid) for good: SIGKILL, which it can't catch (it may have ended already).
def kill(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass

# The signal that ended a run, from wait()'s exit code ("SIGSEGV" for 139), or None when the run exited by itself.
def signal_name(code):
    if code is None or code <= 128:
        return None
    try:
        return signal.Signals(code - 128).name
    except ValueError:
        return "signal %d" % (code - 128)

# Wait for the run (pid) to end; its exit code (128 + the signal's number when a signal ended it).
def wait(pid):
    return process.reap(pid)
