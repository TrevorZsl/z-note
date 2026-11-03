"""Load bundled fonts privately for this process, without installing them."""
import atexit
import ctypes
from pathlib import Path
import sys


CLASSIC_FAMILY = "ZNote Classic"
FONT_CHOICES = {
    "classic": "Times New Roman",
    "yahei": "微软雅黑",
    "simsun": "宋体",
    "system": "电脑默认",
}
FONT_FAMILIES = {"classic": CLASSIC_FAMILY, "yahei": "ZNote YaHei", "simsun": "ZNote SimSun"}
FONT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "fonts"
FONT_FILES = ("classic.ttf", "classic-bold.ttf", "yahei.ttf", "yahei-bold.ttf", "simsun.ttf")
_loaded = []


def load_fonts():
    if _loaded:
        return
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    gdi.AddFontResourceExW.restype = ctypes.c_int
    gdi.RemoveFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    gdi.RemoveFontResourceExW.restype = ctypes.c_int
    try:
        for name in FONT_FILES:
            path = str(FONT_DIR / name)
            if not gdi.AddFontResourceExW(path, 0x10, None):  # FR_PRIVATE
                raise RuntimeError(f"无法加载内置字体：{path}")
            _loaded.append(path)
    except Exception:
        for path in reversed(_loaded):
            gdi.RemoveFontResourceExW(path, 0x10, None)
        _loaded.clear()
        raise

    def unload():
        for path in reversed(_loaded):
            gdi.RemoveFontResourceExW(path, 0x10, None)
        _loaded.clear()
    atexit.register(unload)
