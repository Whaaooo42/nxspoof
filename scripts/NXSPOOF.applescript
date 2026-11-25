-- scripts/NXSPOOF.applescript - the source of NXSPOOF.app (the double-click way into NXSPOOF, with no Terminal window).
-- It runs scripts/open_dashboard.sh from the nx-spoof folder the app sits in, and quits; the dashboard opens in your browser.
-- The first time, macOS asks whether NXSPOOF may use files in your Documents folder: that's where nx-spoof lives (click Allow).
-- Rebuild the app after changing this file: double-click Create NXSPOOF.app.command (menu 8 then 6 runs it too)
on run
	set appPath to POSIX path of (path to me)
	do shell script quoted form of (appPath & "../scripts/open_dashboard.sh")
end run
