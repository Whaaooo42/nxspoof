"""
nxspoof.settings.store - settings.json: load, validate, tidy save, console add/edit/remove, export and import.

Two views of the settings:
  * the document  - what you wrote (unknown keys included). This is what gets saved, so nothing
                    of yours is ever silently dropped or "corrected".
  * the effective - checked values: a bad value is reported as a warning and falls back to its
                    default; a console without a valid MAC is reported and ignored until fixed.

Saving is a tidy rewrite (D9): one fixed layout, the same as today's file
(sections separated by an empty line, consoles one field per line, notes one per line). Every save
is written to a temporary file, checked as valid JSON, then moved over the real one.

Writes are quiet: the store never talks to the event bus. Whoever asks for a change announces it with ONE
line that carries the fact (bus.log(..., fact="console.changed")), so the log always reads cause first and
listeners react after it: settings.changed, console.added, console.changed, console.removed (see
dashboard/controls.py).
"""

import copy
import json
import os
import re
import shutil
import threading
import time

from nxspoof.core.fmt import norm_mac
from nxspoof.core.text import compose, outcome
from nxspoof.settings import schema

MAC_RX = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
DEFAULT_README = ("Settings for NXSPOOF. Command-line options (--no-firewall etc.) override these. "
                  "Every field is explained in docs/reference.html (settings.json).")

# A settings.json that can't be used at all (broken JSON, or not a { } object).
class SettingsError(Exception):
    pass

# ##############################################################################################################################################################
# ##  PARSING AND VALIDATION (PURE)                                                                                                                           ##
# ##############################################################################################################################################################

# Parse settings text into a document dict; broken JSON raises SettingsError with the line and column.
def parse(text, label="settings.json"):
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise SettingsError("%s has an error on line %d, column %d: %s\n"
                            "Fix it (or rename the file to use the defaults) and start again." % (label, e.lineno, e.colno, e.msg))
    if not isinstance(doc, dict):
        raise SettingsError("%s must be one { ... } object at the top, not %s" % (label, type(doc).__name__))
    return doc

# True if text is a MAC address once normalized (e.g. '48:f1:eb:5a:22:c2').
def valid_mac(text):
    return isinstance(text, str) and bool(MAC_RX.match(norm_mac(text)))

# Check a document; returns (effective settings dict, list of warnings). Never changes the document.
def validate(doc):
    warnings = []
    effective = schema.defaults()
    for key, value in doc.items():
        if key.startswith("_") or key == "consoles":
            continue
        if key not in schema.SECTIONS:
            warnings.append("unknown section '%s' in settings.json (kept, not used)" % key)
            continue
        if not isinstance(value, dict):
            warnings.append("'%s' should be a group of settings in { } (using the defaults)" % key)
            continue
        for k, v in value.items():
            name = "%s.%s" % (key, k)
            if name not in schema.BY_NAME:
                warnings.append("unknown setting '%s' (kept, not used)" % name)
                continue
            good, problem = schema.BY_NAME[name].check(v)
            effective[key][k] = good
            if problem:
                warnings.append(problem)
    consoles = doc.get("consoles", [])
    if not isinstance(consoles, list):
        warnings.append("'consoles' should be a list in [ ] (no consoles used)")
        consoles = []
    seen = set()
    for i, entry in enumerate(consoles):
        good, problems = validate_console(entry, i)
        warnings.extend(problems)
        if good is None:
            continue
        if good["mac"] in seen:
            warnings.append("consoles[%d]: %s is listed twice (the first entry is used)" % (i, good["mac"]))
            continue
        seen.add(good["mac"])
        effective["consoles"].append(good)
    return effective, warnings

# Check one console entry; returns (effective console or None if unusable, list of warnings).
def validate_console(entry, index=0):
    good, wrong, kept = check_console(entry, index)
    return good, wrong + kept

