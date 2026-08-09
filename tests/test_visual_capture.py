from __future__ import annotations

from pathlib import Path

import pytest

import visual_capture
from visual_capture import WindowInfo


def test_visual_capture_filters_to_ableton_windows():
    windows = [
        WindowInfo(
            platform="Windows",
            id=100,
            title="vibe-m4l",
            owner="Ableton Live Suite",
            process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
            bounds={"x": 0, "y": 0, "width": 1200, "height": 800},
        ),
        WindowInfo(
            platform="Windows",
            id=200,
            title="Private Browser",
            owner="Chrome",
            process_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ),
        WindowInfo(
            platform="Darwin",
            id=300,
            title="Other Live",
            owner="Live",
            process_path="/Applications/Not Ableton.app/Contents/MacOS/Live",
            bundle_id="example.live",
        ),
    ]
    assert [window.id for window in windows if visual_capture.is_ableton_live_window(window)] == [100]


def test_visual_capture_accepts_verified_macos_ableton_bundle():
    window = WindowInfo(
        platform="Darwin",
        id=100,
        title="vibe-m4l",
        owner="Live",
        process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    assert visual_capture.is_ableton_live_window(window) is True


def test_capture_refuses_non_ableton_window(tmp_path):
    window = WindowInfo(
        platform="Windows",
        id=200,
        title="Browser",
        owner="Chrome",
        process_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    with pytest.raises(RuntimeError, match="non-Ableton"):
        visual_capture.capture_window(window, tmp_path / "browser.png")


def test_capture_windows_window_selects_by_hwnd(tmp_path, monkeypatch):
    # windows-capture matches window_name by *substring*, so a non-target
    # window whose title merely contains "Max for Live" (e.g. a browser tab on
    # this PR, or an M4L patcher editor) would be grabbed instead. Capture must
    # select by the concrete HWND.
    import sys
    import types

    recorded = {}

    class FakeCapture:
        def __init__(self, cursor_capture=False, draw_border=False, monitor_index=None, window_name=None, window_hwnd=None):
            recorded["window_name"] = window_name
            recorded["window_hwnd"] = window_hwnd
            self._on_frame = None

        def event(self, fn):
            if fn.__name__ == "on_frame_arrived":
                self._on_frame = fn
            return fn

        def start(self):
            class _Ctl:
                def stop(self):
                    pass

            class _Frame:
                def save_as_image(self, path):
                    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")

            self._on_frame(_Frame(), _Ctl())

    fake_mod = types.ModuleType("windows_capture")
    fake_mod.WindowsCapture = FakeCapture
    fake_mod.InternalCaptureControl = object
    monkeypatch.setitem(sys.modules, "windows_capture", fake_mod)

    window = WindowInfo(
        platform="Windows",
        id=90210,
        title="Max for Live",
        owner="Ableton Live 12 Suite",
        process_path=r"C:\ProgramData\Ableton\Live 12 Suite\Program\Ableton Live 12 Suite.exe",
        bounds={"x": 0, "y": 0, "width": 575, "height": 576},
    )
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [window])
    visual_capture.capture_windows_window(window, tmp_path / "console.png")
    assert recorded["window_hwnd"] == 90210
    assert recorded["window_name"] is None


def test_is_max_console_window_matches_m4l_console():
    # Max for Live hosts the Max runtime inside Live's process: the console
    # window is owned by Live and titled "Max for Live".
    m4l = WindowInfo(
        platform="Darwin",
        id=402,
        title="Max for Live",
        owner="Live",
        process_path="/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    assert visual_capture.is_max_console_window(m4l) is True


def test_is_max_console_window_ignores_standalone_max_and_other_live_windows():
    # The PR is scoped to the M4L console only: the standalone Max app's
    # "Max Console" window and ordinary Live windows are not matched.
    standalone_max = WindowInfo(
        platform="Darwin", id=400, title="Max Console", owner="Max",
        process_path="/Applications/Max.app/Contents/MacOS/Max",
        bundle_id="com.cycling74.Max",
    )
    live_set = WindowInfo(
        platform="Darwin", id=401, title="Untitled", owner="Live",
        process_path="/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    assert visual_capture.is_max_console_window(standalone_max) is False
    assert visual_capture.is_max_console_window(live_set) is False


def test_select_max_console_prefers_onscreen(monkeypatch):
    # Two "Max for Live" windows (e.g. console + patcher editor): the on-screen
    # one wins even though the other is larger.
    offscreen = WindowInfo(
        platform="Darwin", id=45956, title="Max for Live", owner="Live",
        process_path="/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live", onscreen=False,
        bounds={"x": 0, "y": 0, "width": 1200, "height": 1200},
    )
    onscreen = WindowInfo(
        platform="Darwin", id=90105, title="Max for Live", owner="Live",
        process_path="/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live", onscreen=True,
        bounds={"x": 0, "y": 0, "width": 575, "height": 576},
    )
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [offscreen, onscreen])
    assert visual_capture.select_max_console_window().id == 90105


def test_capture_allows_max_console_window(monkeypatch):
    window = WindowInfo(
        platform="Darwin",
        id=90105,
        title="Max for Live",
        owner="Live",
        process_path="/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    captured = {}
    monkeypatch.setattr(visual_capture, "capture_macos_window", lambda w, out, backend="auto": captured.update(id=w.id) or "stub")
    # Should not raise the non-Ableton/non-Max-Console guard.
    assert visual_capture.capture_window(window, Path("/tmp/_unused_max_console.png")) == "stub"
    assert captured["id"] == 90105


def test_max_console_list_only_filters(monkeypatch):
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Darwin", id=90105, title="Max for Live", owner="Live",
            process_path="/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 0, "width": 575, "height": 576},
        ),
        WindowInfo(
            platform="Darwin", id=300, title="Untitled", owner="Live",
            process_path="/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 0, "width": 1200, "height": 800},
        ),
    ])
    result = visual_capture.capture_max_console_window(list_only=True)
    assert result["count"] == 1
    assert result["windows"][0]["id"] == 90105


