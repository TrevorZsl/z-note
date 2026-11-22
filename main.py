# -*- coding: utf-8 -*-
"""z-note 雏形：常驻托盘的桌面便签（自由文本 + 可勾选待办）。
运行: python main.py   依赖安装: python -m pip install -r requirements.txt
"""
import atexit, ctypes, math, os, queue, struct, sys, time, uuid, winreg
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import tkinter as tk
import subprocess
import threading
import webbrowser
from pathlib import Path
from _tkinter import DONT_WAIT, WINDOW_EVENTS
from tkinter import font as tkfont
from ctypes import wintypes

import pystray
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk

from storage import DATA_FILE, DEFAULT_SETTINGS, load_data, save_data, backup_before_update
from version import APP_VERSION
import updater
from resize_surface import NOTE_CORNER_PREFERENCE, ResizeSurface
from bundled_fonts import CLASSIC_FAMILY, FONT_CHOICES, FONT_FAMILIES, FONT_DIR, load_fonts

APP_NAME = "z-note"
UI_FONT = CLASSIC_FAMILY
NOTE_BG, NOTE_BORDER = "#FFFFFF", "#DFE2E6"
SELECTION_BG = "#DCE7F3"
T_COLOR = "#010203"   # 窗口透明色键（圆角外的部分透出去）
RADIUS = 20
EDGE = 7                              # 窗口边缘可拖拽调大小的宽度
CORNER = RADIUS + 4                   # 四角斜向拉伸热区：整个圆角区都能触发，比 EDGE 好按
ICON_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets", "icons")
PREFIX_PAD = "  "                       # 勾选框后跟 2 个空格
INK, QUIET = "#6B7280", "#666D75"      # 图标与状态栏的墨色（冷灰单色系）
# Windows 上 Tk 的 16ms 定时器有时要等近两个系统时钟节拍。
VISIBILITY_TICK_MS = 10
# 托盘与全局快捷键经队列转交主线程，轮询间隔决定点击后的响应延迟。
PUMP_MS = 30

# ---------- Win32 ----------
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.BeginDeferWindowPos.argtypes = [ctypes.c_int]
user32.BeginDeferWindowPos.restype = wintypes.HANDLE
user32.DeferWindowPos.argtypes = [wintypes.HANDLE, wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.DeferWindowPos.restype = wintypes.HANDLE
user32.EndDeferWindowPos.argtypes = [wintypes.HANDLE]
user32.EndDeferWindowPos.restype = wintypes.BOOL
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
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
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.HRGN, wintypes.UINT]
user32.RedrawWindow.restype = wintypes.BOOL
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
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

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

def _round_popup(window):
    """菜单类弹窗交给 DWM 裁圆角，边缘抗锯齿，并带系统细边框和阴影，与设置窗口一致。"""
    window.update_idletasks()  # Tk 首次布局时才创建外层窗口，之前设置会落到别的句柄上。
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    preference = ctypes.c_int(2)  # DWMWCP_ROUND
    dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))

def smooth_round_rect(cv, x1, y1, x2, y2, r, fill, outline=""):
    # Tk 画布的多边形没有抗锯齿；这里用 4 倍超采样的 PIL 图片贴上去，圆角边缘才圆润。
    # 图片带透明通道，阴影可以与下层内容正常混合；图片缓存在画布上，避免拖动时每帧重画。
    w, h = max(2, round(x2-x1)), max(2, round(y2-y1))
    r = max(0, min(r, w/2, h/2))
    cache = cv.__dict__.setdefault("_smooth_rects", {})
    key = (w, h, round(r, 1), fill, outline)
    photo = cache.get(key)
    if photo is None:
        if len(cache) > 64:
            cache.clear()
        scale = 4
        image = Image.new("RGBA", (w*scale, h*scale), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle(
            (0, 0, w*scale-1, h*scale-1), radius=r*scale, fill=fill or None,
            outline=outline or None, width=scale)
        photo = cache[key] = ImageTk.PhotoImage(image.resize((w, h), Image.Resampling.LANCZOS), master=cv)
    return cv.create_image(round(x1), round(y1), image=photo, anchor="nw")


def check_source(size, checked):
    """勾选框的 4 倍原图；缩到 size 即为显示用图。"""
    scale = 4
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
    return img


def check_images(master, size):
    return {checked: ImageTk.PhotoImage(
                check_source(size, checked).resize((size, size), Image.Resampling.LANCZOS), master=master)
            for checked in (False, True)}

# ---------- 开机启动 ----------
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def autostart_registered():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            command, kind = winreg.QueryValueEx(key, APP_NAME)
    except FileNotFoundError:
        return False
    return kind == winreg.REG_SZ and os.path.normcase(command) == os.path.normcase(autostart_command())


def set_autostart(on):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        if on:
            raise

# ---------- 悬停提示 ----------
class Tooltip:
    """悬停片刻后出现的圆角小提示，全局同一时间只显示一个。"""
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
        tw, cv = self._new_popup(self._w)
        cv.create_text(w / 2, h / 2, text=text, fill="#3A3F46",
                       font=(UI_FONT, 9))
        x = max(4, self._w.winfo_rootx() + self._w.winfo_width() // 2 - w // 2)
        y = self._w.winfo_rooty() - h - 6
        if y < 0:
            y = self._w.winfo_rooty() + self._w.winfo_height() + 6
        self._show_popup(tw, cv, w, h, x, y)

    @staticmethod
    def _new_popup(widget):
        popup = tk.Toplevel(widget)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.attributes("-alpha", 0)
        canvas = tk.Canvas(popup, bg=NOTE_BG, highlightthickness=0, bd=0)
        return popup, canvas

    @staticmethod
    def _show_popup(popup, canvas, width, height, x, y):
        def paint(retry=True):
            if Tooltip._cur is not popup or not canvas.winfo_exists():
                return
            if Tooltip._paint(popup, canvas, width, height):
                return
            if retry:
                # 首次绘制时 Windows 可能还没准备好供捕获的客户区画面。
                canvas._root().after(16, paint, False)
                return
            canvas.configure(bg=T_COLOR)
            border = round_rect(canvas, 1, 1, width-2, height-2, 8,
                                fill=NOTE_BG, outline="#D9DCE1")
            canvas.tag_lower(border)
            popup.attributes("-transparentcolor", T_COLOR)
            popup.attributes("-alpha", 1)

        def on_expose(_event):
            canvas.unbind("<Expose>")
            canvas._root().after_idle(paint)

        canvas.bind("<Expose>", on_expose)
        canvas.place(x=0, y=0, width=width, height=height)
        popup.geometry(f"{width}x{height}+{x}+{y}")
        Tooltip._cur = popup
        popup.deiconify()

    @staticmethod
    def _paint(popup, canvas, width, height):
        # 保留 Tk 排版与字形，将白底文字合成到带逐像素透明度的圆角上。
        scale = 4
        background = Image.new("RGBA", (width*scale, height*scale))
        bounds = (scale, scale, (width-1)*scale-1, (height-1)*scale-1)
        ImageDraw.Draw(background).rounded_rectangle(
            bounds, radius=8*scale, fill=NOTE_BG, outline="#D9DCE1", width=scale)
        background = background.resize((width, height), Image.Resampling.BOX)
        size = width*height*4
        header = ctypes.create_string_buffer(struct.pack(
            "<IiiHHIIiiII", 40, width, -height, 1, 32, 0, size, 0, 0, 0, 0))
        bits = ctypes.c_void_p()
        dc = gdi32.CreateCompatibleDC(None)
        if not dc:
            return False
        bitmap = old = None
        try:
            bitmap = gdi32.CreateDIBSection(dc, header, 0, ctypes.byref(bits), None, 0)
            if not bitmap:
                return False
            old = gdi32.SelectObject(dc, bitmap)
            if not old or not user32.PrintWindow(canvas.winfo_id(), dc, 2):
                return False
            gdi32.GdiFlush()
            text = Image.frombytes("RGB", (width, height), ctypes.string_at(bits, size), "raw", "BGRX")
            if text.getpixel((0, 0)) != (255, 255, 255):
                return False
            image = ImageChops.multiply(background.convert("RGB"), text)
            image.putalpha(background.getchannel("A"))
            pixels = image.convert("RGBa").tobytes("raw", "BGRa")
            ctypes.memmove(bits, pixels, size)

            hwnd = user32.GetParent(popup.winfo_id()) or popup.winfo_id()
            disabled = wintypes.BOOL(True)
            dwmapi.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(disabled), ctypes.sizeof(disabled))
            style = user32.GetWindowLongPtrW(hwnd, -20)
            # 先清除 Tk 为全窗透明度设置的分层样式，再切换到逐像素合成。
            user32.SetWindowLongPtrW(hwnd, -20, style & ~0x00080000)
            user32.SetWindowLongPtrW(hwnd, -20, style | 0x00080000)
            extent, origin = wintypes.SIZE(width, height), _POINT(0, 0)
            blend = ctypes.c_uint32(0x01FF0000)
            return user32.UpdateLayeredWindow(hwnd, None, None, ctypes.byref(extent), dc,
                                              ctypes.byref(origin), 0, ctypes.byref(blend), 2)
        finally:
            if old:
                gdi32.SelectObject(dc, old)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dc)

# ---------- 工具栏圆形按钮 ----------
def load_icon(master, name, size=22, active=False):
    state = "active" if active else "normal"
    with Image.open(os.path.join(ICON_DIR, f"{name}-{state}.png")) as image:
        return ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS),
                                  master=master)


class ToolButton(tk.Canvas):
    """圆角方形悬浮底 + Lucide 图标；置顶、置底激活时保留浅灰圆角底。"""

    def __init__(self, master, icon, get_tip, cmd, bg, size=30):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._bg, self._active = bg, False
        self._image = load_icon(self, icon)
        self._active_image = load_icon(self, icon, active=True) if icon in ("pin", "arrow-down-to-line", "columns-2") else self._image
        # 悬停底与置顶、置底激活底同形状，只靠深浅区分状态。
        self._pill = round_rect(self, 0, 0, size, size, 7, fill="", outline="", state="hidden")
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
    def __init__(self, parent, target, bg, *, auto_hide=False):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, cursor="arrow")
        self.target = target
        self._thumb_image = None
        self._drag_offset = None
        self._auto_hide = auto_hide
        self._shown = False
        self._hovered = False
        self._hide_job = None
        self._thumb_key = None
        self._thumb_cache = {}
        self.bind("<Configure>", lambda e: self.set(*target.yview()))
        self.bind("<Button-1>", self.move)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", self._release)
        if auto_hide:
            self.bind("<Enter>", self._enter)
            self.bind("<Leave>", self._leave)
            self.bind("<Unmap>", self._unmap)
            self.bind("<Destroy>", lambda _e: self._cancel_hide())

    def _cancel_hide(self):
        if self._hide_job is not None:
            self.after_cancel(self._hide_job)
            self._hide_job = None

    def _hide(self):
        self._cancel_hide()
        self._shown = False
        self.delete("thumb")
        self._thumb_image = None
        self._thumb_key = None

    def _unmap(self, _event):
        self._hovered = False
        self._drag_offset = None
        self._hide()

    def _schedule_hide(self):
        self._cancel_hide()
        if self._shown and not self._hovered and self._drag_offset is None:
            self._hide_job = self.after(1000, self._hide)

    def reveal(self):
        first, last = self.target.yview()
        if last - first >= .999:
            return
        if not self._shown:
            self._shown = True
            self.set(first, last)
        self._schedule_hide()

    def _enter(self, _event):
        self._hovered = True
        self.reveal()

    def _leave(self, _event):
        self._hovered = False
        self._schedule_hide()

    def _release(self, _event):
        self._drag_offset = None
        if self._auto_hide:
            self._schedule_hide()

    def set(self, first, last):
        first, last = float(first), float(last)
        key = (first, last, self.winfo_height())
        if key == self._thumb_key:
            return
        self._thumb_key = key
        self.delete("all")
        self._thumb_image = None
        length = self.winfo_height()-8
        if last-first >= .999:
            if self._auto_hide:
                self._cancel_hide()
                self._shown = False
                self._drag_offset = None
            return
        if length < 1 or (self._auto_hide and not (self._shown or self._hovered)):
            self._thumb_key = None
            return
        self._shown = True
        height = max(6, round((last-first)*length) + 6)
        image = self._thumb_cache.get(height)
        if image is None:
            scale = 4
            image = Image.new("RGBA", (8*scale, (height+2)*scale), (0, 0, 0, 0))
            ImageDraw.Draw(image).rounded_rectangle(
                (scale, scale, 7*scale-1, (height+1)*scale-1),
                radius=3*scale, fill="#A7B0BE")
            image = ImageTk.PhotoImage(
                image.resize((8, height+2), Image.Resampling.LANCZOS), master=self)
            self._thumb_cache[height] = image
            if len(self._thumb_cache) > 8:
                self._thumb_cache.pop(next(iter(self._thumb_cache)))
        self._thumb_image = image
        self.create_image(0, round(first*length), anchor="nw", image=self._thumb_image, tags="thumb")

    def move(self, event):
        first, last = self.target.yview()
        if last-first >= .999:
            return
        position = (event.y-4)/max(1,self.winfo_height()-8)
        if self._drag_offset is None:
            self._drag_offset = position-first if first <= position <= last else (last-first)/2
        if self._auto_hide:
            self.reveal()
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

    def schedule_repaint(self):
        # 勾选框是随正文滚动的子窗口，渐隐层下方的旧像素不会自动擦净，会拖出叠影、
        # 也会让进入渐隐区的勾选框漏画。滚动落定后只对渐隐区补一次重绘，渐隐本身不变。
        if getattr(self, "_repaint_job", None) is None and self.windows:
            self._repaint_job = self.body.after_idle(self._repaint)

    def _repaint(self):
        self._repaint_job = None
        if not self.body.winfo_exists():
            return
        self.body.update_idletasks()  # 先让 place 把正文和勾选框移到位，再重绘。
        hwnd = self.body.winfo_id()
        if not hwnd or not user32.IsWindow(hwnd) or not self.height:
            return
        width, viewport = self.body.winfo_width(), self.body.winfo_height()
        for visible, top in zip(self._visible, (0, viewport - self.height)):
            if visible:
                rect = wintypes.RECT(0, top, width, top + self.height)
                # RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW；不加 RDW_ERASE，避免闪一下底色。
                user32.RedrawWindow(hwnd, ctypes.byref(rect), None, 0x0181)

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
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p,
    ctypes.c_void_p, wintypes.HDC, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL


