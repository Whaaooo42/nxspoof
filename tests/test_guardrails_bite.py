"""
test_guardrails_bite.py - proof that every safeguard really catches what it's meant to catch.

Each test copies the project into a temporary folder, plants ONE violation (a print() in a feature, a
subprocess call outside macos/, an inline script in the dashboard page, a 130-line function...), and
checks that the matching guardrail (tools/guardrails.py) or the watchdog (tools/health.py) names it.
An untouched copy passes everything, so each failure comes from the planted violation alone.
Nothing here touches the real project.
"""

import importlib.util
import os
import shutil
import tempfile
import unittest

from nxspoof.core.paths import PROJECT_DIR

IGNORED = shutil.ignore_patterns("logs", "last_run.log", "MEMO.md", "__pycache__", "fixtures", ".DS_Store")

# A tool from tools/ (guardrails or health), loaded as a module from a given project folder.
def tool(name, root=PROJECT_DIR):
    spec = importlib.util.spec_from_file_location("%s_bite_%d" % (name, id(root)), os.path.join(root, "tools", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# A function with many lines (each a simple assignment), as source text.
def long_function(name, lines):
    return "\n# A long one.\ndef %s():\n" % name + "".join("    v%d = %d\n" % (i, i) for i in range(lines)) + "    return v0\n"

# A temporary copy of the project, with helpers to plant violations.
class Planted(unittest.TestCase):
    # A fresh copy for each test.
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nxspoof-bite-")
        self.root = os.path.join(self.tmp, "nx-spoof")
        shutil.copytree(PROJECT_DIR, self.root, ignore=IGNORED, symlinks=True)
        self.g = tool("guardrails")

    # Remove the copy.
    def tearDown(self):
        shutil.rmtree(self.tmp)

    # Write a file in the copy (folders created as needed).
    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    # Add text at the end of a file of the copy.
    def append(self, rel, text):
        with open(os.path.join(self.root, rel), "a", encoding="utf-8") as f:
            f.write(text)

    # Replace text in a file of the copy (it must be there).
    def replace(self, rel, old, new):
        path = os.path.join(self.root, rel)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(old, text, "the planted change needs %r in %s" % (old, rel))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new))

    # Plant a small feature file with the given body.
    def evil(self, body):
        self.write("nxspoof/features/evil.py", '"""\nnxspoof.features.evil - a planted violation.\n"""\n\n' + body)

    # The rule's findings must mention `expected`.
    def assertCaught(self, rule, expected):
        problems = rule(self.root)
        self.assertTrue(any(expected in p for p in problems), "%s didn't catch %r: %r" % (rule.__name__, expected, problems))