def test_title_filter_applies_after_ableton_filter(monkeypatch):
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Windows",
            id=100,
            title="Ableton Set",
            owner="Ableton Live",
            process_path=r"C:\Ableton Live.exe",
            bounds={"x": 0, "y": 0, "width": 1200, "height": 800},
        ),
        WindowInfo(
            platform="Windows",
            id=200,
            title="Ableton Notes in Browser",
            owner="Chrome",
            process_path=r"C:\chrome.exe",
            bounds={"x": 0, "y": 0, "width": 1400, "height": 900},
        ),
    ])
    assert visual_capture.select_ableton_window("Set").id == 100
    with pytest.raises(RuntimeError, match="No Ableton Live window"):
        visual_capture.select_ableton_window("Browser")


def test_list_only_returns_ableton_windows(monkeypatch):
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Windows",
            id=100,
            title="Ableton Set",
            owner="Ableton Live",
            process_path=r"C:\Ableton Live.exe",
            bounds={"x": 0, "y": 0, "width": 1200, "height": 800},
        ),
        WindowInfo(
            platform="Windows",
            id=200,
            title="Browser",
            owner="Chrome",
            process_path=r"C:\chrome.exe",
            bounds={"x": 0, "y": 0, "width": 1400, "height": 900},
        ),
    ])
    result = visual_capture.capture_ableton_window(list_only=True)
    assert result["count"] == 1
    assert result["windows"][0]["id"] == 100


def test_device_detail_region_crops_bottom_of_ableton_window():
    assert visual_capture.capture_region_box((1000, 900), "device-detail") == (0, 594, 1000, 900)
    assert visual_capture.capture_region_box((1000, 900), "detail", bottom_fraction=0.25) == (0, 675, 1000, 900)


def test_explicit_crop_clamps_to_ableton_window_bounds():
    assert visual_capture.capture_region_box((1000, 900), crop=[-10, 50, 120, 60]) == (0, 50, 110, 110)
    with pytest.raises(RuntimeError, match="outside"):
        visual_capture.capture_region_box((1000, 900), crop=[1200, 50, 100, 60])


def test_region_relative_crop_uses_region_as_origin():
    assert visual_capture.capture_region_box(
        (1000, 900),
        region="device-detail",
        crop=[10, 20, 200, 50],
        bottom_fraction=0.25,
        crop_relative_to_region=True,
    ) == (10, 695, 210, 745)
    assert visual_capture.capture_region_box(
        (1000, 900),
        region="device-detail",
        crop=[10, 20, 200, 50],
        bottom_fraction=0.25,
    ) == (10, 20, 210, 70)
    with pytest.raises(RuntimeError, match="outside the region"):
        visual_capture.capture_region_box(
            (1000, 900),
            region="device-detail",
            crop=[10, 300, 200, 50],
            bottom_fraction=0.25,
            crop_relative_to_region=True,
        )


def test_unknown_capture_region_is_rejected():
    with pytest.raises(RuntimeError, match="Unknown Ableton visual capture region"):
        visual_capture.capture_region_box((1000, 900), "browser")


