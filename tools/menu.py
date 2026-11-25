#!/usr/bin/env python3
"""
menu.py - the NXSPOOF menu: every everyday and development action, a number or two away.

Opened by double-clicking menu.command: door ONE, the Terminal way to do everything (the usual way in is NXSPOOF.app, the
dashboard). Numbers only, in order, on every screen (owner, 2026-11-22; the design is now docs/two-doors.html): the main screen has the four
everyday items (start, follow, stop, dashboard), then the four sub-menus (Past runs, This Mac, Development, Docs),
where the numbers start at 1 again; b goes back, q quits. Each item is one row in MENU below: its screen, its label, what
it does (a function, or a sub-menu's name), whether it needs your password, and an optional second line. Its number is
its place on its screen. Add an item = add a row.

Every item is one plain thing (D30: chaining is the app's): one of nx_spoof.py's commands (start, stop, follow, doctor,
cleanup, check, settings, setup, uninstall), a tool, or opening a file or folder.
"""

import collections
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True
from nxspoof.app.system import System  # noqa: E402  (after the path is set)

PLACES = System().places(ROOT)                # where NXSPOOF's files are (nxspoof/core/places.py): on a Mac, the data folder
PY = "/usr/bin/python3"                       # Apple's Python: what NXSPOOF itself runs on
APP_MAKER = "Create NXSPOOF.app.command"      # builds NXSPOOF.app (top of the folder), then opens it
BOLD, DIM, GREEN, YELLOW, RED, RESET = "\x1b[1m", "\x1b[2m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[0m"

# ##############################################################################################################################################################
# ##  STATUS (THE LINE AT THE TOP)                                                                                                                            ##
# ##############################################################################################################################################################

# Run a command quietly in the project folder; return (exit code, output).
def quiet(cmd):
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=10)
        return p.returncode, (p.stdout + p.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)

# The dashboard's port: the one it remembers (nxspoof/dashboard/center.py, D30: last time's, 8800 at first).
def dashboard_port():
    from nxspoof.dashboard import center
    return center.remembered_port(PLACES.runtime)

# True if a run of NXSPOOF is active right now (nx_spoof.py's one answer: the dashboard and other commands don't count).
def spoof_running():
    return quiet([PY, "-B", "nx_spoof.py", "running"])[0] == 0

# True if the dashboard answers on this Mac.
def dashboard_up():
    try:
        urllib.request.urlopen("http://localhost:%d/api/state" % dashboard_port(), timeout=1).read()
        return True
    except OSError:
        return False

# One-line summary: spoof (running: as just asked), virtual cable, Internet Sharing, the dashboard.
def status_line(running):
    cable = quiet([PY, "-B", "nx_spoof.py", "link", "status"])[0] == 0
    code, out = quiet([PY, "-B", "nx_spoof.py", "sharing", "status"])
    sharing_on = "running : YES" in out
    parts = [(GREEN + "● running" if running else DIM + "○ not running") + RESET,
             (GREEN + "cable in" if cable else DIM + "cable out") + RESET,
             (YELLOW + "sharing ON" if sharing_on and not running else (GREEN + "sharing on" if sharing_on else DIM + "sharing off")) + RESET,
             (GREEN + "dashboard open" if dashboard_up() else DIM + "dashboard closed") + RESET]
    return " · ".join(parts)

# ##############################################################################################################################################################
# ##  ACTIONS                                                                                                                                                 ##
# ##############################################################################################################################################################

# Run a command in this window (its output shows here); return its exit code (the menu waits for Enter afterwards).
def run(cmd):
    sys.stdout.flush()                                # our text first, then the command's
    try:
        return subprocess.call(cmd, cwd=ROOT)
    except KeyboardInterrupt:
        return 130
    except OSError as e:
        print(RED + "Couldn't run %s: %s" % (cmd[0], e) + RESET)
        return 1

# Start NXSPOOF (nx_spoof.py start, D30): already running -> said at once; else your password once; it answers once the run is ready (or why it couldn't), and
# the run goes on in the background (closing this window doesn't stop it). This Mac not ready: the reasons, then "let setup
# fix what it can, then start?" (start --fix, as the dashboard's Fix and start), then if still not ready "start anyway?"
# (start --anyway). Watch it with 3 or 4, stop it with 2.
def start_background():
    if spoof_running():                      # said at once, no password asked (the menu drawn earlier may not know yet)
        print(YELLOW + "NXSPOOF is already running: nothing done. Watch it with 3 or 4, stop it with 2." + RESET)
        return
    start = ["sudo", PY, "-B", os.path.join(ROOT, "nx_spoof.py"), "start"]
    code = run(start)
    if code == 2 and input(YELLOW + "Let setup fix what it can, then start? [Y/n] " + RESET).strip().lower() in ("", "y", "yes"):
        code = run(start + ["--fix"])
    if code == 2 and input(YELLOW + "Start anyway? [y/N] " + RESET).strip().lower() in ("y", "yes"):
        run(start + ["--anyway"])

