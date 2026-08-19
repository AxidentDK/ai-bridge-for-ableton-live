""""AI" in three half notes — teal, and shaded so the strokes read as rounded.

WHY EACH STROKE GETS ITS OWN GRADIENT. A single gradient smeared across the whole mark
makes it look tinted, not rounded. A cylinder reads as a cylinder because the light runs
ACROSS it — dark edge, highlight just off centre, dark edge — so every stem here is given
a gradient along its own perpendicular, computed from its direction. The vertical I stem,
the two leaning A stems and the horizontal crossbar therefore all catch the light the same
way, which a single diagonal gradient cannot do.

The heads are shaded across their minor axis for the same reason: an ellipse lit along its
long axis looks flat.
"""
import math
import pathlib

W = 512
HOLE = "#0c1a1c"

# Teal, as a tube: dark rim, a highlight just off centre, dark rim again.
TUBE = ((0.00, "#0f7f88"), (0.18, "#2ac7c0"), (0.40, "#c4fff6"),
        (0.64, "#2bbdb6"), (1.00, "#0d6a76"))

HEAD_RX, HEAD_RY = 44.0, 29.0
HOLE_RX, HOLE_RY = 22.0, 9.5
HEAD_TILT, HOLE_TILT = -20.0, -62.0
STEM_W = 19.0

BASE, APEX = 356.0, 132.0
APEX_X = 196.0
LEFT_X, RIGHT_X = 92.0, 268.0
I_X = 384.0
CROSSBAR_Y = 286.0

gradients = []


def tube(x1, y1, x2, y2, name):
    """A gradient running from (x1,y1) to (x2,y2) in user space — i.e. ACROSS a stroke."""
    stops = "".join(f'      <stop offset="{o}" stop-color="{c}"/>\n' for o, c in TUBE)
    gradients.append(
        f'    <linearGradient id="{name}" gradientUnits="userSpaceOnUse" '
        f'x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}">\n{stops}'
        f'    </linearGradient>\n')
    return name


#: How far along the head's rightmost radius the stem STARTS. It was 0.88 — nearly at the
#: edge — and the stem's round cap then stuck out past the head as a little nub at the
#: bottom of every note. A cap reaches STEM_W/2 beyond its endpoint, so the endpoint has
#: to sit at least that far inside the outline. Pulled in far enough that the cap is
#: swallowed, and no further: the stem must still clear the HOLE, or it would show through
#: the middle of the note.
STEM_INSET = 0.72


def stem_anchor(cx, cy):
    th = math.radians(HEAD_TILT)
    t = math.atan2(-HEAD_RY * math.sin(th), HEAD_RX * math.cos(th))
    x = HEAD_RX * math.cos(t) * math.cos(th) - HEAD_RY * math.sin(t) * math.sin(th)
    y = HEAD_RX * math.cos(t) * math.sin(th) + HEAD_RY * math.sin(t) * math.cos(th)
    return cx + x * STEM_INSET, cy + y * STEM_INSET


def line(p, q, name):
    (x1, y1), (x2, y2) = p, q
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    # Perpendicular to the stroke, spanning exactly its width: this is what rounds it.
    px, py = -dy / length, dx / length
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    half = STEM_W / 2
    gid = tube(mx - px * half, my - py * half, mx + px * half, my + py * half, name)
    return (f'  <line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="url(#{gid})" stroke-width="{STEM_W}" stroke-linecap="round"/>\n')


def head_gradient(name):
    """Across the head's MINOR axis, in the head's OWN coordinates.

    THE BUG THIS FIXES. The heads were flat while the stems were glossy, and squeezing the
    ramp did nothing at all. The reason: a `userSpaceOnUse` gradient is resolved in the
    coordinate system of the element that REFERENCES it — including that element's own
    transforms. The heads sit inside a translate() and a rotate(), so coordinates computed
    in page space landed nowhere near the shape and every head painted a flat end-stop
    colour. Object-bounding-box units sidestep the whole question: the ellipse's local box
    is 2rx by 2ry, so a vertical gradient across it IS the minor axis, whatever the tilt.

    The ramp stops short of the rim (0.08 -> 0.92) so the edges keep the darkest colour,
    which is what makes a flat ellipse read as domed.
    """
    stops = "".join(f'      <stop offset="{0.08 + o * 0.84:.3f}" stop-color="{c}"/>\n'
                    for o, c in TUBE)
    gradients.append(
        f'    <linearGradient id="{name}" x1="0.5" y1="0" x2="0.5" y2="1">\n{stops}'
        f'    </linearGradient>\n')
    return name