def test_postprocess_capture_crops_and_downscales(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    output = tmp_path / "live.png"
    Image.new("RGB", (200, 100), "black").save(output)

    result = visual_capture.postprocess_capture(output, region="device-detail", bottom_fraction=0.5, max_width=50)

    assert result["source_size"] == [200, 100]
    assert result["crop_box"] == [0, 50, 200, 100]
    assert result["size"][0] <= 50
    assert result["content"]["blank"] is True
    assert output.stat().st_size > 0


def test_postprocess_capture_supports_region_relative_crop(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    output = tmp_path / "live.png"
    Image.new("RGB", (200, 100), "black").save(output)

    result = visual_capture.postprocess_capture(
        output,
        region="device-detail",
        crop=[10, 5, 40, 20],
        crop_relative_to_region=True,
        bottom_fraction=0.5,
    )

    assert result["crop_box"] == [10, 55, 50, 75]
    assert result["size"] == [40, 20]


def test_postprocess_capture_collects_full_window_content_stats_without_crop(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    output = tmp_path / "live.png"
    image = Image.new("RGB", (40, 20), "black")
    for x in range(10, 30):
        for y in range(5, 15):
            image.putpixel((x, y), (240, 240, 240))
    image.save(output)

    result = visual_capture.postprocess_capture(output)

    assert result["source_size"] == [40, 20]
    assert result["size"] == [40, 20]
    assert result["crop_box"] is None
    assert result["content"]["blank"] is False


def test_visual_capture_cli_passes_crop_relative_to_region(monkeypatch, capsys):
    captured = {}

    def fake_capture(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "path": "/tmp/live.png"}

    monkeypatch.setattr(visual_capture, "capture_ableton_window", fake_capture)

    assert visual_capture.main([
        "--output", "/tmp/live.png",
        "--region", "device-detail",
        "--crop", "1,2,3,4",
        "--crop-relative-to-region",
        "--bottom-fraction", "0.5",
        "--max-width", "800",
        "--max-height", "300",
    ]) == 0
    capsys.readouterr()
    assert captured["crop_relative_to_region"] is True
    assert captured["bottom_fraction"] == 0.5
    assert captured["max_width"] == 800
    assert captured["max_height"] == 300


def test_capture_blank_full_window_includes_validation_blocker(monkeypatch, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Darwin",
            id=100,
            title="vibe-m4l",
            owner="Live",
            process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 33, "width": 1200, "height": 800},
        )
    ])
    monkeypatch.setattr(visual_capture, "capture_window", lambda _window, output, _backend: Image.new("RGB", (200, 100), "black").save(output) or "fake")

    result = visual_capture.capture_ableton_window(output_path=tmp_path / "live.png")

    assert result["warning"] == "blank_capture"
    assert result["validation_blocker"] == "blank_capture_invalid"
    assert "restart the terminal" in result["permission_hint"]


def test_capture_blank_result_includes_validation_blocker(monkeypatch, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Darwin",
            id=100,
            title="vibe-m4l",
            owner="Live",
            process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 33, "width": 1200, "height": 800},
        )
    ])

    def fake_capture(_window, output, _backend):
        Image.new("RGB", (200, 100), "black").save(output)
        return "fake"

    monkeypatch.setattr(visual_capture, "capture_window", fake_capture)

    result = visual_capture.capture_ableton_window(output_path=tmp_path / "live.png", max_width=100)

    assert result["warning"] == "blank_capture"
    assert result["validation_blocker"] == "blank_capture_invalid"
    assert result["next_action"] == "unlock_or_wake_display_before_visual_e2e"
    assert "Screen Recording permission" in result["permission_hint"]


def test_image_content_stats_detects_nonblank_capture():
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", (40, 20), "black")
    for x in range(10, 30):
        for y in range(5, 15):
            image.putpixel((x, y), (220, 220, 220))

    stats = visual_capture.image_content_stats(image)

    assert stats["blank"] is False
    assert stats["bbox"] == [10, 5, 30, 15]
    assert stats["nonblack_fraction"] > 0.1


def test_default_selection_prefers_largest_verified_ableton_window(monkeypatch):
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Darwin",
            id=10,
            title="",
            owner="Live",
            process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 0, "width": 1470, "height": 33},
        ),
        WindowInfo(
            platform="Darwin",
            id=20,
            title="vibe-m4l",
            owner="Live",
            process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
            bundle_id="com.ableton.live",
            bounds={"x": 0, "y": 33, "width": 1313, "height": 923},
        ),
    ])
    assert visual_capture.select_ableton_window().id == 20


