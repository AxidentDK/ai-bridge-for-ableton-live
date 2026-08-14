"""OS-level UI machinery for the beyond-LOM tools (save, export).

Windows: find Live's main window, bring it to the foreground *reliably*, and
synthesize keystrokes. macOS equivalents use System Events via osascript.

The foreground grab uses the AttachThreadInput technique: a plain
SetForegroundWindow silently loses to Windows' focus-stealing prevention when
another app is frontmost (field-hit 2026-08-08/09 — keystrokes landed in the
wrong app), so we attach to the current foreground thread, raise the window,
and *verify* we actually own the foreground before any key is sent.
"""
from __future__ import annotations

import os
import platform
import subprocess
import time


class UiError(RuntimeError):
    pass


IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"


# --- Windows -------------------------------------------------------------------

def find_live_window():
    """Visible top-level window whose process image is Ableton Live itself.

    Match by executable path, not window title (titles are set-dependent) and
    not process-name globs ('Ableton*' also matches bridge server executables).
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return True
        try:
            buf = ctypes.create_unicode_buffer(4096)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                if os.path.basename(buf.value).lower().startswith("ableton live"):
                    found.append(hwnd)
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else None


def focus_window(hwnd, settle: float = 0.25) -> None:
    """Bring a window to the foreground and VERIFY it got there.

    AttachThreadInput to the current foreground thread defeats focus-stealing
    prevention; without it the keystrokes can land in whatever app is frontmost.
    """
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(me, fg_thread, True)) if fg else False
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    time.sleep(settle)
    if user32.GetForegroundWindow() != hwnd:
        raise UiError(
            "Could not bring the Ableton Live window to the foreground (another "
            "application is holding focus). Bring Live to the front and retry."
        )


def _input_structs():
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    return KEYBDINPUT, INPUT


def send_keys(events) -> None:
    """events: iterable of (virtual_key, is_keyup)."""
    import ctypes

    KEYBDINPUT, INPUT = _input_structs()
    INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002
    seq = []
    for vk, up in events:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
        seq.append(inp)
    arr = (INPUT * len(seq))(*seq)
    sent = ctypes.windll.user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    if sent != len(seq):
        raise UiError("SendInput delivered %d of %d key events" % (sent, len(seq)))


def type_text(text: str) -> None:
    """Type arbitrary unicode text (KEYEVENTF_UNICODE — layout-independent)."""
    import ctypes

    KEYBDINPUT, INPUT = _input_structs()
    INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x0002, 0x0004
    seq = []
    for char in text:
        for up in (False, True):
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            inp.ki = KEYBDINPUT(0, ord(char), KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, None)
            seq.append(inp)
    arr = (INPUT * len(seq))(*seq)
    sent = ctypes.windll.user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    if sent != len(seq):
        raise UiError("SendInput delivered %d of %d unicode events" % (sent, len(seq)))


def chord(*vks) -> list:
    """Key-down/up sequence for a chord, e.g. chord(VK_CONTROL, VK_S)."""
    downs = [(vk, False) for vk in vks]
    ups = [(vk, True) for vk in reversed(vks)]
    return downs + ups


VK_CONTROL, VK_SHIFT, VK_RETURN, VK_ESCAPE = 0x11, 0x10, 0x0D, 0x1B
VK_A, VK_R, VK_S = 0x41, 0x52, 0x53
VK_UP, VK_DOWN, VK_HOME = 0x26, 0x28, 0x24


def find_window_by_title(title: str):
    """Exact-title top-level window (e.g. Live's 'Export Audio/Video' dialog)."""
    import ctypes

    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    return hwnd or None


def window_rect(hwnd) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def click_at(x: int, y: int, settle: float = 0.3) -> None:
    """Left-click at screen coordinates (physical pixels)."""
    import ctypes

    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(settle)
    user32.mouse_event(2, 0, 0, 0, 0)  # LEFTDOWN
    user32.mouse_event(4, 0, 0, 0, 0)  # LEFTUP


# --- macOS ----------------------------------------------------------------------

def osascript(script: str, what: str) -> None:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise UiError("%s failed: %s" % (what, result.stderr.strip() or result.stdout.strip()))
