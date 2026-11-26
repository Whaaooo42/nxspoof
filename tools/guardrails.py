#!/usr/bin/env python3
"""
guardrails.py - the principles of the rebuild as checks (docs/how-its-built.html, Safeguards).

Each rule is a function given a project folder; it returns the places that break the rule (an empty
list = the rule holds). tests/test_guardrails.py runs every rule on the real project on every test run,
and tests/test_guardrails_bite.py plants one violation per rule in a temporary copy to prove each rule
really catches it.

Exceptions are listed by name, with their reason, in the ALLOWED tables below: a new exception has to
be added there on purpose, never slip in by accident. The import layers and the code conventions have
their own tests (test_layers.py, test_conventions.py); the complexity watchdog is tools/health.py.

  python3 -B tools/guardrails.py               # every rule: ✅ holds, or ❌ with the places and what to do
"""

import ast
import importlib.util
import os
import re
import sys
import sysconfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

from nxspoof.app import cli  # noqa: E402  (after the path is set)
from nxspoof.output.colors import TAG_COLORS  # noqa: E402

# Files allowed to print to the Terminal (everything else goes through the event bus), with the reason.
ALLOWED_PRINT = {
    "nxspoof/output/terminal.py": "it IS the Terminal output",
    "nxspoof/core/events.py": "last-resort report on stderr when an output itself breaks",
    "nxspoof/app/launch.py": "a settings.json typo is reported on the Terminal before the dashboard's event bus exists (moved from app/main.py)",
    "nxspoof/app/start.py": "nx_spoof.py start answers on the Terminal: a settings.json typo, another run active, this Mac not ready, then the run's one answer (D30)",
    "nxspoof/app/main.py": "hands the Terminal (sys.stdout) to the small commands that answer there",
    "nxspoof/app/commands.py": "the small commands answer on the Terminal (check, link, running)",
    "nxspoof/setup/checklist.py": "setup asks you questions",
    "nxspoof/macos/sharing.py": "its own small command line (python3 -m nxspoof.macos.sharing)",
    "nxspoof/macos/scprefs.py": "a helper NXSPOOF runs as its own fresh process (SystemConfiguration isn't fork-safe): its output IS its answer (owner's OK 2026-11-25)",
}
# Files allowed to write, move or delete files, with the reason (each hands what it creates back to you).
ALLOWED_FILE_WRITES = {
    "nxspoof/settings/store.py": "saves settings.json (tidy rewrite, through a checked temporary file)",
    "nxspoof/output/logfile.py": "the run's log file, last_run.log, keep_last",
    "nxspoof/output/terminal.py": "opens /dev/null when the Terminal window is gone",
    "nxspoof/dashboard/center.py": "the dashboard's address (dash.json: its port and process id, no secret) in the runtime folder; the port stays remembered when it closes",
    "nxspoof/output/runfile.py": "run.lock (one run at a time) and run.json (where the running run is), in the runtime folder; handed back to you (D30, owner's OK 2026-11-21: 'yes for the lock file')",
    "nxspoof/output/datafolder.py": "NXSPOOF's data folder: creates it, moves the old settings.json/logs over once, the 'data' shortcut, the program copy ▶ Start runs (owner's OK 2026-11-16: 'It just works')",
}
# Files allowed to name machine details (devices, addresses) - as detected DEFAULTS only, with the reason.
ALLOWED_MACHINE_NAMES = {
    "nxspoof/macos/netif.py": "defaults when detection finds nothing (en0, bridge0)",
    "nxspoof/macos/netservices.py": "the bridge's default fixed address (172.16.0.1), as the virtual cable check (macos/cable.py) expects",
    "nxspoof/settings/schema.py": "the bridge address presets offered in Settings (bridge.address choices, the same as macos/netservices.py's)",
}
# Modules outside macos/ allowed to import one of the system modules, with the reason.
ALLOWED_SYSTEM_IMPORTS = {
    ("nxspoof/app/signals.py", "signal"): "turns Ctrl+C / window closed / SIGTERM into a stop request",
}
ALLOWED_BUTTON_VARIANTS = {                  # the only words a dashboard button may add to "btn": what the button means
    "primary": "the main action of its place (Start, Save, Fix selected)",
    "danger": "undo, remove, stop: something you may regret",
    "add-console": "the dashed full-width row under the console cards",
}
SYSTEM_MODULES = ("subprocess", "ctypes", "pty", "signal")
INTERNET = re.compile(r"https?://[a-z0-9.-]+")
INTERNET_IN_DOCS = re.compile(r"(?:\b(?:src|href|action)\s*=\s*[\"']?|url\(\s*[\"']?|@import\s+[\"']?)\s*(?:https?:)?//[a-z0-9.-]+")
MACHINE_NAMES = re.compile(r"[\"'](en\d|bridge\d+|172\.16\.0\.1|192\.168\.2\.\d+)[\"']")
FORK_CALLS = {("os", "fork"), ("os", "forkpty"), ("pty", "fork")}    # a fork that carries on (no exec): nowhere in nxspoof/ (D33)
FORBIDDEN_OS_CALLS = {"chown", "lchown", "geteuid", "getuid", "setuid", "kill", "system", "popen", "fork", "execv", "execvp", "spawnv"}
FILE_CHANGES = {"remove", "unlink", "rename", "replace", "symlink", "makedirs", "mkdir", "rmdir", "chmod", "truncate", "open", "fdopen"}
SHUTIL_CHANGES = {"rmtree", "move", "copy", "copyfile", "copytree"}
STARTER_FOR = {"dashboard": "dashboard.command"}                            # commands the menu reaches another way than their name
OWN_TOOLS = ("menu", "health", "verify_run", "guardrails", "simulate", "run_tests")
REFERENCE_SKIP = {"logs", "last_run.log", "MEMO.md", "__pycache__", ".DS_Store", "tests", "docs", "scripts", "tools"}
PERSONAL_LIST = os.path.join("private", "personal.txt")   # the owner's own words to keep out (git ignores private/: the list never ships)
NOT_PUBLISHED = {".git", "private", "data", "NXSPOOF.app", "__pycache__", "logs", "last_run.log", "MEMO.md", ".DS_Store", "CLAUDE.local.md"}

