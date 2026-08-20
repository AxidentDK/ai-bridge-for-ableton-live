"""A chat window where Gemini has its hands on Live, not just an opinion about it.

    python tools/gemini_studio.py

THIS IS THE GEMINI FRONT END. The bridge speaks MCP, so a Claude user already has a
window — the Claude desktop app. A Gemini user had nothing, because Gemini reaches the
bridge through function calling rather than MCP. This is that window: paste a key, and
Gemini can work your set.

It sends ``gemini_tools.PRODUCER_PREAMBLE``: you are producing in a session, describe the
sound you want, audition before committing, say so if what came back is wrong. Nothing
about accuracy and nothing about anything being evaluated, because a model told where a
system is weak goes looking there, and then every complaint it makes is one you planted.

WHAT YOU SEE. Tool calls appear as they happen, indented and dimmed, so a long silence has
a visible cause: you can watch it search, fail to audition something, and pivot. The
producer sitting here is the one judging the result, so the interesting part is not the
final paragraph — it is which sound it reached for and how quickly it abandoned one.

Everything runs on a worker thread. ``drive`` blocks for as long as Gemini keeps calling
tools, which can be minutes, and a Tk window that stops repainting for minutes looks
crashed. The worker posts events to a queue; the window drains it.

``live_save_set`` is blocked by ``gemini_tools.make_runner``: nothing here can
write over your set. Gemini is TOLD it was refused, so it can say "I would save here"
rather than silently having no way to.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))

import gemini_client                                                  # noqa: E402
import gemini_tools                                                   # noqa: E402
from gemini_ui import (                                               # noqa: E402
    BG, DIM, ERR, FALLBACK_MODELS, FG, KEY_PATH, MODEL, PANEL, USER, KeyDialog,
)


class StudioWindow:
    def __init__(self, root: tk.Tk, key: str | None, model: str = None):
        self.root = root
        self.key = key
        self.model = model or gemini_client.DEFAULT_MODEL
        self.history: list = []
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.blocked: list = []
        self.log = gemini_client.Transcript(gemini_tools.SESSION_DIR, label="studio",
                                            model=self.model)

        root.title("Gemini — studio")
        root.geometry("1080x820")
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.body = tkfont.Font(family="Segoe UI", size=10)
        mono = tkfont.Font(family="Cascadia Mono", size=9)

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(top, text="Model", bg=BG, fg=DIM, font=self.body).pack(side="left")
        self.model_var = tk.StringVar(value=self.model)
        self.model_box = ttk.Combobox(top, textvariable=self.model_var, width=34,
                                      state="readonly", values=list(FALLBACK_MODELS))
        self.model_box.pack(side="left", padx=(8, 0))
        self.model_box.bind("<<ComboboxSelected>>", self._on_model_change)
        self.tools_label = tk.Label(top, text="", bg=BG, fg=DIM, font=self.body)
        self.tools_label.pack(side="right")

        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=(8, 0))
        self.view = tk.Text(wrap, wrap="word", bg=PANEL, fg=FG, font=self.body,
                            relief="flat", padx=14, pady=12, spacing1=2, spacing3=6,
                            insertbackground=FG, state="disabled")
        bar = ttk.Scrollbar(wrap, command=self.view.yview)
        self.view.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.view.pack(side="left", fill="both", expand=True)

        heading = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.view.tag_configure("who_user", foreground=USER, font=heading,
                                spacing1=14, spacing3=4)
        self.view.tag_configure("who_model", foreground=MODEL, font=heading,
                                spacing1=14, spacing3=4)
        self.view.tag_configure("err", foreground=ERR)
        self.view.tag_configure("dim", foreground=DIM)
        # Tool traffic is monospaced and indented so it reads as machinery rather than as
        # something Gemini said. At a glance you can see the shape of a search-and-pivot.
        self.view.tag_configure("tool", font=mono, foreground=DIM,
                                lmargin1=18, lmargin2=30)
        self.view.tag_configure("toolerr", font=mono, foreground=ERR,
                                lmargin1=18, lmargin2=30)

        lower = tk.Frame(root, bg=BG)
        lower.pack(fill="x", padx=10, pady=(8, 4))
        self.entry = tk.Text(lower, height=5, wrap="word", bg=PANEL, fg=FG,
                             font=self.body, relief="flat", padx=10, pady=8,
                             insertbackground=FG)
        self.entry.pack(side="left", fill="both", expand=True)
        self.entry.focus_set()
        self.entry.bind("<Return>", self._on_return)

        buttons = tk.Frame(lower, bg=BG)
        buttons.pack(side="right", fill="y", padx=(8, 0))
        self.send_button = tk.Button(buttons, text="Send", width=11, relief="flat",
                                     bg="#3574f0", fg="white", activebackground="#4b84f2",
                                     activeforeground="white", font=self.body,
                                     command=self.send)
        self.send_button.pack(fill="x")
        tk.Button(buttons, text="Open log", width=11, relief="flat", bg=PANEL, fg=DIM,
                  activebackground="#3a3d41", activeforeground=FG, font=self.body,
                  command=self.open_log).pack(fill="x", pady=(6, 0))
        tk.Button(buttons, text="New session", width=11, relief="flat", bg=PANEL,
                  fg=DIM, activebackground="#3a3d41", activeforeground=FG,
                  font=self.body, command=self.new_session).pack(fill="x", pady=(6, 0))

        self.status = tk.Label(root, anchor="w", bg=BG, fg=DIM, font=self.body,
                               padx=12, pady=6)
        self.status.pack(fill="x")

        self._build_menu()
        self._count_tools()
        self._warm_sidecar()
        self._say("dim",
                  "Gemini has the bridge's tools here — it can read the set, search the "
                  "library by how something SOUNDS, audition, load and play.\n"
                  "Tool calls appear as they happen. Enter sends · Shift+Enter for a "
                  "newline.\n"
                  "live_save_set is blocked: nothing here can write over your set.\n"
                  f"Logging to {self.log.path.name} as we go.\n")
        self._idle()
        self.root.after(100, self._drain)
        if not self.key:
            self._say("err", "\nNo API key yet — paste one to start.\n")
            self.root.after(400, self.ask_for_key)

    # ---- menu ---------------------------------------------------------------------------

    def _build_menu(self) -> None:
        """The menu bar. Its reason to exist is one item: where you put your API key.

        Without it the key can only be pasted into the dialog that appears when there
        ISN'T one — fine on day one, useless on the day you need to replace an expired
        key or point the app at a different account. A setting you can only reach by
        first breaking something is not a setting.
        """
        menubar = tk.Menu(self.root)

        session = tk.Menu(menubar, tearoff=0)
        session.add_command(label="New session", command=self.new_session)
        session.add_separator()
        session.add_command(label="Open this session's log", command=self.open_log)
        session.add_command(label="Open log folder", command=self.open_log_folder)
        session.add_separator()
        session.add_command(label="Close", command=self.on_close)
        menubar.add_cascade(label="Session", menu=session)

        settings = tk.Menu(menubar, tearoff=0)
        settings.add_command(label="Gemini API key…", command=self.ask_for_key)
        settings.add_command(label="Refresh model list", command=self._load_models_async)
        menubar.add_cascade(label="Settings", menu=settings)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Get a free API key",
                             command=lambda: __import__("webbrowser").open(KEY_URL))
        helpmenu.add_command(label="Where my key is stored",
                             command=lambda: messagebox.showinfo(
                                 "Where your key is stored",
                                 f"{KEY_PATH}\n\nOutside the program's folder, so it "
                                 "cannot be shared or committed by accident. Delete that "
                                 "file to revoke access.", parent=self.root))
        helpmenu.add_command(label="What this window can do",
                             command=lambda: messagebox.showinfo(
                                 "Gemini Studio",
                                 "Gemini is connected to Ableton Live through the AI "
                                 "Bridge and can use every one of its tools: read your "
                                 "set, search your library by how something SOUNDS, "
                                 "audition it, load it, write clips and move controls.\n\n"
                                 "Saving your set is deliberately blocked — nothing here "
                                 "can overwrite your work.", parent=self.root))
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.root.configure(menu=menubar)

    def open_log_folder(self) -> None:
        import webbrowser                                             # noqa: PLC0415
        webbrowser.open(self.log.path.parent.as_uri())

    def _load_models_async(self) -> None:
        """Ask the API which models this key can actually reach, and offer those.

        The dropdown starts from a short fallback list because model ids move; a
        hardcoded list goes stale silently, and the only trustworthy source is the key
        itself.
        """
        if not self.key:
            self.ask_for_key()
            return
        self.status.configure(text="asking which models your key can reach…", fg=DIM)

        def work():
            try:
                names = [name for name, _ in gemini_client.list_models(self.key)]
                self.events.put(("models", {"names": names}))
            except Exception as exc:                                  # noqa: BLE001
                self.events.put(("failed", {"message": f"model list: {exc}"}))

        threading.Thread(target=work, daemon=True).start()

    # ---- small helpers -----------------------------------------------------------------

    def _warm_sidecar(self) -> None:
        """Touch the sound index in the background so the FIRST search is not the slow one.

        Measured: the index is ~480 MB, and the first query after boot spends about 18
        SECONDS having Windows page it off disk. Warm, the same query is 2-3 seconds. That
        cost is unavoidable but it does not have to be paid in the middle of an answer —
        here it happens while the user is still reading the greeting and typing.

        Deliberately silent and deliberately forgiving: the sidecar is optional, and a
        window that complained on startup about a component the user may not have installed
        would be worse than the delay it is avoiding.
        """
        def work():
            try:
                import mcp_server                                     # noqa: PLC0415
                if not mcp_server.run_tool(
                        "live_sidecar_status", {}).get("available"):
                    return
                # A real query, not just opening the file: the pages that matter are the
                # ones a search actually reads.
                mcp_server.run_tool("live_find_sound", {"query": "warm", "limit": 1})
            except Exception:                                         # noqa: BLE001, S110
                pass

        threading.Thread(target=work, daemon=True).start()

    def _count_tools(self) -> None:
        try:
            import mcp_server                                         # noqa: PLC0415
            declarations, _ = gemini_tools.to_declarations(mcp_server.TOOLS)
            self.tools_label.configure(text=f"{len(declarations)} tools · save blocked")
        except Exception as exc:                                      # noqa: BLE001
            self.tools_label.configure(text=f"tools unavailable: {exc}")

    def _say(self, tag: str, text: str) -> None:
        self.view.configure(state="normal")
        self.view.insert("end", text, tag)
        self.view.configure(state="disabled")
        self.view.see("end")

    def _idle(self) -> None:
        self.status.configure(
            text=f"{len(self.history)} turns in context · {self.model}", fg=DIM)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        self.send_button.configure(state="disabled" if busy else "normal",
                                   text="Working…" if busy else "Send")
        if busy:
            self.status.configure(text=message or "thinking…", fg=DIM)
        else:
            self._idle()

    def _on_return(self, event):
        if event.state & 0x0001:                       # Shift held: a newline, not a send
            return None
        self.send()
        return "break"

    def _on_model_change(self, _event=None) -> None:
        self.model = self.model_var.get()
        self._idle()

    def ask_for_key(self) -> None:
        KeyDialog(self.root, self._key_saved, self.body)

    def _key_saved(self, key: str, _models) -> None:
        self.key = key
        self._say("dim", "\nKey accepted.\n")

    def open_log(self) -> None:
        import webbrowser                                             # noqa: PLC0415
        webbrowser.open(self.log.path.as_uri())

    def new_session(self) -> None:
        """Forget the conversation. Does NOT undo anything already done in Live.

        Said plainly on screen, because "new session" in a window that has been editing a
        Live set could reasonably be read as "put it back", and it does not.
        """
        self.history = []
        self._say("dim", "\n— new session: Gemini has forgotten the conversation. "
                         "Whatever it already did in Live is still there.\n")
        self._idle()

    # ---- the work ----------------------------------------------------------------------

    def send(self) -> None:
        if self.busy:
            return
        typed = self.entry.get("1.0", "end").strip()
        if not typed:
            return
        if not self.key:
            self.ask_for_key()
            return
        self.entry.delete("1.0", "end")
        self._say("who_user", "\nYou\n")
        self._say(None, typed + "\n")
        self.log.append("user", typed)
        self._set_busy(True)
        threading.Thread(target=self._work, args=(typed,), daemon=True).start()

    def _work(self, typed: str) -> None:
        """Worker thread: never touches a widget, only the queue."""
        import mcp_server                                             # noqa: PLC0415

        def on_event(kind, fields):
            self.events.put((kind, fields))

        try:
            result = gemini_tools.drive(
                typed, self.key,
                run_tool=gemini_tools.make_runner(
                    mcp_server.run_tool, allow_save=False,
                    on_blocked=lambda n, a: self.blocked.append(n)),
                tools=mcp_server.TOOLS, post=gemini_client.post, model=self.model,
                system=gemini_tools.PRODUCER_PREAMBLE, on_event=on_event,
                history=self.history,
                on_retry=lambda message: self.events.put(("retry", {"message": message})))
        except gemini_client.GeminiError as exc:
            self.events.put(("failed", {"message": str(exc)}))
            return
        except Exception as exc:                                      # noqa: BLE001
            # A bug in the loop must not take the window with it, and must not look like
            # a Gemini failure either.
            self.events.put(("failed", {"message": f"{type(exc).__name__}: {exc}"}))
            return
        self.events.put(("done", {"result": result}))

    def _drain(self) -> None:
        try:
            while True:
                kind, fields = self.events.get_nowait()
                # Tool traffic goes to the LOG as well as the screen. The screen scrolls
                # and the window closes; the interesting part of a session is which sound
                # it reached for and what it abandoned, and that has to survive both.
                if kind == "call":
                    args = json.dumps(fields.get("args") or {}, ensure_ascii=False)
                    line = f"  → {fields['name']}({args[:220]})"
                    self._say("tool", f"\n{line}\n")
                    self.log.append("tool", line)
                elif kind == "result":
                    text = json.dumps(fields.get("result") or {}, ensure_ascii=False)
                    mark = "ok " if fields.get("ok") else "ERR"
                    self._say("toolerr" if not fields.get("ok") else "tool",
                              f"     {mark} {text[:300]}\n")
                    self.log.append("tool-result", f"     {mark} {text[:600]}")
                elif kind == "models":
                    names = fields["names"]
                    self.model_box.configure(values=names)
                    self._say("dim",
                              f"\n{len(names)} models reachable with your key.\n")
                    self._idle()
                elif kind == "retry":
                    self._set_busy(True, fields["message"])
                elif kind == "failed":
                    self._say("err", f"\n⚠ {fields['message']}\n")
                    self.log.append("error", fields["message"])
                    self._set_busy(False)
                elif kind == "done":
                    result = fields["result"]
                    # The history is only adopted on success, so a failed turn leaves the
                    # conversation exactly as it was rather than holding a question with
                    # no answer after it.
                    self.history = result["history"]
                    reply = result["text"] or "(no closing text)"
                    self._say("who_model", "\nGemini\n")
                    self._say(None, reply + "\n")
                    self.log.append("model", reply)
                    failed = sum(1 for s in result["steps"] if not s["ok"])
                    if result["stopped_because"] != "answered":
                        self._say("err", f"\n⚠ stopped: {result['stopped_because']}\n")
                    self._say("dim", f"{len(result['steps'])} tool calls, {failed} failed\n")
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def on_close(self) -> None:
        self.log.close(f"{len(self.history)} turns")
        self.root.destroy()


def main() -> int:
    key, _backend = gemini_client.read_key()
    root = tk.Tk()
    StudioWindow(root, key)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