# Follow the newest run live in a Terminal style (Ctrl+C only stops watching; NXSPOOF keeps running).
def follow(style):
    run([PY, "-B", "nx_spoof.py", "follow", "--console", style])

# Open the dashboard: the page if it's up, else NXSPOOF.app (or, without it, scripts/dashboard.command in its own window).
def open_dashboard():
    if dashboard_up():
        run(["open", "http://localhost:%d/" % dashboard_port()])
        return
    if os.path.isdir(os.path.join(ROOT, "NXSPOOF.app")):
        print(DIM + "Opening the dashboard with NXSPOOF.app (no Terminal window, no password needed)…" + RESET)
        run(["open", os.path.join(ROOT, "NXSPOOF.app")])
        return
    print(DIM + "Opening the dashboard in its own window (no password needed)…" + RESET)
    run(["open", "-a", "Terminal", os.path.join(ROOT, "scripts", "dashboard.command")])

# Rebuild NXSPOOF.app from its source (scripts/NXSPOOF.applescript), e.g. after changing it: the same as double-clicking
# "Create NXSPOOF.app.command" (one way to build it), which then opens it.
def rebuild_app():
    run(["/bin/bash", os.path.join(ROOT, APP_MAKER)])

# Open the latest run's log in the default text editor.
def open_last_log():
    path = os.path.join(PLACES.logs, "last_run.log")
    if not os.path.exists(path):
        print(YELLOW + "No run log yet." + RESET)
        return
    run(["open", "-t", os.path.realpath(path)])

# Show NXSPOOF's data folder (settings.json, every log) in Finder.
def open_data_folder():
    if not os.path.isdir(PLACES.config):
        print(YELLOW + "No data folder yet: it's made the first time the dashboard or a run starts." + RESET)
        return
    run(["open", PLACES.config])

# Run every unit test (no password needed).
def run_tests():
    run([PY, "-B", os.path.join("tools", "run_tests.py")])

# Play the scripted session and compare it with the reference run (the previous code's, ~45 s): nothing visible changed by accident?
def simulate_compare(driver="new"):
    print(DIM + "Playing the scripted session (about 45 seconds)…" + RESET)
    out = os.path.join(tempfile.mkdtemp(prefix="nxspoof-menu-"), driver)
    if run([PY, "-B", os.path.join("tools", "simulate.py"), driver, "--out", out]) != 0:
        return
    ref = os.path.join(ROOT, "tests", "fixtures", "reference", "run")         # today's code's reference run (tools/simulate.py)
    code = run([PY, "-B", os.path.join("tools", "simulate.py"), "--compare", ref, out])
    print((GREEN + "Same as the reference run." if code == 0 else YELLOW + "Different from the reference (see above).") + RESET)

# Watch a simulated session on the dashboard in your browser (no Switch, no password; about 3 minutes).
def watch_simulation():
    print(DIM + "Fake consoles come and go for about a minute, then the run stays open 2 more minutes. Ctrl+C ends it early." + RESET)
    out = os.path.join(tempfile.mkdtemp(prefix="nxspoof-menu-"), "watch")
    sys.stdout.flush()
    p = subprocess.Popen([PY, "-B", os.path.join("tools", "simulate.py"), "new", "--hold", "120", "--dashboard", "--out", out], cwd=ROOT)
    try:
        time.sleep(4)
        subprocess.call(["open", "http://localhost:8897/"])
        p.wait()
    except KeyboardInterrupt:
        p.terminate()
        p.wait()

# One of nx_spoof.py's commands (doctor, setup, uninstall, cleanup, check, stop), with sudo when needed.
def command(name, sudo=False, refuse_while_running=False):
    if refuse_while_running and spoof_running():
        print(YELLOW + "NXSPOOF is running: stop it first." + RESET)
        return
    run((["sudo"] if sudo else []) + [PY, "-B", "nx_spoof.py", name])