# ##############################################################################################################################################################
# ##  READING THE PROJECT                                                                                                                                     ##
# ##############################################################################################################################################################

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# Every .py file under folders of a project, as (full path, path relative to the project).
def python_paths(root, *folders):
    for top in folders:
        for folder, _, files in os.walk(os.path.join(root, top)):
            for f in sorted(n for n in files if n.endswith(".py")):
                path = os.path.join(folder, f)
                yield path, os.path.relpath(path, root).replace(os.sep, "/")

# Every .py file under folders of a project, as (path relative to the project, parsed tree, text); unparsable files skipped.
def python_files(root, *folders):
    for path, rel in python_paths(root, *folders):
        text = read_text(path)
        try:
            yield rel, ast.parse(text), text
        except SyntaxError:
            continue

# Every file of the dashboard page and of the docs pages (the dashboard serves both), as (path relative to the project, text).
def web_files(root):
    for top in (os.path.join(root, "nxspoof", "dashboard", "web"), os.path.join(root, "docs")):
        for folder, _, files in os.walk(top):
            for f in sorted(files):
                if f.endswith((".html", ".css", ".js")):
                    path = os.path.join(folder, f)
                    yield os.path.relpath(path, root).replace(os.sep, "/"), read_text(path)
# "file:line what" for one node.
def at(rel, node, what):
    return "%s:%d %s" % (rel, getattr(node, "lineno", 0), what)

