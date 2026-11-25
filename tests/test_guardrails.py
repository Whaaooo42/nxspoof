"""
test_guardrails.py - the principles of the rebuild, checked on every test run (docs/how-its-built.html, Safeguards).

The rules themselves live in tools/guardrails.py (each: project folder -> places that break it); here
every rule runs on the real project and must find nothing. tests/test_guardrails_bite.py proves each
rule catches a planted violation. The import layers and the code conventions have their own files
(test_layers.py, test_conventions.py).
"""

import importlib.util
import os
import unittest

from nxspoof.core.paths import PROJECT_DIR

# tools/guardrails.py, loaded as a module.
def guardrails():
    spec = importlib.util.spec_from_file_location("guardrails", os.path.join(PROJECT_DIR, "tools", "guardrails.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Every rule holds on the real project.
class Guardrails(unittest.TestCase):
    # One sub-test per rule: its name, the places that break it, and what to do.
    def test_every_rule_holds(self):
        for name, protects, fix, problems in guardrails().check_all(PROJECT_DIR):
            with self.subTest(rule=name):
                self.assertEqual(problems, [], "%s (%s) -> %s" % (name, protects, fix))

    # Every exception names its reason (an empty reason is no reason).
    def test_every_exception_has_a_reason(self):
        g = guardrails()
        for table in (g.ALLOWED_PRINT, g.ALLOWED_FILE_WRITES, g.ALLOWED_MACHINE_NAMES, g.ALLOWED_SYSTEM_IMPORTS):
            for place, reason in table.items():
                self.assertTrue(reason.strip(), "exception %r needs a reason" % (place,))

if __name__ == "__main__":
    unittest.main()
