#!/usr/bin/env python3
# ecl_ide.py —— ECL IDE v5.4
# 移除悬停小标；错误解析适配 [行 N, 列 M] 格式

import os
import re
import sys
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font

# ---------------- 配置 ----------------
ECL_MAIN = None
CONSOLE_ENCODING = "utf-8"
SHELL_CMD = ["cmd", "/k", "chcp", "65001",
             ">nul", "&", "powershell", "-NoLogo"]
# PowerShell 7 用户: SHELL_CMD = ["cmd", "/k", "chcp", "65001", ">nul", "&", "pwsh", "-NoLogo"]

KEYWORDS = {
    "def", "end", "if", "elif", "else", "while", "for", "in",
    "return", "break", "continue", "let", "var", "struct", "import",
    "dll_import", "dll_export", "true", "false", "none", "and", "or", "not",
}
THEME = {
    "bg": "#1e1e1e", "fg": "#d4d4d4", "keyword": "#569cd6", "builtin": "#4ec9b0",
    "string": "#ce9178", "comment": "#6a9955", "number": "#b5cea8",
    "decorator": "#c586c0", "cursor": "#aeafad", "error": "#f48771",
    "errline": "#5a2b2b", "gutter": "#252526", "gutterfg": "#858585",
    "search": "#3a3d1f",
}
ECL_KEYWORD_RE = re.compile(r"\b(?:%s)\b" % "|".join(sorted(KEYWORDS)))
ECL_BUILTIN_RE = re.compile(
    r"\b(?:print|len|input|int|str|float|list|dict|range|free|dump|load|py_py)\b")
ECL_STRING_RE = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'')
ECL_COMMENT_RE = re.compile(r"#[^\n]*")
ECL_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
ECL_DECOR_RE = re.compile(r"@@\w+")
# 错误格式1: xxx.ecl:23
ERR_RE_FILE = re.compile(r"([^\s:]+\.ecl):(\d+)")
# 错误格式2: [行 23, 列 1]  —— 当前文件
ERR_RE_LINE = re.compile(r"\[\s*行\s+(\d+)\s*,\s*列\s+(\d+)\s*\]")

SHOW_LINE_NUMBERS = True


def find_ecl_main():
    if ECL_MAIN and os.path.isfile(ECL_MAIN):
        return ECL_MAIN
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.dirname(here)):
        p = os.path.join(d, "ecl.py")
        if os.path.isfile(p):
            return p
    return None


def child_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


# ---------------- 后台进程 ----------------

class BGProc:
    def __init__(self, argv, cwd=None, env=None):
        self.proc = subprocess.Popen(
            argv, cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding=CONSOLE_ENCODING, errors="replace",
        )
        self.q = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in iter(self.proc.stdout.readline, ""):
                self.q.put(line)
        except Exception:
            pass
        self.q.put(None)

    def send(self, line):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            return True
        except Exception:
            return False

    def alive(self):
        return self.proc.poll() is None

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass

    @property
    def returncode(self):
        return self.proc.returncode


# ---------------- 编辑器 ----------------

