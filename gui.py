"""FabricFinder GUI
Windowed chat interface with a report sidebar.

Launch (Windows, no console window):
    double-click  run_gui.pyw
    – or –
    powershell -ExecutionPolicy Bypass -File .\\run_gui.ps1
"""
from __future__ import annotations

import io
import os
import platform
import queue
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext

# Disable auto-update restart when bot.py is imported as a module.
os.environ.setdefault("FABRICFINDER_NO_AUTOUPDATE", "1")

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from dotenv import load_dotenv
load_dotenv(_HERE / ".env")

from rich.console import Console
import bot
import db
import usage_log

REPORTS_DIR = Path(bot.REPORTS_DIR)
REPORTS_DIR.mkdir(exist_ok=True)

_ANSI = re.compile(r"\x1b\[[0-9;]*[mGKA-Z]?")


# ── thin writer that strips ANSI and feeds a callback ──────────────────────────
class _PlainWriter(io.TextIOBase):
    def __init__(self, cb):
        self._cb = cb

    def write(self, text: str) -> int:
        clean = _ANSI.sub("", text).strip()
        if clean:
            self._cb(clean)
        return len(text)

    def flush(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    # colour palette (Catppuccin-inspired)
    C = dict(
        bg="#f4f4f5",
        toolbar="#11111b",
        sidebar="#1e1e2e",
        side_fg="#cdd6f4",
        side_sel="#313244",
        side_btn="#2a2a3c",
        chat_bg="#ffffff",
        user="#1a7f37",
        bot_fg="#0969da",
        error="#cf222e",
        dim="#57606a",
        tool="#9a6700",
        head="#6f42c1",
        send_bg="#0969da",
        send_fg="#ffffff",
        input_bg="#ffffff",
    )
    CHAT_FONT = ("Segoe UI", 10)
    CODE_FONT = ("Consolas", 9)

    def __init__(self):
        super().__init__()
        self.title("FabricFinder")
        self.geometry("1180x760")
        self.minsize(820, 520)
        self.configure(bg=self.C["bg"])

        self._q: queue.Queue = queue.Queue()
        self._messages: list[dict] = []
        self._busy = False
        self._sidebar_paths: list[Path] = []

        self._build_ui()
        self._redirect_bot_console()
        threading.Thread(target=self._connect_worker, daemon=True).start()
        self.after(50, self._drain)

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self):
        # ── toolbar ──────────────────────────────────────────────────────────
        bar = tk.Frame(self, bg=self.C["toolbar"], height=40)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        tk.Label(
            bar, text="⬡  FabricFinder",
            bg=self.C["toolbar"], fg="#cba6f7",
            font=("Segoe UI Semibold", 13), padx=12,
        ).pack(side=tk.LEFT, pady=8)
        self._ver_lbl = tk.Label(
            bar, text="", bg=self.C["toolbar"], fg="#585b70",
            font=("Segoe UI", 9),
        )
        self._ver_lbl.pack(side=tk.LEFT, pady=8)
        self._status_lbl = tk.Label(
            bar, text="Connecting…", bg=self.C["toolbar"], fg="#f9e2af",
            font=("Segoe UI", 9), padx=14,
        )
        self._status_lbl.pack(side=tk.RIGHT, pady=8)

        # ── paned window (sidebar | chat) ─────────────────────────────────────
        pw = tk.PanedWindow(
            self, orient=tk.HORIZONTAL,
            sashwidth=5, bg="#aaaaaa", sashrelief=tk.FLAT,
        )
        pw.pack(fill=tk.BOTH, expand=True)

        # ── sidebar ───────────────────────────────────────────────────────────
        sb = tk.Frame(pw, bg=self.C["sidebar"], width=260)
        pw.add(sb, minsize=180)

        tk.Label(
            sb, text="REPORTS", bg=self.C["sidebar"], fg="#585b70",
            font=("Segoe UI Semibold", 8), anchor="w", padx=10, pady=8,
        ).pack(fill=tk.X)

        # filter entry
        ff = tk.Frame(sb, bg=self.C["sidebar"])
        ff.pack(fill=tk.X, padx=8, pady=(0, 4))
        self._fvar = tk.StringVar()
        self._fvar.trace_add("write", lambda *_: self._refresh_sidebar())
        fe = tk.Entry(
            ff, textvariable=self._fvar,
            bg="#313244", fg=self.C["side_fg"],
            insertbackground=self.C["side_fg"],
            relief=tk.FLAT, font=("Segoe UI", 9),
        )
        fe.insert(0, "filter…")
        fe.bind(
            "<FocusIn>",
            lambda e: fe.delete(0, tk.END) if fe.get() == "filter…" else None,
        )
        fe.pack(fill=tk.X, ipady=4, padx=2)

        # listbox
        lf = tk.Frame(sb, bg=self.C["sidebar"])
        lf.pack(fill=tk.BOTH, expand=True, padx=4)
        vsb = tk.Scrollbar(lf, orient=tk.VERTICAL)
        self._rlist = tk.Listbox(
            lf,
            bg=self.C["sidebar"], fg=self.C["side_fg"],
            selectbackground=self.C["side_sel"], selectforeground="#ffffff",
            relief=tk.FLAT, bd=0, highlightthickness=0,
            font=("Segoe UI", 8), activestyle="none",
            yscrollcommand=vsb.set, cursor="hand2",
        )
        vsb.config(command=self._rlist.yview)
        self._rlist.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._rlist.bind("<Double-Button-1>", self._open_selected)
        self._rlist.bind("<Return>", self._open_selected)

        # sidebar buttons
        bf = tk.Frame(sb, bg=self.C["sidebar"])
        bf.pack(fill=tk.X, padx=8, pady=6)
        for label, cmd in [
            ("📂  Open Reports Folder", self._open_folder),
            ("↺   Refresh List",        self._refresh_sidebar),
        ]:
            tk.Button(
                bf, text=label, bg=self.C["side_btn"], fg=self.C["side_fg"],
                relief=tk.FLAT, font=("Segoe UI", 8), cursor="hand2",
                activebackground="#3a3a54", activeforeground="#ffffff",
                command=cmd,
            ).pack(fill=tk.X, pady=2, ipady=5)

        # ── chat panel ────────────────────────────────────────────────────────
        cf = tk.Frame(pw, bg=self.C["chat_bg"])
        pw.add(cf, minsize=440)
        self._chat = scrolledtext.ScrolledText(
            cf,
            bg=self.C["chat_bg"], fg="#24292f",
            font=self.CHAT_FONT, wrap=tk.WORD,
            relief=tk.FLAT, padx=18, pady=14,
            state=tk.DISABLED, cursor="arrow",
        )
        self._chat.pack(fill=tk.BOTH, expand=True)
        self._setup_tags()

        # ── input bar ─────────────────────────────────────────────────────────
        inp = tk.Frame(self, bg="#e1e4e8", pady=8, padx=12)
        inp.pack(side=tk.BOTTOM, fill=tk.X)
        self._entry = tk.Text(
            inp,
            bg=self.C["input_bg"], fg="#24292f",
            font=self.CHAT_FONT, relief=tk.SOLID, bd=1,
            height=2, wrap=tk.WORD,
        )
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4, padx=(0, 8))
        self._entry.bind("<Return>", self._on_enter)
        self._entry.bind("<Shift-Return>", lambda e: None)  # allow newlines
        self._send_btn = tk.Button(
            inp, text="Send ↵",
            bg=self.C["send_bg"], fg=self.C["send_fg"],
            font=("Segoe UI Semibold", 10),
            relief=tk.FLAT, padx=18,
            cursor="hand2", state=tk.DISABLED,
            activebackground="#0550ae", activeforeground="#ffffff",
            command=self._submit,
        )
        self._send_btn.pack(side=tk.RIGHT, ipady=8)

        self._refresh_sidebar()

    def _setup_tags(self):
        c = self._chat
        c.tag_configure("user_lbl",
            foreground=self.C["user"], font=("Segoe UI Semibold", 10))
        c.tag_configure("user",
            foreground="#24292f", font=self.CHAT_FONT, lmargin1=34, lmargin2=34)
        c.tag_configure("bot_lbl",
            foreground=self.C["bot_fg"], font=("Segoe UI Semibold", 10))
        c.tag_configure("bot",
            foreground="#24292f", font=self.CHAT_FONT, lmargin1=34, lmargin2=34)
        c.tag_configure("head",
            foreground=self.C["head"], font=("Segoe UI Semibold", 12),
            lmargin1=34, spacing1=6, spacing3=2)
        c.tag_configure("bold",
            font=("Segoe UI Semibold", 10))
        c.tag_configure("code",
            font=self.CODE_FONT, background="#f0f0f0",
            foreground="#005cc5", lmargin1=42, lmargin2=42)
        c.tag_configure("table",
            font=self.CODE_FONT, background="#f8f8f8",
            foreground="#24292f", lmargin1=42, lmargin2=42)
        c.tag_configure("bullet",
            foreground="#24292f", font=self.CHAT_FONT, lmargin1=42, lmargin2=52)
        c.tag_configure("dim",
            foreground=self.C["dim"], font=("Segoe UI", 9), lmargin1=34)
        c.tag_configure("tool",
            foreground=self.C["tool"], font=("Segoe UI", 9), lmargin1=34)
        c.tag_configure("error",
            foreground=self.C["error"], font=("Segoe UI", 10), lmargin1=34)

    # ── bot connection ────────────────────────────────────────────────────────
    def _redirect_bot_console(self):
        """Route bot.py's Rich console output into the message queue."""
        writer = _PlainWriter(lambda s: self._q.put(("tool", s)))
        bot.console = Console(
            file=writer, no_color=True,
            highlight=False, markup=False, soft_wrap=True,
        )

    def _connect_worker(self):
        """Load schema + build system prompt in a background thread."""
        try:
            bundle = db.get_schema_bundle()
            sys_msg = bot.SYSTEM_PROMPT.format(
                architecture=bundle["architecture"],
                schema=bundle["schema"],
                memory=bot.memory_block(),
                today=datetime.now().strftime("%Y-%m-%d"),
            )
            self._messages = [{"role": "system", "content": sys_msg}]
            self._q.put(("ok", bot.APP_VERSION))
        except Exception as exc:
            self._q.put(("err", str(exc)))

    # ── chat rendering ────────────────────────────────────────────────────────
    def _put(self, text: str, tag: str):
        self._chat.configure(state=tk.NORMAL)
        self._chat.insert(tk.END, text, tag)
        self._chat.configure(state=tk.DISABLED)
        self._chat.see(tk.END)

    def _render_reply(self, md: str):
        """Render a bot Markdown reply with basic syntax highlighting."""
        self._put("bot  ", "bot_lbl")
        in_code_block = False
        for line in md.splitlines():
            if line.startswith("```"):
                in_code_block = not in_code_block
                self._put(line + "\n", "code")
                continue
            if in_code_block:
                self._put(line + "\n", "code")
                continue
            if re.match(r"^#{1,3}\s", line):
                self._put(re.sub(r"^#{1,3}\s+", "", line) + "\n", "head")
                continue
            if line.strip().startswith("|"):
                self._put(line + "\n", "table")
                continue
            if re.match(r"^\s*[-*]\s", line):
                parts = re.split(r"(\*\*[^*]+\*\*)", line)
                for p in parts:
                    if p.startswith("**") and p.endswith("**"):
                        self._put(p[2:-2], "bold")
                    else:
                        self._put(p, "bullet")
                self._put("\n", "bullet")
                continue
            # normal line — split on **bold**
            parts = re.split(r"(\*\*[^*]+\*\*)", line)
            for p in parts:
                if p.startswith("**") and p.endswith("**"):
                    self._put(p[2:-2], "bold")
                else:
                    self._put(p, "bot")
            self._put("\n", "bot")
        self._put("\n", "bot")

    # ── queue drain (polled 20×/s) ────────────────────────────────────────────
    def _drain(self):
        try:
            while True:
                kind, data = self._q.get_nowait()
                if kind == "ok":
                    self._ver_lbl.configure(text=f"v{data}")
                    self._set_status("● Connected", "#a6e3a1")
                    self._send_btn.configure(state=tk.NORMAL)
                    self._put(
                        "Connected to HelixMCEDU – ask away!\n"
                        "Commands: /chart <prompt>  ·  /tenant <name>  ·  exit\n\n",
                        "dim",
                    )
                    self._entry.focus_set()
                elif kind == "err":
                    self._set_status("✗ Connection failed", self.C["error"])
                    self._put(f"Connection failed:\n{data}\n\n", "error")
                elif kind == "tool":
                    self._put(f"  {data}\n", "tool")
                elif kind == "reply":
                    reply, error = data
                    if error:
                        self._put(f"Error: {reply}\n\n", "error")
                    else:
                        self._render_reply(reply)
                    self._set_busy(False)
                    self._refresh_sidebar()
        except queue.Empty:
            pass
        self.after(50, self._drain)

    # ── user input ────────────────────────────────────────────────────────────
    def _on_enter(self, event):
        if event.state & 0x1:   # Shift held → insert newline
            return
        self._submit()
        return "break"

    def _submit(self):
        if self._busy:
            return
        q = self._entry.get("1.0", tk.END).strip()
        self._entry.delete("1.0", tk.END)
        if not q:
            return
        if q.lower() in {"exit", "quit"}:
            self.destroy()
            return
        if q.lower() in {"/version", "/v"}:
            self._put(f"\n  FabricFinder version: {bot.APP_VERSION}\n\n", "dim")
            return

        self._put("you  ", "user_lbl")
        self._put(q + "\n\n", "user")
        self._set_busy(True)
        self._put("  thinking…\n", "dim")
        threading.Thread(target=self._answer_worker, args=(q,), daemon=True).start()

    def _answer_worker(self, q: str):
        low = q.lower()
        if low.startswith("/chart"):
            content = (
                f"[CHART REQUEST] Build a chart for: {q[len('/chart'):].strip()}\n"
                "Honor the chart type if named; otherwise pick best fit. "
                "Gather data with run_sql (bucket distributions in SQL), "
                "call create_chart, then save_report embedding the image."
            )
        elif low.startswith("/tenant"):
            name = q[len("/tenant"):].strip()
            if not name:
                self._q.put(("reply", ("Usage: /tenant <tenant name>", None)))
                return
            content = _tenant_prompt(name)
        else:
            content = q

        self._messages.append({"role": "user", "content": content})
        state: dict = {}
        error = None
        try:
            reply = bot.answer(self._messages, state)
        except Exception as exc:
            error = repr(exc)
            reply = str(exc)

        u = state.get("usage", {})
        usage_log.log_turn(
            question=q,
            reply=reply,
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
            llm_calls=u.get("llm_calls", 0),
            model=bot.DEPLOYMENT,
            version=bot.APP_VERSION,
            report_path=state.get("report_path"),
            chart_path=state.get("chart_path"),
            error=error,
        )
        bot.append_memory(q, reply, state.get("report_path"))
        self._q.put(("reply", (reply, error)))

    # ── sidebar ───────────────────────────────────────────────────────────────
    def _refresh_sidebar(self):
        filt = self._fvar.get().lower()
        if filt == "filter…":
            filt = ""
        self._rlist.delete(0, tk.END)
        self._sidebar_paths = []
        exts = {".md", ".csv", ".pdf", ".png"}
        try:
            files = sorted(
                (f for f in REPORTS_DIR.iterdir() if f.suffix in exts),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        icons = {"md": "📄 ", "csv": "📊 ", "pdf": "📕 ", "png": "🖼 "}
        for f in files:
            if filt and filt not in f.name.lower():
                continue
            icon = icons.get(f.suffix.lstrip("."), "📎 ")
            self._rlist.insert(tk.END, icon + f.name)
            self._sidebar_paths.append(f)

    def _open_selected(self, _event=None):
        sel = self._rlist.curselection()
        if sel:
            _open_file(self._sidebar_paths[sel[0]])

    def _open_folder(self):
        _open_file(REPORTS_DIR)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool):
        self._busy = busy
        self._send_btn.configure(
            state=tk.DISABLED if busy else tk.NORMAL,
            text="Working…" if busy else "Send ↵",
        )
        self._entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        if not busy:
            self._entry.focus_set()
        self._set_status(
            "⟳ Thinking…" if busy else "● Connected",
            "#f9e2af" if busy else "#a6e3a1",
        )

    def _set_status(self, text: str, color: str):
        self._status_lbl.configure(text=text, fg=color)


