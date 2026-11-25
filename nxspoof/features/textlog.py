"""
nxspoof.features.textlog - reading a run's text log into records, for runs logged before the structured twin
(logs/<run>.jsonl, since 2026-11-15).

The one place that still knows the text log's patterns. It turns each line into the record the twin would have
(kind, level, tag, text, data), so features/runlog.py checks every run the same way. Those old files never
change again, so neither do these patterns; new runs are never read through here.
"""

import re

STAMP_RX = re.compile(r"^\[(\d\d:\d\d:\d\d)\] ")

# A log's lines without their timestamps.
def plain_lines(text):
    return [STAMP_RX.sub("", l) for l in (text or "").splitlines() if l.strip()]

# How an old run was stopped, from its text: "ctrl-c", "SIGHUP", "SIGTERM", "dashboard", or None if it didn't stop cleanly.
def stop_path(lines):
    if "Stopping..." not in lines:
        return None
    i = lines.index("Stopping...")
    before = lines[max(0, i - 3):i]
    if any(l.startswith("NOTE  stop requested from the dashboard") for l in before):
        return "dashboard"
    if any(l.startswith("NOTE  stop requested by nx_spoof.py stop") for l in before):
        return "stop command"
    for l in before:
        m = re.match(r"^NOTE  stop (?:signal received|requested) \((SIG\w+)[):]", l)
        if m:
            return m.group(1)
    return "ctrl-c"

# The old text log's patterns -> (kind, data from the match): checked in order, the first match wins.
TEXT_KINDS = [
    (re.compile(r"^LOG   NXSPOOF (?P<version>\S+) \(the nxspoof package\)$"), "run.version", {}),
    (re.compile(r"^CHECK .* PASS "), "selftest.check", {"passed": True}),
    (re.compile(r"^CHECK "), "selftest.check", {"passed": False}),
    (re.compile(r"^READY "), "run.ready", {}),
    (re.compile(r"^FIREWALL ON "), "firewall.on", {}),
    (re.compile(r"^pf    FAILED"), "firewall.failed", {}),
    (re.compile(r"^pf    firewall rules removed$"), "firewall.removed", {}),
    (re.compile(r"^DHCP  gave address "), "console.address", {"via": "dhcp"}),
    (re.compile(r"^HTTP  .* -> 200 ok "), "log", {"passed": True}),
    (re.compile(r"^DNS   .+ \(\d+\.\d+\.\d+\.\d+\) asked .* -> BLOCKED$"), "console.blocked", {}),
    (re.compile(r"^FTP   .*FTP server is UP"), "service.up", {}),
    (re.compile(r"^FTP   .*FTP server went DOWN"), "service.down", {}),
    (re.compile(r"^WIFI  (?P<name>.+?) \([0-9a-f:]{17}\) joined the Wi-Fi"), "console.joined", {}),
    (re.compile(r"^WIFI  (?P<name>.+?) \([0-9a-f:]{17}\) left the Wi-Fi"), "console.left", {}),
    (re.compile(r"^SUMMARY run .*\((?P<duration>\d+:\d\d:\d\d)\)"), "summary.run", {}),
    (re.compile(r"^SUMMARY (?P<name>.+?)  (?P<mac>[0-9a-f:]{17})  "), "summary.console", {}),
    (re.compile(r"^SHARING turned Internet Sharing OFF"), "sharing.off", {"ok": True}),
    (re.compile(r"^SHARING could not turn it off"), "sharing.off", {"ok": False}),
    (re.compile(r"^NOTE  stop (requested|signal received)"), "run.stop_requested", {}),
    (re.compile(r"^Stopping\.\.\.$"), "run.stopping", {}),
    (re.compile(r"^Stopped\.$"), "run.stopped", {}),
]

# An old text log -> records, as the structured twin would have them (the kinds and data the checks read).
def records_from_text(text):
    raw = (text or "").splitlines()
    lines = plain_lines(text)
    how = stop_path(lines)
    stamps = [m.group(1) for m in (STAMP_RX.match(l) for l in raw if l.strip())]
    records = []
    for n, line in enumerate(lines):
        first, _, rest = line.partition(" ")
        tag = first if rest and (first == "pf" or (first.isalpha() and first.isupper())) else ""
        r = {"t": stamps[n] if n < len(stamps) else "", "kind": "log", "tag": tag, "tech": rest.lstrip(" ") if tag else line,
             "level": "fatal" if tag == "FATAL" else "problem" if tag == "PROBLEM" else "warn" if "WARNING" in line else "info"}
        for rx, kind, data in TEXT_KINDS:
            m = rx.search(line)
            if m:
                r["kind"], r["data"] = kind, dict(data, **{k: v for k, v in m.groupdict().items()})
                break
        if r["kind"] == "run.stopping":
            r["data"] = {"how": how}
        r["mac"] = r.get("data", {}).get("mac")
        records.append(r)
    return records