# Top-level module names imported by a tree (relative imports skipped), with their nodes.
def imported_tops(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node, a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            yield node, node.module.split(".")[0]

# True if a top-level module name is part of Python's standard library (or builtin).
def is_stdlib(name):
    if name in sys.builtin_module_names or name in getattr(sys, "stdlib_module_names", ()):
        return True
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None or spec.origin in (None, "built-in", "frozen"):
        return spec is not None
    stdlib = os.path.realpath(sysconfig.get_paths()["stdlib"])
    origin = os.path.realpath(spec.origin)
    return origin.startswith(stdlib + os.sep) and "site-packages" not in origin

# ##############################################################################################################################################################
# ##  THE RULES (EACH: project folder -> places that break it)                                                                                                ##
# ##############################################################################################################################################################

# Outside macos/: no subprocess, ctypes..., no os.chown / geteuid / kill.
def system_calls_outside_macos(root):
    problems = []
    for rel, tree, _ in python_files(root, "nxspoof"):
        if rel.startswith("nxspoof/macos/"):
            continue
        problems += [at(rel, n, "imports %s" % top) for n, top in imported_tops(tree)
                     if top in SYSTEM_MODULES and (rel, top) not in ALLOWED_SYSTEM_IMPORTS]
        problems += [at(rel, n, "os.%s()" % n.func.attr) for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and isinstance(n.func.value, ast.Name) and n.func.value.id == "os" and n.func.attr in FORBIDDEN_OS_CALLS]
    return problems

# Nowhere in nxspoof/, macos/ included: a fork that carries on (os.fork, os.forkpty, pty.fork). macOS supports only exec after
# a fork, and from macOS 26.1 its frameworks crash in a forked child (FB21364061): every process starts fresh (D33).
def forks(root):
    problems = []
    for rel, tree, _ in python_files(root, "nxspoof"):
        problems += [at(rel, n, "%s.%s()" % (n.func.value.id, n.func.attr)) for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and (n.func.value.id, n.func.attr) in FORK_CALLS]
    return problems

# macos/ returns values or raises: no event bus, no outputs, no log calls.
def macos_logs(root):
    problems = []
    for rel, tree, _ in python_files(root, "nxspoof/macos"):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(("nxspoof.core.events", "nxspoof.output")):
                problems.append(at(rel, node, "imports %s" % node.module))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("log", "say", "fact"):
                problems.append(at(rel, node, ".%s()" % node.func.attr))
    return problems

# Device names and addresses written into the code (outside the files that hold the detected defaults).
def machine_names(root):
    problems = []
    for rel, _, text in python_files(root, "nxspoof"):
        if rel in ALLOWED_MACHINE_NAMES:
            continue
        for i, line in enumerate(text.split("\n"), 1):
            if MACHINE_NAMES.search(line.split("#", 1)[0]):
                problems.append("%s:%d %s" % (rel, i, line.strip()))
    return problems

# print() / sys.stdout / sys.stderr outside the files whose job it is.
def prints(root):
    problems = []
    for rel, tree, _ in python_files(root, "nxspoof"):
        if rel in ALLOWED_PRINT:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                problems.append(at(rel, node, "print()"))
            if isinstance(node, ast.Attribute) and node.attr in ("stdout", "stderr") and isinstance(node.value, ast.Name) and node.value.id == "sys":
                problems.append(at(rel, node, "sys.%s" % node.attr))
    return problems

# What a call does to files, as text ("open(..., 'w')", "os.remove()"), or None if it only reads.
def file_change(call):
    f = call.func
    if isinstance(f, ast.Name) and f.id == "open":
        modes = [a.value for a in call.args[1:2] if isinstance(a, ast.Constant)] + \
                [k.value.value for k in call.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
        writing = [m for m in modes if set(str(m)) & set("wax+")]
        return "open(..., %r)" % writing[0] if writing else None
    if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
        return None
    if (f.value.id == "os" and f.attr in FILE_CHANGES) or (f.value.id == "shutil" and f.attr in SHUTIL_CHANGES):
        return "%s.%s()" % (f.value.id, f.attr)
    return None

# Files written, moved or deleted outside their owner modules.
def file_writes(root):
    problems = []
    for rel, tree, _ in python_files(root, "nxspoof"):
        if rel not in ALLOWED_FILE_WRITES:
            problems += [at(rel, n, file_change(n)) for n in ast.walk(tree) if isinstance(n, ast.Call) and file_change(n)]
    return problems

# Log tags (first argument of .log("TAG", ...)) that have no Terminal color.
def tags_without_color(root):
    problems = []
    for rel, tree, _ in python_files(root, "nxspoof"):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "log" and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                tag = node.args[0].value
                if tag and tag not in TAG_COLORS:
                    problems.append(at(rel, node, "tag %s" % tag))
    return problems

# Imports that aren't Python's standard library (or NXSPOOF's own code).
def non_stdlib_imports(root):
    problems = []
    for rel, tree, _ in python_files(root, "nxspoof", "tools", "tests"):
        if rel.startswith("tests/fixtures/"):                  # the frozen previous code imports its own old modules
            continue
        for node, top in imported_tops(tree):
            if top not in ("nxspoof", "tests") + OWN_TOOLS and not is_stdlib(top):
                problems.append(at(rel, node, "imports %s" % top))
    return problems

# Files that don't parse as Python 3.9 (Apple's /usr/bin/python3).
def newer_python_syntax(root):
    problems = []
    for path, rel in python_paths(root, "nxspoof", "tools", "tests"):
        try:
            ast.parse(read_text(path), feature_version=(3, 9))
        except SyntaxError as e:
            problems.append("%s:%s %s" % (rel, e.lineno, e.msg))
    return problems

# Dashboard scripts that turn strings into HTML or code (innerHTML, document.write, eval...).
def page_html_from_strings(root):
    rx = re.compile(r"\.(innerHTML|outerHTML)\b|insertAdjacentHTML|document\.write|\beval\(|new Function\(")
    return ["%s:%d %s" % (rel, i, l.strip()) for rel, text in web_files(root) if rel.endswith(".js")
            for i, l in enumerate(text.split("\n"), 1) if rx.search(l.split("//", 1)[0])]

# Inline scripts, on...= handlers, or files from the internet in the dashboard page.
def page_inline_or_internet(root):
    problems = []
    for rel, text in web_files(root):
        if rel.endswith(".html"):
            problems += ["%s inline <script>" % rel for _ in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>", text)]
            problems += ["%s %s" % (rel, m.group(0).strip()) for m in re.finditer(r"\son[a-z]+\s*=", text)]
        found = INTERNET_IN_DOCS if rel.startswith("docs/") else INTERNET    # the docs show web addresses as text (log lines): links and loads count
        for m in found.finditer(text):
            if "localhost" not in m.group(0) and "127.0.0.1" not in m.group(0):
                problems.append("%s %s" % (rel, m.group(0)))
    return problems

# Dashboard modules importing anything but each other (./name.js).
def page_imports(root):
    problems = []
    for rel, text in web_files(root):
        for m in re.finditer(r"^\s*import .* from [\"']([^\"']+)[\"']", text, re.M):
            if not re.match(r"^\./[a-z]+\.js$", m.group(1)):
                problems.append("%s imports %s" % (rel, m.group(1)))
    return problems

# Dashboard buttons that leave the one style: a class word besides "btn" and ALLOWED_BUTTON_VARIANTS (js/, the page), a
# .btn.<word> rule for another word, or a rule that resizes buttons somewhere (font-size, padding, height on a ".btn" selector).
def button_styles(root):
    problems = []
    for rel, text in web_files(root):
        if rel.endswith((".js", ".html")):
            problems += button_classes(rel, text)
        elif rel.endswith(".css"):
            problems += button_rules(rel, text)
    return problems

# Class words added to "btn" in a script or the page that aren't ALLOWED_BUTTON_VARIANTS.
def button_classes(rel, text):
    problems = []
    for m in re.finditer(r"[\"']btn((?: [\w-]+)*)[\"']", text):
        extra = [w for w in m.group(1).split() if w not in ALLOWED_BUTTON_VARIANTS]
        if extra:
            problems.append("%s:%d button class '%s'" % (rel, text.count("\n", 0, m.start()) + 1, " ".join(extra)))
    return problems

# Stylesheet rules on .btn that add a variant of their own, or resize buttons in one place.
def button_rules(rel, text):
    problems = []
    for m in re.finditer(r"([^{}]*\.btn\b[^{}]*)\{([^}]*)\}", text):
        selector, line = " ".join(m.group(1).split("*/")[-1].split()), text.count("\n", 0, m.start(1)) + 1
        problems += ["%s:%d .btn.%s" % (rel, line, w) for w in re.findall(r"\.btn\.([\w-]+)", selector) if w not in ALLOWED_BUTTON_VARIANTS]
        if selector != ".btn" and re.search(r"(?:^|[;\s])(font-size|font|padding|height|line-height)\s*:", m.group(2)):
            problems.append("%s:%d '%s' resizes buttons" % (rel, line, selector))
    return problems

# Commands of nx_spoof.py without a row in the menu (tools/menu.py).
def commands_missing_in_menu(root):
    menu = read_text(os.path.join(root, "tools", "menu.py"))
    missing = []
    for name, _ in cli.COMMANDS:
        needle = STARTER_FOR.get(name, '"%s"' % name)       # the script the menu runs, or the quoted command name
        if needle not in menu:
            missing.append("command '%s'" % name)
    return missing

# The value assigned to NAME at the top of a module, read without running it (None if there's no such assignment).
def module_constant(path, name):
    for node in ast.parse(read_text(path)).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None

# The platform contract (D23): app/system.py's System methods and its CONTRACT must list the same methods (the docs' table is
# written from CONTRACT: tools/docs.py).
def platform_contract_drift(root):
    path = os.path.join(root, "nxspoof", "app", "system.py")
    classes = [n for n in ast.parse(read_text(path)).body if isinstance(n, ast.ClassDef) and n.name == "System"]
    methods = {f.name for f in classes[0].body if isinstance(f, ast.FunctionDef) and not f.name.startswith("_")} if classes else set()
    listed = {name for _, names in (module_constant(path, "CONTRACT") or ()) for name in names}
    return (["System.%s isn't in CONTRACT" % m for m in sorted(methods - listed)] +
            ["CONTRACT names %s, which System doesn't have" % m for m in sorted(listed - methods)])
# This Mac's tasks (app/tasks.py TASKS) whose command isn't an nx_spoof.py command: every button has a Terminal twin (D23).
def tasks_without_command(root):
    tasks = module_constant(os.path.join(root, "nxspoof", "app", "tasks.py"), "TASKS")
    if tasks is None:
        return ["nxspoof/app/tasks.py has no TASKS table"]
    commands = {name for name, _ in cli.COMMANDS}
    return ["task '%s' runs '%s', which isn't an nx_spoof.py command" % (kind, words[0]) for kind, (words, _, _) in sorted(tasks.items())
            if words[0] not in commands]

# The docs pages' generated parts (tools/docs.py check): out of date, unknown, unclosed; pages missing from its PAGES or from
# docs/; settings and options without help, modules without a header line.
def generated_parts_stale(root):
    spec = importlib.util.spec_from_file_location("docs_for_guardrails_%d" % id(root), os.path.join(root, "tools", "docs.py"))
    docs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs)
    return docs.check(root)
# Files at the top, in scripts/ and tools/ missing from the files table of docs/reference.html.
def files_missing_in_reference(root):
    doc = read_text(os.path.join(root, "docs", "reference.html"))
    names = [n for n in os.listdir(root) if n not in REFERENCE_SKIP and not n.startswith(".")]
    names += ["scripts/" + n for n in os.listdir(os.path.join(root, "scripts")) if not n.startswith(".")]
    names += ["tools/" + n for n in os.listdir(os.path.join(root, "tools")) if not n.startswith((".", "__"))]
    return sorted(n for n in names if "<code>%s</code>" % n not in doc and "<code>%s/</code>" % n not in doc)
# The words in private/personal.txt (one per line, # for comments), or [] where there's no such list (a public copy).
def personal_words(root):
    try:
        lines = read_text(os.path.join(root, PERSONAL_LIST)).splitlines()
    except OSError:
        return []
    return [w.strip() for w in lines if w.strip() and not w.strip().startswith("#")]

# Every place a file git would publish holds one of those words (whole words, any case): "file:line: word".
def personal_data(root):
    words = personal_words(root)
    if not words:
        return []
    rx = re.compile("|".join(r"(?<!\w)%s(?!\w)" % re.escape(w) for w in words), re.I)
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in NOT_PUBLISHED)
        for name in sorted(f for f in files if f not in NOT_PUBLISHED):
            path = os.path.join(folder, name)
            try:
                text = read_text(path)
            except (OSError, UnicodeDecodeError):
                continue                                 # not text (an icon, a picture)
            for n, line in enumerate(text.splitlines(), 1):
                m = rx.search(line)
                if m:
                    found.append("%s:%d: %s" % (os.path.relpath(path, root), n, m.group(0)))
    return found
# True for a call to registry.name(...) / registry.label(...) (any object called registry, or self.registry / ctx.registry).
def is_name_call(node):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("name", "label")):
        return False
    owner = node.func.value
    return (isinstance(owner, ast.Name) and owner.id == "registry") or (isinstance(owner, ast.Attribute) and owner.attr == "registry")

