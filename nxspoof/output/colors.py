"""
nxspoof.output.colors - Terminal colors: the line's tag (DNS, WIFI...), outcome words (BLOCKED, PASS...)
and console names, exactly like today's Terminal.

Messages are always plain text. Colors are added here, at the very end, and only for the Terminal:
the log file and the dashboard never see an escape code. Console names and outcome words are painted
exactly where the line marks them (core/text.py spans): a name in that console's color, an outcome in
its OUTCOME_COLORS color. Nothing is searched for, so a model like "Nintendo Switch 2" or a policy
sentence like "everything else BLOCKED" is never painted by mistake.
"""

import re

from nxspoof.core.text import SELFTEST, is_outcome

ANSI = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "white": "37", "gray": "90", "bold": "1"}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

TAG_COLORS = {"DNS": "blue", "HTTP": "blue", "WIFI": "cyan", "DHCP": "cyan", "FTP": "green",
              "NEW": "yellow+bold", "SHARING": "yellow", "FIREWALL": "green", "pf": "gray",
              "CHECK": "gray", "READY": "green+bold", "FATAL": "red+bold",
              "PROBLEM": "red+bold", "NOTE": "yellow", "LOG": "gray", "SETTINGS": "gray",
              "SUMMARY": "bold", "DASH": "green", "SETUP": "cyan", "DOCTOR": "cyan", "UNDO": "yellow",
              "START": "cyan", "CLEAN": "yellow"}
OUTCOME_COLORS = {"=blocked": "red", "=fail": "red+bold", "=pass": "green", "=warning": "yellow+bold", "=up": "green+bold", "=down": "yellow"}

# Wrap text in ANSI color codes, e.g. paint('ok', 'green+bold'); unknown colors leave it plain.
def paint(text, color):
    codes = ";".join(ANSI[c] for c in (color or "").split("+") if c in ANSI)
    return "\x1b[%sm%s\x1b[0m" % (codes, text) if codes else text

# Remove every color code (for the log file, the dashboard and no-color Terminals).
def strip(text):
    return ANSI_RE.sub("", text)

# Paint a line: its leading tag (tag=True), then exactly its marked stretches: console names in their colors
# (color_of(who); the Mac's self-test gray, not bold), outcome words in OUTCOME_COLORS. The rest stays plain.
def render(line, spans=(), color_of=None, tag=False):
    out, pos, head = [], 0, ""
    m = re.match(r"^(\S+)(?=\s|$)", line) if tag else None
    if m and m.group(1) in TAG_COLORS and not any(s < m.end() for s, _, _ in spans):
        head, pos = paint(m.group(1), TAG_COLORS[m.group(1)]), m.end()
    for start, end, who in sorted(spans):
        if start < pos or end > len(line):
            continue
        out.append(line[pos:start])
        if is_outcome(who):
            out.append(paint(line[start:end], OUTCOME_COLORS.get(who)))
        else:
            color = (color_of(who) if color_of else None) or "white"
            out.append(paint(line[start:end], color if who == SELFTEST else color + "+bold"))
        pos = end
    out.append(line[pos:])
    return head + "".join(out)

# Color a technical line's leading tag (DNS, WIFI...); it has no marks (a detail line).
def colorize(line):
    return render(line, tag=True)
