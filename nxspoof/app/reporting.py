"""
nxspoof.app.reporting - 🐞 Report a problem: the one engine behind the dashboard's button and `nx_spoof.py report`
(menu 6 → 4). features/report.py writes the report; this file gathers what it needs from this Mac.

  report_text(system, places, store, full)          the report: what kind of Mac, setup's checks, the logs, and the
                                                    names to replace (settings' consoles and owners, the Mac's and your
                                                    names, the hotspot's)
  report_command(args, system, places, store, out)  the Terminal's door: print it, save it (logs/reports/), copy it to
                                                    your clipboard, open GitHub's new issue page (unless --no-open, or no
                                                    repository yet)
  new_issue_url()                                   GitHub's new issue page for NXSPOOF, or None before the repository exists
  attach(center)                                    the dashboard's door: the control center's reader "report" (GET
                                                    /api/data/report?full=1), {"text", "issue_url"}; the page shows the
                                                    report first, then Copy and Open GitHub (js/report.js)
"""

import urllib.parse

from nxspoof import REPOSITORY, VERSION
from nxspoof.core.fmt import error_text, mac_line
from nxspoof.features import report
from nxspoof.output.logfile import save_report

ISSUE_TITLE = "Problem: "                    # what the new issue's title starts with (the template asks for the rest)

# GitHub's new issue page for NXSPOOF (the problem form, a title to finish), or None until the repository exists.
def new_issue_url(repository=REPOSITORY):
    if not repository:
        return None
    return "%s/issues/new?%s" % (repository.rstrip("/"), urllib.parse.urlencode({"template": "problem.yml", "title": ISSUE_TITLE}))

# Something from this Mac for the report; on failure, `fallback` and a note for the report's setup part.
def _ask(what, func, fallback, notes):
    try:
        return func()
    except Exception as e:
        notes.append({"state": "manual", "title": "(%s couldn't be read)" % what, "tech": error_text(e)})
        return fallback

# The report (Markdown). store: the settings store (its consoles' names and owners are replaced), or None; full: the
# newest run's whole log too.
def report_text(system, places, store=None, full=False):
    from nxspoof.app.starter import mac_checks
    notes = []
    facts = _ask("what kind of Mac", system.machine_facts, {}, notes)
    rows = _ask("setup's checks", lambda: mac_checks(places.program, system, places, places.logs_folder()), [], notes)
    words = dict(_ask("this Mac's names", system.personal_words, {}, notes))
    hotspot = _ask("the hotspot's settings", system.hotspot_settings, {}, notes).get("name")
    if hotspot:
        words[hotspot] = "<hotspot>"
    consoles = store.consoles() if store is not None else []
    about = {"mac": mac_line(facts), "version": VERSION, "program": places.program, "data": places.describe()}
    return report.build(places.logs_folder(), about, rows + notes, consoles=[c.get("name") for c in consoles],
                        people=[c.get("owner") for c in consoles], words=words, full=full)

# The dashboard's door (app/launch.py): the control center answers /api/data/report with the report and GitHub's page.
def attach(center):
    center.server.add_reader("report", lambda q: {"text": report_text(center.system, center.places, center.store, full=q.get("full") == "1"),
                                                  "issue_url": new_issue_url()})

# nx_spoof.py report [--full] [--no-open]: the report on the Terminal, saved, copied, and GitHub's new issue page opened.
def report_command(args, system, places, store, out):
    text = report_text(system, places, store, full=bool(args.full))
    out.write(text + "\n")
    saved = save_report(places.logs, text, give=system.give_to_user)
    out.write("🐞 Saved: %s\n" % saved)
    copied = system.copy_text(text)
    out.write("📋 Copied to your clipboard: read it, then paste it into the issue.\n" if copied else
              "📋 Couldn't copy it to your clipboard: open the saved file and copy it from there.\n")
    url = new_issue_url()
    if url is None:
        out.write("🔗 No repository address yet in this copy of NXSPOOF: send the saved file to whoever helps you.\n")
    elif args.no_open:
        out.write("🔗 New issue: %s\n" % url)
    else:
        system.open_url(url)
        out.write("🔗 Opened GitHub's new issue page: paste the report there (%s)\n" % url)
    return 0
