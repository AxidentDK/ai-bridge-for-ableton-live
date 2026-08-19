"""Write a Windows .ico from frames that were each produced at their own size.

WHY NOT JUST Image.save(..., format="ICO", sizes=[...]). That resizes from whatever image
you hand it, with a filter you do not choose, so the 16 and 32 pixel frames — the ones
actually seen on a desktop and in a taskbar — end up as reductions of a reduction. Every
frame here is rendered or rasterised at its OWN size and passed in finished.

The container is simple enough to write directly, and writing it directly is what makes
"one frame, one source" possible:

    ICONDIR      reserved(2)=0, type(2)=1, count(2)
    ICONDIRENTRY width(1), height(1), colours(1), reserved(1), planes(2),
                 bpp(2), bytes(4), offset(4)          -- 16 bytes, one per frame
    then the frame payloads, here PNG for every size

PNG payloads are legal in .ico from Vista onward and are both smaller and lossless; the
alternative is an uncompressed BMP with a separate AND mask, which is how a six-frame icon
turns into 370 KB.
"""
from __future__ import annotations

import io
import struct


def write_ico(frames, path) -> int:
    """``frames`` is an iterable of PIL images, each already at its target size."""
    frames = sorted(frames, key=lambda im: im.size[0])
    payloads = []
    for image in frames:
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        buffer = io.BytesIO()
        # optimize=True costs a moment and shrinks the file; the frames are tiny.
        image.save(buffer, format="PNG", optimize=True)
        payloads.append(buffer.getvalue())

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    directory, body = b"", b""
    for image, payload in zip(frames, payloads):
        w, h = image.size
        # 256 is stored as 0: the field is one byte and 256 does not fit in it.
        directory += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32,
                                 len(payload), offset)
        offset += len(payload)
        body += payload

    with open(path, "wb") as handle:
        handle.write(header + directory + body)
    return len(header) + len(directory) + len(body)
