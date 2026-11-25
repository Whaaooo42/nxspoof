"""
test_verify.py - unit tests for tools/verify_run.py, the real-run checklist checker.

Checked on the reference recordings (full simulated runs of the current code, both stop paths) and on
small made-up logs for the failure cases.
"""

import importlib.util
import os
import shutil
import tempfile
import unittest

from nxspoof.core.paths import PROJECT_DIR

OLD_LOGS = os.path.join(PROJECT_DIR, "tests", "fixtures", "old_text_logs")      # runs logged before the .jsonl twins (textlog.py reads them)

# tools/verify_run.py, loaded as a module.
def verify_module():
    spec = importlib.util.spec_from_file_location("verify_run", os.path.join(PROJECT_DIR, "tools", "verify_run.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# The checklist from logs.
class CheckLog(unittest.TestCase):
    # A full run passes every item; the stop path is recognized.
    def test_reference_runs_pass(self):
        v = verify_module()
        for name, how in (("ctrl_c.log", "Ctrl+C"), ("dashboard_stop.log", "the dashboard's Stop")):
            items = v.check_log(read_text(os.path.join(OLD_LOGS, name)))
            self.assertEqual([i.title for i in items if i.status == "fail"], [], name)
            self.assertIn("stopped cleanly (%s)" % how, [i.title for i in items])

    # A run that never reached READY and crashed: the failing items say why.
    def test_failures_explained(self):
        v = verify_module()
        log = "[10:00:00] CHECK DNS  FAIL ctest.cdn.nintendo.net -> error: timed out\n[10:00:01] PROBLEM self-test failed - see docs/troubleshooting.html.\n"
        items = {i.title: i for i in v.check_log(log)}
        self.assertEqual(items["self-test: 6 checks passed, then READY"].evidence, ["CHECK DNS  FAIL ctest.cdn.nintendo.net -> error: timed out"])
        self.assertEqual(items["stopped cleanly"].status, "fail")
        self.assertIn("PROBLEM self-test failed - see docs/troubleshooting.html.", items["no FATAL / PROBLEM / WARNING lines"].evidence)
        self.assertEqual(items["FTP server seen going up and down"].status, "skip")

    # Stop paths: Ctrl+C, closed window (SIGHUP), dashboard; the self-test's blocked lookup isn't a console's.
    def test_stop_paths_and_self_test(self):
        v = verify_module()
        self.assertEqual(v.stop_path(["NOTE  stop signal received (SIGHUP) - cleaning up", "Stopping..."]), "SIGHUP")
        self.assertEqual(v.stop_path(["READY x", "Stopping..."]), "ctrl-c")
        self.assertIsNone(v.stop_path(["READY x"]))
        items = {i.title: i for i in v.check_log("[10:00:00] DNS   Mac (self-test) asked A     example.com -> BLOCKED\n")}
        self.assertEqual(items["a console's lookups were blocked"].status, "skip")

    # --all: only the new code's run logs count, with their stop paths.
    def test_new_code_runs(self):
        v = verify_module()
        folder = tempfile.mkdtemp(prefix="nxspoof-test-")
        try:
            with open(os.path.join(folder, "2026-11-14_10-00-00.log"), "w") as f:
                f.write("[10:00:00] LOG   NXSPOOF 2.0.0-dev (the nxspoof package)\n[10:05:00] NOTE  stop signal received (SIGHUP) - cleaning up\n[10:05:00] Stopping...\n")
            shutil.copy(os.path.join(OLD_LOGS, "ctrl_c.log"), os.path.join(folder, "2026-11-14_09-00-00.log"))
            self.assertEqual(v.new_code_runs(folder), [("2026-11-14_10-00-00.log", "SIGHUP")])
        finally:
            shutil.rmtree(folder)

if __name__ == "__main__":
    unittest.main()