class Editor:
    def __init__(self, master, app):
        self.app = app
        self.frame = tk.Frame(master, bg=THEME["bg"])

        mono = font.Font(family="Consolas", size=12)
        self.gutter = tk.Text(self.frame, width=5, padx=4, pady=2, bd=0,
                              bg=THEME["gutter"], fg=THEME["gutterfg"],
                              font=mono, state="disabled", takefocus=0,
                              wrap="none", cursor="arrow")
        self.gutter.tag_config("num", justify="right")
        self.text = tk.Text(self.frame, bd=0, padx=6, pady=2,
                            bg=THEME["bg"], fg=THEME["fg"],
                            insertbackground=THEME["cursor"],
                            undo=True, wrap="none", font=mono)
        self.sb = ttk.Scrollbar(
            self.frame, orient="vertical", command=self._yscroll)

        self.text.config(yscrollcommand=self._sync_from_text)
        self.gutter.config(yscrollcommand=self._sync_from_gutter)

        t = self.text
        for tag, key in [("kw", "keyword"), ("bi", "builtin"), ("str", "string"),
                         ("com", "comment"), ("num", "number"), ("dec", "decorator")]:
            t.tag_config(tag, foreground=THEME[key])
        t.tag_config("sel", background="#264f78")
        t.tag_config("errline", background=THEME["errline"])
        t.tag_config("found", background=THEME["search"])

        t.bind("<KeyRelease>", self._on_key)
        t.bind("<ButtonRelease-1>", lambda e: self.highlight())
        t.bind("<Tab>", self._tab)
        t.bind("<Return>", self._auto_indent)
        t.bind("<F5>", lambda e: self.app.run())
        t.bind("<<Modified>>", self._on_modified)

        self._layout()

    def _yscroll(self, *args):
        self.text.yview(*args)
        self.gutter.yview(*args)

    def _sync_from_text(self, first, last):
        self.sb.set(first, last)
        self.gutter.yview_moveto(float(first))

    def _sync_from_gutter(self, first, last):
        self.sb.set(first, last)
        self.text.yview_moveto(float(first))

    def _layout(self):
        self.gutter.pack_forget()
        self.text.pack_forget()
        self.sb.pack_forget()
        if self.app.show_line_numbers:
            self.gutter.pack(side="left", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        self.update_gutter()

    def _on_modified(self, event):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.app.mark_dirty(True)
            self.update_gutter()

    def _on_key(self, event):
        self.highlight()
        self.update_gutter()

    def _tab(self, event):
        self.text.insert("insert", "    ")
        return "break"

    def _auto_indent(self, event):
        line = self.text.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", line).group(0)
        extra = "    " if line.rstrip().endswith(":") else ""
        self.text.insert("insert", "\n" + indent + extra)
        return "break"

    def update_gutter(self):
        if not self.app.show_line_numbers:
            return
        total = int(self.text.index("end-1c").split(".")[0])
        self.gutter.config(state="normal")
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", "\n".join(str(i)
                           for i in range(1, total + 1)), "num")
        self.gutter.config(state="disabled")
        first = self.text.yview()[0]
        self.gutter.yview_moveto(first)

    def highlight(self):
        t = self.text
        text = t.get("1.0", "end-1c")
        for tag in ("kw", "bi", "str", "com", "num", "dec"):
            t.tag_remove(tag, "1.0", "end")
        protected = []
        for pat, tag in [(ECL_COMMENT_RE, "com"), (ECL_STRING_RE, "str")]:
            for m in pat.finditer(text):
                t.tag_add(tag, f"1.0+{m.start()}c", f"1.0+{m.end()}c")
                protected.append((m.start(), m.end()))

        def ok(i):
            return not any(a <= i < b for a, b in protected)

        for pat, tag in [(ECL_KEYWORD_RE, "kw"), (ECL_BUILTIN_RE, "bi"),
                         (ECL_NUMBER_RE, "num"), (ECL_DECOR_RE, "dec")]:
            for m in pat.finditer(text):
                if ok(m.start()):
                    t.tag_add(tag, f"1.0+{m.start()}c", f"1.0+{m.end()}c")

    def find_all(self, needle):
        t = self.text
        t.tag_remove("found", "1.0", "end")
        if not needle:
            return []
        hits, pos = [], "1.0"
        while True:
            pos = t.search(needle, pos, stopindex="end")
            if not pos:
                break
            end = f"{pos}+{len(needle)}c"
            t.tag_add("found", pos, end)
            hits.append((pos, end))
            pos = end
        return hits

    def goto_line(self, lineno):
        self.text.mark_set("insert", f"{lineno}.0")
        self.text.see(f"{lineno}.0")
        self.text.focus_set()

    def mark_error_line(self, lineno):
        self.text.tag_remove("errline", "1.0", "end")
        if lineno:
            self.text.tag_add("errline", f"{lineno}.0", f"{lineno}.end+1c")
            self.text.see(f"{lineno}.0")

    def clear_error(self):
        self.text.tag_remove("errline", "1.0", "end")

    def get(self, *a): return self.text.get(*a)
    def insert(self, *a): return self.text.insert(*a)
    def delete(self, *a): return self.text.delete(*a)


# ---------------- 标签页 ----------------

class Tab:
    def __init__(self, app, path=None):
        self.app = app
        self.path = path
        self.dirty = False
        self.proc = None
        self.proc_kind = None
        self.frame = ttk.Frame(app.notebook)
        self.editor = Editor(self.frame, app)
        self.editor.frame.pack(fill="both", expand=True)
        if path:
            with open(path, "r", encoding="utf-8") as f:
                self.editor.insert("1.0", f.read())
            self.editor.text.edit_modified(False)
        self.editor.highlight()
        self.editor.update_gutter()

    @property
    def title(self):
        name = os.path.basename(self.path) if self.path else "未命名"
        return ("*" + name) if self.dirty else name


