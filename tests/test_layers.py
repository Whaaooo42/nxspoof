"""
test_layers.py - enforces the import rules from docs/how-its-built.html, Layers.

Imports only go down the layers; packages in the same layer don't import each other (one exception:
consoles may use macos.leases); the new package never imports the old top-level files.
"""

import ast
import os
import unittest

from nxspoof.core.paths import PROJECT_DIR

PACKAGE_DIR = os.path.join(PROJECT_DIR, "nxspoof")
LAYERS = {"core": 0, "settings": 1, "consoles": 1, "net": 1, "macos": 1, "output": 1,
          "features": 2, "dashboard": 2, "setup": 2, "app": 3}
EXCEPTIONS = {("consoles", "macos.leases")}
LAUNCHER_MODULES = {"nx_spoof"}                  # the launcher at the top of the folder: the package never imports it

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# Every .py file of the package with its own package name ("" for nxspoof/__init__.py).
def package_files():
    for root, _, files in os.walk(PACKAGE_DIR):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                rel = os.path.relpath(path, PACKAGE_DIR).split(os.sep)
                yield path, (rel[0] if len(rel) > 1 else "")

# Absolute dotted names imported by one file ("nxspoof.macos.leases", "os", ...).
def imports_of(path, own_package):
    tree = ast.parse(read_text(path))
    here = ["nxspoof"] + ([own_package] if own_package else [])
    if os.path.basename(path) != "__init__.py":
        sub = os.path.relpath(os.path.dirname(path), PACKAGE_DIR).split(os.sep)
        here = ["nxspoof"] + [p for p in sub if p != "."]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = here[:len(here) - (node.level - 1)]
                yield ".".join(base + ([node.module] if node.module else []))
            else:
                yield node.module or ""

# Problems with one file's imports, given the package it belongs to.
def layer_problems(path, pkg):
    problems = []
    rel = os.path.relpath(path, PROJECT_DIR)
    for name in imports_of(path, pkg):
        top = name.split(".")[0]
        if top in LAUNCHER_MODULES:
            problems.append("%s imports the launcher %s" % (rel, name))
        if top != "nxspoof" or not pkg:
            continue
        parts = name.split(".")
        target = parts[1] if len(parts) > 1 else ""
        if target in ("", pkg, "core"):
            continue
        if target not in LAYERS:
            problems.append("%s imports unknown package %s" % (rel, name))
            continue
        if any(pkg == a and ".".join(parts[1:]).startswith(b) for a, b in EXCEPTIONS):
            continue
        if LAYERS[target] >= LAYERS[pkg]:
            problems.append("%s (layer %d) imports %s (layer %d)" % (rel, LAYERS[pkg], name, LAYERS[target]))
    return problems

# The import rules between packages.
class LayerRules(unittest.TestCase):
    # Every subpackage of nxspoof is in the layer table (a new folder must be placed on purpose).
    def test_every_package_has_a_layer(self):
        found = {pkg for _, pkg in package_files() if pkg}
        self.assertEqual(found - set(LAYERS), set(), "add new packages to LAYERS (and the architecture doc)")

    # Imports only go down; same-layer packages stay apart; no imports of the old files.
    def test_imports_follow_the_layers(self):
        problems = []
        for path, pkg in package_files():
            problems += layer_problems(path, pkg)
        self.assertEqual(problems, [], "\n".join(problems))

    # The check itself works: a core module importing macos, and an import of the launcher, are caught.
    def test_the_check_catches_violations(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("import nxspoof.macos.sharing\nfrom nxspoof.net import dns\nimport nx_spoof\n")
        try:
            found = layer_problems(f.name, "core")
        finally:
            os.unlink(f.name)
        self.assertEqual(len(found), 3, found)

    # core imports nothing else from nxspoof.
    def test_core_is_self_contained(self):
        for path, pkg in package_files():
            if pkg == "core":
                for name in imports_of(path, pkg):
                    if name.startswith("nxspoof."):
                        self.assertTrue(name.startswith("nxspoof.core"), "%s imports %s" % (path, name))

if __name__ == "__main__":
    unittest.main()
