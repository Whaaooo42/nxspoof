"""
nxspoof.output.datafolder - NXSPOOF's data folder: settings.json, logs/, last_run.log, the dashboard's link file and
the program copy, in ~/Library/Application Support/NXSPOOF/ (macos.user.data_folder()), wherever the nx-spoof folder is.

Why a folder of its own: the dashboard's ▶ Start runs NXSPOOF as root through macOS's password window, and macOS keeps
a program started that way out of Documents, Desktop, Downloads and iCloud Drive without asking (macos/admin.py). So that
run must not need the nx-spoof folder at all: its data lives here, and so does a copy of its program (program/), which
the dashboard refreshes just before each ▶ Start (only the files that changed). Runs from Terminal, the setup windows and
the menu use the same data folder, with the nx-spoof folder's own program. Moving or renaming the nx-spoof folder never
loses settings or runs.

  prepare(places, give, give_link)                 create the role folders (core/places.py); once, move settings.json, logs/
                                                   and last_run.log over from the nx-spoof folder; the `data` shortcut there
                                                   -> [(level, what it did)]
  copy_program(project_dir, runtime, give)         refresh program/ (in the runtime folder) from the nx-spoof folder
                                                   -> (its path, copied, removed)
  remember_before(folder, part, value, give)       note how a part of this Mac was before NXSPOOF first changed it (once)
  before(folder)                                   those notes: {"bridge": {...}} ({} if none)
Never talks to macOS: ownership goes back to you through the callbacks the app passes in (macos.user).
"""

import json
import os
import shutil

SETTINGS = "settings.json"
LOGS = "logs"
LAST_RUN = "last_run.log"
SHORTCUT = "data"                            # the shortcut in the nx-spoof folder that leads here (Finder shows it as an alias)
PROGRAM = "program"                          # the program copy ▶ Start runs
PROGRAM_FILES = ("nx_spoof.py", "nxspoof")   # what the copy holds (a run needs nothing else from the nx-spoof folder)
SKIPPED = ("__pycache__", ".DS_Store")
OLD_SUFFIX = ".old"                          # an old settings.json left in the nx-spoof folder when the data folder already has one

# True if path is folder itself or somewhere inside it.
def inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)

# Create the role folders (places: core/places.py) and, the first time, move the old files over from the nx-spoof folder;
# returns [(level, text)] for the log ("info" or "warn"), empty when there was nothing to do. A program running from inside
# a role folder (the copy ▶ Start runs, or the tests' temporary folders) moves nothing.
def prepare(places, give=None, give_link=None):
    give = give or (lambda path: None)
    give_link = give_link or (lambda path: None)
    done, project_dir = [], places.program
    for folder in places.folders():
        if not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
            give(folder)
            done.append(("info", "created NXSPOOF's %s: %s" % ("data folder" if places.together_in_one() else "folder", folder)))
    if any(inside(project_dir, folder) for folder in places.folders()):
        return done
    for step, folder in ((_move_settings, places.config), (_move_logs, places.logs), (_move_last_run, places.logs), (_shortcut, places.config)):
        try:
            done += step(folder, project_dir, give, give_link)
        except OSError as e:
            done.append(("warn", "data folder: %s couldn't finish (%s)" % (step.__name__.strip("_").replace("_", " "), e)))
    return done

# settings.json: moved over unless the data folder has one already (then the old one is renamed settings.json.old).
def _move_settings(data_dir, project_dir, give, give_link):
    old, new = os.path.join(project_dir, SETTINGS), os.path.join(data_dir, SETTINGS)
    if not os.path.isfile(old) or os.path.islink(old):
        return []
    if not os.path.exists(new):
        shutil.move(old, new)
        give(new)
        return [("info", "moved settings.json from the nx-spoof folder to the data folder")]
    os.replace(old, old + OLD_SUFFIX)
    return [("warn", "the data folder already has a settings.json, so that one is used; the nx-spoof folder's own is kept as "
                     "settings.json%s (not used)" % OLD_SUFFIX)]

