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

#: Shown until the live list arrives. Deliberately short: the real contents come from the
#: API, because model ids move and a hardcoded list goes stale silently.
FALLBACK_MODELS = (DEFAULT_MODEL, "gemini-2.5-pro", "gemini-2.5-flash")

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
                       "so it cannot be committed or shared by accident. Nothing is "
                       "written until the key answers a test call.")).pack(
            anchor="w", pady=(4, 10))
        # Said BEFORE the key is pasted rather than after the limit is hit, or it reads as
        # the tool being broken mid-session.
        tk.Label(self, bg=BG, fg=DIM, font=body_font, justify="left", wraplength=430,
                 text=("The free tier is limited: fine for a track at a time, not for a "
                       "whole project or A/B-ing versions. A paid tier lifts "
                       "that.")).pack(anchor="w", pady=(0, 10))

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
        self.note.configure(text="asking Google which models this key can reach…", fg=DIM)

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
            self.note.configure(text=f"Not saved — the key was rejected.\n{payload[:300]}",
                                fg=ERR)
            return
        path = save_key(key)
        self.on_saved(key, [name for name, _ in payload])
        messagebox.showinfo("Key saved",
                            f"{len(payload)} models reachable.\nSaved to {path}",
                            parent=self)
        self.destroy()
