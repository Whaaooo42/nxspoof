#!/usr/bin/env python3
"""
health.py - the watchdog: is the code getting too long, too tangled or copy-pasted?

Measures every Python file of nxspoof/ and tools/:
  file       lines of code (not counting blank and comment lines), and how many nxspoof modules it uses
  function   lines, complexity (decision points: if, loops, try, and/or...), nesting depth, parameters
  copies     blocks of 8+ code lines that appear twice (copy-paste waiting to become one helper)

Each measure has two levels (LIMITS below):
  memo       worth a look: written to MEMO.md (top of the folder) and shown after every test run
  limit      too far: the test run FAILS (tests/test_guardrails.py) until it's fixed or accepted

It also compares with the last accepted state (tools/health_baseline.json): a file that grew by more than
GROWTH (or a function that got more complex) is in the memo even below the levels, so slow creep is seen.
Known hotspots (above a memo level when the baseline was taken, no worse since) stay quiet.

  python3 -B tools/health.py                   # print the report, write MEMO.md (or remove it when all is well)
  python3 -B tools/health.py --all             # also list the known hotspots (in the baseline and no worse)
  python3 -B tools/health.py --accept "why"    # take today's numbers as the new baseline, with the reason

A value already in the baseline that hasn't got worse stays quiet: the memo is about what changed.
"""

import argparse
import ast
import datetime
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "tools", "health_baseline.json")
MEMO = os.path.join(ROOT, "MEMO.md")
SCANNED = ("nxspoof", "tools")

# (memo, limit) for each measure.
LIMITS = {
    "file_lines": (450, 800),            # code lines in one file
    "file_fan_out": (10, 16),            # nxspoof modules one file imports
    "function_lines": (60, 110),         # lines of one function (with its comments)
    "complexity": (14, 25),              # decision points in one function, plus one
    "nesting": (4, 6),                   # blocks inside blocks inside blocks...
    "parameters": (8, 11),               # parameters of one function
}
GROWTH = {"file_lines": (0.30, 60), "complexity": (0.40, 4)}     # (fraction, at least this much) above the baseline
COPY_WINDOW = 8                                                  # code lines that must repeat to count as a copy
DECISIONS = (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler, ast.With, ast.Assert, ast.comprehension)   # 'a if b else c' is cheap: not counted
BLOCKS = (ast.If, ast.For, ast.While, ast.Try, ast.With)

# ##############################################################################################################################################################
# ##  MEASURING                                                                                                                                               ##
# ##############################################################################################################################################################

# Every scanned .py file, as paths relative to the project.
def scanned_files(root=ROOT):
    out = []
    for top in SCANNED:
        for folder, _, files in os.walk(os.path.join(root, top)):
            out += [os.path.relpath(os.path.join(folder, f), root) for f in files if f.endswith(".py")]
    return sorted(out)

# The code lines of a text: not blank, not a comment, not inside the file's header docstring.
def code_lines(text):
    tree = ast.parse(text)
    skip = set()
    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Constant):
        skip = set(range(tree.body[0].lineno, tree.body[0].end_lineno + 1))
    return [(i, l.strip()) for i, l in enumerate(text.split("\n"), 1) if l.strip() and not l.strip().startswith("#") and i not in skip]

# Decision points inside one function (not counting functions defined inside it), plus one.
def complexity(func):
    count = 1
    for node in _walk_own(func):
        if isinstance(node, DECISIONS):
            count += 1
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
    return count

# The deepest nesting of blocks inside one function.
def nesting(node, depth=0):
    deepest = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        deepest = max(deepest, nesting(child, depth + (1 if isinstance(child, BLOCKS) else 0)))
    return deepest

