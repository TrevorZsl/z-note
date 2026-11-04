# -*- coding: utf-8 -*-
"""z-note 雏形：常驻托盘的桌面便签（自由文本 + 可勾选待办）。
运行: python main.py   依赖安装: python -m pip install -r requirements.txt
"""
import atexit, ctypes, math, os, queue, struct, sys, time, uuid, winreg
from copy import deepcopy
from datetime import datetime
import tkinter as tk
import subprocess
import threading
import webbrowser
from pathlib import Path
from _tkinter import DONT_WAIT, WINDOW_EVENTS
from tkinter import font as tkfont, messagebox
from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

from storage import DATA_FILE, DEFAULT_SETTINGS, load_data, save_data, backup_before_update
from version import APP_VERSION
import updater
from resize_surface import ResizeSurface
from bundled_fonts import CLASSIC_FAMILY, FONT_CHOICES, FONT_FAMILIES, FONT_DIR, load_fonts

APP_NAME = "z-note"
UI_FONT = CLASSIC_FAMILY

NOTE_BG, NOTE_BORDER = "#FFFFFF", "#DFE2E6"
T_COLOR = "#010203"   # 窗口透明色键（圆角外的部分透出去）
RADIUS = 20
EDGE = 7                              # 窗口边缘可拖拽调大小的宽度
CORNER = RADIUS + 4                   # 四角斜向拉伸热区：整个圆角区都能触发，比 EDGE 好按
ICON_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets", "icons")
PREFIX_PAD = "  "                       # 勾选框后跟 2 个空格
INK, QUIET = "#6B7280", "#666D75"      # 图标与状态栏的墨色（冷灰单色系）

# ---------- Win32 ----------
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.SetCapture.argtypes = [wintypes.HWND]
user32.SetCapture.restype = wintypes.HWND
user32.GetCapture.argtypes = []
user32.GetCapture.restype = wintypes.HWND
user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.IsChild.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetTopWindow.argtypes = [wintypes.HWND]
user32.GetTopWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
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
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
user32.GetMonitorInfoW.restype = wintypes.BOOL
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
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
WM_HOTKEY = 0x0312
HOTKEY_ACTIONS = {"new": (1, "newHotkey", "Ctrl+Alt+N"),
                  "hide": (2, "hideHotkey", "Ctrl+Alt+H"),
                  "show": (3, "showHotkey", "Ctrl+Alt+S")}
LOCAL_HOTKEY_ACTIONS = {"convert": ("convertHotkey", "Ctrl+Shift+L")}
HOTKEY_LABELS = {"new": "新建便签", "show": "显示所有便签", "hide": "隐藏所有便签",
                 "convert": "文字／待办切换"}
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

class _WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]

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

_WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                  wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                 _WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.UnhookWinEvent.restype = wintypes.BOOL


def _bottom_insert_after(note_hwnd, reference_hwnd=None):
    # HWND_BOTTOM can put a note behind the desktop after Win+D. Insert just
    # above the first visible desktop host, below the windows still above it.
    # HWND_NOTOPMOST does not move an already non-topmost window. When the
    # desktop is first in that band, HWND_TOP is needed to get above it.
    previous = 0  # HWND_TOP; callers use SWP_NOACTIVATE.
    topmost = None
    reference_seen = reference_hwnd is None
    hwnd = user32.GetTopWindow(None)
    name = ctypes.create_unicode_buffer(256)
    while hwnd:
        if hwnd == reference_hwnd:
            reference_seen = True
        if hwnd != note_hwnd and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            cloaked = wintypes.DWORD()
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:
                hwnd = user32.GetWindow(hwnd, 2)
                continue
            user32.GetClassNameW(hwnd, name, len(name))
            if name.value in ("Progman", "WorkerW"):
                if not reference_seen:
                    # Show Desktop raises the shell above the normal band.
                    # Follow that band temporarily, below other topmost windows.
                    return topmost or -1  # HWND_TOPMOST only when no anchor exists.
                if previous == 0 and user32.GetWindowLongPtrW(note_hwnd, -20) & 0x8:
                    return -2  # Leave the temporary band when the desktop drops back.
                return previous
            if user32.GetWindowLongPtrW(hwnd, -20) & 0x8:
                topmost = hwnd
            else:
                previous = hwnd
        hwnd = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT
    return 1  # HWND_BOTTOM; Explorer may be restarting.

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

# ---------- 开机启动 ----------
def set_autostart(on):
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        if on:
            cmd = f'"{sys.executable}"' if getattr(sys, "frozen", False) else \
                  f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
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
    """圆形悬浮底 + Lucide 图标；置顶、置底激活时保留浅灰圆角底。"""

    def __init__(self, master, icon, get_tip, cmd, bg, size=30):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._bg, self._active = bg, False
        self._image = load_icon(self, icon)
        self._active_image = load_icon(self, icon, active=True) if icon in ("pin", "arrow-down-to-line", "columns-2") else self._image
        r = 13
        self._pill = self.create_oval(size/2 - r, size/2 - r, size/2 + r, size/2 + r,
                                      state="hidden", outline="")
        self._active_pill = (round_rect(self, 0, 0, size, size, 7, fill="#E5E9EF", outline="", state="hidden")
                             if icon in ("pin", "arrow-down-to-line") else None)
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
        if self._active_pill is not None:
            self.itemconfig(self._active_pill, state="normal" if on else "hidden")
        if on:
            self.itemconfig(self._icon, image=self._active_image)
        else:
            self.itemconfig(self._icon, image=self._image)

