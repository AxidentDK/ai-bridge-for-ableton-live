"""Version two of the icon, rendered rather than drawn: a lit 3-D surface, in code.

The SVG version fakes roundness with linear gradients, which is the best a vector file can
do — a gradient does not know where the surface is facing. This builds the surface itself
and lights it.

HOW IT WORKS

1.  A signed distance field. For every pixel, the distance to the nearest point of the
    mark: capsules for the four strokes, an ellipse for each note head, minus the hole.
2.  A height from that distance: h = sqrt(r^2 - d^2), the profile of a half-round tube, so
    the strokes are genuinely cylindrical and the heads genuinely domed.
3.  Normals from the height field's gradient, and then real lighting — Lambert for the
    body, Blinn-Phong for the specular, plus a rim term that catches the silhouette the
    way a glossy object does.
4.  Anti-aliasing straight out of the SDF: coverage is the distance expressed in pixels,
    which is exact rather than sampled, and stays clean at 32px where supersampling alone
    goes soft.

Same geometry as Kim's design — three half notes whose stems draw "AI" — so this is a
second TREATMENT, not a second idea.

numpy and Pillow only.
"""
import math
import pathlib

import numpy as np
from PIL import Image

S = 1024                      # render at this, downsample later
SS = 2                        # supersample factor on top of the analytic AA
N = S * SS

# --- geometry, in 512-space and scaled up ---------------------------------------------
K = N / 512.0
HEAD_RX, HEAD_RY = 44.0 * K, 29.0 * K
HOLE_RX, HOLE_RY = 22.0 * K, 9.5 * K
HEAD_TILT, HOLE_TILT = math.radians(-20.0), math.radians(-62.0)
STEM_R = 19.0 * K / 2.0
STEM_INSET = 0.72

BASE, APEX = 356.0 * K, 132.0 * K
APEX_X = 196.0 * K
LEFT_X, RIGHT_X, I_X = 92.0 * K, 268.0 * K, 384.0 * K
CROSSBAR_Y = 286.0 * K

# Teal, as a material rather than a ramp.
ALBEDO = np.array([0.180, 0.855, 0.831])       # #2eda d4 — brighter teal
BG_TOP = np.array([0.106, 0.169, 0.180])
BG_BOT = np.array([0.043, 0.078, 0.086])
CORNER = 106.0 * K


def stem_anchor(cx, cy):
    t = math.atan2(-HEAD_RY * math.sin(HEAD_TILT), HEAD_RX * math.cos(HEAD_TILT))
    x = HEAD_RX * math.cos(t) * math.cos(HEAD_TILT) - HEAD_RY * math.sin(t) * math.sin(HEAD_TILT)
    y = HEAD_RX * math.cos(t) * math.sin(HEAD_TILT) + HEAD_RY * math.sin(t) * math.cos(HEAD_TILT)
    return cx + x * STEM_INSET, cy + y * STEM_INSET


left_a, right_a = stem_anchor(LEFT_X, BASE), stem_anchor(RIGHT_X, BASE)
i_a = stem_anchor(I_X, BASE)
apex = (APEX_X, APEX)


def on_line(p, q, y):
    t = (y - p[1]) / (q[1] - p[1])
    return p[0] + (q[0] - p[0]) * t


SEGMENTS = [(left_a, apex), (right_a, apex), (i_a, (i_a[0], APEX)),
            ((on_line(left_a, apex, CROSSBAR_Y), CROSSBAR_Y),
             (on_line(right_a, apex, CROSSBAR_Y), CROSSBAR_Y))]
HEADS = [(LEFT_X, BASE), (RIGHT_X, BASE), (I_X, BASE)]

yy, xx = np.mgrid[0:N, 0:N].astype(np.float32)


def capsule_sdf(p, q):
    """Distance to a thick line with round ends — exactly what a stroke is."""
    (x1, y1), (x2, y2) = p, q
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    t = np.clip(((xx - x1) * dx + (yy - y1) * dy) / length2, 0.0, 1.0)
    return np.hypot(xx - (x1 + t * dx), yy - (y1 + t * dy)) - STEM_R


def ellipse_sdf(cx, cy, rx, ry, tilt):
    """Approximate distance to a tilted ellipse.

    The exact distance needs a quartic; this is the standard gradient-normalised estimate,
    k*(k-1)/|grad k|, which is accurate to well under a pixel near the boundary — and near
    the boundary is the only place it is used, for coverage and for the height profile.
    """
    c, s = math.cos(-tilt), math.sin(-tilt)
    u = (xx - cx) * c - (yy - cy) * s
    v = (xx - cx) * s + (yy - cy) * c
    k = np.sqrt((u / rx) ** 2 + (v / ry) ** 2)
    grad = np.sqrt((u / (rx * rx)) ** 2 + (v / (ry * ry)) ** 2) + 1e-9
    return (k - 1.0) * k / grad