def test_macos_capture_uses_window_id_screencapture(monkeypatch, tmp_path):
    calls = []

    class Result:
        stdout = ""
        stderr = ""

    def fake_run(args, **_kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"png")
        return Result()

    monkeypatch.setattr(visual_capture.subprocess, "run", fake_run)
    window = WindowInfo(
        platform="Darwin",
        id=9876,
        title="vibe-m4l",
        owner="Live",
        process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    output = tmp_path / "live.png"
    visual_capture.capture_window(window, output)
    assert calls == [["screencapture", "-x", "-l", "9876", str(output)]]


def test_macos_capture_reports_screencapture_stderr(monkeypatch, tmp_path):
    def fake_run(*_args, **_kwargs):
        raise visual_capture.subprocess.CalledProcessError(
            1,
            ["screencapture"],
            stderr="could not create image from window\n",
        )

    monkeypatch.setattr(visual_capture.subprocess, "run", fake_run)
    window = WindowInfo(
        platform="Darwin",
        id=9876,
        title="vibe-m4l",
        owner="Live",
        process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
    )
    with pytest.raises(RuntimeError, match="could not create image from window"):
        visual_capture.capture_window(window, tmp_path / "live.png", backend="screencapture")


def test_windows_capture_rejects_stale_window_id(monkeypatch, tmp_path):
    target = WindowInfo(
        platform="Windows",
        id=100,
        title="Untitled",
        owner="Ableton Live",
        process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
    )
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Windows",
            id=101,
            title="Untitled",
            owner="Ableton Live",
            process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
        ),
    ])

    with pytest.raises(RuntimeError, match="window id .* could not be re-verified"):
        visual_capture.capture_window(target, tmp_path / "live.png", backend="windows-capture")


def test_windows_capture_rejects_id_belonging_to_non_ableton(monkeypatch, tmp_path):
    # A re-verified id that resolves to a non-Ableton window is refused, even
    # though the caller's WindowInfo claims an Ableton owner.
    target = WindowInfo(
        platform="Windows",
        id=100,
        title="Untitled",
        owner="Ableton Live",
        process_path=r"C:\Program Files\Ableton\Ableton Live Suite\Program\Ableton Live Suite.exe",
    )
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [
        WindowInfo(
            platform="Windows",
            id=100,
            title="Untitled",
            owner="Notes",
            process_path=r"C:\Windows\notepad.exe",
        ),
    ])

    with pytest.raises(RuntimeError, match="not an Ableton Live / Max Console window"):
        visual_capture.capture_window(target, tmp_path / "live.png", backend="windows-capture")


