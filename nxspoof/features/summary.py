"""
nxspoof.features.summary - what happened during the run, printed when NXSPOOF stops.

Two versions from the same numbers (so they can never disagree):
  technical  SUMMARY lines in the log file and the technical Terminal (today's exact layout)
  casual     "📋 What happened:" with one plain line per console (casual Terminal and dashboard)

Time on the Wi-Fi comes from ConsoleActivity.time_on_wifi (one calculation, rounded the same way
in both versions). Consoles that aren't in settings.json get a ready-to-paste entry.
"""

import datetime
import time

from nxspoof.core.fmt import casual_duration, console_snippet_json, fmt_duration, plural
from nxspoof.core.text import compose, join, outcome

RULE = "-" * 64
PF_ORDER = ["DHCP", "DNS", "web test", "IPv6 (blocked)", "everything else (blocked)"]

# HH:MM:SS of a timestamp (local time).
def clock_time(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))

# The services a console was watched for, with their address (watched first, then any others seen).
def _services(r):
    names = list(r.service_urls) + [s for s in r.services if s not in r.service_urls]
    return [(s, r.service_urls.get(s, "?"), r.services.get(s, [])) for s in names]

# One console's block of the technical summary as [(text, mac)]: header, Wi-Fi visits, tests, services, blocked, User-Agents.
def console_block(r, activity, registry, now):
    mac = r.mac
    out = [(compose("%s  %s  %s", registry.label(mac=mac), mac, r.ip or "(no address)"), mac)]
    if r.joined:
        spans = ["%s -> %s" % (clock_time(j), clock_time(r.left[i]) if i < len(r.left) else "still on at stop")
                 for i, j in enumerate(r.joined)]
        out.append(("   on the Wi-Fi   : %s  (total %s)" % (", ".join(spans), fmt_duration(activity.time_on_wifi(mac, now))), mac))
    out.append(("   tests passed   : %s" % (", ".join("%d x %s" % (n, t) for t, n in r.tests.items()) if r.tests else "none"), mac))
    for service, url, events in _services(r):
        label = "%-15s" % (service + " server")
        if events:
            changes = join(", ", (compose("%s %s", outcome("UP", "up") if e == "UP" else outcome("went DOWN", "down"), clock_time(t)) for t, e in events))
            out.append((compose("   %s: %s -> %s", label, changes, url), mac))
        else:
            out.append(("   %s: never seen on %s" % (label, url), mac))
    if r.blocked:
        total = sum(r.blocked.values())
        out.append(("   blocked        : %s, %s" % (plural(total, "lookup"), plural(len(r.blocked), "different name")), mac))
        out += [("     %4d x %s" % (n, name), mac) for name, n in r.blocked.most_common()]
    return out + [("   User-Agent     : %s" % ua, mac) for ua in r.uas]

# The technical summary as [(text, mac, data)]: emitted as SUMMARY lines; data makes a line a fact for the structured
# log (summary.run: the run's length; summary.console: one console's block starts).
#   started/now: timestamps of the run; pf_counts: {"DHCP": n, ...} from the firewall, or None.
def technical_lines(activity, registry, started, now, pf_counts=None):
    out = [(RULE, None), ("run %s -> %s (%s)" % (clock_time(started), clock_time(now),
                                                 str(datetime.timedelta(seconds=now - started)).split(".")[0]), None)]
    facts = {1: {"fact": "summary.run", "seconds": int(now - started)}}
    records = activity.active_records()
    if not records:
        out.append(("no console connected during this run", None))
    unknown = []
    for r in records:
        facts[len(out)] = {"fact": "summary.console", "name": str(registry.label(mac=r.mac))}
        out += console_block(r, activity, registry, now)
        if not registry.known(r.mac):
            unknown.append(r.mac)
    if pf_counts:
        keys = [k for k in PF_ORDER if k in pf_counts] + [k for k in pf_counts if k not in PF_ORDER]
        allowed = "  ".join("%s %d" % (k, pf_counts[k]) for k in keys if "blocked" not in k)
        blocked = "  ".join("%s %d" % (k, pf_counts[k]) for k in keys if "blocked" in k)
        out.append(("firewall: allowed %s   ||   %s" % (allowed, blocked), None))
    for mac in unknown:
        out.append(("NEW console %s - paste into \"consoles\" in settings.json:" % mac, mac))
        out.append(("   %s" % console_snippet_json(mac), mac))
    out.append((RULE, None))
    return [(text, mac, facts.get(i, {})) for i, (text, mac) in enumerate(out)]

# The casual summary as [(text, mac)]: emitted as casual-only lines.
def casual_lines(activity, registry, now):
    out = [("📋 What happened:", None)]
    records = activity.active_records()
    for r in records:
        mac = r.mac
        passes = sum(r.tests.values())
        parts = []
        if r.joined:
            parts.append("here %s" % casual_duration(activity.time_on_wifi(mac, now)))
        if passes:
            parts.append("online check passed %s" % ("once" if passes == 1 else "%d times" % passes))
        else:
            parts.append("online check not passed")
        parts.append("%s to reach the internet blocked" % plural(sum(r.blocked.values()), "attempt"))
        out.append((compose("   %s: %s", registry.name(mac), ", ".join(parts)), mac))
        if not registry.known(mac):
            out.append(("   🙋 To remember it, paste this into \"consoles\" in settings.json:", mac))
            out.append(("      %s" % console_snippet_json(mac), mac))
    if not records:
        out.append(("   Nobody connected this time.", None))
    return out

# Emit both summaries on the bus: technical SUMMARY lines, then the casual lines.
def report(bus, activity, registry, started, now=None, pf_counts=None):
    now = time.time() if now is None else now
    for text, mac, data in technical_lines(activity, registry, started, now, pf_counts):
        bus.log("SUMMARY", text, mac=mac, **data)
    for text, mac in casual_lines(activity, registry, now):
        bus.say(text, mac=mac)
