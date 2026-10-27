"""Run z-note with automatic restart after edits to main.py."""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


source = Path(__file__).with_name("main.py")
env = dict(os.environ, Z_NOTE_DEV="1")


def close_legacy_note():
    # shortcut: this handoff uses pystray's Windows menu; remove it once old app versions are gone.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
    user32.GetMenuStringW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT]
    user32.GetMenuItemRect.argtypes = [wintypes.HWND, wintypes.HMENU, wintypes.UINT, ctypes.POINTER(wintypes.RECT)]

    def windows():
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def collect(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, 256)
            found.append((hwnd, pid.value, name.value))
            return True

        user32.EnumWindows(collect, 0)
        return found

    trays = [(hwnd, pid) for hwnd, pid, name in windows()
             if name.startswith("z-note") and name.endswith("SystemTrayIcon")]
    for tray, pid in trays:
        user32.PostMessageW(tray, 0x40B, 0, 0x205)  # pystray right-click notification
        for _ in range(10):
            time.sleep(0.1)
            for menu, menu_pid, name in windows():
                if menu_pid != pid or name != "#32768":
                    continue
                handle = user32.SendMessageW(menu, 0x01E1, 0, 0)  # MN_GETHMENU
                for index in range(user32.GetMenuItemCount(handle)):
                    label = ctypes.create_unicode_buffer(64)
                    user32.GetMenuStringW(handle, index, label, 64, 0x400)
                    if label.value != "退出":
                        continue
                    rect = wintypes.RECT()
                    if not user32.GetMenuItemRect(0, handle, index, ctypes.byref(rect)):
                        return False
                    original = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(original))
                    if not user32.SetCursorPos((rect.left + rect.right) // 2,
                                               (rect.top + rect.bottom) // 2):
                        return False
                    user32.mouse_event(2, 0, 0, 0, 0)
                    user32.mouse_event(4, 0, 0, 0, 0)
                    user32.SetCursorPos(original.x, original.y)
                    return True
    return False


def main():
    legacy_exit_requested = False
    legacy_deadline = 0
    while True:
        result = subprocess.run([sys.executable, str(source)], env=env, cwd=source.parent)
        if result.returncode == 0:
            break
        if result.returncode == 75:
            time.sleep(0.5)
            continue
        if result.returncode == 76:
            if not legacy_exit_requested:
                if not close_legacy_note():
                    raise SystemExit("无法自动关闭旧版 z-note；未强制结束进程，以免丢失便签内容。")
                legacy_exit_requested = True
                legacy_deadline = time.monotonic() + 5
            elif time.monotonic() > legacy_deadline:
                raise SystemExit("旧版 z-note 未正常退出；未强制结束进程，以免丢失便签内容。")
            time.sleep(0.5)
            continue
        print("启动失败；修改 main.py 后会自动重试。", file=sys.stderr)
        modified = source.stat().st_mtime_ns
        while source.stat().st_mtime_ns == modified:
            time.sleep(0.3)


if __name__ == "__main__":
    main()