# Check one console entry -> (effective console or None if unusable, what's wrong, what's kept but not used: unknown
# fields). Two lists, so adding or editing a console refuses only what's wrong, whatever a console is called.
def check_console(entry, index=0):
    where = "consoles[%d]" % index
    if not isinstance(entry, dict):
        return None, ["%s should be a { } entry (ignored)" % where], []
    label = "%s (%s)" % (where, entry.get("name") or entry.get("mac") or "no name")
    if not valid_mac(entry.get("mac")):
        return None, ["%s: mac %r isn't a MAC address like 48:f1:eb:5a:22:c2 (ignored until it's fixed)" % (label, entry.get("mac"))], []
    problems, kept = [], []
    good = {}
    for f in schema.CONSOLE_FIELDS:
        if f.key not in entry:
            good[f.key] = copy.deepcopy(f.default)
            continue
        value, problem = f.check(entry[f.key])
        good[f.key] = value
        if problem:
            problems.append("%s: %s" % (label, problem.replace(f.name, f.key, 1)))
    good["mac"] = norm_mac(entry["mac"])
    ftp = {}
    for k, v in (good.get("ftp") or {}).items():
        name = "ftp.%s" % k
        if name not in schema.BY_NAME:
            kept.append("%s: unknown ftp setting '%s' (kept, not used)" % (label, k))
            continue
        value, problem = schema.BY_NAME[name].check(v)
        ftp[k] = value
        if problem:
            problems.append("%s: %s" % (label, problem))
    good["ftp"] = ftp
    for k in entry:
        if k not in schema.CONSOLE_BY_KEY:
            kept.append("%s: unknown field '%s' (kept, not used)" % (label, k))
    return good, problems, kept

# ##############################################################################################################################################################
# ##  TIDY FORMAT (PURE)                                                                                                                                      ##
# ##############################################################################################################################################################

# One JSON value on one line (non-ASCII kept as is, e.g. emoji in notes).
def _inline(value):
    if isinstance(value, dict):
        return "{ %s }" % ", ".join("%s: %s" % (json.dumps(k, ensure_ascii=False), _inline(v)) for k, v in value.items()) if value else "{}"
    return json.dumps(value, ensure_ascii=False)

# Known keys in schema order first, then any others in their original order.
def _ordered(keys, known):
    keys = list(keys)
    return [k for k in known if k in keys] + [k for k in keys if k not in known]

# One console entry, one field per line; lists of texts one item per line; small groups inline.
def _format_console(entry, indent):
    pad, inner = " " * indent, " " * (indent + 2)
    keys = _ordered(entry.keys(), [f.key for f in schema.CONSOLE_FIELDS])
    lines = [pad + "{"]
    for n, k in enumerate(keys):
        v = entry[k]
        comma = "," if n < len(keys) - 1 else ""
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            lines.append("%s%s: [" % (inner, json.dumps(k, ensure_ascii=False)))
            lines += ["%s  %s%s" % (inner, json.dumps(x, ensure_ascii=False), "," if i < len(v) - 1 else "") for i, x in enumerate(v)]
            lines.append("%s]%s" % (inner, comma))
        else:
            lines.append("%s%s: %s%s" % (inner, json.dumps(k, ensure_ascii=False), _inline(v), comma))
    lines.append(pad + "}")
    return lines

# The whole document in the tidy layout (the text saved to settings.json).
def format_settings(doc):
    top = [k for k in doc if k.startswith("_")] + [k for k in schema.SECTIONS if k in doc]
    top += [k for k in doc if k not in top and k != "consoles"] + (["consoles"] if "consoles" in doc else [])
    out = ["{"]
    for n, key in enumerate(top):
        value = doc[key]
        comma = "," if n < len(top) - 1 else ""
        name = json.dumps(key, ensure_ascii=False)
        if key == "consoles" and isinstance(value, list):
            if not value:
                out.append("  %s: []%s" % (name, comma))
            else:
                out.append("  %s: [" % name)
                for i, entry in enumerate(value):
                    block = _format_console(entry, 4) if isinstance(entry, dict) else ["    " + _inline(entry)]
                    if i < len(value) - 1:
                        block[-1] += ","
                    out += block
                out.append("  ]%s" % comma)
        elif isinstance(value, dict) and value:
            known = [f.key for f in schema.FIELDS if f.section == key]
            keys = _ordered(value.keys(), known)
            out.append("  %s: {" % name)
            out += ["    %s: %s%s" % (json.dumps(k, ensure_ascii=False), _inline(value[k]), "," if i < len(keys) - 1 else "")
                    for i, k in enumerate(keys)]
            out.append("  }%s" % comma)
        else:
            out.append("  %s: %s%s" % (name, _inline(value), comma))
        if comma:
            out.append("")
    out.append("}")
    return "\n".join(out) + "\n"

