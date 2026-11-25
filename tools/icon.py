#!/usr/bin/env python3
"""
icon.py - NXSPOOF's icon, drawn from shapes (private branding choice, 2026-11-17: the polite "ok").

Nintendo's test page answers with one word, "ok": the only "yes" NXSPOOF ever says. Two drawings of it:
  Classic   a white speech bubble saying "ok" on a green tile: NXSPOOF.app in Finder and the Dock (scripts/NXSPOOF.icns,
            put into the app by Create NXSPOOF.app.command)
  Minimal   just a bold white "ok" on green, no bubble (its details would blur at 16 pixels): the dashboard's and the
            docs' browser-tab icon (favicon.png, and favicon.ico for browsers that look for it, Safari among them), and
            the dashboard header's logo (logo.png, next to its name)
Classic also goes at the top of the README and the docs' home page (docs/images/icon.png, 256 pixels).
Every shape is a distance function in a 1024-unit square (macOS's icon grid: an 824-unit tile with 185-unit corners), so
each size is drawn directly, with smooth edges, by the standard library alone (PNG through zlib); macOS's iconutil packs
the .icns. The files are committed: nothing is drawn when NXSPOOF runs.

  python3 -B tools/icon.py                    # draw the icons again (scripts/NXSPOOF.icns, the favicons .png and .ico, docs/images/icon.png)
  python3 -B tools/icon.py --preview DIR      # also a sheet of both at every size, on light and dark (DIR/icon-sheet.png)

Sections: SHAPES · THE TWO DRAWINGS · PNG AND ICNS · MAIN
"""

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICNS = os.path.join(ROOT, "scripts", "NXSPOOF.icns")
FAVICONS = [os.path.join(ROOT, "nxspoof", "dashboard", "web", "favicon.png"), os.path.join(ROOT, "docs", "favicon.png")]
FAVICON_SIZE = 32                            # a 16-point browser tab on a Retina screen
FAVICON_ICO = (16, 32)                       # favicon.ico, next to each favicon.png: both sizes, each a PNG inside
PICTURE, PICTURE_SIZE = os.path.join(ROOT, "docs", "images", "icon.png"), 256   # the README's and the docs home page's
LOGO, LOGO_SIZE = os.path.join(ROOT, "nxspoof", "dashboard", "web", "logo.png"), 64   # the dashboard header's (shown at 32: sharp on Retina)
ICONSET = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"), (128, "128x128"), (256, "128x128@2x"),
           (256, "256x256"), (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")]
TOP, BOTTOM = (0x52, 0xc8, 0x88), (0x1f, 0x9d, 0x55)       # the tile's green, top to bottom (the dashboard's green at the bottom)
WHITE = (255, 255, 255)

# ##############################################################################################################################################################
# ##  SHAPES                                                                                                                                                  ##
# ##############################################################################################################################################################

# Signed distance to a rounded rectangle centered at (cx, cy), half sizes (hw, hh), corner radius r (negative inside).
def rounded_rect(x, y, cx, cy, hw, hh, r):
    qx, qy = abs(x - cx) - hw + r, abs(y - cy) - hh + r
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r

# Signed distance to a capsule: the segment (ax, ay)-(bx, by) drawn with a round pen of radius r.
def capsule(x, y, ax, ay, bx, by, r):
    px, py, vx, vy = x - ax, y - ay, bx - ax, by - ay
    t = max(0.0, min(1.0, (px * vx + py * vy) / (vx * vx + vy * vy)))
    return math.hypot(px - vx * t, py - vy * t) - r

# Signed distance to a ring: a circle of radius rad drawn with a pen of half width w.
def ring(x, y, cx, cy, rad, w):
    return abs(math.hypot(x - cx, y - cy) - rad) - w

# Signed distance to a triangle (any winding).
def triangle(x, y, a, b, c):
    best, sign = float("inf"), 0
    for (ax, ay), (bx, by) in ((a, b), (b, c), (c, a)):
        vx, vy, px, py = bx - ax, by - ay, x - ax, y - ay
        t = max(0.0, min(1.0, (px * vx + py * vy) / (vx * vx + vy * vy)))
        best = min(best, math.hypot(px - vx * t, py - vy * t))
        sign += 1 if vx * py - vy * px > 0 else -1
    return -best if abs(sign) == 3 else best

# The word "ok" in strokes: an "o" ring and a "k" of three capsules, centered at (cx, cy), scaled by k, pen half width w.
def ok_word(x, y, cx, cy, k, w):
    u, v = (x - cx) / k, (y - cy) / k
    o = ring(u, v, -102, 28, 72, w / k)
    stem = capsule(u, v, 78, -132, 78, 132, w / k)
    arm = capsule(u, v, 78, 58, 178, -50, w / k)
    leg = capsule(u, v, 114, 22, 184, 132, w / k)
    return min(o, stem, arm, leg) * k

# A smooth union of two distances: the shapes melt into each other over k units instead of meeting at a corner.
def smooth_min(a, b, k):
    h = max(0.0, min(1.0, 0.5 + 0.5 * (b - a) / k))
    return b + (a - b) * h - k * h * (1 - h)

# The tile: macOS's icon body (824 units, 185-unit corners) in the 1024 square.
def tile(x, y):
    return rounded_rect(x, y, 512, 512, 412, 412, 185)

# ##############################################################################################################################################################
# ##  THE TWO DRAWINGS                                                                                                                                        ##
# ##############################################################################################################################################################

# The bubble: a rounded body with a tail at its bottom left.
def bubble(x, y):
    body = rounded_rect(x, y, 512, 478, 316, 226, 226)
    tail = triangle(x, y, (345, 650), (262, 786), (470, 682)) - 6          # a slightly rounded tip
    return smooth_min(body, tail, 24)

# The green of the tile at height y.
def green(y):
    t = max(0.0, min(1.0, (y - 100) / 824))
    return tuple(TOP[i] + (BOTTOM[i] - TOP[i]) * t for i in range(3))

# One drawing's layers at (x, y), back to front: [(color, distance), ...] (a soft shadow first for Classic).
def layers(name, x, y):
    if name == "classic":
        return [(green(y), tile(x, y)), (WHITE, bubble(x, y)), (green(y), ok_word(x, y, 494, 478, 1.0, 34))]
    return [(green(y), tile(x, y)), (WHITE, ok_word(x, y, 506, 512, 1.5, 56))]

# Draw one drawing at size n -> rows of RGBA bytes (edges one pixel wide; a soft shadow under Classic's tile from 64 up).
def draw(name, n):
    s = 1024.0 / n
    shadow = name == "classic" and n >= 64
    rows = []
    for j in range(n):
        y = (j + 0.5) * s
        row = bytearray()
        for i in range(n):
            x = (i + 0.5) * s
            r = g = b = a = 0.0                      # premultiplied: each layer goes over what's below
            if shadow:
                a = 0.28 * max(0.0, min(1.0, 0.5 - rounded_rect(x, y, 512, 524, 412, 412, 185) / 22.0))
            if tile(x, y) < s:
                for color, d in layers(name, x, y):
                    cov = max(0.0, min(1.0, 0.5 - d / s))
                    r, g, b = color[0] * cov + r * (1 - cov), color[1] * cov + g * (1 - cov), color[2] * cov + b * (1 - cov)
                    a = cov + a * (1 - cov)
            row += bytes((int(r / a + 0.5), int(g / a + 0.5), int(b / a + 0.5), int(a * 255 + 0.5)) if a else (0, 0, 0, 0))
        rows.append(bytes(row))
    return rows

# ##############################################################################################################################################################
# ##  PNG AND ICNS                                                                                                                                            ##
# ##############################################################################################################################################################

# RGBA rows as the bytes of a PNG file.
def png_bytes(rows):
    # One PNG chunk: length, type, data, CRC.
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    w, h = len(rows[0]) // 4, len(rows)
    raw = b"".join(b"\x00" + r for r in rows)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")

# Write RGBA rows as a PNG file.
def write_png(path, rows):
    with open(path, "wb") as f:
        f.write(png_bytes(rows))

# Write an .ico holding several sizes, each stored as a PNG (what every current browser reads).
def write_ico(path, images):
    pngs = [png_bytes(rows) for rows in images]
    head, offset = struct.pack("<HHH", 0, 1, len(pngs)), 6 + 16 * len(pngs)
    for rows, data in zip(images, pngs):
        n = len(rows)
        head += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(head + b"".join(pngs))

# Pack the Classic drawing at every size into an .icns (macOS's iconutil); returns its message on failure, else None.
def write_icns(path, drawn):
    folder = tempfile.mkdtemp(prefix="nxspoof-icon-")
    try:
        iconset = os.path.join(folder, "NXSPOOF.iconset")
        os.makedirs(iconset)
        for n, name in ICONSET:
            write_png(os.path.join(iconset, "icon_%s.png" % name), drawn[n])
        done = subprocess.run(["/usr/bin/iconutil", "-c", "icns", "-o", path, iconset], capture_output=True, text=True)
        return None if done.returncode == 0 else (done.stderr.strip() or "iconutil failed")
    finally:
        shutil.rmtree(folder, ignore_errors=True)

# A preview sheet: each drawing at each size, on a light and a dark background, side by side.
def write_sheet(path, drawn_classic, drawn_minimal):
    pieces = [drawn_classic[n] for n in (512, 128, 64, 32, 16)] + [drawn_minimal[n] for n in (128, 32, 16)]
    gap, width = 24, sum(len(p) for p in pieces) + 24 * (len(pieces) + 1)
    height = 512 + 2 * gap
    sheet = []
    for bg in ((244, 245, 247), (18, 21, 27)):
        canvas = [bytearray(bytes(bg) + b"\xff") * width for _ in range(height)]
        x0 = gap
        for p in pieces:
            n, y0 = len(p), gap + (512 - len(p)) // 2
            for j in range(n):
                for i in range(n):
                    r, g, b, a = p[j][4 * i:4 * i + 4]
                    k, o = a / 255.0, 4 * (x0 + i)
                    canvas[y0 + j][o:o + 3] = bytes(int(c * k + bc * (1 - k) + 0.5) for c, bc in zip((r, g, b), bg))
            x0 += n + gap
        sheet += [bytes(r) for r in canvas]
    write_png(path, sheet)

# ##############################################################################################################################################################
# ##  MAIN                                                                                                                                                    ##
# ##############################################################################################################################################################

# Draw everything; exit code 1 if the .icns couldn't be made.
def main(argv=None):
    ap = argparse.ArgumentParser(description="Draw NXSPOOF's icon: scripts/NXSPOOF.icns and the favicon files.")
    ap.add_argument("--preview", metavar="DIR", help="also write DIR/icon-sheet.png: both drawings at every size, on light and dark")
    args = ap.parse_args(argv)
    classic = {n: draw("classic", n) for n in sorted({n for n, _ in ICONSET})}
    minimal = {n: draw("minimal", n) for n in sorted({16, FAVICON_SIZE, LOGO_SIZE, 128} | set(FAVICON_ICO))}
    problem = write_icns(ICNS, classic)
    print("❌ %s: %s" % (os.path.relpath(ICNS, ROOT), problem) if problem else "✅ %s (Classic, 16 to 1024 pixels)" % os.path.relpath(ICNS, ROOT))
    for path in FAVICONS:
        write_png(path, minimal[FAVICON_SIZE])
        print("✅ %s (Minimal, %d pixels)" % (os.path.relpath(path, ROOT), FAVICON_SIZE))
        ico = os.path.splitext(path)[0] + ".ico"
        write_ico(ico, [minimal[n] for n in FAVICON_ICO])
        print("✅ %s (Minimal, %s pixels)" % (os.path.relpath(ico, ROOT), " and ".join(str(n) for n in FAVICON_ICO)))
    write_png(LOGO, minimal[LOGO_SIZE])
    print("✅ %s (Minimal, %d pixels)" % (os.path.relpath(LOGO, ROOT), LOGO_SIZE))
    os.makedirs(os.path.dirname(PICTURE), exist_ok=True)
    write_png(PICTURE, classic[PICTURE_SIZE])
    print("✅ %s (Classic, %d pixels)" % (os.path.relpath(PICTURE, ROOT), PICTURE_SIZE))
    if args.preview:
        sheet = os.path.join(args.preview, "icon-sheet.png")
        write_sheet(sheet, classic, minimal)
        write_png(os.path.join(args.preview, "icon-classic-1024.png"), classic[1024])
        print("👀 %s" % sheet)
    return 1 if problem else 0

if __name__ == "__main__":
    sys.exit(main())
