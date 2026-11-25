"""
nxspoof.dashboard.controls - the dashboard's own controls (following a run, app/runfollow.py adds "stop" and
"open_filezilla").

  add_console      save a console to settings.json (MAC, name, model, owner, color), recognized at once:
                   one that connected (from its card), or any console by its MAC (the "Add a console" form)
  edit_console     change a console in settings.json: name, model, owner, color, serial, system
                   version, FTP port, notes; only what really changed is saved, and logged one by one
  remove_console   take a console out of settings.json; if it's connected it stays in the run as an
                   unknown console, so it can be added back from its card
  set_settings     Configure's one Save: several settings (settings/schema.py) checked (all or nothing), saved in one
                   write, each logged with when it takes effect ("applies now" for live settings, "next start" for the
                   others, or kept by an option)

Every change goes through the settings store (checked, tidy rewrite, handed back to you) and is
announced (console.added / console.changed / console.removed), so the rest of NXSPOOF follows at once.
More come with the next features (build plan step 10): decide on a request.
Bad input raises ControlError, which the page shows as the reason.
"""

import json
import os
import re
import time

from nxspoof.core.controls import ControlError
from nxspoof.core.fmt import norm_mac
from nxspoof.core.text import compose
from nxspoof.settings import schema, transfer
from nxspoof.settings.store import SettingsError, valid_mac

CONSOLE_COLORS = schema.COLORS                # the one list (settings/schema.py)
TEXT_FIELDS = (("name", 40), ("owner", 40), ("serial", 40), ("system_version", 40))
MAX_NOTES, MAX_NOTE = 30, 300

# ##############################################################################################################################################################
# ##  CHECKING WHAT THE PAGE SENT                                                                                                                             ##
# ##############################################################################################################################################################

# One line of text: spaces tidied, at most `limit` characters.
def clean_text(value, limit):
    return " ".join(str(value if value is not None else "").split())[:limit]

# Notes: a list or one text with a note per line -> non-empty lines (at most MAX_NOTES of MAX_NOTE characters).
def clean_notes(value):
    lines = value if isinstance(value, list) else str(value or "").split("\n")
    notes = [clean_text(l, MAX_NOTE) for l in lines]
    return [n for n in notes if n][:MAX_NOTES]

# A port number from the page (ControlError if it isn't 1-65535).
def clean_port(value):
    try:
        port = int(str(value).strip())
    except ValueError:
        raise ControlError("the FTP port must be a number")
    if not 1 <= port <= 65535:
        raise ControlError("the FTP port must be between 1 and 65535")
    return port

# The text fields the page changed -> {field: new text} (a name can't be emptied).
def text_changes(payload, entry):
    changes = {}
    for key, limit in TEXT_FIELDS:
        value = clean_text(payload[key], limit) if key in payload else None
        if value is not None and value != (entry.get(key) or ""):
            changes[key] = value
    if changes.get("name") == "":
        raise ControlError("please give it a name")
    return changes

# A model from the page: one of schema.MODEL_NAMES, "" (not set), or the console's current text kept as is.
def model_value(raw, current=""):
    model = clean_text(raw, 60)
    if model and model not in schema.MODEL_NAMES and model != (current or ""):
        raise ControlError("model must be one of: " + ", ".join(schema.MODEL_NAMES))
    return model

# The model, if the page changed it -> {"model": ...} or {}.
def model_change(payload, entry):
    if "model" not in payload:
        return {}
    model = model_value(payload["model"], entry.get("model"))
    return {"model": model} if model != (entry.get("model") or "") else {}

# The color, if the page changed it -> {"color": ...} or {} (ControlError for an unknown color).
def color_change(payload, entry):
    color = str(payload.get("color", entry.get("color")))
    if color == entry.get("color"):
        return {}
    if color not in CONSOLE_COLORS:
        raise ControlError("color must be one of: " + ", ".join(CONSOLE_COLORS))
    return {"color": color}

# The notes, if the page changed them -> {"notes": [...]} or {}.
def notes_change(payload, entry):
    notes = clean_notes(payload["notes"]) if "notes" in payload else None
    return {"notes": notes} if notes is not None and notes != list(entry.get("notes") or []) else {}

# The FTP port, if the page changed it -> {"ftp": {...}} or {}; compared with the port in force, so an untouched field changes nothing.
def ftp_change(payload, entry, effective_port):
    if not str(payload.get("ftp_port", "")).strip():
        return {}
    port, ftp = clean_port(payload["ftp_port"]), dict(entry.get("ftp") or {})
    if port == (ftp.get("port") or effective_port):
        return {}
    ftp["port"] = port
    return {"ftp": ftp}

