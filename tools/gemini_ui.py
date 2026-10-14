"""The bits of window every Gemini front end needs: the palette, the key, the key dialog.

Split out when the studio window became part of what ships. It had been importing these
from ``gemini_chat.py``, which is a development tool and stays private — so the shipped
app depended on a file users would never receive. Moving them here is the alternative to
the obvious wrong answer, which is a second copy: this project has been bitten three times
by duplicated code, and a duplicated key dialog would be two places to get key handling
wrong rather than one.

WHY THE KEY LIVES OUTSIDE THE REPOSITORY. ``backend.gemini_key()`` reads three locations,
and only one of them can be committed by accident. This writes to the home one, always.
A tool that stores your key where git can see it has done you harm no feature makes up for.

Stdlib and tkinter only, like everything else here.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox

from gemini_client import DEFAULT_MODEL, GeminiError, list_models

#: Written to the HOME location, never the repo one. Both are read by
#: ``backend.gemini_key()``; only one of them is inside a git working tree.
KEY_PATH = Path.home() / ".ai-bridge" / "gemini_api_key.txt"

#: Google's own page for issuing one, so "where do I get a key" is answered in the dialog
#: rather than by a search.
KEY_URL = "https://aistudio.google.com/apikey"

#: Shown until the live list arrives. Deliberately short, and the two extras are Google's
#: moving aliases rather than dated ids: "gemini-2.5-flash" was still in the API's own
#: model list on 2026-09-19 while answering 404 "no longer available to new users".
FALLBACK_MODELS = (DEFAULT_MODEL, "gemini-pro-latest", "gemini-flash-latest")

BG = "#1e1f22"
PANEL = "#2b2d30"
FG = "#dfe1e5"
DIM = "#9aa0a6"
USER = "#7cb7ff"
MODEL = "#a5d6a7"
ERR = "#ff8a80"


def save_key(key: str, path: Path = KEY_PATH) -> Path:
    """Write a key to the home location, readable by its owner only where that means
    anything.

    A comment line goes in above it so the file explains itself if it is found in a
    year's time. ``backend._read_key_file`` skips ``#`` lines, so this stays readable by
    the resolution the rest of the program uses.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Gemini API key for the AI Bridge. Saved from the app.\n"
        "# This file lives OUTSIDE the repository on purpose: it cannot be committed\n"
        "# from here. Delete this file to revoke local access.\n"
        f"{key.strip()}\n", encoding="utf-8", newline="")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows ignores the POSIX bits; the file is under the user profile, which is
        # the protection that actually applies there. Not worth failing over.
        pass
    return path


