"""
test_conventions.py - enforces the owner's code conventions on the new code (nxspoof/, tools/, tests/).

  * a one-line '#' comment directly above every function, class and method
  * no docstrings except the file header
  * never two empty lines in a row
  * section banners are 160 characters wide with an empty line above and below

Each check is a function on one file's text (place -> problems); the Proof tests at the end feed each
one a broken example, so a check that stopped catching anything would be noticed.
"""

import ast
import os
import re
import unittest

from nxspoof.core.paths import PROJECT_DIR

CHECKED_DIRS = ["nxspoof", "tools", "tests"]
BANNER_BAR = re.compile(r"^\s*# #{20,}$")

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# Every Python file covered by the conventions, as (path relative to the project, text).
def checked_files():
    for d in CHECKED_DIRS:
        for root, _, files in os.walk(os.path.join(PROJECT_DIR, d)):
            for f in sorted(n for n in files if n.endswith(".py")):
                path = os.path.join(root, f)
                yield os.path.relpath(path, PROJECT_DIR), read_text(path)

# Functions and classes without a '#' comment line directly above them (above their decorators, if any).
def missing_comments(rel, text):
    lines, problems = text.split("\n"), []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            above = lines[first - 2].strip() if first >= 2 else ""
            if not above.startswith("#") or BANNER_BAR.match(lines[first - 2]):
                problems.append("%s:%d %s" % (rel, node.lineno, node.name))
    return problems

# Functions and classes with a docstring (only the file header may be one).
def docstrings(rel, text):
    return ["%s:%d %s" % (rel, n.lineno, n.name) for n in ast.walk(ast.parse(text))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(n)]

# Places with two empty lines in a row.
def double_empty_lines(rel, text):
    lines = text.split("\n")
    return ["%s:%d" % (rel, i + 1) for i in range(1, len(lines) - 1) if not lines[i].strip() and not lines[i - 1].strip()]

# Banners that aren't 160 wide or lack an empty line above or below.
def banner_problems(rel, text):
    lines, problems = text.split("\n"), []
    for i, line in enumerate(lines):
        if not (BANNER_BAR.match(line) and i + 2 < len(lines) and lines[i + 1].lstrip().startswith("# ##  ")):
            continue
        where = "%s:%d" % (rel, i + 1)
        problems += ["%s banner line is %d wide (not 160)" % (where, len(lines[j])) for j in (i, i + 1, i + 2) if len(lines[j]) != 160]
        if i > 0 and lines[i - 1].strip():
            problems.append("%s no empty line above the banner" % where)
        if i + 3 < len(lines) and lines[i + 3].strip():
            problems.append("%s no empty line below the banner" % where)
    return problems

# All files' problems for one check.
def everywhere(check):
    problems = []
    for rel, text in checked_files():
        problems += check(rel, text)
    return problems

# The owner's code conventions, checked on every new file.
class Conventions(unittest.TestCase):
    # A '#' comment line sits directly above every def and class (above its decorators, if any).
    def test_comment_above_every_function_and_class(self):
        problems = everywhere(missing_comments)
        self.assertEqual(problems, [], "missing '#' comment above:\n" + "\n".join(problems))

    # Only the file header may be a docstring.
    def test_no_docstrings_except_the_header(self):
        problems = everywhere(docstrings)
        self.assertEqual(problems, [], "docstrings (use '#' comments above instead):\n" + "\n".join(problems))

    # Never two empty lines in a row.
    def test_no_double_empty_lines(self):
        problems = everywhere(double_empty_lines)
        self.assertEqual(problems, [], "two empty lines in a row:\n" + "\n".join(problems))

    # Banners: 160 wide, title line between two bars, an empty line above and below.
    def test_banners(self):
        problems = everywhere(banner_problems)
        self.assertEqual(problems, [], "\n".join(problems))

# Each check catches a broken example (and leaves a good one alone).
class Proof(unittest.TestCase):
    # A function without a comment, a decorated one with the comment under the decorator, a class after a banner.
    def test_missing_comments(self):
        bar = "# " + "#" * 158
        bad = "def a():\n    pass\n\n@staticmethod\n# wrong place\ndef b():\n    pass\n\n%s\nclass C:\n    pass\n" % bar
        self.assertEqual([p.split()[-1] for p in missing_comments("x.py", bad)], ["a", "b", "C"])
        self.assertEqual(missing_comments("x.py", "# Fine.\ndef a():\n    pass\n"), [])

    # A docstring in a function.
    def test_docstrings(self):
        self.assertEqual(docstrings("x.py", '"""header is fine"""\n# F.\ndef f():\n    """not here"""\n'), ["x.py:3 f"])

    # Two empty lines.
    def test_double_empty_lines(self):
        self.assertEqual(double_empty_lines("x.py", "a = 1\n\n\nb = 2\n"), ["x.py:3"])
        self.assertEqual(double_empty_lines("x.py", "a = 1\n\nb = 2\n"), [])

    # A banner one character short, glued to the code above and below.
    def test_banners(self):
        bar, title = "# " + "#" * 157, "# ##  TITLE" + " " * 146 + "##"
        problems = banner_problems("x.py", "a = 1\n%s\n%s\n%s\nb = 2\n" % (bar, title, bar))
        self.assertTrue(any("not 160" in p for p in problems))
        self.assertTrue(any("above" in p for p in problems) and any("below" in p for p in problems))

if __name__ == "__main__":
    unittest.main()