# A function's own lines: its whole span minus the functions and classes defined inside it.
def own_lines(func):
    total = func.end_lineno - func.lineno + 1
    inner = [n for n in ast.walk(func) if n is not func and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    covered = set()
    for n in inner:
        covered.update(range(n.lineno, n.end_lineno + 1))
    return total - len(covered)

# The nodes of a function, without descending into functions or classes defined inside it.
def _walk_own(func):
    todo = list(ast.iter_child_nodes(func))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        todo.extend(ast.iter_child_nodes(node))

# The measures of one file: {"file_lines", "file_fan_out", "functions": {name: {...}}}.
def measure_file(path, root=ROOT):
    with open(os.path.join(root, path), encoding="utf-8") as f:
        text = f.read()
    tree = ast.parse(text)
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("nxspoof"):
            used.update("%s.%s" % (node.module, a.name) if node.module.count(".") < 2 else node.module for a in node.names)
        elif isinstance(node, ast.Import):
            used.update(a.name for a in node.names if a.name.startswith("nxspoof."))
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = _qualified(tree, node)
            a = node.args
            params = len(a.args) + len(a.kwonlyargs) + (1 if a.vararg else 0) + (1 if a.kwarg else 0) - (1 if a.args and a.args[0].arg in ("self", "cls") else 0)
            functions[name] = {"line": node.lineno, "function_lines": own_lines(node),
                               "complexity": complexity(node), "nesting": nesting(node), "parameters": params}
    return {"file_lines": len(code_lines(text)), "file_fan_out": len(used), "functions": functions}

# "Class.method" or "function" (functions inside functions: "outer.inner").
def _qualified(tree, target):
    path = []

    # Find the chain of definitions leading to target.
    def visit(node, chain):
        for child in ast.iter_child_nodes(node):
            if child is target:
                path.extend(chain + [child.name])
                return True
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if visit(child, chain + [child.name]):
                    return True
            elif visit(child, chain):
                return True
        return False
    visit(tree, [])
    return ".".join(path) or target.name

# Blocks of COPY_WINDOW code lines found more than once: [(first place, other place, first line of the block)].
def copies(paths, root=ROOT):
    seen, found, reported = {}, [], set()
    for path in paths:
        with open(os.path.join(root, path), encoding="utf-8") as f:
            lines = code_lines(f.read())
        meaningful = [(i, l) for i, l in lines if not re.match(r"^(import |from |[)\]}]+,?$|else:$|try:$|finally:$|return$|pass$|continue$|break$)", l)]
        for k in range(len(meaningful) - COPY_WINDOW + 1):
            window = meaningful[k:k + COPY_WINDOW]
            key = hashlib.sha1("\n".join(l for _, l in window).encode()).hexdigest()
            here = (path, window[0][0])
            if key in seen and seen[key][0] != here[0] or key in seen and abs(seen[key][1] - here[1]) >= COPY_WINDOW:
                pair = (seen[key][0], here[0])
                if pair not in reported:
                    reported.add(pair)
                    found.append(("%s:%d" % seen[key], "%s:%d" % here, window[0][1]))
            else:
                seen.setdefault(key, here)
    return found

# Everything measured: {"files": {path: measures}, "copies": [...]}.
def measure(root=ROOT):
    paths = scanned_files(root)
    return {"files": {p: measure_file(p, root) for p in paths}, "copies": copies(paths, root)}

# ##############################################################################################################################################################
# ##  JUDGING                                                                                                                                                 ##
# ##############################################################################################################################################################

# Findings: [(level "memo"/"limit", where, what)]. A memo-level value already in the baseline and no worse is
# known and stays quiet (show_known=True lists it too); a limit fails unless it's accepted up to that value.
def findings(measures, baseline=None, show_known=False):
    baseline = baseline or {}
    files, accepted = baseline.get("files", {}), baseline.get("accepted", {})
    out = []

    # One measure: limit (unless accepted), memo (unless known and no worse), nothing.
    def judge(where, key_place, key, value, known, label):
        memo, limit = LIMITS[key]
        allowed = accepted.get("%s %s" % (key_place, key), {}).get("value")
        if value > limit and (allowed is None or value > allowed):
            out.append(("limit", where, "%s %d (limit %d)" % (label, value, limit)))
        elif value > memo and (show_known or known is None or value > known):
            note = "" if known is None else (", was %d" % known if value > known else ", known")
            out.append(("memo", where, "%s %d (memo at %d%s)" % (label, value, memo, note)))
    for path, m in sorted(measures["files"].items()):
        old = files.get(path, {})
        judge(path, path, "file_lines", m["file_lines"], old.get("file_lines"), "code lines")
        judge(path, path, "file_fan_out", m["file_fan_out"], old.get("file_fan_out"), "nxspoof modules used")
        if old and _grew(m["file_lines"], old["file_lines"], "file_lines"):
            out.append(("memo", path, "grew from %d to %d code lines since the last accepted state" % (old["file_lines"], m["file_lines"])))
        for name, f in sorted(m["functions"].items()):
            where, before = "%s:%d %s()" % (path, f["line"], name), old.get("functions", {}).get(name, {})
            for key, label in (("function_lines", "lines"), ("complexity", "complexity"), ("nesting", "nesting depth"), ("parameters", "parameters")):
                judge(where, "%s %s()" % (path, name), key, f[key], before.get(key), label)
            if before and _grew(f["complexity"], before["complexity"], "complexity"):
                out.append(("memo", where, "complexity grew from %d to %d since the last accepted state" % (before["complexity"], f["complexity"])))
    known_copies = {tuple(c) for c in baseline.get("copies", [])}
    for first, other, line in measures["copies"]:
        if show_known or (first.split(":")[0], other.split(":")[0]) not in known_copies:
            out.append(("memo", other, "same %d+ code lines as %s (starting with: %s)" % (COPY_WINDOW, first, line[:60])))
    return out

# True if a value grew past the allowed growth over its baseline value.
def _grew(now, before, key):
    fraction, at_least = GROWTH[key]
    return now - before >= at_least and now > before * (1 + fraction)

# ##############################################################################################################################################################
# ##  THE MEMO                                                                                                                                                ##
# ##############################################################################################################################################################

# The baseline file's contents ({} if there's none yet).
def load_baseline(path=BASELINE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

# The memo's text for a list of findings ("" when there's nothing to say).
def memo_text(found, when=None):
    if not found:
        return ""
    when = when or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    limits = [f for f in found if f[0] == "limit"]
    lines = ["# 📝 Memo from the watchdog (%s)" % when, "",
             "Some code is getting long, tangled or copy-pasted. Nothing is broken; this is the moment to tidy it before it gets harder.",
             ""]
    if limits:
        lines += ["## ⛔ Past the limit (the test run fails until fixed or accepted)", ""] + ["- `%s`: %s" % (w, t) for _, w, t in limits] + [""]
    memos = [f for f in found if f[0] == "memo"]
    if memos:
        lines += ["## 👀 Worth a look", ""] + ["- `%s`: %s" % (w, t) for _, w, t in memos] + [""]
    lines += ["What to do: split the long function or file along the lines of docs/how-its-built.html, or turn the copies into one helper. "
              "If it's fine as it is, accept it with a reason: `python3 -B tools/health.py --accept \"why\"` (kept in tools/health_baseline.json).",
              "", "This file is rewritten after every test run and disappears when there's nothing to say."]
    return "\n".join(lines) + "\n"

# Write MEMO.md (or remove it when there's nothing to say); returns the findings.
def write_memo(measures=None, baseline=None, path=MEMO):
    found = findings(measures or measure(), baseline if baseline is not None else load_baseline())
    text = memo_text(found)
    if text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    elif os.path.exists(path):
        os.remove(path)
    return found

# Take today's numbers as the baseline; values past a limit are accepted up to today's value, with the reason.
def accept(reason, measures=None, path=BASELINE):
    measures = measures or measure()
    old = load_baseline(path)
    accepted = old.get("accepted", {})
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    for kind, where, _ in findings(measures, {}):
        if kind != "limit":
            continue
        for key in LIMITS:
            value = _value_for(measures, where, key)
            if value is not None and value > LIMITS[key][1]:
                accepted["%s %s" % (_accept_key(where), key)] = {"value": value, "reason": reason, "when": when}
    metrics = ("function_lines", "complexity", "nesting", "parameters")
    data = {"files": {p: {"file_lines": m["file_lines"], "file_fan_out": m["file_fan_out"],
                          "functions": {n: {k: f[k] for k in metrics} for n, f in m["functions"].items()}}
                      for p, m in measures["files"].items()},
            "copies": sorted({(a.split(":")[0], b.split(":")[0]) for a, b, _ in measures["copies"]}),
            "accepted": accepted, "history": old.get("history", []) + [{"when": when, "reason": reason}]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    return data

# "path name()" for a function finding ("path:line name()"), or the path for a file finding.
def _accept_key(where):
    m = re.match(r"^(.+?):\d+ (.+)$", where)
    return "%s %s" % (m.group(1), m.group(2)) if m else where

# A measure's value for a finding's place (None if it doesn't apply).
def _value_for(measures, where, key):
    m = re.match(r"^(.+?):\d+ (.+)\(\)$", where)
    if m:
        f = measures["files"].get(m.group(1), {}).get("functions", {}).get(m.group(2))
        return f.get(key) if f and key in f else None
    return measures["files"].get(where, {}).get(key)

# Command line: print the report and write the memo, or accept today's numbers.
def main(argv=None):
    ap = argparse.ArgumentParser(description="The watchdog: is the code getting too long, too tangled or copy-pasted?")
    ap.add_argument("--accept", metavar="REASON", help="take today's numbers as the new baseline (say why)")
    ap.add_argument("--all", action="store_true", help="also list the known hotspots (already in the baseline, no worse)")
    args = ap.parse_args(argv)
    measures = measure()
    if args.accept:
        accept(args.accept, measures)
        print("Baseline updated (tools/health_baseline.json): %s" % args.accept)
    found = write_memo(measures)
    if args.all:
        known = [f for f in findings(measures, load_baseline(), show_known=True) if f not in found]
        print("Known hotspots (in the baseline, no worse):" if known else "No known hotspots.")
        for _, where, text in known:
            print("  - %s: %s" % (where, text))
    biggest = sorted(((m["file_lines"], p) for p, m in measures["files"].items()), reverse=True)[:5]
    print("Watchdog: %d files measured. Biggest: %s" % (len(measures["files"]), ", ".join("%s (%d)" % (p, n) for n, p in biggest)))
    if not found:
        print("✅ Nothing to report: no memo.")
        return 0
    print(memo_text(found).rstrip())
    return 1 if any(k == "limit" for k, _, _ in found) else 0

if __name__ == "__main__":
    sys.exit(main())
