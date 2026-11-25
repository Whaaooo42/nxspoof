#!/usr/bin/env python3
"""
docs.py - the docs pages' generated parts, written from the code (docs/*.html, owner's OK 2026-11-22: "I trust you with it").

Every table in the docs that mirrors the code is written here, between two markers in the page,
  <!-- generated: NAME -->  ...  <!-- /generated -->
so it can't drift from the code: the navigation every page shares and the home page's list (PAGES below), the settings and
the console fields (settings/schema.py), the options and the commands (app/cli.py), the menu (tools/menu.py MENU), the
modules (each file's own header line), the platform contract (app/system.py CONTRACT, each method's comment), the
guardrails (tools/guardrails.py RULES) and the watchdog's levels (tools/health.py LIMITS). The guardrail "the generated
parts are current" runs check() on every test run. Everything else on the pages is written by hand.

  python3 -B tools/docs.py            # what's out of date (exit code 1 if anything is)
  python3 -B tools/docs.py --write    # rewrite the generated parts

Sections: PAGES · READING THE CODE · THE GENERATED PARTS · CHECK AND WRITE
"""

import ast
import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

from nxspoof.app import cli  # noqa: E402  (after the path is set)
from nxspoof.settings import schema  # noqa: E402

# ##############################################################################################################################################################
# ##  PAGES                                                                                                                                                   ##
# ##############################################################################################################################################################

DOCS = "docs"
# (file, title, group, what it's for): every page, in the navigation's order (group None: the home page).
PAGES = [
    ("index.html", "Home", None, "What NXSPOOF is, and where to go next"),
    ("guide.html", "Guide", "Use it", "Start, stop, the dashboard, a friend's Switch: the everyday things"),
    ("troubleshooting.html", "Troubleshooting", "Use it", "Something's wrong: type what you see, find the fix"),
    ("setup.html", "Setup", "Use it", "A new Mac, step by step (the dashboard does most of it)"),
    ("how-it-works.html", "How it works", "Understand it", "The trick, the three walls, and the four parts of NXSPOOF"),
    ("connection-test.html", "The connection test", "Understand it", "What happens, packet by packet, until a Switch says connected"),
    ("two-doors.html", "The two doors", "Understand it", "NXSPOOF.app and the menu; how a run starts, stops, and what runs as root"),
    ("reference.html", "Reference", "Look it up", "Settings, options, commands, log lines, the dashboard's buttons, every file"),
    ("real-runs.html", "Real runs", "Look it up", "The checklist on the real Mac, after a big change"),
    ("how-its-built.html", "How it's built", "Build it", "The layers, the rules, every module, the platform contract"),
    ("decisions.html", "Decisions", "Build it", "D1 to D30: what was decided, and why"),
]
MARK = re.compile(r"<!-- generated: ([a-z-]+) -->\n(.*?)<!-- /generated -->", re.S)
OPEN_MARK = re.compile(r"<!-- generated: ([a-z-]+) -->")
APPLY_WORDS = {"live": "now", "restart": "next start"}
MENU_TITLE = {"main": "🎮 NXSPOOF"}

# HTML-safe text.
def esc(text):
    return html.escape(str(text), quote=False)

# ##############################################################################################################################################################
# ##  READING THE CODE                                                                                                                                        ##
# ##############################################################################################################################################################

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# A module's syntax tree.
def tree_of(path):
    return ast.parse(read_text(path))

# The value assigned to NAME at the top of a module, read without running it (None if there's no such assignment).
def module_constant(path, name):
    for node in tree_of(path).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None

# The text after "name - " on the first line of a module's header, without a closing colon ("" when there's none).
def header_line(path):
    doc = ast.get_docstring(tree_of(path)) or ""
    first = doc.strip().split("\n", 1)[0]
    return first.split(" - ", 1)[1].rstrip(":").strip() if " - " in first else ""

# The package's modules: [(path relative to the project, header line)], package by package.
def modules(root):
    out = []
    for folder, dirs, files in os.walk(os.path.join(root, "nxspoof")):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "__")) and d != "web")
        for name in sorted(files):
            if name.endswith(".py") and name != "__init__.py":
                path = os.path.join(folder, name)
                out.append((os.path.relpath(path, root), header_line(path)))
    return out