# Every guardrail catches its planted violation.
class GuardrailsBite(Planted):
    # An untouched copy passes every rule (so each catch below comes from the planted violation).
    def test_clean_copy_passes(self):
        self.assertEqual([(name, problems) for name, _, _, problems in self.g.check_all(self.root) if problems], [])

    # subprocess, a disallowed signal import and os.geteuid() outside macos/.
    def test_system_calls(self):
        self.evil("import os\nimport signal\nimport subprocess\n\n# Check root.\ndef root():\n    return os.geteuid() == 0\n")
        for expected in ("imports subprocess", "imports signal", "os.geteuid()"):
            self.assertCaught(self.g.system_calls_outside_macos, expected)

    # A log call inside macos/.
    def test_macos_logging(self):
        self.append("nxspoof/macos/leases.py", "\n# Talks too much.\ndef noisy(bus):\n    bus.log(\"DHCP\", \"hello\")\n")
        self.assertCaught(self.g.macos_logs, "nxspoof/macos/leases.py")

    # A device name written into a feature.
    def test_machine_names(self):
        self.evil('DEVICE = "en0"\n')
        self.assertCaught(self.g.machine_names, "evil.py")

    # print() in a feature.
    def test_print(self):
        self.evil('# Say hi.\ndef hello():\n    print("hi")\n')
        self.assertCaught(self.g.prints, "evil.py")

    # A feature writing a file.
    def test_file_write(self):
        self.evil('# Write.\ndef save():\n    with open("x.txt", "w") as f:\n        f.write("x")\n')
        self.assertCaught(self.g.file_writes, "evil.py")

    # A console name written with plain % formatting (the old way), and the same through an f-string.
    def test_unmarked_console_name(self):
        self.evil('# Log.\ndef hello(bus, registry, mac):\n    bus.log("WIFI", "%s joined" % registry.name(mac))\n'
                  '    bus.say(f"hi {registry.label(mac=mac)}")\n')
        self.assertCaught(self.g.unmarked_console_names, "console name in % formatting")
        self.assertCaught(self.g.unmarked_console_names, "console name in an f-string")

    # A log tag with no color.
    def test_tag_without_color(self):
        self.evil('# Log.\ndef zap(bus):\n    bus.log("ZAP", "x")\n')
        self.assertCaught(self.g.tags_without_color, "tag ZAP")

    # A third-party import.
    def test_third_party_import(self):
        self.evil("import requests\n")
        self.assertCaught(self.g.non_stdlib_imports, "imports requests")

    # Python 3.10+ syntax (a match statement).
    def test_newer_syntax(self):
        self.evil("# Pick.\ndef pick(x):\n    match x:\n        case 1:\n            return 1\n")
        self.assertCaught(self.g.newer_python_syntax, "evil.py")

    # innerHTML in a dashboard module.
    def test_page_html_from_string(self):
        self.append("nxspoof/dashboard/web/js/cards.js", "\n// Bad.\nfunction bad(n, s) { n.innerHTML = s; }\n")
        self.assertCaught(self.g.page_html_from_strings, "innerHTML")

    # An inline script and a file from the internet in the page.
    def test_page_inline_and_internet(self):
        self.replace("nxspoof/dashboard/web/index.html", "</body>", '<script>alert(1)</script>\n<img src="https://evil.example/x.png">\n</body>')
        self.assertCaught(self.g.page_inline_or_internet, "inline <script>")
        self.assertCaught(self.g.page_inline_or_internet, "https://evil.example")

    # A dashboard module importing from outside.
    def test_page_import(self):
        self.append("nxspoof/dashboard/web/js/app.js", '\nimport x from "https://cdn.example/x.js";\n')
        self.assertCaught(self.g.page_imports, "https://cdn.example/x.js")

    # A button with a size of its own: a class word, a .btn.<word> rule, a rule that resizes buttons in one place.
    def test_button_styles(self):
        self.append("nxspoof/dashboard/web/js/runs.js", '\nconst tiny = el("button", "btn small", "x");\n')
        self.append("nxspoof/dashboard/web/css/dashboard.css", "\n.btn.tiny { color: red; }\n.runs-list .btn { font-size: 11px; }\n")
        self.assertCaught(self.g.button_styles, "button class 'small'")
        self.assertCaught(self.g.button_styles, ".btn.tiny")
        self.assertCaught(self.g.button_styles, "'.runs-list .btn' resizes buttons")

    # A command without a menu row.
    def test_command_without_menu_row(self):
        self.replace("tools/menu.py", '"doctor"', '"doktor"')
        self.assertCaught(self.g.commands_missing_in_menu, "command 'doctor'")

    # A System method left out of the platform contract.
    def test_platform_contract(self):
        self.replace("nxspoof/app/system.py", '"open_url", ', '')
        self.assertCaught(self.g.platform_contract_drift, "System.open_url isn't in CONTRACT")

    # A This Mac task whose command doesn't exist.
    def test_task_without_command(self):
        self.replace("nxspoof/app/tasks.py", '(["cleanup"], "cleanup"', '(["sweep"], "cleanup"')
        self.assertCaught(self.g.tasks_without_command, "task 'cleanup' runs 'sweep'")

    # A new module (with its header line) not yet in the docs' modules table, and one without a header line at all.
    def test_module_not_in_the_docs(self):
        self.write("nxspoof/features/newthing.py", '"""\nnxspoof.features.newthing - planted.\n"""\n')
        self.write("nxspoof/features/nameless.py", "# No header.\nX = 1\n")
        self.assertCaught(self.g.generated_parts_stale, "docs/how-its-built.html: out of date: modules")
        self.assertCaught(self.g.generated_parts_stale, "nxspoof/features/nameless.py has no header line")

    # A generated table edited by hand: the settings table no longer what the code says.
    def test_generated_table_edited(self):
        self.replace("docs/reference.html", "<code>logs.keep_last</code>", "<code>logs.keep_some</code>")
        self.assertCaught(self.g.generated_parts_stale, "docs/reference.html: out of date: settings")

    # A docs page outside the navigation, and a generated part left open.
    def test_docs_page_not_in_pages(self):
        self.write("docs/extra.html", "<!doctype html>\n<!-- generated: nav -->\n<!-- /generated -->\n")
        self.replace("docs/guide.html", "<!-- /generated -->", "")
        self.assertCaught(self.g.generated_parts_stale, "docs/extra.html isn't in tools/docs.py PAGES")
        self.assertCaught(self.g.generated_parts_stale, "docs/guide.html: a generated part isn't closed")

    # A file missing from the reference.
    def test_file_not_in_reference(self):
        self.write("notes.txt", "planted\n")
        self.assertCaught(self.g.files_missing_in_reference, "notes.txt")

    # A word from private/personal.txt in a published file is caught (whole words only); the ignored places and a copy
    # without the list are left alone.
    def test_personal_data(self):
        name, mac = "Planty" + " McPlant", "aa:bb:cc:" + "11:22:33"      # in pieces, so this file never holds them whole
        self.write("private/personal.txt", "# planted\n%s\n%s\n" % (name, mac))
        self.append("README.md", "\nThanks to %s for testing.\n" % name.lower())
        self.write("tests/notes.txt", "console %s\n%ser is someone else\n" % (mac.upper(), name))
        self.write("CLAUDE.local.md", "%s's own notes\n" % name)
        problems = self.g.personal_data(self.root)
        self.assertCaught(self.g.personal_data, "README.md:")
        self.assertCaught(self.g.personal_data, "tests/notes.txt:1: " + mac.upper())
        self.assertEqual(len(problems), 2, "not CLAUDE.local.md, not private/, not a longer word: %s" % problems)
        os.remove(os.path.join(self.root, "private", "personal.txt"))
        self.assertEqual(self.g.personal_data(self.root), [], "no list: nothing to check")

    # An inline script and a stylesheet from the internet in a docs page; innerHTML in its script.
    def test_docs_page_safety(self):
        self.replace("docs/guide.html", "</body>", '<script>alert(1)</script>\n<link rel="stylesheet" href="https://cdn.example/x.css">\n</body>')
        self.append("docs/docs.js", "\n// Bad.\nfunction bad(n, s) { n.innerHTML = s; }\n")
        self.assertCaught(self.g.page_inline_or_internet, "docs/guide.html inline <script>")
        self.assertCaught(self.g.page_inline_or_internet, "https://cdn.example")
        self.assertCaught(self.g.page_html_from_strings, "docs/docs.js")

    # A 130-line function: past the watchdog's hard limit.
    def test_watchdog_limit(self):
        self.evil(long_function("huge", 130))
        self.assertCaught(self.g.past_watchdog_limits, "huge()")

