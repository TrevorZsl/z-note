"""Side dock regressions with isolated data and real Windows/Tk windows."""
from copy import deepcopy
import ctypes
from ctypes import wintypes
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
        self.app._note_motion.finish_all()  # 启动淡入结束后再开始各项检查。

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

    def click(self, window, *, settle=True):
        window.bar.event_generate("<ButtonPress-1>", x=30, y=15)
        window.bar.event_generate("<ButtonRelease-1>", x=30, y=15)
        self.root.update()
        if settle and self.app._note_motion._active:
            self.wait(0.20)

    def native_frame(self, note):
        rect = wintypes.RECT()
        self.assertTrue(main.user32.GetWindowRect(note.hwnd, ctypes.byref(rect)))
        alpha = ctypes.c_ubyte()
        read_alpha = main.user32.GetLayeredWindowAttributes
        read_alpha.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_void_p]
        layered = read_alpha(note.hwnd, None, ctypes.byref(alpha), None)
        return rect.left, rect.top, alpha.value if layered else 255

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

    def button_room(self):
        return self.dock.ENTRY_GAP+self.dock.hide_all_button.height

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
        self.app.data["settings"]["uiDpi"] = 96
        self.restart()
        self.app.notes["0"].d["title"] = "11.06"
        self.app.notes["1"].d["title"] = "2026年11月的工作便签与待办事项"
        compact_width = self.dock.bar.winfo_width()
        bar = self.dock.bar
        self.assertEqual(bar.itemcget("dock-disclosure", "image"), str(self.dock.disclosure_icons[0]))
        self.dock._enter()
        self.click_bar()
        self.assertEqual(self.label(self.dock), "收起")
        self.assertEqual(bar.itemcget("dock-disclosure", "image"), str(self.dock.disclosure_icons[1]))
        self.assertLess(bar.coords("dock-disclosure")[0], bar.coords(bar.find_all()[0])[0])
        self.assertEqual(list(self.dock.bookmarks), ["0", "1", "2"])
        self.assertEqual(self.label(self.dock.bookmarks["0"]), "11.06")
        self.assertTrue(self.label(self.dock.bookmarks["1"]).endswith("…"))
        self.assertFalse(hasattr(self.dock, "popup"))
        windows = [self.dock, *self.dock.bookmarks.values()]
        for previous, window in zip(windows, windows[1:]):
            self.assertTrue(window.winfo_ismapped())
            self.assertEqual(window.master, self.root)
            gap = 10 if previous is self.dock else 4
            self.assertEqual(window.winfo_y(), previous.winfo_y()+previous.winfo_height()+gap)
            self.assertEqual(window.bar.winfo_width(), compact_width+12)
            self.assertEqual(window.winfo_height(), max(32, self.dock.font.metrics("linespace")+10))
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
        self.assertEqual(bookmark.bar.cget("bg"), main.NOTE_BG)
        title_position = bookmark.bar.coords(bookmark.bar.find_all()[0])
        self.assertTrue(bookmark.bar.find_withtag("dock-note-visible"))
        for visible in (False, True, False, True):
            self.click(bookmark)
            self.assertEqual(note.d["visible"], visible)
            self.assertIs(self.dock.bookmarks["0"], bookmark)
            self.assertEqual(bookmark._note_visible, visible)
            self.assertEqual(bookmark.bar.cget("bg"), main.NOTE_BG if visible else "#F0F3F7")
            self.assertEqual(bool(bookmark.bar.find_withtag("dock-note-visible")), visible)
            self.assertEqual(bookmark.bar.coords(bookmark.bar.find_all()[0]), title_position)
            self.assertTrue(self.dock.expanded)
            self.assertEqual((note.winfo_x(), note.winfo_y(), note.winfo_width(), note.winfo_height(), note.d["z"]), original)

    def test_note_motion_reverses_from_native_frame_and_saves_original_position(self):
        self.click_bar()
        note, bookmark = self.app.notes["0"], self.dock.bookmarks["0"]
        note.hide()
        self.app.set_opacity(70)
        self.root.update()
        origin = self.native_frame(note)[:2]
        motion = self.app._note_motion
        with patch.object(motion, "enabled", return_value=True), patch.object(main.time, "perf_counter") as clock:
            clock.return_value = 100
            self.click(bookmark, settle=False)
            x, y, alpha = self.native_frame(note)
            self.assertGreater(x, origin[0])
            self.assertLessEqual((x-origin[0])**2 + (y-origin[1])**2, 24**2)
            self.assertEqual(alpha, 0)
            self.assertTrue(bookmark._note_visible)
            clock.return_value += 0.06
            time.sleep(0.03)
            self.root.update()
            frame = self.native_frame(note)
            self.assertTrue(origin[0] < frame[0] < x)
            self.assertTrue(0 < frame[2] < 178)

            # 便签工具栏的减号和书签点击共用同一过渡。
            note._tool_btns[2].event_generate("<Button-1>", x=15, y=15)
            self.root.update()
            self.assertEqual(self.native_frame(note), frame)
            self.assertFalse(bookmark._note_visible)
            clock.return_value += 0.09
            time.sleep(0.03)
            self.root.update()
            frame = self.native_frame(note)
            self.assertLess(frame[2], 100)
            self.assertTrue(note.winfo_ismapped())
            self.click(bookmark, settle=False)
            self.assertEqual(self.native_frame(note), frame)
            self.app._save_positions()
            self.root.update()
            self.assertEqual((note.d["x"], note.d["y"]), origin)
            self.assertEqual(self.native_frame(note), (*origin, round(0.7*255)))
            self.assertIsNone(motion._job)

    def test_batch_motion_uses_one_timer_and_fades_in_place_in_both_modes(self):
        motion = self.app._note_motion
        notes = list(self.app.notes.values())
        origins = [self.native_frame(note)[:2] for note in notes]
        z_states = [note.d["z"] for note in notes]
        change = motion.change
        with patch.object(motion, "enabled", return_value=True), patch.object(main.time, "perf_counter") as clock:
            clock.return_value = 100

            def prepare(*args, **kwargs):
                transition = change(*args, **kwargs)
                clock.return_value += 0.01
                return transition

            for enabled in (True, False):
                self.app.data["settings"]["sideDock"] = enabled
                for visible, action in ((False, self.app.hide_all), (True, self.app.show_all)):
                    with patch.object(motion, "change", side_effect=prepare):
                        action(animate=True)
                    self.root.update()
                    self.assertEqual(len(motion._active), len(notes))
                    self.assertEqual({m.began for m in motion._active.values()}, {clock.return_value})
                    self.assertEqual({m.duration for m in motion._active.values()}, {0.15})
                    self.assertIsNotNone(motion._job)
                    clock.return_value += 0.15 / 4
                    time.sleep(0.03)
                    self.root.update()
                    frames = [self.native_frame(note) for note in notes]
                    self.assertEqual([frame[:2] for frame in frames], origins)
                    alphas = {frame[2] for frame in frames}
                    self.assertEqual(len(alphas), 1)
                    self.assertEqual(alphas, {40 if visible else 215})
                    self.assertEqual([note.d["visible"] for note in notes], [visible]*len(notes))
                    clock.return_value += 0.20
                    time.sleep(0.03)
                    self.root.update()
                    self.assertFalse(motion._active)
                    self.assertEqual([bool(note.winfo_ismapped()) for note in notes], [visible]*len(notes))
                self.assertEqual([note.d["z"] for note in notes], z_states)

    def test_single_note_show_and_hide_are_mirrored(self):
        self.click_bar()
        note, bookmark = self.app.notes["0"], self.dock.bookmarks["0"]
        note.hide()
        self.root.update()
        motion = self.app._note_motion
        with patch.object(motion, "enabled", return_value=True), patch.object(main.time, "perf_counter") as clock:
            clock.return_value = 100
            frames = []
            for _ in range(2):
                self.click(bookmark, settle=False)
                transition = motion._active[note]
                self.assertEqual(transition.duration, 0.16)
                clock.return_value += 0.16 / 4
                time.sleep(0.03)
                self.root.update()
                frames.append(self.native_frame(note))
                clock.return_value += 0.16
                time.sleep(0.03)
                self.root.update()
            self.assertEqual([frame[2] for frame in frames], [40, 215])
            for axis in (0, 1):
                self.assertLessEqual(abs(frames[0][axis] + frames[1][axis]
                                         - transition.start[axis] - transition.target[axis]), 1)

    def test_toolbar_and_queued_new_note_fade_in_at_their_landing_position(self):
        source = self.app.notes["0"]
        motion = self.app._note_motion
        first_frames = []
        initialize = main.NoteWindow.__init__

        def record_initial_frame(note, *args, **kwargs):
            initialize(note, *args, **kwargs)
            first_frames.append(self.native_frame(note))

        with patch.object(motion, "enabled", return_value=True), patch.object(main.time, "perf_counter") as clock, \
                patch.object(main.NoteWindow, "__init__", record_initial_frame):
            clock.return_value = 100
            for from_toolbar in (True, False):
                if from_toolbar:
                    source._tool_btns[3].event_generate("<Button-1>", x=15, y=15)
                else:
                    self.app.q.put("new")
                    self.app._pump()
                self.root.update()
                note = next(reversed(self.app.notes.values()))
                origin = (note.d["x"], note.d["y"])
                self.assertEqual(first_frames[-1], (*origin, 0))
                self.assertEqual(self.native_frame(note), (*origin, 0))
                self.assertEqual(motion._active[note].duration, 0.15)
                self.assertIs(note.focus_get(), note.text)
                note.text.event_generate("<KeyPress>", keysym="a")
                self.assertIn("a", note.text.get("1.0", "end-1c"))
                clock.return_value += 0.15 / 4
                time.sleep(0.03)
                self.root.update()
                self.assertEqual(self.native_frame(note), (*origin, 40))
                clock.return_value += 0.15
                time.sleep(0.03)
                self.root.update()
                self.assertEqual(self.native_frame(note), (*origin, 255))
                self.assertFalse(main.user32.GetWindowLongPtrW(note.hwnd, -20) & motion._LAYERED)

    def test_hotkey_new_notes_land_at_queued_cursor_without_cascading(self):
        original_ids = set(self.app.notes)
        self.app._cascade = 3
        for _ in range(2):
            self.app.q.put(("new_at_cursor", (220, 260)))
        with patch.object(main, "cursor_pos", return_value=(600, 500)) as cursor:
            self.app._pump()
        self.root.update()
        cursor.assert_not_called()
        notes = [note for key, note in self.app.notes.items() if key not in original_ids]
        self.assertEqual([(note.d["x"], note.d["y"]) for note in notes], [(220, 260)] * 2)
        self.assertEqual([self.native_frame(note)[:2] for note in notes], [(220, 260)] * 2)
        self.assertEqual(self.app._cascade, 3)

    def test_new_note_is_immediate_with_animation_disabled_or_native_failure(self):
        motion = self.app._note_motion
        for enabled in (False, True):
            with patch.object(motion, "enabled", return_value=enabled), \
                    patch.object(motion, "_alpha", return_value=False):
                note = self.app.new_note(animate=True)
            self.root.update()
            self.assertTrue(note.winfo_ismapped())
            self.assertEqual(self.native_frame(note), (note.d["x"], note.d["y"], 255))
            self.assertIs(note.focus_lastfor(), note.text)
            self.assertFalse(motion._active)

    def test_note_motion_is_local_without_a_bookmark_and_can_be_disabled(self):
        note = self.app.notes["0"]
        origin = self.native_frame(note)[:2]
        motion = self.app._note_motion
        with patch.object(motion, "enabled", return_value=True):
            self.app.animate_note_visibility(note, False)
            self.assertEqual(motion._active[note].target, (*origin, 0))
            self.app.set_opacity(80)
        self.assertFalse(note.winfo_ismapped())
        with patch.object(main.user32, "SystemParametersInfoW", return_value=1) as spi:
            self.app.animate_note_visibility(note, True)
            self.assertEqual(spi.call_args.args[0], 0x1042)
        self.assertTrue(note.winfo_ismapped())
        self.assertFalse(motion._active)
        with patch.object(motion, "enabled", return_value=True), patch.object(motion, "_alpha", return_value=False):
            self.app.animate_note_visibility(note, False)
        self.assertFalse(note.winfo_ismapped())
        self.assertEqual(self.native_frame(note), (*origin, 204))
        self.assertFalse(motion._active)

    def test_cross_monitor_motion_fades_in_place_and_drag_settles_it(self):
        self.click_bar()
        note, bookmark = self.app.notes["0"], self.dock.bookmarks["0"]
        note.hide()
        self.root.update()
        origin = self.native_frame(note)[:2]
        motion = self.app._note_motion
        with patch.object(motion, "enabled", return_value=True), \
                patch.object(main.user32, "MonitorFromPoint", side_effect=(1, 2)):
            self.click(bookmark, settle=False)
        self.assertEqual(motion._active[note].start, (*origin, 0))
        note._drag_press(SimpleNamespace(x_root=origin[0]+20, y_root=origin[1]+20))
        self.assertFalse(motion._active)
        self.assertIsNone(motion._job)
        self.assertEqual(self.native_frame(note), (*origin, 255))
        note._drag_move(SimpleNamespace(x_root=origin[0]+40, y_root=origin[1]+50))
        self.root.update()
        note._drag_release(None)
        self.assertEqual((note.d["x"], note.d["y"]), (origin[0]+20, origin[1]+30))

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
            for bookmark in original.values():
                self.assertEqual(bool(bookmark.bar.find_withtag("dock-note-visible")), visible)
        self.app.notes["0"].hide()
        self.root.update()
        self.assertFalse(original["0"]._note_visible)
        self.assertFalse(original["0"].bar.find_withtag("dock-note-visible"))
        self.assertEqual(self.dock.bookmarks, original)

    def test_opening_pinned_note_under_bookmarks_keeps_bookmark_clickable(self):
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
        self.app.notes["1"].hide()
        self.click_bar()
        shown, hidden = self.dock.bookmarks["0"], self.dock.bookmarks["1"]
        for bookmark, neighbor in ((shown, hidden), (hidden, shown)):
            original = (bookmark.winfo_x(), bookmark.winfo_width())
            neighbor_geometry = neighbor.winfo_geometry()
            text = self.label(bookmark)
            bookmark._enter()
            self.wait(0.15)
            self.assertEqual(self.label(bookmark), text)
            self.assertEqual(bookmark.winfo_width(), original[1]+12)
            self.assertEqual(bookmark.winfo_x()+bookmark.bar.winfo_width(), main.user32.GetSystemMetrics(0))
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
        self.assertFalse(self.dock.expanded)
        self.assertIsNone(self.dock._empty_tip)
        self.restart()
        self.assertFalse(self.app.notes)
        self.dock._enter()
        self.assertEqual(self.label(self.dock), "z-note")
        self.click_bar(settle=False)
        self.assertFalse(self.dock.expanded)
        self.assertFalse(self.dock.bookmarks)
        self.assertEqual(self.label(self.dock), "z-note")
        bar = self.dock.bar
        self.assertEqual(bar.itemcget("dock-disclosure", "image"), str(self.dock.disclosure_icons[0]))
        self.assertTrue(self.dock._empty_tip.winfo_ismapped())
        self.dock._leave()
        self.assertIsNone(self.dock._empty_tip)

        self.click_bar(settle=False)
        self.assertTrue(self.app.recover_note("0"))
        self.root.update()
        self.assertIsNone(self.dock._empty_tip)
        self.app.hide_all()
        self.click_bar()
        self.assertTrue(self.dock.expanded)
        self.assertEqual(list(self.dock.bookmarks), ["0"])

    def test_empty_tip_restarts_and_clears_on_drag_new_note_and_disable(self):
        for note in list(self.app.notes.values()):
            self.app.remove_note(note)
        self.root.update()
        self.click_bar(settle=False)
        first = self.dock._empty_tip
        self.wait(0.65)
        self.click_bar(settle=False)
        self.assertFalse(first.winfo_exists())
        self.wait(0.65)
        self.assertTrue(self.dock._empty_tip.winfo_ismapped())
        self.wait(0.60)
        self.assertIsNone(self.dock._empty_tip)
        self.assertIsNone(self.dock._empty_tip_job)

        self.click_bar(settle=False)
        press = SimpleNamespace(x_root=self.dock.winfo_rootx()+30, y_root=self.dock.winfo_rooty()+15)
        moved = SimpleNamespace(x_root=press.x_root, y_root=press.y_root+12)
        self.dock._start_drag(press)
        self.dock._move_drag(moved)
        self.dock._end_drag(moved)
        self.assertIsNone(self.dock._empty_tip)

        self.click_bar(settle=False)
        note = self.app.new_note()
        self.root.update()
        self.assertIsNone(self.dock._empty_tip)
        self.assertFalse(self.dock.expanded)
        self.app.remove_note(note)
        self.root.update()
        self.click_bar(settle=False)
        self.app.toggle_side_dock()
        self.root.update()
        self.assertFalse(self.dock.winfo_ismapped())
        self.assertIsNone(main.Tooltip._cur)
        self.assertIsNone(self.dock._empty_tip_job)
        self.app.toggle_side_dock()
        self.root.update()
        self.click_bar(settle=False)
        self.dock.destroy()
        self.assertIsNone(main.Tooltip._cur)
        self.assertIsNone(self.dock._empty_tip_job)

    def test_large_system_scaling_keeps_empty_label_readable(self):
        scaling = self.root.tk.call("tk", "scaling")
        try:
            self.root.tk.call("tk", "scaling", 3.0)
            self.dock.destroy()
            self.dock = self.app.dock = main.SideDock(self.app)
            self.dock.refresh()
            self.root.update()
            self.app.notes["0"].d["title"] = "便签标题名"
            self.app.notes["1"].d.update(title="", lines=[])
            self.app.data["settings"]["sideDockBlankNumbers"] = {"1": 123}
            self.click_bar()
            self.assertTrue(self.label(self.dock.bookmarks["0"]).endswith("…"))
            bookmark = self.dock.bookmarks["1"]
            self.assertEqual(self.label(bookmark), "#123")
            bookmark.tip._show()
            canvas, = main.Tooltip._cur.winfo_children()
            label = next(item for item in canvas.find_all() if canvas.type(item) == "text")
            self.assertEqual(canvas.itemcget(label, "text"), "便签 123")
            main.Tooltip._hide()
            widths = {window.bar.winfo_width() for window in self.dock.bookmarks.values()}
            entry_width = self.dock.bar.winfo_width()
            self.assertEqual(widths, {round(entry_width * 1.14)})
            self.assertGreater(next(iter(widths)), entry_width+12)
            for note in list(self.app.notes.values()):
                self.app.remove_note(note)
            self.root.update()
            self.click_bar(settle=False)
            popup = self.dock._empty_tip
            canvas, = popup.winfo_children()
            label = next(item for item in canvas.find_all() if canvas.type(item) == "text")
            self.assertEqual(canvas.itemcget(label, "text"), "暂无便签")
            self.assertLessEqual(canvas.bbox(label)[2], canvas.winfo_width()-8)
            work = self.dock.work_area()
            self.assertGreaterEqual(popup.winfo_x(), work.left)
            self.assertLessEqual(popup.winfo_x()+popup.winfo_width(), self.dock.winfo_x())
            self.assertGreaterEqual(popup.winfo_y(), work.top)
            self.assertLessEqual(popup.winfo_y()+popup.winfo_height(), work.bottom)
        finally:
            self.root.tk.call("tk", "scaling", scaling)

    def test_two_digit_blank_labels_fit_at_high_scaling(self):
        original_scaling = self.root.tk.call("tk", "scaling")
        for note in self.app.notes.values():
            note.d.update(title="", lines=[])
        self.app.data["settings"]["sideDockBlankNumbers"] = {"0": 10, "1": 11, "2": 12}
        try:
            for dpi in (144, 192, 240):
                with self.subTest(dpi=dpi):
                    self.root.tk.call("tk", "scaling", dpi / 72)
                    self.dock.destroy()
                    self.dock = self.app.dock = main.SideDock(self.app)
                    self.dock.refresh()
                    self.root.update()
                    self.click_bar()
                    for key, number in (("0", 10), ("1", 11), ("2", 12)):
                        bookmark = self.dock.bookmarks[key]
                        self.assertIn(self.label(bookmark), (f"便签 {number}", f"便签{number}"))
                        self.assertLess(bookmark.bar.bbox("all")[2], bookmark.bar.winfo_width())
        finally:
            self.root.tk.call("tk", "scaling", original_scaling)

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

    def test_right_click_bookmark_deletes_note_to_history(self):
        self.click_bar()
        bookmark = self.dock.bookmarks["1"]
        event = type("Event", (), {"x_root": 100, "y_root": 100})()
        self.dock._sorting_bookmark = bookmark
        bookmark._show_delete_menu(event)
        self.assertIsNone(getattr(self.dock, "_context_menu", None))
        self.dock._sorting_bookmark = None
        bookmark._show_delete_menu(event)
        self.root.update()
        menu = self.dock._context_menu
        self.assertTrue(menu.winfo_exists())
        self.assertLess(menu.winfo_reqwidth(), 140)
        self.assertLess(menu.winfo_reqheight(), 50)
        # 菜单打开期间书签保持左伸；菜单关闭且鼠标不在其上时才缩回。
        bookmark._leave()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(bookmark._extension, bookmark.HOVER_EXTENSION)
        # 菜单打开时再次右键：本书签上只关闭；另一书签上则切换到它的菜单。
        menu.event_generate("<Button-3>", x=5, y=5, rootx=bookmark.winfo_rootx() + 5,
                            rooty=bookmark.winfo_rooty() + 5)
        self.root.update()
        self.assertFalse(menu.winfo_exists())
        bookmark._show_delete_menu(event)
        self.root.update()
        menu = self.dock._context_menu
        other = self.dock.bookmarks["2"]
        menu.event_generate("<Button-3>", x=5, y=5, rootx=other.winfo_rootx() + 5,
                            rooty=other.winfo_rooty() + 5)
        self.root.update()
        self.assertFalse(menu.winfo_exists())
        self.assertIsNot(self.dock._context_menu, menu)
        self.assertTrue(self.dock._context_menu.winfo_exists())
        self.dock._context_menu.destroy()
        calls = []
        with patch.object(main.NoteWindow, "_show_context_menu",
                          lambda _w, _x, _y, actions, **_kw: calls.append(actions)):
            bookmark._show_delete_menu(event)
        self.assertEqual([label for label, _ in calls[0]], ["删除"])
        calls[0][0][1]()
        self.root.update()
        self.assertNotIn("1", self.app.notes)
        self.assertNotIn("1", self.dock.bookmarks)
        self.assertIn("1", [n["id"] for n in self.app.data["deletedNotes"]])
        self.assertTrue(self.app.recover_note("1"))

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

    def test_blank_bookmark_name_does_not_change_new_note_title(self):
        self.app.new_note()
        note = self.app.new_note()
        self.click_bar()
        bookmark = self.dock.bookmarks[note.d["id"]]
        chip, = (item for item in note.title_chip.find_all() if note.title_chip.type(item) == "text")
        self.assertEqual(note.title_chip.itemcget(chip, "text"), "无标题")
        self.assertFalse(note.d.get("title"))
        self.assertEqual(bookmark.full_label, "便签 2")

        note.text.insert("1.end", "周五确认酒店")
        note._sync_lines_from_text()
        self.root.update()
        self.assertEqual(bookmark.full_label, "周五确认酒店")
        note.edit_title()
        self.root.update()
        note._title_editor.insert(0, "旅行清单")
        note._title_editor.event_generate("<Return>")
        self.root.update()
        self.assertEqual(bookmark.full_label, "旅行清单")
        note.text.delete("1.0", "end")
        note._sync_lines_from_text()
        note.edit_title()
        self.root.update()
        note._title_editor.delete(0, "end")
        note._title_editor.event_generate("<Return>")
        self.root.update()
        self.assertEqual(bookmark.full_label, "便签 2")
        self.assertFalse(note.d.get("title"))
        self.assertIs(self.dock.bookmarks[note.d["id"]], bookmark)

    def test_blank_numbers_continue_from_current_max_and_restart_when_all_deleted(self):
        for note in list(self.app.notes.values()):
            self.app.remove_note(note)  # 夹具里的有标题便签先移走，只留空白便签。
        notes = [self.app.new_note() for _ in range(4)]
        self.click_bar()
        label = lambda note: self.dock.bookmarks[note.d["id"]].full_label
        self.assertEqual([label(note) for note in notes], ["便签 1", "便签 2", "便签 3", "便签 4"])
        # 删掉 1、2：现有最大号是 4，新便签取 5。
        self.app.remove_note(notes[0])
        self.app.remove_note(notes[1])
        fifth = self.app.new_note()
        self.root.update()
        self.assertEqual(label(fifth), "便签 5")
        # 隐藏的便签仍然占号。
        fifth.hide()
        sixth = self.app.new_note()
        self.root.update()
        self.assertEqual(label(sixth), "便签 6")
        # 全部删除后从 1 开始。
        for note in (notes[2], notes[3], fifth, sixth):
            self.app.remove_note(note)
        fresh = self.app.new_note()
        self.root.update()
        number = lambda note: self.app.data["settings"]["sideDockBlankNumbers"][note.d["id"]]
        self.assertEqual(number(fresh), 1)  # 收纳全空时会收起，直接看分配到的编号。
        # 还原的便签原号已被占用时取最大号 + 1；原号空着时沿用。
        self.assertTrue(self.app.recover_note(notes[0].d["id"]))
        self.root.update()
        self.assertTrue(self.app.recover_note(notes[3].d["id"]))
        self.root.update()
        self.assertEqual(number(self.app.notes[notes[0].d["id"]]), 2)
        self.assertEqual(number(self.app.notes[notes[3].d["id"]]), 4)

    def test_blank_numbers_follow_note_ids_through_sort_delete_restore_and_restart(self):
        first, second, third, fourth = [self.app.new_note() for _ in range(4)]
        ids = [note.d["id"] for note in (first, second, third, fourth)]
        self.click_bar()
        expected = dict(zip(ids, range(1, 5)))
        self.assertEqual(self.app.data["settings"]["sideDockBlankNumbers"], expected)
        self.drag_bookmark(ids[3], ids[0])
        self.assertLess(self.bookmark_order().index(ids[3]), self.bookmark_order().index(ids[0]))
        self.assertEqual(self.dock.bookmarks[ids[3]].full_label, "便签 4")

        self.app.remove_note(third)
        fifth = self.app.new_note()
        fifth_id = fifth.d["id"]
        self.root.update()
        self.assertEqual(self.dock.bookmarks[fifth_id].full_label, "便签 5")
        self.assertEqual(self.dock.bookmarks[ids[3]].full_label, "便签 4")
        self.assertTrue(self.app.recover_note(ids[2]))
        self.root.update()
        self.assertEqual(self.dock.bookmarks[ids[2]].full_label, "便签 3")

        self.app.remove_note(fifth)
        sixth = self.app.new_note()
        sixth_id = sixth.d["id"]
        self.root.update()
        # 回收站里的“便签 5”不再占号：现有最大号是 4，新便签取 5。
        self.assertEqual(self.dock.bookmarks[sixth_id].full_label, "便签 5")
        self.app.remove_note(sixth)
        history = self.app.history_win = main.HistoryWindow(self.app)
        history.switch("deletedNotes")
        with patch.object(main.messagebox, "askyesno", return_value=True):
            history.delete_record(sixth_id)
        self.root.update()
        expected[fifth_id] = 5
        self.assertEqual(self.app.data["settings"]["sideDockBlankNumbers"], expected)
        self.app.save_now()
        self.assertEqual(self.saved.call_args.args[0]["settings"]["sideDockBlankNumbers"], expected)
        self.restart()
        self.click_bar()
        self.assertEqual(self.dock.bookmarks[ids[3]].full_label, "便签 4")
        self.assertTrue(self.app.recover_note(fifth_id))
        replacement = self.app.new_note()
        self.root.update()
        self.assertEqual(self.dock.bookmarks[fifth_id].full_label, "便签 5")
        self.assertEqual(self.dock.bookmarks[replacement.d["id"]].full_label, "便签 6")

    def test_labels(self):
        label = main.SideDock.note_label
        self.assertEqual(label({"title": " 标题 ", "lines": [{"text": "正文"}]}), "标题")
        self.assertEqual(label({"lines": [{"text": "\n \n正文第一行\n第二行"}]}), "正文第一行")
        self.assertEqual(label({"lines": [], "lines2": [{"text": "第二栏"}]}), "第二栏")
        self.assertEqual(label({}), "")

    def bookmark_order(self):
        return sorted(self.dock.bookmarks, key=lambda key: self.dock.bookmarks[key]._position_y)

    def drag_bookmark(self, source, target, *, cancel=None):
        window = self.dock.bookmarks[source]
        x = window.bar.winfo_rootx()+30
        start_y = window._position_y+window.height//2
        target_y = self.dock.bookmarks[target]._position_y+window.height//2
        window.bar.event_generate("<ButtonPress-1>", x=30, y=window.height//2,
                                  rootx=x, rooty=start_y)
        window.bar.event_generate("<B1-Motion>", rootx=x, rooty=target_y)
        preview = self.bookmark_order()
        if cancel is None:
            window.bar.event_generate("<ButtonRelease-1>", rootx=x, rooty=target_y)
        else:
            cancel(window, x, target_y)
        self.root.update()
        return preview

    def test_drag_reorders_bookmarks_and_persists_only_dock_order(self):
        self.app.notes["0"].d["title"] = "办公计划"
        self.app.notes["1"].d["title"] = "1109"
        self.click_bar()
        original_notes = deepcopy(self.app.data["notes"])
        self.assertEqual(self.drag_bookmark("1", "0"), ["1", "0", "2"])
        self.assertEqual(self.bookmark_order(), ["1", "0", "2"])
        self.assertEqual(self.drag_bookmark("1", "2"), ["0", "2", "1"])
        self.assertEqual(self.app.data["notes"], original_notes)
        self.app.save_now()
        self.assertEqual(self.saved.call_args.args[0]["settings"]["sideDockOrder"], ["0", "2", "1"])

        self.click_bar()
        self.click_bar()
        self.assertEqual(self.bookmark_order(), ["0", "2", "1"])
        self.restart()
        self.click_bar()
        self.assertEqual(self.bookmark_order(), ["0", "2", "1"])
        new_note = self.app.new_note()
        self.app.remove_note(self.app.notes["0"])
        self.assertTrue(self.app.recover_note("0"))
        self.root.update()
        expected = ["2", "1", new_note.d["id"], "0"]
        self.assertEqual(self.bookmark_order(), expected)
        self.app.toggle_side_dock()
        self.root.update()
        self.assertFalse(self.dock.bookmarks)
        self.app.toggle_side_dock()
        self.root.update()
        self.click_bar()
        self.assertEqual(self.bookmark_order(), expected)

    def test_bookmark_drag_cancels_outside_or_on_refresh(self):
        self.click_bar()
        visible = self.app.notes["1"].d["visible"]

        def release_outside(window, x, y):
            window.bar.event_generate("<ButtonRelease-1>", rootx=x-300, rooty=y)

        def refresh_and_release(window, x, y):
            self.dock.refresh()
            window.bar.event_generate("<ButtonRelease-1>", rootx=x, rooty=y)

        for cancel in (release_outside, refresh_and_release):
            with self.subTest(cancel=cancel.__name__):
                self.assertEqual(self.drag_bookmark("1", "0", cancel=cancel), ["1", "0", "2"])
                self.assertEqual(self.bookmark_order(), ["0", "1", "2"])
                self.assertNotIn("sideDockOrder", self.app.data["settings"])
                self.assertEqual(self.app.notes["1"].d["visible"], visible)
        self.click(self.dock.bookmarks["1"])
        self.assertNotEqual(self.app.notes["1"].d["visible"], visible)

    def test_dragged_bookmark_follows_pointer_vertically_while_staying_docked(self):
        self.click_bar()
        first, dragged = (self.dock.bookmarks[key] for key in ("0", "1"))
        left, top = dragged.winfo_rootx(), dragged.winfo_rooty()
        first_top = first.winfo_rooty()
        x, y = left+30, top+10
        dragged.bar.event_generate("<ButtonPress-1>", x=30, y=10, rootx=x, rooty=y)
        dragged.bar.event_generate("<B1-Motion>", rootx=x-20, rooty=y+8)
        self.root.update()
        self.assertEqual((dragged.winfo_rootx(), dragged.winfo_rooty()), (left, top+8))
        self.assertEqual(first.winfo_rooty(), first_top)

        target_y = first_top+first.height//2
        dragged.bar.event_generate("<B1-Motion>", rootx=x-20, rooty=target_y)
        self.root.update()
        self.assertEqual((dragged.winfo_rootx(), dragged.winfo_rooty()), (left, target_y-10))
        self.assertEqual(first.winfo_rooty(), top)

        dragged.bar.event_generate("<ButtonRelease-1>", rootx=x-20, rooty=target_y)
        self.root.update()
        self.assertEqual(self.bookmark_order(), ["1", "0", "2"])
        self.assertEqual(dragged.winfo_rooty(), first_top)
        self.assertEqual(dragged.winfo_rootx()+dragged.bar.winfo_width(), main.user32.GetSystemMetrics(0))

    def test_scroll_caps_at_fifteen_and_reorders_only_visible_bookmarks(self):
        self.add_many()
        work = self.dock.work_area()
        work.bottom = (work.top+16+self.dock.bar_height+10+16*(self.dock.bookmark_height+4)
                       + self.button_room())
        with patch.object(self.dock, "work_area", return_value=work):
            self.click_bar()
            self.assertEqual(self.bookmark_order(), [str(i) for i in range(15)])
            keep = self.dock.bookmarks["1"]
            self.dock._wheel(SimpleNamespace(delta=-120))
            self.wait(0.16)
            self.assertEqual(self.bookmark_order(), [str(i) for i in range(1, 16)])
            self.assertIs(self.dock.bookmarks["1"], keep)
            before = self.app.notes["1"].d["visible"]
            self.click(keep)
            self.assertNotEqual(self.app.notes["1"].d["visible"], before)
            visible = ["15", *(str(i) for i in range(1, 15))]
            self.assertEqual(self.drag_bookmark("15", "1"), visible)
            self.assertEqual(self.bookmark_order(), visible)
            self.assertEqual(self.app.data["settings"]["sideDockOrder"],
                             ["0", *visible, *(str(i) for i in range(16, 24))])
            self.click_bar()
            self.click_bar()
            self.assertEqual(self.dock._offset, 0)
            self.assertEqual(self.bookmark_order(), ["0", *visible[:-1]])

    def test_scroll_moves_real_windows_and_clips_rows_at_the_edges(self):
        self.add_many()
        self.app.notes["1"].d["title"] = "很长的标题需要截断显示" * 3  # 只有被截断的标题才有全名提示。
        self.click_bar()
        dock = self.dock
        top = dock.bookmarks["0"].winfo_rooty()
        height = dock.bookmark_height
        step = height+dock.ROW_GAP
        bottom = top+(dock._capacity-1)*step+height
        keep = dock.bookmarks["1"]
        bar_y = dock.winfo_rooty()
        native_maps = []
        deiconify = main.DockBookmark.deiconify

        def map_bookmark(window):
            deiconify(window)
            hwnd = main.user32.GetParent(window.winfo_id()) or window.winfo_id()
            native_maps.append((window.note_id, keep.winfo_rooty(), keep._position_y,
                                bool(main.user32.IsWindowVisible(hwnd))))

        with patch.object(main.time, "perf_counter", return_value=100.0) as clock, \
                patch.object(dock, "lift", wraps=dock.lift) as lift, \
                patch.object(dock, "winfo_containing", return_value=keep.bar):
            dock._wheel(SimpleNamespace(delta=120))
            self.assertIsNone(dock._scroll_job)
            dock._wheel(SimpleNamespace(delta=-120))
            clock.return_value += 0.04
            with patch.object(main.DockBookmark, "deiconify", map_bookmark):
                self.wait(0.03)
            self.assertEqual(native_maps, [(str(dock._capacity), keep._position_y, keep._position_y, True)])
            self.assertLess(top, keep.winfo_rooty())
            self.assertLess(keep.winfo_rooty(), top+step)
            self.assertEqual(dock.winfo_rooty(), bar_y)
            self.assertEqual(len(dock.bookmarks), dock._capacity+1)
            spans = sorted((w.winfo_rooty(), w.winfo_rooty()+w.winfo_height())
                           for w in dock.bookmarks.values())
            self.assertEqual((spans[0][0], spans[-1][1]), (top, bottom))
            self.assertLess(spans[0][1]-top, height)
            self.assertLess(bottom-spans[-1][0], height)
            self.assertTrue(all(next_top-prev_bottom == dock.ROW_GAP
                                for (_, prev_bottom), (next_top, _) in zip(spans, spans[1:])))
            self.click(keep)
            self.assertTrue(keep._note_visible)
            self.assertIsNone(main.Tooltip._cur)

            clock.return_value += 0.13
            self.wait(0.03)
            self.assertIsNone(dock._scroll_job)
            self.assertIs(dock.bookmarks["1"], keep)
            self.assertEqual(keep.winfo_rooty(), top)
            self.assertEqual(keep.winfo_height(), height)
            self.assertEqual(keep.bar.winfo_y(), 0)
            self.assertTrue(keep._hovered)
            self.assertIsNotNone(keep.tip._aid)
            self.assertNotIn("0", dock.bookmarks)
            keep.tip._cancel()
            keep.tip._show()
            canvas, = main.Tooltip._cur.winfo_children()
            label = next(item for item in canvas.find_all() if canvas.type(item) == "text")
            self.assertEqual(canvas.itemcget(label, "text"), keep.full_label)

            dock._wheel(SimpleNamespace(delta=-24*120))
            clock.return_value += 0.13
            self.wait(0.03)
            self.assertEqual(dock._offset, 24-dock._capacity)
            dock._wheel(SimpleNamespace(delta=-120))
            self.assertIsNone(dock._scroll_job)
            lift.assert_not_called()

    def test_scroll_retargets_and_cancels_cleanly_on_refresh_and_close(self):
        self.add_many()
        self.click_bar()
        dock = self.dock
        keep = dock.bookmarks["1"]
        original_y = keep.winfo_rooty()
        with patch.object(main.time, "perf_counter", return_value=100.0) as clock:
            dock._wheel(SimpleNamespace(delta=-120))
            dock._wheel(SimpleNamespace(delta=-120))
            self.assertEqual(dock._offset, 2)
            clock.return_value += 0.02
            self.wait(0.03)
            midway_y = keep.winfo_rooty()
            self.assertLess(midway_y, original_y)
            dock._wheel(SimpleNamespace(delta=240))
            self.assertEqual(dock._offset, 0)
            self.assertEqual(keep.winfo_rooty(), midway_y)
            clock.return_value += 0.02
            self.wait(0.03)
            self.assertLess(midway_y, keep.winfo_rooty())
            self.assertLess(keep.winfo_rooty(), original_y)

            dock.refresh()
            self.root.update()
            self.assertIsNone(dock._scroll_job)
            self.assertEqual(keep.winfo_rooty(), original_y)
            self.assertEqual(len(dock.bookmarks), dock._capacity)

        dock._wheel(SimpleNamespace(delta=-120))
        self.click_bar()
        self.assertIsNone(dock._scroll_job)
        self.assertFalse(dock.expanded)
        self.assertFalse(dock.bookmarks)

        self.click_bar()
        dock._wheel(SimpleNamespace(delta=-120))
        self.app.toggle_side_dock()
        self.root.update()
        self.assertIsNone(dock._scroll_job)
        self.assertFalse(dock.bookmarks)

    def test_bottom_expansion_moves_up_temporarily_and_stays_inside_work_area(self):
        self.add_many()
        self.app.data["settings"]["sideDockY"] = 1.0
        self.dock.refresh()
        self.root.update()
        original_y = self.dock.winfo_y()
        self.click_bar()
        work = self.dock.work_area()
        self.assertLess(self.dock.winfo_y(), original_y)
        last = self.dock.bookmarks[self.bookmark_order()[-1]]
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

    def test_two_rows_fit_exactly_and_scroll_in_short_work_area(self):
        self.add_many()
        work = self.dock.work_area()
        room = self.button_room()
        work.bottom = work.top+16+self.dock.bar_height+10+2*self.dock.bookmark_height+4+room
        button = self.dock.hide_all_button
        with patch.object(self.dock, "work_area", return_value=work):
            self.click_bar()
            self.assertEqual(list(self.dock.bookmarks), ["0", "1"])
            # 一键隐藏按钮固定占最下面一行，书签底边在它上方。
            self.assertEqual(self.dock.bookmarks["1"].winfo_y()+self.dock.bookmark_height,
                             work.bottom-8-room)
            self.assertEqual(button.winfo_y()+button.height, work.bottom-8)
            self.dock._wheel(SimpleNamespace(delta=-120))
            self.wait(0.16)
            self.assertEqual(list(self.dock.bookmarks), ["1", "2"])
            self.assertEqual(self.dock.bookmarks["2"].winfo_y()+self.dock.bookmark_height,
                             work.bottom-8-room)
            self.assertEqual(button.winfo_y()+button.height, work.bottom-8)

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
        self.assertEqual(self.dock.bookmarks["0"].winfo_y(), y-80+self.dock.bar_height+10)
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
        with patch.object(main.time, "perf_counter", return_value=100.0) as clock:
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
            self.assertLess(mid_y, entry_y+self.dock.bar_height+10)
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

    def test_startup_checkbox_persists_and_restores_note_visibility(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            target = str(Path(folder) / "notes.json")
            with patch.object(storage, "DATA_DIR", folder), patch.object(storage, "DATA_FILE", target), \
                    patch.object(storage, "BACKUP_DIR", str(Path(folder) / "backup")), \
                    patch.object(main, "save_data", storage.save_data), \
                    patch.object(main, "load_data", storage.load_data):
                self.app.data["settings"]["showOnStartup"] = True
                self.click_bar()
                self.click(self.dock.bookmarks["0"])
                self.assertFalse(self.app.notes["0"].d["visible"])
                for enabled in (False, True):
                    settings = main.SettingsWindow(self.app)
                    self.root.update()
                    self.assertEqual(settings.v_show.get(), not enabled)
                    checkbox = next(widget for canvas in settings.winfo_children()
                        for frame in canvas.winfo_children() for widget in frame.winfo_children()
                        if isinstance(widget, main.tk.Checkbutton)
                        and str(widget.cget("variable")) == str(settings.v_show))
                    checkbox.invoke()
                    settings.destroy()
                    self.wait(0.75)
                    self.assertIs(storage.load_data()["settings"]["showOnStartup"], enabled)
                    self.restart()
                    for key, layer in (("0", "normal"), ("1", "top"), ("2", "bottom")):
                        note = self.app.notes[key]
                        visible = enabled or key != "0"
                        self.assertIs(note.d["visible"], visible)
                        self.assertEqual(note.state(), "normal" if visible else "withdrawn")
                        self.assertEqual(note.d["z"], layer)
                        self.assertEqual(bool(note.attributes("-topmost")), layer == "top")
                    self.assertFalse(self.dock.expanded)
                settings = main.SettingsWindow(self.app)
                self.assertTrue(settings.v_show.get())
                settings.destroy()

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            target = str(Path(folder) / "notes.json")
            with patch.object(storage, "DATA_DIR", folder), patch.object(storage, "DATA_FILE", target), \
                    patch.object(storage, "BACKUP_DIR", str(Path(folder) / "backup")):
                self.app.data["settings"].update(sideDock=False, sideDockY=0.8,
                                                 sideDockOrder=["1", "0", "2"],
                                                 sideDockBlankNumbers={"1": 3})
                storage.save_data(self.app.data)
                restored = storage.load_data()
        self.assertFalse(restored["settings"]["sideDock"])
        self.assertEqual(restored["settings"]["sideDockY"], 0.8)
        self.assertEqual(restored["settings"]["sideDockOrder"], ["1", "0", "2"])
        self.assertEqual(restored["settings"]["sideDockBlankNumbers"], {"1": 3})

    def test_tooltip_only_for_truncated_titles_after_longer_dwell(self):
        self.app.notes["0"].d["title"] = "短标题"
        self.app.notes["1"].d["title"] = "很长的中文标题，需要完整显示。" * 3
        self.click_bar()
        short, long = self.dock.bookmarks["0"], self.dock.bookmarks["1"]
        self.assertNotIn("…", self.label(short))
        self.assertIn("…", self.label(long))
        self.assertEqual(long.tip._delay, 800)
        short.tip._show()
        self.assertIsNone(main.Tooltip._cur)
        long.tip._show()
        canvas, = main.Tooltip._cur.winfo_children()
        label = next(item for item in canvas.find_all() if canvas.type(item) == "text")
        self.assertEqual(canvas.itemcget(label, "text"), long.full_label)
        main.Tooltip._hide()

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

    def test_hide_all_button_shows_only_while_expanded_with_visible_notes(self):
        button = self.dock.hide_all_button
        self.assertFalse(button.winfo_ismapped())
        self.click_bar()
        order = self.bookmark_order()
        last = self.dock.bookmarks[order[-1]]
        self.assertTrue(button.winfo_ismapped())
        self.assertEqual(button.winfo_y(), last.winfo_y()+self.dock.bookmark_height+self.dock.ENTRY_GAP)
        self.assertEqual(button.winfo_x()+button.bar.winfo_width(), main.user32.GetSystemMetrics(0))
        self.assertEqual(button.bar.winfo_width(), button.WIDTH)
        self.assertTrue(button.bar.find_withtag("dock-hide-all"))
        hwnd = main.user32.GetParent(button.winfo_id()) or button.winfo_id()
        preference = ctypes.c_int()
        main.dwmapi.DwmGetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
        self.assertEqual(preference.value, 3)
        self.assertTrue(button.attributes("-topmost"))
        positions = {key: window.winfo_y() for key, window in self.dock.bookmarks.items()}

        with patch.object(self.app, "hide_all", wraps=self.app.hide_all) as hide_all:
            self.click(button)
            self.wait(0.2)
        hide_all.assert_called_once_with(animate=True)
        self.assertFalse(any(note.d["visible"] for note in self.app.notes.values()))
        self.assertTrue(self.dock.expanded)
        self.assertFalse(button.winfo_ismapped())
        self.assertEqual({key: window.winfo_y() for key, window in self.dock.bookmarks.items()}, positions)
        self.assertFalse(any(window._note_visible for window in self.dock.bookmarks.values()))

        self.click_bar()
        self.click_bar()
        self.assertFalse(button.winfo_ismapped())  # 全部隐藏时展开也不显示。
        self.click(self.dock.bookmarks["0"])
        self.root.update()
        self.assertTrue(button.winfo_ismapped())
        self.assertEqual({key: window.winfo_y() for key, window in self.dock.bookmarks.items()}, positions)

        self.click_bar()
        self.assertFalse(button.winfo_ismapped())
        self.click_bar()
        self.app.toggle_side_dock()
        self.root.update()
        self.assertFalse(button.winfo_ismapped())
        self.app.toggle_side_dock()
        self.root.update()
        self.assertFalse(button.winfo_ismapped())
        self.click_bar()
        self.assertTrue(button.winfo_ismapped())

    def test_hide_all_button_ignores_clicks_while_dock_is_busy(self):
        button = self.dock.hide_all_button
        press = SimpleNamespace(x_root=10, y_root=10)
        with patch.object(self.app, "hide_all") as hide_all:
            self.click_bar(settle=False)
            self.assertIsNotNone(self.dock._transition_job)
            button._drag_press(press)
            button._drag_release(press)
            self.wait(0.24)
            button._drag_press(press)
            self.dock._sorting_bookmark = self.dock.bookmarks["0"]
            button._drag_release(press)
            self.dock._sorting_bookmark = None
            button._drag_press(press)
            button._drag_move(SimpleNamespace(x_root=10, y_root=20))
            button._drag_release(SimpleNamespace(x_root=10, y_root=20))
            hide_all.assert_not_called()
            self.click_bar()
            self.add_many()
            self.click_bar()
            self.dock._wheel(SimpleNamespace(delta=-120))
            self.assertIsNotNone(self.dock._scroll_job)
            button._drag_press(press)
            button._drag_release(press)
            hide_all.assert_not_called()

    def test_hide_all_button_stays_put_below_scrolling_bookmarks(self):
        self.add_many()
        self.click_bar()
        button = self.dock.hide_all_button
        work = self.dock.work_area()
        self.assertEqual(len(self.dock.bookmarks), self.dock._capacity)
        self.assertLessEqual(button.winfo_y()+button.height, work.bottom-8)
        y = button.winfo_y()
        self.dock._wheel(SimpleNamespace(delta=-120))
        self.wait(0.16)
        self.assertEqual(self.bookmark_order()[0], "1")
        self.assertEqual(button.winfo_y(), y)
        self.assertTrue(button.winfo_ismapped())

    def test_hide_all_button_hover_keeps_size_and_tooltip_appears_on_left(self):
        self.click_bar()
        button = self.dock.hide_all_button
        edge = main.user32.GetSystemMetrics(0)
        self.assertEqual(self.dock.bookmarks["0"].HOVER_EXTENSION, 12)
        icon_x, _ = button.bar.coords(button.bar.find_withtag("dock-hide-all")[0])
        self.assertEqual(icon_x, button.WIDTH/2+2)  # 略偏右，补偿左侧阴影。
        button.bar.event_generate("<Enter>")
        self.wait(0.15)
        self.assertEqual(button.bar.winfo_width(), button.WIDTH)  # 悬停不伸出。
        self.assertEqual(button.winfo_x()+button.bar.winfo_width(), edge)
        button.bar.event_generate("<Leave>")
        self.wait(0.15)
        self.assertEqual(button.bar.winfo_width(), button.WIDTH)
        self.assertEqual(button.tip._delay, 800)
        button.tip._cancel()
        button.tip._show()
        self.root.update()
        popup = main.Tooltip._cur
        canvas, = popup.winfo_children()
        label = next(item for item in canvas.find_all() if canvas.type(item) == "text")
        self.assertEqual(canvas.itemcget(label, "text"), "收纳所有")
        self.assertLessEqual(popup.winfo_x()+popup.winfo_width(), button.winfo_rootx())
        button.bar.event_generate("<ButtonPress-1>", x=20, y=15)
        self.root.update()
        self.assertIsNone(main.Tooltip._cur)

    def test_native_corners_and_topmost_tool_windows(self):
        import ctypes
        self.click_bar()
        for window in (self.dock, *self.dock.bookmarks.values()):
            hwnd = main.user32.GetParent(window.winfo_id()) or window.winfo_id()
            preference = ctypes.c_int()
            self.assertEqual(main.dwmapi.DwmGetWindowAttribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference)), 0)
            self.assertEqual(preference.value, 3)  # DWMWCP_ROUNDSMALL
            self.assertTrue(main.user32.GetWindowLongPtrW(hwnd, -20) & 0x80)
            self.assertTrue(window.attributes("-topmost"))
            self.assertEqual(window.winfo_x()+window.bar.winfo_width(), main.user32.GetSystemMetrics(0))


