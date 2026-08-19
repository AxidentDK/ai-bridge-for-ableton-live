# The icon — two versions, both generated

"AI" written in three half notes: the two leaning stems make the A, the third makes the I,
and the three heads sit on the baseline like notes on a stave. Three notes, three strokes.

Both versions are produced by a script, so neither is a file anyone has to redraw. Change
the geometry in one place and every size follows.

| file | how it is made | rebuild with |
|---|---|---|
| `ai-bridge-lit.ico` | **rendered** — a lit 3-D surface | `python scripts/render_icon.py` |
| `ai-bridge-flat.ico` | **vector** — SVG with per-stroke gradients | `python scripts/make_icon_flat.py` |
| `ai-bridge.svg` | the vector source, written by the script above | |
| `ai-bridge-lit.png` | 1024px preview of the rendered version | |

The installer uses **`ai-bridge-lit.ico`** and falls back to the flat one if it is absent.

## Transparent, holes included

There is no tile behind the mark — it sits on whatever the desktop is. That made the hole
in each note head a real question rather than a decoration:

- The **rendered** version already had it right by construction. The hole is SUBTRACTED
  from the distance field (`max(shape, -hole)`), so it was never painted; it opens onto
  the background because there is nothing there.
- The **vector** version painted the hole as an ellipse in the tile's colour, which was
  invisible against a dark tile and would have become a floating dark disc the moment the
  tile left. Each head is now one path holding two ellipse subpaths with
  `fill-rule="evenodd"`, so the hole is cut rather than covered.

The renderer also writes colour at full strength where coverage is partial and lets the
alpha carry the edge. Multiplying colour by coverage as well would premultiply it, and
every anti-aliased edge would darken toward black against a light desktop.

## Every frame is made at its own size

This is the thing most worth keeping. Both files started out built the easy way — render or
rasterise once, then let something scale it down to the other sizes — and the 16 and 32
pixel frames, the ones actually seen on a desktop and in a taskbar, came out soft.

- The rendered icon was reduced to 256, and that 256 was then handed to Pillow's `.ico`
  writer, which resized it AGAIN with a filter of its own choosing. Reductions of a
  reduction. Now each frame is rendered at its own size with 8x supersampling: a 32px icon
  is a 256px render brought down once.
- The vector icon used `magick -define icon:auto-resize`, which rasterises once and scales.
  Being able to draw correctly at any size is the entire point of having a vector source,
  so each frame is now rasterised from the SVG at that size.

`scripts/icon_ico.py` writes the container directly, because `Image.save(format="ICO")`
insists on resizing whatever you give it. Writing the 22-byte header by hand is what makes
"one frame, one source" possible — and the frames are PNG rather than uncompressed BMP,
which took the vector file from **370 KB to 41 KB** with no loss at all.

## What actually differs

The vector version fakes roundness with gradients, one per stroke, each running across its
own perpendicular. That is as far as a vector file can go: **a gradient does not know where
a surface is facing**, so the highlight is a stripe painted on the shape.

The rendered version builds the surface and lights it:

1. a **signed distance field** — for every pixel, the distance to the nearest point of the
   mark: capsules for the strokes, tilted ellipses for the heads, with the hole SUBTRACTED
   (`max(shape, -hole)`);
2. a **height** from that distance, `h = sqrt(r² - d²)`, which is the profile of a
   half-round tube, so a stroke is genuinely cylindrical rather than shaded as if it were;
3. **normals** from that height field, then Lambert, Blinn-Phong specular and a rim term;
4. **anti-aliasing straight out of the field** — coverage is the distance expressed in
   pixels, which is exact rather than sampled, and holds up at 32px.

You can see the difference where the highlight bends around the apex of the A and wraps
the domed heads. A painted stripe cannot do that.

## Two mistakes worth not repeating

**A `userSpaceOnUse` gradient is resolved in the coordinate system of the element that
references it, including that element's own transforms.** The heads sit inside a
`translate()` and a `rotate()`, so gradient coordinates computed in page space landed
nowhere near them and every head painted a flat end-stop colour. It looked like weak
shading; the gradient was simply missing. Object-bounding-box units avoid the question.

**A height field's normal is `(-dh/dx, -dh/dy, 1)` — the z term is about 1** because the
gradient is rise per PIXEL. It was set to `STEM_R * 0.55` ≈ 21 against a gradient peaking
near 1, so every normal pointed at the camera and the first render came out perfectly flat
no matter how it was lit. Units, not art.
