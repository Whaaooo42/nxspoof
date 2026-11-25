"""
nxspoof.features.filezilla - opening FileZilla on a console's FTP server, during a run.

Opened automatically when a console's FTP server comes up (if "open_filezilla" is on). The dashboard's
"Open in FileZilla" button is the dashboard's own control (app/runfollow.py): the page only names a
console; the address always comes from the run's records, never from the page.
"""

from nxspoof.core.fmt import error_text, error_trace

# Opens FileZilla and says so.
class FileZilla(object):
    # installed() -> bool and opener(url) come from macos.user (replaceable for tests).
    def __init__(self, bus, installed, opener):
        self.bus, self.installed, self.opener = bus, installed, opener

    # Open FileZilla on an ftp:// URL, logging what happened; True if it was opened.
    def open(self, url):
        if not self.installed():
            self.bus.log("FTP", "open_filezilla is on, but FileZilla isn't in /Applications",
                         casual="⚠️ I wanted to open FileZilla, but it isn't in your Applications folder.", level="warn")
            return False
        try:
            self.opener(url)
        except Exception as e:
            self.bus.log("FTP", "could not open FileZilla: %s" % error_text(e), level="problem", details=error_trace(e))
            return False
        self.bus.log("FTP", "opened FileZilla on %s" % url, casual="📂 Opened FileZilla on %s" % url)
        return True