# ##############################################################################################################################################################
# ##  THE STORE                                                                                                                                               ##
# ##############################################################################################################################################################

# settings.json with its document, effective view and warnings; all changes go through here.
class SettingsStore(object):
    # Point at a settings file; on_saved(path) runs after every save (e.g. hand the file back to you).
    def __init__(self, path, on_saved=None):
        self.path = path
        self.on_saved = on_saved
        self._lock = threading.RLock()
        self._doc = {}
        self._effective = schema.defaults()
        self.warnings = []
        self.loaded_from_file = False

    # Read and check the file (or use the defaults if it's missing); returns the SETTINGS message.
    def load(self):
        with self._lock:
            if not os.path.exists(self.path):
                self._doc = {"_readme": DEFAULT_README}
                self._doc.update(schema.defaults())
                self.loaded_from_file = False
                self._recheck()
                return "no settings file at %s - using built-in defaults" % self.path
            with open(self.path, encoding="utf-8") as f:
                self._doc = parse(f.read(), os.path.basename(self.path))
            self.loaded_from_file = True
            self._recheck()
            n = len(self._effective["consoles"])
            return "loaded %s (%d console%s)" % (os.path.basename(self.path), n, "" if n == 1 else "s")

    # Recompute the effective view and warnings from the document.
    def _recheck(self):
        self._effective, self.warnings = validate(self._doc)

    # The effective value of one setting, e.g. get("ftp.port").
    def get(self, name):
        f = schema.field(name)
        with self._lock:
            return copy.deepcopy(self._effective[f.section][f.key])

    # A copy of the effective settings (sections + consoles).
    def effective(self):
        with self._lock:
            return copy.deepcopy(self._effective)

    # A copy of the document (what would be saved).
    def document(self):
        with self._lock:
            return copy.deepcopy(self._doc)

    # The usable consoles (valid MAC, first of any duplicates), as copies.
    def consoles(self):
        with self._lock:
            return copy.deepcopy(self._effective["consoles"])

    # One usable console by MAC, or None.
    def console(self, mac):
        mac = norm_mac(mac)
        with self._lock:
            for c in self._effective["consoles"]:
                if c["mac"] == mac:
                    return copy.deepcopy(c)
        return None

    # Change one setting: checked, saved; returns "live" or "restart". Bad values raise ValueError.
    def set(self, name, value):
        f = schema.field(name)
        good, problem = f.check(value)
        if problem:
            raise ValueError(problem)
        with self._lock:
            section = self._doc.setdefault(f.section, {})
            if not isinstance(section, dict):
                raise ValueError("'%s' in settings.json isn't a { } group; fix it by hand first" % f.section)
            section[f.key] = good
            self._recheck()
            self.save()
        return f.apply

    # Change several settings at once ({name: value}): every value is checked first, then all are written in one save
    # (nothing is written if one is wrong). Raises ValueError with "name: problem" for each wrong one, joined by "; ".
    def set_many(self, values):
        fields = [(schema.field(n), v) for n, v in sorted(values.items())]
        checked = [(f, f.check(v)) for f, v in fields]
        problems = ["%s: %s" % (f.name, re.sub(r" \(using .*\)$", "", problem.replace(f.name + " ", "", 1))) for f, (_, problem) in checked if problem]
        if problems:
            raise ValueError("; ".join(problems))
        with self._lock:
            for f, (good, _) in checked:
                section = self._doc.setdefault(f.section, {})
                if not isinstance(section, dict):
                    raise ValueError("%s: '%s' in settings.json isn't a { } group; fix it by hand first" % (f.name, f.section))
                section[f.key] = good
            self._recheck()
            self.save()

    # Add a console (checked: valid and new MAC, unique name, known color); saved.
    def add_console(self, entry):
        entry = dict(entry)
        with self._lock:
            consoles = self._consoles_list()
            good, wrong, _ = check_console(entry, len(consoles))
            if good is None:
                raise ValueError(wrong[0])
            if self.console(good["mac"]) is not None:
                raise ValueError("that console (%s) is already in settings.json" % good["mac"])
            self._check_name_free(good.get("name"), None)
            if wrong:
                raise ValueError(wrong[0])
            entry["mac"] = good["mac"]
            consoles.append(entry)
            self._recheck()
            self.save()
        return good["mac"]

    # Change fields of a console; checked, saved. Raises ValueError for bad changes.
    def update_console(self, mac, changes):
        mac = norm_mac(mac)
        with self._lock:
            consoles = self._consoles_list()
            index = self._index_of(mac)
            updated = dict(consoles[index])
            updated.update(changes)
            if "mac" in changes and norm_mac(changes["mac"]) != mac and self.console(changes["mac"]) is not None:
                raise ValueError("another console already has the MAC %s" % norm_mac(changes["mac"]))
            good, wrong, _ = check_console(updated, index)
            if good is None or wrong:
                raise ValueError(wrong[0])
            if "name" in changes:
                self._check_name_free(changes["name"], mac)
            updated["mac"] = good["mac"]
            consoles[index] = updated
            self._recheck()
            self.save()
        return good["mac"]

    # Remove a console by MAC; saved.
    def remove_console(self, mac):
        mac = norm_mac(mac)
        with self._lock:
            consoles = self._consoles_list()
            del consoles[self._index_of(mac)]
            self._recheck()
            self.save()

    # The problems found when loading, as SETTINGS line texts with WARNING marked: "WARNING ftp.port should be ...".
    def warning_lines(self):
        return [compose("%s %s", outcome("WARNING", "warning"), w) for w in self.warnings]

    # The document's consoles list (created if missing); refuses to edit a broken one.
    def _consoles_list(self):
        consoles = self._doc.setdefault("consoles", [])
        if not isinstance(consoles, list):
            raise ValueError("'consoles' in settings.json isn't a [ ] list; fix it by hand first")
        return consoles

    # Position of a console in the document by MAC (ValueError if it isn't there).
    def _index_of(self, mac):
        for i, entry in enumerate(self._doc.get("consoles", [])):
            if isinstance(entry, dict) and valid_mac(entry.get("mac")) and norm_mac(entry["mac"]) == mac:
                return i
        raise ValueError("no console with the MAC %s in settings.json" % mac)

    # Refuse a name another console already uses (case-insensitive); empty names are allowed.
    def _check_name_free(self, name, own_mac):
        name = (name or "").strip().lower()
        if not name:
            return
        for c in self._effective["consoles"]:
            if c["mac"] != own_mac and (c.get("name") or "").strip().lower() == name:
                raise ValueError("another console is already called '%s'" % c["name"])

    # The settings as the tidy file says them (Export: dashboard 📤, nx_spoof.py settings export).
    def export_text(self):
        with self._lock:
            return format_settings(self._doc)

    # Write the tidy layout to another file (Export to a path); returns the path.
    def export_to(self, path):
        text = self.export_text()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if self.on_saved:
            self.on_saved(path)
        return path

    # Replace everything with another settings file's text (Import): checked first (SettingsError for a broken one, nothing
    # changed), the current file kept next to it as <name>.before-import-<date>_<time>, then saved in the tidy layout.
    # Returns (the new warnings, the safety copy's path or None when there was no file yet).
    def replace(self, text, label="the imported file", now=None):
        doc = parse(text, label)
        effective, warnings = validate(doc)
        with self._lock:
            backup = None
            if os.path.exists(self.path):
                backup = "%s.before-import-%s" % (self.path, time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(now)))
                shutil.copy2(self.path, backup)
                if self.on_saved:
                    self.on_saved(backup)
            self._doc, self._effective, self.warnings = doc, effective, warnings
            self.save()
        return warnings, backup

    # Write the tidy layout safely: temporary file, checked as valid JSON, then moved into place.
    def save(self):
        with self._lock:
            text = format_settings(self._doc)
            json.loads(text)
            folder = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            with open(tmp, encoding="utf-8") as f:
                json.load(f)
            os.replace(tmp, self.path)
            self.loaded_from_file = True
        if self.on_saved:
            self.on_saved(self.path)