# The one-line comment right above each method of a class: {method: comment}.
def method_comments(path, class_name):
    lines = read_text(path).split("\n")
    out = {}
    for node in tree_of(path).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for f in node.body:
                if isinstance(f, ast.FunctionDef):
                    first = min([f.lineno] + [d.lineno for d in f.decorator_list])
                    above = lines[first - 2].strip() if first >= 2 else ""
                    out[f.name] = above[1:].strip() if above.startswith("#") else ""
    return out

# A literal argument of a call, or None (a lambda, a function's name...).
def literal(node):
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None

# The menu's rows, read from tools/menu.py without running it: [(screen, label, sub-menu or None, needs a password, detail, when)].
def menu_rows(root):
    path = os.path.join(root, "tools", "menu.py")
    for node in tree_of(path).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "MENU" for t in node.targets):
            rows = []
            for call in node.value.elts:
                kw = {k.arg: literal(k.value) for k in call.keywords}
                screen, label, action = (literal(a) for a in call.args[:3])
                rows.append((screen, label, action if isinstance(action, str) else None, bool(kw.get("pw")), kw.get("detail"), kw.get("when")))
            return rows
    return []

# The guardrails' rules, read from tools/guardrails.py: [(rule, what it protects)].
def guardrail_rules(root):
    for node in tree_of(os.path.join(root, "tools", "guardrails.py")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "RULES" for t in node.targets):
            return [(literal(t.elts[0]), literal(t.elts[1])) for t in node.value.elts]
    return []

# ##############################################################################################################################################################
# ##  THE GENERATED PARTS                                                                                                                                     ##
# ##############################################################################################################################################################

# The navigation, with this page marked.
def gen_nav(root, page):
    out, group = ['<nav class="side" aria-label="The NXSPOOF docs">', '<a class="brand" href="index.html">🎮 NXSPOOF</a>'], None
    for name, title, in_group, _ in PAGES:
        if in_group != group:
            group = in_group
            out.append("<b>%s</b>" % esc(group))
        here = ' class="on" aria-current="page"' if name == page else ""
        out.append('<a href="%s"%s>%s</a>' % (name, here, esc(title)))
    return "\n".join(out + ["</nav>"])

# The home page's list of pages, by group.
def gen_pages(root, page):
    out, group = [], None
    for name, title, in_group, what in PAGES[1:]:
        if in_group != group:
            out += (["</div>"] if group else []) + ["<h3>%s</h3>" % esc(in_group), '<div class="cards">']
            group = in_group
        out.append('<a class="card link" href="%s"><b>%s</b><span>%s</span></a>' % (name, esc(title), esc(what)))
    return "\n".join(out + ["</div>"])

# What values a field accepts, in words.
def allowed(f):
    if f.choices:
        return ", ".join("<code>%s</code>" % esc(c) for c in f.choices)
    if f.minimum is not None or f.maximum is not None:
        low = "" if f.minimum is None else "from %s" % f.minimum
        high = "" if f.maximum is None else "to %s" % f.maximum
        return esc(" ".join(w for w in (low, high) if w))
    return esc(schema.KIND_WORDS[f.kind])

# A value as settings.json writes it.
def json_value(value):
    return "true" if value is True else "false" if value is False else '"%s"' % value if isinstance(value, str) else "" if value is None else str(value)

# The settings table (settings/schema.py FIELDS).
def gen_settings(root, page):
    out = ['<div class="scroll"><table>', "<tr><th>Setting</th><th>Default</th><th>What it does</th><th>Accepts</th><th>Applies</th></tr>"]
    for f in schema.FIELDS:
        out.append("<tr><td><code>%s</code></td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            esc(f.name), esc(json_value(f.default)), esc(f.help), allowed(f), APPLY_WORDS[f.apply]))
    return "\n".join(out + ["</table></div>"])

# The console fields table (settings/schema.py CONSOLE_FIELDS).
def gen_console_fields(root, page):
    out = ['<div class="scroll"><table>', "<tr><th>Field</th><th>What it does</th><th>Accepts</th></tr>"]
    for f in schema.CONSOLE_FIELDS:
        out.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (esc(f.key), esc(f.help), allowed(f)))
    return "\n".join(out + ["</table></div>"])

# How an option is typed: the flag, and its value when it takes one.
def option_usage(o):
    if o.choices:
        return "%s %s" % (o.flag, "|".join(o.choices))
    return o.flag + (" N" if o.kind == "int" else " …" if o.kind == "text" else "")

