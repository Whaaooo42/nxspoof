"""
nxspoof.macos.appfirewall - macOS's own firewall (System Settings > Network > Firewall).

It isn't NXSPOOF's pf firewall: it decides whether apps may accept incoming connections. With
"Block all incoming connections" on, consoles can't reach NXSPOOF's DNS and web test at all.
Reading the state works without admin rights. Pure: parse_state. Never logs.
"""

import re

from nxspoof.macos import shell

SOCKETFILTERFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"

# socketfilterfw --getglobalstate and --getblockall outputs -> {"enabled": bool|None, "block_all": bool|None}.
def parse_state(global_text, block_text):
    m = re.search(r"State = (\d)", global_text or "")
    enabled = None
    if m:
        enabled = m.group(1) != "0"
    elif "is enabled" in (global_text or ""):
        enabled = True
    elif "is disabled" in (global_text or ""):
        enabled = False
    block = (block_text or "").lower()
    if "disabled" in block:
        block_all = False
    elif "enabled" in block:
        block_all = True
    else:
        block_all = None
    if m and m.group(1) == "2":                      # state 2 = block all incoming connections
        block_all = True
    return {"enabled": enabled, "block_all": block_all}

# The firewall state right now (None values if it couldn't be read). run(cmd) -> (code, output) for tests.
def state(run=None):
    run = run or shell.run
    return parse_state(run([SOCKETFILTERFW, "--getglobalstate"])[1], run([SOCKETFILTERFW, "--getblockall"])[1])