# The changes the page asks for, compared with the console's current entry -> {field: new value} (only real changes).
# effective_port: the FTP port in force for it now (its own, or the global one).
def wanted_changes(payload, entry, effective_port=None):
    changes = text_changes(payload, entry)
    changes.update(model_change(payload, entry))
    changes.update(color_change(payload, entry))
    changes.update(notes_change(payload, entry))
    changes.update(ftp_change(payload, entry, effective_port))
    return changes

# A setting's value as the page sent it -> the right type for its field (ControlError if it can't be one).
def setting_value(field, raw):
    if field.kind == "bool":
        word = str(raw).strip().lower()
        if isinstance(raw, bool) or word in ("true", "false"):
            return raw if isinstance(raw, bool) else word == "true"
        raise ControlError("%s must be on or off" % field.name)
    if field.kind in ("int", "number"):
        try:
            number = float(str(raw).strip())
        except ValueError:
            raise ControlError("%s must be a number" % field.name)
        if field.kind == "int" and number != int(number):
            raise ControlError("%s must be a whole number" % field.name)
        return int(number) if number == int(number) else number
    if field.kind == "text":
        return clean_text(raw, 200)
    raise ControlError("%s is edited in settings.json itself" % field.name)

# The values Configure's Save sent, each checked: (changes {name: (field, value, before)} for those that really change,
# problems ["name: reason"]). Nothing is written here.
def checked_values(store, values):
    changes, problems = {}, []
    for name, raw in sorted(values.items()):
        try:
            field = schema.field(str(name))
            value = setting_value(field, raw)
        except KeyError:
            problems.append("%s: no such setting" % name)
            continue
        except ControlError as e:
            problems.append("%s: %s" % (name, str(e).replace(name + " ", "", 1)))
            continue
        _, problem = field.check(value)                              # its range and choices (the store checks again on saving)
        if problem:
            problems.append("%s: %s" % (name, re.sub(r" \(using .*\)$", "", problem.replace(name + " ", "", 1))))
        elif value != store.get(field.name):
            changes[field.name] = (field, value, store.get(field.name))
    return changes, problems

# When a saved setting takes effect, in words for the log line.
def takes_effect(field, option):
    if option:
        return "saved; this run keeps %s, the change applies from the next start" % option
    return "applies now" if field.apply == "live" else "saved; takes effect at the next start"

# The changes in words for the log line: "name Switch 2 -> Big Switch, color magenta -> cyan, FTP port 5000 -> 5001".
def describe_changes(changes, entry):
    words = []
    for key, value in sorted(changes.items()):
        if key == "notes":
            words.append("notes (%d -> %d lines)" % (len(entry.get("notes") or []), len(value)))
        elif key == "ftp":
            words.append("FTP port %s -> %s" % ((entry.get("ftp") or {}).get("port", "default"), value["port"]))
        else:
            words.append("%s %s -> %s" % (key.replace("_", " "), entry.get(key) or "(empty)", value or "(empty)"))
    return ", ".join(words)

# ##############################################################################################################################################################
# ##  THE CONTROLS                                                                                                                                            ##
# ##############################################################################################################################################################

MAX_IMPORT = 200000                          # bytes: a settings.json is a few KB; anything bigger isn't one

