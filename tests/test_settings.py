"""
test_settings.py - unit tests for nxspoof.settings: schema, validation, tidy save, console edits.

The key check: the tidy rewrite of the real settings.json (a copy in tests/fixtures/) comes out
byte for byte identical, so "tidy" means the owner's existing layout. Validation is also compared
with what the loader read at the cutoff (tests/fixtures/golden.json), so settings keep being read the same way.
"""

import json
import os
import shutil
import tempfile
import time
import unittest

from nxspoof.core.paths import PROJECT_DIR
from nxspoof.settings import schema, store, transfer
from tests import golden

FIXTURE = os.path.join(PROJECT_DIR, "tests", "fixtures", "settings_current.json")

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# A store on a fresh copy of the fixture in a temporary folder (plus the events it emits).
def temp_store(text=None):
    folder = tempfile.mkdtemp(prefix="nxspoof-test-")
    path = os.path.join(folder, "settings.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(read_text(FIXTURE) if text is None else text)
    s = store.SettingsStore(path)
    s.load()
    return s, folder

# The tidy layout is the owner's current layout.
class TidyFormat(unittest.TestCase):
    # Rewriting today's settings.json changes nothing, byte for byte.
    def test_rewrite_of_the_real_file_is_identical(self):
        text = read_text(FIXTURE)
        self.assertEqual(store.format_settings(store.parse(text)), text)

    # Saving through the store also leaves the file identical (and no temporary file behind).
    def test_store_save_is_identical(self):
        s, folder = temp_store()
        s.save()
        self.assertEqual(read_text(s.path), read_text(FIXTURE))
        self.assertEqual(os.listdir(folder), ["settings.json"])
        shutil.rmtree(folder)

    # Emoji and accents in notes are kept as they are, not escaped.
    def test_non_ascii_kept(self):
        doc = store.parse(read_text(FIXTURE))
        doc["consoles"][0]["notes"].append("Café 🎮")
        text = store.format_settings(doc)
        self.assertIn('"Café 🎮"', text)
        self.assertEqual(json.loads(text), doc)

    # Empty consoles and unknown top-level keys keep a tidy shape.
    def test_shapes(self):
        text = store.format_settings({"_readme": "x", "spoof": {"firewall": False}, "zzz": [1, 2], "consoles": []})
        self.assertEqual(text, '{\n  "_readme": "x",\n\n  "spoof": {\n    "firewall": false\n  },\n\n  "zzz": [1, 2],\n\n  "consoles": []\n}\n')

# Reading and checking settings.
class Validation(unittest.TestCase):
    # The real file: two consoles, no warnings.
    def test_real_file_is_clean(self):
        effective, warnings = store.validate(store.parse(read_text(FIXTURE)))
        self.assertEqual(warnings, [])
        self.assertEqual([c["name"] for c in effective["consoles"]], ["Switch 1", "Switch 2"])

    # Same values as recorded, for every setting there still is (D30 moved the run-only ones to --flags).
    def test_same_as_recorded(self):
        new, _ = store.validate(store.parse(read_text(FIXTURE)))
        for section, values in golden("settings_loaded").items():  # the sections there were at the cutoff
            for key, value in values.items() if isinstance(values, dict) else []:
                if "%s.%s" % (section, key) in schema.BY_NAME:
                    self.assertEqual(new[section][key], value, "%s.%s" % (section, key))
        self.assertNotIn("hotspot", new)                                          # the hotspot is macOS's (D25, revised)
        self.assertEqual(new["bridge"], {"address": "172.16.0.1"})
        self.assertEqual([c["mac"] for c in new["consoles"]], golden("settings_console_macs"))

    # Broken JSON gives the old, clear message: line, column, Python's reason, then the advice.
    # (Python's own reason wording differs between versions, e.g. 3.15 names the trailing comma.)
    def test_broken_json_message(self):
        with self.assertRaises(store.SettingsError) as e:
            store.parse('{\n  "spoof": { "firewall": true, }\n}\n')
        first, second = str(e.exception).split("\n")
        self.assertRegex(first, r"^settings\.json has an error on line 2, column \d+: \S")
        self.assertEqual(second, "Fix it (or rename the file to use the defaults) and start again.")

    # Bad values fall back to defaults with a warning; the document itself is untouched.
    def test_bad_values(self):
        doc = store.parse(read_text(FIXTURE))
        doc["ftp"]["port"] = "five thousand"
        doc["logs"]["keep_last"] = -3
        doc["consoles"][0]["color"] = "pink"
        effective, warnings = store.validate(doc)
        self.assertEqual(effective["ftp"]["port"], 5000)
        self.assertEqual(effective["logs"]["keep_last"], 0)
        self.assertEqual(effective["consoles"][0]["color"], "white")
        self.assertEqual(len(warnings), 3)
        self.assertEqual(doc["consoles"][0]["color"], "pink")

    # Unknown keys are reported and kept; consoles with a bad or duplicate MAC are ignored but kept.
    def test_unknown_and_unusable(self):
        doc = store.parse(read_text(FIXTURE))
        doc["ftp"]["turbo"] = True
        doc["consoles"].append({"name": "Broken", "mac": "not-a-mac"})
        doc["consoles"].append({"name": "Twin", "mac": "48:F1:EB:5A:22:C2"})
        effective, warnings = store.validate(doc)
        self.assertEqual(len(effective["consoles"]), 2)
        self.assertTrue(any("turbo" in w for w in warnings))
        self.assertTrue(any("not-a-mac" in w for w in warnings))
        self.assertTrue(any("listed twice" in w for w in warnings))
        self.assertIn('"turbo": true', store.format_settings(doc))
        self.assertIn('"not-a-mac"', store.format_settings(doc))

    # MAC addresses are normalized in the effective view (macOS drops leading zeros).
    def test_mac_normalized(self):
        effective, _ = store.validate({"consoles": [{"name": "X", "mac": "A:1:2:3:4:5"}]})
        self.assertEqual(effective["consoles"][0]["mac"], "0a:01:02:03:04:05")

# The store: missing file, changing settings, editing consoles.
class Store(unittest.TestCase):
    # No file: built-in defaults, with the old message.
    def test_missing_file(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-test-")
        path = os.path.join(folder, "settings.json")
        s = store.SettingsStore(path)
        self.assertEqual(s.load(), "no settings file at %s - using built-in defaults" % path)
        self.assertEqual(s.get("logs.keep_last"), 0)
        self.assertEqual(s.consoles(), [])
        shutil.rmtree(folder)

    # Loading the real file gives the old message.
    def test_load_message(self):
        s = store.SettingsStore(FIXTURE)
        self.assertEqual(s.load(), "loaded settings_current.json (2 consoles)")

    # set(): checked, saved, reloadable, says live/restart (quiet: whoever asked announces it).
    def test_set(self):
        s, folder = temp_store()
        self.assertEqual(s.set("ftp.port", 5021), "live")
        self.assertEqual(s.set("logs.keep_last", 20), "restart")
        with self.assertRaises(ValueError):
            s.set("ftp.port", 70000)
        again = store.SettingsStore(s.path)
        again.load()
        self.assertEqual((again.get("ftp.port"), again.get("logs.keep_last")), (5021, 20))
        shutil.rmtree(folder)

    # add_console(): appended at the end, the rest of the file unchanged.
    def test_add_console(self):
        s, folder = temp_store()
        before = read_text(s.path)
        mac = s.add_console({"name": "Max's Switch", "model": "Switch OLED", "mac": "0A:1:2:3:4:5", "owner": "Max",
                             "color": "yellow", "ftp": {"port": 5000}, "notes": ["Added by a test."]})
        after = read_text(s.path)
        self.assertEqual(mac, "0a:01:02:03:04:05")
        self.assertTrue(after.startswith(before[:before.rindex("    }\n  ]")]))
        self.assertEqual(s.console(mac)["name"], "Max's Switch")
        self.assertEqual(store.format_settings(store.parse(after)), after)
        shutil.rmtree(folder)

    # Refused: a MAC already listed, a name already used (any case), a bad color, a bad MAC.
    def test_add_console_refusals(self):
        s, folder = temp_store()
        before = read_text(s.path)
        for entry in ({"name": "Again", "mac": "48:f1:eb:5a:22:c2"}, {"name": "switch 2", "mac": "aa:bb:cc:dd:ee:ff"},
                      {"name": "Pinky", "mac": "aa:bb:cc:dd:ee:ff", "color": "pink"}, {"name": "No", "mac": "nope"}):
            with self.assertRaises(ValueError):
                s.add_console(entry)
        self.assertEqual(read_text(s.path), before)
        shutil.rmtree(folder)

    # What a console is called never decides what's wrong with it: "my unknown one" with a bad color is refused; an
    # unknown field is only kept (both added and edited).
    def test_console_name_doesnt_hide_problems(self):
        s, folder = temp_store()
        with self.assertRaises(ValueError):
            s.add_console({"name": "my unknown one", "mac": "aa:bb:cc:dd:ee:ff", "color": "pink"})
        mac = s.add_console({"name": "my unknown one", "mac": "aa:bb:cc:dd:ee:ff", "sticker": "blue"})
        with self.assertRaises(ValueError):
            s.update_console(mac, {"color": "pink"})
        s.update_console(mac, {"nickname": "u1"})
        self.assertEqual(s.console(mac)["name"], "my unknown one")
        shutil.rmtree(folder)

    # update_console() and remove_console().
    def test_update_and_remove(self):
        s, folder = temp_store()
        s.update_console("48:f1:eb:5a:22:c2", {"name": "Big Switch", "color": "green"})
        self.assertEqual(s.console("48:f1:eb:5a:22:c2")["name"], "Big Switch")
        with self.assertRaises(ValueError):
            s.update_console("48:f1:eb:5a:22:c2", {"name": "SWITCH 1"})
        s.remove_console("dc:68:eb:5a:11:c1")
        self.assertIsNone(s.console("dc:68:eb:5a:11:c1"))
        with self.assertRaises(ValueError):
            s.remove_console("dc:68:eb:5a:11:c1")
        shutil.rmtree(folder)

    # on_saved runs after each save (used to hand the file back to the owner).
    def test_on_saved(self):
        s, folder = temp_store()
        saved = []
        s.on_saved = saved.append
        s.set("logs.keep_last", 10)
        self.assertEqual(saved, [s.path])
        shutil.rmtree(folder)

# The schema itself.
class Schema(unittest.TestCase):
    # Every field has a help text, a known kind and a valid default.
    def test_fields_are_complete(self):
        for f in schema.FIELDS:
            self.assertTrue(f.help, f.name)
            self.assertIn(f.apply, ("live", "restart"), f.name)
            self.assertEqual(f.check(f.default), (f.default, None), f.name)

    # The defaults are the recorded ones, for every setting there still is.
    def test_defaults_same_as_recorded(self):
        for section, values in golden("default_settings").items():
            for key, value in values.items() if isinstance(values, dict) else []:
                if "%s.%s" % (section, key) in schema.BY_NAME:
                    self.assertEqual(schema.defaults()[section][key], value, "%s.%s" % (section, key))

# Export and import: the tidy text out; another file in, checked first, the old one kept (settings/transfer.py previews it).
class ExportImport(unittest.TestCase):
    # A store on a copy of the real settings file, in a temporary folder.
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-transfer-")
        self.path = os.path.join(self.dir, "settings.json")
        shutil.copy(FIXTURE, self.path)
        self.store = store.SettingsStore(self.path)
        self.store.load()

    # Remove the folder.
    def tearDown(self):
        shutil.rmtree(self.dir)

    # The exported text is the tidy file itself.
    def test_export(self):
        self.assertEqual(self.store.export_text(), read_text(self.path))
        out = self.store.export_to(os.path.join(self.dir, "out.json"))
        self.assertEqual(read_text(out), read_text(self.path))

    # The preview says what changes; describe() says it in one line; nothing is written.
    def test_preview(self):
        data = json.loads(read_text(self.path))
        data["consoles"] = data["consoles"][:1]
        data["ftp"]["port"] = 2121
        change = transfer.preview(self.store, json.dumps(data))
        self.assertEqual((change["consoles_removed"], change["settings"]), (["Switch 2"], [["ftp.port", 5000, 2121]]))
        self.assertEqual(transfer.describe(change), "1 console removed (Switch 2); 1 setting changed (ftp.port 5000 -> 2121)")
        self.assertEqual(transfer.describe(transfer.preview(self.store, read_text(self.path))), "nothing changes (the same settings)")
        self.assertEqual(self.store.get("ftp.port"), 5000)

    # Import replaces everything, keeps the old file next to it; a broken file changes nothing.
    def test_replace(self):
        before = read_text(self.path)
        with self.assertRaises(store.SettingsError):
            self.store.replace("{ broken")
        self.assertEqual(read_text(self.path), before)
        data = json.loads(before)
        data["ftp"]["port"] = 2121
        warnings, backup = self.store.replace(json.dumps(data), now=time.mktime((2026, 11, 17, 9, 0, 0, 0, 0, -1)))
        self.assertEqual((warnings, os.path.basename(backup)), ([], "settings.json.before-import-2026-11-17_09-00-00"))
        self.assertEqual(read_text(backup), before)
        self.assertEqual(self.store.get("ftp.port"), 2121)
        self.assertEqual(json.loads(read_text(self.path))["ftp"]["port"], 2121)

if __name__ == "__main__":
    unittest.main()
