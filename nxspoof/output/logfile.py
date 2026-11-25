"""
nxspoof.output.logfile - the per-run log files: the text log (always technical, never colored) and its
structured twin, one pair per run.

create_run_log() makes logs/<date>_<time>.log (and, for a real run, logs/<date>_<time>.jsonl), points
last_run.log at it, and removes the oldest runs beyond "logs.keep_last" (with their twins). start_output() is the
file a run started from the dashboard writes its Terminal output to (logs/.page_start.txt).
  LogFileSink   writes '[HH:MM:SS] TAG   text' for every log line (casual-only lines and facts never go
                to the text file): for you to read.
  RecordSink    writes every event, lines and facts, as one JSON object per line (kind, level, tag,
                text, console, data): for programs to read (features/runlog.py: the log pane's run picker and the
                run checker), so nothing ever has to parse the text and its wording can change freely.
File ownership is handed back to the real user through the callbacks the app passes in (macos.user),
since this package doesn't talk to macOS itself.
"""

import json
import os
import re
import stat
import time

from nxspoof.output import colors as c

LAST_RUN = "last_run.log"
OLD_SINGLE_LOG = "before-logs-folder.log"
RUN_FILE_RX = re.compile(r"^(\d{4})-(\d\d)-(\d\d)_(\d\d)-(\d\d)-(\d\d)(-dryrun)?\.log$")   # real runs (date, time, -dryrun): setup / dashboard logs never count for keep_last
LOG_FILE_RX = re.compile(r"^\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d(-dryrun)?(?:-([a-z]+))?\.log$")              # any log this module names: a run's, or a labeled one (setup, dashboard...)
RECORDS_EXT = ".jsonl"
START_OUTPUT = ".page_start.txt"                                                   # a page-started run's own Terminal output
TASK_OUTPUT = ".page_task.txt"                                                     # the same for This Mac's tasks and 🧹 Clean up

# The structured twin of a log file: logs/<run>.log -> logs/<run>.jsonl.
def records_path(log_path):
    return os.path.splitext(log_path)[0] + RECORDS_EXT

# The log file of a structured twin: logs/<run>.jsonl -> logs/<run>.log.
def log_path_of(records):
    return os.path.splitext(records)[0] + ".log"

# Create this run's log file and return its path.
#   folder: "logs" (relative to project_dir) or an absolute path; keep_last: 0 keeps everything;
#   give(path) / give_link(path): hand a file / a symbolic link back to the real user;
#   label: added to the name (e.g. "setup"); a labeled log leaves last_run.log on the latest real run.
#   A real run (no label) also gets its structured twin (records_path), for a RecordSink; so does a labeled log with
#   records=True (setup, uninstall, cleanup: the dashboard follows them through it).
def create_run_log(project_dir, folder="logs", keep_last=0, dry_run=False, now=None, give=None, give_link=None,
                   label=None, records=False):
    give = give or (lambda path: None)
    give_link = give_link or (lambda path: None)
    folder = folder or "logs"
    folder = folder if os.path.isabs(folder) else os.path.join(project_dir, folder)
    os.makedirs(folder, exist_ok=True)
    give(folder)
    last = os.path.join(project_dir, LAST_RUN)
    if os.path.isfile(last) and not os.path.islink(last):        # one-time move of the old single growing log
        os.rename(last, os.path.join(folder, OLD_SINGLE_LOG))
    name = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(now)) + ("-dryrun" if dry_run else "") + ("-" + label if label else "")
    path = os.path.join(folder, name + ".log")
    for created in [path] + ([records_path(path)] if records or not label else []):
        open(created, "a").close()
        os.chmod(created, 0o644)
        give(created)
    if not label:
        try:
            if os.path.islink(last) or os.path.exists(last):
                os.remove(last)
            os.symlink(os.path.relpath(path, project_dir), last)
            give_link(last)
        except OSError:
            pass
    prune_runs(folder, keep_last)
    return path

# Save a problem report (app/reporting.py) as logs/reports/<time>-report.md, handed to you; returns its path.
def save_report(project_dir, text, folder="logs", now=None, give=None):
    give = give or (lambda path: None)
    folder = folder if os.path.isabs(folder) else os.path.join(project_dir, folder)
    reports = os.path.join(folder, "reports")
    os.makedirs(reports, exist_ok=True)
    give(reports)
    path = os.path.join(reports, time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(now)) + "-report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    give(path)
    return path

