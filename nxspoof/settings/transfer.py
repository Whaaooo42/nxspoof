"""
nxspoof.settings.transfer - what importing another settings.json would change (the dashboard's 📥 Import preview and
`nx_spoof.py settings import`), before anything is written. Pure: reading and comparing only; the store does the import.

  preview(store, text, label)   {"consoles_added", "consoles_removed", "consoles_changed": [names], "settings": [[name, now,
                                then]], "warnings": [...]} (SettingsError for a file that can't be used at all)
  describe(change)              the same in one line, for the log and the page
"""

from nxspoof.settings import schema, store as store_module

# What importing `text` would change, compared with what the store has now (SettingsError if the text can't be used).
def preview(store, text, label="the file"):
    new, warnings = store_module.validate(store_module.parse(text, label))
    old = store.effective()
    now, then = {c["mac"]: c for c in old["consoles"]}, {c["mac"]: c for c in new["consoles"]}
    name = lambda c: c.get("name") or c["mac"]
    return {"consoles_added": [name(then[m]) for m in then if m not in now],
            "consoles_removed": [name(now[m]) for m in now if m not in then],
            "consoles_changed": [name(then[m]) for m in then if m in now and then[m] != now[m]],
            "settings": [[f.name, old[f.section][f.key], new[f.section][f.key]] for f in schema.FIELDS
                         if old[f.section][f.key] != new[f.section][f.key]],
            "warnings": list(warnings)}

# One line saying what an import changes ("2 consoles added (A, B); 1 setting changed (ftp.port 5000 -> 2121)").
def describe(change):
    parts = []
    for key, word in (("consoles_added", "added"), ("consoles_removed", "removed"), ("consoles_changed", "changed")):
        names = change[key]
        if names:
            parts.append("%d console%s %s (%s)" % (len(names), "" if len(names) == 1 else "s", word, ", ".join(names)))
    if change["settings"]:
        parts.append("%d setting%s changed (%s)" % (len(change["settings"]), "" if len(change["settings"]) == 1 else "s",
                                                     ", ".join("%s %s -> %s" % (n, a, b) for n, a, b in change["settings"])))
    return "; ".join(parts) or "nothing changes (the same settings)"