# Export or import settings.json (action: "export" or "import"), to or from a file you name (Enter: on the Desktop).
def settings_file(action):
    default = os.path.expanduser("~/Desktop/nxspoof-settings.json")
    path = input("File (Enter for %s): " % default).strip() or default
    run([PY, "-B", "nx_spoof.py", "settings", action, os.path.expanduser(path)])

# The guardrails (a line per rule) and the watchdog's full report (with the known spots).
def code_health():
    run([PY, "-B", os.path.join("tools", "guardrails.py")])
    print()
    run([PY, "-B", os.path.join("tools", "health.py"), "--all"])

# Open one of the docs pages (docs/) in the default browser.
def open_doc(page):
    run(["open", os.path.join(ROOT, "docs", page)])

# Rewrite the docs pages' generated tables from the code (tools/docs.py), then say whether all is current.
def update_docs():
    run([PY, "-B", os.path.join("tools", "docs.py"), "--write"])

# Draw NXSPOOF's icon again (tools/icon.py): the app's (scripts/NXSPOOF.icns) and the browser tabs' (favicon.png).
def draw_icon():
    run([PY, "-B", os.path.join("tools", "icon.py")])

# ##############################################################################################################################################################
# ##  THE MENU                                                                                                                                                ##
# ##############################################################################################################################################################

# One menu row. screen: where it shows; label; action: a function, or a sub-menu's name (SCREENS); pw: needs your password;
# detail: what it does, in gray to its right (a sub-menu's comes from SCREENS); gap: a blank line above it; when: "idle" / "running" (drawn grayed out otherwise, but it
# still works if typed: its command answers at once; the state is the one when the screen was drawn), None: always.
Item = collections.namedtuple("Item", "screen label action pw detail gap when")
Item.__new__.__defaults__ = (False, None, False, None)

# The rows, in order: the number is a row's place on its screen. The main screen: on and off, watching the run that's going,
# then the sub-menus (owner, 2026-11-22: a run is one session, its log what it writes, checking it its verdict).
MENU = [
    Item("main", "Start NXSPOOF", start_background, pw=True, when="idle"),
    Item("main", "Stop NXSPOOF", lambda: command("stop"), when="running"),
    Item("main", "Watch it live (technical)", lambda: follow("technical"), gap=True, when="running", detail="Ctrl+C only stops watching"),
    Item("main", "Watch it live (casual)", lambda: follow("casual"), when="running", detail="Ctrl+C only stops watching"),
    Item("main", "Open the dashboard", open_dashboard),
    Item("main", "Past runs…", "past", gap=True),
    Item("main", "This Mac…", "mac"),
    Item("main", "Development…", "dev"),
    Item("main", "Docs…", "docs"),
    Item("past", "Check the latest run", lambda: run(["sudo", PY, "-B", os.path.join("tools", "verify_run.py")]), pw=True,
         detail="✅/❌: did everything go as it should?"),
    Item("past", "Read the latest run's log", open_last_log, detail="in your text editor"),
    Item("past", "Open the data folder", open_data_folder, detail="every run's log, settings.json"),
    Item("past", "Report a problem", lambda: command("report"), detail="for a GitHub issue: names replaced; printed, saved, copied, GitHub opened"),
    Item("mac", "Is this Mac ready?", lambda: command("doctor"), detail="changes nothing"),
    Item("mac", "Set up this Mac", lambda: command("setup", True), pw=True, when="idle", detail="asks before each change"),
    Item("mac", "Undo setup", lambda: command("uninstall", True), pw=True, when="idle", detail="asks first; your files and settings stay"),
    Item("mac", "Clean up after a crash", lambda: command("cleanup", True, True), pw=True, when="idle",
         detail="firewall rules out, sharing off, cable out"),
    Item("mac", "Export settings.json", lambda: settings_file("export")),
    Item("mac", "Import settings.json", lambda: settings_file("import")),
    Item("dev", "Run the tests", run_tests),
    Item("dev", "Simulate a session", simulate_compare, detail="compared with the reference run"),
    Item("dev", "Quick self-test", lambda: command("check"), detail="the DNS and web answers, on this Mac"),
    Item("dev", "Watch a simulated session", watch_simulation, detail="on the dashboard, no Switch needed"),
    Item("dev", "Code health", code_health, detail="guardrails, watchdog, memo"),
    Item("dev", "Rebuild NXSPOOF.app", rebuild_app),
    Item("dev", "Update the docs' tables", update_docs, detail="settings, options, menu, modules: from the code"),
    Item("dev", "Draw the icon again", draw_icon, detail="the app's and the browser tabs'; then rebuild the app"),
    Item("docs", "All the docs", lambda: open_doc("index.html"), detail="the same pages as the dashboard's 📖 Help"),
    Item("docs", "Guide", lambda: open_doc("guide.html"), detail="the everyday things"),
    Item("docs", "Troubleshooting", lambda: open_doc("troubleshooting.html"), detail="type what you see, find the fix"),
    Item("docs", "Reference", lambda: open_doc("reference.html"), detail="settings, options, commands, files"),
    Item("docs", "Real runs", lambda: open_doc("real-runs.html"), detail="the checklist on the real Mac"),
    Item("docs", "How it's built", lambda: open_doc("how-its-built.html"), detail="for development"),
]
SCREENS = {"past": ("Past runs", "check the latest run, read its log, the data folder"),         # screen -> (title, what's in it)
           "mac": ("This Mac", "ready?, set up, undo, clean up, settings file"),
           "dev": ("Development", "tests, simulate, code health"),
           "docs": ("Docs", "in your browser: guide, troubleshooting, reference…")}

