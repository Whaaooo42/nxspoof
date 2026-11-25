"""
nxspoof.app.transfer - `nx_spoof.py settings export|import <file.json>`: your settings.json to a file, or another one in
its place, from the Terminal (the dashboard's ⚙️ Configure has the same Export and Import buttons).

An import is checked first (settings/transfer.py says what it changes; a broken file changes nothing), asked unless
--yes, and the current file is kept as settings.json.before-import-<date>_<time>. Never while a run is active or the
dashboard is open: they own settings.json while they're up (D18).
"""

import os

from nxspoof.app.commands import load_store
from nxspoof.dashboard import center as center_link
from nxspoof.settings import transfer
from nxspoof.settings.store import SettingsError
from nxspoof.output import runfile

# `settings export PATH` / `settings import PATH` (args.words); ask(question) -> bool replaces the Terminal's question (tests).
def settings_command(args, places, system, out, ask=None):
    words = args.words or []
    if len(words) != 2 or words[0] not in ("export", "import"):
        out.write("usage: python3 -B nx_spoof.py settings export|import <file.json>\n")
        return 2
    try:
        store, _ = load_store(args, places, on_saved=system.give_to_user)
    except SettingsError as e:
        out.write("SETTINGS not done: %s\n" % e)
        return 1
    path = os.path.expanduser(words[1])
    if words[0] == "export":
        return export_settings(store, path, out)
    return import_settings(store, path, places, system, out, args.yes or (ask or ask_yes))

# Your settings.json, in the tidy layout, to a file.
def export_settings(store, path, out):
    try:
        out.write("SETTINGS exported %s to %s\n" % (os.path.basename(store.path), store.export_to(path)))
        return 0
    except OSError as e:
        out.write("SETTINGS not exported: %s\n" % e)
        return 1

# Another settings.json in place of yours: refused while NXSPOOF or its dashboard is up, checked, said, asked (sure: True
# or a question function), then imported with the old file kept.
def import_settings(store, path, places, system, out, sure):
    if runfile.held(places.runtime) or center_link.find(places.runtime) is not None:
        out.write("SETTINGS not imported: NXSPOOF or its dashboard is open (it owns settings.json while it's up) - "
                  "close it first, or use the dashboard's ⚙️ Configure > Import\n")
        return 1
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        change = transfer.preview(store, text, os.path.basename(path))
    except (SettingsError, OSError) as e:
        out.write("SETTINGS not imported: %s\n" % e)
        return 1
    out.write("SETTINGS importing %s: %s\n" % (path, transfer.describe(change)))
    for w in change["warnings"]:
        out.write("SETTINGS WARNING %s\n" % w)
    if sure is not True and not sure("Replace your settings with this file?"):
        out.write("SETTINGS nothing changed\n")
        return 0
    _, backup = store.replace(text, os.path.basename(path))
    out.write("SETTINGS imported; your previous settings are kept as %s\n" % (backup or "(there was no settings file)"))
    return 0

# A yes/no question on the Terminal (no = the default, and when there's no Terminal to answer).
def ask_yes(question):
    try:
        return input("%s [y/N] " % question).strip().lower().startswith("y")
    except EOFError:
        return False