# ---------------- 主窗口 ----------------

class App(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.title("ECL IDE")
        self.geometry("1020x700")
        self.tabs = []
        self.show_line_numbers = SHOW_LINE_NUMBERS

        # ===== 菜单 =====
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="新建        Ctrl+N", command=self.new_tab)
        m_file.add_command(label="打开…       Ctrl+O", command=self.open_file)
        m_file.add_separator()
        m_file.add_command(label="保存        Ctrl+S", command=self.save_current)
        m_file.add_separator()
        m_file.add_command(label="关闭标签页  Ctrl+W",
                           command=self.close_current_tab)
        m_file.add_command(label="退出", command=self.on_quit)
        menubar.add_cascade(label="文件", menu=m_file)

        m_edit = tk.Menu(menubar, tearoff=0)
        m_edit.add_command(label="查找…       Ctrl+F", command=self.open_find)
        m_edit.add_command(label="跳转到行…   Ctrl+G", command=self.open_goto)
        m_edit.add_separator()
        self.linenum_var = tk.BooleanVar(value=self.show_line_numbers)
        m_edit.add_checkbutton(label="显示行号", variable=self.linenum_var,
                               command=self.toggle_line_numbers)
        menubar.add_cascade(label="编辑", menu=m_edit)

        m_run = tk.Menu(menubar, tearoff=0)
        m_run.add_command(label="运行        F5", command=self.run)
        m_run.add_command(label="编译 exe（后台）", command=self.compile_exe)
        menubar.add_cascade(label="运行", menu=m_run)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="快捷键说明", command=self.show_help)
        menubar.add_cascade(label="帮助", menu=m_help)

        # ===== 工具栏 =====
        bar = tk.Frame(self, bg="#333")
        bar.pack(fill="x")
        for text, cmd in [("新建", self.new_tab), ("打开", self.open_file),
                          ("保存", self.save_current), ("查找", self.open_find),
                          ("▶ 运行 (F5)", self.run), ("⚒ 编译 exe", self.compile_exe)]:
            tk.Button(bar, text=text, command=cmd, relief="flat",
                      bg="#444", fg="white", padx=10).pack(side="left", padx=4, pady=4)

        # ===== 编辑标签页 =====
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>",
                           lambda e: self.refresh_title())

        self.tab_menu = tk.Menu(self, tearoff=0)
        self.tab_menu.add_command(
            label="关闭标签页", command=self.close_current_tab)
        self.tab_menu.add_command(label="关闭其他", command=self.close_other_tabs)
        self.notebook.bind("<Button-3>", self._show_tab_menu)
        self.notebook.bind("<Button-2>", self._middle_close)

        # ===== 命令条 =====
        self.cmdbar = tk.Frame(self, bg="#333")
        self.cmd_label = tk.Label(
            self.cmdbar, text=" 跳转到行:", bg="#333", fg="#ffd866")
        self.cmd_var = tk.StringVar()
        self.cmd_entry = tk.Entry(self.cmdbar, textvariable=self.cmd_var,
                                  bg="#252526", fg="#d4d4d4",
                                  insertbackground="#d4d4d4", relief="flat")
        self.cmd_label.pack(side="left")
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=4, pady=2)
        self.cmd_entry.bind("<Return>", self._cmd_submit)
        self.cmd_entry.bind("<Escape>", lambda e: self.close_cmdbar())
        self.cmd_mode = None

        self.find_bar = None

        # ===== 底部 =====
        bottom = tk.Frame(self, bg="#333")
        bottom.pack(fill="x")
        self.bottom_pane = ttk.Notebook(bottom)
        self.bottom_pane.pack(fill="both", expand=True)

        out_page = tk.Frame(self.bottom_pane)
        self.output = self._make_text(out_page)
        self.output.pack(fill="both", expand=True)
        in_row = tk.Frame(out_page, bg="#333")
        in_row.pack(fill="x")
        tk.Label(in_row, text="输入>", bg="#333",
                 fg="#aaa").pack(side="left", padx=4)
        self.entry = tk.Entry(in_row, bg="#252526", fg="#d4d4d4",
                              insertbackground="#d4d4d4", relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, padx=4, pady=2)
        self.entry.bind("<Return>", self.send_input)
        self.bottom_pane.add(out_page, text=" 输出 ")

        ps_page = tk.Frame(self.bottom_pane)
        self.ps_out = self._make_text(ps_page)
        self.ps_out.pack(fill="both", expand=True)
        ps_in = tk.Frame(ps_page, bg="#333")
        ps_in.pack(fill="x")
        tk.Label(ps_in, text="PS>", bg="#333",
                 fg="#4ec9b0").pack(side="left", padx=4)
        self.ps_entry = tk.Entry(ps_in, bg="#252526", fg="#d4d4d4",
                                 insertbackground="#d4d4d4", relief="flat")
        self.ps_entry.pack(side="left", fill="x", expand=True, padx=4, pady=2)
        self.ps_entry.bind("<Return>", self.ps_send)
        self.ps_entry.bind("<Up>", self._ps_history_up)
        self.ps_entry.bind("<Down>", self._ps_history_down)
        self.bottom_pane.add(ps_page, text=" PowerShell ")
        self.ps_hist, self.ps_hist_idx = [], -1

        self.status = tk.Label(
            self, text="就绪", anchor="w", bg="#333", fg="#aaa")
        self.status.pack(fill="x")

        # ===== 快捷键 =====
        self.bind_all("<Control-s>", lambda e: self.save_current())
        self.bind_all("<Control-n>", lambda e: self.new_tab())
        self.bind_all("<Control-o>", lambda e: self.open_file())
        self.bind_all("<Control-w>", lambda e: self.close_current_tab())
        self.bind_all("<Control-f>", lambda e: self.open_find())
        self.bind_all("<Control-g>", lambda e: self.open_goto())
        self.bind_all("<Escape>", lambda e: (
            self.close_find(), self.close_cmdbar()))
        self.bind_all("<F6>", lambda e: self.focus_ps())

        self.protocol("WM_DELETE_WINDOW", self.on_quit)

        if path and os.path.isfile(path):
            self.open_path(path)
        else:
            self.new_tab(welcome=True)

    def _make_text(self, parent):
        t = tk.Text(parent, bg=THEME["bg"], fg=THEME["fg"], state="disabled",
                    font=font.Font(family="Consolas", size=11), wrap="word", height=10)
        t.tag_config("err", foreground=THEME["error"])
        t.tag_config("dim", foreground="#888888")
        return t

    # ---------- PowerShell ----------
    def start_ps(self):
        if getattr(self, "ps", None) and self.ps.alive():
            return
        try:
            cwd = None
            tab = self.cur()
            if tab and tab.path:
                cwd = os.path.dirname(tab.path)
            self.ps = BGProc(SHELL_CMD, cwd=cwd)
            self.after(50, self.ps_pump)
        except OSError as e:
            self.ps_write(f"[启动 PowerShell 失败: {e}]\n", err=True)

    def focus_ps(self):
        self.start_ps()
        self.bottom_pane.select(1)
        self.ps_entry.focus_set()

    def ps_pump(self):
        if not getattr(self, "ps", None):
            return
        try:
            while True:
                item = self.ps.q.get_nowait()
                if item is None:
                    self.ps_write("[PowerShell 已退出]\n", dim=True)
                    self.ps = None
                    return
                self.ps_write(item)
        except queue.Empty:
            pass
        self.after(50, self.ps_pump)

    def ps_write(self, s, err=False, dim=False):
        self.ps_out.config(state="normal")
        self.ps_out.insert(
            "end", s, "err" if err else ("dim" if dim else None))
        self.ps_out.see("end")
        self.ps_out.config(state="disabled")

    def ps_send(self, event):
        self.start_ps()
        if not getattr(self, "ps", None):
            return
        cmd = self.ps_entry.get()
        self.ps_entry.delete(0, "end")
        if not cmd:
            return
        self.ps_hist.append(cmd)
        self.ps_hist_idx = len(self.ps_hist)
        self.ps_write(cmd + "\n", dim=True)
        self.ps.send(cmd)
        self.bottom_pane.select(1)

    def _ps_history_up(self, e):
        if self.ps_hist and self.ps_hist_idx > 0:
            self.ps_hist_idx -= 1
            self.ps_entry.delete(0, "end")
            self.ps_entry.insert(0, self.ps_hist[self.ps_hist_idx])
        return "break"

    def _ps_history_down(self, e):
        if self.ps_hist and self.ps_hist_idx < len(self.ps_hist) - 1:
            self.ps_hist_idx += 1
            self.ps_entry.delete(0, "end")
            self.ps_entry.insert(0, self.ps_hist[self.ps_hist_idx])
        else:
            self.ps_hist_idx = len(self.ps_hist)
            self.ps_entry.delete(0, "end")
        return "break"

    # ---------- 命令条 ----------
    def open_goto(self):
        self.cmd_mode = "goto"
        self.cmd_label.config(text=" 跳转到行:")
        self.cmdbar.pack(fill="x", before=self.bottom_pane.master)
        self.cmd_entry.delete(0, "end")
        self.cmd_entry.focus_set()

    def close_cmdbar(self):
        self.cmdbar.pack_forget()

    def _cmd_submit(self, event):
        val = self.cmd_var.get().strip()
        if self.cmd_mode == "goto" and val:
            tab = self.cur()
            if tab:
                try:
                    ln = int(val)
                    total = int(tab.editor.text.index("end-1c").split(".")[0])
                    if 1 <= ln <= total:
                        tab.editor.goto_line(ln)
                        self.close_cmdbar()
                    else:
                        self.set_status(f"行号超出范围（1–{total}）")
                except ValueError:
                    self.set_status("请输入数字行号")

    # ---------- 行号开关 ----------
    def toggle_line_numbers(self):
        self.show_line_numbers = self.linenum_var.get()
        for tab in self.tabs:
            tab.editor._layout()

    # ---------- 查找 ----------
    def open_find(self):
        if not self.find_bar:
            self.find_bar = tk.Frame(self, bg="#333")
            self.find_var = tk.StringVar()
            self.replace_var = tk.StringVar()
            tk.Label(self.find_bar, text=" 查找:", bg="#333",
                     fg="#aaa").pack(side="left")
            self.find_entry = tk.Entry(self.find_bar, textvariable=self.find_var,
                                       bg="#252526", fg="#d4d4d4",
                                       insertbackground="#d4d4d4", relief="flat")
            self.find_entry.bind("<Return>", lambda e: self.do_find())
            tk.Button(self.find_bar, text="查找", command=self.do_find,
                      relief="flat", bg="#444", fg="white").pack(side="left", padx=2)
            tk.Label(self.find_bar, text=" 替换为:",
                     bg="#333", fg="#aaa").pack(side="left")
            e2 = tk.Entry(self.find_bar, textvariable=self.replace_var,
                          bg="#252526", fg="#d4d4d4",
                          insertbackground="#d4d4d4", relief="flat")
            e2.bind("<Return>", lambda e: self.do_replace_all())
            tk.Button(self.find_bar, text="全部替换", command=self.do_replace_all,
                      relief="flat", bg="#444", fg="white").pack(side="left", padx=2)
            tk.Button(self.find_bar, text="✕", command=self.close_find,
                      relief="flat", bg="#444", fg="white").pack(side="left", padx=8)
            self.find_entry.pack(side="left", fill="x", expand=True, padx=2)
            e2.pack(side="left", fill="x", expand=True, padx=2)

        self.find_bar.pack(fill="x", before=self.bottom_pane.master)
        tab = self.cur()
        if tab:
            try:
                sel = tab.editor.get("sel.first", "sel.last")
                if sel:
                    self.find_var.set(sel)
            except tk.TclError:
                pass
        self.find_entry.focus_set()
        self.find_entry.select_range(0, "end")

    def close_find(self):
        if self.find_bar:
            self.find_bar.pack_forget()
            tab = self.cur()
            if tab:
                tab.editor.text.tag_remove("found", "1.0", "end")
                tab.editor.text.focus_set()

    def do_find(self):
        tab = self.cur()
        if not tab:
            return
        hits = tab.editor.find_all(self.find_var.get())
        self.set_status(f"找到 {len(hits)} 处匹配" if hits else "未找到匹配")
        if hits:
            tab.editor.text.mark_set("insert", hits[0][0])
            tab.editor.text.see(hits[0][0])

    def do_replace_all(self):
        tab = self.cur()
        if not tab or not self.find_var.get():
            return
        hits = tab.editor.find_all(self.find_var.get())
        if not hits:
            self.set_status("未找到匹配")
            return
        t = tab.editor.text
        for start, end in reversed(hits):
            t.delete(start, end)
            t.insert(start, self.replace_var.get())
        tab.editor.highlight()
        tab.editor.update_gutter()
        self.set_status(f"已替换 {len(hits)} 处")

    # ---------- 帮助 ----------
    def show_help(self):
        messagebox.showinfo("快捷键", (
            "Ctrl+N  新建      Ctrl+O  打开\n"
            "Ctrl+S  保存      Ctrl+W  关闭标签\n"
            "Ctrl+F  查找      Ctrl+G  跳转到行（内置命令条）\n"
            "F5      运行      F6      PowerShell\n"
            "Esc     关闭查找/命令条\n\n"
            "运行/编译在后台执行，不阻塞编辑"))

    # ---------- 标签页管理 ----------
    def new_tab(self, welcome=False):
        tab = Tab(self)
        self.tabs.append(tab)
        self.notebook.add(tab.frame, text=tab.title)
        if welcome:
            tab.editor.insert(
                "1.0", '# ECL 示例 —— 按 F5 运行\ndef f(x) -> int:\n    return x + 1\nend\nprint(f(1))\n')
            tab.editor.text.edit_modified(False)
            tab.editor.highlight()
        self.notebook.select(tab.frame)

    def open_file(self):
        p = filedialog.askopenfilename(
            filetypes=[("ECL 文件", "*.ecl"), ("所有文件", "*.*")])
        if p:
            self.open_path(p)

    def open_path(self, p):
        p = os.path.abspath(p)
        for tab in self.tabs:
            if tab.path == p:
                self.notebook.select(tab.frame)
                return
        tab = Tab(self, p)
        self.tabs.append(tab)
        self.notebook.add(tab.frame, text=tab.title)
        self.notebook.select(tab.frame)

    def cur(self):
        w = self.notebook.nametowidget(self.notebook.select())
        for tab in self.tabs:
            if tab.frame is w:
                return tab
        return None

    def close_tab(self, tab):
        if tab.dirty:
            r = messagebox.askyesnocancel("未保存", f"{tab.title} 有未保存的修改，保存吗？")
            if r is None:
                return False
            if r and not self.save_tab(tab):
                return False
        if tab.proc and tab.proc.alive():
            tab.proc.kill()
        self.tabs.remove(tab)
        self.notebook.forget(tab.frame)
        return True

    def close_current_tab(self):
        tab = self.cur()
        if tab:
            self.close_tab(tab)

    def close_other_tabs(self):
        cur = self.cur()
        for tab in list(self.tabs):
            if tab is not cur:
                if not self.close_tab(tab):
                    break

    def _tab_at(self, x, y):
        try:
            idx = self.notebook.index(f"@{x},{y}")
        except tk.TclError:
            return None
        w = self.notebook.tabs()[idx]
        frame = self.nametowidget(w)
        for tab in self.tabs:
            if tab.frame is frame:
                return tab
        return None

    def _show_tab_menu(self, event):
        tab = self._tab_at(event.x, event.y)
        if tab:
            self.notebook.select(tab.frame)
            self.tab_menu.tk_popup(event.x_root, event.y_root)

    def _middle_close(self, event):
        tab = self._tab_at(event.x, event.y)
        if tab:
            self.close_tab(tab)

    # ---------- 脏标记 / 标题 ----------
    def mark_dirty(self, dirty=True):
        tab = self.cur()
        if tab:
            tab.dirty = dirty
            self.refresh_title()

    def refresh_title(self):
        tab = self.cur()
        if tab:
            self.title(f"ECL IDE — {tab.path or '未命名'}")
            self.notebook.tab(tab.frame, text=tab.title)

    # ---------- 保存 ----------
    def save_current(self):
        tab = self.cur()
        if tab:
            self.save_tab(tab)

    def save_tab(self, tab):
        if not tab.path:
            p = filedialog.asksaveasfilename(defaultextension=".ecl",
                                             filetypes=[("ECL 文件", "*.ecl")])
            if not p:
                return False
            tab.path = os.path.abspath(p)
        with open(tab.path, "w", encoding="utf-8") as f:
            f.write(tab.editor.get("1.0", "end-1c"))
        tab.dirty = False
        tab.editor.clear_error()
        self.refresh_title()
        self.set_status(f"已保存 {tab.path}")
        return True

    # ---------- 运行 / 编译 ----------
    def _spawn(self, tab, kind, argv):
        cwd = os.path.dirname(tab.path) if tab.path else None
        tab.proc_kind = kind
        try:
            tab.proc = BGProc(argv, cwd=cwd, env=child_env())
        except OSError as e:
            self.write_out(f"启动失败: {e}\n", err=True)
            return
        self.after(50, lambda: self.pump(tab))

    def run(self):
        tab = self.cur()
        if not tab:
            return
        if tab.proc and tab.proc.alive():
            self.set_status("该标签页已有程序在后台运行")
            self.bottom_pane.select(0)
            return
        if not self.save_tab(tab):
            return
        ecl = find_ecl_main()
        if not ecl:
            messagebox.showerror("错误", "找不到 ecl.py")
            return
        self.write_out("", clear=True)
        tab.editor.clear_error()
        self.set_status(f"运行中（后台）… {tab.path}")
        self._spawn(tab, "run", [sys.executable, ecl, "run", tab.path])

    def compile_exe(self):
        tab = self.cur()
        if not tab:
            return
        if tab.proc and tab.proc.alive():
            self.set_status("程序运行中，请先结束")
            return
        if not self.save_tab(tab):
            return
        ecl = find_ecl_main()
        if not ecl:
            messagebox.showerror("错误", "找不到 ecl.py")
            return
        self.write_out("", clear=True)
        self.write_out(
            f"== AOT 编译 {os.path.basename(tab.path)}（后台）==\n", dim=True)
        self.set_status("编译中（后台）… 可继续编辑")
        self._spawn(tab, "compile",
                    [sys.executable, ecl, "compile", tab.path, "--target", "exe"])

    def pump(self, tab):
        if not tab.proc or tab not in self.tabs:
            return
        try:
            while True:
                item = tab.proc.q.get_nowait()
                if item is None:
                    code = tab.proc.returncode
                    kind = "编译" if tab.proc_kind == "compile" else "运行"
                    self.write_out(
                        f"\n[{kind}结束，返回码 {code}]\n", err=(code != 0))
                    self.set_status(f"{kind}结束（返回码 {code}）")
                    tab.proc = None
                    return
                self.write_out(item)
                self.check_error_line(tab, item)
        except queue.Empty:
            pass
        self.after(50, lambda: self.pump(tab))

    def check_error_line(self, tab, text):
        """解析错误输出，标红对应行。支持两种格式：
        1) xxx.ecl:23
        2) [行 23, 列 1]  （当前文件）"""
        # 格式1：带文件名
        for m in ERR_RE_FILE.finditer(text):
            err_path = os.path.abspath(os.path.join(
                os.path.dirname(tab.path or "."), m.group(1)))
            if os.path.abspath(tab.path or "") == err_path:
                try:
                    tab.editor.mark_error_line(int(m.group(2)))
                except Exception:
                    pass
                return
        # 格式2：[行 N, 列 M]，默认当前文件
        m = ERR_RE_LINE.search(text)
        if m:
            try:
                tab.editor.mark_error_line(int(m.group(1)))
            except Exception:
                pass

    # ---------- 输入 ----------
    def send_input(self, event):
        tab = self.cur()
        line = self.entry.get()
        self.entry.delete(0, "end")
        if tab and tab.proc and tab.proc.alive():
            if tab.proc.send(line):
                self.write_out(line + "\n", dim=True)
            else:
                self.write_out("[输入发送失败]\n", err=True)
        else:
            self.write_out("[当前没有正在运行的程序]\n", err=True)

    # ---------- 输出 ----------
    def write_out(self, s, err=False, dim=False, clear=False):
        self.output.config(state="normal")
        if clear:
            self.output.delete("1.0", "end")
        self.output.insert(
            "end", s, "err" if err else ("dim" if dim else None))
        self.output.see("end")
        self.output.config(state="disabled")

    def set_status(self, s):
        self.status.config(text=s)

    # ---------- 退出 ----------
    def on_quit(self):
        for tab in list(self.tabs):
            if not self.close_tab(tab):
                return
        if getattr(self, "ps", None) and self.ps.alive():
            self.ps.kill()
        self.destroy()


if __name__ == "__main__":
    app = App(sys.argv[1] if len(sys.argv) > 1 else None)
    app.mainloop()