class HiddenNoteRevealPaintTests(unittest.TestCase):
    """启动时隐藏的便签经一键显示淡入后，底栏状态文字必须画出来，不能透出下层窗口。"""

    def setUp(self):
        self.errors = []
        note = dict(w=440, h=477, opacity=1.0, color="白", editedAt="2026-11-14T12:00:00")
        self.data = {"schemaVersion": 3, "welcomeVersion": main.APP_VERSION,
                     "settings": dict(main.DEFAULT_SETTINGS, showOnStartup=False),
                     "notes": [dict(note, id="shown", x=900, y=200, z="normal", visible=True,
                                    lines=[dict(type="text", text="a", checked=False)]),
                               dict(note, id="hidden", x=2036, y=524, z="top", visible=False,
                                    lines=[dict(type="todo", text="", checked=False)])]}
        self.enterContext(patch.object(main, "save_data"))
        self.enterContext(patch.object(main, "load_data", side_effect=lambda: deepcopy(self.data)))
        self.enterContext(patch.object(main.App, "_start_tray"))
        self.enterContext(patch.object(main.App, "_start_hotkeys"))
        self.enterContext(patch.object(main.tk.Tk, "report_callback_exception",
                                     side_effect=lambda kind, value, trace: self.errors.append(str(value))))
        self.app = main.App()
        self.app.icon = Mock()
        self.root = self.app.root

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

    def test_show_all_paints_footer_status_of_note_hidden_at_startup(self):
        note = self.app.notes["hidden"]
        d = note.d
        # 洋红底板先放在便签位置并压在最底层，缺画处会直接显示成洋红。
        backdrop = main.tk.Toplevel(self.root, bg="#FF00FF")
        backdrop.overrideredirect(True)
        backdrop.geometry(f"{d['w']}x{d['h']}+{d['x']}+{d['y']}")
        self.root.update()
        main.user32.SetWindowPos(main.user32.GetParent(backdrop.winfo_id()), 1,
                                 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        self.wait(1.0)
        with patch.object(self.app._note_motion, "enabled", return_value=True):
            self.app.show_all(animate=True)
            self.wait(0.6)
        self.assertEqual(note.status.cget("text"), "待办 0/1")
        x, y = note.status.winfo_rootx(), note.status.winfo_rooty()
        image = ImageGrab.grab((x, y, x+note.status.winfo_width(), y+note.status.winfo_height()),
                               all_screens=True).convert("RGB")
        holes = sum(1 for r, g, b in image.get_flattened_data() if r > 200 and g < 60 and b > 200)
        self.assertEqual(holes, 0)


if __name__ == "__main__":
    unittest.main()
