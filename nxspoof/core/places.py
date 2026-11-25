"""
nxspoof.core.places - where NXSPOOF's files are, by role (D23). The platform says where each role lives
(app/system.py places(), from macos/user.py on a Mac); nothing else builds these paths.

  program   the nx-spoof folder: nx_spoof.py, nxspoof/, scripts/, docs/ (never written while NXSPOOF runs)
  config    settings.json
  logs      the folder logs/ is in: logs/<run>.log and its twin, last_run.log, the page's .page_*.txt
  runtime   what only matters while something runs: the dashboard's link file, the program copy ▶ Start runs
On a Mac config, logs and runtime are all NXSPOOF's data folder (~/Library/Application Support/NXSPOOF), so there's
one place to look (the `data` shortcut); another platform would map them to its own habits (Linux: ~/.config/nxspoof,
~/.local/state/nxspoof, /run/user/...; Windows: %APPDATA%, %LOCALAPPDATA%). The tests and the simulator use one
temporary folder for all of them.
"""

import os

SETTINGS_FILE = "settings.json"

# Where each role's files live (absolute folders).
class Places(object):
    # program: the nx-spoof folder; config, logs, runtime: the folders for those roles.
    def __init__(self, program, config, logs, runtime):
        self.program, self.config, self.logs, self.runtime = program, config, logs, runtime

    # One data folder for config, logs and runtime (a Mac's data folder; a test's temporary folder).
    @classmethod
    def together(cls, program, data):
        return cls(program, data, data, data)

    # The settings file.
    def settings_file(self):
        return os.path.join(self.config, SETTINGS_FILE)

    # The docs pages (the dashboard's 📖 Help): docs/ in the nx-spoof folder.
    def docs_folder(self):
        return os.path.join(self.program, "docs")

    # The log files' folder: logs/ in the logs role.
    def logs_folder(self):
        return os.path.join(self.logs, "logs")

    # The role folders, each once (to create them).
    def folders(self):
        return sorted({self.config, self.logs, self.runtime})

    # True when config, logs and runtime are one folder (then it's "the data folder").
    def together_in_one(self):
        return len(self.folders()) == 1

    # For the logs: "<folder> (settings.json, logs/)", or each role's folder when they differ.
    def describe(self):
        if self.together_in_one():
            return "%s (settings.json, logs/)" % self.config
        return "settings %s, logs %s, runtime %s" % (self.config, self.logs, self.runtime)
