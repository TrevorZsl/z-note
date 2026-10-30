# -*- coding: utf-8 -*-
"""z-note 雏形：常驻托盘的桌面便签（自由文本 + 可勾选待办）。
运行: python main.py   依赖: pystray (pip install pystray)
"""
import atexit, ctypes, json, math, os, queue, sys, threading, time, uuid, winreg
from copy import deepcopy
from datetime import datetime
import tkinter as tk
from _tkinter import DONT_WAIT, IDLE_EVENTS, WINDOW_EVENTS
from tkinter import font as tkfont, messagebox
from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

APP_NAME = "z-note"
UI_FONT = "Times New Roman"
DEV_TAKEOVER_EVENT = "z-note-dev-takeover"
DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "z-note")
DATA_FILE = os.path.join(DATA_DIR, "notes.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backup")

NOTE_BG, NOTE_BORDER = "#FFFFFF", "#DFE2E6"
T_COLOR = "#010203"   # 窗口透明色键（圆角外的部分透出去）
RADIUS = 20
EDGE = 7                              # 窗口边缘可拖拽调大小的宽度
CORNER = RADIUS + 4                   # 四角斜向拉伸热区：整个圆角区都能触发，比 EDGE 好按
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons")
PREFIX_PAD = "  "                       # 勾选框后跟 2 个空格
INK, QUIET = "#6B7280", "#666D75"      # 图标与状态栏的墨色（冷灰单色系）

# ---------- Win32 ----------
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.HANDLE, wintypes.UINT]
user32.RedrawWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetFocus.restype = wintypes.HWND

# 小号拖动/缩放光标：Tk 每次指针更新（含 250ms 轮询）都会无条件把部件光标重设回系统标准尺寸
# 且绕过拦截，无法在应用内定点替换；改为指针悬停拖动区时临时把系统 move + 四向缩放光标
# 整体换成 75% 等比版——Tk 缓存的共享句柄会直接渲染替换后的图像，离开拖动区即整体恢复
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 提前到模块级：光标尺寸要按真实 DPI 取
except Exception:
    pass
user32.LoadCursorW.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.CopyImage.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.CopyImage.restype = wintypes.HANDLE
user32.SetSystemCursor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
user32.SetSystemCursor.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
SPI_SETICURSORS = 0x0057
SM_CX, SM_CY = 13, 14                                   # SM_CXCURSOR / SM_CYCURSOR
user32.SystemParametersInfoW(SPI_SETICURSORS, 0, None, 3)   # 自愈上次异常退出可能残留的替换
_SWAP_IDS = (32646, 32644, 32645, 32642, 32643)   # move / 左右 / 上下 / ↖↘ / ↗↙
MOVE_SIZE = max(12, user32.GetSystemMetrics(SM_CX) * 3 // 4)
_HCUR_SMALL = {oid: user32.CopyImage(user32.LoadCursorW(None, wintypes.HANDLE(oid)), 2, MOVE_SIZE, MOVE_SIZE, 0) for oid in _SWAP_IDS}
_HCUR_ORIG = {oid: user32.CopyImage(user32.LoadCursorW(None, wintypes.HANDLE(oid)), 2,
                                    user32.GetSystemMetrics(SM_CX), user32.GetSystemMetrics(SM_CY), 0)
              for oid in _SWAP_IDS}
_OUR_HANDLES = {user32.LoadCursorW(None, wintypes.HANDLE(oid)) for oid in _SWAP_IDS}

_DRAG_HWNS = set()                  # 需要小号光标的拖动区控件句柄
_watch_root = None                  # 看门狗锚点（Tk root），全局只跑一个
_CURSOR_SWAPPED = False             # 五个系统光标当前是否处于替换态

class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]

class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long), ("bmPlanes", ctypes.c_ushort),
                ("bmBitsPixel", ctypes.c_ushort), ("bmBits", ctypes.c_void_p)]

class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]

gdi32 = ctypes.WinDLL("gdi32")
gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
user32.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND

def _pointer_on_drag():
    """指针是否位于拖动区上（用 WindowFromPoint 命中测试；Tk 的指针状态在轮询时会瞬时出错）"""
    pt = wintypes.POINT()
    return bool(user32.GetCursorPos(ctypes.byref(pt))) and user32.WindowFromPoint(pt) in _DRAG_HWNS

def _cursor_is_small():
    ci = _CURSORINFO()
    ci.cbSize = ctypes.sizeof(_CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)) or not ci.hCursor:
        return True  # 查询失败时不动，避免误替换
    ii = _ICONINFO()
    if not user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
        return True
    bm = _BITMAP()
    gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(_BITMAP), ctypes.byref(bm))
    gdi32.DeleteObject(ii.hbmMask)
    gdi32.DeleteObject(ii.hbmColor)
    return (bm.bmWidth, bm.bmHeight) == (MOVE_SIZE, MOVE_SIZE)

def _drag_in_progress():
    """按住左键且显示的是我们替换过的光标 => 正在拖动/缩放便签，指针出界也不恢复"""
    ci = _CURSORINFO()
    ci.cbSize = ctypes.sizeof(_CURSORINFO)
    return (user32.GetKeyState(0x01) < 0
            and bool(user32.GetCursorInfo(ctypes.byref(ci))) and ci.hCursor in _OUR_HANDLES)

def _cursor_watch(root):
    """兜底看门狗：Tk 的 250ms 指针轮询会偶发恢复标准光标，30ms 内纠正回小号"""
    global _watch_root, _CURSOR_SWAPPED
    try:
        if _pointer_on_drag():
            if not _cursor_is_small():
                _cursor_small()
        elif _CURSOR_SWAPPED and not _drag_in_progress():
            _cursor_restore()
        root.after(30, lambda: _cursor_watch(root))
    except Exception:
        _watch_root = None  # root 已销毁，停止轮询

def _hook_move_cursor(widget):
    global _watch_root
    _DRAG_HWNS.add(widget.winfo_id())
    if _watch_root is None:
        _watch_root = widget._root()
        widget.after(30, lambda: _cursor_watch(_watch_root))

def _cursor_small(_e=None):
    """指针进入拖动区：五个系统光标临时整体替换为小号（SetSystemCursor 会吃掉句柄，故每次复制母本）"""
    global _CURSOR_SWAPPED
    for oid in _SWAP_IDS:
        user32.SetSystemCursor(user32.CopyImage(_HCUR_SMALL[oid], 2, MOVE_SIZE, MOVE_SIZE, 0), oid)
    _CURSOR_SWAPPED = True

def _cursor_restore(_e=None):
    """指针离开拖动区：五个系统光标恢复标准尺寸。
    Tk 的指针轮询会对静止指针发虚假 Leave，拖动中指针出界也不恢复，先确认再动手。"""
    global _CURSOR_SWAPPED
    if _e is not None and (_pointer_on_drag() or _drag_in_progress()):
        _cursor_small()
        return
    for oid in _SWAP_IDS:
        user32.SetSystemCursor(user32.CopyImage(_HCUR_ORIG[oid], 2, user32.GetSystemMetrics(SM_CX),
                                                user32.GetSystemMetrics(SM_CY), 0), oid)
    _CURSOR_SWAPPED = False

atexit.register(_cursor_restore)   # 退出时若指针仍悬停在拖动区，Leave 不会再触发，这里兜底恢复

dwmapi = ctypes.WinDLL("dwmapi")
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
WM_HOTKEY = 0x0312
HOTKEY_ACTIONS = {"new": (1, "newHotkey", "Ctrl+Alt+N"),
                  "hide": (2, "hideHotkey", "Ctrl+Alt+H"),
                  "show": (3, "showHotkey", "Ctrl+Alt+S")}
HOTKEY_KEYS = {**{chr(k): k for k in range(65, 91)},
               **{str(k): 48 + k for k in range(10)},
               **{f"F{k}": 111 + k for k in range(1, 25)},
               "Space": 32, "Tab": 9, "Enter": 13, "Backspace": 8,
               "Insert": 45, "Delete": 46, "Home": 36, "End": 35,
               "PgUp": 33, "PgDn": 34, "Left": 37, "Up": 38, "Right": 39, "Down": 40}

def parse_hotkey(value):
    parts = value.split("+")
    modifiers = sum({"Ctrl": 2, "Alt": 1, "Shift": 4}[part] for part in parts[:-1])
    return modifiers, HOTKEY_KEYS[parts[-1]]

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
        if os.getenv("Z_NOTE_DEV"):
            event = kernel32.OpenEventW(0x0002, False, DEV_TAKEOVER_EVENT)
            if event:
                kernel32.SetEvent(event)
                kernel32.CloseHandle(event)
            os._exit(75 if event else 76)
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


