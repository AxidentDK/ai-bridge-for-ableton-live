"""A chat window for Gemini, with the conversation kept and the session logged.

    python tools/gemini_chat.py

WHY A WINDOW, when ``ask_gemini.py`` already sends files over the API. The CLI answers
one question and forgets it: every follow-up has to restate the context, and "what did
you mean by that" is not askable at all. The browser tab could hold a conversation, but
it silently dropped roughly a third of long pastes and gave no way to tell a delivered
message from a lost one until the reply came back about the wrong thing.

This keeps what each was good at: the API's reliability — a failure is a message on the
screen, not an empty text box — with a real conversation on top, and every exchange
written to a markdown log the moment it happens.

NOTHING HERE NEEDS A TEXT EDITOR OR A FILE PATH. The key is pasted into a dialog and
saved outside the repo; the model is chosen from a dropdown filled from the models the
key can actually reach, not from a list someone typed from memory. Starting with no key
at all is a supported path — the window opens and asks.

WHAT IT DOES NOT DO, deliberately: it does not edit files, run anything, or touch the
repo. It is a place to think out loud with a reviewer that has read your code. Logs land
in ``gemini_reviews/``, which is gitignored.

Tkinter, because it ships with Python. The bridge is near-zero-dependency and a chat
window is not a reason to make someone install a GUI toolkit.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gemini_client import (  # noqa: E402
    DEFAULT_MODEL, Conversation, GeminiError, Transcript, list_models, read_key,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "gemini_reviews"

#: Where a file picker should start. Both repos, because a review usually spans them.
SOURCE_ROOTS = (REPO_ROOT, REPO_ROOT.parent / "ai-bridge-listener")

#: Long enough to be worth warning about before it is sent. A 6,000-line paste is a
#: legitimate thing to do here, but it should be a decision rather than an accident.
_BIG_MESSAGE_CHARS = 120_000

#: Where a pasted key is written: the HOME location, never the repo one. Both are read
#: by ``backend.gemini_key()``, but only one of them can be committed by accident, and
#: this program should not be the reason someone's key ends up on GitHub.
KEY_PATH = Path.home() / ".ai-bridge" / "gemini_api_key.txt"

#: Google's own page for issuing one, so "where do I get a key" has an answer in the
#: dialog rather than a search.
KEY_URL = "https://aistudio.google.com/apikey"

#: Shown until the live list arrives. Deliberately short: the dropdown's real contents
#: come from the API, because model ids move and a hardcoded list goes stale silently.
_FALLBACK_MODELS = (DEFAULT_MODEL, "gemini-2.5-pro", "gemini-2.5-flash")

_BG = "#1e1f22"
_PANEL = "#2b2d30"
_FG = "#dfe1e5"
_DIM = "#9aa0a6"
_USER = "#7cb7ff"
_MODEL = "#a5d6a7"
_ERR = "#ff8a80"


def save_key(key: str, path: Path = KEY_PATH) -> Path:
    """Write a key to the home location, readable by its owner only where that means
    anything.

    A comment line goes in above it so the file explains itself if it is found in a
    year's time. ``backend._read_key_file`` skips ``#`` lines, so this stays readable
    by the resolution the rest of the program uses.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Gemini API key for the AI Bridge tools. Saved from the chat window.\n"
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
    merely stored is a key you discover is wrong at the worst moment — mid-review, as an
    HTTP 400 that reads like a bug in the program.

    The field is masked, the value is never logged, never printed and never written to
    the transcript.
    """

    def __init__(self, parent, on_saved, body_font):
        super().__init__(parent)
        self.on_saved = on_saved
        self.title("Gemini API key")
        self.configure(bg=_BG, padx=18, pady=16)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        tk.Label(self, text="Paste your Gemini API key", bg=_BG, fg=_FG,
                 font=(body_font.cget("family"), 11, "bold")).pack(anchor="w")
        tk.Label(self, bg=_BG, fg=_DIM, font=body_font, justify="left", wraplength=430,
                 text=("It is saved to your user profile, outside the repository, so it "
                       "cannot be committed by accident. Nothing is written until the "
                       "key answers a test call.")).pack(anchor="w", pady=(4, 10))

        self.value = tk.StringVar()
        self.field = tk.Entry(self, textvariable=self.value, show="•", width=52,
                              bg=_PANEL, fg=_FG, insertbackground=_FG, relief="flat",
                              font=body_font)
        self.field.pack(ipady=6, fill="x")
        self.field.focus_set()
        self.field.bind("<Return>", lambda _e: self.check_and_save())

        row = tk.Frame(self, bg=_BG)
        row.pack(fill="x", pady=(6, 0))
        self.reveal = tk.IntVar(value=0)
        tk.Checkbutton(row, text="Show", variable=self.reveal, command=self._reveal,
                       bg=_BG, fg=_DIM, selectcolor=_PANEL, activebackground=_BG,
                       activeforeground=_FG, font=body_font,
                       relief="flat", highlightthickness=0).pack(side="left")
        link = tk.Label(row, text="Get a key →", bg=_BG, fg=_USER, font=body_font,
                        cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda _e: webbrowser.open(KEY_URL))

        self.note = tk.Label(self, text="", bg=_BG, fg=_DIM, font=body_font,
                             wraplength=430, justify="left")
        self.note.pack(anchor="w", pady=(10, 0))

        buttons = tk.Frame(self, bg=_BG)
        buttons.pack(fill="x", pady=(14, 0))
        tk.Button(buttons, text="Cancel", relief="flat", bg=_PANEL, fg=_FG, width=10,
                  activebackground="#3a3d41", activeforeground=_FG, font=body_font,
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
                fg=_ERR)
            return
        self.save_button.configure(state="disabled", text="Checking…")
        self.note.configure(text="asking Google which models this key can reach…",
                            fg=_DIM)

        result: queue.Queue = queue.Queue()

        def work():
            try:
                result.put(("ok", list_models(key)))
            except GeminiError as exc:
                result.put(("bad", str(exc)))
            except Exception as exc:                              # noqa: BLE001
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
            # Not saved. A key that cannot list models will not answer a question
            # either, and storing it would only move the failure somewhere less
            # obvious.
            self.note.configure(text=f"Not saved — the key was rejected.\n{payload[:300]}",
                                fg=_ERR)
            return
        path = save_key(key)
        self.on_saved(key, [name for name, _ in payload])
        messagebox.showinfo("Key saved",
                            f"{len(payload)} models reachable.\nSaved to {path}",
                            parent=self)
        self.destroy()


class ChatWindow:
    def __init__(self, root: tk.Tk, key: str | None, model: str = DEFAULT_MODEL):
        self.root = root
        self.key = key
        self.chat = Conversation(model=model)
        self.log = Transcript(OUT_DIR, label="chat", model=model)
        self.replies: queue.Queue = queue.Queue()
        self.attachments: list[Path] = []
        self.busy = False

        root.title("Gemini — peer review")
        root.geometry("1020x780")
        root.configure(bg=_BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.body = tkfont.Font(family="Segoe UI", size=10)
        mono = tkfont.Font(family="Cascadia Mono", size=10)

        self._build_menu()

        # ---- toolbar: the model dropdown, where it can be seen and changed ----------
        top = tk.Frame(root, bg=_BG)
        top.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(top, text="Model", bg=_BG, fg=_DIM, font=self.body).pack(side="left")
        self.model_var = tk.StringVar(value=model)
        self.model_box = ttk.Combobox(top, textvariable=self.model_var, width=34,
                                      state="readonly", values=list(_FALLBACK_MODELS))
        self.model_box.pack(side="left", padx=(8, 0))
        self.model_box.bind("<<ComboboxSelected>>", self._on_model_change)
        self.key_label = tk.Label(top, text="", bg=_BG, fg=_DIM, font=self.body,
                                  cursor="hand2")
        self.key_label.pack(side="right")
        self.key_label.bind("<Button-1>", lambda _e: self.ask_for_key())

        # ---- transcript ------------------------------------------------------------
        wrap = tk.Frame(root, bg=_BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=(8, 0))
        self.view = tk.Text(wrap, wrap="word", bg=_PANEL, fg=_FG, font=self.body,
                            relief="flat", padx=14, pady=12, spacing1=2, spacing3=6,
                            insertbackground=_FG, state="disabled")
        bar = ttk.Scrollbar(wrap, command=self.view.yview)
        self.view.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.view.pack(side="left", fill="both", expand=True)

        heading = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.view.tag_configure("who_user", foreground=_USER, font=heading,
                                spacing1=14, spacing3=4)
        self.view.tag_configure("who_model", foreground=_MODEL, font=heading,
                                spacing1=14, spacing3=4)
        self.view.tag_configure("err", foreground=_ERR)
        self.view.tag_configure("dim", foreground=_DIM)
        self.view.tag_configure("code", font=mono, background="#232529",
                                lmargin1=18, lmargin2=18)

        # ---- input -----------------------------------------------------------------
        lower = tk.Frame(root, bg=_BG)
        lower.pack(fill="x", padx=10, pady=(8, 4))
        self.entry = tk.Text(lower, height=5, wrap="word", bg=_PANEL, fg=_FG,
                             font=self.body, relief="flat", padx=10, pady=8,
                             insertbackground=_FG)
        self.entry.pack(side="left", fill="both", expand=True)
        self.entry.focus_set()
        # Enter sends, Shift+Enter makes a newline — the convention every chat client
        # uses, so the muscle memory is already there. `break` stops Tk inserting the
        # newline as well as sending.
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KeyRelease>", lambda _e: self._update_counter())

        buttons = tk.Frame(lower, bg=_BG)
        buttons.pack(side="right", fill="y", padx=(8, 0))
        self.send_button = tk.Button(buttons, text="Send", width=11, relief="flat",
                                     bg="#3574f0", fg="white", activebackground="#4b84f2",
                                     activeforeground="white", font=self.body,
                                     command=self.send)
        self.send_button.pack(fill="x")
        tk.Button(buttons, text="Attach file…", width=11, relief="flat", bg=_PANEL,
                  fg=_FG, activebackground="#3a3d41", activeforeground=_FG,
                  font=self.body, command=self.attach).pack(fill="x", pady=(6, 0))
        tk.Button(buttons, text="Open log", width=11, relief="flat", bg=_PANEL,
                  fg=_DIM, activebackground="#3a3d41", activeforeground=_FG,
                  font=self.body, command=self.open_log).pack(fill="x", pady=(6, 0))

        self.status = tk.Label(root, anchor="w", bg=_BG, fg=_DIM, font=self.body,
                               padx=12, pady=6)
        self.status.pack(fill="x")

        self._say("dim", "The architecture briefing is sent with every turn, so you can "
                         "ask straight out.\nEnter sends · Shift+Enter for a newline · "
                         "Attach file… puts a whole module in the next message.\n"
                         f"Logging to {self.log.path.name} as we go.\n")
        self._refresh_key_label()
        self._idle()
        self.root.after(100, self._drain)
        if self.key:
            self._load_models_async()
        else:
            # No key: say so plainly and open the dialog, rather than failing on the
            # first send with an error about HTTP headers.
            self._say("err", "\nNo API key yet — paste one to start.\n")
            self.root.after(400, self.ask_for_key)

    # ---- menu ----------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        session = tk.Menu(menubar, tearoff=0)
        session.add_command(label="Save transcript as…", command=self.save_transcript_as)
        session.add_command(label="Open this session's log", command=self.open_log)
        session.add_command(label="Open log folder", command=self.open_log_folder)
        session.add_separator()
        session.add_command(label="Clear conversation", command=self.clear_conversation)
        session.add_separator()
        session.add_command(label="Close", command=self.on_close)
        menubar.add_cascade(label="Session", menu=session)

        settings = tk.Menu(menubar, tearoff=0)
        settings.add_command(label="API key…", command=self.ask_for_key)
        settings.add_command(label="Refresh model list", command=self._load_models_async)
        menubar.add_cascade(label="Settings", menu=settings)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Where the key is stored",
                             command=lambda: messagebox.showinfo(
                                 "Key storage",
                                 f"Keys are read from, in order:\n\n"
                                 f"1. $GEMINI_API_KEY\n"
                                 f"2. {REPO_ROOT / 'gemini_api_key.txt'}  (in the repo)\n"
                                 f"3. {KEY_PATH}\n\n"
                                 f"This window saves to (3), outside the repository, so "
                                 f"a pasted key cannot be committed. Delete that file "
                                 f"to revoke local access."))
        helpmenu.add_command(label="Get an API key",
                             command=lambda: webbrowser.open(KEY_URL))
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.root.config(menu=menubar)

    # ---- key and model -------------------------------------------------------------

    def _refresh_key_label(self) -> None:
        if self.key:
            # Never the key itself, and never enough of it to be useful — the last four
            # characters identify WHICH key without being one.
            self.key_label.configure(text=f"key ····{self.key[-4:]}   (change)", fg=_DIM)
        else:
            self.key_label.configure(text="no key — click to add", fg=_ERR)

    def ask_for_key(self) -> None:
        KeyDialog(self.root, self._key_saved, self.body)

    def _key_saved(self, key: str, models: list[str]) -> None:
        self.key = key
        self._refresh_key_label()
        self._apply_models(models)
        self._say("dim", "[key saved — ready]\n")
        self._idle()

    def _apply_models(self, names: list[str]) -> None:
        if not names:
            return
        self.model_box.configure(values=names)
        if self.model_var.get() not in names:
            # The configured default is not reachable with this key. Prefer a Pro tier
            # if there is one — this is a reasoning task, and flash models are worse at
            # it — but say what happened rather than switching silently.
            pick = next((n for n in names if "pro" in n and "vision" not in n), names[0])
            self.model_var.set(pick)
            self.chat.model = pick
            self._say("dim", f"[{DEFAULT_MODEL} is not reachable with this key — "
                             f"using {pick}]\n")

    def _load_models_async(self) -> None:
        if not self.key:
            self.ask_for_key()
            return
        result: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: result.put(self._fetch_models()),
                         daemon=True).start()

        def poll():
            try:
                names = result.get_nowait()
            except queue.Empty:
                self.root.after(150, poll)
                return
            if names:
                self._apply_models(names)
        self.root.after(150, poll)

    def _fetch_models(self) -> list[str]:
        try:
            return [name for name, _ in list_models(self.key)]
        except Exception:                                          # noqa: BLE001
            # A dropdown that cannot be filled is not worth an error dialog: the
            # fallback list works, and the next real send will report the problem
            # properly.
            return []

    def _on_model_change(self, _event=None) -> None:
        chosen = self.model_var.get()
        if chosen == self.chat.model:
            return
        self.chat.model = chosen
        # Recorded in the log: a reply's quality depends on which model gave it, and a
        # transcript that does not say where the model changed is misleading later.
        self._say("dim", f"[model → {chosen}]\n")
        self.log.append("note", f"model switched to `{chosen}`")

    # ---- transcript rendering ------------------------------------------------------

    def _say(self, tag: str, text: str) -> None:
        self.view.configure(state="normal")
        self.view.insert("end", text, tag)
        self.view.configure(state="disabled")
        self.view.see("end")

    def _render(self, who: str, text: str) -> None:
        """Write a turn, giving fenced code blocks a monospaced tag.

        Markdown is not rendered — this is a code reviewer, and a bold-and-headings
        renderer that mangles an underscore in an identifier would be worse than none.
        Only fences are treated specially, because misaligned code is genuinely harder
        to read.
        """
        self.view.configure(state="normal")
        label = "You" if who == "user" else "Gemini"
        self.view.insert("end", f"{label}\n", f"who_{who}")
        for i, chunk in enumerate(text.split("```")):
            if not chunk:
                continue
            if i % 2:
                # Drop the language line: it is markdown syntax, not content.
                lines = chunk.split("\n")
                if lines and lines[0].strip().isalpha():
                    chunk = "\n".join(lines[1:])
                self.view.insert("end", chunk.strip("\n") + "\n", "code")
            else:
                self.view.insert("end", chunk)
        self.view.insert("end", "\n")
        self.view.configure(state="disabled")
        self.view.see("end")

    # ---- state ---------------------------------------------------------------------

    def _idle(self) -> None:
        bits = [f"{len(self.chat.turns) // 2} exchanges",
                f"~{self.chat.estimated_tokens():,} tokens in context (estimate)"]
        if self.attachments:
            bits.append(f"{len(self.attachments)} file(s) attached to next message")
        self.status.configure(text="  ·  ".join(bits), fg=_DIM)

    def _update_counter(self) -> None:
        if not self.busy:
            typed = len(self.entry.get("1.0", "end-1c"))
            if typed > 400:
                self.status.configure(text=f"{typed:,} characters typed", fg=_DIM)
            else:
                self._idle()

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        self.send_button.configure(state="disabled" if busy else "normal",
                                   text="Waiting…" if busy else "Send")
        if busy:
            self.status.configure(text=message or "thinking…", fg=_USER)
        else:
            self._idle()

    # ---- actions -------------------------------------------------------------------

    def _on_return(self, event):
        if event.state & 0x0001:          # Shift held: let the newline through.
            return None
        self.send()
        return "break"

    def attach(self) -> None:
        picked = filedialog.askopenfilenames(
            title="Attach source files to the next message",
            initialdir=str(SOURCE_ROOTS[0]),
            filetypes=[("Source", "*.py *.md *.js *.json *.txt"), ("All files", "*.*")])
        for name in picked:
            self.attachments.append(Path(name))
        if picked:
            names = ", ".join(Path(p).name for p in picked)
            self._say("dim", f"[attached: {names} — sent with your next message]\n")
        self._idle()

    def open_log(self) -> None:
        os.startfile(self.log.path)  # noqa: S606 — a file this program just wrote

    def open_log_folder(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(OUT_DIR)        # noqa: S606

    def save_transcript_as(self) -> None:
        """Copy the running log somewhere the user chooses.

        A copy, not a move: the live log keeps being appended to, because the session is
        not over. Saving a copy mid-session should not stop the crash-proof one.
        """
        target = filedialog.asksaveasfilename(
            title="Save a copy of this session", defaultextension=".md",
            initialfile=self.log.path.name, filetypes=[("Markdown", "*.md")])
        if target:
            Path(target).write_text(self.log.path.read_text(encoding="utf-8"),
                                    encoding="utf-8", newline="")
            self._say("dim", f"[copy saved to {target}]\n")

    def clear_conversation(self) -> None:
        """Forget the history and start a NEW log file.

        Both, together, on purpose. Clearing the window while continuing to append to
        the same log would produce a transcript whose second half answers questions its
        first half cannot see.
        """
        if self.busy:
            return
        if self.chat.turns and not messagebox.askokcancel(
                "Clear conversation",
                f"Forget {len(self.chat.turns) // 2} exchanges and start a new log?\n\n"
                f"The current log stays on disk:\n{self.log.path.name}"):
            return
        self.log.close("cleared by the user")
        self.chat.turns.clear()
        self.log = Transcript(OUT_DIR, label="chat", model=self.chat.model)
        self.view.configure(state="normal")
        self.view.delete("1.0", "end")
        self.view.configure(state="disabled")
        self._say("dim", f"[new conversation · logging to {self.log.path.name}]\n")
        self._idle()

    def _compose(self, typed: str) -> tuple[str, str]:
        """Build the message actually sent, and a short note for the log."""
        if not self.attachments:
            return typed, ""
        parts, names = [typed], []
        for path in self.attachments:
            try:
                code = path.read_text(encoding="utf-8")
            except OSError as exc:
                self._say("err", f"[could not read {path.name}: {exc}]\n")
                continue
            names.append(path.name)
            parts.append(f"--- FILE: {path.name} ({len(code.splitlines())} lines) ---\n"
                         f"```\n{code}\n```")
        self.attachments = []
        return "\n\n".join(parts), f"with {', '.join(names)}" if names else ""

    def send(self) -> None:
        if self.busy:
            return
        if not self.key:
            self.ask_for_key()
            return
        typed = self.entry.get("1.0", "end-1c").strip()
        if not typed:
            return
        message, note = self._compose(typed)
        if len(message) > _BIG_MESSAGE_CHARS and not messagebox.askokcancel(
                "Large message",
                f"This message is {len(message):,} characters "
                f"(~{len(message)//4:,} tokens). Send it?"):
            return

        self.entry.delete("1.0", "end")
        shown = typed + (f"\n\n[{note}]" if note else "")
        self._render("user", shown)
        self.log.append("user", shown, note=note)
        self._set_busy(True)

        # The request runs on a worker thread and reports back through a queue. Tkinter
        # is not thread-safe: touching a widget from the worker would work most of the
        # time and crash the interpreter occasionally, which is the worst of both.
        def work(text=message, key=self.key):
            try:
                reply = self.chat.send(
                    text, key, on_retry=lambda m: self.replies.put(("retry", m)))
                self.replies.put(("reply", reply))
            except GeminiError as exc:
                self.replies.put(("error", str(exc)))
            except Exception as exc:                              # noqa: BLE001
                self.replies.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _drain(self) -> None:
        """Poll the worker's queue on the UI thread — the only safe place to draw."""
        try:
            while True:
                kind, payload = self.replies.get_nowait()
                if kind == "retry":
                    self.status.configure(text=payload, fg=_ERR)
                elif kind == "reply":
                    self._render("model", payload)
                    self.log.append("model", payload, note=self.chat.model)
                    self._set_busy(False)
                else:
                    self._say("err", f"\n⚠ {payload}\n\n")
                    self.log.append("error", payload)
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def on_close(self) -> None:
        if self.busy and not messagebox.askokcancel(
                "Still waiting", "A reply is still on its way. Close anyway?"):
            return
        self.log.close()
        self.root.destroy()


def main() -> int:
    # A missing key is no longer a reason not to start: the window asks for one. That is
    # the whole point of the dialog — the alternative was a stderr line telling someone
    # to go and create a file they have never heard of.
    key, _backend = read_key()
    root = tk.Tk()
    window = ChatWindow(root, key)
    print(f"logging to {window.log.path}")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