# ---------- 便签窗口 ----------
class VerticalThumb(tk.Canvas):
    def __init__(self, parent, target, bg):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, cursor="arrow")
        self.target = target
        self._thumb_image = None
        self._drag_offset = None
        self.bind("<Configure>", lambda e: self.set(*target.yview()))
        self.bind("<Button-1>", self.move)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_offset", None))

    def set(self, first, last):
        first, last = float(first), float(last)
        self.delete("all")
        self._thumb_image = None
        length = self.winfo_height()-8
        if last-first >= .999 or length < 1:
            return
        height = max(6, round((last-first)*length) + 6)
        scale = 4
        image = Image.new("RGBA", (8*scale, (height+2)*scale), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle(
            (scale, scale, 7*scale-1, (height+1)*scale-1),
            radius=3*scale, fill="#A7B0BE")
        self._thumb_image = ImageTk.PhotoImage(
            image.resize((8, height+2), Image.Resampling.LANCZOS), master=self)
        self.create_image(0, round(first*length), anchor="nw", image=self._thumb_image, tags="thumb")

    def move(self, event):
        first, last = self.target.yview()
        if last-first >= .999:
            return
        position = (event.y-4)/max(1,self.winfo_height()-8)
        if self._drag_offset is None:
            self._drag_offset = position-first if first <= position <= last else (last-first)/2
        self.target.yview_moveto(position-self._drag_offset)


class EdgeFade:
    """正文内的原生透明子窗口，不参与 Tk 布局和鼠标命中。"""
    def __init__(self, body):
        self.body = body
        self.height = 0
        self._size = None
        self._visible = (False, False)
        self._resize_visible = (False, False)
        self.windows = []
        for _ in range(2):
            hwnd = user32.CreateWindowExW(
                0x08080020, "STATIC", None, 0x40000000,
                0, 0, 0, 0, body.winfo_id(), None, None, None)
            if not hwnd:
                self.destroy()
                raise ctypes.WinError()
            self.windows.append(hwnd)
        body.bind("<Destroy>", lambda e: self.destroy() if e.widget == body else None, add="+")

    def update(self, first, last):
        width, viewport = self.body.winfo_width(), self.body.winfo_height()
        height = min(max(1, round(self.body.winfo_fpixels("18p"))), viewport // 4)
        self._resize_visible = (float(first) > 1e-6, float(last) < 1-1e-6)
        note = self.body.master
        if getattr(note, "_resizing", False) and note._resize_surface is not None:
            for hwnd, visible in zip(self.windows, self._visible):
                if visible:
                    user32.ShowWindow(hwnd, 0)
            self._visible = (False, False)
            self.height = height
            return
        if width < 1 or height < 1:
            for hwnd in self.windows:
                user32.ShowWindow(hwnd, 0)
            self.height = 0
            self._visible = (False, False)
            return
        size = (width, height, viewport)
        if size != self._size:
            for hwnd, bottom in zip(self.windows, (False, True)):
                self._paint(hwnd, width, height, bottom)
                user32.SetWindowPos(hwnd, 0, 0, viewport-height if bottom else 0,
                                    width, height, 0x0010)
            self.height = height
            self._size = size
        visible = (float(first) > 1e-6, float(last) < 1-1e-6)
        for hwnd, show, previous in zip(self.windows, visible, self._visible):
            if show != previous:
                user32.ShowWindow(hwnd, 8 if show else 0)  # SW_SHOWNA，不激活便签。
        self._visible = visible

    @staticmethod
    def _paint(hwnd, width, height, bottom):
        # UpdateLayeredWindow 需要预乘 BGRA；白色的各通道恰好都等于 alpha。
        rows = [bytes([round(255 * y / max(1, height-1))]) * (4 * width)
                for y in range(height)]
        pixels = b"".join(rows if bottom else reversed(rows))
        header = ctypes.create_string_buffer(struct.pack(
            "<IiiHHIIiiII", 40, width, -height, 1, 32, 0, len(pixels), 0, 0, 0, 0))
        bits = ctypes.c_void_p()
        dc = gdi32.CreateCompatibleDC(None)
        if not dc:
            raise ctypes.WinError()
        bitmap = old = None
        try:
            bitmap = gdi32.CreateDIBSection(dc, header, 0, ctypes.byref(bits), None, 0)
            if not bitmap:
                raise ctypes.WinError()
            ctypes.memmove(bits, pixels, len(pixels))
            old = gdi32.SelectObject(dc, bitmap)
            size = wintypes.SIZE(width, height)
            origin = _POINT(0, 0)
            blend = ctypes.c_uint32(0x01FF0000)  # AC_SRC_OVER、255、AC_SRC_ALPHA。
            if not user32.UpdateLayeredWindow(hwnd, None, None, ctypes.byref(size), dc,
                                             ctypes.byref(origin), 0, ctypes.byref(blend), 2):
                raise ctypes.WinError()
        finally:
            if old:
                gdi32.SelectObject(dc, old)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dc)

    def destroy(self):
        for hwnd in self.windows:
            user32.DestroyWindow(hwnd)
        self.windows.clear()


gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.DeleteDC.argtypes = [wintypes.HDC]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p,
    ctypes.c_void_p, wintypes.HDC, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL


class NoteEditor:
    """一栏正文编辑器：Text + 勾选框/删除线覆盖层 + 分块滚动。NoteWindow 持有 1~2 个实例。"""

    def __init__(self, win, col):
        self.win, self.col = win, col
        self.d = win.d  # 与窗口共享同一便签字典；只有 lines 按 col 分流
        self.app, self._bg, self.tk = win.app, win._bg, win.tk
        self.done_font = tkfont.Font(family=win.app.note_font_family(),
                                     size=win.app.data["settings"]["fontSize"])
        self.body = tk.Canvas(win, bg=self._bg, bd=0, highlightthickness=0, yscrollincrement=1)
        self.text = tk.Text(self.body, bg=self._bg, fg="#2B2F36", bd=0, highlightthickness=0,
                            padx=12, pady=10, wrap="char", spacing1=9, spacing3=10,
                            selectbackground=self._bg, inactiveselectbackground=self._bg,
                            selectforeground="#2B2F36", selectborderwidth=0,
                            font=win.app.base_font, insertbackground="#2B2F36", cursor="xterm")
        self.text.place(x=0, y=0, relwidth=1)
        self.scroll_thumb = VerticalThumb(win, self.body, self._bg)
        self._make_check_images()
        self.text.tag_config("done", font=self.done_font)  # 只加删除线，不改字色
        self.text.tag_config("selection_fill", background="#DCE7F3", foreground="#2B2F36")
        self.text.tag_raise("selection_fill", "sel")
        self.text.bind("<<Selection>>", self._paint_selection)
        self.text.tag_configure("ime_preedit", underline=True)
        self._strike_widgets = []
        self._selection_masks = []
        self._checkbox_widgets = []
        self._document_height = 0
        self._content_height = 1
        self._chunk_start = 0
        self._buffer_margin = 0
        self._layout_busy = False
        self._layout_pending = False
        self._layout_measure_pending = False
        self._row_layout = {}
        self._wheel_job = None
        self._wheel_remaining = 0
        self._selection_scroll_job = None
        self._strike_job = None
        self._edge_fade = EdgeFade(self.body)
        self._row_drag = None
        self._row_drag_job = None
        # Tk 会把软换行选区画进右边距；独立覆盖层让拖拽区始终保持便签底色。
        self._drag_gutter = tk.Frame(self.text, bg=self._bg)
        self._drag_gutter.place(relx=1, x=-int(self.text.cget("padx"))-54,
                                y=0, width=54, relheight=1, bordermode="ignore")
        self._drag_gutter.bind("<Motion>", self._hover_row)
        self._drag_gutter.bind("<Enter>", self._hover_row)
        self._drag_gutter.bind("<Leave>", lambda e: self.win.after_idle(self._hide_drag_handle))
        for event in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>",
                      "<Button-3>", "<MouseWheel>", "<Control-MouseWheel>"):
            self._drag_gutter.bind(event, lambda e, sequence=event: self.text.event_generate(
                sequence, x=e.x_root - self.text.winfo_rootx(),
                y=e.y_root - self.text.winfo_rooty(), time=e.time, state=e.state,
                **({"delta": e.delta} if "MouseWheel" in sequence else {})))
        self._drag_handle = tk.Canvas(self.text, width=24, height=24, bg=self._bg,
                                      bd=0, highlightthickness=0, cursor="fleur", takefocus=1)
        _hook_move_cursor(self._drag_handle)
        self._drag_handle.bind("<Enter>", _cursor_small)
        self._drag_handle.bind("<Leave>", _cursor_restore)
        for y in (8, 12, 16):
            self._drag_handle.create_line(7, y, 17, y, fill="#9198A2")
        self._drag_handle.bind("<Button-1>", self._row_drag_press)
        self._drag_handle.bind("<B1-Motion>", self._row_drag_motion)
        self._drag_handle.bind("<ButtonRelease-1>", self._row_drag_release)
        self._drag_handle.bind("<Escape>", self._cancel_row_drag)
        self._drag_escape = win.bind("<Escape>", self._cancel_row_drag, add="+")
        self._drag_leave = win.bind("<Leave>",
            lambda e: self.win.after_idle(self._hide_drag_handle), add="+")
        self._drag_handle.bind("<Leave>", self._hide_drag_handle, add="+")
        self.text.tag_configure("drag_margin", rmargin=54)
        self.text.bind("<Motion>", self._hover_row)
        self.text.bind("<Enter>", self._hover_row)
        self.text.bind("<Leave>", lambda e: self.win.after_idle(self._hide_drag_handle))
        self.body.bind("<Unmap>", self._cancel_row_drag)
        self.body.configure(yscrollcommand=self._text_scrolled)
        self.body.bind("<Configure>", self._body_configured)
        win._bind_ime(self.text)
        t = self.text
        t.bind("<KeyRelease>", self._on_keyrelease)
        t.bind("<KeyPress-Return>", self._on_enter)
        t.bind("<KeyPress-BackSpace>", self._on_backspace)
        t.bind("<KeyPress-BackSpace>", self._hide_strikes_before_delete, add="+")
        t.bind("<KeyPress-Delete>", self._hide_strikes_before_delete)
        t.bind("<Left>", self._on_left)
        t.bind("<Up>", self._on_vertical)
        t.bind("<Down>", self._on_vertical)
        tags = t.bindtags()
        t.bindtags(("NoteShortcuts", tags[0], tags[1], "TodoCaret", *tags[2:]))
        t.bind("<<Copy>>", lambda e: self._clipboard_edit("tk_textCopy"))
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
        t.bind("<Control-MouseWheel>", self.win._ctrl_wheel)
        self.body.bind("<Control-MouseWheel>", self.win._ctrl_wheel)
        t.bind("<B1-Motion>", lambda e: self._row_drag_motion(e) if self._row_drag is not None
               else self._selection_scroll(e), add="+")
        t.bind("<ButtonRelease-1>", lambda e: self._row_drag_release(e) if self._row_drag is not None
               else self._stop_selection_scroll(e), add="+")
        t.bind("<Unmap>", self._stop_selection_scroll)
        t.bind("<Unmap>", self._stop_wheel, add="+")
        self.render()

    # ---- 便签数据按栏分流 ----
    @property
    def lines(self):
        if self.col == 0:
            return self.d["lines"]
        return self.d.setdefault("lines2", [{"type": "todo", "text": "", "checked": False}])

    @lines.setter
    def lines(self, value):
        if self.col == 0:
            self.d["lines"] = value
        else:
            self.d["lines2"] = value

    # 编辑器不是控件，定时器等委托给所在窗口。
    def after(self, *args):
        return self.win.after(*args)

    def after_idle(self, *args):
        return self.win.after_idle(*args)

    def after_cancel(self, *args):
        return self.win.after_cancel(*args)

    def update_idletasks(self):
        self.win.update_idletasks()

    def focus_get(self):
        return self.win.focus_get()

    def teardown(self):
        self._cancel_row_drag()
        _DRAG_HWNS.discard(self._drag_handle.winfo_id())
        self.win.unbind("<Escape>", self._drag_escape)
        self.win.unbind("<Leave>", self._drag_leave)
        self._stop_wheel()
        self._stop_selection_scroll()
        if self._strike_job is not None:
            self.win.after_cancel(self._strike_job)
            self._strike_job = None
        self.scroll_thumb.destroy()
        self.body.destroy()

    def _body_configured(self, event):
        self._cancel_row_drag()
        changed = event.width != getattr(self, "_body_width", None)
        self._body_width = event.width
        previous_x = getattr(self, "_body_x", None)
        self._body_x = event.x
        if previous_x is not None and previous_x != event.x:
            # 右栏随分隔线移动后，Tk 的透明图标可能沿用旧的裁剪区域。
            for image in self._check_images.values():
                image.paste(ImageTk.getimage(image))
        self._queue_strikes(measure=changed)

    def _text_scrolled(self, first, last):
        if self._row_drag is None:
            self._drag_handle.place_forget()
        self.scroll_thumb.set(first, last)
        self._edge_fade.update(first, last)
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
        self._wheel_job = self.win.after(16, self._step_wheel)
        self.body.update_idletasks()
        # 先处理整体移动产生的窗口重画事件，再提交这一帧；不运行下一帧的计时器。
        while self.tk.dooneevent(WINDOW_EVENTS | DONT_WAIT):
            pass
        self.body.update_idletasks()

    def _stop_wheel(self, _event=None):
        if self._wheel_job is not None:
            self.win.after_cancel(self._wheel_job)
            self._wheel_job = None
        self._wheel_remaining = 0

    def _body_index(self, x=0, y=0):
        return self.text.index(f"@{x},{round(self.body.canvasy(y)) - self._chunk_start}")

    # ---- 整条拖拽：预览独立于 Text，松手前不改正文或撤销栈 ----
    def _hide_drag_handle(self, _event=None):
        if self._row_drag is None and self._drag_handle.winfo_exists():
            x, y = self._drag_handle.winfo_pointerxy()
            if not (self.text.winfo_rootx() <= x < self.text.winfo_rootx()+self.text.winfo_width() and
                    self.body.winfo_rooty() <= y < self.body.winfo_rooty()+self.body.winfo_height()):
                self._drag_handle.place_forget()

    def _hover_row(self, event):
        if self._row_drag is not None:
            return
        x, y = event.x_root-self.text.winfo_rootx(), event.y_root-self.text.winfo_rooty()
        row = int(self.text.index(f"@{x},{y}").split(".")[0])
        if row > len(self.lines):
            return
        height = self.text.count(f"{row}.0", f"{row+1}.0", "ypixels")[0]
        offset = self.text.count("1.0", f"{row}.0", "ypixels")
        top = int(self.text.cget("pady")) + (offset[0] if offset else 0) - self._chunk_start
        if not top <= y < top + height:
            self._drag_handle.place_forget()
            return
        self._drag_handle._row = row - 1
        viewport_top = -self.text.winfo_y()
        middle = (max(top, viewport_top) + min(top+height, viewport_top+self.body.winfo_height()))/2
        self._drag_handle.place(x=self.text.winfo_width()-55+int(self.text.cget("padx")),
                                y=round(middle)-12, width=24, height=24, bordermode="ignore")
        tk.Misc.lift(self._drag_handle)

    def _measure_drag_rows(self, text=None):
        text = self.text if text is None else text
        rows = []
        for i in range(1, int(text.index("end-1c").split(".")[0]) + 1):
            height = text.count(f"{i}.0", f"{i+1}.0", "ypixels")[0]
            # 使用 Text 自己的软换行边界，浮框与正文的断行保持一致。
            start, segments = f"{i}.0", []
            while text.compare(start, "<", f"{i}.end"):
                end = text.index(f"{start} display lineend +1c")
                if text.compare(end, ">", f"{i}.end"):
                    end = text.index(f"{i}.end")
                content = text.get(start, end)
                if not segments and text.dump(f"{i}.0", f"{i}.1", image=True):
                    content = content[len(PREFIX_PAD):]
                segments.append(content)
                if text.compare(end, "<=", start):
                    break
                start = end
            rows.append((height, segments or [""]))
        return rows

    def _row_drag_press(self, event):
        if any(self.win._ime_composing(editor.text) for editor in self.win.editors):
            return "break"
        for editor in self.win.editors:
            editor._stop_wheel()
            editor._stop_selection_scroll()
            editor._sync_lines_from_text()
            editor._draw_strikes()
        self._remember_edit()
        self._hover_row(event)
        source = self._drag_handle._row
        rows = self._measure_drag_rows()
        top = int(self.text.cget("pady")) + sum(h for h, _ in rows[:source])
        self._row_drag = dict(source=source, target=source, rows=rows,
                              press=(event.x_root, event.y_root), pointer=(event.x_root, event.y_root),
                              offset=event.y_root-self.body.winfo_rooty()+self.body.canvasy(0)-top,
                              canvas=None, cursor=self.text.index("insert"),
                              scroll=self.body.canvasy(0))
        if self.d.get("twoColumn"):
            self._row_drag.update(destination=self, layouts={editor: editor._measure_drag_rows()
                                  for editor in self.win.editors}, floating_rows={self: rows[source]},
                                  scrolls={editor: editor.body.canvasy(0) for editor in self.win.editors})
        self._drag_handle.focus_set()
        self._drag_handle.grab_set()
        return "break"

    def _row_drag_motion(self, event):
        drag = self._row_drag
        if drag is None:
            return "break"
        drag["pointer"] = (event.x_root, event.y_root)
        if drag["canvas"] is None:
            if max(abs(a-b) for a, b in zip(drag["pointer"], drag["press"])) < 5:
                return "break"
            drag["canvas"] = tk.Canvas(self.body, bg=self._bg, bd=0, highlightthickness=0)
            drag["canvas"].place(x=0, y=0, relwidth=1, relheight=1)
            tk.Misc.lift(drag["canvas"])
            if self.d.get("twoColumn"):
                drag["canvases"] = {self: drag["canvas"]}
                for editor in self.win.editors:
                    if editor is self:
                        continue
                    canvas = tk.Canvas(editor.body, bg=self._bg, bd=0, highlightthickness=0)
                    canvas.place(x=0, y=0, relwidth=1, relheight=1)
                    drag["canvases"][editor] = canvas
                    measure = tk.Text(self.win, **{key: editor.text.cget(key) for key in
                        ("font", "padx", "pady", "spacing1", "spacing2", "spacing3", "wrap", "bd", "highlightthickness")})
                    drag["measure"] = measure
                    measure.place(x=-10000, y=0, width=editor.body.winfo_width(), height=editor.body.winfo_height())
                    for tag in ("todo_indent", "drag_margin"):
                        measure.tag_configure(tag, **{key: editor.text.tag_cget(tag, key)
                                              for key in ("lmargin1", "lmargin2", "rmargin")})
                    line = self.lines[drag["source"]]
                    if line["type"] == "todo":
                        measure.image_create("1.0", image=editor._check_slots[line["checked"]])
                        measure.insert("end", PREFIX_PAD)
                    measure.insert("end", line["text"])
                    measure.tag_add("drag_margin", "1.0", "end")
                    if line["type"] == "todo":
                        measure.tag_add("todo_indent", "1.0", "end")
                    measure.update_idletasks()
                    drag["floating_rows"][editor] = editor._measure_drag_rows(measure)[0]
                drag["float"] = tk.Canvas(self.win, bg=self._bg, bd=0, highlightthickness=0)
            self.body.update_idletasks()
            self._row_drag_tick()
        else:
            self._paint_row_drag()
        return "break"

    def _row_drag_tick(self):
        self._row_drag_job = None
        drag = self._row_drag
        if drag is None or drag["canvas"] is None:
            return
        editor = self._drag_destination(drag["pointer"])
        if editor is not None:
            x, y = drag["pointer"]
            y -= editor.body.winfo_rooty()
            height = editor.body.winfo_height()
            edge = min(36, height//4)
            step = -8 if y < edge else (8 if y > height-edge else 0)
            if step:
                editor.body.yview_scroll(step, "units")
        self._paint_row_drag()
        self._row_drag_job = self.win.after(30, self._row_drag_tick)

    def _paint_row_drag(self):
        drag = self._row_drag
        cv = drag["canvas"]
        if cv is None:
            return
        if "canvases" in drag:
            return self._paint_columns_drag()
        cv.delete("all")
        width, viewport = self.body.winfo_width(), self.body.winfo_height()
        pointer_y = drag["pointer"][1] - self.body.winfo_rooty()
        scroll = self.body.canvasy(0)
        pad = int(self.text.cget("pady"))
        # 目标使用当前预览布局的中点，源占位保留在布局中以避免边界抖动。
        order = [i for i in range(len(self.lines)) if i != drag["source"]]
        order.insert(drag["target"], drag["source"])
        y = pad - scroll
        target = len(order)
        for slot, i in enumerate(order):
            height = drag["rows"][i][0]
            if y <= pointer_y < y+height:
                if i == drag["source"]:
                    target = drag["target"]
                else:
                    boundary = slot + (pointer_y >= y+height/2)
                    target = boundary - (drag["target"] < boundary)
                break
            if pointer_y < y:
                target = slot
                break
            y += height
        drag["target"] = max(0, min(len(order)-1, target))
        order.remove(drag["source"])
        order.insert(drag["target"], drag["source"])
        y = pad - scroll
        for i in order:
            height = drag["rows"][i][0]
            if y < viewport and y+height > 0:
                if i == drag["source"]:
                    round_rect(cv, 5, y+2, width-5, y+height-2, min(9, height/3),
                               fill=mix(self._bg, "#B6C4D7", .22), outline="")
                    self._draw_drag_row(cv, i, y, muted=True)
                else:
                    self._draw_drag_row(cv, i, y)
            y += height
        height = drag["rows"][drag["source"]][0]
        y = pointer_y-drag["offset"]
        round_rect(cv, 6, y+5, width-4, y+height, min(10, height/3),
                   fill=mix(self._bg, "#677588", .18), outline="")
        round_rect(cv, 3, y+1, width-7, y+height-4, min(10, height/3),
                   fill=self._bg, outline="#AAB7C8")
        self._draw_drag_row(cv, drag["source"], y)

    def _drag_destination(self, pointer):
        x, y = pointer
        return next((editor for editor in self.win.editors
                     if 0 <= x-editor.body.winfo_rootx() < editor.body.winfo_width()
                     and 0 <= y-editor.body.winfo_rooty() < editor.body.winfo_height()), None)

    def _is_empty_todo_column(self):
        return (len(self.lines) == 1 and self.lines[0]["type"] == "todo"
                and not self.lines[0]["text"] and not self.lines[0]["checked"])

    def _paint_columns_drag(self):
        drag = self._row_drag
        destination = self._drag_destination(drag["pointer"])
        line = self.lines[drag["source"]]
        order = []
        if destination is not None:
            order = ([] if destination is not self and destination._is_empty_todo_column() else
                     [i for i in range(len(destination.lines)) if destination is not self or i != drag["source"]])
            previous = min(len(order), drag["target"]) if destination is drag["destination"] else 0
            content_height = sum(drag["layouts"][destination][i][0] for i in order)
            order.insert(previous, None)
            y = int(destination.text.cget("pady")) - destination.body.canvasy(0)
            pointer_y = drag["pointer"][1] - destination.body.winfo_rooty()
            target = len(order)-1
            for slot, index in enumerate(order):
                height = (drag["floating_rows"][destination][0] if index is None else
                          drag["layouts"][destination][index][0])
                if y <= pointer_y < y+height:
                    boundary = slot + (pointer_y >= y+height/2)
                    target = previous if index is None else boundary - (previous < boundary)
                    break
                if pointer_y < y:
                    target = slot
                    break
                y += height
            if destination is not self and pointer_y >= int(destination.text.cget("pady")) - destination.body.canvasy(0) + content_height:
                target = len(order)-1
            drag["target"] = max(0, min(len(order)-1, target))
            order.remove(None)
            order.insert(drag["target"], None)
        drag["destination"] = destination
        for editor, canvas in drag["canvases"].items():
            canvas.delete("all")
            tk.Misc.lift(canvas)
            indices = order if editor is destination else list(range(len(editor.lines)))
            width, viewport = editor.body.winfo_width(), editor.body.winfo_height()
            y = int(editor.text.cget("pady")) - editor.body.canvasy(0)
            for index in indices:
                placeholder = index is None or (editor is self and index == drag["source"])
                row_line = line if index is None else editor.lines[index]
                layout = drag["floating_rows"][editor] if index is None else drag["layouts"][editor][index]
                height = layout[0]
                if y < viewport and y+height > 0:
                    if placeholder:
                        round_rect(canvas, 5, y+2, width-5, y+height-2, min(9, height/3),
                                   fill=mix(self._bg, "#B6C4D7", .22), outline="")
                    editor._draw_drag_row(canvas, index, y, muted=placeholder, line=row_line, layout=layout)
                y += height
        editor = destination or self
        width = editor.body.winfo_width()
        height, segments = drag["floating_rows"][editor]
        top = editor.body.winfo_rooty() - self.win.winfo_rooty()
        raw_y = drag["pointer"][1] - self.win.winfo_rooty() - drag["offset"] * height/drag["rows"][drag["source"]][0]
        y = max(top, raw_y)
        bottom = min(top+editor.body.winfo_height(), raw_y+height)
        canvas = drag["float"]
        canvas.delete("all")
        if bottom <= y:
            canvas.place_forget()
            return
        offset_x = (drag["press"][0]-self.body.winfo_rootx()) * width/self.body.winfo_width()
        canvas.place(x=drag["pointer"][0]-self.win.winfo_rootx()-offset_x,
                     y=y, width=width, height=bottom-y)
        tk.Misc.lift(canvas)
        y = raw_y-y
        round_rect(canvas, 6, y+5, width-4, y+height, min(10, height/3),
                   fill=mix(self._bg, "#677588", .18), outline="")
        round_rect(canvas, 3, y+1, width-7, y+height-4, min(10, height/3),
                   fill=self._bg, outline="#AAB7C8")
        editor._draw_drag_row(canvas, None, y, line=line, layout=(height, segments))

    def _draw_drag_row(self, cv, index, y, muted=False, line=None, layout=None):
        line = self.lines[index] if line is None else line
        height, segments = self._row_drag["rows"][index] if layout is None else layout
        x = int(self.text.cget("padx"))
        if line["type"] == "todo":
            cv.create_image(x+self._check_margin+self._check_margin//2, y+height/2,
                            image=self._check_images[line["checked"]], anchor="w")
            x += int(self.text.tag_cget("todo_indent", "lmargin2"))
        font = self.done_font if line["type"] == "todo" and line["checked"] else self.app.base_font
        color = mix(self._bg, "#2B2F36", .25) if muted else "#2B2F36"
        for n, segment in enumerate(segments):
            top = y+int(self.text.cget("spacing1"))+n*font.metrics("linespace")
            cv.create_text(x, top, text=segment, font=font, fill=color, anchor="nw")
            if line["type"] == "todo" and line["checked"] and segment:
                middle = top + font.metrics("ascent") * .67
                cv.create_line(x, middle, x+font.measure(segment), middle, fill=color, width=2)
        for dy in (-4, 0, 4):
            cv.create_line(cv.winfo_width()-48, y+height/2+dy,
                           cv.winfo_width()-38, y+height/2+dy, fill="#9198A2")

    def _row_drag_release(self, event):
        drag = self._row_drag
        if drag is None:
            return "break"
        if "canvases" in drag:
            drag["pointer"] = (event.x_root, event.y_root)
            self._paint_columns_drag()
            destination = drag["destination"]
            commit = destination is not None and (destination is not self or drag["source"] != drag["target"])
            self._cancel_row_drag(restore=not commit)
            if commit:
                self.win._columns_editing = True
                try:
                    source, target = drag["source"], drag["target"]
                    indices = list(range(len(self.lines)))
                    if destination is self:
                        indices.insert(target, indices.pop(source))
                    line = self.lines.pop(source)
                    if destination is not self and destination._is_empty_todo_column():
                        destination.lines = []
                    destination.lines.insert(target, line)
                    if not self.lines:
                        self.lines = [{"type": "todo", "text": "", "checked": False}]
                    for editor in self.win.editors:
                        editor.render()
                    if destination is self:
                        row, col = self._row_col(drag["cursor"])
                        destination.text.mark_set("insert", f"{indices.index(row)+1}.{col}")
                    else:
                        destination.text.mark_set("insert", f"{target+1}.{self._prefix_len(line)}")
                    destination._clamp_todo_cursor()
                finally:
                    self.win._columns_editing = False
                destination.text.focus_set()
                destination._see_text("insert")
                destination._remember_edit()
                self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
                self.app.schedule_save()
            return "break"
        inside = (0 <= event.x_root-self.body.winfo_rootx() < self.body.winfo_width() and
                  0 <= event.y_root-self.body.winfo_rooty() < self.body.winfo_height())
        commit = inside and drag["canvas"] is not None and drag["source"] != drag["target"]
        self._cancel_row_drag(restore=not commit)
        if commit:
            source, target = drag["source"], drag["target"]
            self.lines.insert(target, self.lines.pop(source))
            row, col = self._row_col(drag["cursor"])
            indices = list(range(len(self.lines)))
            indices.insert(target, indices.pop(source))
            self.render()
            self.text.mark_set("insert", f"{indices.index(row)+1}.{col}")
            self._clamp_todo_cursor()
            self._remember_edit()
            self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
            self.app.schedule_save()
        return "break"

    def _cancel_row_drag(self, _event=None, restore=True):
        drag, self._row_drag = self._row_drag, None
        if self._row_drag_job is not None:
            self.win.after_cancel(self._row_drag_job)
            self._row_drag_job = None
        if drag is not None:
            self._drag_handle.grab_release()
            if "canvases" in drag:
                for canvas in drag["canvases"].values():
                    canvas.destroy()
                drag["float"].destroy()
                drag["measure"].destroy()
                if restore:
                    for editor, scroll in drag["scrolls"].items():
                        editor._scroll_to(scroll)
            elif drag["canvas"] is not None:
                drag["canvas"].destroy()
            if restore:
                self._scroll_to(drag["scroll"])
            self.text.focus_set()
            self._drag_handle.place_forget()
        return "break" if drag is not None else None

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
                if col < self._prefix_len(self.lines[row]) and row + 1 in self._row_layout:
                    height, offset = self._row_layout[row + 1]
                    size = self._check_images[False].width()
                    box = (box[0], int(self.text.cget("pady")) + offset - self._chunk_start +
                           height // 2 - size // 2, box[2], size)
            top = self.body.canvasy(0)
            pad = max(int(self.text.cget("pady")), self._edge_fade.height)
            y = box[1] + self._chunk_start
            if y < top + pad:
                self._scroll_to(y - pad)
            elif y + box[3] > top + self.body.winfo_height() - pad:
                self._scroll_to(y + box[3] + pad - self.body.winfo_height())
            self._draw_strikes()

    def _stop_selection_scroll(self, _event=None):
        if self._selection_scroll_job is not None:
            self.win.after_cancel(self._selection_scroll_job)
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
        self._clamp_todo_cursor()
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
        self._clamp_todo_cursor()
        if self.body.yview() != before:
            self._selection_scroll_job = self.win.after(30, self._selection_scroll)
        return "break"

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

    def _queue_strikes(self, measure=True):
        if self.win._resizing:
            self.win._resize_dirty = True
        if self._layout_busy:
            self._layout_pending = True
            self._layout_measure_pending |= measure
            return
        if measure:
            self._document_height = 0
            self._row_layout.clear()
        if getattr(self, "_strike_job", None) is None:
            self._strike_job = self.win.after_idle(self._draw_strikes)

    def _draw_strikes(self):
        if self._layout_busy:
            return
        self._layout_busy = True
        try:
            self._layout_text()
            if not self.win._resizing:
                self.body.update_idletasks()
        finally:
            self._layout_busy = False
        if self._layout_pending:
            self._layout_pending = False
            measure, self._layout_measure_pending = self._layout_measure_pending, False
            self._queue_strikes(measure=measure)

    def _layout_text(self):
        self.text.tag_add("drag_margin", "1.0", "end")
        if getattr(self, "_strike_job", None):
            self.win.after_cancel(self._strike_job)
        self._strike_job = None
        width = self.body.winfo_width()
        if width < 50:
            return
        # 测量布局会刷新画面，先隐藏文字已删掉的旧横线，避免空行残留一帧。
        for bar in self._strike_widgets:
            if self.text.compare(bar._text_start, ">=", f"{bar._text_start} lineend"):
                bar.place_forget()
                bar._layout_bounds = None
        # 正文和所有装饰始终留在同一内容坐标系，滚动只移动整个 Text 窗口。
        if self.text.winfo_width() != width:
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
        region = (0, 0, width, self._content_height)
        geometry_changed = self.text.winfo_height() != height
        region_changed = getattr(self, "_body_region", None) != region
        if geometry_changed:
            self.text.place_configure(height=height)
        if region_changed:
            self.body.configure(scrollregion=region)
            self._body_region = region
        # 顶部未滚动且 Text 高度未变时，横向 scrollregion 更新不影响行坐标。
        if geometry_changed or (region_changed and self.body.canvasy(0) != 0):
            self.body.update_idletasks()
        top = round(self.body.canvasy(0))
        target = max(0, top - (height - self.body.winfo_height()) // 2)
        view_changed = round(self.text.yview()[0] * self._document_height) != target
        if view_changed:
            self.text.yview_moveto(target / self._document_height)
        self._chunk_start = round(self.text.yview()[0] * self._document_height)
        text_y = self._chunk_start - top
        if self.text.winfo_y() != text_y:
            self.text.place_configure(y=text_y)
            view_changed = True
        if view_changed:
            self.body.update_idletasks()
        used_masks = used_boxes = used_bars = 0
        content_height = self.text.winfo_height()
        visible_start = self.text.index("@0,0")
        first_row = int(visible_start.split(".")[0])
        last_row = min(len(self.lines), int(self.text.index(f"@0,{content_height}").split(".")[0]))
        for row in range(first_row, last_row + 1):
            line = self.lines[row - 1]
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
                            mask.bind("<Control-MouseWheel>", self.win._ctrl_wheel)
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
                        box.bind("<Motion>", self._hover_row)
                        box.bind("<Leave>", lambda e: self.win.after_idle(self._hide_drag_handle))
                        box.bind("<MouseWheel>", self._text_wheel)
                        box.bind("<Control-MouseWheel>", self.win._ctrl_wheel)
                        self._checkbox_widgets.append(box)
                    used_boxes += 1
                    bounds = (int(self.text.cget("padx")) + self._check_margin + self._check_margin // 2,
                              y, size, size)
                    if getattr(box, "_layout_bounds", None) != bounds:
                        box.place(x=bounds[0], y=y, width=size, height=size, bordermode="ignore")
                        box._layout_bounds = bounds
                        box.lift()
                    if getattr(box, "_todo_row", None) != row:
                        box._todo_row = row
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
                        bar.bind("<Motion>", self._hover_row)
                        bar.bind("<MouseWheel>", self._text_wheel)
                        bar.bind("<Control-MouseWheel>", self.win._ctrl_wheel)
                        for event in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>"):
                            bar.bind(event, lambda e, sequence=event: self.text.event_generate(
                                sequence, x=e.x_root - self.text.winfo_rootx(),
                                y=e.y_root - self.text.winfo_rooty(), time=e.time, state=e.state))
                        self._strike_widgets.append(bar)
                    used_bars += 1
                    bar._text_start = start
                    baseline = display[1] + display[4]
                    middle = baseline - round(self.done_font.metrics("ascent") * 0.33)
                    bounds = (first[0], middle - thickness // 2,
                              max(1, last[0] + last[2] - first[0]), thickness)
                    if getattr(bar, "_layout_bounds", None) != bounds:
                        bar.place(x=bounds[0], y=bounds[1], width=bounds[2],
                                  height=thickness, bordermode="ignore")
                        bar._layout_bounds = bounds
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
            self.win.after_idle(lambda: [w.lift() for w in self._selection_masks + self._checkbox_widgets
                                         if w.winfo_exists()])
        self._clamp_todo_cursor()

    def _on_vertical(self, event):
        if self.win._ime_composing(self.text):
            return
        self._stop_wheel()
        t = self.text
        current = t.index("insert")
        self._see_text(current)
        box = t.bbox(current)
        if not box:
            return "break"
        x = box[0]
        direction = 1 if event.keysym == "Down" else -1
        start = t.index("insert display linestart")
        target = t.index(f"{start} {direction:+d} displaylines display linestart")
        if t.compare(target, "!=", start):
            self._see_text(target)
            display = t.dlineinfo(target)
            end = t.index(f"{target} display lineend")
            target = t.tk.call("tk::TextClosestGap", t, x, display[1] + display[3] // 2)
            if t.compare(target, ">", end):
                target = end
            command = "tk::TextKeySelect" if event.state & 1 else "tk::TextSetCursor"
            t.tk.call(command, t, target)
            self._clamp_todo_cursor()
            self._see_text("insert")
        return "break"

    def _on_left(self, event):
        if self.win._ime_composing(self.text):
            return
        row, col = self._row_col(self.text.index("insert"))
        pre = self._prefix_len(self.lines[row])
        if pre and col <= pre:
            target = f"{row}.end" if row else f"1.{pre}"
            command = "tk::TextKeySelect" if event.state & 1 else "tk::TextSetCursor"
            self.text.tk.call(command, self.text, target)
            self._clamp_todo_cursor()
            self._see_text("insert")
            return "break"

    def _clamp_todo_cursor(self, _event=None):
        if self.win._ime_composing(self.text):
            return
        t = self.text
        start = t.index("insert linestart")
        images = t.dump(start, f"{start}+1c", image=True)
        if images and t.image_cget(start, "image") in {str(i) for i in self._check_slots.values()}:
            target = f"{start}+{1 + len(PREFIX_PAD)}c"
            if t.compare("insert", "<", target):
                t.mark_set("insert", target)

    # ---- 底部添加条 ----
    def add_todo(self):
        row = int(self.text.index("insert").split(".")[0])
        self.lines.insert(row, dict(self.lines[row - 1], type="todo", text="", checked=False))
        self.render(keep_cursor=False)
        self.text.mark_set("insert", f"{row+1}.0+{self._prefix_len(self.lines[row])}c")
        self._see_text(f"{row+1}.0")
        self.text.focus_set()
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    # ---- 渲染 ----
    def render(self, keep_cursor=True):
        self._stop_wheel()
        cur = self.text.index("insert")
        view = self._body_index()
        scroll_y = self.body.canvasy(0)
        old_chunk = self._chunk_start
        top = self.text.dlineinfo(view)
        self.text.delete("1.0", "end")
        self.text.tag_remove("merged_right", "1.0", "end")
        lines = self.lines or [{"type": "text", "text": "", "checked": False}]
        self.lines = lines
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
        for row, ln in enumerate(lines, 1):
            if ln.get("mergedColumn") == 1:
                self.text.tag_add("merged_right", f"{row}.0", f"{row+1}.0")
        self.text.tag_add("drag_margin", "1.0", "end")
        if keep_cursor:
            end = self.text.index("end-1c")
            if self.text.compare(cur, ">", end):
                cur = end
            self.text.mark_set("insert", cur)
        self._clamp_todo_cursor()
        self.win.update_status()
        self._queue_strikes()
        self.body.update_idletasks()
        restored = self.text.dlineinfo(view)
        if top and restored:
            self._scroll_to(scroll_y + restored[1] + self._chunk_start - top[1] - old_chunk)
        self._remember_edit()

    @staticmethod
    def _prefix_len(ln):
        return 1 + len(PREFIX_PAD) if ln["type"] == "todo" else 0

    @staticmethod
    def _row_col(index):
        r, c = index.split(".")
        return int(r) - 1, int(c)

    # ---- 编辑事件 ----
    def _remember_edit(self):
        state = (deepcopy(self.lines), self.text.index("insert"))
        if not hasattr(self, "_edit_history"):
            self._edit_history, self._edit_redo = [state], []
        elif self._edit_history[-1][0] != state[0]:
            self._edit_history.append(state)
            self._edit_redo.clear()
        else:
            self._edit_history[-1] = state
        if len(self.win.__dict__.get("editors", ())) == 2:
            self.win._remember_columns_edit(self.col)

    def _undo_edit(self, redo=False):
        if self.win._ime_composing(self.text):
            return "break"
        if self.d.get("twoColumn"):
            return self.win._undo_columns_edit(self.col, redo)
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
        self.lines = deepcopy(state[0])
        self.render(keep_cursor=False)
        self.text.mark_set("insert", state[1])
        self._clamp_todo_cursor()
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()
        return "break"

    def _clipboard_edit(self, command):
        if self.win._ime_composing(self.text):
            return "break"
        self._sync_lines_from_text()
        self._clamp_todo_cursor()
        t = self.text
        if command in ("tk_textCopy", "tk_textCut"):
            if not t.tag_ranges("sel"):
                return "break"
            first, last = t.index("sel.first"), t.index("sel.last")
            chunks = []
            copied_lines = []
            for row in range(int(first.split(".")[0]), int(last.split(".")[0]) + 1):
                start = first if t.compare(first, ">", f"{row}.0") else f"{row}.0"
                end = last if t.compare(last, "<", f"{row}.end") else f"{row}.end"
                line = self.lines[row - 1]
                body = f"{row}.{self._prefix_len(line)}"
                whole_todo = (line["type"] == "todo" and
                              t.compare(start, "<=", body) and
                              t.compare(end, "==", f"{row}.end") and
                              t.compare(last, ">", f"{row}.0"))
                if line["type"] == "todo" and t.compare(start, "<", body):
                    start = body
                content = t.get(start, end) if t.compare(start, "<=", end) else ""
                chunks.append(content)
                copied_lines.append({"type": "todo" if whole_todo else "text",
                                     "text": content, "checked": whole_todo and line["checked"]})
            t.clipboard_clear()
            content = "\n".join(chunks)
            t.clipboard_append(content)
            # 结构只留在应用内；序号防止外部软件重新复制相同文字时误用旧结构。
            self.app._clipboard_lines = (user32.GetClipboardSequenceNumber(), content, copied_lines)
            if command == "tk_textCopy":
                return "break"
            t.delete(first, last)
            self._on_keyrelease(None)
            return "break"
        if command == "tk_textPaste":
            try:
                content = t.clipboard_get()
            except tk.TclError:
                return "break"
            copied = getattr(self.app, "_clipboard_lines", None)
            if (copied and copied[:2] == (user32.GetClipboardSequenceNumber(), content) and
                    any(line["type"] == "todo" for line in copied[2])):
                self._paste_lines(deepcopy(copied[2]))
                self._on_keyrelease(None)
                return "break"
        if command == "tk_textPaste" and self.text.tag_ranges("sel"):
            self.text.mark_set("insert", "sel.first")
        if command == "tk_textPaste":
            row, _ = self._row_col(t.index("insert"))
            paste_right = self.lines[row].get("mergedColumn") == 1
            t.mark_set("column_paste_start", "insert")
            t.mark_gravity("column_paste_start", "left")
        self.text.tk.call(command, self.text)
        if command == "tk_textPaste":
            if paste_right:
                t.tag_add("merged_right", "column_paste_start", "insert")
            t.mark_unset("column_paste_start")
        self._on_keyrelease(None)
        return "break"

    def _paste_lines(self, incoming):
        t = self.text
        selected = t.tag_ranges("sel")
        first, last = map(str, selected) if selected else (t.index("insert"), t.index("insert"))
        start_row, _ = self._row_col(first)
        end_row, _ = self._row_col(last)
        lines = self.lines
        if lines[start_row].get("mergedColumn") == 1:
            for line in incoming:
                line["mergedColumn"] = 1
        body = f"{start_row + 1}.{self._prefix_len(lines[start_row])}"
        left = t.get(body, first) if t.compare(first, ">", body) else ""
        body = f"{end_row + 1}.{self._prefix_len(lines[end_row])}"
        right = t.get(last if t.compare(last, ">", body) else body, f"{end_row + 1}.end")
        if incoming[0]["type"] == "text" and left:
            incoming[0] = dict(lines[start_row], text=left + incoming[0]["text"])
        elif incoming[0]["type"] == "todo" and left:
            incoming.insert(0, dict(lines[start_row], text=left))
        if (len(incoming) > 1 and incoming[-1]["type"] == "text" and
                not incoming[-1]["text"] and last == f"{end_row + 1}.0"):
            incoming[-1] = dict(lines[end_row], text="")
        # 用 Tk 的字符计数定位，兼容 emoji 等占用多个 Tcl 字符的文字。
        cursor_row = start_row + len(incoming)
        cursor_col = self._prefix_len(incoming[-1]) + int(t.tk.call("string", "length", incoming[-1]["text"]))
        incoming[-1]["text"] += right
        lines[start_row:end_row + 1] = incoming
        self.render(keep_cursor=False)
        t.mark_set("insert", f"{cursor_row}.{cursor_col}")

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
            if row <= len(self.lines) and self.lines[row-1]["type"] == "todo":
                text_start = f"{row}.{self._prefix_len(self.lines[row-1])}"
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
        old = self.lines
        lines = []
        for row in range(1, int(t.index("end-1c").split(".")[0]) + 1):
            image = images.get(f"{row}.0")
            todo = image in checkbox_images
            content = t.get(f"{row}.0", f"{row}.end")
            if todo and content.startswith(PREFIX_PAD):
                content = content[len(PREFIX_PAD):]
            if (todo and not content and image == str(self._check_slots[True]) and
                    row <= len(old) and old[row - 1]["type"] == "todo" and old[row - 1]["text"]):
                image = str(self._check_slots[False])
                t.image_configure(f"{row}.0", image=self._check_slots[False])
                t.tag_remove("done", f"{row}.0", f"{row}.end")
            lines.append({"type": "todo" if todo else "text", "text": content,
                          "checked": todo and image == str(self._check_slots[True])})
            if ("merged_right" in t.tag_names(f"{row}.0") or
                    t.tag_nextrange("merged_right", f"{row}.0", f"{row+1}.0")):
                lines[-1]["mergedColumn"] = 1
        self.lines = lines
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
            self.win.update_status()
        self._remember_edit()

    def _on_keyrelease(self, _e):
        if self.win._ime_composing(self.text):
            return
        self._queue_strikes()
        self._sync_lines_from_text()
        if _e is None or _e.keysym not in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"):
            self._see_text("insert")
        self.app.schedule_save()

    def _on_enter(self, _e):
        if self.win._ime_composing(self.text):
            return
        if self.text.tag_ranges("sel"):
            self.text.delete("sel.first", "sel.last")
        self._sync_lines_from_text()
        row, col = self._row_col(self.text.index("insert"))
        lines = self.lines
        ln = lines[row]
        pre = self._prefix_len(ln)
        rel = max(0, col - pre)
        if ln["type"] == "todo" and ln["text"] and rel == 0:
            lines.insert(row, dict(ln, text="", checked=False))
        else:
            left, right = ln["text"][:rel], ln["text"][rel:]
            ln["text"] = left
            lines.insert(row + 1, dict(ln, text=right, checked=False))
        self.render(keep_cursor=False)
        nrow = row + 2
        self.text.mark_set("insert", f"{nrow}.0+{self._prefix_len(lines[row+1])}c")
        self._see_text(f"{nrow}.0")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()
        return "break"

    def _hide_strikes_before_delete(self, event):
        if self.win._ime_composing(self.text):
            return
        t = self.text
        selected = t.tag_ranges("sel")
        for bar in self._strike_widgets:
            row = bar._text_start.split(".")[0]
            start, end = f"{row}.{1 + len(PREFIX_PAD)}", f"{row}.end"
            if selected:
                removes_text = (t.compare(selected[0], "<=", start) and
                                t.compare(selected[1], ">=", end))
            else:
                position = end if event.keysym == "BackSpace" else start
                removes_text = len(t.get(start, end)) == 1 and t.compare("insert", "==", position)
            if removes_text:
                bar.place_forget()
                bar._layout_bounds = None

    def _on_backspace(self, _e):
        if self.win._ime_composing(self.text):
            return
        if self.text.tag_ranges("sel"):
            return None  # 交给 Text 默认绑定删除选区，随后统一同步所有行。
        row, col = self._row_col(self.text.index("insert"))
        lines = self.lines
        ln = lines[row]
        if col <= self._prefix_len(ln) and row > 0:
            prev = lines[row - 1]
            target = self.text.index(f"{row}.end")
            self._remember_edit()
            if prev["type"] == ln["type"] == "todo" and not prev["text"] and ln["text"]:
                lines[row - 1] = ln
            else:
                prev["text"] += ln["text"]
            del lines[row]
            self.render(keep_cursor=False)
            self.text.mark_set("insert", target)
            self._see_text("insert")
            self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
            self.app.schedule_save()
            return "break"
        if ln["type"] == "todo" and col <= self._prefix_len(ln):
            if row == 0 and not ln["text"] and len(lines) > 1:
                self._remember_edit()
                self.delete_line(row)
            return "break"
        return None

    def _on_click(self, e):
        if self.text.winfo_width()-55 <= e.x < self.text.winfo_width()-31:
            # 快速移入并按下时，Motion 可能尚未显示手柄。
            self._hover_row(e)
            if self._drag_handle.winfo_manager():
                return self._row_drag_press(e)
        self.win.focus_set(); self.text.focus_set()

    def _local_shortcut(self, event):
        modifiers = sum(value for mask, value in ((4, 2), (0x20000, 1), (1, 4)) if event.state & mask)
        if (modifiers, event.keycode) == parse_hotkey(self.app.hotkey_text("convert")):
            if event.type == tk.EventType.KeyRelease:
                return "break"  # 转换已保存，松键不再走普通输入的同步和保存流程。
            return self.toggle_line_type()

    def _conversion_rows(self):
        selected = self.text.tag_ranges("sel")
        if not selected:
            return [self._row_col(self.text.index("insert"))[0]]
        first, _ = self._row_col(str(selected[0]))
        last, col = self._row_col(str(selected[1]))
        if col == 0 and last > first:
            last -= 1
        return list(range(first, last + 1))

    def _conversion_target(self, rows):
        todos = sum(self.lines[row]["type"] == "todo" for row in rows)
        return "text" if todos * 2 > len(rows) else "todo"

    def toggle_line_type(self):
        if self.win._ime_composing(self.text):
            return "break"
        self._sync_lines_from_text()
        rows = self._conversion_rows()
        self.convert_rows(rows, self._conversion_target(rows))
        return "break"

    def _on_rightclick(self, e):
        if self.win._ime_composing(self.text):
            return "break"
        self.win.focus_set(); self.text.focus_set()
        self._sync_lines_from_text()
        try:
            row, _ = self._row_col(self.text.index(f"@{e.x},{e.y}"))
        except Exception:
            return
        lines = self.lines
        if row >= len(lines):
            return
        if not self.text.tag_ranges("sel") or row not in self._conversion_rows():
            self.text.tag_remove("sel", "1.0", "end")
            self.text.mark_set("insert", self.text.index(f"@{e.x},{e.y}"))
            self._clamp_todo_cursor()
        rows = self._conversion_rows()
        target = self._conversion_target(rows)
        label = "转为文字" if target == "text" else "转为待办"
        if len(rows) > 1:
            label = f"全部{label}（{len(rows)} 行）"
        actions = [
            (label, lambda: self.convert_rows(rows, target)),
            ("在下方插入新行", lambda: self.insert_line(row)),
            ("删除此行", lambda: self.delete_line(row)),
        ]
        self.win._show_context_menu(e.x_root, e.y_root, actions, shortcuts={0: self.app.hotkey_text("convert")})

    def toggle_done(self, row):
        ln = self.lines[row]
        ln["checked"] = not ln["checked"]
        self.render()
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def convert(self, row, to):
        self.convert_rows([row], to)

    def convert_rows(self, rows, to):
        if self.win._ime_composing(self.text):
            return
        t = self.text
        positions = [t.index("insert"), *map(str, t.tag_ranges("sel"))]
        positions = [index if t.compare(index, "<=", "end-1c") else t.index("end-1c")
                     for index in positions]
        anchors = []
        for index in positions:
            row, col = self._row_col(index)
            body = f"{row+1}.{self._prefix_len(self.lines[row])}"
            count = t.count(body, index, "chars") if t.compare(index, ">", body) else None
            anchors.append((row, count[0] if count else 0, col == 0))
        for row in rows:
            ln = self.lines[row]
            if to == "text" or ln["type"] != "todo":
                ln["checked"] = False
            ln["type"] = to
        self.render(keep_cursor=False)
        restored = [t.index(f"{row+1}.0" if at_start else
                            f"{row+1}.{self._prefix_len(self.lines[row]) + offset}")
                    for row, offset, at_start in anchors]
        t.mark_set("insert", restored[0])
        if len(restored) == 3:
            t.tag_add("sel", restored[1], restored[2])
        self._clamp_todo_cursor()
        self._remember_edit()
        t.focus_set()
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def insert_line(self, row):
        self.lines.insert(row + 1, dict(self.lines[row], type="text", text="", checked=False))
        self.render(keep_cursor=False)
        self.text.mark_set("insert", f"{row+2}.0")
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def delete_line(self, row):
        if len(self.lines) <= 1:
            self.lines = [{"type": "text", "text": "", "checked": False}]
        else:
            del self.lines[row]
        self.render(keep_cursor=False)
        row = min(row, len(self.lines) - 1)
        self.text.mark_set("insert", f"{row+1}.{self._prefix_len(self.lines[row])}")
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()


class NoteWindow(tk.Toplevel):
    def __init__(self, app, data):
        super().__init__(app.root)
        self.app, self.d = app, data
        self._temporarily_raised = False
        self._bg, self._border = NOTE_BG, NOTE_BORDER
        self.d["color"] = "白"
        self.configure(bg=self._bg)
        self.geometry(f"{data['w']}x{data['h']}+{data['x']}+{data['y']}")
        self.attributes("-alpha", data["opacity"])
        self.overrideredirect(True)
        self.minsize(280, 230)

        # 画布画圆角底板，控件都放圆角内部
        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self._border_item = self.cv.create_rectangle(0, 0, 1, 1, fill=self._bg, outline=self._border)

        # -- 工具栏 --
        self.toolbar = tk.Frame(self, bg=self._bg)
        self._tool_btns = []
        self._chip_font = tkfont.Font(family=app.note_font_family(), size=10, weight="bold")
        self._title_edit_font = self._chip_font

        def btn(icon, get_tip, cmd):
            b = ToolButton(self.toolbar, icon, get_tip, cmd, self._bg)
            self._tool_btns.append(b)
            return b

        self.b_pin = ToolButton(self.toolbar, "pin",
                                lambda: "取消置顶" if self.d["z"] == "top" else "置顶",
                                self.toggle_pin, self._bg)
        self._tool_btns.append(self.b_pin)
        self.b_pin.pack(side="left", padx=(3, 2), pady=9)
        self.b_bottom = btn("arrow-down-to-line",
                            lambda: "取消置底" if self.d["z"] == "bottom" else "置底",
                            self.toggle_bottom)
        self.b_bottom.pack(side="left", padx=(2, 8), pady=9)

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
        self.b_columns = btn("columns-2",
                             lambda: "切回单栏" if self.d.get("twoColumn") else "切换双栏",
                             self.toggle_columns)
        self.b_columns.pack(side="right", padx=5, pady=9)

        # -- 底部状态栏 + 添加条 --
        self.bar = tk.Frame(self, bg=self._bg)
        self.status = tk.Label(self.bar, text="", bg=self._bg, fg=QUIET,
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
        self.add_pill = tk.Label(self.bar, bg=self._bg, bd=0, padx=2, pady=2,
                                 highlightthickness=0, cursor="hand2")
        self.add_pill.bind("<Button-1>", self._pill_press)
        Tooltip(self.add_pill, lambda: "添加待办")
        self._resizing = self._resize_busy = self._resize_ending = False
        self._resize_ready = self._resize_cancel_requested = self._resize_release_pending = False
        self._resize_target = self._resize_start = self._resize_surface = None
        self._resize_next_frame = 0
        self._resize_dirty = False
        self._resize_key_widgets = []
        self._resize_pointer = self._resize_release_point = None
        self._resize_up_since = None
        self._edge_sizing = self._dragging = False
        self._resize_job = None
        self._relayout_job = None
        self.divider = None
        self._columns_editing = False
        self._columns_history, self._columns_redo = [], []
        # 编辑器在状态栏之后创建：render 会立即更新底部状态文字。
        self.editors = [NoteEditor(self, 0)]
        if self.d.get("twoColumn"):
            self.d.setdefault("lines2", [{"type": "todo", "text": "", "checked": False}])
            self.editors.append(NoteEditor(self, 1))
            self._remember_columns_edit(0)
        self._draw_chip()

        self.bind("<Configure>", lambda e: self._queue_relayout() if e.widget == self else None)
        for surface in (self.cv, self.bar):
            surface.bind("<Motion>", self._edge_cursor)
            surface.bind("<Button-1>", self._edge_press)
            surface.bind("<B1-Motion>", self._edge_move)
            surface.bind("<ButtonRelease-1>", self._edge_release)
        # 类级绑定按事件来源的顶层窗口分发，多编辑器也能路由到正确的一栏。
        for sequence in ("<KeyPress>", "<KeyRelease>"):
            if not self.bind_class("NoteShortcuts", sequence):
                self.bind_class("NoteShortcuts", sequence,
                                lambda e: e.widget.winfo_toplevel()._local_shortcut(e))
        # 在 Text 默认移动完成后立即校正，避免连续按键在前缀中插入文字。
        for sequence in ("<KeyPress>", "<KeyRelease>", "<Button-1>", "<B1-Motion>",
                         "<ButtonRelease-1>", "<FocusIn>"):
            if not self.bind_class("TodoCaret", sequence):
                self.bind_class("TodoCaret", sequence,
                                lambda e: e.widget.winfo_toplevel()._clamp_todo_cursor(e))
        # Text 类绑定完成删除后立即同步，长按删除也不必等 KeyRelease 才刷新。
        for sequence in ("<KeyPress-BackSpace>", "<KeyPress-Delete>"):
            if not self.bind_class("TodoCaret", sequence):
                self.bind_class("TodoCaret", sequence,
                                lambda e: e.widget.winfo_toplevel()._on_keyrelease(e))

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
        self._install_z_order()
        self.apply_z()
        self._configure_frameless()
        self._restyle()
        if not data.get("visible", True):
            self.withdraw()

    def _queue_relayout(self):
        if self._relayout_job is None:
            self._relayout_job = self.after_idle(self._run_relayout)

    def _run_relayout(self):
        self._relayout_job = None
        self._relayout()

    # ---- 多编辑器转发：单栏行为不变，测试与旧调用方继续走窗口属性 ----
    def __getattr__(self, name):
        if name == "editors":
            raise AttributeError(name)
        return getattr(self.editors[0], name)

    @property
    def _layout_busy(self):
        return self.editors[0]._layout_busy

    @_layout_busy.setter
    def _layout_busy(self, value):
        self.editors[0]._layout_busy = value

    @property
    def _wheel_remaining(self):
        return self.editors[0]._wheel_remaining

    @_wheel_remaining.setter
    def _wheel_remaining(self, value):
        self.editors[0]._wheel_remaining = value

    @property
    def _wheel_time(self):
        return getattr(self.editors[0], "_wheel_time", 0)

    @_wheel_time.setter
    def _wheel_time(self, value):
        self.editors[0]._wheel_time = value

    def _editor_for(self, widget):
        for editor in self.editors:
            if widget in (editor.text, editor.body, editor.scroll_thumb):
                return editor
        return self.editors[0]

    @property
    def _editor_texts(self):
        return [editor.text for editor in self.editors]

    @property
    def focused(self):
        current = self.focus_get()
        for editor in self.editors:
            if current is editor.text:
                return editor
        return self.editors[0]

    def _local_shortcut(self, event):
        return self._editor_for(event.widget)._local_shortcut(event)

    def _clamp_todo_cursor(self, _event=None):
        editor = (self._editor_for(_event.widget) if _event is not None and
                  hasattr(_event, "widget") else self.focused)
        editor._clamp_todo_cursor()

    def _on_keyrelease(self, event=None):
        editor = (self._editor_for(event.widget) if event is not None and
                  hasattr(event, "widget") else self.focused)
        editor._on_keyrelease(None)

    def render(self, keep_cursor=True):
        for editor in self.editors:
            editor.render(keep_cursor)

    def _sync_lines_from_text(self):
        for editor in self.editors:
            editor._sync_lines_from_text()

    def _relayout(self):
        w, h = self._layout_dimensions()
        if w < 50 or h < 50 or (w, h) == getattr(self, "_last_sz", None):
            return  # 尺寸没变（纯移动触发的 Configure）不重画
        previous = getattr(self, "_last_sz", None)
        self._last_sz = (w, h)
        if previous is None or previous[0] != w:
            self._draw_chip()
        self.cv.coords(self._border_item, 0, 0, w - 1, h - 1)
        ix = RADIUS + 4
        toolbar_h = max(48, self.title_chip.winfo_reqheight())
        text_y = toolbar_h + 12
        if getattr(self, "_toolbar_size", None) != (w, toolbar_h):
            self.toolbar.place(x=ix, y=6, width=w - 2 * ix, height=toolbar_h)
            self._toolbar_size = (w, toolbar_h)
        self._layout_footer(text_y)

    def _layout_footer(self, text_y=None):
        w, h = self._layout_dimensions()
        if w < 50 or h < 50:
            return
        edge = max(12, min(24, round(w * .06)))
        available = w - 2 * edge
        label_font = self.clear_btn.cget("font")
        if getattr(self, "_footer_label_widths", (None,))[0] != label_font:
            self._footer_label_widths = (label_font,
                int(self.tk.call("font", "measure", label_font, "清除已完成")),
                int(self.tk.call("font", "measure", label_font, "清除完成")))
        _, long_width, short_width = self._footer_label_widths
        clear_text = "清除已完成"
        label_width = max(self.status.winfo_reqwidth(), long_width)
        if available - 2 * label_width - 20 < 64:
            clear_text = "清除完成"
            label_width = max(self.status.winfo_reqwidth(), short_width)
        if self.clear_btn.cget("text") != clear_text:
            self.clear_btn.config(text=clear_text)
        gap = min(10, max(6, (available - 2 * label_width - 36) // 2))
        pill_width = min(112, max(36, available - 2 * label_width - 2 * gap))
        pill_height = min(max(36, self.title_chip.winfo_reqheight()), pill_width - 4)
        pill_x = (w - pill_width) // 2
        # 底栏包含按钮上下留白，不能把子窗口的裁剪边界贴在按钮边框上。
        bar_y = h - 10 - pill_height - 8
        if text_y is not None:
            ix = RADIUS + 4
            avail = w - 2 * ix
            height = max(20, bar_y - text_y)
            if len(self.editors) > 1:
                gap = 14
                left_w = (avail - gap) // 2
                for i, editor in enumerate(self.editors):
                    col_w = left_w if i == 0 else max(1, avail - gap - left_w)
                    x = ix if i == 0 else ix + left_w + gap
                    editor.body.place(x=x, y=text_y, width=col_w, height=height)
                    editor.scroll_thumb.place(x=x + col_w - 10, y=text_y + 4,
                                              width=8, height=max(12, height - 8))
                    tk.Misc.lift(editor.scroll_thumb)
                self._layout_divider(ix + left_w, text_y, height)
            else:
                self.editors[0].body.place(x=ix, y=text_y, width=avail, height=height)
                self.editors[0].scroll_thumb.place(x=w - RADIUS - 10, y=text_y + 4,
                                                   width=8, height=max(12, height - 8))
                tk.Misc.lift(self.editors[0].scroll_thumb)
        self.bar.place(x=0, y=bar_y, width=w, height=h - 1 - bar_y)
        center_y = 8 + pill_height / 2
        # 底栏内统一使用局部坐标，移动容器即可带动全部控件。
        content_size = (w, pill_width, pill_height, self.status.winfo_reqwidth(), label_font)
        if getattr(self, "_footer_content_size", None) != content_size:
            self.status.place(x=pill_x / 2, y=center_y, anchor="center", height=26)
            self.clear_btn.place(x=(pill_x + pill_width + w) / 2, y=center_y, anchor="center")
            # 原图位置不变，四周留两像素背景，保护原生移动时的抗锯齿边缘。
            self.add_pill.place(x=pill_x-2, y=6, width=pill_width+4, height=pill_height+4)
            self._footer_content_size = content_size
        if getattr(self, "_pill_render", (None, None))[:2] != (pill_width, pill_height):
            self._draw_pill(pill_width, pill_height)

    def destroy(self):
        self._temporarily_raised = False
        for editor in getattr(self, "editors", ()):
            editor.teardown()
        self._finish_resize(cancel=True, redraw=False, hidden=True)
        if hasattr(self, "_resize_key_tag"):
            self.unbind_class(self._resize_key_tag, "<Escape>")
        if self._relayout_job is not None:
            self.after_cancel(self._relayout_job)
            self._relayout_job = None
        if hasattr(self, "_ime_pump_job"):
            self.after_cancel(self._ime_pump_job)
            user32.SetWindowLongPtrW(self.winfo_id(), -4, self._ime_old_proc)
        if hasattr(self, "_z_old_proc"):
            user32.SetWindowLongPtrW(self.hwnd, -4, self._z_old_proc)
        super().destroy()
        user32.DestroyWindow(self._owner_hwnd)

    def _round_window(self):
        # 由 DWM 抗锯齿裁切外框，避免透明色键的硬像素边缘。
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(self.hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))

    def _restyle(self):
        for b in self._tool_btns:
            b.set_bg(self._bg)
        self.b_pin.set_active(self.d["z"] == "top")
        self.b_bottom.set_active(self.d["z"] == "bottom")
        if hasattr(self, "b_columns"):
            self.b_columns.set_active(bool(self.d.get("twoColumn")))

    def toggle_pin(self):
        self.set_z("normal" if self.d["z"] == "top" else "top")

    def toggle_bottom(self):
        self.set_z("normal" if self.d["z"] == "bottom" else "bottom")

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
        hwnd = self.winfo_id()  # Tk 将原生输入焦点保留在顶层客户窗口。
        self._resize_client_hwnd = hwnd

        @_WNDPROC
        def window_proc(handle, message, wparam, lparam):
            # 原生消息可能嵌套在 Tcl 调用中；这里只处理原生状态，不能调用 Tk。
            if message == 0x0200 and self._resizing:  # coalesce before Tk creates a second motion queue
                if wparam & 0x0001 and self._resize_release_point is None:
                    point = _POINT(ctypes.c_short(lparam & 0xFFFF).value,
                                   ctypes.c_short((lparam >> 16) & 0xFFFF).value)
                    user32.ClientToScreen(handle, ctypes.byref(point))
                    self._resize_pointer = (point.x, point.y)
                pending = wintypes.MSG()
                while user32.PeekMessageW(ctypes.byref(pending), None, 0x0200, 0x0200, 1):
                    if pending.wParam & 0x0001 and self._resize_release_point is None:
                        self._resize_pointer = (pending.pt.x, pending.pt.y)
                return 0
            if message == 0x0202 and self._resizing:  # latch the release coordinates, not a later cursor position
                point = _POINT(ctypes.c_short(lparam & 0xFFFF).value,
                               ctypes.c_short((lparam >> 16) & 0xFFFF).value)
                user32.ClientToScreen(handle, ctypes.byref(point))
                self._resize_release_point = (point.x, point.y)
                self._resize_release_pending = True
            if message in (0x0100, 0x0104, 0x0290) and wparam == 0x1B and self._resizing:
                self._resize_cancel_requested = True
                return 0
            if (message == 0x001F and self._resizing and self._resize_ready and
                    user32.GetAsyncKeyState(0x01) < 0):
                self._resize_cancel_requested = True
            if (message == 0x0215 and self._resizing and self._resize_ready and lparam and
                    lparam != self.hwnd and not user32.IsChild(self.hwnd, lparam) and
                    user32.GetAsyncKeyState(0x01) < 0):
                self._resize_cancel_requested = True
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
        editor = (self._editor_for(widget) if not isinstance(widget, tk.Entry)
                  and widget in self._editor_texts else None)
        if editor is None and not isinstance(widget, tk.Entry):
            return False
        if self._ime_active:
            return True
        self._ime_widget = widget
        self._ime_selected = ""
        self._ime_selected_images = []
        if editor is not None:
            editor._clamp_todo_cursor()
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
        editor = self._editor_for(widget) if not isinstance(widget, tk.Entry) else None
        if editor is not None:
            widget.delete(start, f"{start}+{self._ime_length}c")
            tags = ["ime_preedit"]
            if "todo_indent" in widget.tag_names(f"{start} linestart"):
                tags.append("todo_indent")
            widget.insert(start, value, tags)
            widget.mark_set("insert", f"{start}+{cursor}c")
            editor._queue_strikes()
            editor._see_text("insert")
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
        editor = self._editor_for(widget) if not isinstance(widget, tk.Entry) else None
        self._replace_preedit(self._ime_selected if result is None else result)
        if editor is not None:
            if result is None:
                for offset, image in self._ime_selected_images:
                    widget.image_create(f"{self._ime_start}+{offset}c", image=image, align="center")
                widget.mark_set("insert", f"{self._ime_start}+{self._ime_length + len(self._ime_selected_images)}c")
            widget.tag_remove("ime_preedit", "1.0", "end")
        self._ime_active = False
        self._ime_widget = None
        if editor is not None:
            self.after_idle(editor._on_keyrelease, None)
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
                if self._resizing:
                    point.x += self._resize_target[0] - self.winfo_rootx()
                    point.y += self._resize_target[1] - self.winfo_rooty()
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

    def add_todo(self):
        return self.focused.add_todo()

    def _pill_press(self, _e):
        # 按压态：短暂加深再回弹
        self._draw_pill(pressed=True)
        self.add_pill.after(120, self._draw_pill)
        self.add_todo()

    def _draw_pill(self, width=None, height=None, pressed=False):
        c = self.add_pill
        w = width or c.winfo_width() - 4
        h = height or c.winfo_height() - 4
        if w < 30 or h < 30:
            return
        if getattr(self, "_pill_render", None) == (w, h, pressed):
            return
        # 放大绘制再缩小，让边框和圆角都有抗锯齿。
        scale = 4
        img = Image.new("RGB", (w * scale, h * scale), self._bg)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((scale, scale, (w - 2) * scale, (h - 1) * scale),
                               radius=11 * scale,
                               fill="#EFF1F3" if pressed else "#FFFFFF",
                               outline="#2B2F36", width=round(2.5 * scale))
        img = img.resize((w, h), Image.Resampling.LANCZOS)
        if not hasattr(self, "_pill_icon"):
            with Image.open(os.path.join(ICON_DIR, "plus-normal.png")) as source:
                self._pill_icon = source.resize((26, 26), Image.Resampling.LANCZOS)
        img.paste(self._pill_icon, (round((w-26)/2), round((h-26)/2)), self._pill_icon)
        self._pill_image = ImageTk.PhotoImage(img, master=c)
        c.configure(image=self._pill_image)
        self._pill_render = (w, h, pressed)

    # ---- 标题胶囊 ----
    def _title_max_width(self):
        reserved = 8  # 标题两侧间距和与操作按钮之间的留白。
        for button in self._tool_btns:
            padding = button.pack_info()["padx"]
            padding = (padding, padding) if isinstance(padding, int) else self.tk.splitlist(padding)
            reserved += button.winfo_reqwidth() + sum(int(value) for value in padding)
        # 工具栏加到 4 个右侧按钮后窄窗口余量变小，极限时允许胶囊缩到 22，保证按钮不挤掉。
        return max(22, self._layout_dimensions()[0] - 2 * (RADIUS + 4) - reserved)

    def _draw_chip(self):
        c = self.title_chip
        editing = self._title_editor is not None and self._title_editor.winfo_exists()
        title = self._title_var.get() if editing else self.d.get("title", "")
        text = title or "无标题"
        font = self._title_edit_font if editing else self._chip_font
        natural_width = font.measure(text) + 36
        w = min(self._title_max_width(), max(44, natural_width))
        h = max(28, font.metrics("linespace") + 12)
        render_key = (title, tuple(sorted(font.actual().items())), w, h, editing)
        if not editing and getattr(self, "_chip_render", None) == render_key:
            return
        self._chip_render = render_key
        c.delete("all")
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
    def update_status(self):
        columns = [self.d["lines"]] + ([self.d["lines2"]] if self.d.get("twoColumn") else [])
        todos = [l for lines in columns for l in lines if l["type"] == "todo"]
        done = sum(1 for l in todos if l["checked"])
        self.status.config(text=f"待办 {done}/{len(todos)}" if todos else "")
        self.clear_btn.config(fg="#6F7780" if done else "#A9AFB6")
        self._layout_footer()

    def _remember_columns_edit(self, column):
        if self._columns_editing or not self.d.get("twoColumn") or len(self.editors) != 2:
            return
        state = (deepcopy([editor.lines for editor in self.editors]),
                 [editor.text.index("insert") for editor in self.editors], column)
        if not self._columns_history or self._columns_history[-1][0] != state[0]:
            self._columns_history.append(state)
            self._columns_redo.clear()
        else:
            self._columns_history[-1] = state

    def _undo_columns_edit(self, column, redo=False):
        if any(self._ime_composing(editor.text) for editor in self.editors):
            return "break"
        self._columns_editing = True
        try:
            for editor in self.editors:
                editor._cancel_row_drag()
                editor._sync_lines_from_text()
        finally:
            self._columns_editing = False
        self._remember_columns_edit(column)
        if redo:
            if not self._columns_redo:
                return "break"
            state = self._columns_redo.pop()
            self._columns_history.append(state)
        else:
            if len(self._columns_history) < 2:
                return "break"
            self._columns_redo.append(self._columns_history.pop())
            state = self._columns_history[-1]
        self._columns_editing = True
        try:
            for editor, lines in zip(self.editors, state[0]):
                editor.lines = deepcopy(lines)
            for editor, cursor in zip(self.editors, state[1]):
                editor.render(keep_cursor=False)
                editor.text.mark_set("insert", cursor)
                editor._clamp_todo_cursor()
        finally:
            self._columns_editing = False
        editor = self.editors[state[2]]
        editor.text.focus_set()
        editor._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()
        return "break"

    # ---- 单双栏切换 ----
    def toggle_columns(self):
        focus = self.focus_get()
        if focus in self._editor_texts and self._ime_composing(focus):
            return
        for editor in self.editors:
            editor._cancel_row_drag()
        self._columns_editing = True
        if self.d.get("twoColumn"):
            for editor in self.editors:
                editor._sync_lines_from_text()
            # 单独的空待办仅作空栏占位；其余内容及空行保留归属，参与往返还原。
            if not self.editors[1]._is_empty_todo_column():
                self.d["lines"] += [dict(line, mergedColumn=1) for line in self.d["lines2"]]
            self.d["twoColumn"] = False
            self.d.pop("lines2", None)
            self.editors[1].teardown()
            del self.editors[1]
            if self.divider is not None:
                self.divider.destroy()
                self.divider = None
            self.editors[0].render(keep_cursor=False)
            # 旧双栏数据未记录原宽度，按旧版扩宽比例还原。
            self.d["w"] = self.d.pop("singleColumnWidth", max(280, round(self.winfo_width() / 1.8)))
            self.geometry(f"{self.d['w']}x{self.winfo_height()}+{self.winfo_x()}+{self.winfo_y()}")
        else:
            self.editors[0]._sync_lines_from_text()
            self.d["singleColumnWidth"] = self.winfo_width()
            right = [dict(line) for line in self.d["lines"] if line.get("mergedColumn") == 1]
            self.d["lines"] = [line for line in self.d["lines"] if line.get("mergedColumn") != 1]
            for line in right:
                line.pop("mergedColumn")
            self.d["twoColumn"] = True
            self.d["lines2"] = right or [{"type": "todo", "text": "", "checked": False}]
            self.editors[0].render(keep_cursor=False)
            self.editors.append(NoteEditor(self, 1))
            # 自动适度加宽，加宽后整体保持在工作区内。
            sw = self.winfo_screenwidth()
            new_w = min(max(520, round(self.d["singleColumnWidth"] * 1.3)), sw - 24)
            if new_w > self.winfo_width():
                self.d["w"] = new_w
                self.d["x"] = min(self.winfo_x(), max(0, sw - new_w))
                self.geometry(f"{new_w}x{self.winfo_height()}+{self.d['x']}+{self.winfo_y()}")
        self._columns_editing = False
        self._columns_history, self._columns_redo = [], []
        for editor in self.editors:
            editor.__dict__.pop("_edit_history", None)
            editor.__dict__.pop("_edit_redo", None)
            editor._remember_edit()
        self._last_sz = None
        self.update_idletasks()
        self._relayout()
        self._restyle()
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def _layout_divider(self, x, y, height):
        if self.divider is None:
            self.divider = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0,
                                     width=14, height=max(12, height))
            self.divider.bind("<Configure>", lambda _e: self._draw_divider())
        self.divider.place(x=x, y=y, width=14, height=max(12, height))

    def _draw_divider(self):
        c = self.divider
        if c is None:
            return
        c.delete("all")
        h = c.winfo_height()
        if h > 48:  # 圆头细灰线，上下各留一段不顶满
            margin = 16
            c.create_line(7, margin, 7, h - margin, fill="#C9CED5",
                          width=4, capstyle="round")

    # ---- 编辑事件 ----
    def _show_context_menu(self, x, y, actions, shortcuts=None):
        shortcuts = shortcuts or {}
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
        width = max(184, max(font.measure(label) + (font.measure(shortcuts[i]) + 32 if i in shortcuts else 0)
                             for i, (label, _) in enumerate(actions)) + 44)
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
            if i in shortcuts:
                cv.create_text(width - 18, top + row_h / 2, anchor="e", text=shortcuts[i],
                               fill=INK, font=font, tags=(f"item{i}",))
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

    def clear_done(self):
        self.app.archive_done(self)

    def _ctrl_wheel(self, e):
        step = 10 if e.delta > 0 else -10
        percent = round(self.app.data["settings"]["opacity"] * 100)
        self.app.set_opacity(percent + step)
        return "break"

    # ---- 窗口操作 ----
    def _drag_press(self, e):
        self._edge_sizing = False
        self._dragging = True
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()
    def _drag_move(self, e):
        if not self._dragging:
            return "break"
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")
    def _drag_release(self, _e):
        if not self._dragging:
            return "break"
        self._dragging = False
        self.d["x"], self.d["y"] = self.winfo_x(), self.winfo_y()
        self.app.schedule_save()

    # ---- 四边八向调整大小 ----
    def _edge_hit(self, x, y, widget=None):
        if widget is self.bar:
            y += self.bar.winfo_y()
        w, h = self._layout_dimensions()
        l, r, t, b = x < EDGE, x > w - EDGE, y < EDGE, y > h - EDGE  # 左 右 上 下
        if (x < CORNER or x > w - CORNER) and (y < CORNER or y > h - CORNER):
            # 圆角区整块判定为斜向，光标与拉伸方向随所在角自动匹配
            l, r = x < w / 2, x >= w / 2
            t, b = y < h / 2, y >= h / 2
        return l, r, t, b

    def _edge_cursor(self, e):
        if self._resizing:
            return "break"
        surface = getattr(e, "widget", self.cv)
        l, r, t, b = self._edge_hit(e.x, e.y, surface)
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
        if cur != surface.cget("cursor"):
            surface.config(cursor=cur)

    def _configure_frameless(self):
        style = user32.GetWindowLongPtrW(self.hwnd, -16)
        user32.SetWindowLongPtrW(self.hwnd, -16, style | 0x00040000)
        user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | 0x0004 | 0x0010 | 0x0020)

    def _layout_dimensions(self):
        if self._resizing:
            return self._resize_target[2:]
        return self.winfo_width(), self.winfo_height()

    def _resize_rectangle(self, rect, delta, edges, maximum):
        x, y, width, height = rect
        dx, dy = delta
        left, right, top, bottom = edges
        min_w = 520 if self.d.get("twoColumn") else 280  # 双栏每侧至少留出单栏的编辑宽度
        w = min(maximum[0], max(min_w, width + dx if right else width - dx if left else width))
        h = min(maximum[1], max(230, height + dy if bottom else height - dy if top else height))
        return (x + width - w if left else x, y + height - h if top else y, w, h)

    def _edge_press(self, event):
        if self._resizing or self._resize_ending:
            return "break"
        edges = self._edge_hit(event.x, event.y, getattr(event, "widget", None))
        if not any(edges):
            return self._drag_press(event)
        native = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(native))
        rect = (native.left, native.top, native.right-native.left, native.bottom-native.top)
        maximum = self.maxsize()
        self._resize_limits = (max(rect[2], maximum[0]), max(rect[3], maximum[1]))
        self._resize_pointer = (event.x_root, event.y_root)
        self._resize_release_point = self._resize_up_since = None
        self._resize_start = (rect, self._resize_pointer, edges)
        self._resize_target = rect
        self._resizing = self._resize_busy = True
        self._resize_ready = self._resize_cancel_requested = self._resize_release_pending = False
        self._edge_sizing, self._dragging = True, False
        try:
            user32.SetForegroundWindow(self.hwnd)
            self._resize_surface = ResizeSurface(self, self._resize_client_hwnd, rect)
            width, height = self._resize_surface.capacity
            # Keep the source allocation fixed. Only its three logical regions
            # reflow; the completed visible frame moves/resizes in one submission.
            self.geometry(f"{width}x{height}+{rect[0]}+{rect[1]}")
            for editor in self.editors:
                editor._edge_fade.update(*editor.body.yview())
            self.update_idletasks()
            self._grab_resize_keys()
            self.cv.grab_set_global()
            user32.SetCapture(self._resize_client_hwnd)
            self._resize_ready = True
            self._resize_next_frame = 0
            self._resize_dirty = True
        except Exception:
            self._resize_busy = False
            self._finish_resize(cancel=True, redraw=False)
            raise
        finally:
            self._resize_busy = False
        if self._resize_release_pending:
            self._finish_resize()
        else:
            self._resize_job = self.after(1, self._poll_resize)
        return "break"

    def _edge_move(self, event):
        if not self._resizing:
            return self._drag_move(event)
        if self._resize_release_point is None:
            self._resize_pointer = (event.x_root, event.y_root)
        if not self._resize_busy and time.perf_counter() >= self._resize_next_frame:
            if self._resize_job is not None:
                self.after_cancel(self._resize_job)
                self._resize_job = None
            self._poll_resize()
        return "break"

    def _edge_release(self, event):
        if not self._resizing:
            return self._drag_release(event)
        if self._resize_release_point is None:
            self._resize_release_point = (event.x_root, event.y_root)
        return self._finish_resize()

    def _poll_resize(self):
        self._resize_job = None
        if not self._resizing or self._resize_busy:
            return
        if self._resize_cancel_requested:
            self._finish_resize(cancel=True)
            return
        capture = user32.GetCapture()
        if (self._resize_ready and capture != self._resize_client_hwnd and
                (not capture or user32.IsChild(self.hwnd, capture)) and user32.GetAsyncKeyState(0x01) < 0):
            user32.SetCapture(self._resize_client_hwnd)
        start = time.perf_counter()
        self._resize_busy = True
        try:
            rect, (mx, my), edges = self._resize_start
            down = user32.GetAsyncKeyState(0x01) < 0
            if down and self._resize_release_point is None:
                self._resize_pointer = cursor_pos()
            px, py = self._resize_release_point or self._resize_pointer
            target = self._resize_rectangle(rect, (px-mx, py-my), edges, self._resize_limits)
            if target != self._resize_target or self._resize_dirty:
                self._resize_target = target
                self._render_resize_frame()
                self._resize_next_frame = start + .016
        except Exception:
            self._resize_busy = False
            self._finish_resize(cancel=True, redraw=False)
            raise
        finally:
            self._resize_busy = False
        if not self._resizing:
            return
        up = user32.GetAsyncKeyState(0x01) >= 0
        if up and self._resize_up_since is None:
            self._resize_up_since = time.perf_counter()
        if (self._resize_release_pending or
                (up and time.perf_counter() - self._resize_up_since >= .020)):
            self._finish_resize()
        elif self._resize_job is None:
            # Polling also handles mouse-up if Tk's pointer timer released capture.
            self._resize_job = self.after(1, self._poll_resize)

    def _render_resize_frame(self):
        surface = self._resize_surface
        if not surface:
            return
        x, y, width, height = self._resize_target
        if surface.ensure_capacity(width, height):
            sx, sy = self._resize_start[0][:2]
            self.geometry(f"{surface.capacity[0]}x{surface.capacity[1]}+{sx}+{sy}")
        self._relayout()
        # Configure queues text layout after the new body size is installed.
        # Drain it once, instead of first measuring the previous viewport.
        self.update_idletasks()
        if not self.winfo_exists() or not self._resizing:
            return
        # Tk copies pixels when child windows move, including the footer and
        # todo decorations after reflow. Repaint their final positions before
        # capturing the client DC, in single-column as well as split notes.
        widgets = [self, self.cv, self.toolbar, self.bar, self.status, self.add_pill, self.clear_btn]
        if self.divider is not None:
            widgets.append(self.divider)
        for editor in self.editors:
            widgets.extend((editor.body, editor.text, editor._drag_gutter, editor.scroll_thumb))
            widgets.extend(editor._selection_masks + editor._checkbox_widgets + editor._strike_widgets)
        for widget in widgets:
            widget.event_generate("<Expose>", x=0, y=0,
                                  width=widget.winfo_width(), height=widget.winfo_height())
        self.update_idletasks()
        if not self.winfo_exists() or not self._resizing:
            return
        regions = []
        for editor in self.editors:
            body, fade = editor.body, editor._edge_fade
            span = min(body.winfo_width(), max(0, editor.scroll_thumb.winfo_x()-body.winfo_x()))
            bottom_edge = min(body.winfo_y()+body.winfo_height(), self.bar.winfo_y())
            rows = min(fade.height, max(0, bottom_edge-body.winfo_y()))
            for bottom, visible in enumerate(fade._resize_visible):
                if visible and span > 0 and rows > 0:
                    regions.append((body.winfo_x(), body.winfo_y() +
                        (bottom_edge-body.winfo_y()-rows if bottom else 0), span, rows, bool(bottom)))
        surface.present(self._resize_target, regions)
        self._resize_dirty = False
        if self._ime_active and self._ime_widget is not None:
            self._sync_ime(self._ime_widget)

    def _grab_resize_keys(self):
        if not hasattr(self, "_resize_key_tag"):
            self._resize_key_tag = f"Resize:{self._w}"
            self.bind_class(self._resize_key_tag, "<Escape>", lambda _e: self._finish_resize(cancel=True))
        self._resize_key_widgets = []
        widgets = [self]
        while widgets:
            widget = widgets.pop()
            widgets.extend(widget.winfo_children())
            tags = widget.bindtags()
            self._resize_key_widgets.append((widget, tags))
            widget.bindtags((self._resize_key_tag, *tags))

    def _repair_resize_frame(self):
        # Only at the handover to the real window: invalidate Tk image clipping
        # caches after its native origin changes. Never walk this tree per frame.
        images = {self._pill_image}
        for editor in self.editors:
            images.update(editor._check_images.values())
        for button in self._tool_btns:
            images.update((button._image, button._active_image))
        for image in images:
            image.paste(ImageTk.getimage(image))
        widgets = [self]
        while widgets:
            widget = widgets.pop()
            widgets.extend(widget.winfo_children())
            if widget.winfo_ismapped():
                widget.event_generate("<Expose>", x=0, y=0,
                                      width=widget.winfo_width(), height=widget.winfo_height())
        self.update_idletasks()
        for editor in self.editors:
            editor._edge_fade._size = None
            editor._edge_fade.update(*editor.body.yview())
        dwmapi.DwmFlush()  # one handover fence, not part of the drag-frame path

    def _finish_resize(self, cancel=False, redraw=True, hidden=False):
        if not self._resizing:
            return "break"
        cancel |= self._resize_cancel_requested
        if self._resize_ending:
            if hidden:
                self._resize_cancel_requested |= cancel
                target = self._resize_start[0] if cancel else self._resize_target
                self.d.update(zip(("x", "y", "w", "h"), target))
                if self._resize_surface:
                    self._resize_surface.hide()
                self.withdraw()
                self._resizing = False
                if self._resize_surface:
                    self._resize_surface.close()
                    self._resize_surface = None
            return "break"
        if self._resize_busy and not hidden:
            self._resize_release_pending = True
            self._resize_cancel_requested |= cancel
            return "break"
        self._resize_ending = True
        self._resize_busy = True
        self._resize_ready = False
        surface = self._resize_surface
        target = self._resize_start[0] if cancel else self._resize_target
        restored_geometry = False
        try:
            if not cancel:
                rect, (mx, my), edges = self._resize_start
                if user32.GetAsyncKeyState(0x01) < 0 and self._resize_release_point is None:
                    self._resize_pointer = cursor_pos()
                px, py = self._resize_release_point or self._resize_pointer
                target = self._resize_rectangle(rect, (px-mx, py-my), edges, self._resize_limits)
            self._resize_target = target
            if redraw and not hidden and surface:
                self._render_resize_frame()
                if not self.winfo_exists() or self._resize_surface is not surface:
                    return "break"
            self._resizing = self._edge_sizing = False
            if hidden:
                if surface:
                    surface.hide()
                self.withdraw()
            self.geometry(f"{target[2]}x{target[3]}+{target[0]}+{target[1]}")
            if hidden:
                self.attributes("-alpha", self.d["opacity"])
            if not hidden:
                self.update_idletasks()
                self.attributes("-alpha", self.d["opacity"])
                if surface:
                    surface.keep_source_hidden()
                    surface.prepare_restore()
                if redraw:
                    self._repair_resize_frame()
            restored_geometry = True
            self.d.update(zip(("x", "y", "w", "h"), target))
            if not hidden:
                self.app.schedule_save()
        finally:
            self._resizing = self._edge_sizing = self._resize_ready = False
            if self._resize_cancel_requested:
                target = self._resize_start[0]
            if not restored_geometry and self.winfo_exists():
                self.geometry(f"{target[2]}x{target[3]}+{target[0]}+{target[1]}")
                if not hidden:
                    user32.SetWindowPos(self.hwnd, None, *target, 0x0004 | 0x0010)
            if self._resize_job is not None:
                self.after_cancel(self._resize_job)
                self._resize_job = None
            for widget, tags in getattr(self, "_resize_key_widgets", ()):
                if widget.winfo_exists():
                    widget.bindtags(tags)
            self._resize_key_widgets = []
            if self.cv.winfo_exists() and self.cv.grab_current() is self.cv:
                self.cv.grab_release()
            if user32.GetCapture() == self._resize_client_hwnd:
                user32.ReleaseCapture()
            if surface:
                surface.close()
            self._resize_surface = None
            self._resize_busy = self._resize_ending = self._resize_release_pending = self._resize_cancel_requested = False
        if not hidden and self.d["z"] == "bottom" and self.d.get("visible", True) and user32.IsIconic(self.hwnd):
            self.app._request_bottom_refresh()
        return "break"

    # ---- 层级 / 透明度 / 颜色 ----
    def _install_z_order(self):
        @_WNDPROC
        def window_proc(hwnd, message, wparam, lparam):
            protected = self.d["z"] == "bottom" and self.d.get("visible", True)
            if protected and message == 0x0112 and wparam & 0xFFF0 == 0xF020:  # SC_MINIMIZE
                return 0
            if protected and not self._resizing and message == 0x0018 and not wparam:  # WM_SHOWWINDOW
                self.app._request_bottom_refresh()
                return 0  # Keep Tk's intended visibility when the Shell hides the native window.
            if protected and ((message == 0x0005 and wparam == 1) or
                              (message == 0x0047 and user32.IsIconic(hwnd))):
                # Tk's wrapper may consume WM_SIZE itself, so also handle
                # WM_WINDOWPOSCHANGED before Tk sees the iconic geometry.
                self.app._request_bottom_refresh()
                return 0  # Do not let Tk lay out the editor at its iconic size.
            if message == 0x0083 and wparam:  # full client area
                return 0
            if message == 0x0085:
                return 0
            if message == 0x0086:
                return 1
            if message == 0x0084:
                return 1  # HTCLIENT; all eight resize zones are handled together.
            if message == 0x0112 and wparam & 0xFFF0 == 0xF000:
                return 0
            if (self._resizing and self._resize_ready and user32.GetAsyncKeyState(0x01) < 0 and
                    (message == 0x001F or (message == 0x0006 and wparam & 0xFFFF == 0))):
                self._resize_cancel_requested = True
            if message == 0x0046:
                position = ctypes.cast(lparam, ctypes.POINTER(_WINDOWPOS)).contents
                if self.d["z"] == "bottom" and not self._temporarily_raised and not position.flags & 0x0004:
                    position.hwndInsertAfter = _bottom_insert_after(hwnd, self.app._bottom_reference_hwnd)
            return user32.CallWindowProcW(self._z_old_proc, hwnd, message, wparam, lparam)

        self._z_proc = window_proc
        self._z_old_proc = user32.SetWindowLongPtrW(
            self.hwnd, -4, ctypes.cast(window_proc, ctypes.c_void_p))

    def set_z(self, state):
        if self.d["z"] == state:
            return
        previous = self.d["z"]
        self._temporarily_raised = False
        self.d["z"] = state
        self.apply_z()
        if previous == "bottom" and state == "normal":
            # 置底时窗口可能已经激活，取消后不会再收到激活带来的前移。
            user32.SetWindowPos(self.hwnd, 0, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | 0x0010)  # HWND_TOP / SWP_NOACTIVATE
        self._restyle()
        self.app.schedule_save()

    def apply_z(self):
        top = self.d["z"] == "top"
        self.attributes("-topmost", top)
        # The taskbar's Show Desktop button also invokes Peek while hovered;
        # Peek can fade a window without minimizing or changing WS_VISIBLE.
        excluded = wintypes.BOOL(self.d["z"] == "bottom")
        dwmapi.DwmSetWindowAttribute(self.hwnd, 12, ctypes.byref(excluded), ctypes.sizeof(excluded))
        # 隐藏的所有者可隐藏任务栏；工具窗口会被最小化后的激活顺序跳过。
        user32.SetWindowLongPtrW(self.hwnd, -8, self._owner_hwnd)  # GWLP_HWNDPARENT
        style = user32.GetWindowLongPtrW(self.hwnd, -20)
        user32.SetWindowLongPtrW(self.hwnd, -20, style & ~0x80)  # WS_EX_TOOLWINDOW
        if self.d["z"] == "bottom" and not self._temporarily_raised:
            user32.SetWindowPos(self.hwnd, 1, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | 0x0010)  # SWP_NOACTIVATE

    def hide(self):
        self._temporarily_raised = False
        self.d["visible"] = False
        if self._resizing:
            self._finish_resize(cancel=True, redraw=False, hidden=True)
        self.withdraw()
        self.app.schedule_save()

    def show(self, temporary_raise=False):
        self._temporarily_raised = temporary_raise and self.d["z"] == "bottom"
        self.d["visible"] = True
        self.deiconify()
        self.overrideredirect(True)
        self._round_window()
        self._configure_frameless()
        # 先前移到置顶层，再恢复原状态，确保被其他进程覆盖后也能显示到前面。
        if self.d["z"] != "bottom" or self._temporarily_raised:
            self.attributes("-topmost", True)
        self.apply_z()
        if self.d["z"] != "bottom" or self._temporarily_raised:
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


class FontSelector(tk.Canvas):
    """Rounded font field and popup; no native menu indicator."""

    def __init__(self, parent, variable, command):
        self.variable, self.command = variable, command
        self._popup = None
        self._font = tkfont.Font(root=parent, family=UI_FONT, size=10)
        width = max(self._font.measure(label) for label in FONT_CHOICES.values()) + 32
        height = self._font.metrics("linespace") + 16
        super().__init__(parent, width=width, height=height, bg=NOTE_BG,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())
        self.bind("<Button-1>", self._open)
        for key in ("<Return>", "<space>", "<Down>", "<Up>"):
            self.bind(key, self._open)
        self.bind("<Destroy>", self._destroy)

    def _draw(self):
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        image = Image.new("RGB", (width * 4, height * 4), NOTE_BG)
        border = "#94A3B8" if self.focus_get() == self else NOTE_BORDER
        ImageDraw.Draw(image).rounded_rectangle(
            (2, 2, width * 4 - 3, height * 4 - 3), radius=36,
            fill=NOTE_BG, outline=border, width=4)
        self._image = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=self)
        self.delete("all")
        self.create_image(0, 0, image=self._image, anchor="nw")
        self.create_text(16, height / 2, text=self.variable.get(), font=self._font,
                         fill="#2B2F36", anchor="w")

    def _close(self, _event=None):
        popup, self._popup = self._popup, None
        if popup is not None and popup.winfo_exists():
            popup.grab_release()
            popup.destroy()
        return "break"

    def _destroy(self, event):
        if event.widget == self:
            self._close()
            self.variable.trace_remove("write", self._trace)

    def _open(self, _event=None):
        if self._popup is not None:
            return self._close()
        self.focus_set()
        popup = self._popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg=T_COLOR)
        popup.attributes("-transparentcolor", T_COLOR)
        popup.attributes("-topmost", True)
        labels = list(FONT_CHOICES.values())
        row_height = max(30, self._font.metrics("linespace") + 14)
        width, height = self.winfo_width(), 12 + row_height * len(labels)
        canvas = tk.Canvas(popup, width=width, height=height, bg=T_COLOR,
                           highlightthickness=0, bd=0)
        canvas.pack()
        background = round_rect(canvas, 1, 1, width - 2, height - 2, 10,
                                fill=NOTE_BG, outline=NOTE_BORDER)
        highlights = []
        for index, label in enumerate(labels):
            top = 6 + index * row_height
            highlights.append(round_rect(canvas, 5, top, width - 5, top + row_height, 7,
                                          fill="#F2F3F5", outline="", state="hidden"))
            canvas.create_text(16, top + row_height / 2, text=label, font=self._font,
                               fill="#2B2F36", anchor="w")
        selected = labels.index(self.variable.get())

        def select(index):
            nonlocal selected
            canvas.itemconfigure(highlights[selected], state="hidden")
            selected = index
            canvas.itemconfigure(highlights[selected], state="normal")
            canvas.tag_raise(highlights[selected], background)

        def activate(index):
            self._close()
            self.variable.set(labels[index])
            self.command(labels[index])

        def click(event):
            x, y = event.x_root - popup.winfo_rootx(), event.y_root - popup.winfo_rooty()
            index = (y - 6) // row_height
            if 5 <= x < width - 5 and 0 <= index < len(labels):
                activate(index)
            else:
                self._close()
            return "break"

        def motion(event):
            index = (event.y - 6) // row_height
            if 0 <= index < len(labels):
                select(index)

        def key(event):
            if event.keysym in ("Up", "Down"):
                select((selected + (1 if event.keysym == "Down" else -1)) % len(labels))
            elif event.keysym in ("Return", "space"):
                activate(selected)
            elif event.keysym in ("Escape", "Tab"):
                self._close()
            return "break"

        canvas.bind("<Motion>", motion)
        popup.bind("<Button-1>", click)
        popup.bind("<KeyPress>", key)
        popup.bind("<FocusOut>", lambda _e: self._close())
        work = SettingsWindow._work_area(self.winfo_toplevel())
        x = max(work.left, min(self.winfo_rootx(), work.right - width))
        y = self.winfo_rooty() + self.winfo_height() + 4
        if y + height > work.bottom:
            y = max(work.top, self.winfo_rooty() - height - 4)
        popup.geometry(f"{width}x{height}+{x}+{y}")
        select(selected)
        popup.deiconify()
        popup.grab_set()
        popup.focus_set()
        return "break"


class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.withdraw()
        self.app = app
        self.title("z-note 设置")
        self._icon_image = ImageTk.PhotoImage(_make_tray_icon(), master=self)
        self.iconphoto(False, self._icon_image)
        self.overrideredirect(True)
        self.resizable(True, True)
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
        font_row = tk.Frame(f, bg=NOTE_BG)
        font_row.pack(fill="x", pady=(0, 8))
        tk.Label(font_row, text="字体", bg=NOTE_BG, fg="#2B2F36",
                 font=(UI_FONT, 10)).pack(side="left")
        self.font_choice = tk.StringVar(master=self, value=FONT_CHOICES[app.data["settings"]["fontFamily"]])
        self.font_selector = FontSelector(font_row, self.font_choice, self._font_family)
        self.font_selector.pack(side="right")
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
        for action, name in HOTKEY_LABELS.items():
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

        update_heading = section("关于与更新")
        tk.Label(update_heading, text=f"z-note {APP_VERSION}", bg=NOTE_BG, fg=QUIET,
                 font=(UI_FONT, 9)).pack(side="right")
        self._update_cancel = None
        self._update_busy = False
        self._update_release = self._update_download = None
        self._update_installing = False
        self._update_state = "idle"
        actions = tk.Frame(f, bg=NOTE_BG)
        actions.pack(fill="x")

        def update_button(text, command):
            font = tkfont.Font(root=self, family=UI_FONT, size=9)
            w, h = font.measure(text) + 24, font.metrics("linespace") + 14
            image = Image.new("RGB", (w * 4, h * 4), NOTE_BG)
            ImageDraw.Draw(image).rounded_rectangle((2, 2, w*4-3, h*4-3), radius=32,
                                                     fill=NOTE_BG, outline=NOTE_BORDER, width=4)
            image = ImageTk.PhotoImage(image.resize((w, h), Image.Resampling.LANCZOS), master=self)
            button = tk.Button(actions, text=text, command=command, image=image, compound="center",
                               bg=NOTE_BG, activebackground=NOTE_BG, fg="#475569", font=font,
                               relief="flat", bd=0, highlightthickness=0, cursor="hand2")
            button._image = image
            button.pack(side="left", padx=(0, 8))
            return button

        # Reserve enough width for every state without changing the window width.
        self._update_button = update_button("下载并安装", self._update_action)
        self._update_button.configure(text="检查更新")
        self._update_cancel_button = update_button("取消", self._cancel_update)
        self._update_cancel_button.pack_forget()
        self._update_status = tk.Label(f, text="点击检查正式版本更新。", bg=NOTE_BG, fg=QUIET,
                                       font=(UI_FONT, 9), justify="left", anchor="w")
        self._update_status.pack(fill="x", pady=(8, 0))
        self._update_notes = tk.Label(f, text="", bg=NOTE_BG, fg="#475569",
                                      font=(UI_FONT, 9), justify="left", anchor="w")
        self._update_notes.pack(fill="x", pady=(4, 0))
        release_link = tk.Label(f, text="查看发布页面 ↗", bg=NOTE_BG, fg=QUIET,
                                font=(UI_FONT, 9), cursor="hand2")
        release_link.pack(anchor="w", pady=(4, 0))
        release_link.bind("<Button-1>", lambda _e: webbrowser.open(updater.RELEASES_URL))
        self.bind("<Destroy>", self._destroy_update, add="+")

        def layout_hotkeys(content_width):
            for hotkey_row, label, entry in hotkey_rows:
                label_width = int(self.tk.call("font", "measure", label.cget("font"), label.cget("text")))
                stacked = entry.winfo_reqwidth() + label_width + 28 > content_width
                label.grid_configure(row=0, column=0, columnspan=2 if stacked else 1,
                                     pady=(0, 4) if stacked else 0)
                entry.grid_configure(row=1 if stacked else 0, column=0 if stacked else 1,
                                     columnspan=2 if stacked else 1)
                label.configure(wraplength=max(1, content_width-16 if stacked else
                                                content_width-entry.winfo_reqwidth()-28))

        self.update_idletasks()
        work = SettingsWindow._work_area(self)
        scale = max(1, round(self.winfo_fpixels("1i")) / 96)
        margin = max(8, round(16 * scale))
        available_w, available_h = work.right - work.left - 2*margin, work.bottom - work.top - 2*margin
        width = min(max(round(480 * scale), f.winfo_reqwidth() + 2 * RADIUS), available_w)
        content_width = width - 2 * RADIUS
        # 文本按实际可用宽度换行，窗口高度在换行完成后再计算。
        startup.configure(wraplength=max(1, content_width - self._check_images[False].width() - 16))
        hotkey_hint.configure(wraplength=max(1, content_width - 16))
        path.configure(wraplength=max(1, content_width - 16))
        for label in (self._update_status, self._update_notes):
            label.configure(wraplength=max(1, content_width - 16))
        layout_hotkeys(content_width)
        body.itemconfigure(content, width=content_width)
        for appearance_row in (font_row, row, row3):
            label = appearance_row.winfo_children()[0]
            label.configure(wraplength=max(1, content_width - 16), justify="left")
            if appearance_row.winfo_reqwidth() > content_width - 8:
                label.pack_configure(side="top", anchor="w")
        self.update_idletasks()
        content_height = f.winfo_reqheight()
        height = min(content_height + 2 * RADIUS, available_h)
        scrollbar = VerticalThumb(self, body, NOTE_BG)
        scrollbar.configure(width=8)
        body.configure(yscrollcommand=scrollbar.set)
        self.bind("<MouseWheel>", lambda e: body.yview_scroll(-int(e.delta / 120), "units"))
        border = cv.create_rectangle(0, 0, width - 1, height - 1,
                                     fill=NOTE_BG, outline=NOTE_BORDER)

        def layout(event):
            if event.widget != self:
                return
            w, h = event.width, event.height
            viewport_height = max(1, h - 2 * RADIUS)
            # Reserve scrollbar space so wrapping does not oscillate at the overflow boundary.
            content_width = max(1, w - 2 * RADIUS - scrollbar.winfo_reqwidth() - 4)
            body.place(x=RADIUS, y=RADIUS, width=content_width, height=viewport_height)
            body.itemconfigure(content, width=content_width)
            startup.configure(wraplength=max(1, content_width - self._check_images[False].width() - 16))
            hotkey_hint.configure(wraplength=max(1, content_width - 16))
            path.configure(wraplength=max(1, content_width - 16))
            for label in (self._update_status, self._update_notes):
                label.configure(wraplength=max(1, content_width - 16))
            layout_hotkeys(content_width)
            for appearance_row in (row, row3):
                label = appearance_row.winfo_children()[0]
                label.configure(wraplength=max(1, content_width - 16), justify="left")
                controls_width = sum(c.winfo_reqwidth() for c in appearance_row.winfo_children()[1:])
                label.pack_configure(side="top" if controls_width + label.winfo_reqwidth() > content_width - 8 else "left",
                                     anchor="w")
            cv.coords(border, 0, 0, w - 1, h - 1)

        def update_region(_event=None):
            body.configure(scrollregion=(0, 0, body.winfo_width(), f.winfo_reqheight()))
            if f.winfo_reqheight() > body.winfo_height():
                scrollbar.place(x=self.winfo_width() - RADIUS - scrollbar.winfo_reqwidth(), y=RADIUS,
                                width=scrollbar.winfo_reqwidth(), height=body.winfo_height())
            else:
                scrollbar.place_forget()
                body.yview_moveto(0)

        self.bind("<Configure>", layout)
        f.bind("<Configure>", update_region)
        body.bind("<Configure>", update_region)
        SettingsWindow._lock_size(self, width, height)
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

    def _update_action(self):
        if self._update_busy or self._update_installing:
            return
        if self._update_state == "ready":
            path, checksum = self._update_download
            try:
                self.app.install_update(path, self._update_release, checksum)
            except Exception as exc:
                self._update_status.configure(text=f"无法安装更新：{exc}")
            return
        if self._update_state == "found" and not self._is_installed():
            webbrowser.open(updater.RELEASES_URL)
            return
        downloading = self._update_state == "found"
        cancel = self._update_cancel = threading.Event()
        self._update_busy = True
        self._update_button.configure(state="disabled")
        self._update_cancel_button.pack(side="left")
        self._update_cancel_button.configure(state="normal")
        self._update_status.configure(text="正在下载更新…" if downloading else "正在检查更新…")
        messages, release = self.app.q, self._update_release

        def work():
            try:
                if downloading:
                    value = updater.download_update(release, cancel, lambda done, total:
                        messages.put(("update", self, cancel, "progress", (done, total))))
                    kind = "downloaded"
                else:
                    value, kind = updater.check_update(cancel), "checked"
                if cancel.is_set():
                    if kind == "downloaded":
                        updater.remove_download(value[0])
                    raise updater.Cancelled("已取消更新。")
                messages.put(("update", self, cancel, kind, value))
            except updater.Cancelled:
                messages.put(("update", self, cancel, "cancelled", "已取消更新。"))
            except Exception as exc:
                messages.put(("update", self, cancel, "error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _is_installed():
        return bool(getattr(sys, "frozen", False) and
                    Path(sys.executable).with_name("unins000.exe").is_file())

    def _cancel_update(self):
        if self._update_cancel is not None:
            self._update_cancel.set()
            self._update_status.configure(text="正在取消…")
            self._update_cancel_button.configure(state="disabled")

    def _destroy_update(self, event):
        if event.widget != self or self._update_installing:
            return
        if self._update_cancel is not None:
            self._update_cancel.set()
        if self._update_download is not None:
            updater.remove_download(self._update_download[0])

    def _receive_update(self, cancel, kind, value):
        if cancel is not self._update_cancel or cancel.is_set():
            if kind == "downloaded":
                updater.remove_download(value[0])
            if cancel is not self._update_cancel or kind == "progress":
                return
            kind, value = "cancelled", "已取消更新。"
        if kind == "progress":
            done, total = value
            self._update_status.configure(text=f"正在下载：{done / total:.0%}（{done / 1048576:.1f} / {total / 1048576:.1f} MB）")
            return
        self._update_busy = False
        self._update_cancel_button.pack_forget()
        self._update_button.configure(state="normal")
        if kind == "checked":
            self._update_release = value
            self._update_state = "found" if value else "idle"
            self._update_status.configure(text=f"发现新版本 {value.version}" if value else "已是最新版本。")
            self._update_notes.configure(text=value.notes[:4000] if value else "")
        elif kind == "downloaded":
            self._update_download = value
            self._update_state = "ready"
            self._update_status.configure(text="下载并校验完成。安装将保存数据并退出 z-note，无需卸载。")
        else:
            self._update_status.configure(text=value)
        text = {"idle": "检查更新", "found": "下载并安装" if self._is_installed() else "前往下载",
                "ready": "立即安装"}[self._update_state]
        self._update_button.configure(text=text)

    def _lock_size(self, width, height):
        self.minsize(width, height)
        self.maxsize(width, height)
        self.resizable(False, False)
        self._resize = None

    def _work_area(self):
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = user32.MonitorFromPoint(wintypes.POINT(*cursor_pos()), 2)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.work
        work = wintypes.RECT()
        user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0)
        return work

    def _drag_press(self, event):
        x, y = event.x_root-self.winfo_rootx(), event.y_root-self.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        if (x < EDGE or x > w-EDGE or y < EDGE or y > h-EDGE or
                ((x < CORNER or x > w-CORNER) and (y < CORNER or y > h-CORNER))):
            self._drag = None
            return
        if (event.widget in (self._close_button, self._minimize_button, *self._hotkey_borders) or
                isinstance(event.widget, (SettingsScale, VerticalThumb, tk.Checkbutton, tk.Button, tk.Scrollbar, tk.Entry))):
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
            for action in HOTKEY_LABELS:
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

    def _font_family(self, label):
        self.app.data["settings"]["fontFamily"] = next(key for key, value in FONT_CHOICES.items() if value == label)
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
        font = ImageFont.truetype(str(FONT_DIR / "classic.ttf"), 20)
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
        panes.columnconfigure(1, weight=2, uniform="panes")
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
        work = SettingsWindow._work_area(self)
        scale = max(1, round(self.winfo_fpixels("1i")) / 96)
        margin = max(8, round(16 * scale))
        width = min(max(round(640 * scale), self.winfo_reqwidth()), work.right-work.left-2*margin)
        height = min(round(480 * scale), work.bottom-work.top-2*margin)
        SettingsWindow._lock_size(self, width, height)
        self.geometry(f"{width}x{height}+{work.left+(work.right-work.left-width)//2}+{work.top+(work.bottom-work.top-height)//2}")
        self.update_idletasks()
        hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        preference = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
        style = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, style & ~0x80 | 0x40000)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | 0x04 | 0x20)
        self.hint.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(1, e.width), justify="left"))
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
        stamp = "clearedAt" if self.mode == "archives" else "deletedAt"
        records = sorted(reversed(self.app.data[self.mode]),
                         key=lambda record: record.get(stamp, ""), reverse=True)
        for record in records:
            key = record["noteId"] if self.mode == "archives" else record["id"]
            groups.setdefault(key, []).append(record)
        self.groups = groups
        self.ids = list(groups)
        names = {}
        for key, records in groups.items():
            source = sources.get(key, records[0])
            names[key] = (source.get("title") or "无标题", source.get("editedAt", ""))
        self.checked.intersection_update(self.ids)
        for child in self.left.content.winfo_children():
            child.destroy()
        self.names = []
        self.rows = {}
        for key in self.ids:
            name, stamp = names[key]
            suffix = ""
            if sum(value[0] == name for value in names.values()) > 1:
                suffix = " · " + (stamp.split(".")[0].replace("T", " ") or "编辑时间未知")
            self.names.append(name + suffix)
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
            label.pack(side="left", fill="x", expand=not suffix)
            if suffix:
                row.bind("<Configure>", lambda e, l=label: l.configure(wraplength=max(1, e.width // 2)))
                detail = tk.Label(row, text=suffix, bg=NOTE_BG, fg=INK, anchor="w",
                                  justify="left", font=(UI_FONT, 10), cursor="hand2")
                detail.pack(side="left", fill="x", expand=True)
                detail.bind("<Configure>", lambda e, l=detail: l.configure(wraplength=max(1, e.width-8)))
                detail.bind("<Button-1>", lambda e,k=key: self.choose(k))
            else:
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

    def label(self, text, bold=False, parent=None):
        label = tk.Label(parent or self.content, text=text, bg=NOTE_BG, fg="#2B2F36",
            font=(UI_FONT, 10, "bold") if bold else (UI_FONT, 10), justify="left", anchor="w")
        label.pack(fill="x", pady=(6, 4), padx=4)
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(1, e.width-8)))

    def preview_line(self, line, parent=None):
        if line["type"] != "todo":
            self.label(line["text"], parent=parent)
            return
        row = tk.Frame(parent or self.content, bg=NOTE_BG)
        row.pack(fill="x", padx=4, pady=(6, 4))
        marker = tk.Label(row, image=self._check_images[line["checked"]], bg=NOTE_BG)
        marker.pack(side="left", anchor="center", padx=(0, 8))
        text = tk.Label(row, text=line["text"], bg=NOTE_BG, fg="#2B2F36",
                        font=(UI_FONT, 10, "overstrike" if line["checked"] else "normal"),
                        justify="left", anchor="w")
        text.pack(side="left", fill="x", expand=True)
        text.bind("<Configure>", lambda e: text.configure(wraplength=max(1, e.width-8)))

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
            if note.get("twoColumn"):
                for key, name in (("lines", "左栏"), ("lines2", "右栏")):
                    section = tk.Frame(self.content, bg=NOTE_BG)
                    section.pack(fill="x", padx=4, pady=(8, 12))
                    background = tk.Canvas(section, bg=NOTE_BG, highlightthickness=0, bd=0)
                    background.place(x=0, y=0, relwidth=1, relheight=1)
                    heading = tk.Label(section, text=name, bg="#F5F7FA", fg=QUIET,
                                       font=(UI_FONT, 9), anchor="w", pady=6)
                    heading.pack(fill="x", padx=12, pady=(2, 0))
                    body = tk.Frame(section, bg=NOTE_BG)
                    body.pack(fill="x", padx=8, pady=(4, 10))
                    def draw_section(_event=None, card=section, canvas=background, title=heading):
                        w, h = card.winfo_width(), card.winfo_height()
                        if w < 26 or h < 26:
                            return
                        header_h = title.winfo_y() + title.winfo_height()
                        canvas.delete("all")
                        round_rect(canvas, 1, 1, w-1, h-1, 12, fill=NOTE_BG, outline=NOTE_BORDER)
                        round_rect(canvas, 2, 2, w-2, header_h+12, 11, fill="#F5F7FA", outline="")
                        canvas.create_rectangle(2, header_h, w-2, header_h+13, fill=NOTE_BG, outline="")
                    section.bind("<Configure>", draw_section)
                    heading.bind("<Configure>", draw_section)
                    lines = note.get(key) or []
                    if not lines:
                        tk.Label(body, text="暂无内容", bg=NOTE_BG, fg=QUIET,
                                 font=(UI_FONT, 10), anchor="w").pack(fill="x", padx=4, pady=6)
                    for line in lines:
                        self.preview_line(line, parent=body)
            else:
                for line in note["lines"]:
                    self.preview_line(line)
        else:
            for batch in records:
                self.label("清除于 " + batch["clearedAt"].replace("T", " "), True)
                for item in batch["items"]:
                    self.preview_line(item["line"])
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
        load_fonts()
        self.data = load_data()
        settings = self.data["settings"]
        if settings.get("fontFamily") not in FONT_CHOICES:
            settings["fontFamily"] = "classic"
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
        migrate_z = self.data.get("schemaVersion", 1) < 3
        if migrate_z:
            # 旧版 bottom 只是未置顶，升级后保持原来的普通层级。
            for note in self.data["notes"] + self.data["deletedNotes"]:
                if note.get("z") == "bottom":
                    note["z"] = "normal"
            self.data["schemaVersion"] = 3
        self.q = queue.Queue()
        self._bottom_refresh_pending = False
        self._bottom_hooks = []
        self._quitting = False
        self.root = tk.Tk()
        self._system_font_family = tkfont.nametofont("TkDefaultFont", root=self.root).actual("family")
        # 钉死 tk scaling：首次运行记录系统 DPI，此后不再跟随系统缩放变化，画面始终一致。
        # 想重新适配当前缩放，删掉 %APPDATA%/z-note/notes.json 里的 uiDpi 即可。
        dpi = self.data["settings"].get("uiDpi")
        if not dpi:
            dpi = round(self.root.winfo_fpixels("1i"))
            self.data["settings"]["uiDpi"] = dpi
            save_data(self.data)
        self.root.tk.call("tk", "scaling", dpi / 72.0)
        self.root.withdraw()
        # An independent hidden window marks the ordinary window band. Unlike
        # Tk's root wrapper, its handle and owner order never change.
        self._bottom_reference_hwnd = user32.CreateWindowExW(
            0, "STATIC", None, 0x88000000, 0, 0, 0, 0, None, None, None, None)
        if not self._bottom_reference_hwnd:
            raise ctypes.WinError()
        for name in ("TkDefaultFont", "TkMenuFont"):
            tkfont.nametofont(name, root=self.root).configure(family=UI_FONT)
        self.notes = {}
        self._save_job = None
        self._cascade = 0
        self.settings_win = None
        self.show_notes = self.data["settings"]["showOnStartup"]

        self.base_font = tkfont.Font(family=self.note_font_family(),
                                     size=self.data["settings"]["fontSize"])

        self._restore_notes()
        self._start_bottom_guard()
        if migrate_opacity or migrate_z:
            self.save_now()
        self._start_tray()
        self._start_hotkeys()
        self.root.after(120, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

    # 便签管理
    def _restore_notes(self):
        if self.data.get("welcomeVersion") != APP_VERSION:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.data["notes"].append({
                "id": str(uuid.uuid4()), "x": sw - 480, "y": 90, "w": 440, "h": 560,
                "title": "使用指南",
                "z": "top", "opacity": self.data["settings"]["opacity"],
                "color": self.data["settings"]["color"], "visible": True,
                "lines": [
                    {"type": "text", "text": f"欢迎使用 z-note {APP_VERSION}", "checked": False},
                ] + [{"type": "todo", "text": text, "checked": False} for text in (
                    "点方框勾选，右键编辑行",
                    f"文字 / 待办：{self.hotkey_text('convert')}",
                    "拖三横线排序，可跨栏拖动",
                    "顶部切双栏，点标题改名",
                    "图钉置顶，向下箭头置底",
                    "减号隐藏，底部清除已完成",
                    "托盘：新建、记录、回收站",
                    "设置：字体、透明度与更新",
                )]})
            self.data["welcomeVersion"] = APP_VERSION
            self.save_now()
        for d in self.data["notes"]:
            d.setdefault("editedAt", datetime.now().isoformat(timespec="microseconds"))
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            d["x"] = min(max(0, d["x"]), max(0, sw - 120))
            d["y"] = min(max(0, d["y"]), max(0, sh - 60))
            d["w"] = max(d.get("w", 320), 520 if d.get("twoColumn") else 300)  # 旧数据太窄会挤掉工具栏
            d["h"] = max(d.get("h", 300), 240)
            if d.get("z") not in ("normal", "bottom", "top"):
                d["z"] = "normal"
            w = NoteWindow(self, d)
            self.notes[d["id"]] = w
        if self.show_notes:
            self.show_all(temporary_raise=False)

    def note_font_family(self):
        choice = self.data["settings"]["fontFamily"]
        return self._system_font_family if choice == "system" else FONT_FAMILIES[choice]

    def update_fonts(self):
        family = self.note_font_family()
        self.base_font.config(family=family, size=self.data["settings"]["fontSize"])
        for w in self.notes.values():
            w._chip_font.config(family=family)
            w._draw_chip()
            for editor in w.editors:
                editor._sync_lines_from_text()
                editor.done_font.config(family=family, size=self.data["settings"]["fontSize"])
                editor._make_check_images()
                editor.render()

    def set_opacity(self, percent):
        percent = min(100, max(50, 10 * round(percent / 10)))
        value = percent / 100
        self.data["settings"]["opacity"] = value
        for note in self.notes.values():
            note.d["opacity"] = value
            if note._resizing:
                note._resize_dirty = True
            else:
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
             "w": 440, "h": 477, "z": "normal",
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
        keys = ("lines", "lines2") if w.d.get("twoColumn") else ("lines",)
        done = [(key, i, deepcopy(line)) for key in keys
                for i, line in enumerate(w.d.get(key) or [])
                if line["type"] == "todo" and line["checked"]]
        if not done:
            return
        def change(data):
            note = next(n for n in data["notes"] if n["id"] == w.d["id"])
            data["archives"].append({"id": str(uuid.uuid4()), "noteId": note["id"],
                "title": note.get("title", ""), "editedAt": note.get("editedAt", ""),
                "clearedAt": datetime.now().isoformat(timespec="seconds"),
                "emptied": len(done) == sum(len(note.get(key) or []) for key in keys),
                "items": [{"col": 0 if key == "lines" else 1, "index": i, "line": line}
                          for key, i, line in done]})
            for key in keys:
                note[key] = [line for line in note.get(key) or []
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
            for item in items:
                key = "lines2" if item.get("col") == 1 and note.get("twoColumn") else "lines"
                if (item_index is not None or batch.get("emptied")) and note[key] == [
                        {"type": "text", "text": "", "checked": False}]:
                    note[key] = []
                line = deepcopy(item["line"])
                if item_index is None:
                    note[key].insert(min(item["index"], len(note[key])), line)
                else:
                    line["checked"] = False
                    note[key].append(line)
            note["editedAt"] = datetime.now().isoformat(timespec="microseconds")
            if item_index is None:
                data["archives"] = [b for b in data["archives"] if b["id"] != batch_id]
        if self._store_history(change):
            self.notes[note_id].render(keep_cursor=False)

    def hide_all(self):
        for w in self.notes.values():
            w.hide()
        self.schedule_save()

    def show_all(self, temporary_raise=True):
        for w in self.notes.values():
            w.show(temporary_raise=temporary_raise)
        if temporary_raise and self.notes:
            user32.SetForegroundWindow(w.hwnd)
        self.schedule_save()

    def _request_bottom_refresh(self):
        if not self._quitting and not self._bottom_refresh_pending:
            self._bottom_refresh_pending = True
            self.q.put("refresh_bottom")

    def _start_bottom_guard(self):
        @_WINEVENTPROC
        def on_event(hook, event, hwnd, object_id, child_id, thread, timestamp):
            # Out-of-context callbacks only enqueue work; all Tk/Win32 repairs
            # run through the existing main-thread dispatcher.
            if event < 0x8000 or (object_id in (0, -4) and child_id == 0):
                self._request_bottom_refresh()

        self._bottom_event_proc = on_event
        for first, last in ((0x0003, 0x0003), (0x0016, 0x0017), (0x8002, 0x8004)):
            flags = 0 if first == 0x0003 else 2  # Observe our own foreground changes too.
            hook = user32.SetWinEventHook(first, last, None, on_event, 0, 0, flags)
            if not hook:
                self._stop_bottom_guard()
                raise ctypes.WinError()
            self._bottom_hooks.append(hook)
        self.root.bind("<Destroy>", lambda e: self._stop_bottom_guard() if e.widget == self.root else None,
                       add="+")

    def _stop_bottom_guard(self):
        self._quitting = True
        for hook in getattr(self, "_bottom_hooks", ()):
            user32.UnhookWinEvent(hook)
        self._bottom_hooks = []
        reference = getattr(self, "_bottom_reference_hwnd", None)
        if reference:
            user32.DestroyWindow(reference)
            self._bottom_reference_hwnd = None

    def _refresh_bottom_notes(self):
        self._bottom_refresh_pending = False
        if self._quitting:
            return
        if any(note._temporarily_raised for note in self.notes.values()):
            hwnd = user32.GetForegroundWindow()
            in_app = not hwnd  # Keep the recall through transient activation gaps.
            while hwnd:
                process = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
                if process.value == os.getpid():
                    in_app = True
                    break
                hwnd = user32.GetWindow(hwnd, 4)  # GW_OWNER includes associated IME windows.
            if not in_app:
                for note in self.notes.values():
                    note._temporarily_raised = False
        for note in self.notes.values():
            if note.d["z"] != "bottom" or not note.d.get("visible", True) or note._resizing:
                continue
            if user32.IsIconic(note.hwnd) or not user32.IsWindowVisible(note.hwnd):
                user32.ShowWindow(note.hwnd, 4)  # SW_SHOWNOACTIVATE restores without stealing focus.
            if note._temporarily_raised:
                continue
            anchor = _bottom_insert_after(note.hwnd, self._bottom_reference_hwnd)
            temporary_top = anchor == -1 or (anchor not in (0, 1, -2) and
                            bool(user32.GetWindowLongPtrW(anchor, -20) & 0x8))
            flags = SWP_NOMOVE | SWP_NOSIZE | 0x0010 | 0x0200 | 0x0400
            # Set the native band explicitly before inserting after its last
            # window. This does not change the note's saved bottom state.
            if temporary_top != bool(user32.GetWindowLongPtrW(note.hwnd, -20) & 0x8):
                user32.SetWindowPos(note.hwnd, -1 if temporary_top else -2, 0, 0, 0, 0, flags)
            # Some system windows reject insertion; use the next usable window above them.
            while not user32.SetWindowPos(note.hwnd, anchor, 0, 0, 0, 0, flags):
                if anchor in (0, 1, -1, -2):
                    break
                anchor = user32.GetWindow(anchor, 3)  # GW_HWNDPREV
                if anchor == note.hwnd:
                    anchor = user32.GetWindow(anchor, 3)
                if not anchor:
                    break
                if not temporary_top and user32.GetWindowLongPtrW(anchor, -20) & 0x8:
                    anchor = -2  # Stay below the topmost band.

    # 托盘
    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem(lambda _item: f"新建便签  {self.hotkey_text('new')}", lambda: self.q.put("new")),
            pystray.MenuItem(lambda _item: f"显示所有便签  {self.hotkey_text('show')}", lambda: self.q.put("show")),
            pystray.MenuItem(lambda _item: f"隐藏所有便签  {self.hotkey_text('hide')}", lambda: self.q.put("hide")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("清除记录与回收站…", lambda: self.q.put("history")),
            pystray.MenuItem("设置…", lambda: self.q.put("settings")),
            pystray.MenuItem("退出", lambda: self.q.put("quit")),
        )
        self.icon = pystray.Icon(APP_NAME, _make_tray_icon(), "z-note 便签", menu)
        self.icon.run_detached()

    def hotkey_text(self, action):
        if action in LOCAL_HOTKEY_ACTIONS:
            setting, default = LOCAL_HOTKEY_ACTIONS[action]
        else:
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
        parsed = parse_hotkey(value)
        for other, label in HOTKEY_LABELS.items():
            if other != action and parse_hotkey(self.hotkey_text(other)) == parsed:
                return f"此快捷键已用于另一项功能“{label}”，请换一个组合键。"
        reserved = {"Ctrl+A": "全选", "Ctrl+C": "复制", "Ctrl+X": "剪切", "Ctrl+V": "粘贴",
                    "Ctrl+Z": "撤销", "Ctrl+Y": "重做", "Ctrl+Shift+Z": "重做",
                    "Ctrl+Insert": "复制", "Shift+Insert": "粘贴", "Shift+Delete": "剪切",
                    "Ctrl+Left": "光标移动", "Ctrl+Right": "光标移动",
                    "Ctrl+Up": "光标移动", "Ctrl+Down": "光标移动",
                    "Ctrl+Home": "光标移动", "Ctrl+End": "光标移动",
                    "Ctrl+Shift+Left": "选区移动", "Ctrl+Shift+Right": "选区移动",
                    "Ctrl+Shift+Up": "选区移动", "Ctrl+Shift+Down": "选区移动",
                    "Ctrl+Shift+Home": "选区移动", "Ctrl+Shift+End": "选区移动",
                    "Ctrl+Backspace": "删除文字", "Ctrl+Delete": "删除文字",
                    "Ctrl+D": "删除文字", "Ctrl+H": "删除文字", "Ctrl+K": "删除至行尾",
                    "Ctrl+O": "插入换行", "Ctrl+T": "交换字符", "Ctrl+I": "插入制表符",
                    "Ctrl+Tab": "切换焦点", "Ctrl+Shift+Tab": "切换焦点",
                    "Ctrl+Space": "设置选区起点", "Ctrl+Shift+Space": "扩展选区",
                    "Ctrl+PgUp": "水平翻页", "Ctrl+PgDn": "水平翻页"}
        for shortcut, label in reserved.items():
            if parse_hotkey(shortcut) == parsed:
                return f"此快捷键与正文已有的“{label}”快捷键冲突，请换一个组合键。"
        if not (parsed[0] & 3) and not 112 <= parsed[1] <= 135:
            return "请使用 Ctrl / Alt 组合键，或 F1–F24 功能键。"
        if action in LOCAL_HOTKEY_ACTIONS:
            if self.hotkey_text(action) != value:
                self.data["settings"][LOCAL_HOTKEY_ACTIONS[action][0]] = value
                self.save_now()
            return None
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
            setting, default = LOCAL_HOTKEY_ACTIONS["convert"]
            if self.hotkey_text("convert") != default:
                self.data["settings"][setting] = default
                self.save_now()
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
        for setting, default in LOCAL_HOTKEY_ACTIONS.values():
            self.data["settings"][setting] = default
        self.save_now()
        if hasattr(self, "icon"):
            self.icon.update_menu()
        return None

    def _pump(self):
        try:
            while True:
                act = self.q.get_nowait()
                if isinstance(act, tuple) and act[0] == "update":
                    _, window, cancel, kind, value = act
                    if window.winfo_exists():
                        window._receive_update(cancel, kind, value)
                    elif kind == "downloaded":
                        updater.remove_download(value[0])
                elif isinstance(act, tuple) and act[0] == "capture_hotkey":
                    _, window, action, value = act
                    if window.winfo_exists():
                        window._apply_hotkey(action, value)
                elif act == "new":
                    self.new_note()
                elif act == "hide":
                    self.hide_all()
                elif act == "show":
                    self.show_all()
                elif act == "refresh_bottom":
                    self._refresh_bottom_notes()
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

    def install_update(self, path, release, checksum):
        if not SettingsWindow._is_installed():
            raise updater.UpdateError("请先使用安装版；源码或便携版请从发布页面下载安装。")
        updater.verify_installer(path, release, checksum)
        for w in self.notes.values():
            if w._ime_active:
                raise updater.UpdateError("请先完成便签中的输入法组词，再安装更新。")
            w._sync_lines_from_text()
            title = getattr(w, "_title_editor", None)
            if title is not None and title.winfo_exists():
                w.d["title"] = title.get().strip()
        self._save_positions()
        save_data(self.data)
        backup_before_update()
        # AppMutex keeps Setup from replacing files until this process exits.
        subprocess.Popen([str(path)], cwd=str(path.parent))
        if self.settings_win is not None:
            self.settings_win._update_installing = True
        self.quit(saved=True)

    def _save_positions(self):
        for w in self.notes.values():
            if w._resizing:
                w._finish_resize(cancel=w._resize_cancel_requested, redraw=False, hidden=True)
            else:
                w.d["x"], w.d["y"] = w.winfo_x(), w.winfo_y()

    def quit(self, saved=False):
        self._stop_bottom_guard()
        if not saved:
            self._save_positions()
            self.save_now()
        try:
            self.icon.stop()
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)

def main():
    ensure_single_instance()
    App().root.mainloop()

if __name__ == "__main__":
    main()