# The file a run started from the dashboard's ▶ Start writes its own Terminal output to (it has no Terminal window):
# logs/.page_start.txt (name: TASK_OUTPUT for This Mac's tasks), emptied for each start and owned by you (the run, as root, only appends). Mostly a copy of the run's
# log; it matters when a run can't even open its log (the dashboard shows its last lines then). Returns its path.
def start_output(project_dir, folder="logs", name=START_OUTPUT):
    folder = folder or "logs"
    folder = folder if os.path.isabs(folder) else os.path.join(project_dir, folder)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8"):
        pass
    return path

# The start's output file, open for the run's own output (appended; its Terminal output goes there in the background).
def open_output(path):
    return open(path, "a", encoding="utf-8")

# The last lines of a file (for the dashboard when a started run didn't show up); [] if it can't be read.
def tail(path, lines=12):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n") for l in f.readlines()[-lines:]]
    except OSError:
        return []

# Remove the oldest run logs (and their twins) so only the newest `keep` remain (0 keeps everything); returns the removed names.
def prune_runs(folder, keep):
    keep = int(keep or 0)
    if keep <= 0:
        return []
    runs = sorted(f for f in os.listdir(folder) if RUN_FILE_RX.match(f))
    removed = []
    for old in runs[:-keep]:
        try:
            os.remove(os.path.join(folder, old))
            removed.append(old)
        except OSError:
            continue
        try:
            os.remove(records_path(os.path.join(folder, old)))
        except OSError:
            pass
    return removed

# A bus sink appending technical lines to the run's log file.
class LogFileSink(object):
    # path: the file from create_run_log (None = write nothing, e.g. before it exists).
    def __init__(self, path=None):
        self.path = path

    # Called by the bus for every event.
    def __call__(self, event):
        lines = self.lines(event)
        if not lines or not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("".join(l + "\n" for l in lines))
        except OSError:
            pass

    # The file lines for one event: '[HH:MM:SS] line' plus its details (nothing for casual-only lines and facts).
    def lines(self, event):
        line = event.technical_line()
        if line is None:
            return []
        stamp = "[%s]" % time.strftime("%H:%M:%S", time.localtime(event.ts))
        return ["%s %s" % (stamp, c.strip(l)) for l in [line] + event.detail_lines()]

# Append one event to a run's log and its records twin from another process running as root (the keeper, app/keeper.py,
# after a run that ended without cleaning up). Only to files that already exist, are regular files (never through a
# link, never a pipe that would make root wait) and belong to owner_uid: the run that named them can't point root at
# anything else. True if both were written.
def append_as_root(log_path, owner_uid, event):
    written = 0
    for path, text in ((log_path, "".join(l + "\n" for l in LogFileSink().lines(event))),
                       (records_path(log_path), json.dumps(record(event), ensure_ascii=False, sort_keys=True, default=str) + "\n")):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)     # a pipe with no reader fails at once
        except OSError:
            continue
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_uid == owner_uid:
                f.write(text)
                written += 1
    return written == 2

# One event as a structured record: what it is (kind, level), what it said (tag, tech, casual, details),
# which console (mac), its data, and its marks (console names and outcome words, as [start, end, who] in the
# line "TAG   text"; casual_marks: the same in the casual line), for coloring a past run's log; empty parts left out.
def record(event):
    out = {"ts": round(event.ts, 3), "kind": event.kind, "level": event.level}
    for key, value in (("tag", event.tag), ("tech", event.tech), ("casual", event.casual), ("mac", event.mac),
                       ("details", event.details), ("data", event.data), ("marks", event.technical_names()),
                       ("casual_marks", event.casual_names)):
        if value:
            out[key] = value
    return out

# A bus sink appending every event (lines and facts) to the run's structured log, one JSON object per line.
class RecordSink(object):
    # path: the twin from create_run_log(records=True) (None = write nothing, e.g. before it exists).
    def __init__(self, path=None):
        self.path = path

    # Called by the bus for every event.
    def __call__(self, event):
        if not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record(event), ensure_ascii=False, sort_keys=True, default=str) + "\n")
        except (OSError, ValueError):
            pass