# The watchdog notices what's new or worse, and stays quiet about what's known.
class WatchdogBites(Planted):
    # The copy's watchdog.
    def health(self):
        h = tool("health", self.root)
        return h, h.load_baseline(os.path.join(self.root, "tools", "health_baseline.json"))

    # Right after accepting: nothing to say; the known spots only show when asked.
    def test_known_spots_stay_quiet(self):
        h, _ = self.health()
        path = os.path.join(self.root, "tools", "health_baseline.json")
        measures = h.measure(self.root)
        baseline = h.accept("fresh baseline for the test", measures, path=path)
        self.assertEqual(h.findings(measures, baseline), [])
        self.assertTrue(h.findings(measures, baseline, show_known=True))

    # A 70-line function: a memo, not a failure.
    def test_long_function_memo(self):
        self.evil(long_function("longish", 70))
        h, baseline = self.health()
        found = h.findings(h.measure(self.root), baseline)
        self.assertEqual([k for k, w, _ in found if "longish()" in w], ["memo"])

    # The same 10 lines in two files: a copy memo.
    def test_copy_paste_memo(self):
        block = "".join("    total = total + %d * factor  # step\n" % i for i in range(10))
        for name in ("one", "two"):
            self.write("nxspoof/features/%s.py" % name, '"""\nnxspoof.features.%s - planted.\n"""\n\n# Add up.\ndef add_%s(factor):\n    total = 0\n%s    return total\n'
                       % (name, name, block))
        h, baseline = self.health()
        found = h.findings(h.measure(self.root), baseline)
        self.assertTrue(any("same 8+ code lines" in t and "one.py" in t for _, w, t in found), found)

    # A known file that balloons: a growth memo.
    def test_growth_memo(self):
        self.append("nxspoof/core/fmt.py", "".join("\nCONSTANT_%d = %d" % (i, i) for i in range(80)) + "\n")
        h, baseline = self.health()
        found = h.findings(h.measure(self.root), baseline)
        self.assertTrue(any(w == "nxspoof/core/fmt.py" and "grew from" in t for _, w, t in found), found)

    # Accepting with a reason lifts the limit, and the reason is kept.
    def test_accept_with_reason(self):
        self.evil(long_function("huge", 130))
        h, _ = self.health()
        path = os.path.join(self.root, "tools", "health_baseline.json")
        h.accept("planted for the test", h.measure(self.root), path=path)
        self.assertEqual(self.g.past_watchdog_limits(self.root), [])
        self.assertEqual(h.load_baseline(path)["history"][-1]["reason"], "planted for the test")

if __name__ == "__main__":
    unittest.main()
