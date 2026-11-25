"""
marks.py - shared test checks: every console reference and every outcome word in every line is marked
(nxspoof/core/text.py).

Lines name consoles through compose(), which marks where the name is; outputs color exactly the marks.
A line written the old way ("%s" % name) would show its name uncolored, or worse, invite guessing.
unmarked_references() replays what a run emitted and lists:
  - lines about a console that contain its name with no mark for that console;
  - marks whose text isn't that console's name (or "Mac (self-test)").
Nintendo's connection-test labels ("Switch 2 connection test") are values, not references to your
consoles: they're set aside before looking for an unmarked name.
unmarked_outcomes() lists outcome words (BLOCKED, PASS, FAIL, 200 ok, WARNING, is UP, went DOWN, UP) written
without their mark, except where the word is plain on purpose (PLAIN_ON_PURPOSE: a sentence, not an outcome).
"""

import re

from nxspoof.core.text import SELFTEST, is_outcome
from nxspoof.net.policy import TEST_HOSTS

OUTCOME_WORDS = re.compile(r"\bBLOCKED( \(403\))?|\bFAIL\b|\bPASS\b|\b200 ok\b|\bWARNING\b|\bis UP\b|\bwent DOWN\b|\bUP\b(?= \d)")
PLAIN_ON_PURPOSE = [re.compile(r"^DNS   listening on .*everything else BLOCKED\)$"),        # the policy, said at start
                    re.compile(r"^CHECK HTTP .* -> 200 ok$")]                                # the answer the self-test got (a value)

# Problems in a list of events: "TEXT (why)" for each line that breaks the rule. registry gives the names.
def unmarked_references(events, registry):
    problems = []
    for e in events:
        for text, spans in ((e.technical_line(), e.technical_names()), (e.casual, e.casual_names)):
            if not text:
                continue
            for start, end, who in spans:
                if is_outcome(who):
                    continue
                expected = "Mac (self-test)" if who == SELFTEST else str(registry.name(who))
                if text[start:end] != expected:
                    problems.append("%s (mark covers %r, not %r)" % (text, text[start:end], expected))
            values = text
            for label in TEST_HOSTS.values():
                values = values.replace(label, "")
            if e.mac and str(registry.name(e.mac)) in values and not any(who == e.mac for _, _, who in spans):
                problems.append("%s (names %s without marking it)" % (text, registry.name(e.mac)))
    return problems

# Outcome words in technical lines that aren't covered by an outcome mark -> ["LINE (word)"].
def unmarked_outcomes(events):
    problems = []
    for e in events:
        line = e.technical_line()
        if not line or any(rx.search(line) for rx in PLAIN_ON_PURPOSE):
            continue
        marked = [(s, en) for s, en, who in e.technical_names() if is_outcome(who)]
        for m in OUTCOME_WORDS.finditer(line):
            if not any(s <= m.start() and m.end() <= en for s, en in marked):
                problems.append("%s (%r not marked)" % (line, m.group(0)))
    return problems
