"""
nxspoof.net.sockets - the DNS (UDP) and web (TCP) servers' sockets, bound to one address.

On a Mac, a port under 1024 on one address needs root: during a split run the keeper (app/keeper.py) opens these
sockets and hands them to the run, which runs as you; otherwise the System opens them here (System.open_socket).
"""

import socket

KINDS = {"udp": socket.SOCK_DGRAM, "tcp": socket.SOCK_STREAM}

# A socket of kind "udp" or "tcp" bound to ip:port (SO_REUSEADDR: works next to macOS's own DNS proxy on *:53).
# Raises OSError (EADDRINUSE when the port is taken, EACCES without the rights for it).
def open_socket(kind, ip, port):
    s = socket.socket(socket.AF_INET, KINDS[kind])
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((ip, port))
    except OSError:
        s.close()
        raise
    return s