def head(cx, cy, name):
    gid = head_gradient(name)
    return (f'  <g transform="translate({cx:.1f},{cy:.1f})">\n'
            f'    <ellipse rx="{HEAD_RX}" ry="{HEAD_RY}" fill="url(#{gid})" '
            f'transform="rotate({HEAD_TILT})"/>\n'
            f'    <ellipse rx="{HOLE_RX}" ry="{HOLE_RY}" fill="{HOLE}" '
            f'transform="rotate({HOLE_TILT})"/>\n'
            f'  </g>\n')


def on_line(p, q, y):
    t = (y - p[1]) / (q[1] - p[1])
    return p[0] + (q[0] - p[0]) * t


left_anchor, right_anchor = stem_anchor(LEFT_X, BASE), stem_anchor(RIGHT_X, BASE)
i_anchor = stem_anchor(I_X, BASE)
apex = (APEX_X, APEX)
crossbar = ((on_line(left_anchor, apex, CROSSBAR_Y), CROSSBAR_Y),
            (on_line(right_anchor, apex, CROSSBAR_Y), CROSSBAR_Y))
i_top = (i_anchor[0], APEX)

body = ""
body += line(left_anchor, apex, "g1")
body += line(right_anchor, apex, "g2")
body += line(i_anchor, i_top, "g3")
body += line(*crossbar, "g4")
body += head(LEFT_X, BASE, "g5")
body += head(RIGHT_X, BASE, "g6")
body += head(I_X, BASE, "g7")

# --- centre and fill, from the real extents -------------------------------------------
th = math.radians(HEAD_TILT)
head_hw = math.hypot(HEAD_RX * math.cos(th), HEAD_RY * math.sin(th))
head_hh = math.hypot(HEAD_RX * math.sin(th), HEAD_RY * math.cos(th))
xs, ys = [], []
for cx in (LEFT_X, RIGHT_X, I_X):
    xs += [cx - head_hw, cx + head_hw]
    ys += [BASE - head_hh, BASE + head_hh]
for (x1, y1), (x2, y2) in ((left_anchor, apex), (right_anchor, apex),
                           (i_anchor, i_top), crossbar):
    xs += [min(x1, x2) - STEM_W / 2, max(x1, x2) + STEM_W / 2]
    ys += [min(y1, y2) - STEM_W / 2, max(y1, y2) + STEM_W / 2]

bw, bh = max(xs) - min(xs), max(ys) - min(ys)
cx0, cy0 = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
scale = (0.80 * W) / bw
dx, dy = W / 2 - cx0 * scale, W / 2 - cy0 * scale
print(f"  bounds {bw:.0f} x {bh:.0f} -> scale {scale:.3f}, offset ({dx:+.1f},{dy:+.1f})")

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.4" y2="1">
      <stop offset="0" stop-color="#1b2b2e"/>
      <stop offset="1" stop-color="#0d1618"/>
    </linearGradient>
{"".join(gradients)}  </defs>
  <rect x="16" y="16" width="480" height="480" rx="106" fill="url(#bg)"/>
  <rect x="16" y="16" width="480" height="480" rx="106" fill="none"
        stroke="#2c4448" stroke-width="5"/>
  <g transform="translate({dx:.1f},{dy:.1f}) scale({scale:.4f})">
{body}  </g>
</svg>
'''

assets = pathlib.Path(__file__).resolve().parent.parent / "assets"
out = assets / "ai-bridge.svg"
out.write_text(svg, encoding="utf-8")
print(f"{len(gradients)} per-stroke gradients -> {out}")

# --- .ico: RASTERISE THE SVG AT EACH SIZE, do not scale one render ----------------------
# `magick -define icon:auto-resize` rasterises once and resizes down, so 16 and 32 — the
# sizes actually seen — come out of a reduction. A vector source can be drawn correctly at
# any size, and drawing it at each one is the entire advantage of having a vector source.
# It also wrote uncompressed BMP frames: six of them came to 370 KB.
import shutil                                                          # noqa: E402
import subprocess                                                      # noqa: E402
import sys                                                             # noqa: E402
import tempfile                                                        # noqa: E402

SIZES = (256, 128, 64, 48, 32, 16)
magick = shutil.which("magick") or shutil.which("convert")
if not magick:
    print("  (ImageMagick not found — SVG written, .ico not rebuilt)")
    raise SystemExit(0)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from icon_ico import write_ico                                         # noqa: E402
from PIL import Image                                                  # noqa: E402

frames = []
with tempfile.TemporaryDirectory() as tmp:
    for size in SIZES:
        png = pathlib.Path(tmp) / f"{size}.png"
        subprocess.run([magick, "-background", "none", "-density", str(size * 4),
                        str(out), "-resize", f"{size}x{size}", str(png)], check=True)
        frames.append(Image.open(png).convert("RGBA"))
    written = write_ico(frames, assets / "ai-bridge-flat.ico")
print(f"assets/ai-bridge-flat.ico  {written:,} bytes, {len(frames)} frames "
      f"(each rasterised at its own size)")