# The options table (app/cli.py OPTIONS).
def gen_options(root, page):
    out = ['<div class="scroll"><table>', "<tr><th>Option</th><th>What it does</th></tr>"]
    for o in cli.OPTIONS:
        out.append("<tr><td><code>%s</code></td><td>%s</td></tr>" % (esc(option_usage(o)), esc(o.help)))
    return "\n".join(out + ["</table></div>"])

# The commands table (app/cli.py COMMANDS).
def gen_commands(root, page):
    out = ['<div class="scroll"><table>', "<tr><th>Command</th><th>What it does</th></tr>"]
    for name, what in cli.COMMANDS:
        out.append("<tr><td><code>nx_spoof.py %s</code></td><td>%s</td></tr>" % (esc(name), esc(what)))
    return "\n".join(out + ["</table></div>"])

# The menu, screen by screen, drawn like the Terminal (tools/menu.py MENU and SCREENS).
def gen_menu(root, page):
    rows = menu_rows(root)
    screens = module_constant(os.path.join(root, "tools", "menu.py"), "SCREENS") or {}
    out = ['<div class="menu-screens">']
    for screen in ["main"] + [s for s in screens if any(r[0] == s for r in rows)]:
        title = MENU_TITLE.get(screen) or "🎮 NXSPOOF › %s" % screens[screen][0]
        lines = ["<b>%s</b>" % esc(title), ""]
        mine = [r for r in rows if r[0] == screen]
        width = max(len(r[1]) for r in mine)
        for n, (_, label, sub, pw, detail, when) in enumerate(mine, 1):          # as tools/menu.py row_text() draws it
            detail = screens[sub][1] if sub else detail
            row = "<b>%d</b>  %s %s" % (n, esc(label.ljust(width)), "🔑" if pw else "  ")
            row = '<span class="d">%s</span>' % row.replace("<b>", "").replace("</b>", "") if when == "running" else row
            lines.append("   " + row + ('  <span class="d">%s</span>' % esc(detail) if detail else ""))
        lines += ["", "   <b>q</b>  Quit" if screen == "main" else "   <b>b</b>  Back   <b>q</b>  Quit"]
        out.append('<pre class="term">%s</pre>' % "\n".join(lines))
    return "\n".join(out + ["</div>"])

# The modules table, package by package (each file's own header line).
def gen_modules(root, page):
    out, package = ['<div class="scroll"><table>', "<tr><th>Module</th><th>Its job</th></tr>"], None
    for rel, line in modules(root):
        folder = os.path.dirname(rel)
        if folder != package:
            package = folder
            out.append('<tr class="group"><td colspan="2"><code>%s/</code></td></tr>' % esc(folder))
        out.append("<tr><td><code>%s</code></td><td>%s</td></tr>" % (esc(os.path.basename(rel)), esc(line)))
    return "\n".join(out + ["</table></div>"])

# The platform contract (app/system.py CONTRACT, each method with the comment above it in System).
def gen_contract(root, page):
    path = os.path.join(root, "nxspoof", "app", "system.py")
    comments = method_comments(path, "System")
    out = ['<div class="scroll"><table>', "<tr><th>Group</th><th>Method</th><th>What it does</th></tr>"]
    for group, names in module_constant(path, "CONTRACT") or ():
        for i, name in enumerate(names):
            first = '<td rowspan="%d"><b>%s</b></td>' % (len(names), esc(group)) if i == 0 else ""
            out.append("<tr>%s<td><code>%s</code></td><td>%s</td></tr>" % (first, esc(name), esc(comments.get(name, ""))))
    return "\n".join(out + ["</table></div>"])

# The guardrails (tools/guardrails.py RULES): each rule and what it protects.
def gen_guardrails(root, page):
    out = ['<div class="scroll"><table>', "<tr><th>Rule</th><th>Protects</th></tr>"]
    for name, protects in guardrail_rules(root):
        out.append("<tr><td><b>%s</b></td><td>%s</td></tr>" % (esc(name), esc(protects)))
    return "\n".join(out + ["</table></div>"])