# How a piece of code writes a console name as plain text ("% formatting", "+", "an f-string", "join()"), or None.
def plain_name_use(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        operands = (node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]) + [node.left]
        if any(is_name_call(n) for n in operands):
            return "% formatting" if isinstance(node.op, ast.Mod) else "+"
    if isinstance(node, ast.FormattedValue) and is_name_call(node.value):
        return "an f-string"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
        if any(is_name_call(n) for a in node.args for n in ast.walk(a)):
            return "join()"
    return None

# Console names written as plain text (% formatting, +, f-strings, join) instead of compose() from core/text.py.
def unmarked_console_names(root):
    return [at(rel, n, "console name in %s" % plain_name_use(n)) for rel, tree, _ in python_files(root, "nxspoof")
            for n in ast.walk(tree) if plain_name_use(n)]

# Code past one of the watchdog's hard limits (tools/health.py LIMITS), unless accepted with a reason.
def past_watchdog_limits(root):
    spec = importlib.util.spec_from_file_location("health_for_guardrails", os.path.join(root, "tools", "health.py"))
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    baseline = health.load_baseline(os.path.join(root, "tools", "health_baseline.json"))
    return ["%s: %s" % (where, text) for kind, where, text in health.findings(health.measure(root), baseline) if kind == "limit"]

