"""The repository's social preview card — the image every shared link renders.

    python scripts/social_card.py     ->  assets/social-preview.png  (1280x640)

WHY IT EXISTS. Without one, a link to this repo posted anywhere — Hacker News, Reddit, a
Discord, a forum — renders as a grey box with a repo name in it. That is the first thing
most people will ever see of the project, and "grey box" is a poor first thing.

GitHub wants 1280x640 and crops toward the centre on some surfaces, so everything that
matters is kept inside a generous margin rather than pushed to the edges.

The icon is reused rather than redrawn: assets/ai-bridge-lit.png is the same rendered mark
that ships as the app icon, so the card and the desktop shortcut agree.
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
MARGIN = 84

BG_TOP = (28, 44, 47)
BG_BOT = (11, 18, 20)
TEAL = (94, 214, 205)
WHITE = (242, 246, 247)
DIM = (150, 168, 172)

FONTS = pathlib.Path(r"C:\Windows\Fonts")
TITLE = ImageFont.truetype(str(FONTS / "segoeuib.ttf"), 64)
LEAD = ImageFont.truetype(str(FONTS / "segoeui.ttf"), 34)
BODY = ImageFont.truetype(str(FONTS / "segoeui.ttf"), 27)
SMALL = ImageFont.truetype(str(FONTS / "seguisb.ttf"), 24)


def wrap(draw, text, font, width):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def main() -> int:
    assets = pathlib.Path(__file__).resolve().parent.parent / "assets"
    card = Image.new("RGB", (W, H), BG_BOT)

    # Vertical gradient, drawn a row at a time — cheap and smooth enough at this size.
    draw = ImageDraw.Draw(card)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)],
                  fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT)))

    # The mark, left, vertically centred on the title block.
    icon_path = assets / "ai-bridge-lit.png"
    if icon_path.exists():
        icon = Image.open(icon_path).convert("RGBA").resize((300, 300), Image.LANCZOS)
        card.paste(icon, (MARGIN, (H - 300) // 2 - 20), icon)

    x = MARGIN + 300 + 56
    text_width = W - x - MARGIN

    y = 150
    draw.text((x, y), "AI Bridge", font=TITLE, fill=WHITE)
    y += 74
    draw.text((x, y), "for Ableton Live", font=TITLE, fill=TEAL)
    y += 96

    for line in wrap(draw, "An AI that can actually use Ableton Live — "
                           "not just talk about it.", LEAD, text_width):
        draw.text((x, y), line, font=LEAD, fill=WHITE)
        y += 44
    y += 14

    for line in wrap(draw, "Ask in your own words: it loads the device, finds the sound, "
                           "renders the stems.", BODY, text_width):
        draw.text((x, y), line, font=BODY, fill=DIM)
        y += 36

    draw.text((MARGIN, H - MARGIN - 6),
              "62 tools   ·   563 Live Object Model operations   ·   Apache-2.0",
              font=SMALL, fill=TEAL)

    out = assets / "social-preview.png"
    card.save(out, optimize=True)
    print(f"{out}  {W}x{H}  {out.stat().st_size:,} bytes")
    print("Upload it at: Settings -> General -> Social preview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
