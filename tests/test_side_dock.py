"""Side dock regressions with isolated data and real Windows/Tk windows."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import ImageGrab

import main
import storage


class SideDockTests(unittest.TestCase):
    def setUp(self):
        self.errors = []
        self.data = {"schemaVersion": 3, "welcomeVersion": main.APP_VERSION,
                     "settings": dict(main.DEFAULT_SETTINGS, showOnStartup=False),
                     "notes": [dict(id=str(i), title=f"便签 {i}", x=100+i*30, y=100+i*30,
                                    w=340, h=300, z=z, visible=True, opacity=1.0, color="白",
                                    lines=[dict(type="text", text="测试正文", checked=False)])
                               for i, z in enumerate(("normal", "top", "bottom"))]}
        self.saved = self.enterContext(patch.object(main, "save_data"))
        self.enterContext(patch.object(main, "load_data", side_effect=lambda: deepcopy(self.data)))
        self.enterContext(patch.object(main.App, "_start_tray"))
        self.enterContext(patch.object(main.App, "_start_hotkeys"))
        self.enterContext(patch.object(main.tk.Tk, "report_callback_exception",
                                     side_effect=lambda kind, value, trace: self.errors.append(str(value))))
        self.app = main.App()
        self.app.icon = Mock()
        self.root = self.app.root
        self.dock = self.app.dock
        self.root.update()

    def tearDown(self):
        self.app._stop_bottom_guard()
        for job in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", job)
        self.root.destroy()
        self.assertFalse(self.errors, self.errors)

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    def click_bar(self, *, settle=True):
        self.dock.bar.event_generate("<ButtonPress-1>", x=30, y=15)
        self.dock.bar.event_generate("<ButtonRelease-1>", x=30, y=15)
        self.root.update()
        if settle:
            self.wait(0.24)

    def click(self, window):
        window.bar.event_generate("<ButtonPress-1>", x=30, y=15)
        window.bar.event_generate("<ButtonRelease-1>", x=30, y=15)
        self.root.update()

    def label(self, window):
        return window.bar.itemcget(window.bar.find_all()[0], "text")

    def restart(self):
        self.data = deepcopy(self.app.data)
        self.app._stop_bottom_guard()
        for job in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", job)
        self.root.destroy()
        self.app = main.App()
        self.app.icon = Mock()
        self.root, self.dock = self.app.root, self.app.dock
        self.root.update()

    def add_many(self):
        template = self.app.data["notes"][0]
        for i in range(3, 24):
            data = dict(deepcopy(template), id=str(i), title=f"便签 {i}：中文长标题与待办事项" * 2)
            self.app.data["notes"].append(data)
            self.app.notes[str(i)] = Mock(d=data)
        self.dock.refresh()
        self.root.update()

    def test_defaults_and_legacy_settings(self):
        self.assertTrue(storage.DEFAULT_SETTINGS["sideDock"])
        self.assertEqual(self.label(self.dock), "z-note")
        self.assertTrue(self.dock.winfo_ismapped())
        self.app.data["settings"].pop("sideDock")
        self.app.data["settings"].pop("sideDockY")
        self.restart()
        self.assertTrue(self.app.data["settings"]["sideDock"])
        self.assertEqual(self.app.data["settings"]["sideDockY"], 0.5)
        self.assertFalse(self.dock.expanded)

    def test_main_hover_changes_label_without_resizing_or_expanding(self):
        x, width, height = self.dock.winfo_x(), self.dock.winfo_width(), self.dock.winfo_height()
        self.dock._enter()
        self.root.update()
        self.assertEqual(self.label(self.dock), "展开")
        self.assertEqual(self.dock.winfo_x(), x)
        self.assertEqual(self.dock.winfo_width(), width)
        self.assertEqual(self.dock.winfo_height(), height)
        self.assertIsNone(self.dock._animation_job)
        self.assertEqual(self.dock.winfo_x()+self.dock.bar.winfo_width(), main.user32.GetSystemMetrics(0))
        self.assertFalse(self.dock.expanded)
        self.dock._leave()
        self.root.update()
        self.assertEqual(self.label(self.dock), "z-note")
        self.assertEqual((self.dock.winfo_x(), self.dock.winfo_width()), (x, width))

    def test_rapid_hover_replaces_animation_and_does_not_accumulate(self):
        self.click_bar()
        bookmark = self.dock.bookmarks["0"]
        width = bookmark.winfo_width()
        for _ in range(4):
            bookmark._enter()
            self.wait(0.025)
            bookmark._leave()
            self.wait(0.025)
        self.wait(0.15)
        self.assertEqual(bookmark._extension, 0)
        self.assertEqual(bookmark.winfo_width(), width)
        self.assertIsNone(bookmark._animation_job)

    def test_click_opens_separate_bookmarks_and_leaving_keeps_them_open(self):
        compact_width = self.dock.bar.winfo_width()
        bar = self.dock.bar
        self.assertEqual(bar.itemcget("dock-disclosure", "image"), str(self.dock.disclosure_icons[0]))
        self.dock._enter()
        self.click_bar()
        self.assertEqual(self.label(self.dock), "收起")
        self.assertEqual(bar.itemcget("dock-disclosure", "image"), str(self.dock.disclosure_icons[1]))
        self.assertLess(bar.coords("dock-disclosure")[0], bar.coords(bar.find_all()[0])[0])
        self.assertEqual(list(self.dock.bookmarks), ["0", "1", "2"])
        self.assertFalse(hasattr(self.dock, "popup"))
        windows = [self.dock, *self.dock.bookmarks.values()]
        for previous, window in zip(windows, windows[1:]):
            self.assertTrue(window.winfo_ismapped())
            self.assertEqual(window.master, self.root)
            self.assertEqual(window.winfo_y(), previous.winfo_y()+previous.winfo_height()+6)
            self.assertEqual(window.bar.winfo_width(), compact_width+12)
            self.assertEqual(window.winfo_x()+window.bar.winfo_width(), main.user32.GetSystemMetrics(0))
        self.assertEqual(self.dock.bar.winfo_width(), compact_width)
        self.dock._leave()
        self.wait(0.35)
        self.assertTrue(self.dock.expanded)
        self.assertEqual(self.label(self.dock), "收起")
        self.assertTrue(all(window.winfo_ismapped() for window in windows))
        self.assertEqual(self.dock.bar.winfo_width(), compact_width)
        self.assertEqual(self.dock.winfo_x()+self.dock.bar.winfo_width(), main.user32.GetSystemMetrics(0))
        self.dock._enter()
        self.click_bar()
        self.assertFalse(self.dock.expanded)
        self.assertEqual(self.label(self.dock), "展开")
        self.assertEqual(bar.itemcget("dock-disclosure", "image"), str(self.dock.disclosure_icons[0]))
        self.assertFalse(self.dock.bookmarks)
        self.assertEqual(self.dock.bar.winfo_width(), compact_width)
        self.assertTrue(all(n.d["visible"] for n in self.app.notes.values()))

    def test_note_toggle_reuses_bookmark_and_preserves_saved_geometry(self):
        self.click_bar()
        note, bookmark = self.app.notes["0"], self.dock.bookmarks["0"]
        original = (note.winfo_x(), note.winfo_y(), note.winfo_width(), note.winfo_height(), note.d["z"])
        self.assertEqual(bookmark.bar.cget("bg"), "#F0F3F7")
        for visible in (False, True, False, True):
            self.click(bookmark)
            self.assertEqual(note.d["visible"], visible)
            self.assertIs(self.dock.bookmarks["0"], bookmark)
            self.assertEqual(bookmark._note_visible, visible)
            self.assertEqual(bookmark.bar.cget("bg"), "#F0F3F7" if visible else main.NOTE_BG)
            self.assertTrue(self.dock.expanded)
            self.assertEqual((note.winfo_x(), note.winfo_y(), note.winfo_width(), note.winfo_height(), note.d["z"]), original)

    def test_main_toggle_only_changes_bookmarks_not_note_visibility(self):
        self.app.notes["0"].hide()
        self.root.update()
        original = {key: (note.d["visible"], note.winfo_ismapped())
                    for key, note in self.app.notes.items()}
        with patch.object(main.NoteWindow, "show") as show, \
                patch.object(main.NoteWindow, "hide") as hide, \
                patch.object(self.app, "show_all") as show_all, \
                patch.object(self.app, "hide_all") as hide_all:
            for _ in range(3):
                self.click_bar()
                self.assertEqual(set(self.dock.bookmarks), set(self.app.notes))
                self.assertNotIn("actions", vars(self.dock))
                self.click_bar()
                self.assertFalse(self.dock.bookmarks)
                self.assertEqual({key: (note.d["visible"], note.winfo_ismapped())
                                  for key, note in self.app.notes.items()}, original)
            for action in (show, hide, show_all, hide_all):
                action.assert_not_called()

    def test_external_visibility_keeps_all_bookmarks(self):
        self.click_bar()
        original = dict(self.dock.bookmarks)
        for action, visible in ((self.app.hide_all, False), (self.app.show_all, True)):
            action()
            self.root.update()
            self.assertTrue(all(n.d["visible"] == visible for n in self.app.notes.values()))
            self.assertEqual(self.dock.bookmarks, original)
            self.assertTrue(self.dock.expanded)
        self.app.notes["0"].hide()
        self.root.update()
        self.assertFalse(original["0"]._note_visible)
        self.assertEqual(self.dock.bookmarks, original)

    def test_opening_pinned_note_under_bookmarks_keeps_bookmark_clickable(self):
        from ctypes import wintypes
        self.click_bar()
        note, bookmark = self.app.notes["1"], self.dock.bookmarks["1"]
        edge = main.user32.GetSystemMetrics(0)
        note.geometry(f"340x300+{edge-340}+{bookmark.winfo_y()}")
        note.hide()
        self.root.update()
        self.click(bookmark)
        self.root.update()
        point = wintypes.POINT(edge-45, bookmark.winfo_y()+bookmark.height//2)
        hit = main.user32.WindowFromPoint(point)
        hwnd = main.user32.GetParent(bookmark.winfo_id()) or bookmark.winfo_id()
        self.assertTrue(hit == hwnd or main.user32.IsChild(hwnd, hit))

    def test_each_bookmark_hover_changes_only_its_width(self):
        self.click_bar()
        bookmark, neighbor = self.dock.bookmarks["0"], self.dock.bookmarks["1"]
        original = (bookmark.winfo_x(), bookmark.winfo_width())
        neighbor_geometry = neighbor.winfo_geometry()
        text = self.label(bookmark)
        bookmark._enter()
        self.wait(0.15)
        self.assertEqual(self.label(bookmark), text)
        self.assertEqual(bookmark.winfo_width(), original[1]+12)
        self.assertEqual(neighbor.winfo_geometry(), neighbor_geometry)
        bookmark._leave()
        self.wait(0.15)
        self.assertEqual((bookmark.winfo_x(), bookmark.winfo_width()), original)

    def test_refresh_is_coalesced(self):
        self.click_bar()
        with patch.object(self.dock, "refresh", wraps=self.dock.refresh) as refresh:
            self.app.hide_all()
            self.app.request_dock_refresh()
            self.root.update()
        refresh.assert_called_once()

    def test_empty_install_and_deleting_last_note_keep_entry(self):
        self.click_bar()
        for note in list(self.app.notes.values()):
            self.app.remove_note(note)
        self.root.update()
        self.assertTrue(self.dock.winfo_ismapped())
        self.assertFalse(self.dock.bookmarks)
        self.assertEqual(self.label(self.dock._empty), "暂无便签")
        self.click(self.dock._empty)
        self.assertFalse(self.app.notes)
        self.assertTrue(self.dock.expanded)

    def test_large_system_scaling_keeps_empty_label_readable(self):
        scaling = self.root.tk.call("tk", "scaling")
        try:
            self.root.tk.call("tk", "scaling", 3.0)
            self.dock.destroy()
            self.dock = self.app.dock = main.SideDock(self.app)
            self.dock.refresh()
            self.root.update()
            self.click_bar()
            for note in list(self.app.notes.values()):
                self.app.remove_note(note)
            self.root.update()
            self.assertEqual(self.label(self.dock._empty), "暂无便签")
        finally:
            self.root.tk.call("tk", "scaling", scaling)

    def test_add_delete_and_recover_bookmarks(self):
        self.click_bar()
        original = self.dock.bookmarks["0"]
        new_note = self.app.new_note()
        self.root.update()
        self.assertIn(new_note.d["id"], self.dock.bookmarks)
        self.app.remove_note(new_note)
        self.root.update()
        self.assertNotIn(new_note.d["id"], self.dock.bookmarks)
        self.assertIs(self.dock.bookmarks["0"], original)
        self.assertTrue(self.app.recover_note(new_note.d["id"]))
        self.root.update()
        self.assertIn(new_note.d["id"], self.dock.bookmarks)

    def test_renaming_and_editing_first_line_updates_label_in_place(self):
        self.click_bar()
        note, bookmark = self.app.notes["0"], self.dock.bookmarks["0"]
        note.edit_title()
        self.root.update()
        note._title_editor.delete(0, "end")
        note._title_editor.insert(0, "新的标题")
        note._title_editor.event_generate("<Return>")
        self.root.update()
        self.assertEqual(bookmark.full_label, "新的标题")
        note.edit_title()
        self.root.update()
        note._title_editor.delete(0, "end")
        note._title_editor.event_generate("<Return>")
        self.root.update()
        note.text.delete("1.0", "end")
        note.text.insert("1.0", "正文新的第一行")
        note._sync_lines_from_text()
        self.root.update()
        self.assertEqual(bookmark.full_label, "正文新的第一行")
        self.assertIs(self.dock.bookmarks["0"], bookmark)

    def test_labels(self):
        label = main.SideDock.note_label
        self.assertEqual(label({"title": " 标题 ", "lines": [{"text": "正文"}]}), "标题")
        self.assertEqual(label({"lines": [{"text": "\n \n正文第一行\n第二行"}]}), "正文第一行")
        self.assertEqual(label({"lines": [], "lines2": [{"text": "第二栏"}]}), "第二栏")
        self.assertEqual(label({}), "无标题便签")

    def test_scroll_creates_only_six_and_reuses_overlapping_bookmarks(self):
        self.add_many()
        self.click_bar()
        self.assertEqual(list(self.dock.bookmarks), [str(i) for i in range(6)])
        keep = self.dock.bookmarks["1"]
        self.dock._wheel(SimpleNamespace(delta=-120))
        self.root.update()
        self.assertEqual(set(self.dock.bookmarks), {str(i) for i in range(1, 7)})
        self.assertIs(self.dock.bookmarks["1"], keep)
        self.assertEqual(len(self.dock.bookmarks), 6)
        self.click_bar()
        self.click_bar()
        self.assertEqual(self.dock._offset, 0)
        self.assertEqual(list(self.dock.bookmarks), [str(i) for i in range(6)])

    def test_bottom_expansion_moves_up_temporarily_and_stays_inside_work_area(self):
        self.add_many()
        self.app.data["settings"]["sideDockY"] = 1.0
        self.dock.refresh()
        self.root.update()
        original_y = self.dock.winfo_y()
        self.click_bar()
        work = self.dock.work_area()
        self.assertLess(self.dock.winfo_y(), original_y)
        last = self.dock.bookmarks["5"]
        self.assertLessEqual(last.winfo_y()+last.winfo_height(), work.bottom-8)
        self.assertEqual(self.app.data["settings"]["sideDockY"], 1.0)
        preview = os.environ.get("ZNOTE_DOCK_PREVIEW")
        if preview:
            main.dwmapi.DwmFlush()
            x, y = self.dock.winfo_x(), self.dock.winfo_y()
            ImageGrab.grab((x-24, y-12, main.user32.GetSystemMetrics(0),
                            last.winfo_y()+last.winfo_height()+12)).save(preview)
        self.click_bar()
        self.assertEqual(self.dock.winfo_y(), original_y)

    def test_drag_saves_collapsed_position_and_does_not_expand(self):
        y = self.dock._bar_y
        self.dock._drag_press(SimpleNamespace(x_root=100, y_root=y))
        self.dock._drag_move(SimpleNamespace(x_root=100, y_root=y+100))
        self.dock._drag_release(SimpleNamespace(x_root=100, y_root=y+100))
        self.root.update()
        self.assertFalse(self.dock.expanded)
        self.assertEqual(self.dock.winfo_y(), y+100)
        self.assertGreater(self.app.data["settings"]["sideDockY"], 0.5)
        self.app.save_now()
        self.assertEqual(self.saved.call_args.args[0]["settings"]["sideDockY"], self.app.data["settings"]["sideDockY"])

    def test_drag_expanded_group_saves_new_collapsed_position(self):
        self.click_bar()
        y = self.dock._bar_y
        self.dock._drag_press(SimpleNamespace(x_root=100, y_root=y))
        self.dock._drag_move(SimpleNamespace(x_root=100, y_root=y-80))
        self.dock._drag_release(SimpleNamespace(x_root=100, y_root=y-80))
        self.root.update()
        self.assertTrue(self.dock.expanded)
        self.assertEqual(self.dock.winfo_y(), y-80)
        self.assertEqual(self.dock.bookmarks["0"].winfo_y(), y-80+self.dock.bar_height+6)
        self.click_bar()
        self.assertEqual(self.dock.winfo_y(), y-80)

    def test_disable_shows_all_temporarily_then_restores_layers(self):
        self.click_bar()
        self.app.hide_all()
        self.app.toggle_side_dock()
        self.root.update()
        self.assertFalse(self.dock.winfo_ismapped())
        self.assertFalse(self.dock.bookmarks)
        for note in self.app.notes.values():
            self.assertTrue(note.d["visible"])
            self.assertTrue(note.attributes("-topmost"))
            self.assertIsNotNone(note._dock_reveal_job)
        self.wait(1.1)
        for note in self.app.notes.values():
            self.assertEqual(bool(note.attributes("-topmost")), note.d["z"] == "top")
            self.assertIsNone(note._dock_reveal_job)
        self.app.hide_all()
        self.app.toggle_side_dock()
        self.root.update()
        self.assertTrue(self.dock.winfo_ismapped())
        self.assertFalse(self.dock.expanded)
        self.click_bar()
        self.assertEqual(len(self.dock.bookmarks), 3)

    def test_mode_switch_and_deletion_cancel_pending_animations(self):
        self.click_bar(settle=False)
        bookmark = self.dock.bookmarks["0"]
        bookmark._enter()
        self.dock._enter()
        self.app.toggle_side_dock()
        self.root.update()
        self.assertIsNone(bookmark._animation_job)
        self.assertIsNone(self.dock._animation_job)
        self.assertIsNone(self.dock._transition_job)
        self.app.toggle_side_dock()
        self.root.update()
        self.click_bar()
        bookmark = self.dock.bookmarks["0"]
        bookmark._enter()
        self.app.remove_note(self.app.notes["0"])
        self.root.update()
        self.assertIsNone(bookmark._animation_job)
        self.wait(0.15)

    def test_bookmarks_move_out_from_entry_and_reverse_in_place(self):
        import ctypes
        with patch.object(main.time, "monotonic", return_value=100.0) as clock:
            self.click_bar(settle=False)
            bookmark = self.dock.bookmarks["0"]
            entry_y = self.dock._position_y
            self.assertEqual(bookmark._position_y, entry_y)
            self.assertEqual(bookmark.attributes("-alpha"), 0)
            clock.return_value += 0.08
            time.sleep(0.025)
            self.root.update()
            mid_y = bookmark._position_y
            self.assertGreater(mid_y, entry_y)
            self.assertLess(mid_y, entry_y+self.dock.bar_height+6)
            self.click_bar(settle=False)
            self.assertEqual(bookmark._position_y, mid_y)
            self.assertFalse(self.dock.expanded)
            clock.return_value += 0.08
            time.sleep(0.025)
            self.root.update()
            closing_y = bookmark._position_y
            self.assertLess(closing_y, mid_y)
            self.assertTrue(bookmark.winfo_exists())
            self.click_bar(settle=False)
            self.assertIs(self.dock.bookmarks["0"], bookmark)
            self.assertEqual(bookmark._position_y, closing_y)
            clock.return_value += 0.24
            time.sleep(0.025)
            self.root.update()
            self.assertTrue(self.dock.expanded)
            self.assertEqual(bookmark.attributes("-alpha"), 1)
            self.click_bar(settle=False)
            clock.return_value += 0.08
            time.sleep(0.025)
            self.root.update()
            hwnd = main.user32.GetParent(bookmark.winfo_id()) or bookmark.winfo_id()
            get_alpha = main.user32.GetLayeredWindowAttributes
            get_alpha.argtypes = [main.wintypes.HWND, ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_ubyte), ctypes.c_void_p]
            alpha = ctypes.c_ubyte()
            self.assertTrue(get_alpha(hwnd, None, ctypes.byref(alpha), None))
            self.assertTrue(0 < alpha.value < 255)
            clock.return_value += 0.16
            time.sleep(0.025)
            self.root.update()
        self.assertFalse(bookmark.winfo_exists())

    def test_hide_delete_layer_change_and_fast_mode_switch_cancel_restore(self):
        self.app.toggle_side_dock()
        hidden, deleted, changed = [self.app.notes[str(i)] for i in range(3)]
        hidden.hide()
        changed.set_z("top")
        self.app.remove_note(deleted)
        self.assertIsNone(hidden._dock_reveal_job)
        self.assertIsNone(changed._dock_reveal_job)
        self.wait(1.1)
        self.assertFalse(hidden.d["visible"])
        self.assertTrue(changed.attributes("-topmost"))
        self.app.toggle_side_dock()
        self.app.toggle_side_dock()
        self.app.toggle_side_dock()
        self.assertTrue(all(n._dock_reveal_job is None for n in self.app.notes.values()))
        self.app.hide_all()
        self.wait(1.1)
        self.assertTrue(all(not n.d["visible"] for n in self.app.notes.values()))

    def test_restart_restores_settings_and_position_but_collapses_bookmarks(self):
        self.app.hide_all()
        self.app.data["settings"]["sideDockY"] = 0.8
        self.click_bar()
        self.restart()
        self.assertFalse(self.dock.expanded)
        self.assertFalse(self.dock.bookmarks)
        work = self.dock.work_area()
        expected = work.top+8+round((work.bottom-work.top-self.dock.bar_height-16)*0.8)
        self.assertEqual(self.dock.winfo_y(), expected)
        self.click_bar()
        self.assertEqual(len(self.dock.bookmarks), 3)
        self.assertTrue(all(not n._note_visible for n in self.dock.bookmarks.values()))

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            target = str(Path(folder) / "notes.json")
            with patch.object(storage, "DATA_DIR", folder), patch.object(storage, "DATA_FILE", target), \
                    patch.object(storage, "BACKUP_DIR", str(Path(folder) / "backup")):
                self.app.data["settings"].update(sideDock=False, sideDockY=0.8)
                storage.save_data(self.app.data)
                restored = storage.load_data()
        self.assertFalse(restored["settings"]["sideDock"])
        self.assertEqual(restored["settings"]["sideDockY"], 0.8)

    def test_long_tooltip_stays_on_screen_and_destruction_cancels_it(self):
        self.app.notes["0"].d["title"] = "很长的中文标题，需要完整显示。" * 12
        self.click_bar()
        bookmark = self.dock.bookmarks["0"]
        self.assertIn("…", self.label(bookmark))
        bookmark.tip._show()
        self.root.update()
        popup = main.Tooltip._cur
        work = self.dock.work_area()
        self.assertGreaterEqual(popup.winfo_x(), work.left)
        self.assertLessEqual(popup.winfo_x()+popup.winfo_width(), work.right)
        self.assertGreater(popup.winfo_height(), 30)
        self.click_bar()
        self.assertIsNone(main.Tooltip._cur)

    def test_native_corners_and_topmost_tool_windows(self):
        import ctypes
        self.click_bar()
        for window in (self.dock, *self.dock.bookmarks.values()):
            hwnd = main.user32.GetParent(window.winfo_id()) or window.winfo_id()
            preference = ctypes.c_int()
            self.assertEqual(main.dwmapi.DwmGetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference)), 0)
            self.assertEqual(preference.value, main.NOTE_CORNER_PREFERENCE)
            self.assertTrue(main.user32.GetWindowLongPtrW(hwnd, -20) & 0x80)
            self.assertTrue(window.attributes("-topmost"))
            self.assertEqual(window.winfo_x()+window.bar.winfo_width(), main.user32.GetSystemMetrics(0))


if __name__ == "__main__":
    unittest.main()