def test_visual_capture_cli_returns_json_error(monkeypatch, capsys):
    monkeypatch.setattr(visual_capture, "capture_ableton_window", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blocked")))
    assert visual_capture.main([]) == 1
    output = capsys.readouterr().out
    assert '"ok": false' in output
    assert '"blocked"' in output


# --- OCR-on-capture (opt-in ocr=true) ------------------------------------------------


def _draw_ocr_fixture(path):
    # A few words + a number, drawn with a real TrueType font so the glyphs are
    # legible to Vision. The exact font is not load-bearing; we just need crisp text.
    Image = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw, ImageFont

    font = None
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        if Path(candidate).exists():
            try:
                font = ImageFont.truetype(candidate, 40)
                break
            except Exception:
                pass
    image = Image.new("RGB", (480, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "ERROR jsobject", fill="black", font=font)
    draw.text((20, 90), "value 128", fill="black", font=font)
    image.save(path)


def test_run_ocr_reads_known_text_and_boxes(tmp_path):
    # Gate on Apple Vision being importable so the suite stays green off-macOS
    # (mirrors the importorskip precedent used for the Pillow/windows paths).
    pytest.importorskip("Vision")
    import ocr

    fixture = tmp_path / "fixture.png"
    _draw_ocr_fixture(fixture)

    result = ocr.run_ocr(str(fixture))

    assert result["engine"] == "apple-vision"
    blob = result["text"].lower()
    assert "error" in blob
    assert "jsobject" in blob or "js" in blob
    assert "128" in blob
    # Every recognized line carries a confidence and a 4-tuple pixel box that
    # lands inside the source image (480x160), origin top-left.
    assert result["lines"], "expected at least one recognized line"
    for line in result["lines"]:
        assert 0.0 <= line["confidence"] <= 1.0
        x, y, w, h = line["bbox"]
        assert w > 0 and h > 0
        assert 0 <= x <= 480 and 0 <= y <= 160
        assert x + w <= 480 + 2 and y + h <= 160 + 2  # allow rounding slop
    # The "value 128" line is drawn below "ERROR jsobject"; boxes preserve that
    # ordering (top-left origin), confirming the y-flip is correct.
    tops = [line["bbox"][1] for line in result["lines"]]
    assert tops == sorted(tops) or len(result["lines"]) == 1


def test_run_ocr_drops_lines_below_min_confidence(tmp_path):
    pytest.importorskip("Vision")
    import ocr

    fixture = tmp_path / "fixture.png"
    _draw_ocr_fixture(fixture)

    # An impossible confidence floor drops everything but still returns the stub
    # shape (engine present, empty lines/text) rather than raising.
    result = ocr.run_ocr(str(fixture), min_confidence=1.1)
    assert result["engine"] == "apple-vision"
    assert result["lines"] == []
    assert result["text"] == ""


def _macos_ableton_window():
    # Minimal verified-Ableton window for OCR-path tests (platform value is
    # irrelevant to the OCR flow itself; run_ocr is monkeypatched).
    return WindowInfo(
        platform="Darwin",
        id=100,
        title="Live Set",
        owner="Live",
        process_path="/Applications/Ableton Live Suite.app/Contents/MacOS/Live",
        bundle_id="com.ableton.live",
        bounds={"x": 0, "y": 0, "width": 400, "height": 300},
    )


def test_run_ocr_runs_on_full_res_before_downscale(tmp_path, monkeypatch):
    # capture_ableton_window must OCR the native-resolution file BEFORE
    # postprocess downscales it in place. We assert run_capture_ocr sees the
    # full-size image even when max_width forces a thumbnail.
    Image = pytest.importorskip("PIL.Image")
    seen_sizes = {}

    def fake_run_ocr(path, **_kwargs):
        with Image.open(path) as image:
            seen_sizes["ocr"] = image.size
        return {"engine": "fake", "lines": [], "text": ""}

    import ocr as ocr_module

    monkeypatch.setattr(ocr_module, "run_ocr", fake_run_ocr)
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [_macos_ableton_window()])
    monkeypatch.setattr(
        visual_capture,
        "capture_window",
        lambda _w, output, _b: Image.new("RGB", (400, 300), "white").save(output) or "fake",
    )

    result = visual_capture.capture_ableton_window(output_path=tmp_path / "live.png", max_width=100, ocr=True)

    # OCR saw the full 400x300 capture; the saved PNG was downscaled afterward.
    assert seen_sizes["ocr"] == (400, 300)
    assert result["ocr"]["engine"] == "fake"
    assert result["postprocess"]["size"][0] <= 100


def test_capture_ocr_disabled_by_default(monkeypatch, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    called = {"n": 0}

    import ocr as ocr_module

    def fake_run_ocr(*_a, **_k):
        called["n"] += 1
        return {"engine": "fake", "lines": [], "text": ""}

    monkeypatch.setattr(ocr_module, "run_ocr", fake_run_ocr)
    monkeypatch.setattr(visual_capture, "list_platform_windows", lambda: [_macos_ableton_window()])
    monkeypatch.setattr(
        visual_capture,
        "capture_window",
        lambda _w, output, _b: Image.new("RGB", (400, 300), "white").save(output) or "fake",
    )

    result = visual_capture.capture_ableton_window(output_path=tmp_path / "live.png")

    assert "ocr" not in result
    assert called["n"] == 0


def test_run_capture_ocr_degrades_on_failure(tmp_path, monkeypatch):
    # An OCR failure must never sink the capture: run_capture_ocr returns an
    # error stub instead of propagating.
    import ocr as ocr_module

    monkeypatch.setattr(ocr_module, "run_ocr", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("vision boom")))
    out = tmp_path / "x.png"
    out.write_bytes(b"not-an-image")

    stub = visual_capture.run_capture_ocr(out, True, None)
    assert stub["engine"] == "error"
    assert "vision boom" in stub["error"]
    assert stub["lines"] == [] and stub["text"] == ""


def test_run_ocr_non_macos_returns_unavailable_stub(monkeypatch, tmp_path):
    # On a non-Darwin platform run_ocr returns a clear stub rather than raising.
    import ocr

    monkeypatch.setattr(ocr.platform, "system", lambda: "Linux")
    result = ocr.run_ocr(str(tmp_path / "whatever.png"))
    assert result == {
        "engine": "none",
        "lines": [],
        "text": "",
        "error": "ocr_unavailable",
        "detail": "OCR is currently implemented for macOS (Apple Vision) only; platform=Linux",
    }
