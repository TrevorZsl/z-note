# -*- coding: utf-8 -*-
"""z-note 雏形：常驻托盘的桌面便签（自由文本 + 可勾选待办）。
运行: python main.py   依赖: pystray (pip install pystray)
"""
import ctypes, json, math, os, queue, sys, threading, uuid, winreg
import tkinter as tk
from tkinter import font as tkfont, messagebox
from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

APP_NAME = "z-note"
UI_FONT = "Times New Roman"
DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "z-note")
DATA_FILE = os.path.join(DATA_DIR, "notes.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

NOTE_BG, NOTE_BORDER = "#FFFFFF", "#DFE2E6"
T_COLOR = "#010203"   # 窗口透明色键（圆角外的部分透出去）
RADIUS = 20
EDGE = 7                              # 窗口边缘可拖拽调大小的宽度
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons")
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
user32.GetFocus.restype = wintypes.HWND

dwmapi = ctypes.WinDLL("dwmapi")
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

HWND_BOTTOM = wintypes.HWND(1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
WM_HOTKEY, MOD_ALT, MOD_CONTROL = 0x0312, 0x0001, 0x0002

class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

# 使用控件字体和光标位置显示 Windows 输入法预编辑文字。
imm32 = ctypes.WinDLL("imm32")
imm32.ImmGetContext.argtypes = [wintypes.HWND]
imm32.ImmGetContext.restype = wintypes.HANDLE
imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
imm32.ImmGetCompositionStringW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

user32.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(_POINT), wintypes.UINT]

class _CANDIDATEFORM(ctypes.Structure):
    _fields_ = [("index", wintypes.DWORD), ("style", wintypes.DWORD),
                ("point", _POINT), ("area", wintypes.RECT)]

imm32.ImmSetCandidateWindow.argtypes = [wintypes.HANDLE, ctypes.POINTER(_CANDIDATEFORM)]
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM]
user32.CallWindowProcW.restype = ctypes.c_ssize_t

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
                    "opacity": 1.0, "color": "白", "fontSize": 11}

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"schemaVersion": 2, "settings": dict(DEFAULT_SETTINGS), "notes": []}
    _rotate_backups()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"schemaVersion": 2, "settings": dict(DEFAULT_SETTINGS), "notes": []}

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
            Tooltip._font = tkfont.Font(family=UI_FONT, size=9, root=self._w)
        w, h = Tooltip._font.measure(text) + 24, 30
        tw = tk.Toplevel(self._w)
        tw.overrideredirect(True)
        tw.attributes("-transparentcolor", T_COLOR)
        tw.attributes("-topmost", True)
        cv = tk.Canvas(tw, bg=T_COLOR, highlightthickness=0, bd=0)
        cv.pack()
        round_rect(cv, 1, 1, w - 2, h - 2, 8, fill="#FFFFFF", outline="#D9DCE1")
        cv.create_text(w / 2, h / 2, text=text, fill="#3A3F46",
                       font=(UI_FONT, 9))
        x = max(4, self._w.winfo_rootx() + self._w.winfo_width() // 2 - w // 2)
        y = self._w.winfo_rooty() - h - 6
        if y < 0:
            y = self._w.winfo_rooty() + self._w.winfo_height() + 6
        tw.geometry(f"{w}x{h}+{x}+{y}")
        Tooltip._cur = tw

# ---------- 工具栏圆形按钮 ----------
def load_icon(master, name, size=22, active=False):
    state = "active" if active else "normal"
    with Image.open(os.path.join(ICON_DIR, f"{name}-{state}.png")) as image:
        return ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS),
                                  master=master)


class ToolButton(tk.Canvas):
    """圆形悬浮底 + Lucide 图标的扁平按钮；active 态仅加深图标。"""

    def __init__(self, master, icon, get_tip, cmd, bg, size=30):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._bg, self._active, self._cmd = bg, False, cmd
        self._image = load_icon(self, icon)
        self._active_image = load_icon(self, icon, active=True) if icon == "pin" else self._image
        r = 13
        self._pill = self.create_oval(size/2 - r, size/2 - r, size/2 + r, size/2 + r,
                                      state="hidden", outline="")
        self._icon = self.create_image(size/2, size/2 + 1, image=self._image)
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

    def set_active(self, on):
        self._active = on
        self.itemconfig(self._pill, state="hidden")
        if on:
            self.itemconfig(self._icon, image=self._active_image)
        else:
            self.itemconfig(self._icon, image=self._image)

