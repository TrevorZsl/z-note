"""Present completed Tk client frames during an interactive resize (Windows)."""
import ctypes
import struct
from ctypes import wintypes


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
_user32.SetWindowLongPtrW.restype = ctypes.c_void_p
_user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
_user32.CallWindowProcW.restype = ctypes.c_ssize_t
_user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
    wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                             wintypes.BYTE, wintypes.DWORD]
_user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p,
    ctypes.c_void_p, wintypes.HDC, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                  ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
_gdi32.SelectObject.restype = wintypes.HANDLE
_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


class ResizeSurface:
    """One reusable presentation window and DIB; never reads desktop pixels."""

    def __init__(self, note, client_hwnd, rect):
        self.note, self.client_hwnd = note, client_hwnd
        self.hwnd = self.dc = self.bitmap = self.old_bitmap = None
        self._old_proc = self._proc = None
        self._hidden = self._restored = False
        self._transitions = None
        self._fade_tables = {}
        self.capacity = (0, 0)
        try:
            self.dc = _gdi32.CreateCompatibleDC(None)
            if not self.dc:
                raise ctypes.WinError(ctypes.get_last_error())
            self.ensure_capacity(rect[2], rect[3])
            self.hwnd = _user32.CreateWindowExW(0x080800A0, "STATIC", None, 0x80040000,
                *rect, note.hwnd, None, None, None)  # owned, no activation or mouse input
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._old_proc = _user32.GetWindowLongPtrW(self.hwnd, -4)

            @_WNDPROC
            def proc(hwnd, message, wparam, lparam):
                if message in (0x0083, 0x0085):
                    return 0
                if message == 0x0086:
                    return 1
                if message == 0x0084:
                    return -1  # HTTRANSPARENT
                if message == 0x0021:
                    return 3  # MA_NOACTIVATE
                return _user32.CallWindowProcW(self._old_proc, hwnd, message, wparam, lparam)

            self._proc = proc
            _user32.SetWindowLongPtrW(self.hwnd, -4, ctypes.cast(proc, ctypes.c_void_p))
            preference = ctypes.c_int(2)
            _dwmapi.DwmSetWindowAttribute(self.hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
            disabled = wintypes.BOOL(True)
            _dwmapi.DwmSetWindowAttribute(self.hwnd, 3, ctypes.byref(disabled), ctypes.sizeof(disabled))
            previous = wintypes.BOOL()
            if _dwmapi.DwmGetWindowAttribute(note.hwnd, 3, ctypes.byref(previous), ctypes.sizeof(previous)) == 0:
                self._transitions = previous.value
                _dwmapi.DwmSetWindowAttribute(note.hwnd, 3, ctypes.byref(disabled), ctypes.sizeof(disabled))
            _user32.SetWindowPos(self.hwnd, None, *rect, 0x0010 | 0x0020 | 0x0004)
            # Capture the initial note once, before hiding its live rendering.
            if not _user32.PrintWindow(note.hwnd, self.dc, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            self._upload(rect)
            self.keep_source_hidden()
        except Exception:
            self.close()
            raise

    def ensure_capacity(self, width, height):
        if width <= self.capacity[0] and height <= self.capacity[1]:
            return False
        width = (max(width, self.capacity[0])+255)//256*256
        height = (max(height, self.capacity[1])+255)//256*256
        header = ctypes.create_string_buffer(struct.pack(
            "<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width*height*4, 0, 0, 0, 0))
        bits = ctypes.c_void_p()
        bitmap = _gdi32.CreateDIBSection(None, header, 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        previous = _gdi32.SelectObject(self.dc, bitmap)
        if self.bitmap:
            _gdi32.DeleteObject(self.bitmap)
        else:
            self.old_bitmap = previous
        self.bitmap, self.bits, self.capacity = bitmap, bits, (width, height)
        return True

    def keep_source_hidden(self):
        style = _user32.GetWindowLongPtrW(self.note.hwnd, -20)
        _user32.SetWindowLongPtrW(self.note.hwnd, -20, style | 0x00080000)
        _user32.SetLayeredWindowAttributes(self.note.hwnd, 0, 0, 2)

    def present(self, rect, fades=()):
        if self._hidden or not self.hwnd:
            return
        width, height = rect[2:]
        if width > self.capacity[0] or height > self.capacity[1]:
            raise ValueError("resize source is smaller than its target")
        source = _user32.GetWindowDC(self.client_hwnd)
        if not source:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _gdi32.BitBlt(self.dc, 0, 0, width, height, source, 0, 0, 0x00CC0020):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _user32.ReleaseDC(self.client_hwnd, source)
        # Native layered fade children are suspended; blend them inside the body,
        # so no intermediate fade surface can cover the footer during a move.
        for x, y, span, rows, bottom in fades:
            key = rows, bottom
            if key not in self._fade_tables:
                alphas = [round(255*i/max(1, rows-1)) for i in range(rows)]
                if not bottom:
                    alphas.reverse()
                self._fade_tables[key] = [bytes(min(255, a + (c*(255-a)+127)//255)
                                               for c in range(256)) for a in alphas]
            for row, table in enumerate(self._fade_tables[key]):
                address = self.bits.value + ((y+row)*self.capacity[0]+x)*4
                pixels = ctypes.string_at(address, span*4).translate(table)
                ctypes.memmove(address, pixels, len(pixels))
        self._upload(rect)

    def _upload(self, rect):
        position, size, origin = wintypes.POINT(*rect[:2]), wintypes.SIZE(*rect[2:]), wintypes.POINT(0, 0)
        blend = ctypes.c_uint32(round(self.note.d["opacity"]*255) << 16)
        if not _user32.UpdateLayeredWindow(self.hwnd, None, ctypes.byref(position), ctypes.byref(size),
                self.dc, ctypes.byref(origin), 0, ctypes.byref(blend), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        if _user32.GetWindow(self.hwnd, 2) != self.note.hwnd:  # GW_HWNDNEXT
            above = _user32.GetWindow(self.note.hwnd, 3)  # GW_HWNDPREV
            if above != self.hwnd:
                _user32.SetWindowPos(self.hwnd, above, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002)
        _user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x0010 | 0x0040 | 0x0004 | 0x0001 | 0x0002)
        if self.note.d["z"] == "bottom":
            # Showing an owned popup can raise its owner group. Move the group
            # back together so a bottom note never covers a normal window.
            _user32.SetWindowPos(self.note.hwnd, 1, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002)

    def hide(self):
        self._hidden = True
        if self.hwnd:
            _user32.ShowWindow(self.hwnd, 0)

    def prepare_restore(self):
        # For an opaque note, restore its normal GDI mode while it is still
        # covered. Transparent notes stay hidden until the final handover.
        if self.note.d["opacity"] == 1:
            _user32.SetLayeredWindowAttributes(self.note.hwnd, 0, 255, 2)
            style = _user32.GetWindowLongPtrW(self.note.hwnd, -20)
            _user32.SetWindowLongPtrW(self.note.hwnd, -20, style & ~0x00080000)
            self._restored = True

    def close(self):
        if self.hwnd:
            if not self._restored and _user32.IsWindow(self.note.hwnd):
                style = _user32.GetWindowLongPtrW(self.note.hwnd, -20)
                if self.note.d["opacity"] < 1:
                    _user32.SetWindowLongPtrW(self.note.hwnd, -20, style | 0x00080000)
                    _user32.SetLayeredWindowAttributes(self.note.hwnd, 0, round(self.note.d["opacity"]*255), 2)
                else:
                    _user32.SetLayeredWindowAttributes(self.note.hwnd, 0, 255, 2)
                    _user32.SetWindowLongPtrW(self.note.hwnd, -20, style & ~0x00080000)
            if _user32.IsWindow(self.hwnd):
                if self._old_proc:
                    _user32.SetWindowLongPtrW(self.hwnd, -4, self._old_proc)
                _user32.DestroyWindow(self.hwnd)
            self.hwnd = self._proc = None
        if self.old_bitmap:
            _gdi32.SelectObject(self.dc, self.old_bitmap)
            self.old_bitmap = None
        if self.bitmap:
            _gdi32.DeleteObject(self.bitmap)
            self.bitmap = None
        if self.dc:
            _gdi32.DeleteDC(self.dc)
            self.dc = None
        if self._transitions is not None and _user32.IsWindow(self.note.hwnd):
            previous = wintypes.BOOL(self._transitions)
            _dwmapi.DwmSetWindowAttribute(self.note.hwnd, 3, ctypes.byref(previous), ctypes.sizeof(previous))
            self._transitions = None