# The dashboard's 📥 Import (control "import_settings", the control center only, while nothing runs): payload {"text": another
# settings.json, "apply": false} -> what it would change (settings/transfer.py; nothing written); {"apply": true} -> imported
# (the current file kept as settings.json.before-import-<time>), one SETTINGS line with fact settings.imported.
def import_settings(bus, store, payload):
    p = payload or {}
    text = p.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_IMPORT:
        raise ControlError("pick a settings.json file (up to %d KB)" % (MAX_IMPORT // 1000))
    try:
        change = transfer.preview(store, text, "the chosen file")
        if not p.get("apply"):
            return {"ok": True, "preview": change, "summary": transfer.describe(change), "message": "Check what changes, then Import."}
        _, backup = store.replace(text, "the imported file")
    except SettingsError as e:
        raise ControlError(str(e))
    kept = os.path.basename(backup) if backup else "nothing (there was no settings file)"
    bus.log("SETTINGS", "imported from a file: %s; your previous settings are kept as %s" % (transfer.describe(change), kept),
            casual="📥 Settings imported. Your previous ones are kept as %s in the data folder." % kept, fact="settings.imported")
    return {"ok": True, "message": "Imported. Your previous settings are kept as %s." % kept}

# Register the dashboard's controls. clock: replaceable for tests (the note's date); overridden_by(name): the
# option that overrides a setting for this run, or None (the run's Context).
def register(controls, bus, store, registry, activity, clock=time.time, overridden_by=None):
    # True if the console was seen during this run.
    def seen(mac):
        with activity.lock:
            return mac in activity.records

    # The settings entry of a console that is in settings.json (ControlError otherwise).
    def known_entry(payload):
        mac = norm_mac(str(payload.get("mac", "")))
        entry = registry.entry(mac)
        if entry is None:
            raise ControlError("that console isn't in settings.json")
        return mac, entry

    # Dashboard "Add": a console that connected, or any console by its MAC; checked, saved (tidy rewrite), recognized at once.
    def add_console(payload):
        mac = norm_mac(str(payload.get("mac", "")))
        if not valid_mac(mac):
            raise ControlError("that isn't a MAC address like 48:f1:eb:5a:22:c2 (the Switch shows it in System Settings > Internet)")
        if registry.known(mac):
            raise ControlError("that console is already in settings.json")
        name, owner = (clean_text(payload.get(k, ""), 40) for k in ("name", "owner"))
        model = model_value(payload.get("model", ""))
        color = str(payload.get("color", "yellow"))
        if not name:
            raise ControlError("please give it a name")
        if color not in CONSOLE_COLORS:
            raise ControlError("color must be one of: " + ", ".join(CONSOLE_COLORS))
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(clock()))
        entry = {"name": name, "model": model, "mac": mac, "owner": owner, "color": color, "serial": "", "system_version": "",
                 "ftp": {"port": 5000}, "notes": ["Added from the dashboard on %s." % stamp]}
        try:
            store.add_console(entry)
        except ValueError as e:
            raise ControlError(str(e))
        bus.log("SETTINGS", compose("added console %s (%s) to settings.json from the dashboard", registry.name(mac), mac),
                casual=compose("✅ Saved %s to settings.json", registry.name(mac)), mac=mac, fact="console.added", name=name)
        return {"ok": True, "message": "Saved '%s' to settings.json" % name}

    # Dashboard "Edit": only what really changed is saved; the log line lists each change.
    def edit_console(payload):
        mac, entry = known_entry(payload)
        changes = wanted_changes(payload, entry, registry.ftp_settings(mac).get("port"))
        if not changes:
            return {"ok": True, "message": "Nothing changed."}
        before = registry.name(mac)
        try:
            store.update_console(mac, changes)
        except ValueError as e:
            raise ControlError(str(e))
        bus.log("SETTINGS", compose("changed console %s (%s) from the dashboard: %s", before, mac, describe_changes(changes, entry)),
                casual=compose("✏️ Saved the changes to %s", registry.name(mac)), mac=mac, fact="console.changed", changed=sorted(changes))
        return {"ok": True, "message": "Saved the changes to '%s'" % str(registry.name(mac))}          # a toast: plain on purpose

    # Dashboard "Remove": out of settings.json; a connected console stays in the run as unknown.
    def remove_console(payload):
        mac, _ = known_entry(payload)
        name = registry.name(mac)
        try:
            store.remove_console(mac)
        except ValueError as e:
            raise ControlError(str(e))
        bus.log("SETTINGS", compose("removed console %s (%s) from settings.json from the dashboard", name, mac),
                casual=compose("🗑️ Removed %s from settings.json", name), mac=mac, fact="console.removed")
        back = " It connected during this run, so its card offers to add it back." if seen(mac) else ""
        return {"ok": True, "message": "Removed '%s' from settings.json.%s" % (name, back)}

    # Dashboard Configure's Save: payload {"values": {setting name: value}}; each value checked (all or nothing), only real
    # changes saved, in one write; one SETTINGS line per change with when it takes effect (each line is its fact).
    def set_settings(payload):
        values = (payload or {}).get("values")
        if not isinstance(values, dict) or not values:
            raise ControlError("nothing to save")
        changes, problems = checked_values(store, values)
        if problems:
            raise ControlError("; ".join(problems))
        if not changes:
            return {"ok": True, "message": "Nothing changed."}
        try:
            store.set_many({n: v for n, (_, v, _) in changes.items()})
        except ValueError as e:
            raise ControlError(str(e))
        for name, (field, value, before) in sorted(changes.items()):
            when = takes_effect(field, overridden_by(name) if overridden_by else None)
            bus.log("SETTINGS", "changed %s %s -> %s from the dashboard (%s)" % (name, json.dumps(before), json.dumps(value), when),
                    casual="⚙️ Saved %s: %s (%s)" % (name, json.dumps(value), when),
                    fact="settings.changed", name=name, value=value, apply=field.apply)
        count = len(changes)
        return {"ok": True, "message": "Saved %d change%s." % (count, "" if count == 1 else "s"), "saved": sorted(changes)}
    controls.add("add_console", add_console)
    controls.add("edit_console", edit_console)
    controls.add("remove_console", remove_console)
    controls.add("set_settings", set_settings)