class KeyDialog(tk.Toplevel):
    """Paste a key, check it against the live API, then save it.

    It is checked BEFORE it is saved, by asking the API to list models. A key that is
    merely stored is a key you discover is wrong at the worst moment — mid-session, as an
    HTTP 400 that reads like a bug in the program.

    The field is masked, and the value is never logged, never printed and never written
    to a transcript.
    """

    def __init__(self, parent, on_saved, body_font):
        super().__init__(parent)
        self.on_saved = on_saved
        self.title("Gemini API key")
        self.configure(bg=BG, padx=18, pady=16)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        tk.Label(self, text="Paste your Gemini API key", bg=BG, fg=FG,
                 font=(body_font.cget("family"), 11, "bold")).pack(anchor="w")
        tk.Label(self, bg=BG, fg=DIM, font=body_font, justify="left", wraplength=430,
                 text=("It is saved to your user profile, outside the program's folder, "
                       "so it cannot be shared by accident. Nothing is saved until "
                       "Google confirms the key works.")).pack(
            anchor="w", pady=(4, 10))
        # Said BEFORE the key is pasted rather than after the limit is hit, or it reads as
        # the tool being broken mid-session.
        tk.Label(self, bg=BG, fg=DIM, font=body_font, justify="left", wraplength=430,
                 text=("The free tier is limited: fine for a track at a time, not for a "
                       "whole project or A/B-ing versions. A paid tier is "
                       "recommended.")).pack(anchor="w", pady=(0, 10))

        self.value = tk.StringVar()
        self.field = tk.Entry(self, textvariable=self.value, show="•", width=52,
                              bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                              font=body_font)
        self.field.pack(ipady=6, fill="x")
        self.field.focus_set()
        self.field.bind("<Return>", lambda _e: self.check_and_save())

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        self.reveal = tk.IntVar(value=0)
        tk.Checkbutton(row, text="Show", variable=self.reveal, command=self._reveal,
                       bg=BG, fg=DIM, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, font=body_font,
                       relief="flat", highlightthickness=0).pack(side="left")
        link = tk.Label(row, text="Get a free key →", bg=BG, fg=USER, font=body_font,
                        cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda _e: webbrowser.open(KEY_URL))

        self.note = tk.Label(self, text="", bg=BG, fg=DIM, font=body_font,
                             wraplength=430, justify="left")
        self.note.pack(anchor="w", pady=(10, 0))

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", pady=(14, 0))
        tk.Button(buttons, text="Cancel", relief="flat", bg=PANEL, fg=FG, width=10,
                  activebackground="#3a3d41", activeforeground=FG, font=body_font,
                  command=self.destroy).pack(side="right")
        self.save_button = tk.Button(buttons, text="Check and save", relief="flat",
                                     bg="#3574f0", fg="white", width=14,
                                     activebackground="#4b84f2", activeforeground="white",
                                     font=body_font, command=self.check_and_save)
        self.save_button.pack(side="right", padx=(0, 8))

        if KEY_PATH.exists():
            self.note.configure(text=f"A key is already saved at {KEY_PATH}. "
                                     f"Saving replaces it.")

    def _reveal(self) -> None:
        self.field.configure(show="" if self.reveal.get() else "•")

    def check_and_save(self) -> None:
        key = self.value.get().strip()
        if len(key) < 20:
            self.note.configure(
                text="That looks too short to be a key — a truncated paste, usually.",
                fg=ERR)
            return
        self.save_button.configure(state="disabled", text="Checking…")
        self.note.configure(text="checking the key with Google…", fg=DIM)

        result: queue.Queue = queue.Queue()

        def work():
            try:
                result.put(("ok", list_models(key)))
            except GeminiError as exc:
                result.put(("bad", str(exc)))
            except Exception as exc:                                  # noqa: BLE001
                result.put(("bad", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()
        self.after(120, lambda: self._poll(result, key))

    def _poll(self, result: queue.Queue, key: str) -> None:
        try:
            kind, payload = result.get_nowait()
        except queue.Empty:
            self.after(120, lambda: self._poll(result, key))
            return
        if kind == "bad":
            self.save_button.configure(state="normal", text="Check and save")
            # Not saved. A key that cannot list models will not answer a question either,
            # and storing it would only move the failure somewhere less obvious.
            self.note.configure(text=f"Not saved. {payload[:300]}", fg=ERR)
            return
        path = save_key(key)
        self.on_saved(key, [name for name, _ in payload])
        messagebox.showinfo("Key saved",
                            f"Saved to {path}",
                            parent=self)
        self.destroy()


class SoundSearchDialog(tk.Toplevel):
    """Install, update and feed the bridge's ears, in plain words.

    `find_sound` works by listening, which needs the listener: a separate download
    with models and real dependencies. This dialog is the whole journey — what it is,
    one button to fetch it, a scan to build the index, and a check for updates once it
    is installed. Everything heavy runs off the UI thread; workers talk back through a
    queue, never touching a widget (the same rule the main window lives by).
    """

    def __init__(self, parent, body_font):
        super().__init__(parent)
        self.title("Sound search")
        self.configure(bg=BG, padx=18, pady=16)
        self.resizable(False, False)
        self.transient(parent)
        self.q: queue.Queue = queue.Queue()
        self.proc = None

        tk.Label(self, text="Find your sounds by how they sound", bg=BG, fg=FG,
                 font=(body_font.cget("family"), 11, "bold")).pack(anchor="w")
        tk.Label(self, bg=BG, fg=DIM, font=body_font, justify="left", wraplength=470,
                 text=("Ask for “a crispy clap” or “a dark pad, no drums” "
                       "and the bridge finds the file — even in sample folders that have "
                       "no tags at all. For that it needs its ears: the listener, a "
                       "separate download of about 100 MB (program, models and their "
                       "libraries). It listens on your computer only; your sounds never "
                       "leave it.")).pack(anchor="w", pady=(4, 10))

        self.state_label = tk.Label(self, bg=BG, fg=DIM, font=body_font)
        self.state_label.pack(anchor="w", pady=(0, 8))

        row = tk.Frame(self, bg=BG)
        row.pack(anchor="w", pady=(0, 8))
        self.action = tk.Button(row, text="…", state="disabled", command=self._install,
                                bg=PANEL, fg=FG, activebackground=PANEL, relief="flat",
                                padx=12, pady=4, font=body_font)
        self.action.pack(side="left")
        self.update_btn = tk.Button(row, text="Check for update", command=self._check_update,
                                    bg=PANEL, fg=FG, activebackground=PANEL, relief="flat",
                                    padx=12, pady=4, font=body_font)

        self.scan_frame = tk.Frame(self, bg=BG)
        self.scan_mode = tk.StringVar(value="live")
        tk.Radiobutton(self.scan_frame, text="The sounds Ableton already knows",
                       value="live", variable=self.scan_mode, bg=BG, fg=FG,
                       selectcolor=PANEL, activebackground=BG, font=body_font).pack(anchor="w")
        tk.Radiobutton(self.scan_frame, text="A folder I choose…", value="folder",
                       variable=self.scan_mode, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, font=body_font).pack(anchor="w")
        srow = tk.Frame(self.scan_frame, bg=BG)
        srow.pack(anchor="w", pady=(4, 0))
        self.scan_btn = tk.Button(srow, text="Scan", command=self._scan, bg=PANEL, fg=FG,
                                  activebackground=PANEL, relief="flat", padx=12, pady=4,
                                  font=body_font)
        self.scan_btn.pack(side="left")
        self.stop_btn = tk.Button(srow, text="Stop", command=self._stop, bg=PANEL, fg=DIM,
                                  activebackground=PANEL, relief="flat", padx=12, pady=4,
                                  font=body_font, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.out = tk.Text(self, height=9, width=64, bg=PANEL, fg=DIM, relief="flat",
                           font=body_font, state="disabled")
        self.out.pack(fill="x", pady=(8, 0))

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._drain)
        self._refresh_state()

    # ---- helpers -------------------------------------------------------------------

    def _say(self, line: str) -> None:
        self.out.configure(state="normal")
        self.out.insert("end", line.rstrip() + "\n")
        self.out.see("end")
        self.out.configure(state="disabled")

    def _refresh_state(self) -> None:
        def work():
            import sound_search_setup as sss                          # noqa: PLC0415
            self.q.put(("state", sss.status()))
        threading.Thread(target=work, daemon=True).start()

    def _on_state(self, st: dict) -> None:
        if st["installed"]:
            n = st.get("indexed")
            idx = f"{n:,} sounds indexed" if n else "nothing scanned yet"
            rel = st.get("release") or "version unknown"
            self.state_label.configure(fg=MODEL, text=f"Installed ({rel}) — {idx}.")
            self.action.pack_forget()
            self.update_btn.pack(side="left")
            self.scan_frame.pack(anchor="w")
        else:
            self.state_label.configure(fg=DIM, text="Not installed yet.")
            self.action.configure(text="Download and set up (about 100 MB)",
                                  state="normal")

    # ---- actions -------------------------------------------------------------------

    def _install(self, update: bool = False) -> None:
        self.action.configure(state="disabled")
        self.update_btn.configure(state="disabled")

        def work():
            import sound_search_setup as sss                          # noqa: PLC0415
            try:
                sss.install(progress=lambda m: self.q.put(("line", m)), update=update)
                self.q.put(("installed", None))
            except Exception as exc:                                  # noqa: BLE001
                self.q.put(("error", f"{exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _check_update(self) -> None:
        self.update_btn.configure(state="disabled")

        def work():
            import sound_search_setup as sss                          # noqa: PLC0415
            try:
                self.q.put(("update", sss.check_update()))
            except Exception as exc:                                  # noqa: BLE001
                self.q.put(("error", f"{exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _scan(self) -> None:
        import sound_search_setup as sss                              # noqa: PLC0415
        folders = None
        if self.scan_mode.get() == "folder":
            from tkinter import filedialog                            # noqa: PLC0415
            picked = filedialog.askdirectory(parent=self)
            if not picked:
                return
            folders = [picked]
        cmd = sss.scan_command(folders)
        self._say("scanning… (you can keep using Studio; Stop ends it early and "
                  "keeps what is done)")
        self.scan_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal", fg=FG)

        def work():
            import subprocess                                         # noqa: PLC0415
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,  # noqa: S603
                                             stderr=subprocess.STDOUT, text=True,
                                             creationflags=flags)
                for line in self.proc.stdout:
                    if line.strip():
                        self.q.put(("line", line))
                self.proc.wait()
                self.q.put(("scanned", self.proc.returncode))
            except Exception as exc:                                  # noqa: BLE001
                self.q.put(("error", f"{exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self._say("stopping — every sound already analysed stays in the index.")

    def _close(self) -> None:
        self._stop()
        self.destroy()

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self._say(str(payload))
                elif kind == "state":
                    self._on_state(payload)
                elif kind == "installed":
                    self._say("done.")
                    self._refresh_state()
                elif kind == "scanned":
                    self._say("scan finished." if payload == 0 else
                              f"the scan ended with code {payload} — see the lines above.")
                    self.scan_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled", fg=DIM)
                    self._refresh_state()
                elif kind == "update":
                    cur, new = payload["installed"], payload["latest"]
                    if payload["update_available"]:
                        self._say(f"an update exists: {cur or 'unknown'} → {new}")
                        self.update_btn.configure(text=f"Update to {new}", state="normal",
                                                  command=lambda: self._install(update=True))
                    else:
                        self._say(f"up to date ({cur}).")
                        self.update_btn.configure(state="normal")
                elif kind == "error":
                    self._say(f"that did not work: {payload}")
                    self.action.configure(state="normal")
                    self.update_btn.configure(state="normal")
                    self.scan_btn.configure(state="normal")
        except queue.Empty:
            pass
        try:
            self.after(100, self._drain)
        except tk.TclError:
            pass
