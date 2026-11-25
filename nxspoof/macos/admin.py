"""
nxspoof.macos.admin - starting something as root with macOS's own password window (control center stage 2, D17).

The dashboard runs as you, without admin rights. Its ▶ Start and This Mac's tasks ask macOS to show its usual password
window (osascript's "do shell script ... with administrator privileges", with NXSPOOF's own sentence in it). Once you
type your password, macOS runs one shell line as root, and the window answers when it has finished (nx_spoof.py start
finishes once the run has its answer; the run itself goes on in the background, on its own: macos/process.py).

Two macOS rules shape that line (both found on the real Mac, 2026-11-16):
  - the line is run by Apple's authtrampoline, which macOS holds responsible for it: a program started that way is
    refused the Documents, Desktop, Downloads and iCloud Drive folders without any question. So that run reads nothing
    from the nx-spoof folder: it runs from NXSPOOF's data folder (output/datafolder.py), which macOS doesn't guard;
  - there is no terminal at all: `nohup` refuses to run ("can't detach from console"), and a backgrounded
    `cd ... && command` is killed when the line ends. So: cd first (its error comes back to the window's answer), then
    the command, waited for; a run leaves the line's session itself (setsid) before the line ends.
The run is told who you are the way sudo tells it (SUDO_USER, SUDO_UID, SUDO_GID), so everything it creates is
still handed back to you and apps open as you.
Never logs: returns values or raises.

  foreground_line(cwd, cmd, who, output)  the shell line: in cwd, as root on behalf of `who`, output appended to `output`,
                                          waited for (the window's answer comes when it's done)
  ask(line, prompt)                       show the password window; returns the osascript process at once
  outcome(code, output)                   how it went: ("started" | "cancelled" | "failed", what to say)
"""

import shlex

from nxspoof.macos import shell

OSASCRIPT = "/usr/bin/osascript"
APPLESCRIPT = ("on run argv",
               "do shell script (item 1 of argv) with prompt (item 2 of argv) with administrator privileges",
               "end run")                    # the line and the sentence come in as arguments: nothing is pasted into the script
CANCELLED = "-128"                           # AppleScript's "User canceled." error number

# The shell line that runs cmd (a list) in folder cwd, as root on behalf of who = (name, uid, gid): no input, its output (and
# errors) appended to the file `output`, waited for: the password window's answer comes when it has finished. Its own exit
# code doesn't make the window fail (its records say how it went); a folder it can't enter does (macOS's message).
def foreground_line(cwd, cmd, who, output):
    name, uid, gid = who
    return "cd %s || exit 1; /usr/bin/env SUDO_USER=%s SUDO_UID=%d SUDO_GID=%d %s >>%s 2>&1 </dev/null; exit 0" % (
        shlex.quote(cwd), shlex.quote(name), int(uid), int(gid), " ".join(shlex.quote(c) for c in cmd), shlex.quote(output))

# The osascript command that shows the password window with `prompt` and then runs `line` as root.
def command(line, prompt):
    out = [OSASCRIPT]
    for part in APPLESCRIPT:
        out += ["-e", part]
    return out + [line, prompt]

# Show macOS's password window (returns at once with the running osascript; shell.finish(it) waits for the answer).
def ask(line, prompt, start=shell.start):
    return start(command(line, prompt))

# How the password window ended: ("started", ""), ("cancelled", ...) when you closed it, ("failed", macOS's message).
def outcome(code, output):
    text = (output or "").strip()
    if code == 0:
        return "started", ""
    if CANCELLED in text or "User canceled" in text:
        return "cancelled", "the password window was cancelled"
    return "failed", text.splitlines()[-1] if text else "osascript ended with code %d" % code

# Wait for the password window (ask's process) -> its outcome.
def finish(handle):
    return outcome(*shell.finish(handle))