# logs/: each file moved over (one already there is left where it was), then the empty old folder removed.
def _move_logs(data_dir, project_dir, give, give_link):
    old, new = os.path.join(project_dir, LOGS), os.path.join(data_dir, LOGS)
    if not os.path.isdir(old) or os.path.islink(old):
        return []
    os.makedirs(new, exist_ok=True)
    give(new)
    moved, left = 0, []
    for name in sorted(os.listdir(old)):
        if os.path.lexists(os.path.join(new, name)):
            left.append(name)
            continue
        shutil.move(os.path.join(old, name), os.path.join(new, name))
        moved += 1
    if not left:
        os.rmdir(old)
        return [("info", "moved %d log file(s) from the nx-spoof folder's logs/ to the data folder" % moved)]
    return [("warn", "moved %d log file(s) to the data folder; %d already there were left in the nx-spoof folder's logs/ (%s)" % (
        moved, len(left), ", ".join(left[:3]) + (", ..." if len(left) > 3 else "")))]

# last_run.log: the shortcut to the latest run, made again in the data folder (its logs moved there with it).
def _move_last_run(data_dir, project_dir, give, give_link):
    old, new = os.path.join(project_dir, LAST_RUN), os.path.join(data_dir, LAST_RUN)
    if not os.path.lexists(old):
        return []
    if not os.path.islink(old):
        if os.path.lexists(new):
            return []
        shutil.move(old, new)                # a very old single growing log: logfile.create_run_log files it away
        return [("info", "moved the old single last_run.log to the data folder")]
    target = os.readlink(old)
    os.remove(old)
    if os.path.lexists(new) or os.path.isabs(target):
        return []
    os.symlink(target, new)
    give_link(new)
    return [("info", "last_run.log now points to the latest run from the data folder")]

# The `data` shortcut in the nx-spoof folder, so the logs and settings stay one click away.
def _shortcut(data_dir, project_dir, give, give_link):
    link = os.path.join(project_dir, SHORTCUT)
    if os.path.lexists(link):
        return []
    os.symlink(data_dir, link)
    give_link(link)
    return [("info", "added the '%s' shortcut in the nx-spoof folder (it opens the data folder)" % SHORTCUT)]

BEFORE = "mac_before.json"                  # how this Mac was before NXSPOOF first changed it (Undo's "before")

# Note how one part of this Mac (part: "bridge") was before NXSPOOF first changed it, unless already noted:
# the first record wins, so Undo can always put back the original. folder: the settings role. Returns True if written.
def remember_before(folder, part, value, give=None):
    notes = before(folder)
    if part in notes or value is None:
        return False
    notes[part] = value
    path = os.path.join(folder, BEFORE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    (give or (lambda p: None))(path)
    return True

# The notes of how this Mac was before NXSPOOF first changed it ({} if none, or unreadable).
def before(folder):
    try:
        with open(os.path.join(folder, BEFORE), encoding="utf-8") as f:
            notes = json.load(f)
        return notes if isinstance(notes, dict) else {}
    except (OSError, ValueError):
        return {}

# Every file the program copy should hold, as paths relative to the nx-spoof folder.
def program_files(project_dir):
    out = []
    for top in PROGRAM_FILES:
        path = os.path.join(project_dir, top)
        if os.path.isfile(path):
            out.append(top)
            continue
        for folder, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIPPED)
            rel = os.path.relpath(folder, project_dir)
            out += [os.path.join(rel, f) for f in sorted(files) if f not in SKIPPED and not f.endswith(".pyc")]
    return out

# Refresh the program copy (runtime folder/program) from the nx-spoof folder: files that changed (size or time) are copied,
# files gone from the nx-spoof folder removed. Returns (its path, files copied, files removed).
def copy_program(project_dir, runtime, give=None):
    give = give or (lambda path: None)
    dest = os.path.join(runtime, PROGRAM)
    if inside(project_dir, dest):
        return dest, 0, 0
    wanted = program_files(project_dir)
    copied = removed = 0
    for rel in wanted:
        src, dst = os.path.join(project_dir, rel), os.path.join(dest, rel)
        if _same(src, dst):
            continue
        if not os.path.isdir(os.path.dirname(dst)):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            give(os.path.dirname(dst))
        shutil.copy2(src, dst)
        give(dst)
        copied += 1
    keep = set(wanted)
    for folder, dirs, files in os.walk(dest, topdown=False):
        for f in files:
            if os.path.relpath(os.path.join(folder, f), dest) not in keep:
                os.remove(os.path.join(folder, f))
                removed += 1
        if folder != dest and not os.listdir(folder):
            os.rmdir(folder)
    return dest, copied, removed

# True if dst is already a copy of src (same size and modification time, as shutil.copy2 keeps it).
def _same(src, dst):
    try:
        a, b = os.stat(src), os.stat(dst)
    except OSError:
        return False
    return a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime)
