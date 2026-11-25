#!/usr/bin/env python3
"""
run_tests.py - run all unit tests, from any folder (no sudo needed).

  python3 -B ~/Documents/nx-spoof/tools/run_tests.py          # all tests
  python3 -B ~/Documents/nx-spoof/tools/run_tests.py -v       # one line per test

It finds the project folder by itself, so the current folder doesn't matter.
Exit code 0 = everything passed. Afterwards the watchdog (tools/health.py) looks at the code: when
something new got too long, tangled or copy-pasted, it writes MEMO.md and says so here.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Discover and run every test in tests/, with the project folder as the import root.
def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    verbosity = 2 if "-v" in sys.argv[1:] else 1
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    print("Python %s - %s" % (sys.version.split()[0], "ALL PASSED" if result.wasSuccessful() else "SOMETHING FAILED"))
    watchdog()
    sys.exit(0 if result.wasSuccessful() else 1)

# The watchdog's verdict after the tests: a memo (MEMO.md) when something new needs a look.
def watchdog():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import health
    found = health.write_memo()
    if not found:
        print("Watchdog: nothing to report.")
        return
    limits = sum(1 for kind, _, _ in found if kind == "limit")
    print("\n📝 MEMO from the watchdog: %d thing%s to look at%s - see MEMO.md" % (
        len(found), "" if len(found) == 1 else "s", " (%d past a limit: the run fails)" % limits if limits else ""))
    for kind, where, text in found[:8]:
        print("   %s %s: %s" % ("⛔" if kind == "limit" else "👀", where, text))
    if len(found) > 8:
        print("   ... and %d more in MEMO.md" % (len(found) - 8))

if __name__ == "__main__":
    main()