# The watchdog's levels (tools/health.py LIMITS): where a memo starts, where the run fails.
def gen_limits(root, page):
    words = {"file_lines": "code lines in one file", "file_fan_out": "nxspoof modules one file imports",
             "function_lines": "lines of one function", "complexity": "decision points in one function",
             "nesting": "blocks inside blocks", "parameters": "parameters of one function"}
    out = ['<div class="scroll"><table>', "<tr><th>Measure</th><th>Memo at</th><th>Limit (the tests fail)</th></tr>"]
    for key, (memo, limit) in (module_constant(os.path.join(root, "tools", "health.py"), "LIMITS") or {}).items():
        out.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (esc(words.get(key, key)), memo, limit))
    return "\n".join(out + ["</table></div>"])

GENERATORS = {"nav": gen_nav, "pages": gen_pages, "settings": gen_settings, "console-fields": gen_console_fields, "options": gen_options,
              "commands": gen_commands, "menu": gen_menu, "modules": gen_modules, "contract": gen_contract, "guardrails": gen_guardrails,
              "limits": gen_limits}

# ##############################################################################################################################################################
# ##  CHECK AND WRITE                                                                                                                                         ##
# ##############################################################################################################################################################

# A page with its generated parts rewritten (unknown parts left as they are).
def regenerate(root, page, text):
    # One generated part, written again (or left as it is when no generator knows it).
    def fill(m):
        gen = GENERATORS.get(m.group(1))
        return m.group(0) if gen is None else "<!-- generated: %s -->\n%s\n<!-- /generated -->" % (m.group(1), gen(root, page))
    return MARK.sub(fill, text)

# What the code says that the docs can't: settings and options without help, modules without a header line.
def missing_words(root):
    problems = ["setting %s has no help text (settings/schema.py)" % f.name for f in schema.FIELDS + schema.CONSOLE_FIELDS if not f.help.strip()]
    problems += ["option %s has no help text (app/cli.py)" % o.flag for o in cli.OPTIONS if not o.help.strip()]
    problems += ["%s has no header line ('name - its job') for the modules table" % rel for rel, line in modules(root) if not line]
    return problems

# Pages missing from PAGES or from docs/, and generated parts that are unknown, unclosed or out of date.
def page_problems(root):
    folder = os.path.join(root, DOCS)
    names = [p[0] for p in PAGES]
    found = sorted(n for n in os.listdir(folder) if n.endswith(".html")) if os.path.isdir(folder) else []
    problems = ["docs/%s isn't in tools/docs.py PAGES" % n for n in found if n not in names]
    problems += ["tools/docs.py PAGES names docs/%s, which doesn't exist" % n for n in names if n not in found]
    for name in (n for n in names if n in found):
        text = read_text(os.path.join(folder, name))
        problems += ["docs/%s: unknown generated part '%s'" % (name, m.group(1)) for m in MARK.finditer(text) if m.group(1) not in GENERATORS]
        if len(OPEN_MARK.findall(text)) != len(MARK.findall(text)):
            problems.append("docs/%s: a generated part isn't closed (<!-- /generated -->)" % name)
        if "<!-- generated: nav -->" not in text:
            problems.append("docs/%s has no navigation (<!-- generated: nav -->)" % name)
        if regenerate(root, name, text) != text:
            stale = [m.group(1) for m in MARK.finditer(text) if m.group(1) in GENERATORS and
                     regenerate(root, name, m.group(0)) != m.group(0)]
            problems.append("docs/%s: out of date: %s" % (name, ", ".join(stale)))
    return problems

# Everything that keeps the generated parts from being current (empty: all good).
def check(root=ROOT):
    return missing_words(root) + page_problems(root)

# Rewrite every page's generated parts; returns the pages that changed.
def write(root=ROOT):
    changed = []
    for name, _, _, _ in PAGES:
        path = os.path.join(root, DOCS, name)
        if not os.path.isfile(path):
            continue
        text = read_text(path)
        new = regenerate(root, name, text)
        if new != text:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
            changed.append(name)
    return changed

# Command line: say what's out of date, or (--write) rewrite it.
def main():
    if "--write" in sys.argv[1:]:
        changed = write()
        print("Rewritten: %s" % ", ".join(changed) if changed else "Nothing to rewrite: every generated part is current.")
    problems = check()
    for p in problems:
        print("❌ %s" % p)
    if not problems:
        print("✅ The docs' generated parts are current.")
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