def check_images(master, size):
    images = {}
    scale = 4
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
        images[checked] = ImageTk.PhotoImage(
            img.resize((size, size), Image.Resampling.LANCZOS), master=master)
    return images

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
class VerticalThumb(tk.Canvas):
    def __init__(self, parent, target, bg):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, cursor="arrow")
        self.target = target
        self._drag_offset = None
        self.bind("<Configure>", lambda e: self.set(*target.yview()))
        self.bind("<Button-1>", self.move)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_offset", None))

    def set(self, first, last):
        first, last = float(first), float(last)
        self.delete("all")
        length = self.winfo_height()-8
        if last-first >= .999 or length < 1:
            return
        self.create_line(4,4+first*length,4,4+last*length,
                         fill="#A7B0BE",width=6,capstyle="round",tags="thumb")

    def move(self, event):
        first, last = self.target.yview()
        if last-first >= .999:
            return
        position = (event.y-4)/max(1,self.winfo_height()-8)
        if self._drag_offset is None:
            self._drag_offset = position-first if first <= position <= last else (last-first)/2
        self.target.yview_moveto(position-self._drag_offset)


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
        btn("plus", lambda: f"新建便签（{self.app.hotkey_text('new')}）",
            lambda: self.app.new_note()).pack(side="right", padx=5, pady=9)
        btn("trash", lambda: "删除便签", self.delete_self).pack(side="right", padx=5, pady=9)

        # -- 正文 --
        self.done_font = tkfont.Font(family=UI_FONT,
                                     size=app.data["settings"]["fontSize"])
        self.body = tk.Canvas(self, bg=self._bg, bd=0, highlightthickness=0, yscrollincrement=1)
        self.text = tk.Text(self.body, bg=self._bg, fg="#2B2F36", bd=0, highlightthickness=0,
                            padx=12, pady=10, wrap="char", spacing1=9, spacing3=10,
                            selectbackground=self._bg, inactiveselectbackground=self._bg,
                            selectforeground="#2B2F36", selectborderwidth=0,
                            font=app.base_font, insertbackground="#2B2F36", cursor="xterm")
        self.text.place(x=0, y=0)
        self.scroll_thumb = VerticalThumb(self, self.body, self._bg)
        self._make_check_images()
        self.text.tag_config("done", font=self.done_font)  # 只加删除线，不改字色
        self.text.tag_config("selection_fill", background="#DCE7F3", foreground="#2B2F36")
        self.text.tag_raise("selection_fill", "sel")
        self.text.bind("<<Selection>>", self._paint_selection)

        # -- 底部状态栏 + 添加条 --
        self.bar = tk.Frame(self, bg=self._bg)
        self.status = tk.Label(self, text="", bg=self._bg, fg=QUIET,
                               font=(UI_FONT, 9), padx=0, bd=0)
        for w in (self.toolbar, spacer, self.title_box, self.bar, self.status, self.cv):
            w.config(cursor="fleur")
            _hook_move_cursor(w)
            w.bind("<Enter>", _cursor_small)
            w.bind("<Leave>", _cursor_restore)
            w.bind("<Button-1>", self._drag_press)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_release)
        self.clear_btn = tk.Label(self.bar, text="清除已完成", bg=self._bg,
                                  fg="#A9AFB6", font=(UI_FONT, 9),
                                  padx=0, bd=0, cursor="hand2")
        self.clear_btn.bind("<Enter>", lambda e: self.clear_btn.config(fg="#454B52"))
        self.clear_btn.bind("<Leave>", lambda e: self.update_status())
        self.clear_btn.bind("<Button-1>", lambda e: self.clear_done())
        self.add_pill = tk.Canvas(self, bg=self._bg, highlightthickness=0, cursor="hand2")
        self.add_pill.bind("<Button-1>", self._pill_press)
        Tooltip(self.add_pill, lambda: "添加待办")
        self._strike_widgets = []
        self._selection_masks = []
        self._checkbox_widgets = []
        self._caret_color = self.text.cget("insertbackground")
        self._prefix_caret = tk.Frame(self.text, bg=self._caret_color)
        for sequence in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>", "<MouseWheel>"):
            self._prefix_caret.bind(sequence, lambda e, sequence=sequence: self.text.event_generate(
                sequence, x=e.x_root - self.text.winfo_rootx(), y=e.y_root - self.text.winfo_rooty(),
                state=e.state, **({"delta": e.delta} if sequence == "<MouseWheel>" else {})))
        self._prefix_caret_job = None
        self._document_height = 0
        self._content_height = 1
        self._chunk_start = 0
        self._buffer_margin = 0
        self._layout_busy = False
        self._layout_pending = False
        self._row_layout = {}
        self._wheel_job = None
        self._wheel_remaining = 0
        self._selection_scroll_job = None
        self.body.configure(yscrollcommand=self._text_scrolled)
        self.body.bind("<Configure>", lambda e: self._queue_strikes())
        self._bind_ime(self.text)
        self._draw_chip()

        self.bind("<Configure>", lambda e: self._relayout() if e.widget == self else None)
        self.cv.bind("<Motion>", self._edge_cursor)
        self.cv.bind("<Button-1>", self._edge_press)
        self.cv.bind("<B1-Motion>", self._edge_move)
        self.cv.bind("<ButtonRelease-1>", self._edge_release)
        t = self.text
        t.bind("<KeyRelease>", self._on_keyrelease)
        t.bind("<KeyPress-Return>", self._on_enter)
        t.bind("<KeyPress-BackSpace>", self._on_backspace)
        for sequence in ("<Left>", "<Right>", "<Shift-Left>", "<Shift-Right>"):
            t.bind(sequence, self._prefix_arrow)
        for sequence in ("<KeyPress>", "<KeyRelease>", "<ButtonRelease-1>", "<FocusIn>", "<FocusOut>"):
            t.bind(sequence, lambda e: self.after_idle(self._update_prefix_caret), add="+")
        t.bind("<<Cut>>", lambda e: self._clipboard_edit("tk_textCut"))
        t.bind("<<Paste>>", lambda e: self._clipboard_edit("tk_textPaste"))
        t.bind("<<Undo>>", lambda e: self._undo_edit())
        t.bind("<<Redo>>", lambda e: self._undo_edit(redo=True))
        t.bind("<Control-a>", self._select_all)
        t.bind("<Control-Shift-Z>", lambda e: self._undo_edit(redo=True))
        t.bind("<Prior>", self._page_key)
        t.bind("<Next>", self._page_key)
        t.bind("<Button-1>", self._on_click)
        t.bind("<Button-3>", self._on_rightclick)
        t.bind("<MouseWheel>", self._text_wheel)
        self.body.bind("<MouseWheel>", self._text_wheel)
        self.scroll_thumb.bind("<MouseWheel>", self._text_wheel)
        self.scroll_thumb.bind("<Button-1>", self._stop_wheel, add="+")
        t.bind("<KeyPress>", self._stop_wheel, add="+")
        t.bind("<Control-MouseWheel>", self._ctrl_wheel)
        self.body.bind("<Control-MouseWheel>", self._ctrl_wheel)
        t.bind("<B1-Motion>", self._selection_scroll, add="+")
        t.bind("<ButtonRelease-1>", self._stop_selection_scroll, add="+")
        t.bind("<Unmap>", self._stop_selection_scroll)
        t.bind("<Unmap>", self._stop_wheel, add="+")

        self.render()
        self.update_idletasks()
        self._relayout()
        self.hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        # 每张便签独立成组，激活一张时不能连带前移其他便签。
        self._owner_hwnd = user32.CreateWindowExW(
            0, "STATIC", None, 0x80000000, 0, 0, 0, 0, None, None, None, None)
        if not self._owner_hwnd:
            raise ctypes.WinError()
        self._round_window()
        self._install_inline_ime()
        self.apply_z()
        self._restyle()
        if not data.get("visible", True):
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
        self._layout_footer(text_y)
        self._queue_strikes()

    def _layout_footer(self, text_y=None):
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
        bar_y = round(center_y - 13)
        if text_y is not None:
            self.body.place(x=RADIUS + 4, y=text_y, width=w - 2 * (RADIUS + 4),
                            height=max(20, bar_y - text_y - 8))
            self.scroll_thumb.place(x=w-RADIUS-10, y=text_y+4,
                                    width=8, height=max(12,bar_y-text_y-16))
            tk.Misc.lift(self.scroll_thumb)
        self.bar.place(x=0, y=bar_y, width=w, height=26)
        self.status.place(x=pill_x / 2, y=bar_y + 13, anchor="center", height=26)
        self.clear_btn.place(x=(pill_x + pill_width + w) / 2, y=13, anchor="center")
        self.add_pill.place(x=pill_x, y=h - 10 - pill_height,
                            width=pill_width, height=pill_height)
        self._draw_pill(pill_width, pill_height)

    def _text_scrolled(self, first, last):
        self.scroll_thumb.set(first, last)
        if self._layout_busy:
            return
        top = round(self.body.canvasy(0))
        if ((self._chunk_start > 0 and top < self._chunk_start + self._buffer_margin) or
                (self.text.yview()[1] < 1 and
                 top + self.body.winfo_height() > self._chunk_start + self.text.winfo_height() - self._buffer_margin)):
            self._draw_strikes()
        else:
            self.text.place_configure(y=self._chunk_start - top)

    def _text_wheel(self, event):
        first, last = self.body.yview()
        if last-first < .999:
            # 每格移动 36 个逻辑像素，随界面缩放，不随字体大小改变。
            distance = -event.delta / 120 * self.text.winfo_fpixels("27p")
            if distance * self._wheel_remaining < 0:
                self._wheel_remaining = 0
            self._wheel_remaining += distance
            if self._wheel_job is None:
                self._wheel_time = time.monotonic() - .016
                self._step_wheel()
        else:
            self._stop_wheel()
            self.body.yview_moveto(0)
        return "break"

    def _step_wheel(self):
        self._wheel_job = None
        remaining = self._wheel_remaining
        if abs(remaining) < 1:
            # 不足一像素的移动留给下次输入，精细滚轮和触控板的小增量不能丢。
            return
        now = time.monotonic()
        # 按实际经过时间推进，电脑繁忙时下一帧会追上进度。
        progress = -math.expm1(-(now - self._wheel_time) / .04)
        self._wheel_time = now
        step = min(int(abs(remaining)), max(1, round(abs(remaining) * progress)))
        step *= 1 if remaining > 0 else -1
        self.body.yview_scroll(step, "units")
        self._wheel_remaining -= step
        self._wheel_job = self.after(16, self._step_wheel)
        self.body.update_idletasks()
        # 先处理整体移动产生的窗口重画事件，再提交这一帧；不运行下一帧的计时器。
        while self.tk.dooneevent(WINDOW_EVENTS | DONT_WAIT):
            pass
        self.body.update_idletasks()

    def _stop_wheel(self, _event=None):
        if self._wheel_job is not None:
            self.after_cancel(self._wheel_job)
            self._wheel_job = None
        self._wheel_remaining = 0

    def _body_index(self, x=0, y=0):
        return self.text.index(f"@{x},{round(self.body.canvasy(y)) - self._chunk_start}")

    def _scroll_to(self, y):
        self.body.yview_moveto(y / self._content_height)

    def _see_text(self, index):
        if getattr(self, "_strike_job", None):
            self._draw_strikes()
        box = self.text.bbox(index)
        if box is None:
            self.text.see(index)
            self._chunk_start = round(self.text.yview()[0] * self._document_height)
            box = self.text.bbox(index)
        if box:
            if self.text.compare(index, "==", "insert"):
                row, col = self._row_col(self.text.index("insert"))
                if col < self._prefix_len(self.d["lines"][row]) and row + 1 in self._row_layout:
                    height, offset = self._row_layout[row + 1]
                    size = self._check_images[False].width()
                    box = (box[0], int(self.text.cget("pady")) + offset - self._chunk_start +
                           height // 2 - size // 2, box[2], size)
            top, pad = self.body.canvasy(0), int(self.text.cget("pady"))
            y = box[1] + self._chunk_start
            if y < top + pad:
                self._scroll_to(y - pad)
            elif y + box[3] > top + self.body.winfo_height() - pad:
                self._scroll_to(y + box[3] + pad - self.body.winfo_height())
            self._draw_strikes()

    def _stop_selection_scroll(self, _event=None):
        if self._selection_scroll_job is not None:
            self.after_cancel(self._selection_scroll_job)
            self._selection_scroll_job = None

    def _page_key(self, event):
        self._stop_wheel()
        box = self.text.bbox("insert")
        cursor_y = box[1] + self._chunk_start - self.body.canvasy(0) if box else -1
        if not 0 <= cursor_y < self.body.winfo_height():
            box = None
        direction = 1 if event.keysym == "Next" else -1
        distance = direction * max(1, self.body.winfo_height() - self.app.base_font.metrics("linespace"))
        self.body.yview_scroll(distance, "units")
        self.body.update_idletasks()
        target = (self._body_index(box[0], round(cursor_y)) if box else
                  self._body_index(self.body.winfo_height() // 2, 0))
        command = "tk::TextKeySelect" if event.state & 1 else "tk::TextSetCursor"
        self.text.tk.call(command, self.text, target)
        self._see_text("insert")
        return "break"

    def _selection_scroll(self, event=None):
        self._stop_selection_scroll()
        if event is not None:
            self._selection_x = event.x
            self._selection_y = event.y_root - self.body.winfo_rooty()
        height = self.body.winfo_height()
        direction = -1 if self._selection_y < 0 else (1 if self._selection_y >= height else 0)
        if not direction:
            return
        before = self.body.yview()
        self.body.yview_scroll(direction * 8, "units")
        self.body.update_idletasks()
        y = self.body.canvasy(max(0, min(height - 1, self._selection_y))) - self._chunk_start
        self.text.tk.call("tk::TextSelectTo", self.text, self._selection_x, round(y))
        if self.body.yview() != before:
            self._selection_scroll_job = self.after(30, self._selection_scroll)
        return "break"

    def destroy(self):
        if self._prefix_caret_job is not None:
            self.after_cancel(self._prefix_caret_job)
        self._stop_wheel()
        self._stop_selection_scroll()
        if getattr(self, "_strike_job", None):
            self.after_cancel(self._strike_job)
            self._strike_job = None
        if hasattr(self, "_ime_pump_job"):
            self.after_cancel(self._ime_pump_job)
            user32.SetWindowLongPtrW(self.winfo_id(), -4, self._ime_old_proc)
        super().destroy()
        user32.DestroyWindow(self._owner_hwnd)

    def _round_window(self):
        # 由 DWM 抗锯齿裁切外框，避免透明色键的硬像素边缘。
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(self.hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))

    def _make_check_images(self):
        size = max(18, round(self.app.base_font.metrics("ascent") * 1.1))
        self._check_images = check_images(self.text, size)
        # 原生插入光标跨在字符边界上，勾选框子窗口不能覆盖占位符的左右边界。
        self._check_margin = max(1, int(self.text.cget("insertwidth")))
        slot_width = size + 2 * self._check_margin
        self._check_slots = {checked: ImageTk.PhotoImage(Image.new("RGBA", (slot_width, size)), master=self.text)
                             for checked in (False, True)}
        # Text 在左内边距处裁切光标，首行也要留出半个光标的绘制宽度。
        caret_margin = self._check_margin // 2
        self.text.tag_config("todo_indent", lmargin1=caret_margin,
                             lmargin2=caret_margin + slot_width + self.app.base_font.measure(PREFIX_PAD))

    def _restyle(self):
        for b in self._tool_btns:
            b.set_bg(self._bg)
        self.b_pin.set_active(self.d["z"] == "top")

    def toggle_pin(self):
        self.set_z("top" if self.d["z"] == "bottom" else "bottom")

    def _ime_composing(self, widget):
        if getattr(self, "_ime_native_widget", None) == widget:
            return True
        if hasattr(self, "_ime_pending") and not self._ime_pending.empty():
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
                if message == 0x0281 and self._ime_target is not None:  # 正文与标题均使用行内预编辑。
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
            self._queue_strikes()
            self._see_text("insert")
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
        if self._layout_busy:
            self._layout_pending = True
            return
        self._document_height = 0
        self._row_layout.clear()
        if getattr(self, "_strike_job", None):
            self.after_cancel(self._strike_job)
        self._strike_job = self.after_idle(self._draw_strikes)

    def _draw_strikes(self):
        if self._layout_busy:
            return
        self._layout_busy = True
        try:
            self._layout_text()
            self.body.update_idletasks()
        finally:
            self._layout_busy = False
        if self._layout_pending:
            self._layout_pending = False
            self._queue_strikes()

    def _layout_text(self):
        if getattr(self, "_strike_job", None):
            self.after_cancel(self._strike_job)
        self._strike_job = None
        width = self.body.winfo_width()
        if width < 50:
            return
        # 正文和所有装饰始终留在同一内容坐标系，滚动只移动整个 Text 窗口。
        self.text.place_configure(width=width)
        self.body.update_idletasks()
        if not self._document_height:
            self._document_height = self.text.count("1.0", "end", "update", "ypixels")
        pad = int(self.text.cget("pady"))
        self._buffer_margin = (max(self.app.base_font.metrics("linespace"), self._check_images[False].width()) +
                               int(self.text.cget("spacing1")) + int(self.text.cget("spacing3")) + pad)
        self._content_height = max(self.body.winfo_height(), self._document_height + 2 * pad)
        # Windows 子窗口坐标有范围限制；正文保留数屏缓冲，跨段时整组一起切换。
        height = min(self._content_height, max(self.body.winfo_height(),
                     min(16000, max(self.body.winfo_height() * 5,
                                    self.body.winfo_height() + 4 * self._buffer_margin))))
        self.text.place_configure(height=height)
        self.body.configure(scrollregion=(0, 0, width, self._content_height))
        self.body.update_idletasks()
        top = round(self.body.canvasy(0))
        target = max(0, top - (height - self.body.winfo_height()) // 2)
        self.text.yview_moveto(target / self._document_height)
        self._chunk_start = round(self.text.yview()[0] * self._document_height)
        self.text.place_configure(y=self._chunk_start - top)
        self.body.update_idletasks()
        used_masks = used_boxes = used_bars = 0
        content_height = self.text.winfo_height()
        visible_start = self.text.index("@0,0")
        first_row = int(visible_start.split(".")[0])
        last_row = min(len(self.d["lines"]), int(self.text.index(f"@0,{content_height}").split(".")[0]))
        for row in range(first_row, last_row + 1):
            line = self.d["lines"][row - 1]
            if line["type"] == "todo":
                if row not in self._row_layout:
                    row_height = self.text.count(f"{row}.0", f"{row+1}.0", "ypixels")[0]
                    offset = self.text.count("1.0", f"{row}.0", "ypixels")
                    self._row_layout[row] = (row_height, offset[0] if offset else 0)
                row_height, offset = self._row_layout[row]
                row_top = pad + offset - self._chunk_start
                # Tk 会把软换行的选区背景画进左边距，盖回勾选框旁的留白。
                if self.text.tag_nextrange("selection_fill", f"{row}.0", f"{row}.end"):
                    top, bottom = max(0, row_top), min(content_height, row_top + row_height)
                    if top is not None:
                        if used_masks < len(self._selection_masks):
                            mask = self._selection_masks[used_masks]
                        else:
                            mask = tk.Frame(self.text, bg=self._bg)
                            mask.bind("<MouseWheel>", self._text_wheel)
                            mask.bind("<Control-MouseWheel>", self._ctrl_wheel)
                            for event in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>"):
                                mask.bind(event, lambda e, sequence=event: self.text.event_generate(
                                    sequence, x=e.x_root - self.text.winfo_rootx(),
                                    y=e.y_root - self.text.winfo_rooty(), time=e.time, state=e.state))
                            self._selection_masks.append(mask)
                        used_masks += 1
                        mask.place(x=int(self.text.cget("padx")), y=top,
                                   width=int(self.text.tag_cget("todo_indent", "lmargin2")),
                                   height=bottom - top, bordermode="ignore")
                        mask.lift()
                size = self._check_images[False].width()
                y = row_top + row_height // 2 - size // 2
                if -size < y < content_height:
                    image = self._check_images[line["checked"]]
                    if used_boxes < len(self._checkbox_widgets):
                        box = self._checkbox_widgets[used_boxes]
                        if str(box.cget("image")) != str(image):
                            box.configure(image=image)
                    else:
                        box = tk.Label(self.text, image=image, bg=self._bg, bd=0, cursor="hand2")
                        box.bind("<MouseWheel>", self._text_wheel)
                        box.bind("<Control-MouseWheel>", self._ctrl_wheel)
                        self._checkbox_widgets.append(box)
                    used_boxes += 1
                    box._todo_row = row
                    box.place(x=int(self.text.cget("padx")) + self._check_margin + self._check_margin // 2,
                              y=y, width=size, height=size,
                              bordermode="ignore")
                    box.lift()
                    box.bind("<Button-1>", lambda _e, r=row-1: self.toggle_done(r))
            if line["type"] != "todo" or not line["checked"]:
                continue
            start = f"{row}.{self._prefix_len(line)}"
            if self.text.compare(start, "<", visible_start):
                start = visible_start
            end = f"{row}.end"
            while self.text.compare(start, "<", end):
                display = self.text.dlineinfo(start)
                if not display or display[1] >= content_height:
                    break
                stop = self.text.index(f"@{self.text.winfo_width()},{max(0, display[1])}")
                first = self.text.bbox(start)
                last = self.text.bbox(f"{end}-1c" if self.text.compare(stop, ">=", end) else stop)
                if first and last:
                    thickness = max(2, round(self.done_font.metrics("ascent") * 0.12))
                    if used_bars < len(self._strike_widgets):
                        bar = self._strike_widgets[used_bars]
                    else:
                        bar = tk.Frame(self.text, bg="#505761", height=thickness, cursor="xterm")
                        bar.bind("<MouseWheel>", self._text_wheel)
                        bar.bind("<Control-MouseWheel>", self._ctrl_wheel)
                        for event in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>"):
                            bar.bind(event, lambda e, sequence=event: self.text.event_generate(
                                sequence, x=e.x_root - self.text.winfo_rootx(),
                                y=e.y_root - self.text.winfo_rooty(), time=e.time, state=e.state))
                        self._strike_widgets.append(bar)
                    used_bars += 1
                    bar._text_start = start
                    baseline = display[1] + display[4]
                    middle = baseline - round(self.done_font.metrics("ascent") * 0.33)
                    bar.place(x=first[0], y=middle - thickness // 2,
                              width=max(1, last[0] + last[2] - first[0]),
                              height=thickness, bordermode="ignore")
                if display[1] + display[3] >= content_height:
                    break
                next_start = self.text.index(f"@0,{display[1] + display[3]}")
                if self.text.compare(next_start, "<=", start):
                    break
                start = next_start

        # 这次没排上的控件回收掉，池子始终只留当前需要的。
        for pool, used in ((self._selection_masks, used_masks),
                           (self._checkbox_widgets, used_boxes),
                           (self._strike_widgets, used_bars)):
            for widget in pool[used:]:
                widget.destroy()
            del pool[used:]

        if self._selection_masks:
            self.after_idle(lambda: [w.lift() for w in self._selection_masks + self._checkbox_widgets
                                     if w.winfo_exists()])
        self._update_prefix_caret()

    def _prefix_arrow(self, event):
        self.after_idle(self._update_prefix_caret)
        if event.state & 0x000c or self._ime_composing(self.text):
            return
        row, col = self._row_col(self.text.index("insert"))
        pre = self._prefix_len(self.d["lines"][row])
        if pre and ((event.keysym == "Left" and 1 < col <= pre) or
                    (event.keysym == "Right" and 1 <= col < pre)):
            target = f"{row+1}.{1 if event.keysym == 'Left' else pre}"
            command = "tk::TextKeySelect" if event.state & 1 else "tk::TextSetCursor"
            self.text.tk.call(command, self.text, target)
            return "break"

    def _update_prefix_caret(self):
        if self._prefix_caret_job is not None:
            self.after_cancel(self._prefix_caret_job)
            self._prefix_caret_job = None
        self._prefix_caret.place_forget()
        self.text.configure(insertbackground=self._caret_color)
        row, col = self._row_col(self.text.index("insert"))
        if (self.text.focus_get() != self.text or row >= len(self.d["lines"]) or
                not 0 <= col < self._prefix_len(self.d["lines"][row]) or
                self._ime_composing(self.text)):
            return
        box = next((box for box in self._checkbox_widgets if box._todo_row == row + 1), None)
        if box is None:
            return
        # 前缀的插入位置属于整条待办的复选框，不属于首个文字显示行。
        self.text.configure(insertbackground=self._bg)
        width = int(self.text.cget("insertwidth"))
        if not width:
            return
        geometry = {key: int(box.place_info()[key]) for key in ("x", "y", "width", "height")}
        x = geometry["x"] - self._check_margin if col == 0 else geometry["x"] + geometry["width"] + self._check_margin
        self._prefix_caret.place(x=x - width // 2, y=geometry["y"],
                                 width=width, height=geometry["height"], bordermode="ignore")
        self._prefix_caret.configure(bg=self._caret_color)
        self._prefix_caret.lift()
        if int(self.text.cget("insertofftime")):
            self._prefix_caret_job = self.after(int(self.text.cget("insertontime")), self._blink_prefix_caret)

    def _blink_prefix_caret(self):
        self._prefix_caret_job = None
        visible = self._prefix_caret.cget("bg") == self._caret_color
        self._prefix_caret.configure(bg=self._bg if visible else self._caret_color)
        delay = int(self.text.cget("insertofftime" if visible else "insertontime"))
        self._prefix_caret_job = self.after(max(1, delay), self._blink_prefix_caret)

    # ---- 底部添加条 ----
    def add_todo(self):
        row = int(self.text.index("insert").split(".")[0])
        self.d["lines"].insert(row, {"type": "todo", "text": "", "checked": False})
        self.render(keep_cursor=False)
        self.text.mark_set("insert", f"{row+1}.0+{self._prefix_len(self.d['lines'][row])}c")
        self._see_text(f"{row+1}.0")
        self.text.focus_set()
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
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
        self._bind_ime(e)
        e.focus_force()
        e.select_range(0, "end")

        def commit(_e=None):
            if not e.winfo_exists() or self._ime_composing(e):
                return
            self.d["title"] = e.get().strip()
            self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
            self.app.schedule_save()
            e.destroy()
            self._title_editor = None
            self._draw_chip()

        e.bind("<Return>", commit)
        e.bind("<Escape>", commit)
        e.bind("<FocusOut>", commit, add="+")

    # ---- 渲染 ----
    def render(self, keep_cursor=True):
        self._stop_wheel()
        cur = self.text.index("insert")
        view = self._body_index()
        scroll_y = self.body.canvasy(0)
        old_chunk = self._chunk_start
        top = self.text.dlineinfo(view)
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
        self.body.update_idletasks()
        restored = self.text.dlineinfo(view)
        if top and restored:
            self._scroll_to(scroll_y + restored[1] + self._chunk_start - top[1] - old_chunk)
        self._remember_edit()

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
    def _remember_edit(self):
        state = (deepcopy(self.d["lines"]), self.text.index("insert"))
        if not hasattr(self, "_edit_history"):
            self._edit_history, self._edit_redo = [state], []
        elif self._edit_history[-1][0] != state[0]:
            self._edit_history.append(state)
            self._edit_redo.clear()
        else:
            self._edit_history[-1] = state

    def _undo_edit(self, redo=False):
        if self._ime_composing(self.text):
            return "break"
        self._sync_lines_from_text()
        if redo:
            if not self._edit_redo:
                return "break"
            state = self._edit_redo.pop()
            self._edit_history.append(state)
        else:
            if len(self._edit_history) < 2:
                return "break"
            self._edit_redo.append(self._edit_history.pop())
            state = self._edit_history[-1]
        self.d["lines"] = deepcopy(state[0])
        self.render(keep_cursor=False)
        self.text.mark_set("insert", state[1])
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()
        return "break"

    def _clipboard_edit(self, command):
        if self._ime_composing(self.text):
            return "break"
        self._sync_lines_from_text()
        if command == "tk_textPaste" and self.text.tag_ranges("sel"):
            self.text.mark_set("insert", "sel.first")
        self.text.tk.call(command, self.text)
        self._on_keyrelease(None)
        return "break"

    def _select_all(self, _event):
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.mark_set("insert", "end-1c")
        return "break"

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
        if old != lines:
            self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        structure_changed = (len(old) != len(lines) or
                             any(a["type"] != b["type"] for a, b in zip(old, lines)))
        if structure_changed:
            self.render()
        else:
            for row, line in enumerate(lines, 1):
                if line["type"] == "todo":
                    t.tag_add("todo_indent", f"{row}.0", f"{row}.end")
            self.update_status()
        self._remember_edit()

    def _on_keyrelease(self, _e):
        if self._ime_composing(self.text):
            return
        self._queue_strikes()
        self._sync_lines_from_text()
        if _e is None or _e.keysym not in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"):
            self._see_text("insert")
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
        if ln["type"] == "todo" and rel == 0:
            right = ""
        else:
            ln["text"] = left
        lines.insert(row + 1, {"type": ln["type"], "text": right, "checked": False})
        self.render(keep_cursor=False)
        nrow = row + 2
        self.text.mark_set("insert", f"{nrow}.0+{self._prefix_len(lines[row+1])}c")
        self._see_text(f"{nrow}.0")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
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
            self._see_text("insert")
            self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
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
        target = "text" if lines[row]["type"] == "todo" else "todo"
        actions = [
            ("转为文本行" if target == "text" else "转为待办行",
             lambda: self.convert(row, target)),
            ("在下方插入新行", lambda: self.insert_line(row)),
            ("删除此行", lambda: self.delete_line(row)),
        ]
        self._show_context_menu(e.x_root, e.y_root, actions)

    def _show_context_menu(self, x, y, actions):
        old = getattr(self, "_context_menu", None)
        if old and old.winfo_exists():
            old.destroy()
        p = tk.Toplevel(self)
        self._context_menu = p
        p.withdraw()
        p.overrideredirect(True)
        p.configure(bg=T_COLOR)
        p.attributes("-transparentcolor", T_COLOR)
        p.attributes("-topmost", True)
        font = tkfont.Font(family=UI_FONT, size=10)
        width = max(184, max(font.measure(label) for label, _ in actions) + 44)
        row_h = max(34, font.metrics("linespace") + 12)
        height = 16 + row_h * len(actions) + 9
        cv = tk.Canvas(p, width=width, height=height, bg=T_COLOR,
                       highlightthickness=0, bd=0)
        cv.pack()
        round_rect(cv, 1, 1, width - 2, height - 2, 11,
                   fill="#FFFFFF", outline="#D9DCE1")
        highlights = []
        for i, (label, _) in enumerate(actions):
            top = 8 + i * row_h + (9 if i == 2 else 0)
            highlight = round_rect(cv, 5, top, width - 5, top + row_h, 7,
                                   fill="#F2F3F5", outline="", state="hidden")
            highlights.append(highlight)
            cv.create_text(18, top + row_h / 2, anchor="w", text=label,
                           fill="#2B2F36", font=font, tags=(f"item{i}",))
            cv.tag_bind(f"item{i}", "<Enter>", lambda _e, n=i: select(n))
            cv.tag_bind(f"item{i}", "<Button-1>", lambda _e, n=i: activate(n))
            cv.tag_bind(highlight, "<Enter>", lambda _e, n=i: select(n))
            cv.tag_bind(highlight, "<Button-1>", lambda _e, n=i: activate(n))
        sep_y = 8 + 2 * row_h + 4
        cv.create_line(14, sep_y, width - 14, sep_y, fill="#E2E4E8")

        selected = [None]

        def select(n):
            if selected[0] is not None:
                cv.itemconfigure(highlights[selected[0]], state="hidden")
            selected[0] = n
            cv.itemconfigure(highlights[n], state="normal")
            cv.tag_lower(highlights[n], f"item{n}")

        def close(_e=None):
            if p.winfo_exists():
                p.grab_release()
                p.destroy()

        def activate(n):
            close()
            actions[n][1]()

        def key(e):
            if e.keysym in ("Down", "Up"):
                select((selected[0] + (1 if e.keysym == "Down" else -1)) % len(actions)
                       if selected[0] is not None else (0 if e.keysym == "Down" else len(actions) - 1))
            elif e.keysym in ("Return", "space"):
                activate(selected[0] if selected[0] is not None else 0)
            elif e.keysym == "Escape":
                close()

        p.bind("<Key>", key)
        p.bind("<FocusOut>", close)
        p.bind("<Button-1>", close)
        p.geometry(f"{width}x{height}+{min(x, p.winfo_screenwidth() - width)}+{min(y, p.winfo_screenheight() - height)}")
        p.deiconify()
        p.grab_set()
        p.focus_set()

    def toggle_done(self, row):
        ln = self.d["lines"][row]
        ln["checked"] = not ln["checked"]
        self.render()
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def convert(self, row, to):
        ln = self.d["lines"][row]
        ln["type"] = to
        if to == "text":
            ln["checked"] = False
        self.render(keep_cursor=False)
        self.text.mark_set("insert", f"{row+1}.0+{self._prefix_len(ln)}c")
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def insert_line(self, row):
        self.d["lines"].insert(row + 1, {"type": "text", "text": "", "checked": False})
        self.render(keep_cursor=False)
        self.text.mark_set("insert", f"{row+2}.0")
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def delete_line(self, row):
        if len(self.d["lines"]) <= 1:
            self.d["lines"] = [{"type": "text", "text": "", "checked": False}]
        else:
            del self.d["lines"][row]
        self.render(keep_cursor=False)
        row = min(row, len(self.d["lines"]) - 1)
        self.text.mark_set("insert", f"{row+1}.{self._prefix_len(self.d['lines'][row])}")
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def clear_done(self):
        self.app.archive_done(self)

    def _ctrl_wheel(self, e):
        step = 10 if e.delta > 0 else -10
        percent = round(self.app.data["settings"]["opacity"] * 100)
        self.app.set_opacity(percent + step)
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
        l, r, t, b = x < EDGE, x > w - EDGE, y < EDGE, y > h - EDGE  # 左 右 上 下
        if (x < CORNER or x > w - CORNER) and (y < CORNER or y > h - CORNER):
            # 圆角区整块判定为斜向，光标与拉伸方向随所在角自动匹配
            l, r = x < w / 2, x >= w / 2
            t, b = y < h / 2, y >= h / 2
        return l, r, t, b

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
            cur = "fleur"
        if cur != self.cv.cget("cursor"):
            self.cv.config(cursor=cur)

    def _edge_press(self, e):
        l, r, t, b = self._edge_hit(e.x, e.y)
        if not (l or r or t or b):
            return self._drag_press(e)
        self._rs = [l, r, t, b, self.winfo_width(), self.winfo_height(),
                    self.winfo_x(), self.winfo_y(), e.x_root, e.y_root]
        return "break"

    def _edge_move(self, e):
        if not getattr(self, "_rs", None):
            return self._drag_move(e)
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
        # 仅在这一帧布局期间启用子窗口合成，避免 Text 和勾选框分批出现在屏幕上。
        # 常驻 WS_EX_COMPOSITED 会使 Tk 的 Expose 事件不断循环。
        style = user32.GetWindowLongPtrW(self.hwnd, -20)
        user32.SetWindowLongPtrW(self.hwnd, -20, style | 0x02000000)
        try:
            # 左/上沿缩放同时改变原点，不能把旧客户区像素复制到新位置。
            user32.SetWindowPos(self.hwnd, None, x, y, w, h,
                                0x0004 | 0x0010 | 0x0100)  # NOZORDER | NOACTIVATE | NOCOPYBITS
            self.update_idletasks()
            user32.RedrawWindow(self.hwnd, None, None, 0x0001 | 0x0080 | 0x0100)
            # RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW
        finally:
            user32.SetWindowLongPtrW(self.hwnd, -20, style)
        user32.RedrawWindow(self.hwnd, None, None, 0x0001 | 0x0080 | 0x0100)
        self.text.event_generate("<Expose>", x=0, y=0,
                                 width=self.text.winfo_width(), height=self.text.winfo_height())
        while self.tk.dooneevent(WINDOW_EVENTS | IDLE_EVENTS | DONT_WAIT):
            pass
        return "break"

    def _edge_release(self, _e):
        if getattr(self, "_rs", None):
            self._rs = None
            self.d.update(w=self.winfo_width(), h=self.winfo_height(),
                          x=self.winfo_x(), y=self.winfo_y())
            self.app.schedule_save()
        else:
            self._drag_release(_e)

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
        # 隐藏的所有者可隐藏任务栏；工具窗口会被最小化后的激活顺序跳过。
        user32.SetWindowLongPtrW(self.hwnd, -8, self._owner_hwnd)  # GWLP_HWNDPARENT
        style = user32.GetWindowLongPtrW(self.hwnd, -20)
        user32.SetWindowLongPtrW(self.hwnd, -20, style & ~0x80)  # WS_EX_TOOLWINDOW

    def hide(self):
        self.d["visible"] = False
        self.withdraw()
        self.app.schedule_save()

    def show(self):
        self.d["visible"] = True
        self.deiconify()
        self.overrideredirect(True)
        self._round_window()
        # 先前移到置顶层，再恢复原状态，确保被其他进程覆盖后也能显示到前面。
        self.attributes("-topmost", True)
        self.apply_z()
        self.lift()

    def delete_self(self):
        self.app.remove_note(self)

# ---------- 设置窗口 ----------
class SettingsScale(tk.Canvas):
    def __init__(self, master, minimum, maximum, step, command):
        super().__init__(master, width=180, height=42, bg=NOTE_BG,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.minimum, self.maximum, self.step = minimum, maximum, step
        self.command, self.value = command, minimum
        self.bind("<Button-1>", self._pick)
        self.bind("<B1-Motion>", self._pick)
        self.bind("<Left>", lambda _e: self.set(self.value - self.step))
        self.bind("<Right>", lambda _e: self.set(self.value + self.step))
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())
        self._draw()

    def get(self):
        return self.value

    def set(self, value):
        value = min(self.maximum, max(self.minimum,
                    self.minimum + round((float(value) - self.minimum) / self.step) * self.step))
        if value != self.value:
            self.value = value
            self._draw()
            self.command(value)

    def _pick(self, event):
        self.focus_set()
        self.set(self.minimum + (event.x - 12) * (self.maximum - self.minimum) / 156)
        return "break"

    def _draw(self):
        # 超采样绘制刻度和握柄，保持圆角与线条平滑。
        scale = 4
        img = Image.new("RGBA", (180 * scale, 42 * scale), NOTE_BG)
        draw = ImageDraw.Draw(img)
        def box(bounds, radius, **kw):
            draw.rounded_rectangle(tuple(int(v * scale) for v in bounds),
                                   radius=radius * scale, **kw)
        x = 12 + 156 * (self.value - self.minimum) / (self.maximum - self.minimum)
        box((12, 11, 168, 17), 3, fill="#E9EDF1")
        if x > 12:
            box((12, 11, x, 17), 3, fill="#7D8C9D")
        count = (self.maximum - self.minimum) // self.step
        for index in range(count + 1):
            tick_x = (12 + 156 * index / count) * scale
            draw.line((tick_x, 31 * scale, tick_x, 35 * scale),
                      fill="#8E9CAC" if 12 + 156 * index / count <= x else "#D8DEE5",
                      width=scale)
        box((x - 9, 3, x + 9, 27), 6, fill="#E6EAF0")
        box((x - 9, 1, x + 9, 25), 6, fill="#FFFFFF",
            outline="#64748B" if self.focus_get() == self else "#9CA9B8", width=scale)
        for offset in (-2, 2):
            draw.line(((x + offset) * scale, 9 * scale,
                       (x + offset) * scale, 17 * scale), fill="#7D8C9D", width=scale)
        self._image = ImageTk.PhotoImage(img.resize((180, 42), Image.Resampling.LANCZOS), master=self)
        self.delete("all")
        self.create_image(0, 0, image=self._image, anchor="nw")


class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.withdraw()
        self.app = app
        self.title("z-note 设置")
        self._icon_image = ImageTk.PhotoImage(_make_tray_icon(), master=self)
        self.iconphoto(False, self._icon_image)
        self.overrideredirect(True)
        self.resizable(False, False)
        self.configure(bg=NOTE_BG)
        cv = tk.Canvas(self, bg=NOTE_BG, highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        body = tk.Canvas(self, bg=NOTE_BG, highlightthickness=0, bd=0)
        f = tk.Frame(body, bg=NOTE_BG, padx=4, pady=2)
        content = body.create_window(0, 0, window=f, anchor="nw")

        header = tk.Frame(f, bg=NOTE_BG)
        header.pack(fill="x", pady=(0, 12))
        title = tk.Label(header, text="设置", bg=NOTE_BG, fg="#1F2328",
                         font=(UI_FONT, 12, "bold"), cursor="fleur")
        title.pack(side="left")
        _hook_move_cursor(title)
        title.bind("<Enter>", _cursor_small)
        title.bind("<Leave>", _cursor_restore)
        close = tk.Label(header, text="×", bg=NOTE_BG, fg=INK,
                         font=(UI_FONT, 17), cursor="hand2")
        close.pack(side="right", padx=(8, 2))
        close.bind("<Button-1>", lambda _e: self.destroy())
        minimize = tk.Label(header, text="−", bg=NOTE_BG, fg=INK,
                            font=(UI_FONT, 17), cursor="hand2")
        minimize.pack(side="right", padx=(8, 2))
        minimize.bind("<Button-1>", lambda _e: self._minimize())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._drag = None
        self._close_button = close
        self._minimize_button = minimize
        self.bind("<Button-1>", self._drag_press)
        self.bind("<B1-Motion>", self._drag_move)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))

        def section(title):
            tk.Frame(f, bg=NOTE_BORDER, height=1).pack(fill="x", pady=(14, 14))
            heading = tk.Frame(f, bg=NOTE_BG)
            heading.pack(fill="x", pady=(0, 8))
            tk.Label(heading, text=title, bg=NOTE_BG, fg="#1F2328",
                     font=(UI_FONT, 11, "bold")).pack(side="left")
            return heading

        section("常规")

        self._check_images = check_images(self, 22)
        self.v_boot = tk.BooleanVar(value=app.data["settings"]["autostart"])
        tk.Checkbutton(f, text=" 开机自启", variable=self.v_boot, bg=NOTE_BG,
                       fg="#2B2F36", activebackground=NOTE_BG, selectcolor=NOTE_BG,
                       font=(UI_FONT, 10), image=self._check_images[False],
                       selectimage=self._check_images[True], compound="left",
                       indicatoron=False, relief="flat", offrelief="flat",
                       bd=0, highlightthickness=0, cursor="hand2",
                       command=self._toggle_boot).pack(anchor="w", pady=4)

        self.v_show = tk.BooleanVar(value=app.data["settings"]["showOnStartup"])
        startup = tk.Checkbutton(f, text=" 启动时显示便签", variable=self.v_show,
                       bg=NOTE_BG, fg="#2B2F36", activebackground=NOTE_BG,
                       selectcolor=NOTE_BG, font=(UI_FONT, 10),
                       image=self._check_images[False], selectimage=self._check_images[True],
                       compound="left", indicatoron=False, relief="flat", offrelief="flat",
                       bd=0, highlightthickness=0, cursor="hand2",
                       command=self._save, justify="left", anchor="w")
        startup.pack(anchor="w", pady=4)

        section("外观")
        row = tk.Frame(f, bg=NOTE_BG); row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text="透明度", bg=NOTE_BG, fg="#2B2F36",
                 font=(UI_FONT, 10)).pack(side="left")
        opacity_value = tk.Label(row, bg=NOTE_BG, fg=QUIET,
                                 font=(UI_FONT, 9), width=4, anchor="center")
        opacity_value.pack(side="right")
        def update_opacity(v):
            opacity_value.config(text=f"{v}%")
            self._op(v)
        s = SettingsScale(row, 0, 50, 10, update_opacity)
        app.opacity_scale = s
        s.set(round((1 - app.data["settings"]["opacity"]) * 100))
        opacity_value.config(text=f"{s.get()}%")
        if app.data["settings"]["opacity"] != (100 - s.get()) / 100:
            self._op(s.get())
        s.pack(side="right")

        row3 = tk.Frame(f, bg=NOTE_BG); row3.pack(fill="x", pady=(4, 0))
        tk.Label(row3, text="字号", bg=NOTE_BG, fg="#2B2F36",
                 font=(UI_FONT, 10)).pack(side="left")
        font_value = tk.Label(row3, bg=NOTE_BG, fg=QUIET,
                              font=(UI_FONT, 9), width=4, anchor="center")
        font_value.pack(side="right")
        def update_font(v):
            font_value.config(text=str(v))
            self._font(v)
        s2 = SettingsScale(row3, 10, 15, 1, update_font)
        s2.set(app.data["settings"]["fontSize"])
        font_value.config(text=str(s2.get()))
        if app.data["settings"]["fontSize"] != s2.get():
            self._font(s2.get())
        s2.pack(side="right")

        hotkey_heading = section("快捷键")
        reset_font = tkfont.Font(root=self, family=UI_FONT, size=9)
        button_w = reset_font.measure("重置快捷键") + 24
        button_h = reset_font.metrics("linespace") + 14
        button_image = Image.new("RGB", (button_w * 4, button_h * 4), NOTE_BG)
        ImageDraw.Draw(button_image).rounded_rectangle(
            (2, 2, button_w * 4 - 3, button_h * 4 - 3), radius=32,
            fill=NOTE_BG, outline=NOTE_BORDER, width=4)
        self._reset_image = ImageTk.PhotoImage(
            button_image.resize((button_w, button_h), Image.Resampling.LANCZOS), master=self)
        reset_button = tk.Button(hotkey_heading, text="重置快捷键", command=self._reset_hotkeys,
                                 image=self._reset_image, compound="center", bg=NOTE_BG,
                                 fg="#475569", font=reset_font, relief="flat", bd=0,
                                 highlightthickness=0, padx=0, pady=0, cursor="hand2")
        reset_button.pack(side="left", padx=(16, 0))
        self._hotkey_font = tkfont.Font(root=self, family=UI_FONT, size=9)
        hotkey_width = math.ceil(self._hotkey_font.measure("Ctrl+Alt+Shift+Backspace") /
                                 self._hotkey_font.measure("0")) + 2
        self.hotkey_entries = {}
        self._hotkey_vars = {}
        self._hotkey_borders = []
        hotkey_rows = []
        for action, name in (("new", "新建便签"), ("hide", "隐藏所有便签"), ("show", "显示所有便签")):
            hotkey_row = tk.Frame(f, bg=NOTE_BG)
            hotkey_row.pack(fill="x", pady=5)
            hotkey_row.columnconfigure(0, weight=1)
            label = tk.Label(hotkey_row, text=name, bg=NOTE_BG, fg="#2B2F36",
                             font=(UI_FONT, 10), justify="left", anchor="w")
            label.grid(row=0, column=0, sticky="w", padx=(0, 12))
            border = tk.Canvas(hotkey_row, bg=NOTE_BG, highlightthickness=0, bd=0,
                               cursor="hand2")
            entry = tk.Entry(border, font=self._hotkey_font, width=hotkey_width, justify="center",
                             state="readonly", readonlybackground=NOTE_BG, fg="#475569",
                             relief="flat", highlightthickness=0, cursor="hand2")
            box_w, box_h = entry.winfo_reqwidth() + 12, entry.winfo_reqheight() + 12
            border.configure(width=box_w, height=box_h)
            outline = round_rect(border, 1, 1, box_w - 1, box_h - 1, 7,
                                 fill=NOTE_BG, outline=NOTE_BORDER)
            entry.place(x=6, y=6)
            border.grid(row=0, column=1, sticky="e")
            border.bind("<Button-1>", lambda _e, widget=entry: widget.focus_set())
            entry.bind("<FocusIn>", lambda _e, canvas=border, shape=outline:
                       canvas.itemconfigure(shape, outline="#94A3B8"), add="+")
            entry.bind("<FocusOut>", lambda _e, canvas=border, shape=outline:
                       canvas.itemconfigure(shape, outline=NOTE_BORDER), add="+")
            self._hotkey_vars[action] = tk.StringVar(master=self, value=app.hotkey_text(action))
            entry.configure(textvariable=self._hotkey_vars[action])
            entry.bind("<KeyPress>", lambda e, action=action: self._capture_hotkey(e, action))
            entry.bind("<Button-1>", lambda e: e.widget.focus_set())
            entry.bind("<FocusIn>", lambda _e, action=action:
                       setattr(app, "_hotkey_capture", (self, action)), add="+")
            entry.bind("<FocusOut>", lambda _e: setattr(app, "_hotkey_capture", None), add="+")
            self.hotkey_entries[action] = entry
            self._hotkey_borders.append(border)
            hotkey_rows.append((hotkey_row, label, border))
        hotkey_hint = tk.Label(f, text="点击方框后按下组合键，自动保存。Esc 取消。",
                              bg=NOTE_BG, fg=QUIET, font=(UI_FONT, 9), justify="left",
                              anchor="w")
        self._hotkey_hint = hotkey_hint
        hotkey_hint.pack(fill="x", pady=(8, 0))
        self.bind("<Destroy>", lambda e: e.widget == self and setattr(app, "_hotkey_capture", None))
        section("数据")
        path = tk.Label(f, text=DATA_FILE, bg=NOTE_BG, fg="#A9AFB6",
                        font=(UI_FONT, 9), wraplength=460, justify="left")
        path.pack(anchor="w", pady=(8, 0))
        self.update_idletasks()
        work = wintypes.RECT()
        user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
        available_w, available_h = work.right - work.left - 32, work.bottom - work.top - 32
        width = min(max(720, f.winfo_reqwidth() + 2 * RADIUS), available_w)
        content_width = width - 2 * RADIUS
        # 文本按实际可用宽度换行，窗口高度在换行完成后再计算。
        startup.configure(wraplength=max(1, content_width - self._check_images[False].width() - 16))
        hotkey_hint.configure(wraplength=max(1, content_width - 16))
        path.configure(wraplength=max(1, content_width - 16))
        for hotkey_row, label, entry in hotkey_rows:
            label.configure(wraplength=max(1, content_width - entry.winfo_reqwidth() - 28))
        body.itemconfigure(content, width=content_width)
        for appearance_row in (row, row3):
            label = appearance_row.winfo_children()[0]
            label.configure(wraplength=max(1, content_width - 16), justify="left")
            if appearance_row.winfo_reqwidth() > content_width - 8:
                label.pack_configure(side="top", anchor="w")
        self.update_idletasks()
        content_height = f.winfo_reqheight()
        height = min(content_height + 2 * RADIUS, available_h)
        viewport_height = height - 2 * RADIUS
        if content_height > viewport_height:
            scrollbar = tk.Scrollbar(self, orient="vertical", command=body.yview)
            scrollbar_width = scrollbar.winfo_reqwidth()
            content_width -= scrollbar_width + 4
            scrollbar.place(x=width - RADIUS - scrollbar_width, y=RADIUS,
                            width=scrollbar_width, height=viewport_height)
            body.configure(yscrollcommand=scrollbar.set)
            startup.configure(wraplength=max(1, content_width - self._check_images[False].width() - 16))
            hotkey_hint.configure(wraplength=max(1, content_width - 16))
            path.configure(wraplength=max(1, content_width - 16))
            for hotkey_row, label, entry in hotkey_rows:
                label.configure(wraplength=max(1, content_width - entry.winfo_reqwidth() - 28))
            body.itemconfigure(content, width=content_width)
            for appearance_row in (row, row3):
                label = appearance_row.winfo_children()[0]
                label.configure(wraplength=max(1, content_width - 16))
                if appearance_row.winfo_reqwidth() > content_width - 8:
                    label.pack_configure(side="top", anchor="w")
            self.update_idletasks()
            content_height = f.winfo_reqheight()
            self.bind("<MouseWheel>", lambda e: body.yview_scroll(-int(e.delta / 120), "units"))
        body.place(x=RADIUS, y=RADIUS, width=content_width, height=viewport_height)
        body.configure(scrollregion=(0, 0, content_width, content_height))
        cv.configure(width=width, height=height)
        # 与便签窗口一致，由 DWM 抗锯齿裁切矩形底板。
        cv.create_rectangle(0, 0, width - 1, height - 1,
                            fill=NOTE_BG, outline=NOTE_BORDER)
        self.geometry("%dx%d+%d+%d" % (width, height,
                       work.left + (work.right - work.left - width) // 2,
                       work.top + (work.bottom - work.top - height) // 2))
        self.update_idletasks()
        hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        preference = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
        style = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, style & ~0x80 | 0x40000)  # WS_EX_TOOLWINDOW / WS_EX_APPWINDOW
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | 0x04 | 0x20)
        self.deiconify()

    def _drag_press(self, event):
        if (event.widget in (self._close_button, self._minimize_button, *self._hotkey_borders) or
                isinstance(event.widget, (SettingsScale, tk.Checkbutton, tk.Button, tk.Scrollbar, tk.Entry))):
            self._drag = None
            return
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _minimize(self):
        user32.ShowWindow(user32.GetParent(self.winfo_id()) or self.winfo_id(), 6)  # SW_MINIMIZE

    def _drag_move(self, event):
        if self._drag is None:
            return
        self.geometry("+%d+%d" % (event.x_root - self._drag[0],
                                   event.y_root - self._drag[1]))
        self.update_idletasks()

    def _capture_hotkey(self, event, action):
        if event.keysym == "Escape":
            self.focus_set()
            return "break"
        key = next((name for name, code in HOTKEY_KEYS.items() if code == event.keycode), None)
        if key is None:
            return "break"
        parts = [name for mask, name in ((4, "Ctrl"), (0x20000, "Alt"), (1, "Shift"))
                 if event.state & mask]
        if not (event.state & (4 | 0x20000)) and not 112 <= event.keycode <= 135:
            self._hotkey_hint.configure(text="请使用 Ctrl / Alt 组合键，或 F1–F24 功能键。", fg="#A44A42")
            return "break"
        self._apply_hotkey(action, "+".join(parts + [key]))
        return "break"

    def _apply_hotkey(self, action, value):
        error = self.app.update_hotkey(action, value)
        if error:
            self._hotkey_hint.configure(text=error, fg="#A44A42")
        else:
            self._hotkey_vars[action].set(value)
            self._hotkey_hint.configure(text="快捷键已保存并生效。", fg=QUIET)

    def _reset_hotkeys(self):
        error = self.app.reset_hotkeys()
        if error:
            self._hotkey_hint.configure(text=error, fg="#A44A42")
        else:
            for action in HOTKEY_ACTIONS:
                self._hotkey_vars[action].set(self.app.hotkey_text(action))
            self._hotkey_hint.configure(text="快捷键已恢复默认值。", fg=QUIET)

    def _toggle_boot(self):
        if set_autostart(self.v_boot.get()):
            self.app.data["settings"]["autostart"] = self.v_boot.get()
        else:
            self.v_boot.set(False)
            messagebox.showwarning("z-note", "写入开机自启失败。", parent=self)
        self._save()

    def _op(self, v):
        self.app.set_opacity(100 - int(float(v)))

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

class HistoryPane(tk.Canvas):
    """圆角底板内放置内容和按可见比例绘制的滚动滑块。"""
    def __init__(self, parent):
        super().__init__(parent, bg=NOTE_BG, highlightthickness=0, bd=0)
        self.viewport = tk.Canvas(self, bg=NOTE_BG, highlightthickness=0)
        self.content = tk.Frame(self.viewport, bg=NOTE_BG)
        self.window = self.viewport.create_window(0, 0, window=self.content, anchor="nw")
        self.bars = {"y": VerticalThumb(self, self.viewport, NOTE_BG)}
        self.viewport.configure(yscrollcommand=self.bars["y"].set)
        self.bind("<Configure>", self.layout)
        self.content.bind("<Configure>", self.update_region)
        self.viewport.bind("<Configure>", self.update_region)
        self.viewport.bind("<MouseWheel>", self.wheel)

    def wheel(self, event):
        if self.content.winfo_reqheight() > self.viewport.winfo_height():
            self.viewport.yview_scroll(-int(event.delta/120), "units")
        else:
            self.viewport.yview_moveto(0)
        return "break"

    def layout(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        image = Image.new("RGBA", (max(1,w)*4, max(1,h)*4))
        ImageDraw.Draw(image).rounded_rectangle((2,2,w*4-3,h*4-3), radius=48,
                                                fill=NOTE_BG, outline=NOTE_BORDER, width=4)
        self._border = ImageTk.PhotoImage(image.resize((max(1,w),max(1,h)), Image.Resampling.LANCZOS), master=self)
        self.delete("border")
        self.create_image(0,0,anchor="nw",image=self._border,tags="border")
        self.viewport.place(x=12,y=12,width=max(1,w-30),height=max(1,h-30))
        self.bars["y"].place(x=w-15,y=14,width=8,height=max(1,h-38))
        self.update_region()

    def update_region(self, _event=None):
        self.viewport.itemconfigure(self.window, width=max(1,self.viewport.winfo_width()))
        self.viewport.configure(scrollregion=(0, 0, self.viewport.winfo_width(),
            max(self.content.winfo_reqheight(), self.viewport.winfo_height())))
        if self.content.winfo_reqheight() <= self.viewport.winfo_height():
            self.viewport.yview_moveto(0)
        for axis in self.bars:
            self.bars[axis].set(*self.viewport.yview())

class HistoryWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.withdraw()
        self.app = app
        self.mode = "archives"
        self.selected_id = None
        self.overrideredirect(True)
        self.configure(bg=NOTE_BORDER)
        self.title("z-note 清除记录与回收站")
        self._icon_image = ImageTk.PhotoImage(_make_tray_icon(), master=self)
        self.iconphoto(False, self._icon_image)
        shell = tk.Frame(self, bg=NOTE_BG, padx=20, pady=16)
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        header = tk.Frame(shell, bg=NOTE_BG)
        header.pack(fill="x")
        title = tk.Label(header, text="清除记录与回收站", bg=NOTE_BG, fg="#1F2328",
                         font=(UI_FONT, 12, "bold"), cursor="fleur")
        title.pack(side="left")
        self._drag = None
        self._close_button = tk.Label(header, text="×", bg=NOTE_BG, fg=INK,
                                      font=(UI_FONT, 17), cursor="hand2")
        self._close_button.pack(side="right", padx=(8, 2))
        self._close_button.bind("<Button-1>", lambda e: self.destroy())
        self._minimize_button = tk.Label(header, text="−", bg=NOTE_BG, fg=INK,
                                         font=(UI_FONT, 17), cursor="hand2")
        self._minimize_button.pack(side="right", padx=(8, 2))
        self._minimize_button.bind("<Button-1>", lambda e: SettingsWindow._minimize(self))
        self._hotkey_borders = []
        self.bind("<Button-1>", SettingsWindow._drag_press.__get__(self))
        self.bind("<B1-Motion>", SettingsWindow._drag_move.__get__(self))
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.batch_mode = False
        self.checked = set()
        self._check_images = check_images(self, 22)
        tabs = tk.Frame(shell, bg=NOTE_BG)
        tabs.pack(fill="x", pady=(18, 12))
        self.tabs = {}
        for key, label in (("archives", "已清除待办"), ("deletedNotes", "已删除便签")):
            self.tabs[key] = self.button(tabs, label, lambda key=key: self.switch(key))
            self.tabs[key].pack(side="left", padx=(0, 12))
        self.batch_button = self.button(tabs, "批量删除", self.toggle_batch)
        self.batch_button.pack(side="right")
        self.delete_button = self.button(tabs, "删除", self.delete_selected)
        self.hint = tk.Label(shell, bg=NOTE_BG, fg=QUIET, font=(UI_FONT, 10), anchor="w")
        self.hint.pack(fill="x", pady=(0, 12))
        panes = tk.Frame(shell, bg=NOTE_BG)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=1, uniform="panes")
        panes.columnconfigure(1, weight=1, uniform="panes")
        panes.rowconfigure(0, weight=1)
        self.left = HistoryPane(panes)
        self.left.grid(row=0,column=0,sticky="nsew",padx=(0,10))
        self.right = HistoryPane(panes)
        self.right.grid(row=0,column=1,sticky="nsew",padx=(10,0))
        self._hotkey_borders = list(self.left.bars.values()) + list(self.right.bars.values())
        self.canvas = self.right.viewport
        self.content = self.right.content
        self.bind("<MouseWheel>", self.wheel)
        self.bind("<Escape>", lambda e: self.destroy())
        work = wintypes.RECT()
        user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0)
        width = min(max(960, self.winfo_reqwidth()), work.right-work.left-32)
        height = min(720, work.bottom-work.top-32)
        self.geometry(f"{width}x{height}+{work.left+(work.right-work.left-width)//2}+{work.top+(work.bottom-work.top-height)//2}")
        self.update_idletasks()
        hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        preference = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
        style = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, style & ~0x80 | 0x40000)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | 0x04 | 0x20)
        self.refresh()
        self.deiconify()

    def button(self, parent, text, command):
        font = tkfont.Font(root=self, family=UI_FONT, size=10)
        width, height = font.measure(text) + 24, font.metrics("linespace") + 12
        image = Image.new("RGBA", (width * 4, height * 4))
        ImageDraw.Draw(image).rounded_rectangle((2, 2, width*4-3, height*4-3), radius=28,
                                                fill=NOTE_BG, outline=NOTE_BORDER, width=4)
        photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=self)
        button = tk.Button(parent, text=text, command=command, image=photo, compound="center",
            bg=NOTE_BG, fg="#475569", activebackground=NOTE_BG, activeforeground="#1F2328",
            font=font, relief="flat", bd=0, padx=0, pady=0, cursor="hand2", highlightthickness=0)
        button._image = photo
        return button

    def switch(self, mode):
        self.mode = mode
        self.selected_id = None
        self.batch_mode = False
        self.checked.clear()
        self.refresh()

    def refresh(self):
        for key, tab in self.tabs.items():
            tab.configure(fg="#1F2328" if key == self.mode else QUIET,
                          relief="sunken" if key == self.mode else "flat")
        self.hint.configure(text="清除的已完成待办会保存在这里，按便签整理。" if self.mode == "archives"
                            else "彻底删除便签不会删除已清除待办的记录。")
        sources = {n["id"]: n for n in self.app.data["deletedNotes"] + self.app.data["notes"]}
        groups = {}
        for record in self.app.data[self.mode]:
            key = record["noteId"] if self.mode == "archives" else record["id"]
            groups.setdefault(key, []).append(record)
        self.groups = groups
        self.ids = list(groups)
        names = {}
        for key, records in groups.items():
            source = sources.get(key, records[-1])
            names[key] = (source.get("title") or "无标题", source.get("editedAt", ""))
        self.checked.intersection_update(self.ids)
        for child in self.left.content.winfo_children():
            child.destroy()
        self.names = []
        self.rows = {}
        for key in self.ids:
            name, stamp = names[key]
            if sum(value[0] == name for value in names.values()) > 1:
                name += " · " + (stamp.split(".")[0].replace("T", " ") or "编辑时间未知")
            self.names.append(name)
            row = tk.Frame(self.left.content, bg=NOTE_BG)
            row.pack(fill="x", pady=4)
            row.bind("<Button-1>", lambda e,k=key: self.choose(k))
            self.rows[key] = row
            if self.batch_mode:
                value = tk.BooleanVar(value=key in self.checked)
                box = tk.Checkbutton(row, variable=value, bg=NOTE_BG, activebackground=NOTE_BG,
                    indicatoron=False, image=self._check_images[False], selectimage=self._check_images[True],
                    command=lambda k=key, v=value: self.check(k,v.get()), bd=0, highlightthickness=0)
                box._value = value
                box.pack(side="left", anchor="center", padx=(0, 6))
            label = tk.Label(row, text=name, bg=NOTE_BG, fg="#2B2F36", anchor="w",
                             justify="left", font=(UI_FONT,10), cursor="hand2")
            label.pack(side="left",fill="x",expand=True)
            label.bind("<Configure>", lambda e, l=label: l.configure(wraplength=max(1,e.width-8)))
            label.bind("<Button-1>", lambda e,k=key: self.choose(k))
        if self.selected_id not in self.ids:
            self.selected_id = self.ids[0] if self.ids else None
        self.batch_button.configure(text="取消批量" if self.batch_mode else "批量删除")
        self.delete_button.pack_forget()
        if self.batch_mode and self.checked:
            self.delete_button.pack(side="right",padx=8)
        self.left.content.update_idletasks()
        self.left.update_region()
        self.highlight_selection()
        self.select()

    def wheel(self, event):
        pane = self.left if event.x_root < self.right.winfo_rootx() else self.right
        return pane.wheel(event)

    def choose(self, key):
        self.selected_id = key
        self.highlight_selection()
        self.select()

    def highlight_selection(self):
        for key, row in self.rows.items():
            color = "#E9EDF2" if key == self.selected_id else NOTE_BG
            row.configure(bg=color)
            for child in row.winfo_children():
                child.configure(bg=color)

    def check(self, key, selected):
        self.checked.add(key) if selected else self.checked.discard(key)
        self.delete_button.pack_forget()
        if self.checked:
            self.delete_button.pack(side="right",padx=8)

    def toggle_batch(self):
        self.batch_mode = not self.batch_mode
        self.checked.clear()
        self.refresh()

    def delete_selected(self):
        keys = set(self.checked)
        if not keys:
            return
        if messagebox.askyesno("批量删除", f"彻底删除选中的 {len(keys)} 张便签记录？此操作无法恢复。", parent=self):
            field = "noteId" if self.mode == "archives" else "id"
            mode = self.mode
            def change(data):
                data[mode] = [r for r in data[mode] if r[field] not in keys]
            self.app._store_history(change)

    def label(self, text, bold=False):
        label = tk.Label(self.content, text=text, bg=NOTE_BG, fg="#2B2F36",
            font=(UI_FONT, 10, "bold") if bold else (UI_FONT, 10), justify="left", anchor="w")
        label.pack(fill="x", pady=(6, 4), padx=4)
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(1, e.width-8)))

    def select(self, _event=None):
        for child in self.content.winfo_children():
            child.destroy()
        if self.selected_id is None:
            self.label("暂无记录")
            self.content.update_idletasks()
            self.right.update_region()
            return
        records = self.groups[self.selected_id]
        self.canvas.yview_moveto(0)
        if self.mode == "deletedNotes":
            note = records[0]
            actions = tk.Frame(self.content, bg=NOTE_BG)
            actions.pack(anchor="w", pady=(0,12))
            self.button(actions, "彻底删除", lambda: self.delete_record(note["id"])).pack(side="left")
            self.button(actions, "还原", lambda: self.app.recover_note(note["id"])).pack(side="left", padx=(8,0))
            self.label(note.get("title") or "无标题", True)
            for line in note["lines"]:
                self.label(("✓ " if line["checked"] else "□ ") + line["text"] if line["type"] == "todo" else line["text"])
        else:
            for batch in reversed(records):
                self.label("清除于 " + batch["clearedAt"].replace("T", " "), True)
                for item in batch["items"]:
                    self.label("✓ " + item["line"]["text"])
                tk.Frame(self.content, bg=NOTE_BORDER, height=1).pack(fill="x", pady=12)
        self.content.update_idletasks()
        self.right.update_region()

    def delete_record(self, record_id):
        message = ("彻底删除这张便签？此操作无法恢复，已清除待办的记录仍保留。"
                   if self.mode == "deletedNotes" else "永久删除这次清除记录？此操作无法恢复。")
        if messagebox.askyesno("彻底删除", message, parent=self):
            key = self.mode
            self.app._store_history(lambda data: data.__setitem__(key,
                [r for r in data[key] if r["id"] != record_id]))


