"""
nxspoof.app.pagewatch - the page IS the dashboard (D27): the control center closes by itself once no page shows it.

  PageWatch   checked about twice a second by the control center. A page counts while its live stream is open (a reload
              or a second tab counts too). FIRST_PAGE seconds for the first page to open, then PAGE_GONE seconds with no
              page, nothing starting and no This Mac task under way: the dashboard closes.
              While a run is active, a macOS dialog asks first (D30, the app's one cozy question): Stop NXSPOOF (a signal
              to the run, like ■ Stop) or Keep running (the default, and the answer when nobody clicks within 2 minutes:
              the Switch stays connected). Either way the dashboard closes; the next one shows the run if it goes on.
"""

import time

FIRST_PAGE = 90                              # seconds for the first page to open (the browser starting up)
PAGE_GONE = 1.5                              # seconds without any page before closing (a reload reconnects in ~0.05 s; owner: 30 s, then 5 s, felt late)
ASK = ("NXSPOOF is still running.\n\nYour Switch stays connected, but this Mac's Wi-Fi stays a hotspot until you stop it. "
       "To see NXSPOOF again, open the app.")
STOP, KEEP = "Stop NXSPOOF", "Keep running"

# Watches the page(s) of one control center.
class PageWatch(object):
    # center: the Center (server.viewers(), is_starting(), tasks, follow, system, note, close_keeping_run, request_stop);
    # clock: replaceable for tests.
    def __init__(self, center, clock=time.time):
        self.center, self.clock = center, clock
        self.seen, self.ever = clock(), False

    # Look now; closes the dashboard when no page has shown it for long enough. Returns True if it asked to close.
    def check(self):
        c, now = self.center, self.clock()
        pages = c.server.viewers()
        if pages or c.is_starting() or c.tasks.busy():
            self.ever = self.ever or pages > 0
            self.seen = now
            return False
        if now - self.seen < (PAGE_GONE if self.ever else FIRST_PAGE):
            return False
        if not c.follow.present():
            c.note("no page has shown the dashboard for %d s - closing it (open NXSPOOF again whenever you need it)" % (now - self.seen),
                   casual="👋 The dashboard's page was closed, so the dashboard closes too.")
            return c.request_stop("its page was closed")
        answer = c.system.ask_user(ASK, (STOP, KEEP), KEEP)
        c.note("the page was closed while NXSPOOF runs; the dialog's answer: %s" % (answer or "none within 2 minutes, so Keep running"))
        if answer != STOP and c.server.viewers():       # NXSPOOF opened again while the dialog was up: its page is back here
            c.note("a page shows the dashboard again: it stays open")
            self.seen = self.clock()
            return False
        if answer == STOP:
            c.follow.stop()
            return c.request_stop("its page was closed, run stopped")
        return c.close_keeping_run("its page was closed")
