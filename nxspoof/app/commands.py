"""
nxspoof.app.commands - the small commands of nx_spoof.py (app/main.py picks the command):

  check     the self-test on 127.0.0.1 with free ports: are the DNS and web answers right on this Mac?
  link      the virtual cable: status / up / down (macos/cable.py)
and, for every command that reads settings or writes logs, NXSPOOF's data folder made ready (prepare_data).
"""

import os

from nxspoof.core.places import Places
from nxspoof.macos import cable, user
from nxspoof.net import selftest
from nxspoof.net.dns import DnsServer
from nxspoof.net.policy import Policy
from nxspoof.net.web import TestWebServer
from nxspoof.output import datafolder
from nxspoof.settings.store import SettingsError, SettingsStore
from nxspoof.output import runfile

USES_DATA = ("start", "dashboard", "check", "setup", "doctor", "uninstall", "cleanup", "settings", "report")      # commands that read settings or write logs

# Where NXSPOOF's files are (core/places.py): the platform's (System.places) for the program in default_program, or, given a
# project folder (tests, the simulator), that one folder for every role.
def find_places(system, project_dir, default_program):
    return Places.together(project_dir, project_dir) if project_dir else system.places(default_program)

# NXSPOOF's folders ready for `command` (output/datafolder.py: created, old files moved over once): returns what that
# did ([(level, text)], for the run's or the dashboard's log), or None when they can't be created (said on the Terminal).
def prepare_data(command, system, places):
    if command not in USES_DATA:
        return []
    try:
        return datafolder.prepare(places, give=system.give_to_user, give_link=system.give_link_to_user)
    except OSError as e:
        print("NXSPOOF's folders (%s) can't be created (%s)" % (places.describe(), e))
        return None

# Load settings.json (path from --settings, else the settings role's: core/places.py); returns (store, message) or raises SettingsError.
def load_store(args, places, on_saved=None):
    store = SettingsStore(args.settings or places.settings_file(), on_saved=on_saved)
    return store, store.load()

# The self-test on 127.0.0.1 with free ports: are the DNS and web answers right on this Mac?
def check_command(args, places, out):
    try:
        store, message = load_store(args, places)
    except SettingsError as e:
        out.write("SETTINGS %s\n" % e)
        return 1
    out.write("SETTINGS %s\n" % message)
    for line in store.warning_lines():
        out.write("SETTINGS %s\n" % line)
    policy = Policy()
    dns = DnsServer("127.0.0.1", 0, "127.0.0.1", policy, lambda *a: None)
    web = TestWebServer("127.0.0.1", 0, policy, lambda *a: None)
    dns.start()
    web.start()
    try:
        checks = selftest.run_selftest("127.0.0.1", dns.sock.getsockname()[1], web.httpd.server_address[1], "127.0.0.1")
    finally:
        web.stop()
        dns.stop()
    for ch in checks:
        out.write("CHECK %s\n" % selftest.check_line(ch))
    ok = all(ch.passed for ch in checks)
    out.write("check: %s\n" % ("all good - the DNS and web answers are right" if ok else "FAILED - see docs/troubleshooting.html"))
    return 0 if ok else 1

# Is a run active right now (it holds the lock, output/runfile.py)? -> "yes" / "no" on the Terminal; exit 0 = yes (for the
# scripts and the menu).
def running_command(places, out):
    active = runfile.held(places.runtime)
    out.write("yes\n" if active else "no\n")
    return 0 if active else 1

LINK_ACTIONS = {"status": cable.status, "up": cable.up, "down": cable.down}         # down answers nothing (None)

# Why `link <action>` can't run now (an unknown action, or up/down without admin rights), or None.
def link_refusal(action):
    if action not in LINK_ACTIONS:
        return "usage: python3 nx_spoof.py link [status]   or   sudo python3 nx_spoof.py link up|down"
    if action != "status" and not user.is_root():
        return "Run with sudo: sudo python3 -B nx_spoof.py link %s" % action
    return None

# The virtual cable (macos/cable.py): status (default), up or down; up and down need admin rights. Exit 0 = plugged in and usable.
def link_command(words):
    action = (list(words) or ["status"])[0]
    refusal = link_refusal(action)
    if refusal:
        print(refusal)
        return 1
    try:
        st = LINK_ACTIONS[action]()
    except OSError as e:
        print("ERROR: %s" % e)
        return 1
    if st is None:
        print("Virtual cable removed.")
        return 0
    print("Thunderbolt Bridge link: %s\nCHECK %s" % (st.link, st.text()))
    return 0 if st.ok else 1
