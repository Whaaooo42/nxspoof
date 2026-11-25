"""
nxspoof.macos.firewall - the pf firewall rules NXSPOOF loads into its own anchor.

The rules let a console reach the Mac only for DHCP, DNS (UDP) and the web test; everything else
from the console is dropped. Plan B (when macOS holds port 53) adds a redirect of DNS to another port.

Pure: build_rules, parse_counters, short_name, status_from, parse_token. Thin: apply, counters, flush, release (run
pfctl, need root). Never logs: apply() returns the status, errors raise FirewallError.

pf stays on while any program holds a token from `pfctl -E` (macOS counts them): apply() hands its token back in the
status, and release(token) gives it back at the end, so a run leaves pf as it found it (on Nov 24, 15 runs had left
15 tokens and pf on for two days).
"""

import re
import subprocess

TOKEN_RX = re.compile(r"Token\s*:\s*(\d+)")

ANCHOR = "com.apple/999.nxspoof"

# pfctl refused the rules (the message is pfctl's own).
class FirewallError(RuntimeError):
    pass

# ##############################################################################################################################################################
# ##  RULES AND COUNTERS (PURE)                                                                                                                               ##
# ##############################################################################################################################################################

# The pf rules for the hotspot interface and the Mac's address on it (plan B redirect if alt_dns_port).
def build_rules(iface, ip, alt_dns_port=None, firewall=True):
    rules = []
    if alt_dns_port:
        rules.append("rdr pass on %s inet proto udp from any to %s port 53 -> %s port %d" % (iface, ip, ip, alt_dns_port))
    if firewall:
        dns_ports = "{ 53, %d }" % alt_dns_port if alt_dns_port else "53"
        rules += [
            "block drop in quick on %s inet6 all" % iface,
            "pass in quick on %s inet proto udp from any port 68 to any port 67" % iface,
            "pass in quick on %s inet proto udp from any to %s port %s" % (iface, ip, dns_ports),
            "pass in quick on %s inet proto tcp from any to %s port 80" % (iface, ip),
            "block drop in quick on %s all" % iface,
        ]
    return rules

# `pfctl -vsr` text -> [(rule as pf shows it, packets)], in order.
def parse_counters(text):
    out, rule = [], None
    for line in (text or "").splitlines():
        if not line.startswith(" "):
            rule = line.strip() or None
            continue
        if rule and "Packets:" in line:
            try:
                out.append((rule, int(line.split("Packets:", 1)[1].split()[0])))
            except (ValueError, IndexError):
                pass
    return out

# The token `pfctl -E` hands back (pf stays on while a token is held), from its output, or None.
def parse_token(text):
    m = TOKEN_RX.search(text or "")
    return m.group(1) if m else None

# Short label for a rule as pf shows it: DHCP, DNS, web test, IPv6 (blocked), everything else (blocked).
def short_name(rule):
    if "inet6" in rule:
        return "IPv6 (blocked)"
    if "port = 67" in rule:
        return "DHCP"
    if "proto udp" in rule and rule.startswith("pass"):      # after DHCP, the one udp pass: DNS (on 53, or plan B's port)
        return "DNS"
    if "port = 80" in rule:
        return "web test"
    if rule.startswith("block"):
        return "everything else (blocked)"
    return rule

# Is the firewall really in place? From pfctl's outputs -> {"loaded", "enabled", "hooked", "ok"}.
def status_from(iface, anchor_rules, pf_info, main_rules, firewall=True):
    loaded = ("block drop in quick on %s all" % iface) in anchor_rules if firewall else bool(anchor_rules.strip())
    enabled = "Status: Enabled" in pf_info
    hooked = 'anchor "com.apple/*"' in main_rules
    return {"loaded": loaded, "enabled": enabled, "hooked": hooked, "ok": loaded and enabled and hooked}

# ##############################################################################################################################################################
# ##  RUNNING PFCTL (NEEDS ROOT)                                                                                                                              ##
# ##############################################################################################################################################################

# Run /sbin/pfctl with arguments (and optional input); returns the finished process.
def pfctl(*args, stdin=None):
    return subprocess.run(["/sbin/pfctl"] + list(args), input=stdin, capture_output=True, text=True)

# Load rules into NXSPOOF's anchor, make sure pf is on, and return status_from(...). Raises FirewallError.
# run: the pfctl runner (replaceable: the simulator uses a fake one).
def apply(iface, rules, firewall=True, run=None):
    run = run or pfctl
    if not rules:
        return {"loaded": False, "enabled": False, "hooked": False, "ok": False}
    r = run("-a", ANCHOR, "-f", "-", stdin="\n".join(rules) + "\n")
    if r.returncode != 0:
        raise FirewallError(r.stderr.strip() or "pfctl failed (code %d)" % r.returncode)
    enabled = run("-E")
    anchor = run("-a", ANCHOR, "-sr").stdout + run("-a", ANCHOR, "-sn").stdout
    return dict(status_from(iface, anchor, run("-s", "info").stdout, run("-sr").stdout, firewall),
                token=parse_token((enabled.stdout or "") + (enabled.stderr or "")))

# The packet counters of NXSPOOF's rules right now: [(rule, packets)].
def counters(run=None):
    return parse_counters((run or pfctl)("-a", ANCHOR, "-vsr").stdout)

# Remove every NXSPOOF rule (safe to call when none are loaded).
def flush(run=None):
    (run or pfctl)("-a", ANCHOR, "-F", "all")

# Give back the token apply() got: pf goes off again if nobody else holds one (macOS's Internet Sharing has its own).
def release(token, run=None):
    (run or pfctl)("-X", str(token))

# True if NXSPOOF rules are still loaded (e.g. after a crash).
def leftovers(run=None):
    run = run or pfctl
    return bool(run("-a", ANCHOR, "-sr").stdout.strip() or run("-a", ANCHOR, "-sn").stdout.strip())
