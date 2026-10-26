# -*- coding: utf-8 -*-
"""z-note 雏形：常驻托盘的桌面便签（自由文本 + 可勾选待办）。
运行: python main.py   依赖: pystray (pip install pystray)
"""
import ctypes, json, math, os, queue, sys, threading, uuid, winreg
import tkinter as tk
from tkinter import font as tkfont, messagebox
from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw, ImageFont

APP_NAME = "z-note"
DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "z-note")
DATA_FILE = os.path.join(DATA_DIR, "notes.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

PALETTE = {  # 名称: (背景, 描边) —— 冷调便签色
    "淡黄": ("#FFF7DC", "#EBDCA6"), "粉": ("#FCE9EE", "#F0CBD4"),
    "蓝": ("#E5EEFA", "#C9D9EE"),   "绿": ("#E4F1E6", "#C6DCCC"),
    "紫": ("#EDE8F8", "#D6CBEE"),   "橙": ("#FDEDDF", "#F1D5BB"),
    "灰白": ("#F4F5F7", "#DFE2E6"), "白": ("#FDFDFE", "#E4E6EA"),
}
T_COLOR = "#010203"   # 窗口透明色键（圆角外的部分透出去）
RADIUS = 20
EDGE = 7                              # 窗口边缘可拖拽调大小的宽度
ICON_FONT, ICON_FONT_FALLBACK = "Segoe Fluent Icons", "Segoe MDL2 Assets"
GLYPH_PIN = "\uE718"                   # 图钉：黑色=置底，灰色=置顶
GLYPH_COLOR = "\uE790"                 # 调色板
GLYPH_NEW, GLYPH_HIDE, GLYPH_DELETE = "\uE710", "\uE921", "\uE74D"
BOX_ON, BOX_OFF = "\uE73E", "\uECCA"   # 勾选框：对勾 / 空心圆
PREFIX_PAD = "  "                       # 勾选框后跟 2 个空格
INK, QUIET = "#6B7280", "#666D75"      # 图标与状态栏的墨色（冷灰单色系）

# ---------- Win32 ----------
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND

HWND_BOTTOM = wintypes.HWND(1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
WM_HOTKEY, MOD_ALT, MOD_CONTROL = 0x0312, 0x0001, 0x0002

class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

def cursor_pos():
    pt = _POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def ensure_single_instance():
    kernel32.CreateMutexW(None, wintypes.BOOL(False), "z-note-singleton-mutex")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        r = tk.Tk(); r.withdraw()
        messagebox.showinfo(APP_NAME, "z-note 已经在运行了（看系统托盘）。")
        os._exit(0)

# ---------- 小工具 ----------
def darken(hex_color, f=0.90):
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))

def mix(a, b, t):
    ca, cb = a.lstrip("#"), b.lstrip("#")
    return "#%02x%02x%02x" % tuple(
        int(int(ca[i:i+2], 16) * (1 - t) + int(cb[i:i+2], 16) * t) for i in (0, 2, 4))

def round_rect(cv, x1, y1, x2, y2, r, **kw):
    # 直边 + 四角圆弧：直线段走边，每个角用 12 段小线段逼近四分之一圆
    pts = []
    for cx, cy, a0 in ((x2 - r, y1 + r, 270), (x2 - r, y2 - r, 0),
                       (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
        for i in range(13):
            a = math.radians(a0 + 90 * i / 12)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return cv.create_polygon(pts, **kw)

# ---------- 数据 ----------
DEFAULT_SETTINGS = {"autostart": False, "showOnStartup": True,
                    "opacity": 0.95, "color": "淡黄", "fontSize": 11}

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"schemaVersion": 1, "settings": dict(DEFAULT_SETTINGS), "notes": []}
    _rotate_backups()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"schemaVersion": 1, "settings": dict(DEFAULT_SETTINGS), "notes": []}

def _rotate_backups():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        import time
        stamp = time.strftime("%Y%m%d-%H%M%S")
        with open(DATA_FILE, "r", encoding="utf-8") as f, \
             open(os.path.join(BACKUP_DIR, f"notes.{stamp}.json"), "w", encoding="utf-8") as g:
            g.write(f.read())
        backups = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("notes."))
        for old in backups[:-5]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except Exception:
        pass

def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)

def set_autostart(on):
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        if on:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ,
                                  f'"{sys.executable}" "{os.path.abspath(__file__)}"')
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass
        return True
    except OSError:
        return False

