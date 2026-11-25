"""
nxspoof.output.terminal - the Terminal window, in either style.

  technical   every log line, with a gray [HH:MM:SS] and colors (casual-only lines are skipped)
  casual      the friendly line when there is one; problems (level warn and worse) are always shown,
              with their technical text in gray under a "😕 Something went wrong" header

Colors can be turned off (--no-color). If the window is closed while
NXSPOOF runs, writing fails: the sink switches to /dev/null and everything keeps going (the log file
still gets every line, and the shutdown still runs).
"""

import os
import sys
import time

from nxspoof.output import colors as c

PROBLEM_HEADER = "😕 Something went wrong (details below and in the log file):"
MODES = ("technical", "casual")

# A bus sink printing events to the Terminal.
class TerminalSink(object):
    # mode: "technical"/"casual"; color_of(who) -> a marked name's color (registry.color_of); stream: sys.stdout by default.
    def __init__(self, mode="technical", colors=True, color_of=None, stream=None):
        if mode not in MODES:
            raise ValueError("unknown Terminal style %r (expected %s)" % (mode, " or ".join(MODES)))
        self.mode = mode
        self.colors = colors
        self.color_of = color_of
        self.stream = stream

    # Called by the bus for every event.
    def __call__(self, event):
        for line in self.lines(event):
            self._write(line)

    # The Terminal lines for one event (with color codes; they're removed when writing if colors are off).
    def lines(self, event):
        if self.mode == "technical":
            return self._technical(event)
        return self._casual(event)

    # Technical style: '[HH:MM:SS] TAG   text' for log lines, then their indented details.
    def _technical(self, event):
        line = event.technical_line()
        if line is None:
            return []
        stamp = c.paint("[%s]" % time.strftime("%H:%M:%S", time.localtime(event.ts)), "gray")
        lines = ["%s %s" % (stamp, c.render(line, event.technical_names(), self.color_of, tag=True))]
        return lines + ["%s %s" % (stamp, c.colorize(d)) for d in event.detail_lines()]

    # Casual style: the friendly text, else (for problems only) the technical text in gray; a problem's details after it,
    # except those its friendly text already says (setup's hints are in both).
    def _casual(self, event):
        out = []
        if event.casual:
            out.append(c.render(event.casual, event.casual_names, self.color_of))
        elif event.is_problem() and event.tech is not None:
            out += [PROBLEM_HEADER, "   " + c.paint(c.strip(event.technical_line()).strip(), "gray")]
        if event.is_problem():
            out += ["   " + c.paint(c.strip(d).strip(), "gray") for d in event.details if c.strip(d).strip() not in (event.casual or "")]
        return out

    # Print one line; if the window is gone, keep going silently.
    def _write(self, line):
        stream = self.stream or sys.stdout
        try:
            stream.write((line if self.colors else c.strip(line)) + "\n")
            stream.flush()
        except Exception:
            try:
                self.stream = open(os.devnull, "w")
            except OSError:
                pass