# ---------- 便签窗口 ----------
class NoteWindow(tk.Toplevel):
    def __init__(self, app, data):
        super().__init__(app.root)
        self.app, self.d = app, data
        self._bg, self._border = NOTE_BG, NOTE_BORDER
        self.d["color"] = "白"
        self.configure(bg=self._bg)
        self.geometry(f"{data['w']}x{data['h']}+{data['x']}+{data['y']}")
        self.attributes("-alpha", data["opacity"])
        self.overrideredirect(True)

        # 画布画圆角底板，控件都放圆角内部
        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)

        # -- 工具栏 --
        self.toolbar = tk.Frame(self, bg=self._bg)
        self._tool_btns = []
        self._chip_font = tkfont.Font(family=UI_FONT, size=10, weight="bold")
        self._title_edit_font = self._chip_font

        def btn(icon, get_tip, cmd):
            b = ToolButton(self.toolbar, icon, get_tip, cmd, self._bg)
            self._tool_btns.append(b)
            return b

        self.b_pin = ToolButton(self.toolbar, "pin",
                                lambda: "取消置顶" if self.d["z"] == "top" else "置顶",
                                self.toggle_pin, self._bg)
        self._tool_btns.append(self.b_pin)
        self.b_pin.pack(side="left", padx=(3, 8), pady=9)

        # 标题胶囊：悬停有提示，点击改名
        self.title_box = tk.Frame(self.toolbar, bg=self._bg)
        self.title_box.pack(side="left", padx=2)
        self.title_chip = tk.Canvas(self.title_box, bg=self._bg,
                                    highlightthickness=0, cursor="hand2")
        self._title_editor = None
        self.title_chip.pack()
        self.title_chip.bind("<Button-1>", lambda e: self.edit_title())
        Tooltip(self.title_chip, lambda: "点击修改标题")

        spacer = tk.Label(self.toolbar, text="", bg=self._bg, cursor="fleur")
        spacer.pack(side="left", fill="both", expand=True)
        btn("minus", lambda: "隐藏这张便签", self.hide).pack(side="right", padx=(2, 8), pady=9)
        btn("plus", lambda: "新建便签（Ctrl+Alt+N）",
            lambda: self.app.new_note()).pack(side="right", padx=5, pady=9)
        btn("trash", lambda: "删除便签", self.delete_self).pack(side="right", padx=5, pady=9)
        for w in (self.toolbar, spacer):
            w.bind("<Button-1>", self._drag_press)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_release)

        # -- 正文 --
        self.done_font = tkfont.Font(family=UI_FONT,
                                     size=app.data["settings"]["fontSize"])
        self.text = tk.Text(self, bg=self._bg, fg="#2B2F36", bd=0, highlightthickness=0,
                            padx=12, pady=10, wrap="char", spacing1=9, spacing3=10,
                            selectbackground=self._bg, inactiveselectbackground=self._bg,
                            selectforeground="#2B2F36", selectborderwidth=0,
                            font=app.base_font, insertbackground="#2B2F36", cursor="xterm")
        self._make_check_images()
        self.text.tag_config("todo_indent", lmargin2=self._check_images[False].width() + app.base_font.measure(PREFIX_PAD))
        self.text.tag_config("done", font=self.done_font)  # 只加删除线，不改字色
        self.text.tag_config("selection_fill", background="#DCE7F3", foreground="#2B2F36")
        self.text.tag_raise("selection_fill", "sel")
        self.text.bind("<<Selection>>", self._paint_selection)

        # -- 底部状态栏 + 添加条 --
        self.bar = tk.Frame(self, bg=self._bg)
        self.status = tk.Label(self.bar, text="", bg=self._bg, fg=QUIET,
                               font=(UI_FONT, 9), padx=0, bd=0)
        self.clear_btn = tk.Label(self.bar, text="清除已完成", bg=self._bg,
                                  fg="#A9AFB6", font=(UI_FONT, 9),
                                  padx=0, bd=0, cursor="hand2")
        self.clear_btn.bind("<Enter>", lambda e: self.clear_btn.config(fg="#454B52"))
        self.clear_btn.bind("<Leave>", lambda e: self.update_status())
        self.clear_btn.bind("<Button-1>", lambda e: self.clear_done())
        Tooltip(self.clear_btn, lambda: "清除已完成")
        self.add_pill = tk.Canvas(self, bg=self._bg, highlightthickness=0, cursor="hand2")
        self.add_pill.bind("<Button-1>", self._pill_press)
        Tooltip(self.add_pill, lambda: "添加待办")
        self._strike_widgets = []
        self._selection_masks = []
        self._checkbox_widgets = []
        self.text.configure(yscrollcommand=lambda *args: self._queue_strikes())
        self.text.bind("<Configure>", lambda e: self._queue_strikes())
        self._bind_ime(self.text)
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
        t.bind("<Control-MouseWheel>", self._ctrl_wheel)
        self.bind("<FocusOut>", lambda e: self.sink_if_bottom())

        self.render()
        self.update_idletasks()
        self._relayout()
        self.hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        self._round_window()
        self._install_inline_ime()
        self.apply_z()
        self._restyle()
        if not (data.get("visible", True) and app.show_notes):
            self.withdraw()

    def _relayout(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50 or (w, h) == getattr(self, "_last_sz", None):
            return  # 尺寸没变（纯移动触发的 Configure）不重画
        self._last_sz = (w, h)
        self._draw_chip()
        self.cv.delete("all")
        self.cv.create_rectangle(0, 0, w - 1, h - 1, fill=self._bg, outline=self._border)
        ix = RADIUS + 4
        toolbar_h = max(48, self.title_chip.winfo_reqheight())
        text_y = toolbar_h + 12
        self.toolbar.place(x=ix, y=6, width=w - 2 * ix, height=toolbar_h)
        self.text.place(x=ix, y=text_y, width=w - 2 * ix,
                        height=max(20, h - text_y - 86))
        self._layout_footer()
        self._queue_strikes()

    def _layout_footer(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        edge = max(12, min(24, round(w * .06)))
        available = w - 2 * edge
        self.clear_btn.config(text="清除已完成")
        label_width = max(self.status.winfo_reqwidth(), self.clear_btn.winfo_reqwidth())
        if available - 2 * label_width - 20 < 64:
            self.clear_btn.config(text="清除完成")
            label_width = max(self.status.winfo_reqwidth(), self.clear_btn.winfo_reqwidth())
        gap = min(10, max(6, (available - 2 * label_width - 36) // 2))
        pill_width = min(112, max(36, available - 2 * label_width - 2 * gap))
        pill_height = min(max(36, self.title_chip.winfo_reqheight()), pill_width - 4)
        pill_x = (w - pill_width) // 2
        center_y = h - 10 - pill_height / 2
        self.bar.place(x=0, y=round(center_y - 13), width=w, height=26)
        self.status.place(x=pill_x / 2, y=13, anchor="center")
        self.clear_btn.place(x=(pill_x + pill_width + w) / 2, y=13, anchor="center")
        self.add_pill.place(x=pill_x, y=h - 10 - pill_height,
                            width=pill_width, height=pill_height)
        self._draw_pill(pill_width, pill_height)

    def destroy(self):
        if hasattr(self, "_ime_pump_job"):
            self.after_cancel(self._ime_pump_job)
            user32.SetWindowLongPtrW(self.winfo_id(), -4, self._ime_old_proc)
        super().destroy()

    def _round_window(self):
        # 由 DWM 抗锯齿裁切外框，避免透明色键的硬像素边缘。
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(self.hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))

    def _make_check_images(self):
        size = max(18, round(self.app.base_font.metrics("ascent") * 1.1))
        scale = 4
        self._check_images = {}
        for checked in (False, True):
            img = Image.new("RGBA", (size * scale, size * scale))
            draw = ImageDraw.Draw(img)
            color = "#596575" if checked else "#9EA8B5"
            draw.rounded_rectangle((scale, scale, (size - 1) * scale, (size - 1) * scale),
                                   radius=round(size * .25 * scale), outline=color,
                                   width=max(5, round(size * .09 * scale)))
            if checked:
                draw.line([(size * .27 * scale, size * .51 * scale),
                           (size * .44 * scale, size * .68 * scale),
                           (size * .75 * scale, size * .34 * scale)],
                          fill=color, width=max(5, round(size * .09 * scale)), joint="curve")
            self._check_images[checked] = ImageTk.PhotoImage(
                img.resize((size, size), Image.Resampling.LANCZOS), master=self.text)
        self._check_slots = {checked: ImageTk.PhotoImage(Image.new("RGBA", (size, size)), master=self.text)
                             for checked in (False, True)}
        if hasattr(self, "_checkbox_widgets"):
            self.text.tag_config("todo_indent", lmargin2=size + self.app.base_font.measure(PREFIX_PAD))

    def _restyle(self):
        for b in self._tool_btns:
            b.set_bg(self._bg)
        self.b_pin.set_active(self.d["z"] == "top")

    def toggle_pin(self):
        self.set_z("top" if self.d["z"] == "bottom" else "bottom")

    def _ime_composing(self, widget):
        if getattr(self, "_ime_native_widget", None) == widget:
            return True
        if widget == self.text and hasattr(self, "_ime_pending") and not self._ime_pending.empty():
            return True
        if getattr(self, "_ime_active", False) and self._ime_widget == widget:
            return True
        hwnd = widget.winfo_id()
        context = imm32.ImmGetContext(hwnd)
        if not context:
            return False
        try:
            return imm32.ImmGetCompositionStringW(context, 8, None, 0) > 0
        finally:
            imm32.ImmReleaseContext(hwnd, context)

    def _install_inline_ime(self):
        self._ime_active = False
        self._ime_widget = self._ime_target = self._ime_native_widget = None
        self._ime_pending = queue.SimpleQueue()
        self.text.tag_configure("ime_preedit", underline=True)
        hwnd = self.winfo_id()  # Tk 将原生输入焦点保留在顶层客户窗口。

        @_WNDPROC
        def window_proc(handle, message, wparam, lparam):
            # 原生消息可能嵌套在 Tcl 调用中；这里只读 IMM 数据，不能调用 Tk。
            try:
                if message == 0x0281 and self._ime_target == self.text:  # 正文使用行内预编辑。
                    lparam &= ~0x80000000
                elif message == 0x010D and self._ime_target is not None:
                    self._ime_native_widget = self._ime_target
                    self._ime_pending.put(("start", self._ime_target, None, None))
                    return 0
                elif message == 0x010F and self._ime_native_widget is not None:
                    widget = self._ime_native_widget
                    context = imm32.ImmGetContext(handle)
                    try:
                        if lparam & 0x0800:  # GCS_RESULTSTR
                            self._ime_pending.put(("result", widget, self._ime_string(context, 0x0800), None))
                        if lparam & 0x0008:  # GCS_COMPSTR
                            value = self._ime_string(context, 0x0008)
                            cursor = imm32.ImmGetCompositionStringW(context, 0x0080, None, 0)
                            self._ime_pending.put(("update", widget, value, cursor))
                        elif not lparam:
                            self._ime_pending.put(("end", widget, None, None))
                    finally:
                        imm32.ImmReleaseContext(handle, context)
                    return 0
                elif message == 0x010E and self._ime_native_widget is not None:
                    self._ime_pending.put(("end", self._ime_native_widget, None, None))
                    self._ime_native_widget = None
                    return 0
                elif message == 0x0082:  # WM_NCDESTROY
                    user32.SetWindowLongPtrW(handle, -4, self._ime_old_proc)
            except Exception:
                self._ime_pending.put(("error", None, sys.exc_info(), None))
            return user32.CallWindowProcW(self._ime_old_proc, handle, message, wparam, lparam)

        self._ime_proc = window_proc  # ctypes 回调必须与窗口保持相同生命周期。
        self._ime_old_proc = user32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(window_proc, ctypes.c_void_p))
        self._ime_pump_job = self.after(8, self._pump_inline_ime)

    def _pump_inline_ime(self):
        while not self._ime_pending.empty():
            kind, widget, value, cursor = self._ime_pending.get()
            if kind == "error":
                self.app.root.report_callback_exception(*value)
                continue
            if not widget.winfo_exists():
                continue
            if kind == "start":
                self._begin_preedit(widget)
            elif kind == "update":
                if value or self._ime_active:
                    self._begin_preedit(widget)
                    self._replace_preedit(value, cursor)
            elif kind == "result":
                self._begin_preedit(widget)
                self._finish_preedit(value)
            elif kind == "end":
                self._finish_preedit()
        self._ime_pump_job = self.after(8, self._pump_inline_ime)

    @staticmethod
    def _ime_string(context, flag):
        size = imm32.ImmGetCompositionStringW(context, flag, None, 0)
        if size <= 0:
            return ""
        buffer = ctypes.create_string_buffer(size)
        imm32.ImmGetCompositionStringW(context, flag, buffer, size)
        return buffer.raw.decode("utf-16-le")

    def _begin_preedit(self, widget=None):
        widget = widget or self.focus_get()
        if widget != self.text and not isinstance(widget, tk.Entry):
            return False
        if self._ime_active:
            return True
        self._ime_widget = widget
        self._ime_selected = ""
        self._ime_selected_images = []
        if widget == self.text:
            selection = widget.tag_ranges("sel")
            if selection:
                widget.mark_set("insert", selection[0])
                self._ime_selected = widget.get(*selection)
                for kind, name, index in widget.dump(*selection, image=True):
                    distance = widget.count(selection[0], index, "indices")
                    self._ime_selected_images.append((distance[0] if distance else 0,
                                                      widget.image_cget(index, "image")))
                widget.delete(*selection)
            self._ime_start = widget.index("insert")
        else:
            if widget.selection_present():
                start, end = widget.index("sel.first"), widget.index("sel.last")
                self._ime_selected = widget.get()[start:end]
                widget.delete(start, end)
                widget.icursor(start)
            self._ime_start = widget.index("insert")
        self._ime_length = 0
        self._ime_active = True
        return True

    def _replace_preedit(self, value, cursor=None):
        widget, start = self._ime_widget, self._ime_start
        length = int(widget.tk.call("string", "length", value))
        cursor = length if cursor is None else max(0, min(cursor, length))
        if widget == self.text:
            widget.delete(start, f"{start}+{self._ime_length}c")
            widget.insert(start, value, ("ime_preedit",))
            widget.mark_set("insert", f"{start}+{cursor}c")
            widget.see("insert")
            self._queue_strikes()
        else:
            widget.delete(start, start + self._ime_length)
            widget.insert(start, value)
            widget.icursor(start + cursor)
            self._draw_chip()
        self._ime_length = length
        widget.after_idle(self._sync_ime, widget)

    def _finish_preedit(self, result=None):
        if not self._ime_active:
            return
        widget = self._ime_widget
        self._replace_preedit(self._ime_selected if result is None else result)
        if widget == self.text:
            if result is None:
                for offset, image in self._ime_selected_images:
                    widget.image_create(f"{self._ime_start}+{offset}c", image=image, align="center")
                widget.mark_set("insert", f"{self._ime_start}+{self._ime_length + len(self._ime_selected_images)}c")
            widget.tag_remove("ime_preedit", "1.0", "end")
        self._ime_active = False
        self._ime_widget = None
        if widget == self.text:
            self.after_idle(self._on_keyrelease, None)
        elif self.focus_get() != widget:
            self.after_idle(lambda: widget.event_generate("<FocusOut>") if widget.winfo_exists() else None)

    def _sync_ime(self, widget):
        if not widget.winfo_exists():
            return
        hwnd = widget.winfo_id()
        context = imm32.ImmGetContext(hwnd)
        if not context:
            return
        try:
            box = widget.bbox("insert")
            if box:
                point = _POINT(box[0], box[1])
                focus = user32.GetFocus() or hwnd
                user32.MapWindowPoints(hwnd, focus, ctypes.byref(point), 1)
                area = wintypes.RECT(point.x, point.y, point.x + box[2], point.y + box[3])
                form = _CANDIDATEFORM(0, 0x0040, _POINT(point.x, area.bottom + 6), area)
                imm32.ImmSetCandidateWindow(context, ctypes.byref(form))
        finally:
            imm32.ImmReleaseContext(hwnd, context)

    def _bind_ime(self, widget):
        # 焦点状态由 Tk 事件更新，原生输入法回调不进入 Tcl。
        def focus_in(_event):
            self._ime_target = widget
            widget.after_idle(self._sync_ime, widget)
        def focus_out(_event):
            if self._ime_target == widget:
                self._ime_target = None
        widget.bind("<FocusIn>", focus_in, add="+")
        widget.bind("<FocusOut>", focus_out, add="+")
        widget.bind("<Destroy>", focus_out, add="+")
        for event in ("<KeyPress>", "<ButtonRelease-1>"):
            widget.bind(event, lambda e: widget.after_idle(self._sync_ime, widget), add="+")

    def _queue_strikes(self):
        if getattr(self, "_strike_job", None):
            self.after_cancel(self._strike_job)
        self._strike_job = self.after_idle(self._draw_strikes)

    def _draw_strikes(self):
        self._strike_job = None
        for widget in self._strike_widgets:
            widget.destroy()
        self._strike_widgets.clear()
        for widget in self._selection_masks:
            widget.destroy()
        self._selection_masks.clear()
        for widget in self._checkbox_widgets:
            widget.destroy()
        self._checkbox_widgets.clear()
        for row, line in enumerate(self.d["lines"], 1):
            if line["type"] == "todo":
                # Tk 会把软换行的选区背景画进左边距，盖回勾选框旁的留白。
                if self.text.tag_nextrange("selection_fill", f"{row}.0", f"{row}.end"):
                    start, top, bottom = f"{row}.0", None, None
                    while self.text.compare(start, "<=", f"{row}.end"):
                        info = self.text.dlineinfo(start)
                        if info:
                            top = info[1] if top is None else min(top, info[1])
                            bottom = info[1] + info[3] if bottom is None else max(bottom, info[1] + info[3])
                        next_start = self.text.index(f"{start} +1 display lines display linestart")
                        if self.text.compare(next_start, "<=", start):
                            break
                        start = next_start
                    if top is not None:
                        mask = tk.Frame(self.text, bg=self._bg)
                        mask.place(x=int(self.text.cget("padx")), y=top,
                                   width=int(self.text.tag_cget("todo_indent", "lmargin2")),
                                   height=bottom - top, bordermode="ignore")
                        mask.lift()
                        for event in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>"):
                            mask.bind(event, lambda e, sequence=event: self.text.event_generate(
                                sequence, x=e.x_root - self.text.winfo_rootx(),
                                y=e.y_root - self.text.winfo_rooty(), time=e.time, state=e.state))
                        self._selection_masks.append(mask)
                size = self._check_images[False].width()
                count = self.text.count(f"{row}.0", f"{row}.end", "displaylines")
                middle = (self.text.dlineinfo(f"{row}.0 +{(count[0] + 1) // 2} display lines")
                          if count else self.text.dlineinfo(f"{row}.0"))
                y = (middle[1] + (0 if count and count[0] % 2 else middle[3] // 2) - size // 2
                     if middle else None)
                if y is not None and -size < y < self.text.winfo_height():
                    box = tk.Label(self.text, image=self._check_images[line["checked"]],
                                   bg=self._bg, bd=0, cursor="hand2")
                    box.place(x=int(self.text.cget("padx")), y=y, width=size, height=size,
                              bordermode="ignore")
                    box.lift()
                    box.bind("<Button-1>", lambda _e, r=row-1: self.toggle_done(r))
                    self._checkbox_widgets.append(box)
            if line["type"] != "todo" or not line["checked"]:
                continue
            start = f"{row}.{self._prefix_len(line)}"
            end = f"{row}.end"
            while self.text.compare(start, "<", end):
                stop = self.text.index(f"{start} display lineend")
                if self.text.compare(stop, ">", end):
                    stop = self.text.index(end)
                first = self.text.bbox(start)
                last = self.text.bbox(f"{stop}-1c") if self.text.compare(stop, ">", start) else None
                if first and last:
                    thickness = max(2, round(self.done_font.metrics("ascent") * 0.12))
                    bar = tk.Frame(self.text, bg="#505761", height=thickness, cursor="xterm")
                    display = self.text.dlineinfo(start)
                    baseline = display[1] + display[4]
                    middle = baseline - round(self.done_font.metrics("ascent") * 0.33)
                    bar.place(x=first[0], y=middle - thickness // 2,
                              width=max(1, last[0] + last[2] - first[0]),
                              height=thickness, bordermode="ignore")
                    for event in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>"):
                        bar.bind(event, lambda e, sequence=event: self.text.event_generate(
                            sequence, x=e.x_root - self.text.winfo_rootx(),
                            y=e.y_root - self.text.winfo_rooty(), time=e.time, state=e.state))
                    self._strike_widgets.append(bar)
                next_start = self.text.index(f"{start} +1 display lines display linestart")
                if self.text.compare(next_start, "<=", start):
                    break
                start = next_start

        if self._selection_masks:
            self.after_idle(lambda: [w.lift() for w in self._selection_masks + self._checkbox_widgets
                                     if w.winfo_exists()])

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
        self._draw_pill(pressed=True)
        self.add_pill.after(120, self._draw_pill)
        self.add_todo()

    def _draw_pill(self, width=None, height=None, pressed=False):
        c = self.add_pill
        w = width or c.winfo_width()
        h = height or c.winfo_height()
        if w < 30 or h < 30:
            return
        # 放大绘制再缩小，让边框和圆角都有抗锯齿。
        scale = 4
        img = Image.new("RGB", (w * scale, h * scale), self._bg)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((scale, scale, (w - 2) * scale, (h - 1) * scale),
                               radius=11 * scale,
                               fill="#EFF1F3" if pressed else "#FFFFFF",
                               outline="#2B2F36", width=round(2.5 * scale))
        self._pill_image = ImageTk.PhotoImage(
            img.resize((w, h), Image.Resampling.LANCZOS), master=c)
        c.delete("all")
        c.create_image(0, 0, anchor="nw", image=self._pill_image)
        self._add_icon = load_icon(c, "plus", size=26)
        c.create_image(w / 2, h / 2, image=self._add_icon)

    # ---- 标题胶囊 ----
    def _title_max_width(self):
        reserved = 8  # 标题两侧间距和与操作按钮之间的留白。
        for button in self._tool_btns:
            padding = button.pack_info()["padx"]
            padding = (padding, padding) if isinstance(padding, int) else self.tk.splitlist(padding)
            reserved += button.winfo_reqwidth() + sum(int(value) for value in padding)
        return max(44, self.winfo_width() - 2 * (RADIUS + 4) - reserved)

    def _draw_chip(self):
        c = self.title_chip
        c.delete("all")
        editing = self._title_editor is not None and self._title_editor.winfo_exists()
        title = self._title_var.get() if editing else self.d.get("title", "")
        text = title or "无标题"
        font = self._title_edit_font if editing else self._chip_font
        natural_width = font.measure(text) + 36
        w = min(self._title_max_width(), max(44, natural_width))
        h = max(28, font.metrics("linespace") + 12)
        c.config(width=w, height=h)
        self.title_chip.pack_configure(pady=max(0, (44 - h) // 2))
        round_rect(c, 1, 1, w - 1, h - 1, min(11, h // 2),
                   fill="#FFFFFF", outline="#E2E4E8")
        if editing:
            e = self._title_editor
            e.place(x=14, y=5, width=max(20, w - 28), height=h - 10)
            if natural_width <= w:
                e.xview_moveto(0)
            elif e.index("insert") == e.index("end"):
                e.xview_moveto(1)
            return
        if self._chip_font.measure(text) > w - 36:
            while text and self._chip_font.measure(text + "…") > w - 36:
                text = text[:-1]
            text += "…"
        # 边框对称内缩、文字落在几何中心；不要再按字形留白手工偏移，
        # 旧字体时代的 -1/-2 校正在 Times New Roman 下会把文字顶出上边。
        c.create_text(w / 2, h / 2, text=text, font=self._chip_font,
                      fill="#1F2328" if title else "#A9AFB6")

    def edit_title(self):
        if any(isinstance(child, tk.Entry) for child in self.title_chip.winfo_children()):
            return
        Tooltip._hide()
        self.title_chip.pack_forget()
        self._title_var = tk.StringVar(master=self, value=self.d.get("title", ""))
        e = tk.Entry(self.title_chip, bd=0, relief="flat",
                     highlightthickness=0, bg="#FFFFFF", fg="#1F2328",
                     insertbackground="#1F2328", selectbackground="#E2E8F0",
                     selectforeground="#1F2328", font=self._title_edit_font, justify="center",
                     textvariable=self._title_var)
        self._title_editor = e
        self._title_var.trace_add("write", lambda *_: self._draw_chip())
        self.title_chip.pack()
        self._draw_chip()
        e.focus_force()
        e.select_range(0, "end")

        def commit(_e=None):
            if not e.winfo_exists() or self._ime_composing(e):
                return
            self.d["title"] = e.get().strip()
            self.app.schedule_save()
            e.destroy()
            self._title_editor = None
            self._draw_chip()

        e.bind("<Return>", commit)
        e.bind("<Escape>", commit)
        e.bind("<FocusOut>", commit, add="+")

    # ---- 渲染 ----
    def render(self, keep_cursor=True):
        cur = self.text.index("insert")
        self.text.delete("1.0", "end")
        lines = self.d["lines"] or [{"type": "text", "text": "", "checked": False}]
        self.d["lines"] = lines
        for i, ln in enumerate(lines):
            row = i + 1
            if ln["type"] == "todo":
                self.text.image_create("end", image=self._check_slots[ln["checked"]], align="center")
                self.text.insert("end", PREFIX_PAD)
            self.text.insert("end", ln["text"])
            if ln["type"] == "todo":
                self.text.tag_add("todo_indent", f"{row}.0", f"{row}.end")
            if ln["type"] == "todo" and ln["checked"]:
                self.text.tag_add("done", f"{row}.{self._prefix_len(ln)}", f"{row}.end")
            if i < len(lines) - 1:
                self.text.insert("end", "\n")
        if keep_cursor:
            end = self.text.index("end-1c")
            if self.text.compare(cur, ">", end):
                cur = end
            self.text.mark_set("insert", cur)
        self.update_status()
        self._queue_strikes()

    @staticmethod
    def _prefix_len(ln):
        return 1 + len(PREFIX_PAD) if ln["type"] == "todo" else 0

    def _row_col(self, index):
        r, c = index.split(".")
        return int(r) - 1, int(c)

    def update_status(self):
        todos = [l for l in self.d["lines"] if l["type"] == "todo"]
        done = sum(1 for l in todos if l["checked"])
        self.status.config(text=f"待办 {done}/{len(todos)}" if todos else "")
        self.clear_btn.config(fg="#6F7780" if done else "#A9AFB6")
        self._layout_footer()

    # ---- 编辑事件 ----
    def _paint_selection(self, _event=None):
        # 原生选区保留复制/删除语义；背景只画文字，避开勾选框和换行后的空白。
        t = self.text
        t.tag_remove("selection_fill", "1.0", "end")
        selected = t.tag_ranges("sel")
        self._queue_strikes()
        if not selected:
            return
        first, last = selected
        for row in range(int(str(first).split(".")[0]), int(str(last).split(".")[0]) + 1):
            start = first if t.compare(first, ">", f"{row}.0") else f"{row}.0"
            if row <= len(self.d["lines"]) and self.d["lines"][row-1]["type"] == "todo":
                text_start = f"{row}.{self._prefix_len(self.d['lines'][row-1])}"
                if t.compare(start, "<", text_start):
                    start = text_start
            end = last if t.compare(last, "<", f"{row}.end") else f"{row}.end"
            if t.compare(start, "<", end):
                t.tag_add("selection_fill", start, end)

    def _sync_lines_from_text(self):
        # 选区删除和粘贴都可能改变多行，不能只保存光标所在行。
        t = self.text
        images = {index: t.image_cget(index, "image")
                  for kind, name, index in t.dump("1.0", "end-1c", image=True)}
        checkbox_images = {str(image) for image in self._check_slots.values()}
        lines = []
        for row in range(1, int(t.index("end-1c").split(".")[0]) + 1):
            image = images.get(f"{row}.0")
            todo = image in checkbox_images
            content = t.get(f"{row}.0", f"{row}.end")
            if todo and content.startswith(PREFIX_PAD):
                content = content[len(PREFIX_PAD):]
            lines.append({"type": "todo" if todo else "text", "text": content,
                          "checked": todo and image == str(self._check_slots[True])})
        old = self.d["lines"]
        self.d["lines"] = lines
        structure_changed = (len(old) != len(lines) or
                             any(a["type"] != b["type"] for a, b in zip(old, lines)))
        if structure_changed:
            self.render()
        else:
            for row, line in enumerate(lines, 1):
                if line["type"] == "todo":
                    t.tag_add("todo_indent", f"{row}.0", f"{row}.end")
            self.update_status()

    def _on_keyrelease(self, _e):
        if self._ime_composing(self.text):
            return
        self._queue_strikes()
        self._sync_lines_from_text()
        self.app.schedule_save()

    def _on_enter(self, _e):
        if self._ime_composing(self.text):
            return
        if self.text.tag_ranges("sel"):
            self.text.delete("sel.first", "sel.last")
        self._sync_lines_from_text()
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
        if self._ime_composing(self.text):
            return
        if self.text.tag_ranges("sel"):
            return None  # 交给 Text 默认绑定删除选区，随后统一同步所有行。
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
        if pre and col < pre:  # 点在勾选框上
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
        self.render()
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
        step = 5 if e.delta > 0 else -5
        percent = round(self.app.data["settings"]["opacity"] * 100)
        self.app.set_opacity(min(100, max(10, percent + step)))
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

    def hide(self):
        self.d["visible"] = False
        self.withdraw()
        self.app.schedule_save()

    def show(self):
        self.d["visible"] = True
        self.deiconify()
        self.overrideredirect(True)
        self._round_window()
        self.apply_z()

    def delete_self(self):
        # 不用原生 messagebox：overrideredirect 父窗口会让原生模态框收不到点击
        p = tk.Toplevel(self)
        p.withdraw()
        p.overrideredirect(True)
        p.configure(bg=T_COLOR)
        p.attributes("-transparentcolor", T_COLOR)
        p.attributes("-topmost", True)
        title_font = tkfont.Font(family=UI_FONT, size=10)
        body_font = tkfont.Font(family=UI_FONT, size=9)
        W = max(268, title_font.measure("删除便签") + 40)
        cv = tk.Canvas(p, bg=T_COLOR, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        title = cv.create_text(W / 2, 16, anchor="n", text="删除便签", fill="#1F2328",
                               font=title_font)
        body = cv.create_text(W / 2, cv.bbox(title)[3] + 12, anchor="n", width=W - 32,
                              justify="center", text="删除后无法恢复，确定删除这张便签吗？",
                              fill="#666D75", font=body_font, tags="message")
        button_h = max(30, body_font.metrics("linespace") + 10)
        H = cv.bbox(body)[3] + 16 + button_h + 16
        cv.config(width=W, height=H)
        background = round_rect(cv, 1, 1, W - 2, H - 2, 12,
                                fill="#FFFFFF", outline="#D9DCE1")
        cv.tag_lower(background)
        bx, by = W - 16, H - 16
        round_rect(cv, bx - 76, by - button_h, bx, by, 8,
                   fill="#E5484D", outline="", tags="confirm")
        cv.create_text(bx - 38, by - button_h / 2, text="删除", fill="#FFFFFF",
                       font=(UI_FONT, 9), tags="confirm")
        round_rect(cv, bx - 164, by - button_h, bx - 88, by, 8,
                   fill="#F2F3F5", outline="#D9DCE1", tags="cancel")
        cv.create_text(bx - 126, by - button_h / 2, text="取消", fill="#3A3F46",
                       font=body_font, tags="cancel")

        def cancel(_e=None):
            p.grab_release()
            p.destroy()

        def confirm(_e=None):
            cancel()
            self.app.remove_note(self)

        cv.tag_bind("confirm", "<Button-1>", confirm)
        cv.tag_bind("cancel", "<Button-1>", cancel)
        p.bind("<Escape>", cancel)
        p.update_idletasks()
        x = max(0, self.winfo_rootx() + (self.winfo_width() - W) // 2)
        y = max(0, self.winfo_rooty() + (self.winfo_height() - H) // 2)
        p.geometry(f"{W}x{H}+{x}+{y}")
        p.deiconify()
        p.grab_set()
        p.focus_set()
        p.bind("<FocusOut>", cancel)

# ---------- 设置窗口 ----------
class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("z-note 设置")
        # 不能 transient 到常驻隐藏的 root：Windows 上宿主隐藏时 transient 窗口永远不显示
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
        tk.Label(row, text="所有便签不透明度", bg="#FAFAF8").pack(side="left")
        s = tk.Scale(row, from_=10, to=100, resolution=5, orient="horizontal", length=150,
                     bg="#FAFAF8", highlightthickness=0, troughcolor="#E8E4DD",
                     command=self._op)
        app.opacity_scale = s
        s.set(int(app.data["settings"]["opacity"] * 100))
        s.pack(side="right")

        row3 = tk.Frame(f, bg="#FAFAF8"); row3.pack(fill="x", pady=(8, 0))
        tk.Label(row3, text="字号", bg="#FAFAF8").pack(side="left")
        s2 = tk.Scale(row3, from_=9, to=16, orient="horizontal", length=150,
                      bg="#FAFAF8", highlightthickness=0, troughcolor="#E8E4DD",
                      command=self._font)
        s2.set(app.data["settings"]["fontSize"])
        s2.pack(side="right")

        tk.Label(f, text="全局快捷键：Ctrl+Alt+N 新建便签 · Ctrl+Alt+H 显示/隐藏全部\n数据：%s"
                 % DATA_FILE, bg="#FAFAF8", fg="#999",
                 font=(UI_FONT, 9), justify="left").pack(anchor="w", pady=(14, 0))

    def _toggle_boot(self):
        if set_autostart(self.v_boot.get()):
            self.app.data["settings"]["autostart"] = self.v_boot.get()
        else:
            self.v_boot.set(False)
            messagebox.showwarning("z-note", "写入开机自启失败。", parent=self)
        self._save()

    def _op(self, v):
        self.app.set_opacity(int(float(v)))

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
        font = ImageFont.truetype("times.ttf", 20)
    except OSError:
        try:
            font = ImageFont.load_default(20)
        except TypeError:
            font = ImageFont.load_default()
    d.text((16, 15), "Z", fill=(70, 58, 20, 255), font=font, anchor="mm")
    return img

class App:
    def __init__(self):
        self.data = load_data()
        migrate_opacity = self.data.get("schemaVersion", 1) < 2
        if migrate_opacity:
            self.data["schemaVersion"] = 2
            self.data["settings"]["opacity"] = 1.0
            for note in self.data["notes"]:
                note["opacity"] = 1.0
        self.q = queue.Queue()
        self.root = tk.Tk()
        # 钉死 tk scaling：首次运行记录系统 DPI，此后不再跟随系统缩放变化，画面始终一致。
        # 想重新适配当前缩放，删掉 %APPDATA%/z-note/notes.json 里的 uiDpi 即可。
        dpi = self.data["settings"].get("uiDpi")
        if not dpi:
            dpi = round(self.root.winfo_fpixels("1i"))
            self.data["settings"]["uiDpi"] = dpi
            save_data(self.data)
        self.root.tk.call("tk", "scaling", dpi / 72.0)
        self.root.withdraw()
        for name in ("TkDefaultFont", "TkMenuFont"):
            tkfont.nametofont(name, root=self.root).configure(family=UI_FONT)
        self.notes = {}
        self._save_job = None
        self._cascade = 0
        self.settings_win = None
        self.show_notes = self.data["settings"]["showOnStartup"]

        self.base_font = tkfont.Font(family=UI_FONT,
                                     size=self.data["settings"]["fontSize"])

        self._restore_notes()
        if migrate_opacity:
            self.save_now()
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
            w._make_check_images()
            w.render()

    def set_opacity(self, percent):
        value = percent / 100
        self.data["settings"]["opacity"] = value
        for note in self.notes.values():
            note.d["opacity"] = value
            note.attributes("-alpha", value)
        scale = getattr(self, "opacity_scale", None)
        if scale is not None and scale.winfo_exists() and scale.get() != percent:
            scale.set(percent)
        self.schedule_save()

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
                        self.settings_win.deiconify()
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