# --- the field ------------------------------------------------------------------------
sdf = np.full((N, N), 1e9, dtype=np.float32)
for p, q in SEGMENTS:
    sdf = np.minimum(sdf, capsule_sdf(p, q))
for cx, cy in HEADS:
    sdf = np.minimum(sdf, ellipse_sdf(cx, cy, HEAD_RX, HEAD_RY, HEAD_TILT))

# The hole is CUT from the mark: max(shape, -hole) is subtraction in SDF terms.
hole = np.full((N, N), 1e9, dtype=np.float32)
for cx, cy in HEADS:
    hole = np.minimum(hole, ellipse_sdf(cx, cy, HOLE_RX, HOLE_RY, HOLE_TILT))
sdf = np.maximum(sdf, -hole)

# --- height, normals, light -----------------------------------------------------------
# A half-round profile: full height along the spine, falling to zero at the edge.
depth = np.clip(-sdf, 0.0, STEM_R)
height = np.sqrt(np.maximum(STEM_R ** 2 - (STEM_R - depth) ** 2, 0.0))

gy, gx = np.gradient(height)
# UNITS MATTER HERE. For a height field the normal is (-dh/dx, -dh/dy, 1): the gradient is
# rise per PIXEL, so the z term has to be about 1 as well. It was STEM_R*0.55 — around 21
# against a gradient that peaks near 1 — which made every normal point at the camera and
# the whole mark render dead flat, lit or not. Below 1 exaggerates the curvature; 0.85
# gives a firm roll-off without the rim going to mush.
nz = np.full_like(height, 0.85)
norm = np.sqrt(gx * gx + gy * gy + nz * nz) + 1e-9
nx, ny, nz = -gx / norm, -gy / norm, nz / norm

light = np.array([-0.45, -0.72, 0.53])
light /= np.linalg.norm(light)
lambert = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0.0, 1.0)

view = np.array([0.0, 0.0, 1.0])
half = light + view
half /= np.linalg.norm(half)
spec = np.clip(nx * half[0] + ny * half[1] + nz * half[2], 0.0, 1.0) ** 26.0
rim = np.clip(1.0 - nz, 0.0, 1.0) ** 2.2       # the silhouette catch of a glossy body

shade = (0.38 + 0.86 * lambert)[..., None] * ALBEDO
shade = shade + spec[..., None] * 1.15 + rim[..., None] * ALBEDO * 0.42
shade = np.clip(shade, 0.0, 1.0)

# --- background: rounded square, its own soft gradient ---------------------------------
half_n = N / 2.0
qx = np.abs(xx - half_n) - (half_n - 16 * K) + CORNER
qy = np.abs(yy - half_n) - (half_n - 16 * K) + CORNER
box = (np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
       + np.minimum(np.maximum(qx, qy), 0.0) - CORNER)

t = (yy / N)[..., None]
bg = BG_TOP * (1 - t) + BG_BOT * t
# A faint inner light at the top edge, so the tile is not a flat slab.
bg = bg + np.clip(1.0 - (yy / (N * 0.45)), 0, 1)[..., None] * 0.035

# --- composite, with coverage straight from the SDF -----------------------------------
def coverage(field):
    return np.clip(0.5 - field, 0.0, 1.0)[..., None]


img = bg * coverage(box)                       # tile over transparency
img = img * (1 - coverage(sdf)) + shade * coverage(sdf)
alpha = np.clip(coverage(box)[..., 0] + coverage(sdf)[..., 0], 0.0, 1.0)

rgba = np.concatenate([np.clip(img, 0, 1), alpha[..., None]], axis=2)
out = (rgba * 255.0 + 0.5).astype(np.uint8)

image = Image.fromarray(out, "RGBA").resize((S, S), Image.LANCZOS)
here = pathlib.Path(__file__).resolve().parent.parent / "assets"
image.save(here / "ai-bridge-lit.png")

sizes = (256, 128, 64, 48, 32, 16)
frames = []
for s in sizes:
    frames.append(image.resize((s, s), Image.LANCZOS))
frames[0].save(here / "ai-bridge-lit.ico", format="ICO",
               sizes=[(s, s) for s in sizes])
print(f"rendered {N}x{N} -> {S}, plus {', '.join(str(s) for s in sizes)}")
