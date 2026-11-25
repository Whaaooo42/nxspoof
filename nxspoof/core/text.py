"""
nxspoof.core.text - text that knows which parts of it name a console, and which words are an outcome.

A console's name is never found in a line by searching for it afterwards (a model like "Nintendo
Switch 2", a test label like "Switch 2 connection test" or an old name would match too). Instead the
code that writes the line marks the reference when it writes it:

  name("Switch 2", mac)                          -> "Switch 2", marked as console mac
  compose("%s joined the Wi-Fi", label)          -> "Switch 2 (48:f1:...) joined the Wi-Fi", the name still marked

The same goes for the words that say how something went (BLOCKED, PASS, FAIL, 200 ok, WARNING, is UP,
went DOWN): the code that writes them marks them, never a search afterwards:
  outcome("BLOCKED", "blocked")                  -> "BLOCKED", marked as the outcome "=blocked"

Marked is a plain str (it prints, compares, joins and goes to the log file exactly like the text it
holds) plus `spans`: [(start, end, who)] where who is a console's MAC, SELFTEST, or "=" + an outcome
(OUTCOMES). Outputs (the Terminal, the dashboard) color exactly those stretches; nothing else is ever guessed.
Ordinary %-formatting of a Marked gives plain text (the marks are dropped, the text is unchanged):
use compose() to keep them.
"""

import re

SELFTEST = "self-test"                             # who for "Mac (self-test)": the Mac's own self-check
OUTCOMES = ("blocked", "fail", "pass", "warning", "up", "down")      # how something went; marked as "=" + one of these
SPEC_RX = re.compile(r"%(?:%|[-#0 +]*\d*(?:\.\d+)?[sdifrxX])")

# A str that also carries the stretches naming consoles: spans = ((start, end, who), ...).
class Marked(str):
    # text: the plain text; spans: (start, end, who) for each console reference in it.
    def __new__(cls, text="", spans=()):
        obj = str.__new__(cls, text)
        obj.spans = tuple((int(s), int(e), w) for s, e, w in spans)
        return obj

    # Short form for debugging.
    def __repr__(self):
        return "Marked(%s, %r)" % (str.__repr__(self), list(self.spans))

# A console reference: the whole text names `who` (a MAC, or SELFTEST).
def name(text, who):
    text = str(text)
    return Marked(text, [(0, len(text), who)] if text and who else [])

# An outcome word: the whole text is that outcome (one of OUTCOMES), e.g. outcome("BLOCKED", "blocked").
def outcome(text, kind):
    if kind not in OUTCOMES:
        raise ValueError("unknown outcome %r (expected one of %s)" % (kind, ", ".join(OUTCOMES)))
    text = str(text)
    return Marked(text, [(0, len(text), "=" + kind)] if text else [])

# True for a span's who that is an outcome ("=blocked"...), not a console.
def is_outcome(who):
    return str(who).startswith("=")

# The spans of any text (none for a plain str).
def spans_of(text):
    return getattr(text, "spans", ())

# %-format a template like the % operator, keeping the marks of Marked arguments (shifted to where they land).
def compose(template, *args):
    pieces, spans, used, pos = [], [], 0, 0
    for m in SPEC_RX.finditer(template):
        pieces.append(template[pos:m.start()])
        pos = m.end()
        if m.group(0) == "%%":
            pieces.append("%")
            continue
        if used >= len(args):
            raise TypeError("not enough arguments for the template %r" % template)
        arg = args[used]
        used += 1
        piece = m.group(0) % (arg,)
        start = sum(len(p) for p in pieces)
        inner = piece.find(str(arg)) if spans_of(arg) else -1          # where the text lands inside its padding
        if inner >= 0:
            spans += [(start + inner + s, start + inner + e, w) for s, e, w in spans_of(arg)]
        pieces.append(piece)
    if used != len(args):
        raise TypeError("not all arguments converted for the template %r" % template)
    pieces.append(template[pos:])
    return Marked("".join(pieces), spans)

# Join texts with a separator, keeping every mark.
def join(separator, texts):
    texts = list(texts)
    return compose(separator.replace("%", "%%").join(["%s"] * len(texts)), *texts) if texts else Marked("")
