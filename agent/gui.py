"""
eLmi 3005 - retro Linux terminal GUI (tkinter, zero compiled deps - SAC safe)

A full-screen retro CRT terminal that drives the eLmi agent. It boots like
an old Unix box: a flickering boot log, then the eLmi logo rendered in bold
ASCII art dead-center, then drops you into a real `eLmi@3005:~$` prompt.

Terminal conventions:
  - phosphor-green body text  (#3CF)
  - amber/gold for the logo + the eLmi model name (OpenClaw flair)
  - monospace throughout (Consolas / Cascadia Mono)
  - plain terminal lines, no chat bubbles
  - a live prompt line (username@host:~$ ) inside the entry

Depends only on:
  - agent.py (the agent itself)
  - tkinter (Python standard library - nothing for SAC to block)

Run from the agent folder:
    python gui.py
or pass flags the same way as the CLI:
    python gui.py --allow-exec --allow-dir "C:/Users/meori/Desktop"
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent  # noqa: E402  (agent.py sits next to this file)

# Forward --allow-exec / --allow-dir so the GUI honours the same gates.
import agent as _a
argv = list(sys.argv[1:])
while argv:
    arg = argv.pop(0)
    if arg == "--allow-exec":
        _a._APPROVED_EXEC = True
    elif arg == "--allow-dir" and argv:
        _a.add_approved_dir(argv.pop(0))


# ---- CRT phosphor palette -------------------------------------------------
BG = "#050805"              # near-black cathode
FG = "#3CF06B"              # phosphor green body text
DIM = "#1E8C3F"             # dim green (brackets, labels)
AMBER = "#FFB000"           # amber/gold (logo, model name, OpenClaw accent)
RED = "#FF5C5C"             # errors / critical
BORDER = "#123F23"          # subtle green border
PROMPT_FG = "#FFB000"
TOOL_FG = "#FFD75E"         # tool traces amber


def _logo_lines():
    """Bold ASCII 'eLmi' logo - wide block letters, rendered centered."""
    return [
        r"  ▄████████  ▄█        ▄███████▄  ███    █▄     ",
        r" ███    ███ ███       ███    ███ ███    ███▄    ",
        r" ███    █▀  ███       ███    ███ ███    ███▀██▄ ",
        r" ███        ███       ███    ███ ███    ███   ██▄",
        r"▀███████████ ███       ███    ███ ███    ███    ██▄",
        r"         ███ ███       ███    ███ ███    ███    ███",
        r"   ▄█    ███ ███▌    ▄ ███    ███ ███    ███▄   ███",
        r" ▄████████▀  ███████▄▄█████████▀  ████████▀ ▄▄███▀",
        r"            ▀                                          ",
    ]


class ELmiApp:
    def __init__(self, root):
        self.root = root
        root.title("eLmi 3005 — retro terminal")
        root.geometry("980x860")
        root.minsize(640, 560)
        root.configure(bg=BG)

        self.q = queue.Queue()
        self.busy = False
        self.history = []
        self.attachments = []
        self._booted = False

        self._fonts()
        self._build()
        self._boot_splash()
        self.root.after(100, self._poll)

    def _fonts(self):
        mono = "Cascadia Mono"
        if "Consolas" in (f[0] for f in tkfont.families()):
            mono = "Consolas"
        self.f_title = tkfont.Font(family=mono, size=30, weight="bold")
        self.f_logo = tkfont.Font(family=mono, size=17, weight="bold")
        self.f_boot = tkfont.Font(family=mono, size=11)
        self.f_body = tkfont.Font(family=mono, size=12)
        self.f_prompt = tkfont.Font(family=mono, size=12, weight="bold")
        self.f_tool = tkfont.Font(family=mono, size=11)

    def _build(self):
        # ---- boot frame (full screen, replaced after splash) ----
        self.boot = tk.Frame(self.root, bg=BG)
        self.boot.pack(fill="both", expand=True)

        self.boot_text = tk.Text(
            self.boot, wrap="word", state="disabled", bg=BG, fg=FG,
            insertbackground=FG, bd=0, highlightthickness=0,
            padx=24, pady=16, font=self.f_boot, cursor="arrow")
        self.boot_text.tag_configure("g", foreground=FG)
        self.boot_text.tag_configure("dim", foreground=DIM)
        self.boot_text.tag_configure("amber", foreground=AMBER)
        self.boot_text.tag_configure("red", foreground=RED)
        self.boot_text.pack(fill="both", expand=True)

        # ---- logo center (hidden, shown during splash) ----
        self.logo_frame = tk.Frame(self.boot, bg=BG)
        self.logo_frame.place(relx=0.5, rely=0.5, anchor="center")
        self.logo_lbl = tk.Label(self.logo_frame, text="", font=self.f_logo,
                                 bg=BG, fg=AMBER, justify="center")
        self.logo_lbl.pack()
        self.logo_sub = tk.Label(self.logo_frame,
                                 text="** MODEL 3005 **",
                                 font=self.f_title, bg=BG, fg=AMBER)
        self.logo_sub.pack(pady=(10, 0))

        # ---- main terminal (hidden until boot ends) ----
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(fill="both", expand=True)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(0, weight=1)  # chat feed takes top
        self.main.grid_rowconfigure(1, weight=0)  # search band is fixed
        self.main.grid_rowconfigure(2, weight=3)  # buffer below search

        # scrolling terminal feed (bounded to the top region only)
        self.chat = scrolledtext.ScrolledText(
            self.main, wrap="word", state="disabled", bg=BG, fg=FG, bd=0,
            insertbackground=FG, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=BORDER,
            padx=20, pady=14, font=self.f_body, selectbackground=AMBER,
            selectforeground=BG, cursor="arrow", spacing3=6)
        self.chat.tag_configure("user_lab", foreground=AMBER,
                                font=self.f_tool, spacing1=6, spacing3=2)
        self.chat.tag_configure("user_body", foreground="#FFD47A",
                                font=self.f_body, lmargin1=16, lmargin2=16,
                                spacing1=1, spacing3=8)
        self.chat.tag_configure("bot_lab", foreground=DIM,
                                font=self.f_tool, spacing1=6, spacing3=2)
        self.chat.tag_configure("bot_body", foreground=FG, font=self.f_body,
                                lmargin1=16, lmargin2=16, spacing1=1,
                                spacing3=8)
        self.chat.tag_configure("tool", foreground=TOOL_FG, font=self.f_tool,
                                lmargin1=16, spacing1=4, spacing3=2)
        self.chat.tag_configure("think", foreground=DIM, font=self.f_tool,
                                lmargin1=16, spacing1=2, spacing3=2)
        self.chat.tag_configure("red", foreground=RED, font=self.f_body)
        self.chat.grid(row=0, column=0, sticky="nsew", padx=16, pady=(14, 4))

        self._term(
            "think", "💬 eLmi 3005 online. Ask anything — it can search the web, "
            "read code, and (with exec) run real terminal commands.\n"
            "  ⌨  exec is " +
            ("ON   ·   " if _a._APPROVED_EXEC
             else "OFF (relaunch with --allow-exec)   ·   ") +
            f"{len(agent.TOOLS)} tools loaded\n")

        # ---- google-style centered search bar + logo (own bounded band) ----
        box = tk.Frame(self.main, bg=BG)
        box.grid(row=1, column=0, sticky="n", pady=8)
        box.grid_columnconfigure(0, weight=1)

        self.mini_logo = tk.Label(box, text="eLmi 3005", font=self.f_title,
                                  bg=BG, fg=AMBER)
        self.mini_logo.grid(row=0, column=0, pady=(2, 12))

        # attachments chips row
        self.att_row = tk.Frame(box, bg=BG)
        self.att_row.grid(row=1, column=0, pady=(0, 6))
        self.att_labels = []

        self.search = tk.Frame(box, bg=BORDER, highlightthickness=2,
                               highlightbackground=BORDER,
                               highlightcolor=AMBER)
        self.search.grid(row=2, column=0, sticky="ew")
        self.search.grid_columnconfigure(1, weight=1)
        self.prompt_lbl = tk.Label(self.search, text="❯",
                                   font=self.f_prompt, bg=BORDER, fg=PROMPT_FG)
        self.prompt_lbl.grid(row=0, column=0, padx=(14, 4), pady=7)
        self.entry = tk.Entry(self.search, font=self.f_body, relief="flat",
                              bg=BORDER, fg=FG, insertbackground=AMBER,
                              highlightthickness=0, bd=0, width=48)
        self.entry.grid(row=0, column=1, sticky="ew", ipady=8, padx=(0, 6))
        self.attach_btn = tk.Button(self.search, text="📎", font=self.f_prompt,
                                    bg=BORDER, fg=AMBER, activebackground=DIM,
                                    activeforeground=BG, relief="flat", bd=0,
                                    width=3, cursor="hand2",
                                    command=self._pick_files)
        self.attach_btn.grid(row=0, column=2, padx=(4, 6))
        self.send_btn = tk.Button(self.search, text="⟶", font=self.f_prompt,
                                  bg=AMBER, fg=BG, activebackground="#E0AF3E",
                                  activeforeground=BG, relief="flat", bd=0,
                                  width=3, cursor="hand2", command=self._send)
        self.send_btn.grid(row=0, column=3, padx=(0, 6))
        hint = ("exec ON" if _a._APPROVED_EXEC else "exec OFF") + f" · {len(agent.TOOLS)} tools"
        tk.Label(box, text=hint, font=self.f_boot, bg=BG, fg=DIM).grid(
            row=3, column=0, pady=(8, 0))
        self.entry.bind("<Return>", lambda e: self._send())
        self.entry.focus_set()

    # ------------------------------------------------------------------ boot
    def _log(self, text, tag="g"):
        self.boot_text.configure(state="normal")
        self.boot_text.insert("end", text, tag)
        self.boot_text.configure(state="disabled")
        self.boot_text.see("end")

    def _boot_splash(self):
        boot_lines = [
            ("eLmi 3005 BIOS v4.2 — © 2026 ELMI SYSTEMS\n", "amber"),
            ("cpu     : minimax-m3:free  @ 1x cryo-core\n", "g"),
            ("mem     : 8 tokens of working memory\n", "g"),
            ("disk    : 20 tools loaded\n", "g"),
            ("cwd     : %s\n" % agent._CWD, "g"),
            ("exec    : %s\n" % ("ON" if _a._APPROVED_EXEC else "OFF"), "g"),
            ("net     : OpenRouter api :: secure channel\n", "dim"),
            ("search  : bing (html)  ::  duckduckgo (blocked)\n", "dim"),
            ("gui     : tkinter / standard library (SAC-clean)\n", "dim"),
            ("\n", "dim"),
        ]
        for i, (line, tag) in enumerate(boot_lines):
            delay = 40 + i * 35
            self.root.after(delay, lambda l=line, t=tag: self._log(l, t))
        self.root.after(40 + len(boot_lines) * 35 + 200, self._show_logo)

    def _animate_logo(self, step=0):
        hues = [AMBER, "#FFCC33", "#FFB000", "#E8960A", AMBER, "#FFB000"]
        self.logo_lbl.configure(fg=hues[step % len(hues)])
        self.logo_sub.configure(fg=hues[(step + 2) % len(hues)])
        if self._booted:
            return
        self.root.after(180, lambda: self._animate_logo(step + 1))

    def _show_logo(self):
        self.boot_text.forget()
        self.logo_lbl.configure(text="\n".join(_logo_lines()))
        self.logo_frame.place(relx=0.5, rely=0.5, anchor="center")
        self.logo_frame.lift()
        # thin cursor block under the logo for the flicker/typing feel
        self._animate_logo()
        self.root.after(2200, self._boot_done)

    def _boot_done(self):
        self._booted = True
        self.logo_frame.place_forget()
        self.boot.forget()
        self.main.pack(fill="both", expand=True)
        self.entry.focus_set()

    # ------------------------------------------------------------------ chat
    def _msg(self, text, tag):
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _term(self, tag, text):
        self._msg(text + "\n", tag)

    def _speak(self, who, body):
        if who == "you":
            self._term("user_lab", "❯ YOU  —  you asked")
            self._msg("  " + body + "\n", "user_body")
        else:
            self._term("bot_lab", "✦ eLmi")
            for line in body.split("\n"):
                self._msg("  " + line + "\n", "bot_body")
            if not body:
                self._term("bot_body", "  (no output)")

    _IMG = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    _TXT = {".txt", ".md", ".py", ".js", ".html", ".json", ".csv", ".log",
            ".ini", ".yaml", ".yml", ".cfg", ".xml", ".css", ".c", ".cpp",
            ".h", ".rs", ".go", ".java", ".ts"}

    def _classify(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in self._IMG:
            return "image"
        if ext in self._TXT:
            return "text"
        return "other"

    def _pick_files(self):
        files = filedialog.askopenfilenames(
            title="Attach files to eLmi",
            filetypes=[("All files", "*.*"),
                       ("Documents", "*.txt *.md *.pdf *.docx *.py *.json *.csv"),
                       ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
        for path in files:
            if any(p.lower() == os.path.abspath(path).lower()
                   for _, p, _ in self.attachments):
                continue
            name = os.path.basename(path)
            kind = self._classify(path)
            self.attachments.append((name, os.path.abspath(path), kind))
        self._render_attachments()

    def _render_attachments(self):
        for lbl in self.att_labels:
            lbl.destroy()
        self.att_labels = []
        if not self.attachments:
            return
        for name, path, kind in self.attachments:
            icon = "🖼" if kind == "image" else ("📄" if kind == "text" else "📎")
            chip = tk.Label(self.att_row, text=f"{icon} {name}", font=self.f_tool,
                            bg=BORDER, fg=FG, padx=8, pady=2, cursor="hand2")
            chip.pack(side="left", padx=(0, 6))
            chip.bind("<Button-1>",
                      lambda e, p=path: self._drop_attachment(p))
            self.att_labels.append(chip)

    def _drop_attachment(self, path):
        self.attachments = [a for a in self.attachments
                            if a[1] != path]
        self._render_attachments()

    def _send(self):
        text = self.entry.get().strip()
        if self.busy:
            return
        if not text and not self.attachments:
            return
        self.entry.delete(0, "end")
        if text in ("help", "--help", "?") and not self.attachments:
            self._term("think", self._help_text())
            return
        labels = "  ".join(f"{n}" for _, _, n in
                           [(a[0], a[1], a[2]) for a in self.attachments]) or ""
        self._speak("you", text + (f"\n  📎 attached: {labels}" if labels else ""))
        self._term("think", "⏳ eLmi is thinking…")
        self.busy = True
        self.entry.configure(state="disabled")
        self.attach_btn.configure(state="disabled")
        self.send_btn.configure(state="disabled")
        payload = list(self.attachments)
        self.attachments = []
        self._render_attachments()
        threading.Thread(target=self._work, args=(text, payload),
                         daemon=True).start()

    def _help_text(self):
        execs = "ON" if _a._APPROVED_EXEC else "OFF"
        return (
            "💬 Available tools: " + str(len(agent.TOOLS)) +
            "   ·   exec " + execs + "\n"
            "  🖥 run_command, run_python, change_directory, get_pwd\n"
            "  🔍 websearch, bing, list_files, read_file, search_code\n"
            "  🧠 remember, recall, get_word_length & more\n"
            "Just ask in plain language — eLmi decides what to call.\n"
        )

    def _work(self, question, attachments=None):
        try:
            answer = agent.ask(question, history=self.history,
                               verbose=False, on_tool=self._tool_hook,
                               attachments=attachments)
        except Exception as exc:
            answer = f"GUI error: {exc}"
        self.q.put(("done", question, answer))

    def _tool_hook(self, name, args, result):
        self.q.put(("tool", name, args))

    def _poll(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "tool":
                    argstr = ", ".join(
                        f"{k}={v!r}" for k, v in list(b.items())[:3])
                    self._term("tool", f"🔧 {a}({argstr[:90]})")
                elif kind == "done":
                    self._finish(a, b)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self, question, answer):
        self.history.append((question, answer))
        self.history = self.history[-12:]
        self.busy = False
        self.entry.configure(state="normal")
        self.attach_btn.configure(state="normal")
        self.send_btn.configure(state="normal")
        self.entry.focus_set()

        # clear the "thinking…" placeholder line, then show the reply
        self.chat.configure(state="normal")
        last_line = self.chat.index("end-1c linestart")
        self.chat.delete(last_line, "end-1c")
        self.chat.configure(state="disabled")
        self._speak("eLmi", answer)

    def on_close(self):
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ELmiApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