# ---------- 悬停提示 ----------
class Tooltip:
    """悬停片刻后出现的深色圆角小提示，全局同一时间只显示一个。"""
    _cur = None
    _font = None

    def __init__(self, widget, get_text, delay=500):
        self._w, self._gt, self._delay, self._aid = widget, get_text, delay, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress-1>", self._leave, add="+")

    def _enter(self, _e):
        self._cancel()
        self._aid = self._w.after(self._delay, self._show)

    def _leave(self, _e):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._aid:
            self._w.after_cancel(self._aid)
            self._aid = None

    @classmethod
    def _hide(cls):
        if cls._cur:
            try:
                cls._cur.destroy()
            except Exception:
                pass
            cls._cur = None

    def _show(self):
        self._hide()
        text = self._gt()
        if not text:
            return
        if Tooltip._font is None:
            Tooltip._font = tkfont.Font(family="Microsoft YaHei UI", size=9, root=self._w)
        w, h = Tooltip._font.measure(text) + 24, 30
        tw = tk.Toplevel(self._w)
        tw.overrideredirect(True)
        tw.attributes("-transparentcolor", T_COLOR)
        tw.attributes("-topmost", True)
        cv = tk.Canvas(tw, bg=T_COLOR, highlightthickness=0, bd=0)
        cv.pack()
        round_rect(cv, 1, 1, w - 2, h - 2, 8, fill="#FFFFFF", outline="#D9DCE1")
        cv.create_text(w / 2, h / 2, text=text, fill="#3A3F46",
                       font=("Microsoft YaHei UI", 9))
        x = max(4, self._w.winfo_rootx() + self._w.winfo_width() // 2 - w // 2)
        y = self._w.winfo_rooty() - h - 6
        if y < 0:
            y = self._w.winfo_rooty() + self._w.winfo_height() + 6
        tw.geometry(f"{w}x{h}+{x}+{y}")
        Tooltip._cur = tw

# ---------- 工具栏圆形按钮 ----------
class ToolButton(tk.Canvas):
    """圆形悬浮底 + 图标字形的扁平按钮；active 态为深色实底（图钉的按下感）。"""

    def __init__(self, master, glyph, get_tip, cmd, icon_font, bg,
                 active_glyph=None, size=26, ink=INK):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._bg, self._ink, self._font = bg, ink, icon_font
        self._glyph, self._aglyph, self._active, self._cmd = glyph, active_glyph, False, cmd
        r = 11
        self._pill = self.create_oval(size/2 - r, size/2 - r, size/2 + r, size/2 + r,
                                      state="hidden", outline="")
        self._icon = self.create_text(size/2, size/2, text=glyph,
                                      font=(icon_font, 11), fill=ink)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", lambda e: cmd())
        Tooltip(self, get_tip)

    def _on_enter(self, _e):
        if not self._active:
            self.itemconfig(self._pill, state="normal", fill=mix(self._bg, "#000000", 0.06))

    def _on_leave(self, _e):
        if not self._active:
            self.itemconfig(self._pill, state="hidden")

    def set_bg(self, bg):
        self._bg = bg
        self.config(bg=bg)
        if self._active:
            self.itemconfig(self._pill, fill=darken(bg, 0.72))

    def set_active(self, on):
        self._active = on
        if on:
            self.itemconfig(self._pill, state="hidden")
            self.itemconfig(self._icon, fill="#1F2328", text=self._aglyph or self._glyph)
        else:
            self.itemconfig(self._icon, fill=self._ink, text=self._glyph)

# ---------- 便签窗口 ----------
class NoteWindow(tk.Toplevel):
    def __init__(self, app, data):
        super().__init__(app.root)
        self.app, self.d = app, data
        self._boosted = False
        self._bg, self._border = PALETTE[data["color"]]
        self.configure(bg=T_COLOR)
        self.attributes("-transparentcolor", T_COLOR)
        self.geometry(f"{data['w']}x{data['h']}+{data['x']}+{data['y']}")
        self.attributes("-alpha", data["opacity"])
        self.overrideredirect(True)

        # 画布画圆角底板，控件都放圆角内部
        self.cv = tk.Canvas(self, bg=T_COLOR, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)

        # -- 工具栏 --
        self.toolbar = tk.Frame(self, bg=self._bg)
        self._tool_btns = []
        self._chip_font = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")

        def btn(glyph, get_tip, cmd):
            b = ToolButton(self.toolbar, glyph, get_tip, cmd, app.icon_font, self._bg)
            self._tool_btns.append(b)
            return b

        self.b_pin = ToolButton(self.toolbar, GLYPH_PIN,
                                lambda: "图钉：黑色 = 置底，灰色 = 置顶",
                                self.toggle_pin, app.icon_font, self._bg,
                                ink="#9B968D")
        self._tool_btns.append(self.b_pin)
        self.b_pin.pack(side="left", padx=(8, 3), pady=9)

        # 标题胶囊：悬停有提示，点击改名
        self.title_box = tk.Frame(self.toolbar, bg=self._bg)
        self.title_box.pack(side="left", padx=2)
        self.title_chip = tk.Canvas(self.title_box, height=28, bg=self._bg,
                                    highlightthickness=0, cursor="hand2")
        self.title_chip.pack(pady=8)
        self.title_chip.bind("<Button-1>", lambda e: self.edit_title())
        Tooltip(self.title_chip, lambda: "点击修改标题")

        spacer = tk.Label(self.toolbar, text="", bg=self._bg, cursor="fleur")
        spacer.pack(side="left", fill="both", expand=True)
        btn(GLYPH_HIDE, lambda: "隐藏这张便签", self.hide).pack(side="right", padx=(2, 8), pady=9)
        btn(GLYPH_DELETE, lambda: "删除便签", self.delete_self).pack(side="right", padx=5, pady=9)
        btn(GLYPH_NEW, lambda: "新建便签（Ctrl+Alt+N）",
            lambda: self.app.new_note()).pack(side="right", padx=5, pady=9)
        btn(GLYPH_COLOR, lambda: "便签颜色", self.open_colors).pack(side="right", padx=(8, 5), pady=9)
        for w in (self.toolbar, spacer):
            w.bind("<Button-1>", self._drag_press)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_release)

        # -- 正文 --
        self.done_font = tkfont.Font(family="Microsoft YaHei UI",
                                     size=app.data["settings"]["fontSize"], overstrike=True)
        self.text = tk.Text(self, bg=self._bg, fg="#2B2F36", bd=0, highlightthickness=0,
                            padx=12, pady=10, wrap="word", spacing1=5, spacing3=6,
                            font=app.base_font, insertbackground="#2B2F36", cursor="xterm")
        self.text.tag_config("boxoff", font=(app.icon_font, 12), foreground="#B8BEC6")
        self.text.tag_config("boxon", font=(app.icon_font, 12), foreground="#6F7780")
        self.text.tag_config("done", font=self.done_font)  # 只加删除线，不改字色

        # -- 底部状态栏 + 添加条 --
        self.bar = tk.Frame(self, bg=self._bg)
        self.status = tk.Label(self.bar, text="", bg=self._bg, fg=QUIET,
                               font=("Microsoft YaHei UI", 9))
        self.status.pack(side="left")
        self.clear_btn = tk.Label(self.bar, text="清除已完成", bg=self._bg,
                                  fg="#A9AFB6", font=("Microsoft YaHei UI", 9),
                                  padx=6, cursor="hand2")
        self.clear_btn.pack(side="right")
        self.clear_btn.bind("<Enter>", lambda e: self.clear_btn.config(fg="#454B52"))
        self.clear_btn.bind("<Leave>", lambda e: self.update_status())
        self.clear_btn.bind("<Button-1>", lambda e: self.clear_done())
        Tooltip(self.clear_btn, lambda: "清除已完成")
        self.add_pill = tk.Canvas(self, bg=self._bg, highlightthickness=0, cursor="hand2")
        self.add_pill.bind("<Button-1>", self._pill_press)
        Tooltip(self.add_pill, lambda: "添加待办")
        self._bg_widgets = [self.toolbar, spacer, self.bar, self.status,
                            self.clear_btn, self.add_pill]
        self._draw_chip()

        self.bind("<Configure>", lambda e: self._relayout())
        self.cv.bind("<Motion>", self._edge_cursor)
        self.cv.bind("<Button-1>", self._edge_press)
        self.cv.bind("<B1-Motion>", self._edge_move)
        self.cv.bind("<ButtonRelease-1>", self._edge_release)
        t = self.text
        t.bind("<KeyRelease>", self._on_keyrelease)
        t.bind("<KeyPress-Return>", self._on_enter)
        t.bind("<KeyPress-BackSpace>", self._on_backspace)
        t.bind("<Button-1>", self._on_click)
        t.bind("<Button-3>", self._on_rightclick)
        t.bind("<FocusIn>", lambda e: self._set_boost(True))
        t.bind("<FocusOut>", lambda e: self._set_boost(False))
        t.bind("<Control-MouseWheel>", self._ctrl_wheel)
        self.bind("<FocusOut>", lambda e: self.sink_if_bottom())

        self.render()
        self.update_idletasks()
        self._relayout()
        self.hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        self.apply_z()
        self._restyle()
        if not (data.get("visible", True) and app.show_notes):
            self.withdraw()

    def _relayout(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50 or (w, h) == getattr(self, "_last_sz", None):
            return  # 尺寸没变（纯移动触发的 Configure）不重画
        self._last_sz = (w, h)
        self.cv.delete("all")
        round_rect(self.cv, 1, 1, w - 2, h - 2, RADIUS,
                   fill=self._bg, outline=self._border)
        ix = RADIUS + 4
        self.toolbar.place(x=ix, y=6, width=w - 2 * ix, height=44)
        self.text.place(x=ix, y=56, width=w - 2 * ix, height=max(20, h - 56 - 86))
        self.bar.place(x=ix + 6, y=h - 78, width=w - 2 * ix - 12, height=26)
        self.add_pill.place(x=ix + 4, y=h - 46, width=w - 2 * ix - 8, height=36)
        self._draw_pill(w - 2 * ix - 8)

    def _restyle(self):
        for b in self._tool_btns:
            b.set_bg(self._bg)
        self.b_pin.set_active(self.d["z"] == "bottom")

    def toggle_pin(self):
        self.set_z("top" if self.d["z"] == "bottom" else "bottom")

    # ---- 底部添加条 ----
    def add_todo(self):
        self.d["lines"].append({"type": "todo", "text": "", "checked": False})
        self.render(keep_cursor=False)
        row = len(self.d["lines"])
        self.text.mark_set("insert", f"{row}.0+{self._prefix_len(self.d['lines'][-1])}c")
        self.text.see(f"{row}.0")
        self.text.focus_set()
        self.app.schedule_save()

    def _pill_press(self, _e):
        # 按压态：短暂加深再回弹
        self.add_pill.itemconfig("pill", fill="#EFF1F3")
        self.add_pill.after(120, lambda: self.add_pill.itemconfig("pill", fill="#FFFFFF"))
        self.add_todo()

    def _draw_pill(self, width=None):
        c = self.add_pill
        w = width or c.winfo_width()
        if w < 40:
            return
        c.delete("all")
        round_rect(c, 1, 1, w - 2, 35, 14, fill="#FFFFFF",
                   outline="#D9DCE1", tags="pill")
        c.create_text(w / 2, 18, text=GLYPH_NEW, font=(self.app.icon_font, 12), fill=INK)

    # ---- 标题胶囊 ----
    def _draw_chip(self):
        c = self.title_chip
        c.delete("all")
        title = self.d.get("title", "")
        text = title or "无标题"
        while self._chip_font.measure(text) > 140 and len(text) > 2:
            text = text[:-2] + "…"
        w = self._chip_font.measure(text) + 26
        c.config(width=w, height=28)
        round_rect(c, 1, 2, w - 1, 26, 11, fill="#FFFFFF", outline="#E2E4E8")
        c.create_text(w / 2, 14, text=text, font=self._chip_font,
                      fill="#1F2328" if title else "#A9AFB6")

    def edit_title(self):
        self.title_chip.pack_forget()
        e = tk.Entry(self.title_box, bd=0, width=12,
                     highlightthickness=2,
                     highlightbackground="#E2DFD8",
                     highlightcolor="#8C867C",
                     bg="#FFFFFF", fg="#3A362F",
                     font=self._chip_font, justify="center")
        e.pack(ipady=3, pady=1)
        e.insert(0, self.d.get("title", ""))
        e.focus_set()
        e.select_range(0, "end")

        def commit(_e=None):
            self.d["title"] = e.get().strip()
            self.app.schedule_save()
            e.destroy()
            self.title_chip.pack()
            self._draw_chip()

        e.bind("<Return>", commit)
        e.bind("<Escape>", commit)
        e.bind("<FocusOut>", commit)

    # ---- 渲染 ----
    def render(self, keep_cursor=True):
        cur = self.text.index("insert")
        self.text.delete("1.0", "end")
        lines = self.d["lines"] or [{"type": "text", "text": "", "checked": False}]
        self.d["lines"] = lines
        for i, ln in enumerate(lines):
            row = i + 1
            prefix = (BOX_ON if ln["checked"] else BOX_OFF) + PREFIX_PAD \
                if ln["type"] == "todo" else ""
            self.text.insert("end", prefix + ln["text"])
            if ln["type"] == "todo":
                self.text.tag_add("boxon" if ln["checked"] else "boxoff",
                                  f"{row}.0", f"{row}.{len(prefix)}")
                if ln["checked"]:
                    self.text.tag_add("done", f"{row}.{len(prefix)}", f"{row}.end")
            if i < len(lines) - 1:
                self.text.insert("end", "\n")
        if keep_cursor:
            end = self.text.index("end-1c")
            if self.text.compare(cur, ">", end):
                cur = end
            self.text.mark_set("insert", cur)
        self.update_status()

    @staticmethod
    def _prefix_len(ln):
        return len(BOX_OFF) + len(PREFIX_PAD) if ln["type"] == "todo" else 0

    def _row_col(self, index):
        r, c = index.split(".")
        return int(r) - 1, int(c)

    def update_status(self):
        todos = [l for l in self.d["lines"] if l["type"] == "todo"]
        done = sum(1 for l in todos if l["checked"])
        self.status.config(text=f"待办 {done}/{len(todos)}" if todos else "")
        self.clear_btn.config(fg="#6F7780" if done else "#A9AFB6")

    # ---- 编辑事件 ----
    def _on_keyrelease(self, _e):
        lines = self.d["lines"]
        row, col = self._row_col(self.text.index("insert"))
        if row >= len(lines):
            return
        ln = lines[row]
        pre = self._prefix_len(ln)
        if col < pre:
            self.text.mark_set("insert", f"{row+1}.0+{pre}c")
            return
        content = self.text.get(f"{row+1}.0+{pre}c", f"{row+1}.end")
        if "\n" in content:  # 多行粘贴：拆成多条
            parts = content.split("\n")
            new = []
            for j, p in enumerate(parts):
                if j == 0:
                    ln["text"] = p
                    new.append(ln)
                else:
                    new.append({"type": "text", "text": p, "checked": False})
            lines[row:row+1] = new
            self.render(keep_cursor=False)
            self.text.mark_set("insert", "end-1c lineend")
        else:
            ln["text"] = content
        self.app.schedule_save()

    def _on_enter(self, _e):
        row, col = self._row_col(self.text.index("insert"))
        lines = self.d["lines"]
        ln = lines[row]
        pre = self._prefix_len(ln)
        rel = max(0, col - pre)
        left, right = ln["text"][:rel], ln["text"][rel:]
        ln["text"] = left
        lines.insert(row + 1, {"type": ln["type"], "text": right, "checked": False})
        self.render(keep_cursor=False)
        nrow = row + 2
        self.text.mark_set("insert", f"{nrow}.0+{self._prefix_len(lines[row+1])}c")
        self.text.see(f"{nrow}.0")
        self.app.schedule_save()
        return "break"

    def _on_backspace(self, _e):
        row, col = self._row_col(self.text.index("insert"))
        lines = self.d["lines"]
        ln = lines[row]
        if col <= self._prefix_len(ln) and row > 0:
            prev = lines[row - 1]
            prev["text"] += ln["text"]
            del lines[row]
            target = f"{row}.0+{self._prefix_len(prev)}c lineend"
            self.render(keep_cursor=False)
            self.text.mark_set("insert", target)
            self.app.schedule_save()
            return "break"
        return None

    def _on_click(self, e):
        self.focus_set(); self.text.focus_set()
        try:
            row, col = self._row_col(self.text.index(f"@{e.x},{e.y}"))
        except Exception:
            return None
        lines = self.d["lines"]
        if row >= len(lines):
            return None
        ln = lines[row]
        pre = self._prefix_len(ln)
        if pre and col <= len(BOX_OFF) + 1:  # 点在勾选框上
            self.toggle_done(row)
            return "break"
        if col < pre:
            self.text.mark_set("insert", f"{row+1}.0+{pre}c")
            return "break"
        return None

    def _on_rightclick(self, e):
        self.focus_set(); self.text.focus_set()
        try:
            row, _ = self._row_col(self.text.index(f"@{e.x},{e.y}"))
        except Exception:
            return
        lines = self.d["lines"]
        if row >= len(lines):
            return
        m = tk.Menu(self, tearoff=0)
        if lines[row]["type"] == "todo":
            m.add_command(label="转为文本行", command=lambda: self.convert(row, "text"))
        else:
            m.add_command(label="转为待办行", command=lambda: self.convert(row, "todo"))
        m.add_command(label="在下方插入新行", command=lambda: self.insert_line(row))
        m.add_separator()
        m.add_command(label="删除此行", command=lambda: self.delete_line(row))
        m.tk_popup(e.x_root, e.y_root)

    def toggle_done(self, row):
        ln = self.d["lines"][row]
        ln["checked"] = not ln["checked"]
        pre = self._prefix_len(ln)
        self.text.delete(f"{row+1}.0", f"{row+1}.{pre}")
        self.text.insert(f"{row+1}.0", (BOX_ON if ln["checked"] else BOX_OFF) + PREFIX_PAD)
        tag = "boxon" if ln["checked"] else "boxoff"
        self.text.tag_add(tag, f"{row+1}.0", f"{row+1}.{pre}")
        if ln["checked"]:
            self.text.tag_add("done", f"{row+1}.{pre}", f"{row+1}.end")
        else:
            self.text.tag_remove("done", f"{row+1}.0", f"{row+1}.end")
        self.text.mark_set("insert", f"{row+1}.0+{pre}c")
        # Q弹：只把这一行的勾选框放大一下再弹回
        pop = f"pop{row}-{uuid.uuid4().hex[:6]}"
        self.text.tag_add(pop, f"{row+1}.0", f"{row+1}.{pre}")
        self.text.tag_config(pop, font=(self.app.icon_font, 16))
        self.text.after(90, lambda: self.text.tag_delete(pop))
        self.update_status()
        self.app.schedule_save()

    def convert(self, row, to):
        ln = self.d["lines"][row]
        ln["type"] = to
        if to == "text":
            ln["checked"] = False
        self.render(keep_cursor=False)
        self.text.mark_set("insert", f"{row+1}.0+{self._prefix_len(ln)}c")
        self.app.schedule_save()

    def insert_line(self, row):
        self.d["lines"].insert(row + 1, {"type": "text", "text": "", "checked": False})
        self.render(keep_cursor=False)
        self.text.mark_set("insert", f"{row+2}.0")
        self.app.schedule_save()

    def delete_line(self, row):
        if len(self.d["lines"]) <= 1:
            self.d["lines"] = [{"type": "text", "text": "", "checked": False}]
        else:
            del self.d["lines"][row]
        self.render(keep_cursor=False)
        self.app.schedule_save()

    def clear_done(self):
        if not any(l["checked"] for l in self.d["lines"]):
            return
        self.d["lines"] = [l for l in self.d["lines"]
                           if not (l["type"] == "todo" and l["checked"])]
        if not self.d["lines"]:
            self.d["lines"] = [{"type": "text", "text": "", "checked": False}]
        self.render(keep_cursor=False)
        self.app.schedule_save()

    def _ctrl_wheel(self, e):
        step = 0.05 if e.delta > 0 else -0.05
        v = min(1.0, max(0.1, self.d["opacity"] + step))
        self.d["opacity"] = v
        if not self._boosted:
            self.attributes("-alpha", v)
        self.app.schedule_save()
        return "break"

    # ---- 窗口操作 ----
    def _drag_press(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()
    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")
    def _drag_release(self, _e):
        self.d["x"], self.d["y"] = self.winfo_x(), self.winfo_y()
        self.app.schedule_save()

    # ---- 四边八向调整大小 ----
    def _edge_hit(self, x, y):
        w, h = self.winfo_width(), self.winfo_height()
        return x < EDGE, x > w - EDGE, y < EDGE, y > h - EDGE  # 左 右 上 下

    def _edge_cursor(self, e):
        l, r, t, b = self._edge_hit(e.x, e.y)
        if (l and t) or (r and b):
            cur = "size_nw_se"
        elif (l and b) or (r and t):
            cur = "size_ne_sw"
        elif l or r:
            cur = "size_we"
        elif t or b:
            cur = "size_ns"
        else:
            cur = "arrow"
        if cur != self.cv.cget("cursor"):
            self.cv.config(cursor=cur)

    def _edge_press(self, e):
        l, r, t, b = self._edge_hit(e.x, e.y)
        if not (l or r or t or b):
            return None
        self._rs = [l, r, t, b, self.winfo_width(), self.winfo_height(),
                    self.winfo_x(), self.winfo_y(), e.x_root, e.y_root]
        return "break"

    def _edge_move(self, e):
        if not getattr(self, "_rs", None):
            return
        l, r, t, b, w0, h0, x0, y0, mx0, my0 = self._rs
        dx, dy = e.x_root - mx0, e.y_root - my0
        x, y, w, h = x0, y0, w0, h0
        if r:
            w = max(280, w0 + dx)
        if b:
            h = max(230, h0 + dy)
        if l:
            w = max(280, w0 - dx)
            x = x0 + w0 - w
        if t:
            h = max(230, h0 - dy)
            y = y0 + h0 - h
        self.geometry(f"{w}x{h}+{x}+{y}")
        return "break"

    def _edge_release(self, _e):
        if getattr(self, "_rs", None):
            self._rs = None
            self.d.update(w=self.winfo_width(), h=self.winfo_height(),
                          x=self.winfo_x(), y=self.winfo_y())
            self.app.schedule_save()

    # ---- 层级 / 透明度 / 颜色 ----
    def set_z(self, state):
        if self.d["z"] == state:
            return
        self.d["z"] = state
        self.apply_z()
        self._restyle()
        self.app.schedule_save()

    def apply_z(self):
        top = self.d["z"] == "top"
        self.attributes("-topmost", top)
        if not top:
            h = getattr(self, "hwnd", None) or user32.GetParent(self.winfo_id())
            user32.SetWindowPos(h, HWND_BOTTOM, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def sink_if_bottom(self):
        if self.d["z"] == "bottom" and getattr(self, "hwnd", None):
            if user32.GetForegroundWindow() != self.hwnd:
                user32.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _set_boost(self, on):
        if on and self.d["opacity"] < 0.8:
            self.attributes("-alpha", 0.8); self._boosted = True
        else:
            self._boosted = False
            self.attributes("-alpha", self.d["opacity"])

    # ---- 弹出层（圆角） ----
    def _popup(self, w, h, builder):
        p = tk.Toplevel(self)
        p.overrideredirect(True)
        p.configure(bg=T_COLOR)
        p.attributes("-transparentcolor", T_COLOR)
        p.geometry(f"{w}x{h}")
        cv = tk.Canvas(p, bg=T_COLOR, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        round_rect(cv, 1, 1, w - 2, h - 2, 12, fill="#FFFFFF", outline="#DDD8D0")
        inner = tk.Frame(p, bg="#FFFFFF")
        inner.place(x=10, y=8, width=w - 20, height=h - 16)
        builder(inner)
        p.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() - w - 16
        y = self.winfo_rooty() + 40
        p.geometry(f"+{max(0, x)}+{y}")
        p.grab_set(); p.focus_set()
        p.bind("<FocusOut>", lambda e: p.destroy())
        return p

    def open_colors(self):
        p = None

        def build(f):
            nonlocal p
            row = tk.Frame(f, bg="#FFFFFF")
            row.pack(anchor="w")
            for name, (bg, _bd) in PALETTE.items():
                c = tk.Canvas(row, width=26, height=26, bg="#FFFFFF",
                              highlightthickness=0, cursor="hand2")
                c.create_oval(3, 3, 23, 23, fill=bg, width=2,
                              outline="#4A453D" if name == self.d["color"] else "#D8D3CA")
                c.pack(side="left", padx=1, pady=1)
                c.bind("<Button-1>", lambda e, n=name: (self.set_color(n), p.destroy()))
            tk.Label(f, text="点击选择便签颜色", bg="#FFFFFF", fg="#A8A39A",
                     font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=2)
        p = self._popup(260, 72, build)

    def set_color(self, name):
        self.d["color"] = name
        self._bg, self._border = PALETTE[name]
        self.text.config(bg=self._bg)
        for w in self._bg_widgets:
            w.config(bg=self._bg)
        self._draw_chip()
        self._restyle()
        self._relayout()
        self.update_status()
        self.app.schedule_save()

    def hide(self):
        self.d["visible"] = False
        self.withdraw()
        self.app.schedule_save()

    def show(self):
        self.d["visible"] = True
        self.deiconify()
        self.overrideredirect(True)
        self.apply_z()

    def delete_self(self):
        # 不用原生 messagebox：overrideredirect 父窗口会让原生模态框收不到点击
        p = tk.Toplevel(self)
        p.overrideredirect(True)
        p.configure(bg=T_COLOR)
        p.attributes("-transparentcolor", T_COLOR)
        p.attributes("-topmost", True)
        W, H = 268, 118
        cv = tk.Canvas(p, bg=T_COLOR, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        round_rect(cv, 1, 1, W - 2, H - 2, 12, fill="#FFFFFF", outline="#D9DCE1")
        cv.create_text(W / 2, 30, text="删除便签", fill="#1F2328",
                       font=("Microsoft YaHei UI", 10, "bold"))
        cv.create_text(W / 2, 56, text="删除后无法恢复，确定删除这张便签吗？",
                       fill="#666D75", font=("Microsoft YaHei UI", 9))
        bx, by = W - 16, H - 14
        b_del = round_rect(cv, bx - 76, by - 30, bx, by, 8, fill="#E5484D", outline="")
        cv.create_text(bx - 38, by - 15, text="删除", fill="#FFFFFF",
                       font=("Microsoft YaHei UI", 9, "bold"))
        b_cancel = round_rect(cv, bx - 164, by - 30, bx - 88, by, 8,
                              fill="#F2F3F5", outline="#D9DCE1")
        cv.create_text(bx - 126, by - 15, text="取消", fill="#3A3F46",
                       font=("Microsoft YaHei UI", 9))

        def cancel(_e=None):
            p.grab_release()
            p.destroy()

        def confirm(_e=None):
            cancel()
            self.app.remove_note(self)

        cv.tag_bind(b_del, "<Button-1>", confirm)
        cv.tag_bind(b_cancel, "<Button-1>", cancel)
        p.bind("<Escape>", cancel)
        p.update_idletasks()
        x = max(0, self.winfo_rootx() + (self.winfo_width() - W) // 2)
        y = max(0, self.winfo_rooty() + (self.winfo_height() - H) // 2)
        p.geometry(f"+{x}+{y}")
        p.grab_set()
        p.focus_set()
        p.bind("<FocusOut>", cancel)

# ---------- 设置窗口 ----------
class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("z-note 设置")
        self.transient(app.root)
        self.resizable(False, False)
        self.configure(bg="#FAFAF8")
        f = tk.Frame(self, bg="#FAFAF8", padx=20, pady=16)
        f.pack(fill="both", expand=True)

        self.v_boot = tk.BooleanVar(value=app.data["settings"]["autostart"])
        tk.Checkbutton(f, text="开机自启", variable=self.v_boot, bg="#FAFAF8",
                       activebackground="#FAFAF8", command=self._toggle_boot).pack(anchor="w", pady=2)

        self.v_show = tk.BooleanVar(value=app.data["settings"]["showOnStartup"])
        tk.Checkbutton(f, text="启动时显示便签（关闭=仅驻留托盘）", variable=self.v_show,
                       bg="#FAFAF8", activebackground="#FAFAF8", command=self._save).pack(anchor="w", pady=2)

        row = tk.Frame(f, bg="#FAFAF8"); row.pack(fill="x", pady=(10, 2))
        tk.Label(row, text="新便签默认透明度", bg="#FAFAF8").pack(side="left")
        s = tk.Scale(row, from_=10, to=100, orient="horizontal", length=150,
                     bg="#FAFAF8", highlightthickness=0, troughcolor="#E8E4DD",
                     command=self._op)
        s.set(int(app.data["settings"]["opacity"] * 100))
        s.pack(side="right")

        row2 = tk.Frame(f, bg="#FAFAF8"); row2.pack(fill="x", pady=2)
        tk.Label(row2, text="新便签默认颜色", bg="#FAFAF8").pack(anchor="w")
        cs = tk.Frame(row2, bg="#FAFAF8"); cs.pack(anchor="w", pady=4)
        for name, (bg, _bd) in PALETTE.items():
            c = tk.Canvas(cs, width=22, height=22, bg="#FAFAF8",
                          highlightthickness=0, cursor="hand2")
            c.create_oval(2, 2, 20, 20, fill=bg, outline="#C9C4BB")
            c.pack(side="left", padx=3)
            c.bind("<Button-1>", lambda e, n=name: self._color(n))

        row3 = tk.Frame(f, bg="#FAFAF8"); row3.pack(fill="x", pady=(8, 0))
        tk.Label(row3, text="字号", bg="#FAFAF8").pack(side="left")
        s2 = tk.Scale(row3, from_=9, to=16, orient="horizontal", length=150,
                      bg="#FAFAF8", highlightthickness=0, troughcolor="#E8E4DD",
                      command=self._font)
        s2.set(app.data["settings"]["fontSize"])
        s2.pack(side="right")

        tk.Label(f, text="全局快捷键：Ctrl+Alt+N 新建便签 · Ctrl+Alt+H 显示/隐藏全部\n数据：%s"
                 % DATA_FILE, bg="#FAFAF8", fg="#999",
                 font=("Microsoft YaHei UI", 9), justify="left").pack(anchor="w", pady=(14, 0))

    def _toggle_boot(self):
        if set_autostart(self.v_boot.get()):
            self.app.data["settings"]["autostart"] = self.v_boot.get()
        else:
            self.v_boot.set(False)
            messagebox.showwarning("z-note", "写入开机自启失败。", parent=self)
        self._save()

    def _op(self, v):
        self.app.data["settings"]["opacity"] = int(v) / 100
        self._save()

    def _color(self, n):
        self.app.data["settings"]["color"] = n
        self._save()

    def _font(self, v):
        self.app.data["settings"]["fontSize"] = int(v)
        self.app.update_fonts()
        self._save()

    def _save(self):
        self.app.schedule_save()

# ---------- 主应用 ----------
def _make_tray_icon():
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, 30, 30], 7, fill=(255, 213, 79, 255))
    try:
        font = ImageFont.load_default(20)
    except TypeError:
        font = ImageFont.load_default()
    d.text((16, 15), "Z", fill=(70, 58, 20, 255), font=font, anchor="mm")
    return img

class App:
    def __init__(self):
        self.data = load_data()
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.notes = {}
        self._save_job = None
        self._cascade = 0
        self.settings_win = None
        self.show_notes = self.data["settings"]["showOnStartup"]

        self.base_font = tkfont.Font(family="Microsoft YaHei UI",
                                     size=self.data["settings"]["fontSize"])
        fams = tkfont.families(self.root)
        self.icon_font = ICON_FONT if ICON_FONT in fams else ICON_FONT_FALLBACK

        self._restore_notes()
        self._start_tray()
        self._start_hotkeys()
        self.root.after(120, self._pump)
        self.root.after(1200, self._enforce_bottom)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

    # 便签管理
    def _restore_notes(self):
        if not self.data["notes"]:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.data["notes"].append({
                "id": str(uuid.uuid4()), "x": sw - 360, "y": 90, "w": 320, "h": 340,
                "z": "top", "opacity": self.data["settings"]["opacity"],
                "color": self.data["settings"]["color"], "visible": True,
                "lines": [
                    {"type": "text", "text": "欢迎使用 z-note", "checked": False},
                    {"type": "todo", "text": "点前面的圆框勾选我", "checked": False},
                    {"type": "todo", "text": "右键这行：转换/插入/删除", "checked": False},
                    {"type": "todo", "text": "Ctrl+滚轮 调透明度", "checked": False},
                ]})
            self.save_now()
        for d in self.data["notes"]:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            d["x"] = min(max(0, d["x"]), max(0, sw - 120))
            d["y"] = min(max(0, d["y"]), max(0, sh - 60))
            d["w"] = max(d.get("w", 320), 300)  # 旧数据太窄会挤掉工具栏
            d["h"] = max(d.get("h", 300), 240)
            if d.get("z") not in ("bottom", "top"):  # 旧数据迁移：去掉 normal 态
                d["z"] = "top"
            w = NoteWindow(self, d)
            self.notes[d["id"]] = w

    def update_fonts(self):
        self.base_font.config(size=self.data["settings"]["fontSize"])
        for w in self.notes.values():
            w.done_font.config(size=self.data["settings"]["fontSize"])

    def new_note(self):
        cx, cy = cursor_pos()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        off = 24 * (self._cascade % 6); self._cascade += 1
        d = {"id": str(uuid.uuid4()),
             "x": min(cx + off, sw - 240), "y": min(cy + off, sh - 200),
             "w": 320, "h": 300, "z": "top",
             "opacity": self.data["settings"]["opacity"],
             "color": self.data["settings"]["color"], "visible": True,
             "lines": [{"type": "todo", "text": "", "checked": False}]}
        self.data["notes"].append(d)
        w = NoteWindow(self, d)
        self.notes[d["id"]] = w
        w.show()
        w.text.focus_set()
        self.schedule_save()
        return w

    def remove_note(self, w):
        self.notes.pop(w.d["id"], None)
        if w.d in self.data["notes"]:
            self.data["notes"].remove(w.d)
        w.destroy()
        self.schedule_save()

    def toggle_all(self):
        any_visible = any(w.d["visible"] for w in self.notes.values())
        for w in self.notes.values():
            (w.hide() if any_visible else w.show())
        self.schedule_save()

    # 托盘
    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("新建便签  Ctrl+Alt+N", lambda: self.q.put("new"), default=True),
            pystray.MenuItem("显示/隐藏全部便签  Ctrl+Alt+H", lambda: self.q.put("toggle")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("设置…", lambda: self.q.put("settings")),
            pystray.MenuItem("退出", lambda: self.q.put("quit")),
        )
        self.icon = pystray.Icon(APP_NAME, _make_tray_icon(), "z-note 便签", menu)
        self.icon.run_detached()

    # 全局快捷键（线程消息循环）
    def _start_hotkeys(self):
        def loop():
            user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT, 0x4E)  # N
            user32.RegisterHotKey(None, 2, MOD_CONTROL | MOD_ALT, 0x48)  # H
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.q.put("new" if msg.wParam == 1 else "toggle")
        threading.Thread(target=loop, daemon=True).start()

    def _pump(self):
        try:
            while True:
                act = self.q.get_nowait()
                if act == "new":
                    self.new_note()
                elif act == "toggle":
                    self.toggle_all()
                elif act == "settings":
                    if self.settings_win is None or not self.settings_win.winfo_exists():
                        self.settings_win = SettingsWindow(self)
                    else:
                        self.settings_win.lift(); self.settings_win.focus_set()
                elif act == "quit":
                    self.quit()
        except queue.Empty:
            pass
        self.root.after(120, self._pump)

    def _enforce_bottom(self):
        fg = user32.GetForegroundWindow()
        for w in self.notes.values():
            if w.d["z"] == "bottom" and w.d["visible"] and fg != w.hwnd:
                user32.SetWindowPos(w.hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self.root.after(1200, self._enforce_bottom)

    # 保存
    def schedule_save(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(700, self.save_now)

    def save_now(self):
        self._save_job = None
        try:
            save_data(self.data)
        except Exception:
            pass

    def quit(self):
        for w in self.notes.values():
            w.d["x"], w.d["y"] = w.winfo_x(), w.winfo_y()
        self.save_now()
        try:
            self.icon.stop()
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)

def main():
    ensure_single_instance()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().root.mainloop()

if __name__ == "__main__":
    main()
