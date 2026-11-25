"""
nxspoof.net.dns - the DNS server the consoles use: test names -> the Mac, everything else -> blocked.

It only answers and reports: every query goes to on_query(client_ip, name, qtype_name, decision, answer),
and the console tracker decides what it means (counters, log line, events). Pure helpers
(build_reply, encode_query, parse_answer) are tested with real packets. Never logs.
"""

import socket
import struct
import threading

from nxspoof.core.fmt import error_text
from nxspoof.net.policy import BLOCK_IP
from nxspoof.net.sockets import open_socket

QTYPE_NAMES = {1: "A", 28: "AAAA", 255: "ANY", 65: "HTTPS", 12: "PTR", 33: "SRV"}

# ##############################################################################################################################################################
# ##  PACKETS (PURE)                                                                                                                                          ##
# ##############################################################################################################################################################

# Parse a query and build the reply -> (reply bytes, name, qtype) or (None, None, None) for junk.
# answer_for(name, qtype) returns the IPv4 address to answer with, or None for an empty "no error" reply.
def build_reply(data, answer_for):
    if len(data) < 12:
        return None, None, None
    tid, flags, qdcount = struct.unpack(">HHH", data[:6])
    if flags & 0x8000 or qdcount < 1:            # a response, or no question
        return None, None, None
    pos, labels = 12, []
    while True:
        if pos >= len(data):
            return None, None, None
        length = data[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0:                        # compression pointer: not used in questions
            return None, None, None
        labels.append(data[pos + 1:pos + 1 + length].decode("ascii", "replace"))
        pos += 1 + length
    if pos + 4 > len(data):
        return None, None, None
    qtype, _qclass = struct.unpack(">HH", data[pos:pos + 4])
    name = ".".join(labels)
    ip = answer_for(name, qtype) if qtype in (1, 255) else None
    answers = struct.pack(">HHHIH", 0xC00C, 1, 1, 60, 4) + socket.inet_aton(ip) if ip else b""
    header = struct.pack(">HHHHHH", tid, 0x8000 | 0x0400 | (flags & 0x0100) | 0x0080, 1, 1 if answers else 0, 0, 0)
    return header + data[12:pos + 4] + answers, name, qtype

# A DNS query packet for a name (A record by default), as a console would send it.
def encode_query(name, qtype=1, tid=0x1234):
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for part in name.split("."):
        q += bytes([len(part)]) + part.encode()
    return q + b"\x00" + struct.pack(">HH", qtype, 1)

# The IPv4 address in a one-answer reply (the last 4 bytes), or None if there's no answer.
def parse_answer(reply):
    if len(reply) < 12 or struct.unpack(">H", reply[6:8])[0] == 0:
        return None
    return socket.inet_ntoa(reply[-4:])

# ##############################################################################################################################################################
# ##  THE SERVER                                                                                                                                              ##
# ##############################################################################################################################################################

# Answers DNS on ip:port using the policy; reports each query to on_query.
class DnsServer(object):
    # answer_ip: the Mac's address given for test names; on_error(text) gets problems with single packets;
    # opener(kind, ip, port): who opens the socket (System.open_socket: the keeper during a split run; net/sockets.py by default).
    def __init__(self, ip, port, answer_ip, policy, on_query, on_error=None, opener=None):
        self.ip, self.port, self.answer_ip = ip, port, answer_ip
        self.policy, self.on_query, self.on_error = policy, on_query, on_error
        self.opener = opener or open_socket
        self.sock = None

    # Open ip:port (SO_REUSEADDR: works next to macOS's own DNS proxy on *:53) and start answering.
    # Raises OSError (EADDRINUSE when macOS holds the port -> the caller switches to plan B).
    def start(self):
        self.sock = self.opener("udp", self.ip, self.port)
        threading.Thread(target=self._loop, daemon=True).start()

    # The policy's answer for one name: the Mac for test names, the block address otherwise.
    def _answer_for(self, name, qtype):
        return self.answer_ip if self.policy.answers_with_mac(name) else BLOCK_IP

    # Answer queries until the socket is closed.
    def _loop(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                return
            try:
                reply, name, qtype = build_reply(data, self._answer_for)
                if reply is None:
                    continue
                self.sock.sendto(reply, addr)
                decision = self.policy.decide(name)
                answer = self._answer_for(name, qtype) if qtype in (1, 255) else None
                self.on_query(addr[0], name, QTYPE_NAMES.get(qtype, str(qtype)), decision, answer)
            except Exception as e:
                if self.on_error:
                    self.on_error(error_text(e))

    # Stop answering (closes the socket).
    def stop(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