# ── module-level helpers ──────────────────────────────────────────────────────
def _open_file(path: Path):
    try:
        if platform.system() == "Windows":
            os.startfile(str(path))
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        messagebox.showerror("Cannot open", str(exc))


def _tenant_prompt(name: str) -> str:
    return (
        f"[TENANT REPORT REQUEST] Tenant: {name!r}.\n"
        "Produce a detailed, FACT-ONLY PDF report. Steps:\n"
        "1. Resolve the tenant. Query tenant_mapping with a case-insensitive LIKE "
        "on the name. If MORE THAN ONE distinct tenant matches, STOP and list them "
        "(name, country, tenant_id) — do NOT call save_tenant_pdf yet.\n"
        "2. Once a single tenant is identified, call `lookup_tenant_contacts` with "
        "the tenant_id to fetch AM/AE/vertical/renewal/etc. from LXP. Then gather "
        "via run_sql: full MAU+NUA history oldest-first, last-full-month MAU+NUA, "
        "YoY single-month comparison, trailing-6-month YoY comparison, top-10 "
        "content by sessions (resolve IDs with name_worlds), device breakdown if "
        "available, months active, peak MAU + month, peak NUA + month.\n"
        "3. Classify as HEALTHY/INTENSIVE/PIPELINE/DECLINE using trailing-6m YoY "
        "(preferred) with single-month YoY as tiebreaker. Cite specific % changes.\n"
        "4. Call save_tenant_pdf ONCE with all gathered fields.\n"
        "5. Reply with a brief summary paragraph and the saved PDF path."
    )


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    App().mainloop()