class App:
    def __init__(self):
        self.data = load_data()
        settings = self.data["settings"]
        if "toggleHotkey" in settings:
            settings.setdefault("hideHotkey", settings.pop("toggleHotkey"))
        self.data.setdefault("archives", [])
        self.data.setdefault("deletedNotes", [])
        for note in self.data["notes"]:
            note.setdefault("editedAt", datetime.now().isoformat(timespec="microseconds"))
        self.history_win = None
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
        self._dev_mtime = os.path.getmtime(__file__) if os.getenv("Z_NOTE_DEV") else None
        self._takeover_event = kernel32.CreateEventW(None, False, False, DEV_TAKEOVER_EVENT)
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
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

    # 便签管理
    def _restore_notes(self):
        if not self.data["notes"] and not self.data["archives"] and not self.data["deletedNotes"]:
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
            d.setdefault("editedAt", datetime.now().isoformat(timespec="microseconds"))
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            d["x"] = min(max(0, d["x"]), max(0, sw - 120))
            d["y"] = min(max(0, d["y"]), max(0, sh - 60))
            d["w"] = max(d.get("w", 320), 300)  # 旧数据太窄会挤掉工具栏
            d["h"] = max(d.get("h", 300), 240)
            if d.get("z") not in ("bottom", "top"):  # 旧数据迁移：去掉 normal 态
                d["z"] = "top"
            w = NoteWindow(self, d)
            self.notes[d["id"]] = w
        if self.show_notes:
            self.show_all()

    def update_fonts(self):
        self.base_font.config(size=self.data["settings"]["fontSize"])
        for w in self.notes.values():
            w.done_font.config(size=self.data["settings"]["fontSize"])
            w._make_check_images()
            w.render()

    def set_opacity(self, percent):
        percent = min(100, max(50, 10 * round(percent / 10)))
        value = percent / 100
        self.data["settings"]["opacity"] = value
        for note in self.notes.values():
            note.d["opacity"] = value
            note.attributes("-alpha", value)
        scale = getattr(self, "opacity_scale", None)
        if scale is not None and scale.winfo_exists() and scale.get() != 100 - percent:
            scale.set(100 - percent)
        self.schedule_save()

    def new_note(self):
        cx, cy = cursor_pos()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        off = 24 * (self._cascade % 6); self._cascade += 1
        d = {"id": str(uuid.uuid4()),
             "x": min(cx + off, sw - 240), "y": min(cy + off, sh - 200),
             "w": 543, "h": 477, "z": "bottom",
             "opacity": self.data["settings"]["opacity"],
             "color": self.data["settings"]["color"], "visible": True,
             "editedAt": datetime.now().isoformat(timespec="microseconds"),
             "lines": [{"type": "todo", "text": "", "checked": False}]}
        self.data["notes"].append(d)
        w = NoteWindow(self, d)
        self.notes[d["id"]] = w
        w.show()
        w.text.focus_set()
        self.schedule_save()
        return w

    def _store_history(self, change):
        candidate = deepcopy(self.data)
        change(candidate)
        try:
            save_data(candidate)
        except Exception as exc:
            messagebox.showerror("保存失败", f"内容尚未移动，请重试。\n{exc}", parent=self.root)
            return False
        # 保留打开窗口引用的便签对象。
        for key in ("archives", "deletedNotes"):
            self.data[key] = candidate[key]
        self.data["notes"][:] = [next((n for n in self.data["notes"] if n["id"] == item["id"]), item)
                                for item in candidate["notes"]]
        for item in candidate["notes"]:
            target = next(n for n in self.data["notes"] if n["id"] == item["id"])
            target.update(item)
        if self.history_win is not None and self.history_win.winfo_exists():
            self.history_win.refresh()
        return True

    def archive_done(self, w):
        w._sync_lines_from_text()
        done = [(i, deepcopy(line)) for i, line in enumerate(w.d["lines"])
                if line["type"] == "todo" and line["checked"]]
        if not done:
            return
        def change(data):
            note = next(n for n in data["notes"] if n["id"] == w.d["id"])
            data["archives"].append({"id": str(uuid.uuid4()), "noteId": note["id"],
                "title": note.get("title", ""), "editedAt": note.get("editedAt", ""),
                "clearedAt": datetime.now().isoformat(timespec="seconds"),
                "emptied": len(done) == len(note["lines"]),
                "items": [{"index": i, "line": line} for i, line in done]})
            note["lines"] = [line for line in note["lines"]
                             if not (line["type"] == "todo" and line["checked"])] or [
                                 {"type": "text", "text": "", "checked": False}]
        if self._store_history(change):
            w.render(keep_cursor=False)

    def remove_note(self, w):
        w._sync_lines_from_text()
        def change(data):
            note = next(n for n in data["notes"] if n["id"] == w.d["id"])
            note["deletedAt"] = datetime.now().isoformat(timespec="seconds")
            data["deletedNotes"].append(note)
            data["notes"].remove(note)
        if self._store_history(change):
            self.notes.pop(w.d["id"], None)
            w.destroy()

    def recover_note(self, note_id):
        def change(data):
            note = next(n for n in data["deletedNotes"] if n["id"] == note_id)
            data["deletedNotes"].remove(note)
            note.pop("deletedAt", None)
            note["visible"] = True
            data["notes"].append(note)
        if self._store_history(change):
            note = next(n for n in self.data["notes"] if n["id"] == note_id)
            self.notes[note_id] = NoteWindow(self, note)
            self.notes[note_id].show()
            return True
        return False

    def return_archive(self, batch_id, item_index=None):
        batch = next(b for b in self.data["archives"] if b["id"] == batch_id)
        note_id = batch["noteId"]
        if note_id not in self.notes:
            if any(n["id"] == note_id for n in self.data["deletedNotes"]):
                if not messagebox.askyesno("恢复便签", "原便签已删除，先恢复便签再加入待办？", parent=self.history_win):
                    return
                if not self.recover_note(note_id):
                    return
            else:
                if not messagebox.askyesno("新建便签", "原便签已彻底删除，新建便签并加入待办？", parent=self.history_win):
                    return
                w = self.new_note()
                w.d["title"] = batch["title"]
                note_id = w.d["id"]
        def change(data):
            note = next(n for n in data["notes"] if n["id"] == note_id)
            items = batch["items"] if item_index is None else [batch["items"][item_index]]
            if (item_index is not None or batch.get("emptied")) and note["lines"] == [
                    {"type": "text", "text": "", "checked": False}]:
                note["lines"] = []
            for item in items:
                line = deepcopy(item["line"])
                if item_index is None:
                    note["lines"].insert(min(item["index"], len(note["lines"])), line)
                else:
                    line["checked"] = False
                    note["lines"].append(line)
            note["editedAt"] = datetime.now().isoformat(timespec="microseconds")
            if item_index is None:
                data["archives"] = [b for b in data["archives"] if b["id"] != batch_id]
        if self._store_history(change):
            self.notes[note_id].render(keep_cursor=False)

    def hide_all(self):
        for w in self.notes.values():
            w.hide()
        self.schedule_save()

    def show_all(self):
        for w in self.notes.values():
            w.show()
        self.schedule_save()

    # 托盘
    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem(lambda _item: f"新建便签  {self.hotkey_text('new')}", lambda: self.q.put("new")),
            pystray.MenuItem(lambda _item: f"隐藏所有便签  {self.hotkey_text('hide')}", lambda: self.q.put("hide")),
            pystray.MenuItem(lambda _item: f"显示所有便签  {self.hotkey_text('show')}", lambda: self.q.put("show")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("清除记录与回收站…", lambda: self.q.put("history")),
            pystray.MenuItem("设置…", lambda: self.q.put("settings")),
            pystray.MenuItem("退出", lambda: self.q.put("quit")),
        )
        self.icon = pystray.Icon(APP_NAME, _make_tray_icon(), "z-note 便签", menu)
        self.icon.run_detached()

    def hotkey_text(self, action):
        _, setting, default = HOTKEY_ACTIONS[action]
        return self.data["settings"].get(setting, default)

    # 在 Tk 所在线程注册，编辑时可立即检查占用并替换绑定。
    def _start_hotkeys(self):
        self._hotkey_hwnd = self.root.winfo_id()
        self._active_hotkeys = {}
        self._hotkey_ids = {action: identity for action, (identity, _, _) in HOTKEY_ACTIONS.items()}
        @_WNDPROC
        def window_proc(hwnd, message, wparam, lparam):
            if message == WM_HOTKEY:
                action = next((action for action, identity in self._hotkey_ids.items()
                               if identity == wparam), None)
                if action:
                    capture = getattr(self, "_hotkey_capture", None)
                    if capture:
                        self.q.put(("capture_hotkey", *capture, self.hotkey_text(action)))
                    else:
                        self.q.put(action)
                return 0
            if message == 0x0082:  # WM_NCDESTROY
                for identity in self._hotkey_ids.values():
                    user32.UnregisterHotKey(hwnd, identity)
                user32.SetWindowLongPtrW(hwnd, -4, self._hotkey_old_proc)
            return user32.CallWindowProcW(self._hotkey_old_proc, hwnd, message, wparam, lparam)
        self._hotkey_proc = window_proc
        self._hotkey_old_proc = user32.SetWindowLongPtrW(
            self._hotkey_hwnd, -4, ctypes.cast(window_proc, ctypes.c_void_p))
        for action, (identity, _, _) in HOTKEY_ACTIONS.items():
            value = self.hotkey_text(action)
            modifiers, key = parse_hotkey(value)
            if user32.RegisterHotKey(self._hotkey_hwnd, identity, modifiers | 0x4000, key):
                self._active_hotkeys[action] = value

    def update_hotkey(self, action, value):
        if any(other != action and self.hotkey_text(other) == value for other in HOTKEY_ACTIONS):
            return "此快捷键已用于另一项功能，请换一个组合键。"
        if self._active_hotkeys.get(action) == value:
            return None
        modifiers, key = parse_hotkey(value)
        # 新组合注册成功后才释放旧组合，冲突时原绑定继续有效。
        identity, setting, _ = HOTKEY_ACTIONS[action]
        old_identity = self._hotkey_ids[action]
        new_identity = identity + 100 if old_identity == identity else identity
        if not user32.RegisterHotKey(self._hotkey_hwnd, new_identity, modifiers | 0x4000, key):
            return "此快捷键已被系统或其他应用占用，请换一个组合键。"
        user32.UnregisterHotKey(self._hotkey_hwnd, old_identity)
        self._hotkey_ids[action] = new_identity
        self._active_hotkeys[action] = value
        self.data["settings"][setting] = value
        self.save_now()
        if hasattr(self, "icon"):
            self.icon.update_menu()
        return None

    def reset_hotkeys(self):
        defaults = {action: default for action, (_, _, default) in HOTKEY_ACTIONS.items()}
        if all(self.hotkey_text(action) == value and self._active_hotkeys.get(action) == value
               for action, value in defaults.items()):
            return None
        old_ids, old_active = self._hotkey_ids.copy(), self._active_hotkeys.copy()
        for action in old_active:
            user32.UnregisterHotKey(self._hotkey_hwnd, old_ids[action])
        registered = {}
        for action, (identity, _, default) in HOTKEY_ACTIONS.items():
            modifiers, key = parse_hotkey(default)
            if not user32.RegisterHotKey(self._hotkey_hwnd, identity, modifiers | 0x4000, key):
                for new_id in registered.values():
                    user32.UnregisterHotKey(self._hotkey_hwnd, new_id)
                restored = {}
                for old_action, value in old_active.items():
                    old_modifiers, old_key = parse_hotkey(value)
                    if user32.RegisterHotKey(self._hotkey_hwnd, old_ids[old_action],
                                             old_modifiers | 0x4000, old_key):
                        restored[old_action] = value
                self._active_hotkeys = restored
                if len(restored) != len(old_active):
                    return "默认快捷键被占用，且部分原快捷键未能恢复，请重新设置。"
                return "默认快捷键被系统或其他应用占用，原快捷键已保留。"
            registered[action] = identity
        self._hotkey_ids = {action: identity for action, (identity, _, _) in HOTKEY_ACTIONS.items()}
        self._active_hotkeys = defaults
        for action, (_, setting, default) in HOTKEY_ACTIONS.items():
            self.data["settings"][setting] = default
        self.save_now()
        if hasattr(self, "icon"):
            self.icon.update_menu()
        return None

    def _pump(self):
        if self._takeover_event and kernel32.WaitForSingleObject(self._takeover_event, 0) == 0:
            self.quit()
        if self._dev_mtime is not None and os.path.getmtime(__file__) != self._dev_mtime:
            self.quit(75)
        try:
            while True:
                act = self.q.get_nowait()
                if isinstance(act, tuple) and act[0] == "capture_hotkey":
                    _, window, action, value = act
                    if window.winfo_exists():
                        window._apply_hotkey(action, value)
                elif act == "new":
                    self.new_note()
                elif act == "hide":
                    self.hide_all()
                elif act == "show":
                    self.show_all()
                elif act == "history":
                    if self.history_win is None or not self.history_win.winfo_exists():
                        self.history_win = HistoryWindow(self)
                    self.history_win.refresh()
                    self.history_win.deiconify()
                    self.history_win.lift()
                    self.history_win.focus_force()
                elif act == "settings":
                    if self.settings_win is None or not self.settings_win.winfo_exists():
                        self.settings_win = SettingsWindow(self)
                    win = self.settings_win
                    win.attributes("-topmost", True)
                    win.deiconify()
                    win.lift()
                    win.focus_force()
                    self.root.after(300, lambda: win.winfo_exists() and win.attributes("-topmost", False))
                elif act == "quit":
                    self.quit()
        except queue.Empty:
            pass
        self.root.after(120, self._pump)

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

    def quit(self, exit_code=0):
        for w in self.notes.values():
            w.d["x"], w.d["y"] = w.winfo_x(), w.winfo_y()
        self.save_now()
        try:
            self.icon.stop()
        except Exception:
            pass
        self.root.destroy()
        os._exit(exit_code)

def main():
    ensure_single_instance()
    App().root.mainloop()

if __name__ == "__main__":
    main()
