"""
nxspoof.macos.user - acting on behalf of the person who ran sudo: file ownership, running apps as
them, opening URLs, System Settings and FileZilla; where your home and NXSPOOF's data folder are.

NXSPOOF runs as root (through sudo), so anything it creates would belong to root; give_to_user()
hands it back. Apps must open as the real user, not as root, so they go through run_as_user().
Whether a run is active is the lock's answer (output/runfile.py); starting and stopping one is macos/process.py.
Never logs: returns values or raises.
"""

import os
import pwd
import subprocess

SHARING_SETTINGS_URL = "x-apple.systempreferences:com.apple.Sharing-Settings.extension"
FILEZILLA_BIN = "/Applications/FileZilla.app/Contents/MacOS/filezilla"
DATA_FOLDER = os.path.join("Library", "Application Support", "NXSPOOF")     # under your home (output/datafolder.py says why)

# Who is running this program, as (name, uid, gid): you, for the dashboard (handed to a run it starts as root).
def me():
    uid = os.getuid()
    return pwd.getpwuid(uid).pw_name, uid, os.getgid()

# Your home folder: the dashboard's user, or the person behind sudo for a run (never root's /var/root).
def home():
    who = sudo_user()
    return pwd.getpwuid(who[1] if who else os.getuid()).pw_dir

# NXSPOOF's data folder (settings, logs, runtime files: core/places.py), the same wherever the nx-spoof folder is.
def data_folder():
    return os.path.join(home(), DATA_FOLDER)

# True when running as root (through sudo).
def is_root():
    return os.geteuid() == 0

# The real user behind sudo as (name, uid, gid), or None when not running through sudo.
def sudo_user():
    try:
        return os.environ["SUDO_USER"], int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    except (KeyError, ValueError):
        return None

# Hand a file or folder back to the real user (no-op without sudo); True if ownership was changed.
def give_to_user(path):
    who = sudo_user()
    if who is None:
        return False
    try:
        os.chown(path, who[1], who[2])
        return True
    except OSError:
        return False

# Same for a symbolic link itself (e.g. last_run.log), not the file it points to.
def give_link_to_user(path):
    who = sudo_user()
    if who is None:
        return False
    try:
        os.lchown(path, who[1], who[2])
        return True
    except OSError:
        return False

# The command prefixed to run as the real user when we're root through sudo (unchanged otherwise).
def as_user_command(cmd):
    who = sudo_user()
    return (["sudo", "-u", who[0]] if who and os.geteuid() == 0 else []) + list(cmd)

# Run a command as the real user; wait=True returns the finished process, False returns it running.
def run_as_user(cmd, wait=True):
    full = as_user_command(cmd)
    if wait:
        return subprocess.run(full, capture_output=True)
    return subprocess.Popen(full, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

# Your account name and your full name (as macOS has them), even when running as root through sudo.
def account_names():
    who = sudo_user()
    entry = pwd.getpwuid(who[1] if who else os.getuid())
    return entry.pw_name, (entry.pw_gecos or "").split(",")[0].strip()

# Put text on the real user's clipboard (pbcopy, as them: root's clipboard isn't theirs) -> True if it worked.
def copy_text(text):
    try:
        return subprocess.run(as_user_command(["/usr/bin/pbcopy"]), input=text, text=True, capture_output=True).returncode == 0
    except OSError:
        return False

# Open a URL in the real user's default browser (or app for that kind of URL).
def open_url(url):
    return run_as_user(["open", url])

# Open System Settings > General > Sharing.
def open_sharing_settings():
    return open_url(SHARING_SETTINGS_URL)

# True if FileZilla is installed in /Applications.
def filezilla_installed():
    return os.path.exists(FILEZILLA_BIN)

# Open FileZilla, as the real user, connected to an ftp:// URL; raises FileNotFoundError if it's missing.
def open_filezilla(url):
    if not filezilla_installed():
        raise FileNotFoundError("FileZilla isn't in /Applications")
    return run_as_user([FILEZILLA_BIN, url], wait=False)
