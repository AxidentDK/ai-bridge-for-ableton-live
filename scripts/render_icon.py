"""The icon, rendered rather than drawn: a lit 3-D surface, in code.

    python scripts/render_icon.py        ->  assets/ai-bridge-lit.ico + .png

The SVG version fakes roundness with linear gradients, which is the best a vector file can
do — a gradient does not know where the surface is facing. This builds the surface itself
and lights it.

HOW IT WORKS

1.  A signed distance field. For every pixel, the distance to the nearest point of the
    mark: capsules for the four strokes, an ellipse for each note head, with the hole
    SUBTRACTED — max(shape, -hole) is subtraction in distance-field terms.
2.  A height from that distance: h = sqrt(r^2 - d^2), the profile of a half-round tube, so
    the strokes are genuinely cylindrical and the heads genuinely domed.
3.  Normals from the height field's gradient, then real lighting — Lambert for the body,
    Blinn-Phong for the specular, and a rim term that catches the silhouette the way a
    glossy object does.
4.  Anti-aliasing straight out of the field: coverage is the distance expressed in pixels,
    which is exact rather than sampled.

EVERY FRAME IS RENDERED AT ITS OWN SIZE. The first version rendered once and downsampled,
and then handed the 256px result to Pillow's .ico writer, which resized it AGAIN with a
filter of its own choosing — so 16 and 32, the sizes actually seen on a desktop and in a
taskbar, were reductions of a reduction. Geometry is defined in a 512 unit square and
scaled per frame, so a 32px icon is a 32px render at 8x supersampling rather than a
thumbnail of a big one.

numpy and Pillow only.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from icon_ico import write_ico                                        # noqa: E402

SIZES = (256, 128, 64, 48, 32, 16)
SUPERSAMPLE = 8               # per frame, before the single downsample
PREVIEW = 1024

# --- geometry, in a 512 unit square -----------------------------------------------------
U = 512.0
G_HEAD_RX, G_HEAD_RY = 44.0, 29.0
G_HOLE_RX, G_HOLE_RY = 22.0, 9.5
HEAD_TILT, HOLE_TILT = math.radians(-20.0), math.radians(-62.0)
G_STEM_R = 19.0 / 2.0
STEM_INSET = 0.72
G_BASE, G_APEX = 356.0, 132.0
G_APEX_X = 196.0
G_LEFT_X, G_RIGHT_X, G_I_X = 92.0, 268.0, 384.0
G_CROSSBAR_Y = 286.0
G_CORNER, G_INSET = 106.0, 16.0

ALBEDO = np.array([0.180, 0.855, 0.831])
BG_TOP = np.array([0.106, 0.169, 0.180])
BG_BOT = np.array([0.043, 0.078, 0.086])


def render(pixels: int, supersample: int = SUPERSAMPLE) -> Image.Image:
    """One frame, rendered at ``pixels`` and supersampled, then reduced ONCE."""
    n = pixels * supersample
    k = n / U
    head_rx, head_ry = G_HEAD_RX * k, G_HEAD_RY * k
    hole_rx, hole_ry = G_HOLE_RX * k, G_HOLE_RY * k
    stem_r = G_STEM_R * k
    base, apex_y = G_BASE * k, G_APEX * k
    left_x, right_x, i_x = G_LEFT_X * k, G_RIGHT_X * k, G_I_X * k
    apex_x, crossbar_y = G_APEX_X * k, G_CROSSBAR_Y * k
    corner, inset = G_CORNER * k, G_INSET * k

    def stem_anchor(cx, cy):
        t = math.atan2(-head_ry * math.sin(HEAD_TILT), head_rx * math.cos(HEAD_TILT))
        x = (head_rx * math.cos(t) * math.cos(HEAD_TILT)
             - head_ry * math.sin(t) * math.sin(HEAD_TILT))
        y = (head_rx * math.cos(t) * math.sin(HEAD_TILT)
             + head_ry * math.sin(t) * math.cos(HEAD_TILT))
        return cx + x * STEM_INSET, cy + y * STEM_INSET

    left_a, right_a = stem_anchor(left_x, base), stem_anchor(right_x, base)
    i_a = stem_anchor(i_x, base)
    apex = (apex_x, apex_y)

    def on_line(p, q, y):
        return p[0] + (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1])

    segments = [(left_a, apex), (right_a, apex), (i_a, (i_a[0], apex_y)),
                ((on_line(left_a, apex, crossbar_y), crossbar_y),
                 (on_line(right_a, apex, crossbar_y), crossbar_y))]
    heads = [(left_x, base), (right_x, base), (i_x, base)]

    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)

    def capsule(p, q):
        (x1, y1), (x2, y2) = p, q
        dx, dy = x2 - x1, y2 - y1
        t = np.clip(((xx - x1) * dx + (yy - y1) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
        return np.hypot(xx - (x1 + t * dx), yy - (y1 + t * dy)) - stem_r

    def ellipse(cx, cy, rx, ry, tilt):
        # Gradient-normalised estimate: exact distance to an ellipse needs a quartic, and
        # this is accurate to well under a pixel near the boundary, which is the only place
        # it is used — for coverage and for the height profile.
        c, s = math.cos(-tilt), math.sin(-tilt)
        u = (xx - cx) * c - (yy - cy) * s
        v = (xx - cx) * s + (yy - cy) * c
        kk = np.sqrt((u / rx) ** 2 + (v / ry) ** 2)
        grad = np.sqrt((u / (rx * rx)) ** 2 + (v / (ry * ry)) ** 2) + 1e-9
        return (kk - 1.0) * kk / grad

    sdf = np.full((n, n), 1e9, dtype=np.float32)
    for p, q in segments:
        sdf = np.minimum(sdf, capsule(p, q))
    for cx, cy in heads:
        sdf = np.minimum(sdf, ellipse(cx, cy, head_rx, head_ry, HEAD_TILT))
    hole = np.full((n, n), 1e9, dtype=np.float32)
    for cx, cy in heads:
        hole = np.minimum(hole, ellipse(cx, cy, hole_rx, hole_ry, HOLE_TILT))
    sdf = np.maximum(sdf, -hole)

    depth = np.clip(-sdf, 0.0, stem_r)
    height = np.sqrt(np.maximum(stem_r ** 2 - (stem_r - depth) ** 2, 0.0))

    gy, gx = np.gradient(height)
    # A height field's normal is (-dh/dx, -dh/dy, 1): the gradient is rise per PIXEL, so
    # the z term is about 1. Setting it to a size-derived number once made every normal
    # point at the camera and the whole mark rendered flat however it was lit. Scaled by
    # the supersample factor so the roll-off looks the same at every frame size.
    nz_flat = 0.85 * supersample
    norm = np.sqrt(gx * gx + gy * gy + nz_flat * nz_flat) + 1e-9
    nx, ny, nz = -gx / norm, -gy / norm, nz_flat / norm

    light = np.array([-0.45, -0.72, 0.53])
    light /= np.linalg.norm(light)
    lambert = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0.0, 1.0)
    half = light + np.array([0.0, 0.0, 1.0])
    half /= np.linalg.norm(half)
    spec = np.clip(nx * half[0] + ny * half[1] + nz * half[2], 0.0, 1.0) ** 26.0
    rim = np.clip(1.0 - nz, 0.0, 1.0) ** 2.2

    shade = (0.38 + 0.86 * lambert)[..., None] * ALBEDO
    shade = np.clip(shade + spec[..., None] * 1.15 + rim[..., None] * ALBEDO * 0.42, 0, 1)

    half_n = n / 2.0
    qx = np.abs(xx - half_n) - (half_n - inset) + corner
    qy = np.abs(yy - half_n) - (half_n - inset) + corner
    box = (np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
           + np.minimum(np.maximum(qx, qy), 0.0) - corner)

    t = (yy / n)[..., None]
    bg = BG_TOP * (1 - t) + BG_BOT * t
    bg = bg + np.clip(1.0 - (yy / (n * 0.45)), 0, 1)[..., None] * 0.035

    def coverage(field):
        return np.clip(0.5 - field, 0.0, 1.0)[..., None]

    img = bg * coverage(box)
    img = img * (1 - coverage(sdf)) + shade * coverage(sdf)
    alpha = np.clip(coverage(box)[..., 0] + coverage(sdf)[..., 0], 0.0, 1.0)

    rgba = np.concatenate([np.clip(img, 0, 1), alpha[..., None]], axis=2)
    full = Image.fromarray((rgba * 255.0 + 0.5).astype(np.uint8), "RGBA")
    return full.resize((pixels, pixels), Image.LANCZOS)


def main() -> int:
    assets = pathlib.Path(__file__).resolve().parent.parent / "assets"
    assets.mkdir(exist_ok=True)
    frames = []
    for size in SIZES:
        frames.append(render(size))
        print(f"  rendered {size:>3} at {size * SUPERSAMPLE}px")
    written = write_ico(frames, assets / "ai-bridge-lit.ico")
    render(PREVIEW, supersample=2).save(assets / "ai-bridge-lit.png")
    print(f"\nassets/ai-bridge-lit.ico  {written:,} bytes, {len(frames)} frames")
    print(f"assets/ai-bridge-lit.png  {PREVIEW}px preview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