def selection_corner_pieces(rects, radius):
    """按 VS Code 的方式给选区轮廓的每个转角配圆角片。rects 是各显示行露出的选区 (左, 上, 右, 下)；
    返回 (x, y, 半径, top, left, concave)：凸角削在矩形内，凹角在矩形外侧补一道反向圆弧。
    同一台阶处凸角和凹角各取台阶宽的一半以内，窄台阶也不会出缺口。"""
    pieces = []
    for i, (left, top, right, bottom) in enumerate(rects):
        cap = min(radius, (right - left) // 2, (bottom - top) // 2)
        for is_top in (True, False):
            edge = top if is_top else bottom
            neighbour = next((other for j, other in enumerate(rects) if j != i
                              and abs((other[3] if is_top else other[1]) - edge) <= 1
                              and other[0] < right and other[2] > left), None)
            for is_left in (True, False):
                if neighbour is None:
                    size, concave = cap, False
                else:
                    step = neighbour[0] - left if is_left else right - neighbour[2]
                    if abs(step) <= 1:
                        continue  # 与相邻行齐平，轮廓在这里是直线
                    concave = step < 0
                    size = min(radius, -step // 2) if concave else min(cap, step // 2)
                if size < 1:
                    continue
                if concave:
                    x = left - size if is_left else right
                else:
                    x = left if is_left else right - size
                pieces.append((x, top if is_top else bottom - size, size, is_top, is_left, concave))
    return pieces


class NoteEditor:
    """一栏正文编辑器：Text + 勾选框/删除线覆盖层 + 分块滚动。NoteWindow 持有 1~2 个实例。"""

    BODY_PAD_X = 12
    DRAG_GUTTER_WIDTH = 54
    INSERT_WIDTH = 2
    INSERT_HALF_WIDTH = INSERT_WIDTH // 2

    def __init__(self, win, col):
        self.win, self.col = win, col
        self.d = win.d  # 与窗口共享同一便签字典；只有 lines 按 col 分流
        self.app, self._bg, self.tk = win.app, win._bg, win.tk
        self.done_font = tkfont.Font(family=win.app.note_font_family(),
                                     size=win.app.data["settings"]["fontSize"])
        self.body = tk.Canvas(win, bg=self._bg, bd=0, highlightthickness=0, yscrollincrement=1)
        self.text = tk.Text(self.body, bg=self._bg, fg="#2B2F36", bd=0, highlightthickness=0,
                            padx=self.BODY_PAD_X - self.INSERT_HALF_WIDTH, pady=10,
                            wrap="char", spacing1=9, spacing3=10,
                            selectbackground=self._bg, inactiveselectbackground=self._bg,
                            selectforeground="#2B2F36", selectborderwidth=0,
                            font=win.app.base_font, insertbackground="#2B2F36",
                            insertwidth=self.INSERT_WIDTH, cursor="xterm")
        self.text.place(x=0, y=0, relwidth=1)
        self.scroll_thumb = VerticalThumb(win, self.body, self._bg, auto_hide=True)
        self._make_check_images()
        self.text.tag_config("done", font=self.done_font)  # 只加删除线，不改字色
        self.text.tag_config("selection_fill", background=SELECTION_BG, foreground="#2B2F36")
        self.text.tag_raise("selection_fill", "sel")
        self.text.bind("<<Selection>>", self._paint_selection)
        self.text.tag_configure("ime_preedit", underline=True)
        self._strike_widgets = []
        self._selection_masks = []
        self._selection_corners = []  # 选区圆角片，独立于底色遮罩
        self._selection_corner_spares = []  # 隐藏备用的角片，下次选区直接复用
        self._selection_corner_cache = None
        self._checkbox_widgets = []
        self._pending_removal = None  # 删整行前记下的旧布局，由下一次 render 播放收起
        self._removal = None
        self._removal_tags = set()  # 已配置的收起间距 tag；重配或删除会让 Tk 重测全文
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
        self._selection_carry = 0.0
        self._selection_time = 0.0
        self._strike_job = None
        self._edge_fade = EdgeFade(self.body)
        self._row_drag = None
        self._row_drag_job = None
        # Tk 会把软换行选区画进右边距；独立覆盖层让拖拽区始终保持便签底色。
        self._drag_gutter = tk.Frame(self.text, bg=self._bg)
        self._drag_gutter.place(relx=1, x=-self.BODY_PAD_X - self.DRAG_GUTTER_WIDTH + self.INSERT_HALF_WIDTH,
                                y=0, width=self.DRAG_GUTTER_WIDTH - self.INSERT_HALF_WIDTH,
                                relheight=1, bordermode="ignore")
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
        handle_image = Image.new("RGBA", (96, 96))
        handle_draw = ImageDraw.Draw(handle_image)
        for y in (8, 12, 16):
            handle_draw.rounded_rectangle((28, y * 4 - 2, 67, y * 4 + 2),
                                          radius=2, fill="#9198A2")
        self._drag_handle_image = ImageTk.PhotoImage(
            handle_image.resize((24, 24), Image.Resampling.LANCZOS), master=self._drag_handle)
        self._drag_handle.create_image(12, 12, image=self._drag_handle_image)
        self._drag_handle.bind("<Button-1>", self._row_drag_press)
        self._drag_handle.bind("<B1-Motion>", self._row_drag_motion)
        self._drag_handle.bind("<ButtonRelease-1>", self._row_drag_release)
        self._drag_handle.bind("<Escape>", self._cancel_row_drag)
        self._drag_escape = win.bind("<Escape>", self._cancel_row_drag, add="+")
        self._drag_leave = win.bind("<Leave>",
            lambda e: self.win.after_idle(self._hide_drag_handle), add="+")
        self._drag_handle.bind("<Leave>", self._hide_drag_handle, add="+")
        # 给居中的插入光标留出裁切空间，在段落边距内补回文字位置和换行宽度。
        self.text.tag_configure("drag_margin", lmargin1=self.INSERT_HALF_WIDTH,
                                lmargin2=self.INSERT_HALF_WIDTH,
                                rmargin=self.DRAG_GUTTER_WIDTH + self.INSERT_HALF_WIDTH,
                                lmargincolor=self._bg, rmargincolor=self._bg)
        self.text.tag_raise("todo_indent", "drag_margin")
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
        # 拖选由 B1-Motion 和 _selection_scroll 驱动；Tk 自带的出界扫描会用旧坐标反复重算选区。
        t.bind("<B1-Leave>", lambda e: "break")
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
        self._edge_fade.schedule_repaint()
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
            self.scroll_thumb.reveal()
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
        self.scroll_thumb.reveal()
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
        self._drag_handle.place(x=self.text.winfo_width()-55+self.BODY_PAD_X,
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
                    measure.tag_raise("todo_indent", "drag_margin")
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
                before = editor.body.yview()
                editor.body.yview_scroll(step, "units")
                if editor.body.yview() != before:
                    editor.scroll_thumb.reveal()
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
                    smooth_round_rect(cv, 5, y+2, width-5, y+height-2, min(9, height/3),
                                      mix(self._bg, "#B6C4D7", .22))
                    self._draw_drag_row(cv, i, y, muted=True)
                else:
                    self._draw_drag_row(cv, i, y)
            y += height
        height = drag["rows"][drag["source"]][0]
        y = pointer_y-drag["offset"]
        smooth_round_rect(cv, 6, y+5, width-4, y+height, min(10, height/3),
                          mix(self._bg, "#677588", .18))
        smooth_round_rect(cv, 3, y+1, width-7, y+height-4, min(10, height/3),
                          self._bg, "#AAB7C8")
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
                        smooth_round_rect(canvas, 5, y+2, width-5, y+height-2, min(9, height/3),
                                          mix(self._bg, "#B6C4D7", .22))
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
        smooth_round_rect(canvas, 6, y+5, width-4, y+height, min(10, height/3),
                          mix(self._bg, "#677588", .18))
        smooth_round_rect(canvas, 3, y+1, width-7, y+height-4, min(10, height/3),
                          self._bg, "#AAB7C8")
        editor._draw_drag_row(canvas, None, y, line=line, layout=(height, segments))

    def _draw_drag_row(self, cv, index, y, muted=False, line=None, layout=None):
        line = self.lines[index] if line is None else line
        height, segments = self._row_drag["rows"][index] if layout is None else layout
        x = self.BODY_PAD_X
        if line["type"] == "todo":
            cv.create_image(x+self._check_margin+self._check_margin//2, y+height/2,
                            image=self._check_images[line["checked"]], anchor="w")
            x += int(self.text.tag_cget("todo_indent", "lmargin2")) - self.INSERT_HALF_WIDTH
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
        removal = self._removal
        if removal is not None and removal["began"] is None:
            box = self.text.bbox("insert")
            top = box and box[1] + self.text.winfo_y()
            if box and 0 <= top and top + box[3] <= self.body.winfo_height():
                # 删除整行时立即滚动会让还没收起的空位把下面的行顶下去，交给收起动画逐帧完成。
                removal["scroll_request"] = y
                return
            # 光标不在可见区时照旧立即滚过去，收起从滚动后的位置开始。
            removal["scroll_request"] = None
            self.body.yview_moveto(y / self._content_height)
            removal["top_start"] = round(self.body.canvasy(0))
            return
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
            if self.body.canvasy(0) != top:
                self.scroll_thumb.reveal()
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
        self.scroll_thumb.reveal()
        self.body.update_idletasks()
        target = (self._body_index(box[0], round(cursor_y)) if box else
                  self._body_index(self.body.winfo_height() // 2, 0))
        command = "tk::TextKeySelect" if event.state & 1 else "tk::TextSetCursor"
        self.text.tk.call(command, self.text, target)
        self._clamp_todo_cursor()
        self._see_text("insert")
        return "break"

    def _selection_scroll(self, event=None):
        # 拖选出界后按时间连续滚动，离边缘越远越快；鼠标事件只更新位置，速度与事件频率无关。
        if event is not None:
            self._selection_x = event.x
            self._selection_y = event.y_root - self.body.winfo_rooty()
        height = self.body.winfo_height()
        pointer_y = self._selection_y
        outside = -pointer_y if pointer_y < 0 else (pointer_y - height + 1 if pointer_y >= height else 0)
        if not outside:
            self._stop_selection_scroll()
            if event is not None:
                self._drag_select(event.x, event.y)
            return "break"
        if event is not None and self._selection_scroll_job is not None:
            return "break"  # 计时器已在运行，下一帧会用最新的鼠标位置。
        now = time.monotonic()
        if event is not None:
            self._selection_carry = 0.0
            self._selection_time = now - .016
        scale = self.text.winfo_fpixels("1i") / 96
        speed = min(3000, 150 + 12 * outside / scale) * scale  # 像素/秒
        elapsed = min(.05, now - self._selection_time)
        self._selection_time = now
        self._selection_carry += (-1 if pointer_y < 0 else 1) * speed * elapsed
        step = int(self._selection_carry)
        self._selection_carry -= step
        # 先登记下一帧，处理窗口事件时重入也不会再启动第二个计时器。
        self._stop_selection_scroll()
        self._selection_scroll_job = self.win.after(16, self._selection_scroll)
        if not step:
            return "break"
        before = self.body.yview()
        self.body.yview_scroll(step, "units")
        self.body.update_idletasks()
        while self.tk.dooneevent(WINDOW_EVENTS | DONT_WAIT):
            pass
        self.body.update_idletasks()
        y = self.body.canvasy(max(0, min(height - 1, pointer_y))) - self._chunk_start
        self._drag_select(self._selection_x, round(y))
        if self.body.yview() != before:
            self.scroll_thumb.reveal()
        else:
            self._stop_selection_scroll()
        return "break"

    def _drag_select(self, x, y):
        """鼠标拖选按字逐个选。选区下端停在空行开头时，这一行连同换行符一起选上，光标仍留在这一行；
        下端只会是按下或指到的那一行，向上、向下拖都能选中起点或终点的空行，不会多选下一行。"""
        t = self.text
        t.tk.call("tk::TextSelectTo", t, x, y)
        if (t.tk.call("set", "::tk::Priv(selectMode)") == "char" and t.tag_ranges("sel") and
                t.compare("sel.first linestart", "==", "sel.last linestart") and not self._trimmed_range()):
            # 单行里只盖到勾选框（如空待办）：和空文字行一样不算选中，继续拖到别的行时按锚点重算。
            t.tag_remove("sel", "1.0", "end")
        if t.tk.call("set", "::tk::Priv(selectMode)") in ("char", "word") and t.tag_ranges("sel"):
            last = t.index("sel.last")
            row = int(last.split(".")[0])
            body = self._text_body(row)
            if (t.compare("sel.first", "<", f"{row}.0") and t.compare(body, ">=", f"{row}.end")
                    and t.compare(last, "<=", body)):
                t.tag_add("sel", last, f"{row}.end+1c")
        self._clamp_todo_cursor()

    def _select_to(self, x, y, mode):
        t = self.text
        cur = str(t.tk.call("tk::TextClosestGap", t, x, y))
        caret = self._todo_caret_index(cur)
        if mode == "word":
            self._select_words(cur, caret)
            return True
        if caret == cur:
            return False

        # Tk 会在 TextSelectTo 返回前重绘。前缀里的命中仍用于选区，插入光标要先落在正文。
        anchor = str(t.tk.call("tk::TextAnchor", t))
        if anchor not in t.mark_names():
            t.mark_set(anchor, cur)
        if t.compare(cur, "!=", anchor) or abs(int(t.tk.call("set", "::tk::Priv(pressX)")) - x) >= 3:
            t.tk.call("set", "::tk::Priv(mouseMoved)", 1)
        if mode == "line" or t.tk.getboolean(t.tk.call("set", "::tk::Priv(mouseMoved)")):
            first, last = (cur, anchor) if t.compare(cur, "<", anchor) else (anchor, cur)
            if mode == "line":
                first = t.index(f"{first} linestart")
                last = t.index(f"{last} lineend +1c")
            t.tag_remove("sel", "1.0", "end")
            t.mark_set("insert", caret)
            t.tag_add("sel", first, last)
            t.update_idletasks()
        return True

    def _key_select(self, index):
        t = self.text
        index = t.index(index)
        caret = self._todo_caret_index(index)
        if caret == index:
            return False
        anchor = str(t.tk.call("tk::TextAnchor", t))
        if not t.tag_ranges("sel") or anchor not in t.mark_names():
            t.mark_set(anchor, "insert")
        t.tk.call("tk::TextKeyExtend", t, index)
        t.mark_set("insert", caret)
        t.see("insert")
        t.update_idletasks()
        return True

    def _word_at(self, index, after):
        """index 所在那一条待办或文字里紧挨 index 的词，跨过自动换行，但不越过这一条的首尾，也不含勾选框。
        after 为真取 index 后面的字，否则取前面的字；词是连续的非空白或连续的空白，与 Tk 一致。空条目返回 None。"""
        t = self.text
        row, col = map(int, t.index(index).split("."))
        start = int(self._text_body(row).split(".")[1])
        content = t.get(f"{row}.{start}", f"{row}.end")
        if not content:
            return None
        i = max(0, min(len(content) - 1, col - start - (0 if after else 1)))
        space = content[i].isspace()
        first, last = i, i + 1
        while first and content[first - 1].isspace() == space:
            first -= 1
        while last < len(content) and content[last].isspace() == space:
            last += 1
        return f"{row}.{start + first}", f"{row}.{start + last}"

    def _select_words(self, cur, caret):
        """代替 Tk 的按词选择（双击、双击后拖动）：两端各在自己那一条里取词，不会顺着换行选到相邻条目；
        双击空条目不选中，光标留在这一条。"""
        t = self.text
        anchor_mark = str(t.tk.call("tk::TextAnchor", t))
        if anchor_mark not in t.mark_names():
            t.mark_set(anchor_mark, cur)
        anchor = t.index(anchor_mark)
        first, last = self._word_at(anchor, t.mark_gravity(anchor_mark) == "right") or (anchor, anchor)
        if t.compare(cur, "<", first):
            first = (self._word_at(cur, True) or (cur,))[0]
        elif t.compare(cur, ">", last):
            last = (self._word_at(cur, False) or (cur, cur))[1]
        t.tag_remove("sel", "1.0", "end")
        t.mark_set("insert", caret)
        if t.compare(first, "<", last):
            t.tag_add("sel", first, last)

    def _make_check_images(self):
        size = max(18, round(self.app.base_font.metrics("ascent") * 1.1))
        self._check_images = check_images(self.text, size)
        # 原生插入光标跨在字符边界上，勾选框子窗口不能覆盖占位符的左右边界。
        self._check_margin = max(1, int(self.text.cget("insertwidth")))
        # 框线到文字的可见间距取框宽的 0.45：收窄占位图，勾选框盖住前缀空格的左侧一段。
        # 框图右侧有 1px 透明边，光标仍停在空格之后，不被勾选框遮住。
        natural_gap = 1 + self._check_margin + self.app.base_font.measure(PREFIX_PAD)
        slot_width = size + 2 * self._check_margin - max(0, natural_gap - round(size * .45))
        self._check_slots = {checked: ImageTk.PhotoImage(Image.new("RGBA", (slot_width, size)), master=self.text)
                             for checked in (False, True)}
        # Text 在左内边距处裁切光标，首行也要留出半个光标的绘制宽度。
        caret_margin = self._check_margin // 2 + self.INSERT_HALF_WIDTH
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

    def _selection_mask(self, key, cursor):
        """按行取底色遮罩，key 如 ("tail", 3)、("margin", 3)：同一行一直用同一个遮罩，
        选区变大时已盖好的行不挪位置，挪开时不会露出 Tk 画的整行高亮。"""
        for mask in self._selection_masks:
            if mask._mask_key == key:
                break
        else:
            mask = tk.Frame(self.text, bg=self._bg)
            mask._mask_key = key
            mask.bind("<MouseWheel>", self._text_wheel)
            mask.bind("<Control-MouseWheel>", self.win._ctrl_wheel)
            mask.bind("<Motion>", self._hover_row)
            for event in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>", "<Button-3>"):
                mask.bind(event, lambda e, sequence=event: self.text.event_generate(
                    sequence, x=e.x_root - self.text.winfo_rootx(),
                    y=e.y_root - self.text.winfo_rooty(), time=e.time, state=e.state))
            self._selection_masks.append(mask)
        if mask.cget("cursor") != cursor:
            mask.configure(cursor=cursor)
        return mask

    def _cover_empty_row_tail(self, row):
        """Tk 把空行换行符的高亮一直画到行尾；只留一个空格宽的标记，其余用底色遮罩盖回。
        返回遮罩；这一行不在当前正文窗口里时返回 None。"""
        box, display = self.text.bbox(f"{row}.end"), self.text.dlineinfo(f"{row}.end")
        if not (box and display):
            return None
        left = box[0] + max(1, self.app.base_font.measure(" "))
        right = self._drag_gutter.winfo_x() - self.INSERT_HALF_WIDTH
        if right <= left:
            return None
        mask = self._selection_mask(("tail", row), "xterm")
        mask.place(x=left, y=display[1], width=right - left, height=display[3], bordermode="ignore")
        mask.lift()
        return mask

    def _selection_corner_radius(self):
        return max(2, round(3 * self.text.winfo_fpixels("1i") / 96))

    def _selection_corner_image(self, size, top, left, concave):
        """四分之一圆角图。凸角：圆心在内侧，圆内选区色、圆外底色，盖住 Tk 画的直角；
        凹角：圆心横向翻到外侧，圆内底色、圆外选区色，在选区外补出反向圆弧。"""
        if self._selection_corner_cache is None:
            self._selection_corner_cache = {}
        key = (size, top, left, concave, self._bg)
        image = self._selection_corner_cache.get(key)
        if image is None:
            big = size * 4
            inside, outside = (self._bg, SELECTION_BG) if concave else (SELECTION_BG, self._bg)
            canvas = Image.new("RGB", (big, big), outside)
            cx, cy = (big if left != concave else 0), (big if top else 0)
            ImageDraw.Draw(canvas).ellipse((cx - big, cy - big, cx + big, cy + big), fill=inside)
            image = ImageTk.PhotoImage(canvas.resize((size, size), Image.Resampling.LANCZOS), master=self.text)
            self._selection_corner_cache[key] = image
        return image

    def _selection_corner(self, index):
        """按序复用的角图控件；事件转给 Text，与底色遮罩一致。"""
        if index < len(self._selection_corners):
            return self._selection_corners[index]
        if self._selection_corner_spares:
            corner = self._selection_corner_spares.pop()
            self._selection_corners.append(corner)
            return corner
        corner = tk.Label(self.text, bd=0, highlightthickness=0, padx=0, pady=0, cursor="xterm")
        corner.bind("<MouseWheel>", self._text_wheel)
        corner.bind("<Control-MouseWheel>", self.win._ctrl_wheel)
        corner.bind("<Motion>", self._hover_row)
        for event in ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>", "<Button-3>"):
            corner.bind(event, lambda e, sequence=event: self.text.event_generate(
                sequence, x=e.x_root - self.text.winfo_rootx(),
                y=e.y_root - self.text.winfo_rooty(), time=e.time, state=e.state))
        self._selection_corners.append(corner)
        return corner

    def _selection_rects(self, first_row, last_row, content_height):
        """可见行里每个显示行实际露出的选区矩形 (左, 上, 右, 下)，与 Tk 画法和各遮罩的边界一致。"""
        t = self.text
        width = t.winfo_width()
        right_limit = width - self.BODY_PAD_X - self.DRAG_GUTTER_WIDTH
        visible_start = t.index("@0,0")
        rects = []
        for row in range(first_row, last_row + 1):
            # 相邻行的标记会和本行连成一段 tag，行首已在段内时取包含它的那段。
            found = (t.tag_prevrange("selection_fill", f"{row}.0+1c")
                     if "selection_fill" in t.tag_names(f"{row}.0")
                     else t.tag_nextrange("selection_fill", f"{row}.0", f"{row}.end"))
            if found and self.lines[row - 1]["text"]:
                start, end = found[0], found[1]
                if t.compare(start, "<", f"{row}.0"):
                    start = t.index(f"{row}.0")
                if t.compare(end, ">", f"{row}.end"):
                    end = t.index(f"{row}.end")
                if t.compare(start, "<", visible_start):
                    start = visible_start
                while t.compare(start, "<", end):
                    display, box = t.dlineinfo(start), t.bbox(start)
                    if not (display and box) or display[1] >= content_height:
                        break
                    bottom = display[1] + display[3]
                    stop = t.index(f"@{width},{max(0, display[1])}")
                    if t.compare(end, ">", stop):
                        right = right_limit  # 选区接着软换行：Tk 把底色画到右内边距，拖拽区盖回
                    else:
                        last = t.bbox(f"{end}-1c")
                        right = min(last[0] + last[2], right_limit) if last else right_limit
                    if right > box[0]:
                        rects.append((box[0], display[1], right, bottom))
                    if bottom >= content_height:
                        break
                    next_start = t.index(f"@0,{bottom}")
                    if t.compare(next_start, "<=", start):
                        break
                    start = next_start
            elif not self.lines[row - 1]["text"] and "selection_fill" in t.tag_names(f"{row}.end"):
                # 空行标记：与 _cover_empty_row_tail 留出的一个空格宽一致。
                box, display = t.bbox(f"{row}.end"), t.dlineinfo(f"{row}.end")
                if box and display:
                    rects.append((box[0], display[1], box[0] + max(1, self.app.base_font.measure(" ")),
                                  display[1] + display[3]))
        return rects

    def _layout_selection_corners(self, first_row, last_row, content_height):
        """选区轮廓的每个转角都放圆角片（凸角和凹角），见 selection_corner_pieces。"""
        used = 0
        if self.text.tag_ranges("selection_fill"):
            rects = self._selection_rects(first_row, last_row, content_height)
            for piece in selection_corner_pieces(rects, self._selection_corner_radius()):
                x, y, size, top, left, concave = piece
                corner = self._selection_corner(used)
                used += 1
                corner._corner_piece = piece
                image = self._selection_corner_image(size, top, left, concave)
                if str(corner.cget("image")) != str(image):
                    corner.configure(image=image)
                bounds = (x, y, size)
                if getattr(corner, "_layout_bounds", None) != bounds:
                    corner.place(x=x, y=y, width=size, height=size, bordermode="ignore")
                    corner._layout_bounds = bounds
                if concave and self._selection_masks:
                    corner.lift()  # 本帧刚抬起的遮罩不能盖住凹角圆弧
        # 用不上的角片藏起来留作备用：销毁控件比隐藏慢得多，会拖慢删除选区后的第一帧。
        for corner in self._selection_corners[used:]:
            corner.place_forget()
            corner._layout_bounds = None
            self._selection_corner_spares.append(corner)
        del self._selection_corners[used:]

    def _layout_text(self):
        # 全文重加 tag 会让 Tk 重测每一行；收起动画逐帧布局时正文结构不变，render 已加过。
        if self._removal is None:
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
        self._content_height = max(self.body.winfo_height(), self._document_height + 2 * pad +
                                   (self._removal["extra"] if self._removal and self._removal["below"]
                                    else 0))  # 删掉末尾的行时，空出的高度逐帧收回
        # Windows 子窗口坐标有范围限制；正文保留数屏缓冲，跨段时整组一起切换。
        if self.win._resizing:
            # 拉伸时每帧都要重排、重画整个 Text；只保留可见区加上下各一行余量，
            # 帧耗时不再随缓冲区和正文长度增长。松手后恢复数屏缓冲。
            height = min(self._content_height, self.body.winfo_height() + 2 * self._buffer_margin)
        else:
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
        if round(self.text.yview()[0] * self._document_height) != target:
            self.text.yview_moveto(target / self._document_height)
        self._chunk_start = round(self.text.yview()[0] * self._document_height)
        text_y = self._chunk_start - top
        if self.text.winfo_y() != text_y:
            self.text.place_configure(y=text_y)
        # 和正文一起提交装饰布局，避免重绘先露出软换行选区左侧的底色。
        used_masks = set()
        used_boxes = used_bars = 0
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
                if self._removal and not self._removal["below"] and self._removal["row"] == row:
                    # 收起中的行上方多出的间距不属于这一行，勾选框跟着文字走。
                    row_top += self._removal["extra"]
                    row_height -= self._removal["extra"]
                # Tk 会把软换行的选区背景画进左边距，盖回勾选框旁的留白。
                # 遮罩只需盖住 Text 自己画的底色，压在最底层：拖选时每次重排，勾选框都不会被它盖白一帧。
                if self.text.tag_nextrange("selection_fill", f"{row}.0", f"{row}.end"):
                    top, bottom = max(0, row_top), min(content_height, row_top + row_height)
                    if top is not None:
                        mask = self._selection_mask(("margin", row), "")
                        # 原生边距先画底色，再画光标。子窗口给光标左半边留出空间。
                        mask_width = int(self.text.tag_cget("todo_indent", "lmargin2")) - 2 * self.INSERT_HALF_WIDTH
                        mask.place(x=self.BODY_PAD_X, y=top,
                                   width=mask_width, height=bottom - top, bordermode="ignore")
                        mask.lower()
                        used_masks.add(mask)
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
                    bounds = (self.BODY_PAD_X + self._check_margin + self._check_margin // 2,
                              y, size, size)
                    if getattr(box, "_layout_bounds", None) != bounds:
                        box.place(x=bounds[0], y=y, width=size, height=size, bordermode="ignore")
                        box._layout_bounds = bounds
                        box.lift()
                    if getattr(box, "_todo_row", None) != row:
                        box._todo_row = row
                        box.bind("<Button-1>", lambda _e, r=row-1: self.toggle_done(r))
            if not line["text"] and "selection_fill" in self.text.tag_names(f"{row}.end"):
                mask = self._cover_empty_row_tail(row)
                if mask is not None:
                    used_masks.add(mask)
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
                last_index = f"{end}-1c" if self.text.compare(stop, ">=", end) else stop
                last = self.text.bbox(last_index)
                if last:
                    # 软换行处 Tk 会把该显示行末字的 bbox 延伸到右边缘，按字形实际宽度截回。
                    last = (last[0], last[1],
                            min(last[2], self.done_font.measure(self.text.get(last_index))), last[3])
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

        self._layout_selection_corners(first_row, last_row, content_height)
        # 这次没排上的控件回收掉，池子始终只留当前需要的。
        for mask in self._selection_masks:
            if mask not in used_masks:
                mask.destroy()
        self._selection_masks[:] = [mask for mask in self._selection_masks if mask in used_masks]
        for pool, used in ((self._checkbox_widgets, used_boxes),
                           (self._strike_widgets, used_bars)):
            for widget in pool[used:]:
                widget.destroy()
            del pool[used:]

        if self._selection_masks:
            # 凹角圆弧会落在留白和空行尾部遮罩上，排在最上层。留白遮罩已压在底层，这里不抬，免得盖住勾选框。
            self.win.after_idle(lambda: [w.lift() for w in [m for m in self._selection_masks
                                                            if m._mask_key[0] != "margin"]
                                         + self._checkbox_widgets + self._selection_corners if w.winfo_exists()])
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
        if (_event is not None and _event.type == tk.EventType.KeyPress and
                "drag_margin" not in t.tag_names("1.0")):
            # Tk 在文档开头插字时不继承标签，等松键再补会先按裁切边界画一帧。
            t.tag_add("drag_margin", "1.0", "2.0")
        index = t.index("insert")
        caret = self._todo_caret_index(index)
        if caret != index:
            t.mark_set("insert", caret)

    def _todo_caret_index(self, index):
        start = self._text_body(int(index.split(".")[0]))
        return start if self.text.compare(index, "<", start) else index

    def _text_body(self, row):
        # 按文本中的勾选框图片判断，连续按键时行数据可能还没同步。
        t = self.text
        start = f"{row}.0"
        if (t.dump(start, f"{start}+1c", image=True) and
                t.image_cget(start, "image") in {str(i) for i in self._check_slots.values()}):
            return t.index(f"{row}.{1 + len(PREFIX_PAD)}")
        return start

    def _reaches_note_end(self, first, last):
        """选区从首行开头一直选到文末（如全选），最后一行是空行时也算选中。"""
        t = self.text
        return t.compare(last, "==", "end-1c") and t.compare(first, "<=", self._text_body(1))

    def _trimmed_range(self):
        """选区收到高亮内容后的 (first, last)，不改动选区；勾选框和两端的换行属于行结构。
        空行要连同换行符一起选中才算。没有可删的内容时返回 None。"""
        t = self.text
        first, last = t.index("sel.first"), t.index("sel.last")
        first_row, last_row = int(first.split(".")[0]), int(last.split(".")[0])
        if last_row > first_row:
            body = self._text_body(last_row)
            if t.compare(body, "<", f"{last_row}.end"):
                excluded = t.compare(last, "<=", body)  # 末行一个字都没选中
            else:
                # 只停在空行开头、没选到它的换行就不算；从首行选到文末（如全选）时包括最后的空行。
                excluded = (t.compare(last, "==", f"{last_row}.0") and
                            not self._reaches_note_end(first, last))
            if excluded:
                last_row -= 1
                last = t.index(f"{last_row}.end")
        body = self._text_body(last_row)
        if t.compare(last, "<", body):
            last = body
        body = self._text_body(first_row)
        if t.compare(first, "<", body):
            first = body
        if (first_row < last_row and t.compare(body, "<", first) and
                t.compare(first, "==", f"{first_row}.end")):
            first = self._text_body(first_row + 1)  # 起点在有字行的行尾：这行没有被高亮
        if t.compare(first, ">=", last):
            return None
        return first, last

    def _whole_rows(self):
        """高亮覆盖两行以上的完整内容时返回这些行 (top, bottom)，行号从 1 起；否则返回 None。"""
        t = self.text
        span = t.tag_ranges("sel") and self._trimmed_range()
        if not span:
            return None
        first, last = span
        top, bottom = int(first.split(".")[0]), int(last.split(".")[0])
        if (bottom > top and t.compare(first, "<=", self._text_body(top)) and
                t.compare(last, ">=", f"{bottom}.end")):
            return top, bottom
        return None

    def _trim_selection(self):
        """删除、输入、剪贴前把选区收到高亮的文字上，与 _paint_selection 的高亮范围一致；
        选区里没有可删的内容时取消选区并返回 False。"""
        t = self.text
        if not t.tag_ranges("sel"):
            return True
        span = self._trimmed_range()
        if not span:
            t.tag_remove("sel", "1.0", "end")
            return False
        first, last = span
        if (first, last) != (t.index("sel.first"), t.index("sel.last")):
            t.tag_remove("sel", "1.0", "end")
            t.tag_add("sel", first, last)
        if not (t.compare(first, "<=", "insert") and t.compare("insert", "<=", last)):
            t.mark_set("insert", last)  # Tk 只在光标位于选区内时删除整个选区
        return True

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
        self._finish_removal()  # 重建正文前结束上一段收起，旧行号已不再对应
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
        self._begin_removal()  # 在首次重绘前补上被删行的高度，下面的行不会先跳上去
        self._queue_strikes()
        self.body.update_idletasks()
        restored = self.text.dlineinfo(view)
        if top and restored:
            self._scroll_to(scroll_y + restored[1] + self._chunk_start - top[1] - old_chunk)
        self._remember_edit()

    # ---- 删除整行的收起动画 ----
    # 被删的勾选框随空位一起压扁、变淡，下面的行顶上来时正好消失，中间不留空格。
    ROW_REMOVAL_DURATION = 0.15

    def _ypixels(self, start, end):
        return self.text.count(start, end, "update", "ypixels") or 0

    def _snapshot_removal(self, top, bottom, survivors):
        """删除前记下旧的第 top..bottom 行（从 1 起），删除后前 survivors 行仍在。
        其余行的勾选框淡出，下面的行从原位置滑上来。"""
        self._pending_removal = None
        if not NoteVisibilityMotion.enabled():
            return
        t = self.text
        pad = int(t.cget("pady"))
        size = self._check_images[False].width()
        checked = str(self._check_images[True])
        ghosts = [(box._layout_bounds[0] + size / 2, box._layout_bounds[1] + size / 2,
                   str(box.cget("image")) == checked)
                  for box in self._checkbox_widgets
                  if box.winfo_ismapped() and getattr(box, "_layout_bounds", None) and
                  top + survivors <= getattr(box, "_todo_row", 0) <= bottom]
        self._pending_removal = {
            "top": top, "survivors": survivors, "ghosts": ghosts,
            "height": self._ypixels(f"{top}.0", f"{bottom + 1}.0"),
            "next_top": pad + self._ypixels("1.0", f"{bottom + 1}.0") - self._chunk_start}

    def _begin_removal(self):
        snap, self._pending_removal = self._pending_removal, None
        if snap is None:
            return
        t = self.text
        row = snap["top"] + snap["survivors"]
        # 删的是末尾几行时下面没有行托住空位，空出的高度暂时留在正文末尾之下。
        below = row > int(t.index("end-1c").split(".")[0])
        kept = (self._ypixels(f"{snap['top']}.0", "end" if below else f"{row}.0")
                if snap["survivors"] else 0)
        gap = max(0, snap["height"] - kept)
        if not gap and not snap["ghosts"]:
            return
        if gap > self.body.winfo_height():
            return  # 一次删掉超过一屏：滑动整屏只会闪一下，按原来的方式直接删除
        full = self._check_images[False].width()
        if getattr(self, "_ghost_sources", {}).get("size") != full:
            # 首次绘制前备好原图，第一帧不再多花时间画图。
            self._ghost_sources = {"size": full, True: check_source(full, True),
                                   False: check_source(full, False)}
        ghosts = []
        for _ in snap["ghosts"]:
            ghost = tk.Label(t, bg=self._bg, bd=0, highlightthickness=0)
            ghost.bind("<MouseWheel>", self._text_wheel)
            ghosts.append(ghost)
        # 计时从第一帧开始：此前补齐高度的画面已经画出，重排耗时不会吃掉开头几帧。
        # 删除时要求的滚动（保持视图、露出光标、到底回弹）先记下，由各帧和空位一起完成。
        self._removal = dict(snap, row=row, gap=gap, below=below, extra=0, tag=None,
                             labels=ghosts, began=None, job=None,
                             top_start=round(self.body.canvasy(0)), scroll_request=None,
                             scroll_target=None)
        self._set_removal_spacing(gap)
        self._place_removal_ghosts(1.0)
        self._removal["job"] = self.win.after(VISIBILITY_TICK_MS, self._removal_frame)

    def _set_removal_spacing(self, extra):
        """只在收起行的第一个字符上换 tag。每个间距值的 tag 只配置一次：
        重配已用过的 tag、整段 tag_remove 或 tag_delete 都会让 Tk 重测全文。"""
        removal, t = self._removal, self.text
        removal["extra"] = extra
        if removal["below"]:
            return  # 末尾的空位由 _layout_text 加在滚动区域里
        start = f"{removal['row']}.0"
        if removal["tag"]:
            t.tag_remove(removal["tag"], start, f"{start}+1c")
            removal["tag"] = None
        if extra:
            spacing = int(t.cget("spacing1")) + extra
            name = f"row_removal_{spacing}"
            if name not in self._removal_tags:
                t.tag_configure(name, spacing1=spacing)
                self._removal_tags.add(name)
            t.tag_add(name, start, f"{start}+1c")
            removal["tag"] = name

    def _removal_frame(self):
        removal = self._removal
        if removal is None or not self.text.winfo_exists():
            return
        removal["job"] = None
        now = time.perf_counter()
        if removal["began"] is None:
            removal["began"] = now - VISIBILITY_TICK_MS / 1000
        if removal["scroll_target"] is None:
            # 删除后的最终滚动位置：按要求的位置，再按收起后的正文高度回弹到底。
            pad, view = int(self.text.cget("pady")), self.body.winfo_height()
            document = self._document_height or self._ypixels("1.0", "end")
            final = max(view, document - (0 if removal["below"] else removal["extra"]) + 2 * pad)
            request = removal["scroll_request"]
            removal["scroll_target"] = max(0, min(removal["top_start"] if request is None else request,
                                                  final - view))
        progress = min(1.0, (now - removal["began"]) / self.ROW_REMOVAL_DURATION)
        if progress >= 1:
            self._finish_removal(settle=True)
            return
        left = (1 - progress) ** 2  # 先快后慢，首帧位移比三次方平缓
        start, target = removal["top_start"], removal["scroll_target"]
        if removal["gap"]:
            self._set_removal_spacing(round(removal["gap"] * left))
        if start != target:
            # 滚动与空位同步收拢：中间删除时下面的行上移；滚到底时下面不动、上面下移。
            self.body.yview_moveto((target + (start - target) * left) / self._content_height)
        if removal["gap"]:
            self._queue_strikes()
            self._draw_strikes()  # 勾选框和删除线与正文同一帧移动
        self._place_removal_ghosts(left)
        removal["job"] = self.win.after(VISIBILITY_TICK_MS, self._removal_frame)

    def _place_removal_ghosts(self, left):
        """被删的勾选框随空位一起压扁、变淡，下面的行顶上来时正好消失。"""
        removal = self._removal
        full = self._check_images[False].width()
        size = round(full * left)
        if removal["gap"]:
            pad = int(self.text.cget("pady"))
            # 空位此刻的下沿；旧位置到它的距离按剩余高度等比收拢。
            edge = "end" if removal["below"] else f"{removal['row']}.0"
            bottom = pad + self._ypixels("1.0", edge) - self._chunk_start + removal["extra"]
        for label, (x, y, checked) in zip(removal["labels"], removal["ghosts"]):
            if size < 2:
                label.place_forget()
                continue
            if removal["gap"]:
                y = bottom - (removal["next_top"] - y) * left
            image = self._ghost_sources[checked].resize((size, size), Image.Resampling.LANCZOS)
            image.putalpha(image.getchannel("A").point(lambda a: round(a * left)))
            label._image = ImageTk.PhotoImage(image, master=self.text)
            label.configure(image=label._image)
            label.place(x=round(x - size / 2), y=round(y - size / 2), width=size, height=size,
                        bordermode="ignore")
            label.lower()

    def _finish_removal(self, settle=False):
        removal, self._removal = self._removal, None
        if removal is None:
            return
        if removal["job"] is not None:
            self.win.after_cancel(removal["job"])
        for label in removal["labels"]:
            label.destroy()
        if self.text.winfo_exists():
            if removal["tag"]:
                # 按 tag 实际所在的范围移除，不做整段 tag_remove。
                ranges = self.text.tag_ranges(removal["tag"])
                for start, end in zip(ranges[::2], ranges[1::2]):
                    self.text.tag_remove(removal["tag"], start, end)
            if settle and removal["scroll_target"] is not None:
                self.body.yview_moveto(removal["scroll_target"] / self._content_height)
            elif removal["scroll_request"] is not None:
                self._scroll_to(removal["scroll_request"])  # 第一帧前就被打断：补上删除时要求的滚动
            if removal["gap"]:
                self._queue_strikes()

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
        rows = self._whole_rows() if command == "tk_textCut" else None  # 收拢前判断，见 _local_shortcut
        self._trim_selection()
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
            if rows:  # 剪切整行和删除整行一样，不留空行
                return self.delete_rows(*rows)
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
        selected = t.tag_ranges("sel")
        ranges, markers = [], []
        if selected:
            first, last = selected
            first_row, last_row = int(str(first).split(".")[0]), int(str(last).split(".")[0])
            for row in range(first_row, last_row + 1):
                start = first if t.compare(first, ">", f"{row}.0") else f"{row}.0"
                if row <= len(self.lines) and self.lines[row-1]["type"] == "todo":
                    text_start = f"{row}.{self._prefix_len(self.lines[row-1])}"
                    if t.compare(start, "<", text_start):
                        start = text_start
                end = last if t.compare(last, "<", f"{row}.end") else f"{row}.end"
                if t.compare(start, "<", end):
                    ranges.append((t.index(start), t.index(end)))
                elif row <= len(self.lines) and not self.lines[row-1]["text"] and (
                        row < last_row or (t.compare(last, ">", f"{row}.0") and row > first_row)
                        or (row > first_row and self._reaches_note_end(first, last))):
                    # 空行没有文字可画：只给被选中的空行补一个换行符大小的标记，其余行不变。
                    markers.append(row)
        # 新标记的空行先盖好尾部遮罩再打标记，Text 的重画排在遮罩之后，尾部不会先整行变蓝。
        # 先把待处理的重画按旧状态画完：旧遮罩是齐的，透明的 sel 也不显色。
        keys = {mask._mask_key for mask in self._selection_masks}
        missing = [row for row in markers if ("tail", row) not in keys]
        if missing:
            t.update_idletasks()
            for row in missing:
                self._cover_empty_row_tail(row)
        t.tag_remove("selection_fill", "1.0", "end")
        self._queue_strikes()
        for start, end in ranges:
            t.tag_add("selection_fill", start, end)
        for row in markers:
            t.tag_add("selection_fill", f"{row}.end", f"{row}.end+1c")

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
            if not self.win.d.get("title"):
                self.app.request_dock_refresh()
        structure_changed = (len(old) != len(lines) or
                             any(a["type"] != b["type"] for a, b in zip(old, lines)))
        if not (structure_changed and len(lines) < len(old)):
            self._pending_removal = None  # 选区删除最终没有减少行数，不播放收起
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
        at_line_start = col <= self._prefix_len(ln)
        if at_line_start and row == 0 and not ln["text"] and len(lines) > 1:
            self._remember_edit()
            self.delete_line(row)
            return "break"
        if at_line_start and row > 0:
            prev = lines[row - 1]
            target = self.text.index(f"{row}.end")
            self._remember_edit()
            if prev["type"] == ln["type"] == "todo" and not prev["text"] and ln["text"]:
                self._snapshot_removal(row, row, 0)  # 空的上一行收起，当前行滑上去
                lines[row - 1] = ln
            else:
                self._snapshot_removal(row, row + 1, 1)  # 当前行并入上一行
                prev["text"] += ln["text"]
            del lines[row]
            self.render(keep_cursor=False)
            self.text.mark_set("insert", target)
            self._see_text("insert")
            self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
            self.app.schedule_save()
            return "break"
        if ln["type"] == "todo" and at_line_start:
            # 第一行没有可合并的上一行，退格去掉勾选框、保留正文，与右键「转为文字」一致。
            self._remember_edit()
            self.convert_rows([0], "text")
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
        # 会替换选区的按键先收拢选区，勾选框和行尾换行不随选区删除。
        deleting = event.keysym in ("BackSpace", "Delete")
        typing = bool(event.char) and (event.char == "\t" or event.char.isprintable()) and (
            not event.state & 4 or bool(event.state & 0x20000))  # AltGr 在 Windows 上是 Ctrl+Alt
        if (event.type == tk.EventType.KeyPress and self.text.tag_ranges("sel") and
                (deleting or typing or event.keysym in ("Return", "KP_Enter")) and
                not self.win._ime_composing(self.text)):
            # 收拢会把空的末行写成它的行首，先按原选区判断是否整行选中。
            rows = self._whole_rows() if deleting else None
            if not self._trim_selection():
                if deleting:
                    return "break"  # 选区里只有勾选框或换行，没有可删的文字
            elif deleting:
                if rows:  # 两行以上整行选中：按行删除，不留空行
                    return self.delete_rows(*rows)
                first, last = (int(str(i).split(".")[0]) for i in self.text.tag_ranges("sel"))
                if last > first:  # 首行保留，尾行剩下的文字接上去，中间各行收起
                    self._snapshot_removal(first, last, 1)

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
        # 高亮覆盖两行以上的完整内容时叫「删除 n 行」，否则叫「删除选中内容」；删法都和删除键一样。
        whole = self._whole_rows()
        delete_label = f"删除 {whole[1] - whole[0] + 1} 行" if whole else "删除选中内容"
        actions = [
            (label, lambda: self.convert_rows(rows, target)),
            ("在下方插入新行", lambda: self.insert_line(row)),
            (delete_label, self.delete_selection) if self.text.tag_ranges("sel")
            else ("删除此行", lambda: self.delete_line(row)),
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
            self._snapshot_removal(row + 1, row + 1, 0)
            del self.lines[row]
        self.render(keep_cursor=False)
        row = min(row, len(self.lines) - 1)
        self.text.mark_set("insert", f"{row+1}.{self._prefix_len(self.lines[row])}")
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()

    def delete_rows(self, top, bottom):
        """删除选中的整行（行号从 1 起），待办和文字一样整行删掉；整篇删光时留一行，含有待办时留空待办。"""
        self._sync_lines_from_text()
        lines = self.lines
        removed = lines[top - 1:bottom]
        if len(removed) == len(lines):
            todo = any(line["type"] == "todo" for line in removed)
            keep = [dict(removed[0], type="todo" if todo else "text", text="", checked=False)]
        else:
            keep = []
        self._snapshot_removal(top, bottom, len(keep))
        lines[top - 1:bottom] = keep
        self.render(keep_cursor=False)
        if not keep and top > 1:
            self.text.mark_set("insert", f"{top - 1}.end")  # 整行删掉后光标回到上一行行尾
        else:
            row = min(top - 1, len(self.lines) - 1)
            self.text.mark_set("insert", f"{row+1}.{self._prefix_len(self.lines[row])}")
        self._see_text("insert")
        self.d["editedAt"] = datetime.now().isoformat(timespec="microseconds")
        self.app.schedule_save()
        return "break"

    def delete_selection(self):
        """右键「删除选中内容」：重放 Delete 键，和按键删选区走同一条流程。"""
        self.text.focus_force()  # 菜单关掉后焦点可能不在正文上，按键事件要发给有焦点的正文
        self.text.event_generate("<KeyPress-Delete>")
        self.text.event_generate("<KeyRelease-Delete>")


class NoteWindow(tk.Toplevel):
    def __init__(self, app, data, *, start_transparent=False):
        super().__init__(app.root)
        self.app, self.d = app, data
        self._temporarily_raised = False
        self._bg, self._border = NOTE_BG, NOTE_BORDER
        self.d["color"] = "白"
        self.configure(bg=self._bg)
        self.geometry(f"{data['w']}x{data['h']}+{data['x']}+{data['y']}")
        self.attributes("-alpha", 0.0 if start_transparent else data["opacity"])
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
        btn("minus", lambda: "隐藏这张便签",
            lambda: self.app.animate_note_visibility(self, False)).pack(side="right", padx=(2, 8), pady=9)
        btn("plus", lambda: f"新建便签（{self.app.hotkey_text('new')}）",
            lambda: self.app.new_note(animate=True)).pack(side="right", padx=5, pady=9)
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
        # Tk 把行尾右半边的空白算成下一行开头，点击和拖选会落到下一行；便签正文停在本行行尾。
        if not self.tk.call("info", "procs", "::tk::NoteTextClosestGap"):
            self.tk.eval("""
                rename ::tk::TextClosestGap ::tk::NoteTextClosestGap
                proc ::tk::TextClosestGap {w x y} {
                    set gap [::tk::NoteTextClosestGap $w $x $y]
                    if {"TodoCaret" in [bindtags $w]} {
                        set pos [$w index @$x,$y]
                        if {[$w compare $gap > $pos] && [$w compare $pos == "$pos lineend"]} {
                            return $pos
                        }
                    }
                    return $gap
                }
            """)
            # Tk 按词选择时把换行也当空白，双击空行会一路选到上下的文字；便签正文改为只在被点的那一条里取词。
            root = self._root()

            def note_editor(path):
                widget = root.nametowidget(path)
                return widget.winfo_toplevel()._editor_for(widget)

            select_to = root.register(lambda path, x, y, mode: note_editor(path)._select_to(int(x), int(y), mode))
            key_select = root.register(lambda path, index: note_editor(path)._key_select(index))
            self.tk.eval(f"""
                rename ::tk::TextSelectTo ::tk::NoteTextSelectTo
                proc ::tk::TextSelectTo {{w x y args}} {{
                    if {{"TodoCaret" in [bindtags $w] && [info exists ::tk::Priv(selectMode)]
                            && [{select_to} $w $x $y $::tk::Priv(selectMode)]}} {{
                        return
                    }}
                    ::tk::NoteTextSelectTo $w $x $y {{*}}$args
                }}
                rename ::tk::TextKeySelect ::tk::NoteTextKeySelect
                proc ::tk::TextKeySelect {{w index}} {{
                    if {{"TodoCaret" in [bindtags $w] && [{key_select} $w $index]}} {{
                        return
                    }}
                    ::tk::NoteTextKeySelect $w $index
                }}
            """)

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
        editor._clamp_todo_cursor(_event)

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
                    # 与分页线两端对齐（分页线绘制在 y+13 至 y+height-13），滑块不越过分隔线。
                    editor.scroll_thumb.place(x=x + col_w - 10, y=text_y + 13,
                                              width=8, height=max(12, height - 26))
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
        self.app._note_motion.finish(self, apply_target=False)
        self._cancel_dock_reveal()
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
        preference = ctypes.c_int(NOTE_CORNER_PREFERENCE)
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
            editor._trim_selection()
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
            tags = ["ime_preedit", "drag_margin"]
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
                               outline="#5B6472", width=round(2.5 * scale))
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
            self.app.request_dock_refresh()
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
        origin = self.app._note_motion.finish(self)
        if origin is not None and not self.d.get("visible", True):
            return
        for editor in self.editors:
            editor._cancel_row_drag()
        expanded_geometry = None
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
            x, y = origin if origin is not None else (self.winfo_x(), self.winfo_y())
            self.geometry(f"{self.d['w']}x{self.winfo_height()}+{x}+{y}")
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
                x, y = origin if origin is not None else (self.winfo_x(), self.winfo_y())
                self.d["w"] = new_w
                self.d["x"] = min(x, max(0, sw - new_w))
                expanded_geometry = f"{new_w}x{self.winfo_height()}+{self.d['x']}+{y}"
        preview = None
        if expanded_geometry and self.winfo_viewable() and self._resize_surface is None:
            # 扩宽时先保留当前画面，等 Tk 子窗口完成布局和绘制再交还。
            rect = wintypes.RECT()
            if user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
                try:
                    preview = ResizeSurface(self, self._resize_client_hwnd, (
                        rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
                        restore_bottom=not self._temporarily_raised)
                except OSError:
                    pass  # 预览面不可用时仍允许切换栏数。
        try:
            if expanded_geometry:
                self.geometry(expanded_geometry)
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
            if preview:
                self.update_idletasks()
                dwmapi.DwmFlush()
        finally:
            if preview:
                preview.close()
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
        c._line_image = None
        h = c.winfo_height()
        if h <= 48:
            return
        # 先放大绘制圆头，缩回后保留边缘的半透明像素。
        scale = 4
        width, height = 6, h - 26
        image = Image.new("RGBA", (width * scale, height * scale))
        ImageDraw.Draw(image).rounded_rectangle(
            (scale, scale, 5 * scale - 1, (height - 1) * scale - 1),
            radius=2 * scale, fill="#C9CED5")
        c._line_image = ImageTk.PhotoImage(
            image.resize((width, height), Image.Resampling.LANCZOS), master=c)
        c.create_image(4, 13, anchor="nw", image=c._line_image)

    # ---- 编辑事件 ----
    def _show_context_menu(self, x, y, actions, shortcuts=None, compact=False, on_right_click=None):
        shortcuts = shortcuts or {}
        old = getattr(self, "_context_menu", None)
        if old and old.winfo_exists():
            old.destroy()
        p = tk.Toplevel(self)
        self._context_menu = p
        p.withdraw()
        p.overrideredirect(True)
        p.configure(bg=NOTE_BG)
        p.attributes("-topmost", True)
        _round_popup(p)
        font = tkfont.Font(family=UI_FONT, size=10)
        # compact：单项小菜单（收纳书签），去掉固定最小宽度并收紧行高与边距。
        min_width, min_row, side, bottom = (0, 0, 36, 8) if compact else (184, 34, 44, 9)
        width = max(min_width, max(font.measure(label) + (font.measure(shortcuts[i]) + 32 if i in shortcuts else 0)
                                   for i, (label, _) in enumerate(actions)) + side)
        row_h = max(min_row, font.metrics("linespace") + (8 if compact else 12))
        height = (8 if compact else 16) + row_h * len(actions) + bottom
        cv = tk.Canvas(p, width=width, height=height, bg=NOTE_BG,
                       highlightthickness=0, bd=0)
        cv.pack()
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
        if on_right_click is not None:
            # 菜单独占鼠标时，右键也要能关闭，并把点击交还给调用方处理。
            def right_click(e):
                close()
                on_right_click(e)
            p.bind("<Button-3>", right_click)
        if compact:
            # 小菜单以 (x, y) 为锚点：右缘贴在锚点左侧、垂直居中，不遮挡锚点处的书签。
            x, y = max(0, x - width), max(0, y - height // 2)
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
        origin = self.app._note_motion.finish(self)
        if origin is not None and not self.d.get("visible", True):
            return "break"
        self._edge_sizing = False
        self._dragging = True
        x, y = origin if origin is not None else (self.winfo_x(), self.winfo_y())
        self._dx, self._dy = e.x_root - x, e.y_root - y
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
        if self.app._note_motion.finish(self) is not None and not self.d.get("visible", True):
            return "break"
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
            if hidden or surface is None:
                self.attributes("-alpha", self.d["opacity"])
            if not hidden:
                self.update_idletasks()
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
                if not hidden and redraw and restored_geometry:
                    dwmapi.DwmFlush()  # Finish the preview frame immediately before the opacity swap.
                if not hidden and self.winfo_exists() and self.attributes("-alpha") != self.d["opacity"]:
                    self.attributes("-alpha", self.d["opacity"])
                surface.close()
            self._resize_surface = None
            self._resize_busy = self._resize_ending = self._resize_release_pending = self._resize_cancel_requested = False
        # 拉伸期间正文只保留一屏缓冲，结束后在空闲时恢复数屏缓冲，滚动仍然顺滑。
        for editor in self.editors:
            editor._queue_strikes(measure=False)
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
            if message == 0x0086:  # WM_NCACTIVATE
                # Match the preview's lighter frame while keeping client focus.
                return user32.DefWindowProcW(hwnd, message, 0, -1)
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
        user32.DefWindowProcW(self.hwnd, 0x0086, 0, -1)

    def set_z(self, state):
        self.app._note_motion.finish(self)
        self._cancel_dock_reveal()
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
        self.app._note_motion.finish(self, apply_target=False)
        self._cancel_dock_reveal()
        self._temporarily_raised = False
        self.d["visible"] = False
        if self._resizing:
            self._finish_resize(cancel=True, redraw=False, hidden=True)
        self.withdraw()
        self.app.schedule_save()
        self.app.request_dock_refresh()

    def show(self, temporary_raise=False):
        self.app._note_motion.finish(self, apply_target=False)
        self._cancel_dock_reveal()
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
        self.app.request_dock_refresh()

    def _cancel_dock_reveal(self):
        job = getattr(self, "_dock_reveal_job", None)
        if job is not None:
            self.after_cancel(job)
            self._dock_reveal_job = None
            self._temporarily_raised = False
            self.apply_z()

    def reveal_from_dock_mode(self):
        self.show(temporary_raise=True)
        self._temporarily_raised = True
        self.attributes("-topmost", True)
        self.lift()

        def restore():
            self._dock_reveal_job = None
            self._temporarily_raised = False
            self.apply_z()

        self._dock_reveal_job = self.after(1000, restore)

    def delete_self(self):
        self.app.remove_note(self)

# ---------- 设置窗口 ----------
def _show_after_layout(window):
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()

    def reveal():
        if window.winfo_exists():
            window.update_idletasks()
            if window.winfo_exists():
                PopupFade.show(window)

    def on_map(event):
        if event.widget == window:
            window.unbind("<Map>", map_binding)
            window.master.after_idle(reveal)

    # 首次映射会触发布局，等正文挂载并绘制后再显示窗口。
    map_binding = window.bind("<Map>", on_map, add="+")
    preference = ctypes.c_int(2)
    dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
    window.attributes("-alpha", 0.0)
    window._popup_alpha = 0.0
    # Tk 设置透明度时会重置扩展样式，因此随后再设置任务栏样式。
    style = user32.GetWindowLongPtrW(hwnd, -20)
    user32.SetWindowLongPtrW(hwnd, -20, style & ~0x80 | 0x40000)  # WS_EX_TOOLWINDOW / WS_EX_APPWINDOW
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | 0x04 | 0x20)
    window.deiconify()


class SmoothWheel:
    """设置和回收站列表的滚轮，手感与便签正文一致：每格 27pt，按经过时间平滑逼近。"""

    def __init__(self, canvas):
        self.canvas = canvas
        canvas.configure(yscrollincrement=1)  # “units” 以像素为单位，才能逐像素平滑移动。
        self._remaining = 0.0
        self._time = 0.0
        self._job = None

    def wheel(self, delta):
        first, last = self.canvas.yview()
        if last - first >= .999:
            self.stop()
            return
        distance = -delta / 120 * self.canvas.winfo_fpixels("27p")
        if distance * self._remaining < 0:
            self._remaining = 0.0  # 反向滚动时丢弃尚未走完的旧方向。
        self._remaining += distance
        if self._job is None:
            self._time = time.monotonic() - .016
            self._step()

    def _step(self):
        self._job = None
        remaining = self._remaining
        first, last = self.canvas.yview()
        if abs(remaining) < 1 or (remaining < 0 and first <= 0) or (remaining > 0 and last >= 1):
            if abs(remaining) >= 1:
                self._remaining = 0.0  # 到顶或到底后不再积攒。
            return
        now = time.monotonic()
        progress = -math.expm1(-(now - self._time) / .04)
        self._time = now
        step = min(int(abs(remaining)), max(1, round(abs(remaining) * progress)))
        step *= 1 if remaining > 0 else -1
        self.canvas.yview_scroll(step, "units")
        self._remaining -= step
        self._job = self.canvas.after(16, self._step)

    def stop(self):
        if self._job is not None:
            self.canvas.after_cancel(self._job)
            self._job = None
        self._remaining = 0.0


class SettingsScale(tk.Canvas):
    TRACK_INSET = 12

    def __init__(self, master, minimum, maximum, step, command, *, reverse=False):
        super().__init__(master, width=180, height=36, bg=NOTE_BG,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.minimum, self.maximum, self.step = minimum, maximum, step
        self.reverse = reverse
        self.command, self.value = command, minimum
        self._pressed = False
        self.bind("<Button-1>", self._press)
        # 只跟随在滑块上按下开始的拖动；字体弹窗等在按下时关闭，余下的拖动不能改到滑块。
        self.bind("<B1-Motion>", lambda e: self._pick(e) if self._pressed else "break")
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_pressed", False))
        direction = -1 if reverse else 1
        self.bind("<Left>", lambda _e: self.set(self.value - direction * self.step))
        self.bind("<Right>", lambda _e: self.set(self.value + direction * self.step))
        self.bind("<Configure>", lambda _e: self._draw())
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

    def _press(self, event):
        self._pressed = True
        return self._pick(event)

    def _pick(self, event):
        self.focus_set()
        span = max(1, self.winfo_width() - 2 * self.TRACK_INSET)
        fraction = (event.x - self.TRACK_INSET) / span
        self.set(self.minimum + (1 - fraction if self.reverse else fraction)
                 * (self.maximum - self.minimum))
        return "break"

    def _draw(self):
        # 超采样绘制刻度和握柄，保持圆角与线条平滑。
        scale = 4
        width = max(self.winfo_width(), 2 * self.TRACK_INSET + 1)
        height = max(self.winfo_height(), 36)
        left, right, middle = self.TRACK_INSET, width - self.TRACK_INSET, height / 2
        img = Image.new("RGBA", (width * scale, height * scale), NOTE_BG)
        draw = ImageDraw.Draw(img)
        def box(bounds, radius, **kw):
            draw.rounded_rectangle(tuple(int(v * scale) for v in bounds),
                                   radius=radius * scale, **kw)
        fraction = (self.value - self.minimum) / (self.maximum - self.minimum)
        x = left + (right - left) * (1 - fraction if self.reverse else fraction)
        box((left, middle - 3, right, middle + 3), 3, fill="#E9EDF1")
        if x > left:
            box((left, middle - 3, x, middle + 3), 3, fill="#7D8C9D")
        count = (self.maximum - self.minimum) // self.step
        for index in range(count + 1):
            tick_x = left + (right - left) * index / count
            draw.line((tick_x * scale, (middle + 12) * scale, tick_x * scale, (middle + 15) * scale),
                      fill="#8E9CAC" if tick_x <= x else "#D8DEE5",
                      width=scale)
        # 纯白圆形握柄，仍按刻度一格一格吸附。
        box((x - 9, middle - 8, x + 9, middle + 10), 9, fill="#E6EAF0")
        box((x - 9, middle - 9, x + 9, middle + 9), 9, fill="#FFFFFF",
            outline="#64748B" if self.focus_get() == self else "#9CA9B8", width=scale)
        self._image = ImageTk.PhotoImage(img.resize((width, height), Image.Resampling.LANCZOS), master=self)
        self.delete("all")
        self.create_image(0, 0, image=self._image, anchor="nw")


class FontSelector(tk.Canvas):
    """Rounded font field and menu rendered in the current interface font."""

    def __init__(self, parent, variable, command):
        self.variable, self.command = variable, command
        self._popup = None
        self._font = tkfont.Font(root=parent, family=UI_FONT, size=10)
        super().__init__(parent, bg=NOTE_BG, highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        self._fit()
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())
        self.bind("<Button-1>", self._open)
        for key in ("<Return>", "<space>", "<Down>", "<Up>"):
            self.bind(key, self._open)
        self.bind("<Destroy>", self._destroy)

    def _fit(self):
        """按当前字体量出能放下所有选项的尺寸；切换字体后重新调用。"""
        self.configure(width=max(self._font.measure(label) for label in FONT_CHOICES.values()) + 44,
                       height=self._font.metrics("linespace") + 16)

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
        label = self.variable.get()
        if self._font.measure(label) > width - 42:
            while label and self._font.measure(label + "…") > width - 42:
                label = label[:-1]
            label = label + "…"
        self.create_text(12, height / 2, text=label, font=self._font,
                         fill="#2B2F36", anchor="w")
        self.create_line(width - 22, height / 2 - 2, width - 17, height / 2 + 3,
                         width - 12, height / 2 - 2, fill=INK, width=1.5)

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
        popup.configure(bg=NOTE_BG)
        popup.attributes("-topmost", True)
        _round_popup(popup)
        labels = list(FONT_CHOICES.values())
        work = SettingsWindow._work_area(self.winfo_toplevel())
        row_height = max(30, self._font.metrics("linespace") + 14)
        width = min(work.right - work.left, max(self.winfo_width(),
                    self.winfo_reqwidth()))
        height = 12 + row_height * len(labels)
        canvas = tk.Canvas(popup, width=width, height=height, bg=NOTE_BG,
                           highlightthickness=0, bd=0)
        canvas.pack()
        highlights = []
        current = labels.index(self.variable.get())
        for index, label in enumerate(labels):
            top = 6 + index * row_height
            highlights.append(round_rect(canvas, 5, top, width - 5, top + row_height, 7,
                                          fill="#F2F3F5", outline="", state="hidden"))
            middle = top + row_height / 2
            canvas.create_text(16, middle, text=label, font=self._font,
                               fill="#2B2F36", anchor="w")
            if index == current:
                canvas.create_line(width - 26, middle, width - 22, middle + 4,
                                   width - 15, middle - 4, fill=INK, width=2)
        selected = current

        def select(index):
            nonlocal selected
            canvas.itemconfigure(highlights[selected], state="hidden")
            selected = index
            canvas.itemconfigure(highlights[selected], state="normal")

        def activate(index):
            self._close()
            self.variable.set(labels[index])
            self.command(labels[index])
            self.focus_set()

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
                self.focus_set()
            return "break"

        canvas.bind("<Motion>", motion)
        popup.bind("<Button-1>", click)
        popup.bind("<KeyPress>", key)
        popup.bind("<FocusOut>", lambda _e: self._close())
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

        header = tk.Frame(self, bg=NOTE_BG, padx=4)
        title = tk.Label(header, text="设置", bg=NOTE_BG, fg="#1F2328",
                         font=(UI_FONT, 12, "bold"), cursor="fleur")
        title.pack(side="left")
        _hook_move_cursor(title)
        title.bind("<Enter>", _cursor_small)
        title.bind("<Leave>", _cursor_restore)
        close = tk.Label(header, text="×", bg=NOTE_BG, fg=INK,
                         font=(UI_FONT, 17), cursor="hand2")
        close.pack(side="right", padx=(8, 2))
        close.bind("<Button-1>", lambda _e: PopupFade.hide(self, self.destroy))
        minimize = tk.Label(header, text="−", bg=NOTE_BG, fg=INK,
                            font=(UI_FONT, 17), cursor="hand2")
        minimize.pack(side="right", padx=(8, 2))
        minimize.bind("<Button-1>", lambda _e: self._minimize())
        self.bind("<Escape>", lambda _e: PopupFade.hide(self, self.destroy))
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
                       command=self._toggle_startup_display, justify="left", anchor="w")
        startup.pack(anchor="w", pady=4)

        section("外观")
        appearance = []

        def appearance_row(text):
            row = tk.Frame(f, bg=NOTE_BG)
            row.pack(fill="x", pady=(0, 4))
            label = tk.Label(row, text=text, bg=NOTE_BG, fg="#2B2F36",
                             font=(UI_FONT, 10), anchor="w", justify="left")
            controls = tk.Frame(row, bg=NOTE_BG)
            appearance.append((row, label, controls))
            return controls

        font_row = appearance_row("字体")
        self.font_choice = tk.StringVar(master=self, value=FONT_CHOICES[app.data["settings"]["fontFamily"]])
        self.font_selector = FontSelector(font_row, self.font_choice, self._font_family)
        row = appearance_row("不透明度")
        opacity_value = tk.Label(row, bg=NOTE_BG, fg=QUIET,
                                 font=(UI_FONT, 9), anchor="e", padx=0, bd=0)
        def update_opacity(v):
            opacity_value.config(text=f"{100 - v}%")
            self._op(v)
        s = SettingsScale(row, 0, 50, 10, update_opacity, reverse=True)
        app.opacity_scale = s
        s.set(round((1 - app.data["settings"]["opacity"]) * 100))
        opacity_value.config(text=f"{100 - s.get()}%")
        if app.data["settings"]["opacity"] != (100 - s.get()) / 100:
            self._op(s.get())
        row3 = appearance_row("字号")
        font_value = tk.Label(row3, bg=NOTE_BG, fg=QUIET,
                              font=(UI_FONT, 9), anchor="e", padx=0, bd=0)
        def update_font(v):
            font_value.config(text=str(v))
            self._font(v)
        s2 = SettingsScale(row3, 10, 15, 1, update_font)
        s2.set(app.data["settings"]["fontSize"])
        font_value.config(text=str(s2.get()))
        if app.data["settings"]["fontSize"] != s2.get():
            self._font(s2.get())
        inset = SettingsScale.TRACK_INSET
        self.font_selector.grid(row=0, column=0, sticky="ew", padx=inset)
        for controls, scale_widget, value in ((row, s, opacity_value), (row3, s2, font_value)):
            scale_widget.grid(row=0, column=0, sticky="ew")
            value.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        def layout_appearance(content_width):
            value_width = int(self.tk.call("font", "measure", opacity_value.cget("font"), "100%")) + 8
            rail_width = max(180, self.font_selector.winfo_reqwidth() + 2 * inset)
            label_width = max(int(self.tk.call("font", "measure", label.cget("font"), label.cget("text")))
                              for _, label, _ in appearance)
            available = max(1, content_width - 8)
            desired = rail_width + value_width
            stacked = label_width + 16 + desired > available
            controls_width = min(available, desired)
            common_height = max(36, self.font_selector.winfo_reqheight())
            for appearance_line, label, controls in appearance:
                appearance_line.columnconfigure(0, weight=1)
                appearance_line.columnconfigure(1, minsize=0 if stacked else controls_width)
                label.configure(wraplength=available)
                label.grid(row=0, column=0, sticky="w", pady=(0, 4) if stacked else 0)
                controls.grid(row=1 if stacked else 0, column=0 if stacked else 1, sticky="e")
                controls.columnconfigure(0, minsize=max(1, controls_width - value_width), weight=1)
                controls.columnconfigure(1, minsize=value_width)
                controls.rowconfigure(0, minsize=common_height)
                controls.configure(width=controls_width, height=common_height)
                controls.grid_propagate(False)

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
            entry.bind("<FocusIn>", lambda _e, action=action:
                       self._show_hotkey_feedback("请按组合键，Esc 取消。", action, prompt=True), add="+")
            entry.bind("<FocusOut>", lambda _e, action=action:
                       self._clear_hotkey_prompt(action), add="+")
            self.hotkey_entries[action] = entry
            self._hotkey_borders.append(border)
            hotkey_rows.append((hotkey_row, label, border))
        hotkey_hint = tk.Label(f, text="点快捷键框修改。",
                              bg=NOTE_BG, fg=QUIET, font=(UI_FONT, 9), justify="left",
                              anchor="w")
        self._hotkey_hint = hotkey_hint
        self._hotkey_heading = hotkey_heading
        self._feedback_action = None
        self._feedback_prompt = False
        hotkey_hint.pack(fill="x", after=hotkey_heading)
        self.bind("<Destroy>", lambda e: e.widget == self and setattr(app, "_hotkey_capture", None))
        update_heading = section("关于与更新")
        tk.Label(update_heading, text=f"z-note {APP_VERSION}", bg=NOTE_BG, fg=QUIET,
                 font=(UI_FONT, 9)).pack(side="right")
        self._update_cancel = None
        self._update_busy = False
        self._waiting_for_update_check = False
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
                               bg=NOTE_BG, activebackground=NOTE_BG, fg="#475569", font=(UI_FONT, 9),
                               relief="flat", bd=0, highlightthickness=0, cursor="hand2")
            button._image = image
            return button

        # Reserve enough width for every state without changing the window width.
        self._update_button = update_button("立即更新", self._update_action)
        self._update_button.configure(text="检查更新")
        self._update_button.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self._update_cancel_button = update_button("取消", self._cancel_update)
        self._update_cancel_button.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self._update_cancel_button.grid_remove()
        self._update_status_choices = ("点击检查正式版本更新。",)
        self._update_error = None
        self._update_status_font = tkfont.Font(root=self, family=UI_FONT, size=9)
        self._update_status = tk.Label(actions, text=self._update_status_choices[0], bg=NOTE_BG,
                                       fg=QUIET, font=self._update_status_font, bd=0, padx=0)
        self._update_status.grid(row=0, column=2, sticky="w")
        self._update_details = tk.Button(actions, text="详情", command=self._show_update_error,
                                         bg=NOTE_BG, activebackground=NOTE_BG, fg="#475569",
                                         font=self._update_status_font, relief="flat", bd=0,
                                         highlightthickness=1, highlightbackground=NOTE_BG,
                                         padx=2, pady=0, cursor="hand2", takefocus=True)
        self._update_details.bind("<Return>", lambda _event: self._update_details.invoke())
        self._update_details.grid(row=0, column=3, sticky="w", padx=(6, 0))
        self._update_details.grid_remove()
        actions.grid_columnconfigure(4, weight=1)
        actions.bind("<Configure>", lambda event: self._layout_update_status(event.width))
        self._update_release_link = tk.Label(actions, text="查看发布页↗", bg=NOTE_BG,
                                             fg=QUIET, font=(UI_FONT, 9), cursor="hand2")
        self._update_release_link.grid(row=0, column=5, sticky="e", padx=(12, 0))
        self._update_release_link.bind("<Button-1>", lambda _e: webbrowser.open(updater.RELEASES_URL))
        self.bind("<Destroy>", self._destroy_update, add="+")

        section("数据")
        path = tk.Label(f, text=DATA_FILE, bg=NOTE_BG, fg=QUIET,
                        font=(UI_FONT, 9), wraplength=460, justify="left")
        path.pack(anchor="w")

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
        scrollbar = VerticalThumb(self, body, NOTE_BG)
        scrollbar.configure(width=8)
        content_width = max(1, width - 2 * RADIUS - scrollbar.winfo_reqwidth() - 4)
        # 文本按实际可用宽度换行，窗口高度在换行完成后再计算。
        startup.configure(wraplength=max(1, content_width - self._check_images[False].width() - 16))
        path.configure(wraplength=max(1, content_width - 16))
        layout_hotkeys(content_width)
        self._layout_hotkey_feedback(content_width)
        body.itemconfigure(content, width=content_width)
        layout_appearance(content_width)
        self._layout_update_status(content_width - 2 * f.winfo_pixels(f.cget("padx")))
        self.update_idletasks()
        content_height = f.winfo_reqheight()
        bottom_padding = 10
        height = min(content_height + RADIUS + header.winfo_reqheight() + 10 + bottom_padding, available_h)
        body.configure(yscrollcommand=scrollbar.set)
        self._smooth_wheel = SmoothWheel(body)
        self.bind("<MouseWheel>", lambda e: self._smooth_wheel.wheel(e.delta))
        border = cv.create_rectangle(0, 0, width - 1, height - 1,
                                     fill=NOTE_BG, outline=NOTE_BORDER)

        def layout(event):
            if event.widget != self:
                return
            w, h = event.width, event.height
            body_top = RADIUS + header.winfo_reqheight() + 10
            viewport_height = max(1, h - body_top - bottom_padding)
            # Reserve scrollbar space so wrapping does not oscillate at the overflow boundary.
            content_width = max(1, w - 2 * RADIUS - scrollbar.winfo_reqwidth() - 4)
            header.place(x=RADIUS, y=RADIUS, width=content_width)
            body.place(x=RADIUS, y=body_top, width=content_width, height=viewport_height)
            body.itemconfigure(content, width=content_width)
            startup.configure(wraplength=max(1, content_width - self._check_images[False].width() - 16))
            path.configure(wraplength=max(1, content_width - 16))
            layout_hotkeys(content_width)
            self._layout_hotkey_feedback(content_width)
            layout_appearance(content_width)
            cv.coords(border, 0, 0, w - 1, h - 1)

        def update_region(_event=None):
            body.configure(scrollregion=(0, 0, body.winfo_width(), f.winfo_reqheight()))
            if f.winfo_reqheight() > body.winfo_height():
                scrollbar.place(x=self.winfo_width() - RADIUS - scrollbar.winfo_reqwidth(), y=body.winfo_y(),
                                width=scrollbar.winfo_reqwidth(), height=body.winfo_height())
            else:
                scrollbar.place_forget()
                body.yview_moveto(0)

        self.bind("<Configure>", layout)
        # 切换字体后按当前窗口尺寸重排一次，换行和快捷键框的折行随新字体更新。
        self._relayout = lambda: layout(type("Event", (), {"widget": self, "width": self.winfo_width(),
                                                           "height": self.winfo_height()})())
        f.bind("<Configure>", update_region)
        body.bind("<Configure>", update_region)
        SettingsWindow._lock_size(self, width, height)
        self.geometry("%dx%d+%d+%d" % (width, height,
                       work.left + (work.right - work.left - width) // 2,
                       work.top + (work.bottom - work.top - height) // 2))
        if app._update_check_succeeded:
            self._show_available_update(app._available_update)
        self.update_idletasks()
        _show_after_layout(self)

    def _update_action(self):
        if self._update_busy or self._update_installing:
            return
        if self._update_state == "ready":
            if self._update_error is not None:
                self._show_update_ready()
            if not getattr(sys, "frozen", False) and not messagebox.askyesno("安装更新",
                    f"新版 {self._update_release.version} 已下载并校验完成。\n\n"
                    "现在安装吗？将保存并备份便签，退出 z-note 后打开覆盖安装程序，无需卸载。",
                    parent=self):
                return
            path, checksum = self._update_download
            try:
                self.app.install_update(path, self._update_release, checksum)
            except Exception as exc:
                self._set_update_status("安装启动失败。", "安装失败", error=str(exc))
            return
        downloading = self._update_state == "found"
        cancel = self._update_cancel = threading.Event()
        self._update_busy = True
        self._update_button.configure(state="disabled")
        self._update_cancel_button.grid()
        self._update_cancel_button.configure(state="normal")
        self._set_update_status("正在下载更新…" if downloading else "正在检查更新…")
        if not downloading:
            self._waiting_for_update_check = True
            self.app._check_for_update(manual=True)
            return
        messages, release = self.app.q, self._update_release

        def work():
            try:
                value = updater.download_update(release, cancel, lambda done, total:
                    messages.put(("update", self, cancel, "progress", (done, total))))
                if cancel.is_set():
                    updater.remove_download(value[0])
                    raise updater.Cancelled("已取消更新。")
                messages.put(("update", self, cancel, "downloaded", value))
            except updater.Cancelled:
                messages.put(("update", self, cancel, "cancelled", "已取消更新。"))
            except Exception as exc:
                messages.put(("update", self, cancel, "error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _cancel_update(self):
        if self._update_cancel is not None:
            self._update_cancel.set()
            if self._waiting_for_update_check:
                self._receive_update(self._update_cancel, "cancelled", "已取消更新。")
                return
            self._set_update_status("正在取消…")
            self._update_cancel_button.configure(state="disabled")

    def _destroy_update(self, event):
        if event.widget != self or self._update_installing:
            return
        if self._update_cancel is not None:
            self._update_cancel.set()
        if self._update_download is not None:
            updater.remove_download(self._update_download[0])

    def _layout_update_status(self, content_width):
        width = content_width - self._update_button.winfo_reqwidth() - 12
        measure = self._update_status_font.measure
        link = self._update_release_link
        link_width = link.winfo_reqwidth() + 12
        # Keep the link in place as cancel, progress, and error details come and go.
        inline = width >= link_width + max(
            self._update_cancel_button.winfo_reqwidth() + 12 + measure("50%（5.0 / 10.0 MB）"),
            self._update_details.winfo_reqwidth() + 6 + measure("下载失败，请重试。"),
        )
        link.grid_configure(row=0 if inline else 1, columnspan=1 if inline else 6,
                            column=5 if inline else 0, pady=0 if inline else (8, 0))
        if inline:
            width -= link_width
        if self._update_cancel_button.winfo_manager():
            width -= self._update_cancel_button.winfo_reqwidth() + 12
        if self._update_error is not None:
            width -= self._update_details.winfo_reqwidth() + 6
        text = next((choice for choice in self._update_status_choices if measure(choice) <= width), "")
        if not text and measure("…") <= width:
            prefix = self._update_status_choices[-1]
            while measure(prefix + "…") > width:
                prefix = prefix[:-1]
            text = prefix + "…"
        self._update_status.configure(text=text)

    def _set_update_status(self, text, *shorter, error=None):
        self._update_status_choices = (text, *shorter)
        self._update_error = error
        if error is None:
            self._update_details.grid_remove()
        else:
            self._update_details.grid()
        self._layout_update_status(self._update_button.master.winfo_width())

    def _show_update_error(self):
        if self._update_error is not None:
            messagebox.showerror("更新详情", self._update_error, parent=self)

    def _show_update_ready(self):
        self._set_update_status("下载校验完成，可立即安装。", "可立即安装")

    def _show_available_update(self, release):
        self._update_release = release
        self._update_state = "found" if release else "idle"
        self._set_update_status(f"可更新至 v{release.version}" if release else "已是最新版本。")
        self._update_button.configure(text="立即更新" if release else "检查更新")

    def _receive_update_check(self, kind, value):
        if self._waiting_for_update_check:
            self._receive_update(self._update_cancel, kind, value)
        elif kind == "checked" and not self._update_busy and self._update_state != "ready":
            self._show_available_update(value)

    def _receive_update(self, cancel, kind, value):
        if cancel is not self._update_cancel or cancel.is_set():
            if kind == "downloaded":
                updater.remove_download(value[0])
            if cancel is not self._update_cancel or kind == "progress":
                return
            kind, value = "cancelled", "已取消更新。"
        if kind == "progress":
            done, total = value
            percent = f"{done / total:.0%}"
            sizes = f"{percent}（{done / 1048576:.1f} / {total / 1048576:.1f} MB）"
            self._set_update_status(f"正在下载 {sizes}", sizes, percent)
            return
        checking = self._waiting_for_update_check or self._update_state == "idle"
        self._update_busy = False
        self._waiting_for_update_check = False
        self._update_cancel_button.grid_remove()
        self._update_button.configure(state="normal")
        if kind == "checked":
            self._show_available_update(value)
        elif kind == "downloaded":
            self._update_download = value
            self._update_state = "ready"
            self._show_update_ready()
            self._update_button.configure(text="立即安装")
            self._update_action()
        else:
            if self._update_state == "found" and self._update_release != self.app._available_update:
                self._show_available_update(self.app._available_update)
            if kind == "error":
                phase = "检查失败" if checking else "下载失败"
                self._set_update_status(f"{phase}，请重试。", phase, error=value)
            else:
                self._set_update_status(value)

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

    def _fit_hotkey_boxes(self):
        """切换字体后，按新字体重新量快捷键输入框，圆角边框跟着放大或缩小。"""
        hotkey_width = math.ceil(self._hotkey_font.measure("Ctrl+Alt+Shift+Backspace") /
                                 self._hotkey_font.measure("0")) + 2
        for border in self._hotkey_borders:
            border.winfo_children()[0].configure(width=hotkey_width)
        self.update_idletasks()  # 具名字体改动在空闲时才生效，先刷新再量尺寸。
        for border in self._hotkey_borders:
            entry = border.winfo_children()[0]
            box_w, box_h = entry.winfo_reqwidth() + 12, entry.winfo_reqheight() + 12
            border.configure(width=box_w, height=box_h)
            outline = border.find_all()[0]
            shape = round_rect(border, 1, 1, box_w - 1, box_h - 1, 7)
            border.coords(outline, *border.coords(shape))
            border.delete(shape)
        self.update_idletasks()
        self._relayout()

    def _drag_press(self, event):
        x, y = event.x_root-self.winfo_rootx(), event.y_root-self.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        if (x < EDGE or x > w-EDGE or y < EDGE or y > h-EDGE or
                ((x < CORNER or x > w-CORNER) and (y < CORNER or y > h-CORNER))):
            self._drag = None
            return
        if (event.widget in (self._close_button, self._minimize_button, *self._hotkey_borders) or
                isinstance(event.widget, (SettingsScale, FontSelector, VerticalThumb, tk.Checkbutton, tk.Button,
                                          tk.Scrollbar, tk.Entry))):
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
            self._hotkey_hint.pack_forget()
            self._feedback_prompt = False
            self.focus_set()
            return "break"
        key = next((name for name, code in HOTKEY_KEYS.items() if code == event.keycode), None)
        if key is None:
            return "break"
        parts = [name for mask, name in ((4, "Ctrl"), (0x20000, "Alt"), (1, "Shift"))
                 if event.state & mask]
        self._apply_hotkey(action, "+".join(parts + [key]))
        return "break"

    def _layout_hotkey_feedback(self, content_width):
        available = max(1, content_width - 8)
        inset = 0
        if self._feedback_action is not None:
            border = self.hotkey_entries[self._feedback_action].master
            inset = max(0, available - border.winfo_reqwidth())
        self._hotkey_hint.configure(wraplength=max(1, available - inset))
        if self._hotkey_hint.winfo_manager():
            self._hotkey_hint.pack_configure(padx=(inset, 0))

    def _show_hotkey_feedback(self, text, action=None, *, error=False, prompt=False):
        self._feedback_action = action
        self._feedback_prompt = prompt
        target = self._hotkey_heading if action is None else self.hotkey_entries[action].master.master
        self._hotkey_hint.configure(text=text, fg="#A44A42" if error else QUIET)
        self._hotkey_hint.pack(fill="x", after=target)
        self._layout_hotkey_feedback(target.master.winfo_width())

    def _clear_hotkey_prompt(self, action):
        if self._feedback_prompt and self._feedback_action == action:
            self._hotkey_hint.pack_forget()
            self._feedback_prompt = False

    def _apply_hotkey(self, action, value):
        error = self.app.update_hotkey(action, value)
        if error:
            self._show_hotkey_feedback(error, action, error=True)
        else:
            self._hotkey_vars[action].set(value)
            self._show_hotkey_feedback("快捷键已保存并生效。", action)

    def _reset_hotkeys(self):
        error = self.app.reset_hotkeys()
        if error:
            self._show_hotkey_feedback(error, error=True)
        else:
            for action in HOTKEY_LABELS:
                self._hotkey_vars[action].set(self.app.hotkey_text(action))
            self._show_hotkey_feedback("快捷键已恢复默认值。")

    def _toggle_boot(self):
        settings = self.app.data["settings"]
        enabled = self.v_boot.get()
        try:
            set_autostart(enabled)
        except OSError as exc:
            self.v_boot.set(settings["autostart"])
            messagebox.showwarning("z-note", f"更新开机自启失败。\n{exc}", parent=self)
            return
        settings["autostart"] = enabled
        self._save()

    def _toggle_startup_display(self):
        self.app.data["settings"]["showOnStartup"] = self.v_show.get()
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
        self._region_suspended = 0
        self.bind("<Configure>", self.layout)
        self.content.bind("<Configure>", self.update_region)
        self.viewport.bind("<Configure>", self.update_region)
        self.viewport.bind("<MouseWheel>", self.wheel)
        self._smooth_wheel = SmoothWheel(self.viewport)

    def suspend_region(self):
        self._region_suspended += 1

    def resume_region(self):
        self._region_suspended = max(0, self._region_suspended - 1)
        if not self._region_suspended:
            self.update_region()

    def wheel(self, event):
        if self.content.winfo_reqheight() > self.viewport.winfo_height():
            self._smooth_wheel.wheel(event.delta)
        else:
            self._smooth_wheel.stop()
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
        if self._region_suspended:
            return
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
        self._close_button.bind("<Button-1>", self._close)
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
        self._button_font = tkfont.Font(root=self, family=UI_FONT, size=10)
        tabs = tk.Frame(shell, bg=NOTE_BG)
        tabs.pack(fill="x", pady=(18, 12))
        tabs.columnconfigure(1, weight=1)
        tab_buttons = tk.Frame(tabs, bg=NOTE_BG)
        tab_buttons.grid(row=0, column=0, sticky="w")
        self.tabs = {}
        for key, label in (("archives", "已清除待办"), ("deletedNotes", "已删除便签")):
            self.tabs[key] = self.button(tab_buttons, label, lambda key=key: self.switch(key))
            # 选中页签用与列表选中行相同的浅灰底，和普通按钮区分开。
            self.tabs[key]._selected_image = self._button_image(label, "#E9EDF2")
            self.tabs[key].pack(side="left", padx=(0, 12))
        self._batch_actions = tk.Frame(tabs, bg=NOTE_BG)
        self._batch_actions.grid(row=0, column=1, sticky="e")
        self.select_all_button = self.button(self._batch_actions, "取消全选", self.toggle_all)
        self._select_all_image = self._button_image("全选")
        self.select_all_button.grid(row=0, column=0, padx=(0, 8))
        self.select_all_button.grid_remove()
        self.delete_button = self.button(self._batch_actions, "删除", self.delete_selected)
        self.delete_button.grid(row=0, column=1, padx=(0, 8))
        self.delete_button.grid_remove()
        self.batch_button = self.button(self._batch_actions, "批量管理", self.toggle_batch)
        self.batch_button.grid(row=0, column=2)
        self.hint = tk.Label(shell, bg=NOTE_BG, fg=QUIET, font=(UI_FONT, 10), anchor="w")
        self.hint.pack(fill="x", pady=(0, 12))
        panes = tk.Frame(shell, bg=NOTE_BG)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=1, uniform="panes")
        panes.columnconfigure(1, weight=2, uniform="panes")
        panes.rowconfigure(0, weight=1)
        # 每个标签一份独立列表：切换只换显示，不销毁重建行控件。
        self._states = {}
        for key in ("archives", "deletedNotes"):
            pane = HistoryPane(panes)
            pane.grid(row=0,column=0,sticky="nsew",padx=(0,10))
            self._states[key] = {
                "pane": pane, "built": False, "ids": [], "groups": {}, "info": {},
                "names": [], "rows": {}, "check_vars": {}, "check_boxes": {},
                "recycle_labels": {}, "labels": {}, "batch_layout": None,
                "row_highlight": None, "highlighted_key": None}
        self.right = HistoryPane(panes)
        self.right.grid(row=0,column=1,sticky="nsew",padx=(10,0))
        self._activate(self.mode)
        self.canvas = self.right.viewport
        self.content = self.right.content
        self.bind("<MouseWheel>", self.wheel)
        self.bind("<Escape>", lambda e: PopupFade.hide(self, self.withdraw))
        work = SettingsWindow._work_area(self)
        scale = max(1, round(self.winfo_fpixels("1i")) / 96)
        margin = max(8, round(16 * scale))
        width = min(max(round(640 * scale), self.winfo_reqwidth()), work.right-work.left-2*margin)
        height = min(round(480 * scale), work.bottom-work.top-2*margin)
        SettingsWindow._lock_size(self, width, height)
        self.geometry(f"{width}x{height}+{work.left+(work.right-work.left-width)//2}+{work.top+(work.bottom-work.top-height)//2}")
        self.update_idletasks()
        # Reserve room for the delete button so checking a row cannot move the toolbar.
        self._batch_min_width = tab_buttons.winfo_reqwidth() + 16 + sum(
            button.winfo_reqwidth() for button in self._batch_actions.winfo_children())
        tabs.bind("<Configure>", lambda e: self._layout_batch_actions(e.width))
        self.hint.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(1, e.width), justify="left"))
        self._sync_pane(self.mode)
        self._render_view()
        _show_after_layout(self)
        self.after(400, self._warm_inactive)

    def _button_image(self, text, fill=NOTE_BG):
        font = self._button_font
        width, height = font.measure(text) + 24, font.metrics("linespace") + 12
        image = Image.new("RGBA", (width * 4, height * 4))
        ImageDraw.Draw(image).rounded_rectangle((2, 2, width*4-3, height*4-3), radius=28,
                                                fill=fill, outline=NOTE_BORDER, width=4)
        return ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=self)

    def button(self, parent, text, command):
        photo = self._button_image(text)
        button = tk.Button(parent, text=text, command=command, image=photo, compound="center",
            bg=NOTE_BG, fg="#475569", activebackground=NOTE_BG, activeforeground="#1F2328",
            font=self._button_font, relief="flat", bd=0, padx=0, pady=0, cursor="hand2", highlightthickness=0)
        button._image = photo
        return button

    def _activate(self, mode):
        self.mode = mode
        state = self._states[mode]
        self.left = state["pane"]
        tk.Misc.tkraise(self.left)
        self.ids = state["ids"]
        self.groups = state["groups"]
        self.names = state["names"]
        self.rows = state["rows"]
        self._check_vars = state["check_vars"]
        self._check_boxes = state["check_boxes"]
        self._recycle_labels = state["recycle_labels"]
        self._hotkey_borders = list(self.left.bars.values()) + list(self.right.bars.values())

    def _compute_model(self, mode):
        sources = {n["id"]: n for n in self.app.data["deletedNotes"] + self.app.data["notes"]}
        stamp = "clearedAt" if mode == "archives" else "deletedAt"
        records = sorted(reversed(self.app.data[mode]),
                         key=lambda record: record.get(stamp, ""), reverse=True)
        groups = {}
        for record in records:
            key = record["noteId"] if mode == "archives" else record["id"]
            groups.setdefault(key, []).append(record)
        names = {}
        for key, group in groups.items():
            source = sources.get(key, group[0])
            names[key] = (source.get("title") or "无标题", source.get("editedAt", ""))
        titles = Counter(name for name, _ in names.values())
        recycled = mode == "deletedNotes"
        info = {}
        for key, (name, edited_at) in names.items():
            detail = suffix = ""
            if recycled or titles[name] > 1:
                timestamp = (edited_at or "").split(".")[0].replace("T", " ") or "编辑时间未知"
                if recycled:
                    detail = timestamp
                    suffix = "\n" + timestamp
                else:
                    detail = suffix = " · " + timestamp
            info[key] = (name, detail or None, suffix)
        return list(groups), groups, info

    def _sync_pane(self, mode):
        state = self._states[mode]
        ids, groups, info = self._compute_model(mode)
        unchanged = (state["built"] and ids == state["ids"]
                     and info == state["info"] and groups == state["groups"])
        state["groups"] = groups
        if unchanged:
            if mode == self.mode:
                self.groups = groups
            return False
        if not state["built"]:
            state["ids"] = ids
            self._build_rows(mode, info)
        else:
            self._diff_rows(mode, ids, info)
        state["info"] = info
        state["names"] = [info[key][0] + info[key][2] for key in ids]
        state["built"] = True
        if mode == self.mode:
            self.ids = ids
            self.groups = groups
            self.names = state["names"]
            self.checked.intersection_update(self.ids)
            self.left.update_region()
        return True

    def _build_rows(self, mode, info):
        state = self._states[mode]
        pane = state["pane"]
        for child in pane.content.winfo_children():
            child.destroy()
        for cache in ("rows", "check_vars", "check_boxes", "recycle_labels", "labels", "info"):
            state[cache].clear()
        state["row_highlight"] = None
        state["highlighted_key"] = None
        # Lock the pane so the rebuild never shows a blank or half-erased list;
        # the final RedrawWindow paints the new state once.
        hwnds = [pane.viewport.winfo_id(), pane.content.winfo_id()]
        pane.suspend_region()
        for hwnd in hwnds:
            user32.SendMessageW(hwnd, 0x000B, 0, 0)  # WM_SETREDRAW
        try:
            for key in state["ids"]:
                self._create_row(mode, key, info[key]).pack(fill="x", pady=4)
            pane.content.update_idletasks()
        finally:
            for hwnd in hwnds:
                if user32.IsWindow(hwnd):
                    user32.SendMessageW(hwnd, 0x000B, 1, 0)
            pane.resume_region()
            if user32.IsWindow(pane.viewport.winfo_id()):
                # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
                user32.RedrawWindow(pane.viewport.winfo_id(), None, None, 0x0185)

    def _diff_rows(self, mode, ids, info):
        state = self._states[mode]
        old_ids = state["ids"]
        old_set, new_set = set(old_ids), set(ids)
        state["ids"] = ids
        for key in old_ids:
            if key not in new_set:
                self._destroy_row(mode, key)
        pane = state["pane"]
        pane.suspend_region()
        try:
            for index, key in enumerate(ids):
                if key not in old_set:
                    self._create_row(mode, key, info[key])
                    self._pack_row_at(mode, key, index)
                elif state["info"].get(key) != info[key]:
                    old_info = state["info"][key]
                    if (old_info[1] is None) != (info[key][1] is None):
                        self._destroy_row(mode, key)
                        self._create_row(mode, key, info[key])
                        self._pack_row_at(mode, key, index)
                    else:
                        self._update_row_text(mode, key, info[key])
        finally:
            pane.resume_region()
        packed = [key for key in ids if state["rows"][key].winfo_manager()]
        if packed != ids:
            for key in ids:
                state["rows"][key].pack_forget()
            for key in ids:
                state["rows"][key].pack(fill="x", pady=4)

    def _create_row(self, mode, key, info):
        state = self._states[mode]
        recycled = mode == "deletedNotes"
        name, detail, _suffix = info
        row = tk.Frame(state["pane"].content, bg=NOTE_BG, padx=5 if recycled else 6,
                       pady=2 if recycled else 0)
        row.bind("<Button-1>", lambda e,k=key: self.choose(k))
        state["rows"][key] = row
        value = state["check_vars"][key] = tk.BooleanVar(master=self, value=key in self.checked)
        state["check_boxes"][key] = tk.Checkbutton(row, variable=value, bg=NOTE_BG,
            activebackground=NOTE_BG, selectcolor=NOTE_BG, indicatoron=False,
            image=self._check_images[False], selectimage=self._check_images[True],
            command=lambda k=key, v=value: self.check(k,v.get()), bd=0, highlightthickness=0)
        label = tk.Label(row, text=name, bg=NOTE_BG, fg="#2B2F36", anchor="w",
                         justify="left", font=(UI_FONT,10), cursor="hand2",
                         padx=1 if recycled else 0)
        detail_label = None
        if recycled:
            row.columnconfigure(1, weight=1)
            label.grid(row=0, column=1, sticky="ew")
            detail_label = tk.Label(row, text=detail, bg=NOTE_BG, fg=INK,
                                    anchor="w", justify="left", font=(UI_FONT, 9), cursor="hand2")
            detail_label.grid(row=1, column=1, sticky="ew")
            state["recycle_labels"][key] = (label, detail_label)
            detail_label.bind("<Configure>", self._wrap_recycle_label)
            detail_label.bind("<Button-1>", lambda e,k=key: self.choose(k))
            label.bind("<Configure>", self._wrap_recycle_label)
        else:
            label.pack(side="left", fill="x", expand=detail is None)
            if detail is None:
                label.bind("<Configure>", lambda e, l=label: l.configure(wraplength=max(1,e.width-8)))
            else:
                row.bind("<Configure>", lambda e, l=label: l.configure(wraplength=max(1, e.width // 2)))
                detail_label = tk.Label(row, text=detail, bg=NOTE_BG, fg=INK,
                                        anchor="w", justify="left", font=(UI_FONT, 10), cursor="hand2")
                detail_label.pack(side="left", fill="x", expand=True)
                detail_label.bind("<Configure>", lambda e, l=detail_label: l.configure(wraplength=max(1, e.width-8)))
                detail_label.bind("<Button-1>", lambda e,k=key: self.choose(k))
        label.bind("<Button-1>", lambda e,k=key: self.choose(k))
        state["labels"][key] = (label, detail_label)
        return row

    def _pack_row_at(self, mode, key, index):
        state = self._states[mode]
        following = None
        for other in state["ids"][index+1:]:
            candidate = state["rows"].get(other)
            if candidate is not None and candidate.winfo_manager():
                following = candidate
                break
        if following is not None:
            state["rows"][key].pack(fill="x", pady=4, before=following)
        else:
            state["rows"][key].pack(fill="x", pady=4)

    def _destroy_row(self, mode, key):
        state = self._states[mode]
        row = state["rows"].pop(key, None)
        for cache in ("check_vars", "check_boxes", "recycle_labels", "labels", "info"):
            state[cache].pop(key, None)
        if key == state["highlighted_key"]:
            state["row_highlight"] = None
            state["highlighted_key"] = None
        if row is not None:
            row.destroy()

    def _update_row_text(self, mode, key, info):
        label, detail_label = self._states[mode]["labels"][key]
        name, detail, _suffix = info
        if str(label.cget("text")) != name:
            label.configure(text=name)
        if detail_label is not None and str(detail_label.cget("text")) != detail:
            detail_label.configure(text=detail)

    def _warm_inactive(self):
        if not self.winfo_exists():
            return
        # 窗口还不可见时不预热，避免把构造期间的时间算进打开耗时。
        if not self.winfo_viewable() or float(self.attributes("-alpha")) < 1.0:
            return
        other = "deletedNotes" if self.mode == "archives" else "archives"
        if self._states[other]["built"]:
            return
        records = self.app.data[other]
        keys = {r["noteId"] if other == "archives" else r["id"] for r in records}
        if len(keys) > 300:
            return
        # 预热时冻结整窗重绘，避免逐行布局把可见窗口重画多次。
        hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        user32.SendMessageW(hwnd, 0x000B, 0, 0)  # WM_SETREDRAW
        try:
            self._sync_pane(other)
        finally:
            if user32.IsWindow(hwnd):
                user32.SendMessageW(hwnd, 0x000B, 1, 0)
                # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
                user32.RedrawWindow(hwnd, None, None, 0x0185)

    def _render_view(self):
        for key, tab in self.tabs.items():
            tab.configure(fg="#1F2328" if key == self.mode else QUIET,
                          image=tab._selected_image if key == self.mode else tab._image)
        self.hint.configure(text="清除的已完成待办会保存在这里，按便签整理。" if self.mode == "archives"
                            else "彻底删除便签不会删除已清除待办的记录。")
        self.checked.intersection_update(self.ids)
        if self.selected_id not in self.ids:
            self.selected_id = None
        self._show_batch_controls()
        self.highlight_selection()
        self.left.content.update_idletasks()
        self.left.update_region()
        self.select()

    def switch(self, mode):
        self.selected_id = None
        self.batch_mode = False
        self.checked.clear()
        state = self._states[mode]
        for value in state["check_vars"].values():
            if value.get():
                value.set(False)
        # Only relayout insets when the pane was left in batch mode; a normal list
        # keeps its labels untouched across switches.
        if state["batch_layout"]:
            state["batch_layout"] = None
        # Build/diff while the pane is still behind, then reveal it in one step.
        self._sync_pane(mode)
        self._activate(mode)
        self.left.viewport.yview_moveto(0)
        self._render_view()

    def refresh(self, *, if_changed=False):
        changed = self._sync_pane(self.mode)
        if if_changed and not changed:
            return
        self._render_view()

    def _show_batch_controls(self):
        recycled = self.mode == "deletedNotes"
        state = self._states[self.mode]
        if recycled:
            if state["batch_layout"] != self.batch_mode:
                for key, box in self._check_boxes.items():
                    self._layout_recycle_check(key, box)
                state["batch_layout"] = self.batch_mode
            elif self.batch_mode:
                for key, box in self._check_boxes.items():
                    if box.winfo_manager() != "place":
                        self._layout_recycle_check(key, box)
        else:
            for key, box in self._check_boxes.items():
                if self.batch_mode:
                    if not box.winfo_manager():
                        row = self.rows[key]
                        box.pack(side="left", anchor="center", padx=(0, 6), before=row.pack_slaves()[0])
                elif box.winfo_manager():
                    box.pack_forget()
        self.batch_button.configure(text="取消批量" if self.batch_mode else "批量管理")
        self._layout_batch_actions(self._batch_actions.master.winfo_width())
        self._update_batch_controls()

    def _layout_recycle_check(self, key, box):
        # Keep the label HWNDs in place so Tk cannot copy old text pixels when the inset changes.
        inset = box.winfo_reqwidth() + 6 if self.batch_mode else 0
        padx = 1 + inset
        for label in self._recycle_labels[key]:
            width = label.winfo_width()
            if width <= 1:
                width = self.left.viewport.winfo_width() - 2 * label.master.winfo_pixels(label.master.cget("padx"))
            wraplength = max(1, width - inset - 8)
            if label.winfo_pixels(label.cget("padx")) != padx:
                label.configure(padx=padx)
            if label.winfo_pixels(label.cget("wraplength")) != wraplength:
                label.configure(wraplength=wraplength)
        if self.batch_mode:
            if box.winfo_manager() != "place":
                box.place(x=0, rely=.5, anchor="w")
                box.lift()
        elif box.winfo_manager():
            box.place_forget()

    def _wrap_recycle_label(self, event):
        label = event.widget
        label.configure(wraplength=max(1, event.width - label.winfo_pixels(label.cget("padx")) - 7))

    def wheel(self, event):
        pane = self.left if event.x_root < self.right.winfo_rootx() else self.right
        return pane.wheel(event)

    def choose(self, key):
        self.selected_id = key
        self.highlight_selection()
        self.select()

    def _close(self, _event):
        PopupFade.hide(self, self.withdraw)
        if self.selected_id is not None:
            self.choose(None)

    def _paint_row(self, key, color):
        row = self.rows[key]
        row.configure(bg=color)
        for child in row.winfo_children():
            child.configure(bg=color)
            if isinstance(child, tk.Checkbutton):
                child.configure(selectcolor=color, activebackground=color)

    def highlight_selection(self):
        state = self._states[self.mode]
        if state["row_highlight"] is not None:
            state["row_highlight"].destroy()
            state["row_highlight"] = None
        previous = state["highlighted_key"]
        state["highlighted_key"] = None
        if previous is not None and previous in self.rows:
            self._paint_row(previous, NOTE_BG)
        if self.selected_id is None or self.selected_id not in self.rows:
            return
        self._paint_row(self.selected_id, "#E9EDF2")
        row = self.rows[self.selected_id]
        background = state["row_highlight"] = tk.Canvas(row, bg=NOTE_BG, bd=0, highlightthickness=0)
        background.bind("<Button-1>", lambda e, key=self.selected_id: self.choose(key))
        background.bind("<Configure>", self._draw_row_highlight)
        background.place(x=0, y=0, relwidth=1, relheight=1, bordermode="ignore")
        tk.Misc.lower(background)
        state["highlighted_key"] = self.selected_id

    def _draw_row_highlight(self, event):
        canvas = event.widget
        canvas.delete("all")
        round_rect(canvas, 0, 0, event.width, event.height, min(6, event.width/2, event.height/2),
                   fill=canvas.master.cget("bg"), outline="")

    def _layout_batch_actions(self, width):
        stacked = self.batch_mode and width < self._batch_min_width
        self._batch_actions.grid_configure(row=1 if stacked else 0, column=0 if stacked else 1,
                                           columnspan=2 if stacked else 1, pady=(8, 0) if stacked else 0)

    def _update_batch_controls(self):
        if self.batch_mode:
            all_selected = self.ids and len(self.checked) == len(self.ids)
            self.select_all_button.configure(
                text="取消全选" if all_selected else "全选",
                image=self.select_all_button._image if all_selected else self._select_all_image,
                state="normal" if self.ids else "disabled")
            self.select_all_button.grid()
        else:
            self.select_all_button.grid_remove()
        if self.batch_mode and self.checked:
            self.delete_button.grid()
        else:
            self.delete_button.grid_remove()

    def check(self, key, selected):
        self.checked.add(key) if selected else self.checked.discard(key)
        self._check_vars[key].set(selected)
        self._update_batch_controls()

    def toggle_all(self):
        selected = len(self.checked) < len(self.ids)
        if selected:
            self.checked.update(self.ids)
        else:
            self.checked.clear()
        for value in self._check_vars.values():
            value.set(selected)
        self._update_batch_controls()

    def toggle_batch(self):
        self.batch_mode = not self.batch_mode
        for key in self.checked:
            self._check_vars[key].set(False)
        self.checked.clear()
        content = self.left.content
        if self.mode != "deletedNotes" or not content.winfo_ismapped():
            self._show_batch_controls()
            return
        hwnd = content.winfo_id()
        user32.SendMessageW(hwnd, 0x000B, 0, 0)  # WM_SETREDRAW
        try:
            self._show_batch_controls()
            content.update_idletasks()
        finally:
            if user32.IsWindow(hwnd):
                user32.SendMessageW(hwnd, 0x000B, 1, 0)
                # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
                user32.RedrawWindow(hwnd, None, None, 0x0185)

    def delete_selected(self):
        keys = set(self.checked)
        if not keys:
            return
        if messagebox.askyesno("批量删除", f"彻底删除选中的 {len(keys)} 张便签记录？此操作无法恢复。", parent=self):
            field = "noteId" if self.mode == "archives" else "id"
            self._delete_records(field, keys)

    def _delete_records(self, field, keys):
        mode = self.mode

        def change(data):
            data[mode] = [record for record in data[mode] if record[field] not in keys]

        if self.app._store_history(change) and mode == "deletedNotes":
            self.app.request_dock_refresh()

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

    def _center_empty(self):
        empty = getattr(self, "_empty_label", None)
        if empty is not None and empty.winfo_exists():
            top = max(0, (self.canvas.winfo_height() - empty.winfo_reqheight()) // 2)
            empty.pack_configure(pady=(top, 0))

    def select(self, _event=None):
        for child in self.content.winfo_children():
            child.destroy()
        if self.selected_id is None:
            # 占位提示用浅灰色，在预览区居中。
            self._empty_label = tk.Label(self.content, text="请选择一条记录" if self.ids else "暂无记录",
                                         bg=NOTE_BG, fg=QUIET, font=(UI_FONT, 10), anchor="center")
            self._empty_label.pack(fill="x")
            if not getattr(self, "_empty_centering", False):
                self._empty_centering = True
                self.canvas.bind("<Configure>", lambda _e: self._center_empty(), add="+")
            self._center_empty()
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
                        # 底色、标题底和边框画在同一张超采样图里，圆角处两层抗锯齿边缘不会透出白缝。
                        scale, cw, ch = 4, w-2, h-2
                        box = (0, 0, cw*scale-1, ch*scale-1)
                        mask = Image.new("L", (cw*scale, ch*scale), 0)
                        ImageDraw.Draw(mask).rounded_rectangle(box, radius=12*scale, fill=255)
                        fill = Image.new("RGBA", mask.size, NOTE_BG)
                        if header_h > 1:
                            ImageDraw.Draw(fill).rectangle((0, 0, cw*scale, (header_h-1)*scale-1), fill="#F5F7FA")
                        image = Image.new("RGBA", mask.size, (0, 0, 0, 0))
                        image.paste(fill, (0, 0), mask)
                        ImageDraw.Draw(image).rounded_rectangle(box, radius=12*scale, outline=NOTE_BORDER, width=scale)
                        # 按面积平均缩小：LANCZOS 会让圆弧比直边浅，并在弧线内侧留下发白的亮边。
                        canvas._card_image = ImageTk.PhotoImage(
                            image.resize((cw, ch), Image.Resampling.BOX), master=canvas)
                        canvas.create_image(1, 1, image=canvas._card_image, anchor="nw")
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
            self._delete_records("id", {record_id})


class SideDockTooltip(Tooltip):
    @classmethod
    def _hide(cls):
        Tooltip._hide()

    def _show(self):
        self._aid = None
        dock = self._w.master.dock
        if not self._gt():
            return  # 标题已完整显示时不再重复提示。
        if dock._sorting_bookmark is None and dock._scroll_job is None:
            self.show_at(self._w, self._gt(), beside=True)

    @staticmethod
    def show_at(widget, text, *, beside=False):
        Tooltip._hide()
        work = SideDock.work_area()
        popup, canvas = Tooltip._new_popup(widget)
        label = canvas.create_text(0, 0, anchor="nw", text=text, font=(UI_FONT, 9),
                                   justify="left", fill="#3A3F46",
                                   width=max(1, min(480, work.right-work.left-48)))
        left, top, right, bottom = canvas.bbox(label)
        width, height = right-left+24, max(30, bottom-top+12)
        canvas.move(label, 12-left, (height-(bottom-top))/2-top)
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        if beside and left-width-8 >= work.left+8:
            x = left-width-8
            y = top+(widget.winfo_height()-height)//2
        else:
            x = min(work.right-width-8, max(work.left+8, left-width+40))
            y = top-height-6
            if y < work.top+8:
                y = top+widget.winfo_height()+6
        y = min(work.bottom-height-8, max(work.top+8, y))
        Tooltip._show_popup(popup, canvas, width, height, x, y)
        return popup


class DockBookmark(tk.Toplevel):
    """主入口和便签书签共用的贴边圆角窗口。"""
    NOTE_TITLE_X = 30
    NOTE_RIGHT_PADDING = 12
    NUMBER_RIGHT_PADDING = 6
    HOVER_EXTENSION = 12

    def __init__(self, dock, height, note_id=None):
        super().__init__(dock.app.root)
        self.withdraw()
        self.dock, self.note_id, self.height = dock, note_id, height
        self.base_width = dock.bar_width if self is dock else dock.bookmark_width
        self.full_label = ""
        self._compact_label = None
        self._label_truncated = False
        self._note_visible = False
        self._hovered = False
        self._menu_open = False
        self._extension = 0
        self._animation_job = None
        self._position_y = 0
        self._canvas_y = 0
        self._press = None
        self._drawn = None
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=NOTE_BG)
        self.bar = tk.Canvas(self, bg=NOTE_BG, bd=0, highlightthickness=0,
                             cursor="hand2")
        if self is dock:
            self.disclosure_icons = (load_icon(self.bar, "disclosure-right", 9),
                                     load_icon(self.bar, "disclosure-down", 9))
            dot = Image.new("RGBA", (20, 20))
            ImageDraw.Draw(dot).ellipse((0, 0, 19, 19), fill="#7A838E")
            self.note_visible_dot = ImageTk.PhotoImage(
                dot.resize((5, 5), Image.Resampling.LANCZOS), master=self.bar)
        self.bar.place(x=0, y=0)
        self.bar.bind("<Enter>", self._enter)
        self.bar.bind("<Leave>", self._leave)
        self.bar.bind("<ButtonPress-1>", self._drag_press)
        self.bar.bind("<B1-Motion>", self._drag_move)
        self.bar.bind("<ButtonRelease-1>", self._drag_release)
        if self is not dock:
            self.bar.bind("<Button-3>", self._show_delete_menu)
        self.bar.bind("<MouseWheel>", dock._wheel)
        # 只有标题被截断或缩写时才提示全名，并多停留一会儿再出现。
        self.tip = SideDockTooltip(self.bar, lambda: self.full_label if self._label_truncated else "",
                                   delay=800) if self is not dock else None
        self.bind("<Map>", self._style_native)

    def _style_native(self, event):
        if event.widget != self:
            return
        # Tk 映射后才生成最终外层句柄，在此设置圆角，避免样式落到临时句柄上。
        hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
        style = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, style & ~0x40000 | 0x80)
        # 小圆角保留抗锯齿，并缩小密集书签之间叠加的 DWM 阴影。
        preference = ctypes.c_int(3)  # DWMWCP_ROUNDSMALL
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))

    def update_label(self, label, visible=False, *, compact_label=None):
        self.full_label, self._note_visible = label, visible
        self._compact_label = compact_label
        self._draw()

    def _draw(self):
        width = self.base_width + self._extension
        text = self.full_label
        is_note = self.note_id is not None
        if self is self.dock:
            if self.dock.expanded:
                text = "收起"
            elif self._hovered and self.dock._rows:
                text = "展开"
        right_padding = self.NUMBER_RIGHT_PADDING if self._compact_label else self.NOTE_RIGHT_PADDING
        limit = self.base_width - self.NOTE_TITLE_X - right_padding
        if self._compact_label and self.dock.font.measure(text) > limit:
            text = text.replace(" ", "", 1)
            if self.dock.font.measure(text) > limit:
                text = self._compact_label
        if is_note and self.dock.font.measure(text) > limit:
            while text and self.dock.font.measure(text + "…") > limit:
                text = text[:-1]
            text += "…"
        self._label_truncated = is_note and text != self.full_label
        bg = "#F0F3F7" if is_note and not self._note_visible else NOTE_BG
        if self._drawn == (text, bg, width):
            return
        self._drawn = (text, bg, width)
        self.configure(bg=bg)
        self.bar.configure(bg=bg)
        self.bar.place_configure(width=width, height=self.height)
        self.bar.delete("all")
        text_x = self.NOTE_TITLE_X if is_note else width/2 + (8 if self is self.dock else 0)
        text_y = self.height/2
        if self is self.dock and text == self.full_label:
            text_y -= 2  # 补偿英文小写字形的视觉中心偏下。
        self.bar.create_text(text_x, text_y, text=text, font=self.dock.font,
                             anchor="w" if is_note else "center",
                             fill="#1F2328" if self is self.dock else "#475569")
        if self is self.dock:
            icon_y = self.height/2 - (1 if self.dock.expanded else 0)
            self.bar.create_image(19, icon_y, image=self.disclosure_icons[self.dock.expanded],
                                  tags="dock-disclosure")
        elif is_note and self._note_visible:
            # 以当前显示的字形为准，英文下伸字母和中文可有不同中线。
            ink_middle = text_y
            if self.dock.label_ink_font is not None:
                _, top, _, bottom = self.dock.label_ink_font.getbbox(text, anchor="ls")
                ink_middle += self.dock.label_baseline_offset + (top+bottom)/2
            dot_y = math.floor(ink_middle + 0.5)
            self.bar.create_image(19, dot_y, image=self.dock.note_visible_dot,
                                  tags="dock-note-visible")

    def place_at(self, y, batch=None, *, lift=True):
        if lift or not self.winfo_ismapped():
            self.lift()
        self.move_to(y, batch=batch)
        if not self.winfo_ismapped():
            self.deiconify()

    def move_to(self, y, *, viewport=None, batch=None):
        self._position_y = round(y)
        self._position(viewport, batch)

    def set_motion_opacity(self, opacity):
        previous = self.attributes("-alpha")
        if previous == opacity:
            return
        if previous == 1 and self.winfo_ismapped():
            # Tk 会缓存分层样式，已映射的窗口需要重新打开它才能淡出。
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            style = user32.GetWindowLongPtrW(hwnd, -20)
            user32.SetWindowLongPtrW(hwnd, -20, style | 0x00080000)
        self.attributes("-alpha", opacity)
        if opacity == 1:
            # Tk 保留分层样式，完成动画后恢复普通的 DWM 圆角窗口。
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            style = user32.GetWindowLongPtrW(hwnd, -20)
            user32.SetWindowLongPtrW(hwnd, -20, style & ~0x00080000)

    def _position(self, viewport=None, batch=None):
        width = self.base_width + self._extension
        edge = user32.GetSystemMetrics(0)
        top, bottom = self._position_y, self._position_y+self.height
        if viewport is not None:
            top, bottom = max(top, viewport[0]), min(bottom, viewport[1])
        canvas_y = self._position_y-top
        if canvas_y != self._canvas_y:
            self._canvas_y = canvas_y
            self.bar.place_configure(y=canvas_y)
        # 原生窗口右端伸出屏幕，让可见部分只保留 DWM 抗锯齿左圆角。
        geometry = (width+24, bottom-top, edge-width, top)
        if batch is not None and self.winfo_ismapped():
            batch.append((self, geometry))  # 已显示的窗口留给 _commit_moves 一次性提交。
        else:
            self.geometry("%dx%d+%d+%d" % geometry)
        if viewport is None:
            self._draw()

    def _animate(self, target):
        if self.dock._sorting_bookmark is self or self.dock._scroll_job is not None:
            return
        self._cancel_animation()
        origin, started = self._extension, time.monotonic()
        if target == origin:
            return

        def frame():
            self._animation_job = None
            progress = min(1.0, (time.monotonic()-started)/0.1)
            self._extension = round(origin+(target-origin)*(1-(1-progress)**2))
            self._position()
            if progress < 1:
                self._animation_job = self.after(16, frame)

        frame()

    def _cancel_animation(self):
        if self._animation_job is not None:
            self.after_cancel(self._animation_job)
            self._animation_job = None

    def _enter(self, event=None):
        self._hovered = True
        if self is self.dock:
            self._draw()
        else:
            self._animate(self.HOVER_EXTENSION)

    def _leave(self, event=None):
        if self._menu_open:
            return
        self._hovered = False
        if self is self.dock:
            self._draw()
        else:
            self._animate(0)

    def _drag_press(self, event):
        if self is self.dock:
            self.dock._start_drag(event)
        elif self.dock._scroll_job is None:
            self._press = (event.x_root, event.y_root, event.y)

    def _drag_move(self, event):
        if self is self.dock:
            self.dock._move_drag(event)
        elif self.dock._sorting_bookmark is self:
            self.dock._preview_sort(self, event)
        elif self._press and max(abs(event.x_root-self._press[0]),
                                 abs(event.y_root-self._press[1])) >= 5:
            if self.dock.expanded and self.dock._transition_job is None:
                self._cancel_animation()
                self.tip._leave(None)
                self.dock._sorting_bookmark = self
                self.lift()
                self.dock._preview_sort(self, event)
            else:
                self._press = None

    def _drag_release(self, event):
        if self is self.dock:
            self.dock._end_drag(event)
        elif self.dock._sorting_bookmark is self:
            self.dock._finish_sort(self, event)
        elif self._press is not None:
            self._press = None
            self.dock.app.toggle_docked_note(self.note_id)

    def _show_delete_menu(self, event):
        if (self.dock._sorting_bookmark is not None or self.dock._scroll_job is not None
                or self.dock._transition_job is not None):
            return
        self._press = None
        self.tip._leave(None)
        app, note_id = self.dock.app, self.note_id

        def delete():
            note = app.notes.get(note_id)
            if note is not None:
                app.remove_note(note)

        # 复用便签右键菜单的自绘样式；以常驻的收纳入口作父窗口，便签隐藏时也能弹出。
        def right_click_again(e):
            # 菜单已关闭：再次右键本书签只关闭；右键其他书签则切换到它的菜单。
            target = self.dock.winfo_containing(e.x_root, e.y_root)
            other = next((b for b in self.dock.bookmarks.values()
                          if b is not self and b.bar is target), None)
            if other is not None:
                other._show_delete_menu(e)

        # 菜单弹在该书签左侧并与其垂直居中，避免盖住书签本身和相邻书签；
        # 以书签完全左伸后的左缘为准，切换书签时位置一致。
        anchor_x = user32.GetSystemMetrics(0) - self.base_width - self.HOVER_EXTENSION - 6
        NoteWindow._show_context_menu(
            self.dock, anchor_x, self.winfo_rooty() + self.winfo_height() // 2,
            [("删除", delete)], compact=True, on_right_click=right_click_again)

        # 菜单独占鼠标会让书签收到“离开”，这里保持左伸，菜单关闭后再按鼠标位置决定是否缩回。
        menu = getattr(self.dock, "_context_menu", None)
        if menu is None or not menu.winfo_exists():
            return
        self._menu_open, self._hovered = True, True
        self._animate(self.HOVER_EXTENSION)

        def closed(e):
            if e.widget is not menu:
                return
            self._menu_open = False
            if self.winfo_exists() and self.dock.winfo_containing(
                    *self.dock.winfo_pointerxy()) is not self.bar:
                self._hovered = False
                self._animate(0)

        menu.bind("<Destroy>", closed, add="+")

    def hide_bookmark(self):
        self._cancel_animation()
        if self.tip:
            self.tip._leave(None)
        self._hovered, self._extension = False, 0
        self._drawn = None
        self.withdraw()

    def destroy(self):
        self._cancel_animation()
        if self.tip:
            self.tip._leave(None)
        super().destroy()


class DockHideAllButton(DockBookmark):
    """书签组最下面的一键隐藏按钮，复用书签的贴边窗口，点击等同“隐藏所有便签”。"""
    WIDTH = 40
    HOVER_EXTENSION = 0  # 悬停不伸出，只显示提示。

    def __init__(self, dock):
        super().__init__(dock, dock.bookmark_height)
        self.base_width = self.WIDTH
        # 复用书签的悬停提示：标题按“已截断”处理，停留后在左侧显示中文说明。
        self.full_label, self._label_truncated = "收纳所有", True
        # 深色图标与灰字书签区分开。
        self.icon = load_icon(self.bar, "panel-right-close", 20, active=True)

    def _draw(self):
        width = self.base_width + self._extension
        if self._drawn == width:
            return
        self._drawn = width
        self.bar.place_configure(width=width, height=self.height)
        self.bar.delete("all")
        # 左侧有 DWM 阴影，视觉中心偏左，图标右移 2px 补偿。
        self.bar.create_image(width/2+2, self.height/2, image=self.icon,
                              tags="dock-hide-all")

    def _show_delete_menu(self, event):
        pass

    def _idle(self):
        dock = self.dock
        return (dock._transition_job is None and dock._sorting_bookmark is None
                and dock._scroll_job is None)

    def _drag_press(self, event):
        self._press = (event.x_root, event.y_root) if self._idle() else None

    def _drag_move(self, event):
        if self._press and max(abs(event.x_root-self._press[0]),
                               abs(event.y_root-self._press[1])) >= 5:
            self._press = None

    def _drag_release(self, event):
        pressed, self._press = self._press, None
        if pressed is not None and self._idle():
            self.dock.app.hide_all(animate=True)


class SideDock(DockBookmark):
    """主入口和有限数量的独立书签，不使用共同的弹出面板。"""
    ENTRY_GAP = 10
    ROW_GAP = 4
    MAX_BOOKMARKS = 15
    SCROLL_DURATION = 0.12

    def __init__(self, app):
        self.app = app
        label_size = 10
        self.font = tkfont.Font(root=app.root, family=app.note_font_family(), size=label_size)
        self.set_label_font()
        line_height = self.font.metrics("linespace")
        label_width = max(self.font.measure(text) for text in ("z-note", "展开", "收起"))
        self.bar_width = max(88, label_width+42)  # 左侧留给三角，右侧保留文字留白。
        self.bar_height = max(34, line_height+16)
        self.bookmark_width = round(self.bar_width * 1.14)
        self.bookmark_height = max(32, line_height+10)
        self.expanded = False
        self._rows = []
        self.bookmarks = {}
        self._empty_tip = self._empty_tip_job = None
        self._offset = 0
        self._dragged = False
        self._drag_anchor = None
        self._transition_job = None
        self._sorting_bookmark = None
        self._scroll_job = None
        self._scroll_position = 0.0
        super().__init__(self, self.bar_height)
        self.hide_all_button = DockHideAllButton(self)
        self.update_label("z-note")

    def set_label_font(self):
        self.font.configure(family=self.app.note_font_family())
        choice = self.app.data["settings"]["fontFamily"]
        self.label_ink_font = None
        if choice in FONT_FAMILIES:
            pixels = round(self.app.root.winfo_fpixels(f"{self.font.cget('size')}p"))
            self.label_ink_font = ImageFont.truetype(str(FONT_DIR / f"{choice}.ttf"), pixels)
        self.label_baseline_offset = self.font.metrics("ascent") - self.font.metrics("linespace")//2

    @staticmethod
    def work_area():
        work = wintypes.RECT()
        user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0)
        return work

    @staticmethod
    def note_label(note):
        title = (note.get("title") or "").strip()
        if title:
            return title
        for key in ("lines", "lines2"):
            for line in note.get(key) or []:
                for text in line.get("text", "").splitlines():
                    if text.strip():
                        return text.strip()
        return ""

    def _sync_blank_numbers(self):
        data = self.app.data
        settings = data["settings"]
        previous = settings.get("sideDockBlankNumbers", {})
        retained_ids = {note["id"] for key in ("notes", "deletedNotes") for note in data[key]}
        numbers = {}
        if isinstance(previous, dict):
            numbers = {identity: number for identity, number in previous.items()
                       if identity in retained_ids and type(number) is int and number > 0}
        if settings.get("sideDock", True):
            # 只有当前仍显示为“便签 N”的便签占号（含隐藏的）；回收站里的和已有标题、内容的不算。
            # 新编号取占号中的最大值 + 1，一个都没有时从 1 开始；还原或重新变空的便签
            # 原号未被占用时沿用，否则同样取最大值 + 1。
            used, waiting = set(), []
            for note in data["notes"]:
                if self.note_label(note):
                    continue
                number = numbers.get(note["id"])
                if number is not None and number not in used:
                    used.add(number)
                else:
                    waiting.append(note["id"])
            next_number = max(used, default=0) + 1
            for identity in waiting:
                numbers[identity] = next_number
                next_number += 1
        if numbers != previous:
            settings["sideDockBlankNumbers"] = numbers
            self.app.schedule_save()
        return numbers

    def refresh(self):
        settings = self.app.data["settings"]
        remaining = {n["id"]: self.app.notes[n["id"]] for n in self.app.data["notes"]
                     if n["id"] in self.app.notes}
        order = settings.get("sideDockOrder", [])
        keys = order if isinstance(order, list) else ()
        ordered = [remaining.pop(key) for key in keys if isinstance(key, str) and key in remaining]
        active_order = [note.d["id"] for note in ordered]
        if active_order != order:
            settings["sideDockOrder"] = active_order
            self.app.schedule_save()
        self._rows = ordered + list(remaining.values())
        self._sync_blank_numbers()
        if not settings.get("sideDock", True):
            self._hide_empty_tip()
            self._cancel_transition()
            self.expanded = False
            self._offset = 0
            self._clear_bookmarks()
            self.hide_bookmark()
            return
        if self._rows:
            self._hide_empty_tip()
        else:
            self.expanded = False
            self._cancel_transition()
        if self._transition_job is None:
            self._layout(self._drag_anchor if self._dragged and self._press else None)

    def _clear_bookmarks(self):
        self._cancel_scroll()
        self._sorting_bookmark = None
        for window in self.bookmarks.values():
            window.destroy()
        self.bookmarks.clear()
        self.hide_all_button.hide_bookmark()

    def _show_empty_tip(self):
        self._hide_empty_tip()
        self._empty_tip = SideDockTooltip.show_at(self.bar, "暂无便签", beside=True)
        self._empty_tip_job = self.after(1200, self._hide_empty_tip)

    def _hide_empty_tip(self):
        if self._empty_tip_job is not None:
            self.after_cancel(self._empty_tip_job)
            self._empty_tip_job = None
        if self._empty_tip is not None and self._empty_tip is Tooltip._cur:
            Tooltip._hide()
        self._empty_tip = None

    def _leave(self, event=None):
        self._hide_empty_tip()
        super()._leave(event)

    def _layout(self, y=None, *, animate=False):
        self._cancel_scroll()
        if self._sorting_bookmark is not None:
            bookmark = self._sorting_bookmark
            bookmark._press = None
            bookmark._extension = bookmark.HOVER_EXTENSION if bookmark._hovered else 0
        self._sorting_bookmark = None
        self._cancel_transition()
        work = self.work_area()
        entry_gap, row_gap = self.ENTRY_GAP, self.ROW_GAP
        row_step = self.bookmark_height+row_gap
        travel = max(1, work.bottom-work.top-self.bar_height-16)
        ratio = min(1, max(0, self.app.data["settings"].get("sideDockY", 0.5)))
        self._anchor_y = work.top+8+round(travel*ratio) if y is None else y
        if not self.expanded:
            self._bar_y = int(min(work.bottom-8-self.bar_height, max(work.top+8, self._anchor_y)))
            if animate:
                windows = list(self.bookmarks.values())
                if self.hide_all_button.winfo_ismapped():
                    windows.append(self.hide_all_button)
                self._animate_layout([(window, self._bar_y) for window in windows])
                return
            self._offset = 0
            self._clear_bookmarks()
            self.place_at(self._bar_y)
            return
        # 一键隐藏按钮固定占书签组最下面一行；按钮出现或消失时书签容量和位置都不变。
        button = self.hide_all_button
        button_room = entry_gap+button.height
        room = work.bottom-work.top-16-self.bar_height-entry_gap-button_room
        self._capacity = max(1, min(self.MAX_BOOKMARKS, (room+row_gap)//row_step))
        self._offset = min(max(0, self._offset), max(0, len(self._rows)-self._capacity))
        page = self._rows[self._offset:self._offset+self._capacity]
        group_height = self.bar_height+entry_gap+len(page)*row_step-row_gap+button_room
        self._bar_y = int(max(work.top+8, min(self._anchor_y, work.bottom-8-group_height)))
        if y is not None:
            self._anchor_y = self._bar_y
        visible_ids = {note.d["id"] for note in page}
        for identity in list(self.bookmarks):
            if identity not in visible_ids:
                self.bookmarks.pop(identity).destroy()
        next_y = self._bar_y+self.bar_height+entry_gap
        slots = []
        for note in page:
            slots.append((self._bookmark_for(note), next_y))
            next_y += row_step
        if any(note.d.get("visible", True) for note in self._rows):
            slots.append((button, next_y-row_gap+entry_gap))
        else:
            button.hide_bookmark()
        if animate:
            self._animate_layout(slots)
        else:
            batch = []
            # 拖动主入口时窗口层级不变，已显示的书签不必每次移动都重新置顶。
            lift = y is None
            self.place_at(self._bar_y, batch, lift=lift)
            for window, target in slots:
                window.set_motion_opacity(1)
                window.place_at(target, batch, lift=lift)
            self._commit_moves(batch)

    @staticmethod
    def _commit_moves(batch):
        """一次提交多个书签窗口的位置：逐个经 Tk 移动十几个窗口约 19ms，批量提交约 2ms。"""
        if not batch:
            return
        handle = user32.BeginDeferWindowPos(len(batch))
        for window, (width, height, x, y) in batch:
            if handle:
                hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
                handle = user32.DeferWindowPos(handle, hwnd, None, x, y, width, height, 0x0004 | 0x0010)
        if not handle or not user32.EndDeferWindowPos(handle):
            for window, geometry in batch:  # 批量提交失败时退回逐个移动。
                window.geometry("%dx%d+%d+%d" % geometry)

    def _bookmark_for(self, note):
        identity = note.d["id"]
        if identity not in self.bookmarks:
            self.bookmarks[identity] = DockBookmark(self, self.bookmark_height, identity)
        window = self.bookmarks[identity]
        label = self.note_label(note.d)
        compact_label = None
        if not label:
            number = self.app.data["settings"].get("sideDockBlankNumbers", {}).get(identity)
            if number is None:
                # A scroll or animation can draw before the queued editor refresh.
                number = self._sync_blank_numbers()[identity]
            label, compact_label = f"便签 {number}", f"#{number}"
        window.update_label(label, note.d.get("visible", True), compact_label=compact_label)
        return window

    def _cancel_transition(self):
        if self._transition_job is not None:
            self.after_cancel(self._transition_job)
            self._transition_job = None

    def _animate_layout(self, slots):
        opening = self.expanded
        bar_start, bar_target = self._position_y, self._bar_y
        motion = []
        for window, target in slots:
            if not window.winfo_ismapped():
                window.set_motion_opacity(0)
                window.place_at(bar_start)
            motion.append((window, window._position_y, target, window.attributes("-alpha")))
        self.lift()
        self._draw()
        started = time.perf_counter()
        duration = 0.21 if opening else 0.16
        opacity_target = 1 if opening else 0

        def frame():
            self._transition_job = None
            elapsed = (time.perf_counter()-started)/duration
            if elapsed >= 1:
                self._layout()
                return
            # 展开先拉开间距再减速，收起时向入口加速并淡出。
            progress = 1-(1-elapsed)**3 if opening else elapsed**2
            batch = []
            self.move_to(bar_start+(bar_target-bar_start)*progress, batch=batch)
            for window, origin, target, opacity in motion:
                window.set_motion_opacity(opacity+(opacity_target-opacity)*progress)
                window.move_to(origin+(target-origin)*progress, batch=batch)
            self._commit_moves(batch)
            self._transition_job = self.after(VISIBILITY_TICK_MS, frame)

        self._transition_job = self.after(VISIBILITY_TICK_MS, frame)

    def _wheel(self, event):
        if (not self.expanded or not event.delta or self._press or self._sorting_bookmark
                or any(window._press for window in self.bookmarks.values())):
            return "break"
        steps = -int(event.delta/120) or (-1 if event.delta > 0 else 1)
        target = min(max(0, self._offset+steps), max(0, len(self._rows)-self._capacity))
        if target == self._offset:
            return "break"
        if self._scroll_job is None:
            if self._transition_job is not None:
                self._layout()
            for window in self.bookmarks.values():
                window._cancel_animation()
                window.tip._leave(None)
            self._scroll_position = self._offset
            self._scroll_pixels = self._offset*(self.bookmark_height+self.ROW_GAP)
            self._scroll_job = self.after(VISIBILITY_TICK_MS, self._scroll_frame)
        self._scroll_origin = self._scroll_position
        self._scroll_started = time.perf_counter()
        self._offset = target
        return "break"

    def _scroll_frame(self):
        def continue_scroll():
            if self._scroll_position == self._offset:
                self._scroll_job = None
                self._restore_scroll_hover()
            else:
                self._scroll_job = self.after(VISIBILITY_TICK_MS, self._scroll_frame)

        elapsed = min(1.0, (time.perf_counter()-self._scroll_started)/self.SCROLL_DURATION)
        progress = 1-(1-elapsed)**3
        self._scroll_position = self._scroll_origin+(self._offset-self._scroll_origin)*progress
        if elapsed == 1:
            self._scroll_position = self._offset
        step = self.bookmark_height+self.ROW_GAP
        shift = round(self._scroll_position*step)
        if shift == self._scroll_pixels:
            continue_scroll()
            return
        self._scroll_pixels = shift
        first = shift//step
        top = self._bar_y+self.bar_height+self.ENTRY_GAP
        bottom = top+self._capacity*step-self.ROW_GAP
        viewport = top, bottom
        visible = set()
        entering = []
        batch = []
        for i, note in enumerate(self._rows[first:first+self._capacity+1], start=first):
            y = top+i*step-shift
            if y >= bottom or y+self.bookmark_height <= top:
                continue
            identity = note.d["id"]
            visible.add(identity)
            window = self.bookmarks.get(identity)
            if window is None:
                entering.append((note, y))
            else:
                window.move_to(y, viewport=viewport, batch=batch)
        self._commit_moves(batch)

        def commit():
            for identity in self.bookmarks.keys()-visible:
                self.bookmarks.pop(identity).destroy()
            for note, y in entering:
                window = self._bookmark_for(note)
                window.lift()
                window.move_to(y, viewport=viewport)
                window.deiconify()
            continue_scroll()

        # 先让 Tk 提交已映射窗口的几何位置，再显示新滑入的窗口。
        self._scroll_job = self.after_idle(commit)

    def _cancel_scroll(self):
        if self._scroll_job is not None:
            self.after_cancel(self._scroll_job)
            self._scroll_job = None
            for window in self.bookmarks.values():
                window._extension = window.HOVER_EXTENSION if window._hovered else 0

    def _restore_scroll_hover(self):
        under_pointer = self.winfo_containing(*self.winfo_pointerxy())
        for window in self.bookmarks.values():
            window.tip._cancel()
            window._hovered = under_pointer is window.bar
            window._animate(window.HOVER_EXTENSION if window._hovered else 0)
            if window._hovered:
                window.tip._enter(None)

    def _page_at_drop(self, bookmark, event):
        page = self._rows[self._offset:self._offset+self._capacity]
        first_y = self._bar_y+self.bar_height+self.ENTRY_GAP
        step = self.bookmark_height+self.ROW_GAP
        y = event.y_root-first_y
        edge = user32.GetSystemMetrics(0)
        left = edge-self.bookmark_width-self.HOVER_EXTENSION
        if left <= event.x_root < edge and 0 <= y < len(page)*step-self.ROW_GAP:
            source = next(i for i, note in enumerate(page) if note.d["id"] == bookmark.note_id)
            page.insert(y//step, page.pop(source))
        return page

    def _preview_sort(self, bookmark, event):
        first_y = self._bar_y+self.bar_height+self.ENTRY_GAP
        step = self.bookmark_height+self.ROW_GAP
        batch = []
        for i, note in enumerate(self._page_at_drop(bookmark, event)):
            window = self.bookmarks[note.d["id"]]
            y = first_y+i*step
            if window is not bookmark and window._position_y != y:
                window.move_to(y, batch=batch)
        _, _, offset_y = bookmark._press
        bookmark.move_to(max(0, event.y_root-offset_y), batch=batch)
        self._commit_moves(batch)

    def _finish_sort(self, bookmark, event):
        page = self._page_at_drop(bookmark, event)
        visible = slice(self._offset, self._offset+len(page))
        if page == self._rows[visible]:
            self._layout()
            return
        rows = self._rows.copy()
        rows[visible] = page
        self.app.data["settings"]["sideDockOrder"] = [note.d["id"] for note in rows]
        self.refresh()
        self.app.schedule_save()

    def _start_drag(self, event):
        if self._scroll_job is not None:
            self._layout()
        self._press = (event.x_root, event.y_root, self._position_y)
        self._dragged = False
        self._drag_anchor = None

    def _move_drag(self, event):
        if self._press is None:
            return
        x, y, origin = self._press
        if not self._dragged and max(abs(event.x_root-x), abs(event.y_root-y)) >= 5:
            self._dragged = True
            self._hide_empty_tip()
        if self._dragged:
            self._layout(origin+event.y_root-y)
            self._drag_anchor = self._anchor_y

    def _end_drag(self, event):
        if self._press is None:
            return
        self._press = None
        if self._dragged:
            work = self.work_area()
            travel = max(1, work.bottom-work.top-self.bar_height-16)
            self.app.data["settings"]["sideDockY"] = min(1, max(0, (self._anchor_y-work.top-8)/travel))
            self.app.schedule_save()
            self._dragged = False
            self._drag_anchor = None
        elif not self._rows:
            self._show_empty_tip()
        else:
            self.expanded = not self.expanded
            self._layout(animate=True)

    def destroy(self):
        self._hide_empty_tip()
        self._cancel_transition()
        self._clear_bookmarks()
        self.hide_all_button.destroy()
        super().destroy()


@dataclass
class _NoteTransition:
    rect: wintypes.RECT
    layered: bool
    visible: bool
    start: tuple
    target: tuple
    current: tuple
    began: float
    duration: float


class NoteVisibilityMotion:
    """One UI timer for all note fades, independent of their saved geometry."""
    SINGLE_DURATION = 0.16
    LOCAL_DURATION = 0.15
    _LAYERED = 0x00080000

    def __init__(self, root):
        self.root = root
        self._active = {}
        self._job = None

    @staticmethod
    def enabled():
        allowed = wintypes.BOOL()
        return bool(user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(allowed), 0) and allowed.value)

    @staticmethod
    def _offset(rect, bookmark):
        if bookmark is None or not bookmark.bar.winfo_ismapped():
            return 0, 0
        bar = bookmark.bar
        x, y = (rect.left+rect.right)//2, (rect.top+rect.bottom)//2
        bx, by = bar.winfo_rootx()+bar.winfo_width()//2, bar.winfo_rooty()+bar.winfo_height()//2
        monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 0)
        if not monitor or monitor != user32.MonitorFromPoint(wintypes.POINT(bx, by), 0):
            return 0, 0

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return 0, 0
        work = info.work
        if (rect.left < work.left or rect.right > work.right
                or rect.top < work.top or rect.bottom > work.bottom):
            return 0, 0
        dx, dy = bx-x, by-y
        scale = 24 / max(24, math.hypot(dx, dy))
        return (min(work.right-rect.right, max(work.left-rect.left, int(dx*scale))),
                min(work.bottom-rect.bottom, max(work.top-rect.top, int(dy*scale))))

    @classmethod
    def _alpha(cls, note, opacity):
        style = user32.GetWindowLongPtrW(note.hwnd, -20)
        if not style & cls._LAYERED:
            user32.SetWindowLongPtrW(note.hwnd, -20, style | cls._LAYERED)
        return user32.SetLayeredWindowAttributes(note.hwnd, 0, round(opacity*255), 2)

    @staticmethod
    def _repaint(note):
        # 切换分层样式或在分层状态下映射后，系统不会补画所有子窗口（如底栏状态文字），
        # 缺画处会透出桌面。按 Win32 文档整窗连同子窗口重绘一次：
        # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW | RDW_FRAME。
        user32.RedrawWindow(note.hwnd, None, None, 0x0585)

    @staticmethod
    def _move(note, x, y):
        return user32.SetWindowPos(note.hwnd, None, x, y, 0, 0, SWP_NOSIZE | 0x0004 | 0x0010)

    def _apply_frame(self, note, motion, frame):
        if not self._alpha(note, frame[2]):
            return False
        if frame[:2] != motion.current[:2] and not self._move(note, *frame[:2]):
            return False
        motion.current = frame
        return True

    def change(self, note, visible, *, duration, bookmark=None, temporary_raise=False, first_show=False,
               show_window=True):
        # show_window=False：窗口已按原逻辑显示，只淡入透明度（启动时使用）。
        def apply_now():
            if visible:
                if first_show:
                    self._restore_alpha(note, layered=False)
                if show_window:
                    note.show(temporary_raise=temporary_raise)
            else:
                note.hide()

        if not duration or note._resizing or note._dragging or note._ime_active:
            apply_now()
            return
        previous = self._active.get(note)
        if not first_show and note.d.get("visible", True) == visible:
            if visible and previous is None:
                apply_now()  # 一键显示仍按原规则前移已显示的便签。
            return

        if previous is None:
            rect = wintypes.RECT()
            if not user32.GetWindowRect(note.hwnd, ctypes.byref(rect)):
                apply_now()
                return
            # 新建窗口在首次绘制前设置的 layered 仅用于这次淡入。
            layered = not first_show and bool(user32.GetWindowLongPtrW(note.hwnd, -20) & self._LAYERED)
        else:
            rect, layered = previous.rect, previous.layered
        x, y = rect.left, rect.top
        dx, dy = self._offset(rect, bookmark)
        opacity = note.d["opacity"]
        current = previous.current if previous else (x, y, 0 if visible else opacity)
        start = (x+dx, y+dy, 0) if visible and previous is None else current
        target = (x, y, opacity) if visible else (x+dx, y+dy, 0)
        motion = _NoteTransition(rect, layered, visible, start, target, current, time.perf_counter(), duration)

        # show/hide 本身会清理旧动画，反向时先取出旧状态，再调用显示。
        self._active.pop(note, None)
        if visible:
            note.attributes("-alpha", start[2])
            if not self._alpha(note, start[2]):
                self._active[note] = motion
                self.finish(note, apply_target=False)
                apply_now()
                return
            if show_window:
                note.show(temporary_raise=temporary_raise)
            self._repaint(note)
        else:
            note._cancel_dock_reveal()
            note.d["visible"] = False
        self._active[note] = motion
        if not self._apply_frame(note, motion, start):
            self.finish(note)
            return
        if self._job is None:
            self._job = self.root.after(VISIBILITY_TICK_MS, self._tick)
        return motion

    def _tick(self):
        self._job = None
        now = time.perf_counter()
        for note, motion in list(self._active.items()):
            elapsed = (now-motion.began)/motion.duration
            if elapsed >= 1:
                self.finish(note)
                continue
            progress = elapsed * elapsed * (3 - 2 * elapsed)
            x, y, opacity = (a+(b-a)*progress for a, b in zip(motion.start, motion.target))
            if not self._apply_frame(note, motion, (round(x), round(y), opacity)):
                self.finish(note)
        if self._active:
            self._job = self.root.after(VISIBILITY_TICK_MS, self._tick)

    def finish(self, note, *, apply_target=True):
        motion = self._active.pop(note, None)
        if motion is None:
            return None
        if not self._active and self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
        hiding = apply_target and not motion.visible
        if hiding:
            self._alpha(note, 0)
        origin = motion.rect.left, motion.rect.top
        if motion.current[:2] != origin and not self._move(note, *origin):
            note.geometry(f"+{origin[0]}+{origin[1]}")
        if hiding:
            note.hide()
        self._restore_alpha(note, motion.layered)
        return origin

    def _restore_alpha(self, note, layered):
        opacity = note.d["opacity"]
        note.attributes("-alpha", opacity)
        style = user32.GetWindowLongPtrW(note.hwnd, -20)
        if opacity < 1 and not style & self._LAYERED:
            # Tk 记着曾设过分层样式，原生移除后不会再补上，半透明需要它。
            style |= self._LAYERED
            user32.SetWindowLongPtrW(note.hwnd, -20, style)
        if style & self._LAYERED:
            user32.SetLayeredWindowAttributes(note.hwnd, 0, round(opacity*255), 2)
            if opacity == 1 and not layered:
                user32.SetWindowLongPtrW(note.hwnd, -20, style & ~self._LAYERED)
                self._repaint(note)

    def finish_all(self):
        return {note: self.finish(note) for note in tuple(self._active)}


class PopupFade:
    """设置与回收站窗口的淡入淡出，时长和缓动与一键显示便签一致。"""
    DURATION = NoteVisibilityMotion.LOCAL_DURATION
    _LAYERED = 0x00080000

    @staticmethod
    def _hwnd(window):
        return user32.GetParent(window.winfo_id()) or window.winfo_id()

    @classmethod
    def _set_alpha(cls, window, opacity):
        hwnd = cls._hwnd(window)
        style = user32.GetWindowLongPtrW(hwnd, -20)
        if not style & cls._LAYERED:
            user32.SetWindowLongPtrW(hwnd, -20, style | cls._LAYERED)
        user32.SetLayeredWindowAttributes(hwnd, 0, round(opacity*255), 2)
        window._popup_alpha = opacity

    @staticmethod
    def cancel(window):
        job = getattr(window, "_popup_fade_job", None)
        if job is not None:
            window.after_cancel(job)
            window._popup_fade_job = None
        return job is not None

    @classmethod
    def _run(cls, window, target, done):
        start = getattr(window, "_popup_alpha", 1.0)
        cls.cancel(window)
        if not NoteVisibilityMotion.enabled() or start == target:
            done()
            return
        began = time.perf_counter()

        def frame():
            window._popup_fade_job = None
            if not window.winfo_exists():
                return
            t = min(1.0, (time.perf_counter()-began)/cls.DURATION)
            cls._set_alpha(window, start+(target-start)*t*t*(3-2*t))
            if t < 1:
                window._popup_fade_job = window.after(VISIBILITY_TICK_MS, frame)
            else:
                done()

        frame()

    @classmethod
    def show(cls, window):
        """首次显示（窗口已在透明状态下映射）或重新打开已隐藏、最小化的窗口。"""
        fading = cls.cancel(window)
        if window.state() != "normal":
            if not fading:
                cls._set_alpha(window, 0.0)
            window.deiconify()
        elif not fading and getattr(window, "_popup_alpha", 1.0) == 1.0:
            return

        def done():
            if not window.winfo_exists():
                return
            if float(window.attributes("-alpha")) != 1.0:
                window.attributes("-alpha", 1.0)
            hwnd = cls._hwnd(window)
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)
            style = user32.GetWindowLongPtrW(hwnd, -20)
            user32.SetWindowLongPtrW(hwnd, -20, style & ~cls._LAYERED)
            window._popup_alpha = 1.0

        cls._run(window, 1.0, done)

    @classmethod
    def hide(cls, window, then):
        """淡出后执行 then（隐藏或销毁）；淡出途中再次打开会反向淡入。"""
        def done():
            if window.winfo_exists():
                then()

        cls._run(window, 0.0, done)


class RoundMessageBox:
    """代替系统消息框：圆角白底、浅灰边框按钮、淡入淡出，与设置和回收站同一风格。

    接口与 tkinter.messagebox 的 askyesno / showinfo / showerror / showwarning 相同。
    """

    @staticmethod
    def _button_image(master, font, text, fill):
        width = max(76, font.measure(text) + 32)
        height = font.metrics("linespace") + 14
        image = Image.new("RGBA", (width*4, height*4))
        ImageDraw.Draw(image).rounded_rectangle((2, 2, width*4-3, height*4-3), radius=32,
                                                fill=fill, outline=NOTE_BORDER, width=4)
        return ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=master)

    @classmethod
    def _ask(cls, title, message, buttons, parent=None):
        load_fonts()
        master = parent if parent is not None and parent.winfo_exists() else tk._default_root
        win = tk.Toplevel(master)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=NOTE_BG)
        win.attributes("-topmost", True)
        _round_popup(win)
        scale = max(1, round(win.winfo_fpixels("1i")) / 96)
        body = tk.Frame(win, bg=NOTE_BG, padx=24, pady=20)
        body.pack(fill="both", expand=True)
        # 固定内容宽度，短句也保持舒展的版面，换行按缩放后的像素计算。
        tk.Frame(body, bg=NOTE_BG, width=round(340 * scale), height=0).pack()
        tk.Label(body, text=title, bg=NOTE_BG, fg="#1F2328", font=(UI_FONT, 11, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(body, text=message, bg=NOTE_BG, fg="#2B2F36", font=(UI_FONT, 10),
                 justify="left", anchor="w", wraplength=round(340 * scale)).pack(fill="x", pady=(10, 18))
        row = tk.Frame(body, bg=NOTE_BG)
        row.pack(fill="x")
        font = tkfont.Font(root=win, family=UI_FONT, size=10)
        result, closing = [None], [False]

        def choose(index):
            if closing[0]:
                return
            closing[0], result[0] = True, index
            win.grab_release()
            PopupFade.hide(win, win.destroy)

        win._images = []
        # 第一个按钮是主操作：放在最右、浅灰底，回车确认；Esc 取消。
        for index, text in enumerate(buttons):
            primary = index == 0
            image = cls._button_image(win, font, text, "#E9EDF2" if primary else NOTE_BG)
            win._images.append(image)
            tk.Button(row, text=text, image=image, compound="center", command=lambda i=index: choose(i),
                      bg=NOTE_BG, activebackground=NOTE_BG, fg="#1F2328" if primary else "#475569",
                      activeforeground="#1F2328", font=font, relief="flat", bd=0, padx=0, pady=0,
                      highlightthickness=0, cursor="hand2").pack(side="right", padx=(8, 0))
        win.bind("<Escape>", lambda _e: choose(None))
        win.bind("<Return>", lambda _e: choose(0))

        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        if parent is not None and parent.winfo_exists() and parent.winfo_viewable():
            cx = parent.winfo_rootx() + parent.winfo_width()//2
            cy = parent.winfo_rooty() + parent.winfo_height()//2
        else:
            work = SettingsWindow._work_area(win)
            cx, cy = (work.left+work.right)//2, (work.top+work.bottom)//2
        win.geometry(f"+{cx - width//2}+{cy - height//2}")
        win.attributes("-alpha", 0.0)
        win._popup_alpha = 0.0
        win.deiconify()
        win.update_idletasks()
        PopupFade.show(win)
        win.focus_force()
        win.grab_set()
        master.wait_window(win)
        return result[0]

    @classmethod
    def askyesno(cls, title, message, parent=None, **_options):
        return cls._ask(title, message, ("确定", "取消"), parent) == 0

    @classmethod
    def showinfo(cls, title, message, parent=None, **_options):
        cls._ask(title, message, ("知道了",), parent)
        return "ok"

    showerror = showwarning = showinfo


# 现有调用处与测试沿用 messagebox.askyesno / showinfo 等名字，统一改用圆角对话框。
messagebox = RoundMessageBox


class App:
    def __init__(self):
        load_fonts()
        first_launch = not os.path.exists(DATA_FILE)
        self.data = load_data()
        settings = self.data["settings"]
        if settings["autostart"]:
            try:
                if not autostart_registered():
                    set_autostart(True)
            except OSError:
                # 注册表暂时不可写时，保留用户选择，下次启动再尝试。
                pass
        settings.setdefault("sideDock", True)
        settings.setdefault("sideDockY", 0.5)
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
        self._last_update_check = None
        self._update_check_cancel = None
        self._update_check_succeeded = False
        self._available_update = None
        self._bottom_refresh_pending = False
        self._bottom_hooks = []
        self._quitting = False
        self.root = tk.Tk()
        self._note_motion = NoteVisibilityMotion(self.root)
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
        global UI_FONT
        UI_FONT = self.note_font_family()
        for name in ("TkDefaultFont", "TkMenuFont"):
            tkfont.nametofont(name, root=self.root).configure(family=UI_FONT)
        self.notes = {}
        self.dock = None
        self._dock_refresh_job = None
        self._save_job = None
        self._cascade = 0
        self.settings_win = None
        self.show_notes = self.data["settings"]["showOnStartup"]

        self.base_font = tkfont.Font(family=self.note_font_family(),
                                     size=self.data["settings"]["fontSize"])

        self._restore_notes(show_welcome=first_launch)
        self.dock = SideDock(self)
        self.dock.refresh()
        self._start_bottom_guard()
        if migrate_opacity or migrate_z:
            self.save_now()
        self._start_tray()
        self._start_hotkeys()
        self.root.after(PUMP_MS, self._pump)
        self.root.after(10_000, lambda: self._last_update_check is None and self._check_for_update())
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

    # 便签管理
    def _restore_notes(self, *, show_welcome=False):
        if show_welcome:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.data["notes"].append({
                "id": str(uuid.uuid4()), "x": sw - 480, "y": 90, "w": 440, "h": 580,
                "title": "使用指南",
                "z": "top", "opacity": self.data["settings"]["opacity"],
                "color": self.data["settings"]["color"], "visible": True,
                "lines": [
                    {"type": "text", "text": "快速上手", "checked": False},
                ] + [{"type": kind, "text": text, "checked": False} for kind, text in (
                    ("todo", "勾选这条待办"),
                    ("text", "底部加号：添加待办"),
                    ("text", "顶部减号：隐藏便签"),
                    ("text", "右侧书签：找回便签"),
                    ("text", ""),
                    ("text", "其他操作"),
                    ("text", f"{self.hotkey_text('convert')} 切换待办"),
                    ("text", "右键编辑 · 拖动排序"),
                    ("text", "点标题改名"),
                    ("text", "托盘可显示全部便签"),
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
            # 构造期间窗口会先映射再设圆角、边框和层级，保持透明直到全部就绪。
            w = NoteWindow(self, d, start_transparent=True)
            self.notes[d["id"]] = w
        if self.show_notes:
            self.show_all()
        self.root.after_idle(self._reveal_startup_notes)

    def _reveal_startup_notes(self):
        """显示与层级已按原逻辑处理，这里只把透明度统一淡入，效果与一键显示相同。"""
        self.root.update_idletasks()
        duration = NoteVisibilityMotion.LOCAL_DURATION if self._note_motion.enabled() else 0
        motions = []
        for note in self.notes.values():
            if not note.winfo_exists():
                continue
            if note.d.get("visible", True):
                motion = self._note_motion.change(note, True, duration=duration,
                                                  first_show=True, show_window=False)
                if motion is not None:
                    motions.append(motion)
            else:
                self._note_motion._restore_alpha(note, layered=False)
        began = time.perf_counter()
        for motion in motions:
            motion.began = began

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
        self._retarget_ui_fonts(family)

    def _retarget_ui_fonts(self, family):
        """把已打开窗口里的界面文字换成新字体；之后新建的菜单、提示和窗口直接读 UI_FONT。"""
        global UI_FONT
        if UI_FONT == family:
            return
        old = UI_FONT
        UI_FONT = family
        call = self.root.tk.call
        if self.dock is not None:
            self.dock.set_label_font()
            self.request_dock_refresh()
        for name in tkfont.names(self.root):
            if call("font", "configure", name, "-family") == old:
                call("font", "configure", name, "-family", family)

        def convert(spec):
            parts = list(self.root.tk.splitlist(spec)) if spec else []
            if not parts or parts[0] != old:
                return None
            parts[0] = family
            return tuple(parts)

        pending = [self.root]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, tk.Canvas):
                for item in widget.find_all():
                    if widget.type(item) == "text":
                        font = convert(widget.itemcget(item, "font"))
                        if font:
                            widget.itemconfigure(item, font=font)
            elif "font" in widget.keys():
                font = convert(widget.cget("font"))
                if font:
                    widget.configure(font=font)
        Tooltip._font = None
        for note in self.notes.values():
            note._footer_content_size = None
            note._layout_footer()
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.font_selector._fit()
            self.settings_win._fit_hotkey_boxes()

    def set_opacity(self, percent):
        self._note_motion.finish_all()
        percent = min(100, max(50, 10 * round(percent / 10)))
        value = percent / 100
        self.data["settings"]["opacity"] = value
        for note in self.notes.values():
            note.d["opacity"] = value
            if note._resizing:
                note._resize_dirty = True
            else:
                self._note_motion._restore_alpha(note, layered=False)
        scale = getattr(self, "opacity_scale", None)
        if scale is not None and scale.winfo_exists() and scale.get() != 100 - percent:
            scale.set(100 - percent)
        self.schedule_save()

    def new_note(self, *, animate=False, position=None):
        fade = animate and self._note_motion.enabled()
        if position is None:
            cx, cy = cursor_pos()
            offset = 24 * (self._cascade % 6)
            self._cascade += 1
            position = cx + offset, cy + offset
        x, y = position
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        d = {"id": str(uuid.uuid4()),
             "x": min(x, sw - 240), "y": min(y, sh - 200),
             "w": 440, "h": 477, "z": "normal",
             "opacity": self.data["settings"]["opacity"],
             "color": self.data["settings"]["color"], "visible": True,
             "editedAt": datetime.now().isoformat(timespec="microseconds"),
             "lines": [{"type": "todo", "text": "", "checked": False}]}
        self.data["notes"].append(d)
        w = NoteWindow(self, d, start_transparent=fade)
        self.notes[d["id"]] = w
        if fade:
            self._note_motion.change(w, True, duration=NoteVisibilityMotion.LOCAL_DURATION, first_show=True)
        else:
            w.show()
        if animate:
            w.text.focus_force()
        else:
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
        self._note_motion.finish(w)
        w._sync_lines_from_text()
        identity = w.d["id"]
        order = self.data["settings"].get("sideDockOrder", [])
        new_order = [key for key in order if key != identity] if isinstance(order, list) else order
        def change(data):
            note = next(n for n in data["notes"] if n["id"] == identity)
            note["deletedAt"] = datetime.now().isoformat(timespec="seconds")
            data["deletedNotes"].append(note)
            data["notes"].remove(note)
            if new_order != order:
                data["settings"]["sideDockOrder"] = new_order
        if self._store_history(change):
            if new_order != order:
                self.data["settings"]["sideDockOrder"] = new_order
            self.notes.pop(identity, None)
            w.destroy()
            self.request_dock_refresh()

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

    def hide_all(self, *, animate=False):
        if animate and self._note_motion.enabled():
            self._animate_all(False)
        else:
            for w in self.notes.values():
                w.hide()
        self.schedule_save()

    def _animate_all(self, visible, temporary_raise=False):
        motions = []
        for note in self.notes.values():
            motion = self._note_motion.change(note, visible, duration=NoteVisibilityMotion.LOCAL_DURATION,
                                              temporary_raise=temporary_raise)
            if motion is not None:
                motions.append(motion)
        # 等这一批窗口都准备好，再统一开始计时。
        began = time.perf_counter()
        for motion in motions:
            motion.began = began
        self.request_dock_refresh()

    def request_dock_refresh(self):
        if (getattr(self, "dock", None) is not None
                and not getattr(self, "_quitting", False)
                and self._dock_refresh_job is None):
            self._dock_refresh_job = self.root.after_idle(self._refresh_dock)

    def _refresh_dock(self):
        self._dock_refresh_job = None
        self.dock.refresh()

    def toggle_docked_note(self, note_id):
        note = self.notes.get(note_id)
        if note is not None:
            self.animate_note_visibility(note, not note.d.get("visible", True))

    def animate_note_visibility(self, note, visible):
        bookmark = None
        if self.dock is not None and self.dock.expanded and self.data["settings"].get("sideDock", True):
            bookmark = self.dock.bookmarks.get(note.d["id"])
            if bookmark is not None and not bookmark.attributes("-alpha"):
                bookmark = None
        duration = NoteVisibilityMotion.SINGLE_DURATION if self._note_motion.enabled() else 0
        self._note_motion.change(note, visible, duration=duration,
                                 bookmark=bookmark, temporary_raise=visible)
        if visible:
            user32.SetForegroundWindow(note.hwnd)
        self.schedule_save()
        self.request_dock_refresh()

    def toggle_side_dock(self):
        self._note_motion.finish_all()
        enabled = not self.data["settings"].get("sideDock", True)
        self.data["settings"]["sideDock"] = enabled
        for note in self.notes.values():
            note._cancel_dock_reveal()
            if not enabled:
                note.reveal_from_dock_mode()
        self.schedule_save()
        self.request_dock_refresh()
        self.icon.update_menu()

    def show_all(self, temporary_raise=True, *, animate=False):
        if animate and self._note_motion.enabled():
            self._animate_all(True, temporary_raise)
        else:
            for w in self.notes.values():
                w.show(temporary_raise=temporary_raise)
        if temporary_raise and self.notes:
            user32.SetForegroundWindow(next(reversed(self.notes.values())).hwnd)
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
                    if getattr(note, "_dock_reveal_job", None) is None:
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
            pystray.MenuItem("收纳模式", lambda: self.q.put("toggle_side_dock"),
                             checked=lambda item: self.data["settings"].get("sideDock", True)),
            pystray.MenuItem("回收站...", lambda: self.q.put("history")),
            pystray.MenuItem(lambda _item: "设置（有更新）…" if self._available_update else "设置…",
                             lambda: self.q.put("settings")),
            pystray.MenuItem("退出", lambda: self.q.put("quit")),
        )
        self.icon = pystray.Icon(APP_NAME, _make_tray_icon(), "z-note 便签", menu)
        self.icon.run_detached()

    def _check_for_update(self, *, manual=False):
        if self._quitting or self._update_check_cancel is not None:
            return
        now = time.monotonic()
        if not manual and self._last_update_check is not None and now - self._last_update_check < 86_400:
            return
        self._last_update_check = now
        cancel = self._update_check_cancel = threading.Event()
        require_confirmed_no_update = self._available_update is not None

        def work():
            try:
                value = updater.check_update(cancel, require_confirmed_no_update=require_confirmed_no_update)
                self.q.put(("update_check", cancel, "checked", value))
            except updater.Cancelled:
                self.q.put(("update_check", cancel, "cancelled", "已取消更新。"))
            except Exception as exc:
                self.q.put(("update_check", cancel, "error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _receive_update_check(self, cancel, kind, value):
        if cancel is not self._update_check_cancel:
            return
        self._update_check_cancel = None
        if self._quitting or cancel.is_set():
            return
        if kind == "checked":
            had_update = self._available_update is not None
            self._available_update = value
            self._update_check_succeeded = True
            icon = getattr(self, "icon", None)
            if icon is not None and had_update != (value is not None):
                icon.update_menu()
        window = self.settings_win
        if window is not None and window.winfo_exists():
            window._receive_update_check(kind, value)

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
                    elif action == "new":
                        self.q.put(("new_at_cursor", cursor_pos()))
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
                elif isinstance(act, tuple) and act[0] == "update_check":
                    self._receive_update_check(*act[1:])
                elif isinstance(act, tuple) and act[0] == "capture_hotkey":
                    _, window, action, value = act
                    if window.winfo_exists():
                        window._apply_hotkey(action, value)
                elif isinstance(act, tuple) and act[0] == "new_at_cursor":
                    self.new_note(animate=True, position=act[1])
                elif act == "new":
                    self.new_note(animate=True)
                elif act == "hide":
                    self.hide_all(animate=True)
                elif act == "show":
                    self.show_all(animate=True)
                elif act == "toggle_side_dock":
                    self.toggle_side_dock()
                elif act == "refresh_bottom":
                    self._refresh_bottom_notes()
                elif act == "history":
                    if self.history_win is None or not self.history_win.winfo_exists():
                        self.history_win = HistoryWindow(self)
                    else:
                        self.history_win.refresh(if_changed=True)
                        PopupFade.show(self.history_win)
                    self.history_win.lift()
                    self.history_win.focus_force()
                elif act == "settings":
                    created = self.settings_win is None or not self.settings_win.winfo_exists()
                    if created:
                        self.settings_win = SettingsWindow(self)
                    self._check_for_update()
                    win = self.settings_win
                    win.attributes("-topmost", True)
                    if created:
                        win.deiconify()
                    else:
                        PopupFade.show(win)
                    win.lift()
                    win.focus_force()
                    self.root.after(300, lambda: win.winfo_exists() and win.attributes("-topmost", False))
                elif act == "quit":
                    self.quit()
        except queue.Empty:
            pass
        self.root.after(PUMP_MS, self._pump)

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
        command = [str(path)]
        if getattr(sys, "frozen", False):
            command.extend(("/SP-", "/SILENT", "/NORESTART", "/ZNOTEUPDATE=1",
                            f"/DIR={Path(sys.executable).parent}"))
        # Setup's post-install launch must not reuse this process's _MEI directory.
        install_env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        subprocess.Popen(command, cwd=str(path.parent), env=install_env)
        if self.settings_win is not None:
            self.settings_win._update_installing = True
        self.quit(saved=True)

    def _save_positions(self):
        motion = getattr(self, "_note_motion", None)
        origins = motion.finish_all() if motion is not None else {}
        for w in self.notes.values():
            if w._resizing:
                w._finish_resize(cancel=w._resize_cancel_requested, redraw=False, hidden=True)
            elif w in origins:
                w.d["x"], w.d["y"] = origins[w]
            else:
                w.d["x"], w.d["y"] = w.winfo_x(), w.winfo_y()

    def quit(self, saved=False):
        self._stop_bottom_guard()
        update_check = getattr(self, "_update_check_cancel", None)
        if update_check is not None:
            update_check.set()
        if getattr(self, "_dock_refresh_job", None) is not None:
            self.root.after_cancel(self._dock_refresh_job)
            self._dock_refresh_job = None
        if getattr(self, "dock", None) is not None:
            self.dock.destroy()
        for note in self.notes.values():
            note._cancel_dock_reveal()
        if not saved:
            self._save_positions()
            self.save_now()
        elif getattr(self, "_note_motion", None) is not None:
            self._note_motion.finish_all()
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