# (rule, what it protects, what to do when it breaks, the check).
RULES = [
    ("only macos/ talks to the Mac", "no subprocess, ctypes or system calls outside nxspoof/macos/ (signals: app/signals.py)",
     "add a function to macos/ (it can take a fake runner in tests) and call that", system_calls_outside_macos),
    ("no fork that carries on (D33)", "every NXSPOOF process starts fresh: macOS supports only exec after a fork, and its frameworks crash in a forked child from macOS 26.1",
     "start a fresh process instead (macos/process.py started); if doing it right truly needs a fork, bring it to the owner: the golden rule comes first", forks),
    ("macos/ never logs", "macos/ returns values or raises", "return a value or raise, and let the caller log it", macos_logs),
    ("no hard-coded machine details", "devices and addresses are detected", "detect them (macos/netif.py) or use its defaults", machine_names),
    ("messages go through the event bus", "print only where printing is the job (ALLOWED_PRINT)",
     "use bus.log(tag, technical, casual=...) or bus.say(casual), or add the file to ALLOWED_PRINT with its reason", prints),
    ("files have one owner", "settings via settings/store.py, logs via output/logfile.py (ALLOWED_FILE_WRITES)",
     "give a new kind of file an owner module that hands it back to you (macos.user.give_to_user)", file_writes),
    ("console names are marked", "lines name consoles with compose() (core/text.py), so outputs color exactly the name",
     "build the line with compose(\"...%s...\", registry.name(mac)); for plain text on purpose, wrap it in str(...)", unmarked_console_names),
    ("every log tag has a color", "tags are in TAG_COLORS (Terminal colors, dashboard column)",
     "add the tag to TAG_COLORS in nxspoof/output/colors.py", tags_without_color),
    ("standard library only", "nothing to install on a new Mac", "use Python's standard library", non_stdlib_imports),
    ("Apple's Python 3.9", "every file parses as Python 3.9", "write it the 3.9 way (no match statements...)", newer_python_syntax),
    ("page never turns text into HTML", "dashboard and docs text is inserted as plain text", "build elements with el(tag, cls, text) from js/core.js",
     page_html_from_strings),
    ("page: no inline scripts, no internet", "the dashboard and the docs pages run only their own script files and work offline",
     "move scripts into their own file (js/, docs/docs.js), keep every file local", page_inline_or_internet),
    ("page modules import only each other", "js/ modules import ./other.js only", "import ./name.js", page_imports),
    ("one button style", "every dashboard button is .btn, one size; only ALLOWED_BUTTON_VARIANTS say what it means",
     "use \"btn\" (+ primary or danger), and size it nowhere else", button_styles),
    ("every command has a menu row", "new commands are one key away in menu.command", "add a row to MENU in tools/menu.py",
     commands_missing_in_menu),
    ("the platform contract is complete", "app/system.py's CONTRACT lists every System method (the docs' table is written from it)",
     "add the method to CONTRACT, in its group", platform_contract_drift),
    ("every dashboard task has a command", "the two doors stay the same (D23): each This Mac button runs an nx_spoof.py command",
     "make the task run a command of cli.COMMANDS (add the command first, with its menu row)", tasks_without_command),
    ("every file is in the reference", "docs/reference.html's Files table lists every file at the top, in scripts/ and tools/",
     "add a row to that table", files_missing_in_reference),
    ("nothing personal in the repository", "no word from the owner's private/personal.txt in a file git would publish (the list never ships: a "
     "public copy has nothing to check)", "take it out (tests use made-up values), or keep that file out of git", personal_data),
    ("the docs' generated parts are current", "the tables that mirror the code (settings, options, commands, menu, modules, contract, "
     "rules) are written from it; every setting and option has help, every module a header line, every page its place in PAGES",
     "python3 -B tools/docs.py --write (and add any missing help text, header line, or PAGES row)", generated_parts_stale),
    ("nothing past the watchdog's limits", "tools/health.py hard limits", "split it, or accept it: python3 -B tools/health.py --accept \"why\"",
     past_watchdog_limits),
]

# Every rule on a project folder -> [(rule, protects, fix, problems)].
def check_all(root=ROOT):
    return [(name, protects, fix, func(root)) for name, protects, fix, func in RULES]

# Command line: every rule, ✅ or ❌ with its places and what to do; exit code 1 if any rule breaks.
def main():
    results = check_all()
    for name, protects, fix, problems in results:
        print("%s %s  (%s)" % ("✅" if not problems else "❌", name, protects))
        for p in problems[:10]:
            print("      %s" % p)
        if problems:
            print("      -> %s" % fix)
    broken = sum(1 for r in results if r[3])
    print("\n%s" % ("All %d rules hold." % len(results) if not broken else "%d rule(s) broken." % broken))
    return 1 if broken else 0

if __name__ == "__main__":
    sys.exit(main())