# The rows of one screen, in order: [(number as text, row)].
def rows(screen):
    return [(str(n), row) for n, row in enumerate((r for r in MENU if r.screen == screen), 1)]

# Print one screen of the menu: the status line (main) or its title, its numbered rows (🔑: needs your password; grayed out:
# not for now, given whether NXSPOOF runs), b / q.
def show_menu(screen="main"):
    running = spoof_running()
    print("\x1b[2J\x1b[H", end="")                    # clear the window
    if screen == "main":
        print(BOLD + "🎮 NXSPOOF" + RESET + "   " + status_line(running) + "\n")
    else:
        print(BOLD + "🎮 NXSPOOF › " + SCREENS[screen][0] + RESET + "\n")
    shown = rows(screen)
    width = max(len(item.label) for _, item in shown)
    for key, item in shown:
        print("\n" * item.gap + row_text(key, item, item.when not in (None, "running" if running else "idle"), width))
    print("\n   %sq%s  Quit\n" % (BOLD, RESET) if screen == "main" else "\n   %sb%s  Back   %sq%s  Quit\n" % (BOLD, RESET, BOLD, RESET))

# One row as it's drawn: its number, its label (grayed out when it's not for now) padded to the screen's widest, 🔑 when it
# needs your password, then what it does in gray (one column on every screen), cut with … to fit the window.
def row_text(key, item, grayed, width):
    detail = SCREENS[item.action][1] if isinstance(item.action, str) else item.detail or ""
    label = "%-*s %s" % (width, item.label, "🔑" if item.pw else "  ")
    number = "%s%s%s" % (DIM if grayed else BOLD, key, RESET if not grayed else "")
    text = "   %s  %s%s" % (number, label, RESET if grayed else "")
    room = shutil.get_terminal_size((100, 32)).columns - (3 + len(key) + 2 + width + 3 + 2) - 1
    if detail and len(detail) > room:                                # a narrow window: cut, never wrap
        detail = detail[:max(room - 1, 0)] + "…" if room > 1 else ""
    return (text + "  " + DIM + detail + RESET) if detail else text.rstrip()

# Run one menu item, then wait for Enter (so its output can be read).
def do_item(label, func):
    print("\n" + BOLD + "▶ " + label + RESET + "\n")
    try:
        func()
    except KeyboardInterrupt:
        print()
    input("\n" + DIM + "Press Enter to return to the menu…" + RESET)

# Act on one choice typed on a screen -> the screen to show next, or None to quit (a number: its row; b or Enter goes back
# from a sub-menu; q quits).
def choose(screen, raw):
    choice = raw.lower()
    numbered = dict(rows(screen))
    if choice in ("q", "quit", "exit"):
        return None
    if screen != "main" and choice in ("b", "back", ""):
        return "main"
    if choice not in numbered:
        if choice:
            input(YELLOW + "No item '%s' here: type its number%s (Enter to go on)." % (raw, ", b to go back" if screen != "main" else "") + RESET)
        return screen
    item = numbered[choice]
    if isinstance(item.action, str):
        return item.action
    do_item(item.label, item.action)
    return screen

# Show the menu and act on each choice until q (Ctrl+C or Ctrl+D also quit).
def main():
    screen = "main"
    while screen:
        show_menu(screen)
        try:
            screen = choose(screen, input("Choose: ").strip())
        except (EOFError, KeyboardInterrupt):
            print()
            return

if __name__ == "__main__":
    main()
