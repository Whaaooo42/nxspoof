"""
nxspoof.dashboard.state - the snapshot the dashboard page shows (GET /api/state).

  run       phase, uptime, hotspot address, firewall, sharing, FTP watch, Terminal style, log file
  consoles  one entry per console: the ones in settings.json first, then unknown ones seen this run;
            known ones carry their settings (for the Edit form), unknown ones a paste-ready entry
  actions   the controls that can be used right now
  tags      the technical line types (so the page doesn't keep its own copy)
  models    the console models the forms offer (settings/schema.py CONSOLE_MODELS)
  settings  one row per setting (settings/schema.py): what it does, its type and limits, the value saved in
            settings.json, the value in force for this run, and the option overriding it (if any)

Same shape as the current dashboard's, so the page works unchanged; built only from the registry,
the activity and the run's facts (nothing is computed twice).
"""

import time

from nxspoof.core.fmt import console_snippet_json
from nxspoof.output.colors import TAG_COLORS
from nxspoof.settings import schema

SECTION_TITLES = {"bridge": "Thunderbolt Bridge", "logs": "Log files", "ftp": "FTP watch"}

# One console's entry for the page.
def console_entry(mac, registry, activity):
    entry = registry.entry(mac) or {}
    with activity.lock:
        r = activity.records.get(mac)
        events = list(r.services.get("FTP", [])) if r else []
        out = {
            "mac": mac, "name": registry.name(mac), "model": entry.get("model", ""), "owner": entry.get("owner", ""),
            "color": registry.color(mac), "known": registry.known(mac), "seen": bool(r and r.active()),
            "ip": r.ip if r else None, "present": bool(r and r.connected), "online": bool(r and r.online),
            "joined": list(r.joined) if r else [], "left": list(r.left) if r else [],
            "tests": sum(r.tests.values()) if r else 0, "blocked_total": sum(r.blocked.values()) if r else 0,
            "blocked": [[name, n] for name, n in r.blocked.most_common(100)] if r else [],
            "ftp": {"url": r.service_urls.get("FTP") if r else None, "up": bool(events and events[-1][1] == "UP"),
                    "events": [[ts, what] for ts, what in events]},
            "uas": list(r.uas) if r else []}
    if out["known"]:
        out["settings"] = {k: entry.get(k, "") for k in ("name", "model", "owner", "color", "serial", "system_version")}
        out["settings"].update(notes=list(entry.get("notes") or []), ftp_port=registry.ftp_settings(mac).get("port"))
    else:
        out["snippet"] = console_snippet_json(mac)
    return out

# One row per setting for Configure. in_force(name): what the run uses now; overridden_by(name): option or None.
def settings_rows(store, in_force=None, overridden_by=None):
    rows = []
    for f in schema.FIELDS:
        saved = store.get(f.name)
        rows.append({"name": f.name, "section": f.section, "section_title": SECTION_TITLES.get(f.section, f.section),
                     "label": f.key.replace("_", " "), "kind": f.kind, "help": f.help, "apply": f.apply,
                     "choices": list(f.choices or []), "min": f.minimum, "max": f.maximum, "default": f.default, "saved": saved,
                     "in_force": in_force(f.name) if in_force else saved, "overridden_by": overridden_by(f.name) if overridden_by else None})
    return rows

# The whole snapshot. run_info: the run's facts (phase, started, hotspot_ip, dry_run, firewall, ...);
# in_force / overridden_by: see settings_rows (the run's Context).
def build_state(run_info, registry, activity, store, controls, now=None, in_force=None, overridden_by=None):
    run = dict(run_info)
    run["now"] = time.time() if now is None else now
    known = [c["mac"] for c in store.consoles()]
    with activity.lock:
        seen = [mac for mac in activity.records if mac not in known]
    return {"run": run, "consoles": [console_entry(mac, registry, activity) for mac in known + seen],
            "actions": controls.available(), "tags": sorted(TAG_COLORS), "settings": settings_rows(store, in_force, overridden_by),
            "models": [{"name": n, "detail": d} for n, d in schema.CONSOLE_MODELS]}
