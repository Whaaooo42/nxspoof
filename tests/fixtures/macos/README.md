# macOS samples for the tests

The samples marked Real came from the owner's Mac; every hardware address in them (the consoles', the Mac's network interfaces and the IPv6 addresses built from them) and the home network's address were replaced with made-up ones (2026-11-17) before NXSPOOF went public. Only the Nintendo maker prefix of the consoles' addresses is real.

| File | Source |
|---|---|
| `dhcpd_leases` | **Real**: `/var/db/dhcpd_leases` on the owner's Mac (2026-11-13), both Switches |
| `ifconfig_sharing_off.txt` | **Real**: `/sbin/ifconfig` with Internet Sharing off (2026-11-13) |
| `ifconfig_sharing_on.txt` | **Constructed**: the real file above plus a `bridge100` block in macOS's format, as seen when sharing was on |
| `hardware_ports.txt` | **Real**: `networksetup -listallhardwareports` (2026-11-13) |
| `kernel_softap.log` | **Real**: kernel log lines from the airplane-mode test run, 2026-11-13 15:33-15:35 (Switch 2 joins, leaves, comes back; Switch 1 joins and leaves) |
| `pfctl_vsr.txt` | **Constructed**: `pfctl -a com.apple/999.nxspoof -vsr` format (needs root to capture), packet numbers from the real 15:33 run's summary |
| `scutil_list.txt`, `scutil_show_bridge.txt` | **Real**: `scutil` listing of network services, and the Thunderbolt Bridge service details (2026-11-13) |
| `networksetup_order.txt` | **Real**: `networksetup -listnetworkserviceorder` (2026-11-13) |
| `networksetup_order_no_bridge.txt` | **Constructed**: same format, a Mac without a Thunderbolt Bridge service and with one disabled service (`(*)`) |
| `networksetup_info_manual.txt` | **Real**: `networksetup -getinfo "Thunderbolt Bridge"` (2026-11-13), the fixed address setup gives it |
| `networksetup_info_dhcp.txt` | **Constructed**: same format for a service still on automatic (DHCP) |
