"""Optional OCR for visual captures.

The strongest use case is reading the Max for Live console's free-form error
text (the kind of Max-level error that never reaches a plugin's own logfile) off
a screenshot, so an agent can act on it as text instead of pixels. Reading a
dial's numeric label is a weak secondary use; do not over-fit to it.

macOS uses Apple's Vision framework (VNRecognizeTextRequest), which is fully on
device and ships with the OS — no model download, no network. The request is run
SYNCHRONOUSLY via ``performRequests_error_`` (unlike ScreenCaptureKit, whose
completion-handler APIs need the threading.Event bridge in visual_capture.py), so
no async plumbing is needed here.

Other platforms return a clear "unavailable" stub for now.
"""

from __future__ import annotations

import platform
from typing import Any


def run_ocr(
    image_path: str,
    *,
    lang: str = "en-US",
    recognition_level: str = "accurate",
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    """Recognize text in an on-disk image.

    Returns a dict shaped:
        {
          "engine": "apple-vision" | "none",
          "lines": [{"text": str, "confidence": float, "bbox": [x, y, w, h]}, ...],
          "text": "<lines joined by newlines>",
        }

    ``bbox`` is in pixels of the source image, origin TOP-LEFT (Vision reports
    normalized boxes with a bottom-left origin; we y-flip and scale here so the
    boxes line up with the saved PNG / the rest of visual_capture's coordinates).
    Lines below ``min_confidence`` are dropped.
    """
    system = platform.system()
    if system == "Darwin":
        return _run_ocr_macos(
            image_path,
            lang=lang,
            recognition_level=recognition_level,
            min_confidence=min_confidence,
        )
    # TODO Windows OCR: Windows.Media.Ocr (WinRT) is a viable follow-up but is
    # deferred — it needs an async WinRT call + a language pack check, and the
    # primary target (the Max console) is read on macOS. Return a clear stub so
    # callers can branch on engine == "none" instead of crashing.
    return {
        "engine": "none",
        "lines": [],
        "text": "",
        "error": "ocr_unavailable",
        "detail": "OCR is currently implemented for macOS (Apple Vision) only; platform=%s" % system,
    }


def _run_ocr_macos(
    image_path: str,
    *,
    lang: str,
    recognition_level: str,
    min_confidence: float,
) -> dict[str, Any]:
    try:
        from Foundation import NSURL
        from Vision import (
            VNImageRequestHandler,
            VNRecognizeTextRequest,
            VNRequestTextRecognitionLevelAccurate,
            VNRequestTextRecognitionLevelFast,
        )
    except ImportError as exc:
        raise RuntimeError(
            "macOS OCR requires pyobjc-framework-Vision (pip install pyobjc-framework-Vision)"
        ) from exc

    # Vision works in normalized coordinates, so we need the source image's pixel
    # size to convert boxes back to pixels. Read it from the file directly (via
    # ImageIO) so we never depend on Pillow here.
    width_px, height_px = _macos_image_pixel_size(image_path)

    url = NSURL.fileURLWithPath_(str(image_path))
    handler = VNImageRequestHandler.alloc().initWithURL_options_(url, {})

    request = VNRecognizeTextRequest.alloc().init()
    level = (
        VNRequestTextRecognitionLevelAccurate
        if str(recognition_level).lower() != "fast"
        else VNRequestTextRecognitionLevelFast
    )
    request.setRecognitionLevel_(level)
    # Language correction biases toward dictionary words, which hurts the digits,
    # short labels, and dotted error tokens we mostly care about. Turn it off.
    request.setUsesLanguageCorrection_(False)
    if lang:
        try:
            request.setRecognitionLanguages_([lang])
        except Exception:
            # Older Vision builds may not accept the languages setter; the
            # default (en-US) is fine, so ignore rather than fail the capture.
            pass

    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError("Vision OCR failed: %s" % (error or "unknown error"))

    lines: list[dict[str, Any]] = []
    for observation in request.results() or []:
        candidate = observation.topCandidates_(1)
        if not candidate or len(candidate) == 0:
            continue
        top = candidate[0]
        text = str(top.string() or "")
        confidence = float(top.confidence())
        if not text or confidence < min_confidence:
            continue
        lines.append({
            "text": text,
            "confidence": round(confidence, 4),
            "bbox": _vision_bbox_to_pixels(observation.boundingBox(), width_px, height_px),
        })

    return {
        "engine": "apple-vision",
        "lines": lines,
        "text": "\n".join(line["text"] for line in lines),
    }


def _vision_bbox_to_pixels(box: Any, width_px: int, height_px: int) -> list[int]:
    # Vision normalizes boxes to [0, 1] with the origin at the BOTTOM-left, so the
    # y axis is inverted relative to image pixels (origin top-left). Flip y and
    # scale to the source image's pixel size.
    origin = box.origin
    size = box.size
    x = float(origin.x) * width_px
    width = float(size.width) * width_px
    height = float(size.height) * height_px
    # Bottom-left origin -> top-left origin: the box top in pixel space is
    # (1 - (y + height)) * image_height.
    y_top = (1.0 - (float(origin.y) + float(size.height))) * height_px
    return [int(round(x)), int(round(y_top)), int(round(width)), int(round(height))]


def _macos_image_pixel_size(image_path: str) -> tuple[int, int]:
    import Quartz
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(str(image_path))
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        raise RuntimeError("could not open image for OCR: %s" % image_path)
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        raise RuntimeError("could not decode image for OCR: %s" % image_path)
    return int(Quartz.CGImageGetWidth(image)), int(Quartz.CGImageGetHeight(image))
