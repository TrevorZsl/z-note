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
            self.click_bar()
            self.assertTrue(self.label(self.dock.bookmarks["0"]).endswith("…"))
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
        work.bottom = work.top+16+self.dock.bar_height+10+16*(self.dock.bookmark_height+4)
        with patch.object(self.dock, "work_area", return_value=work):
            self.click_bar()
            self.assertEqual(self.bookmark_order(), [str(i) for i in range(15)])
            keep = self.dock.bookmarks["1"]
            self.dock._wheel(SimpleNamespace(delta=-120))
            self.root.update()
            self.assertEqual(self.bookmark_order(), [str(i) for i in range(1, 16)])
            self.assertIs(self.dock.bookmarks["1"], keep)
            visible = ["15", *(str(i) for i in range(1, 15))]
            self.assertEqual(self.drag_bookmark("15", "1"), visible)
            self.assertEqual(self.bookmark_order(), visible)
            self.assertEqual(self.app.data["settings"]["sideDockOrder"],
                             ["0", *visible, *(str(i) for i in range(16, 24))])
            self.click_bar()
            self.click_bar()
            self.assertEqual(self.dock._offset, 0)
            self.assertEqual(self.bookmark_order(), ["0", *visible[:-1]])

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
        work.bottom = work.top+16+self.dock.bar_height+10+2*self.dock.bookmark_height+4
        with patch.object(self.dock, "work_area", return_value=work):
            self.click_bar()
            self.assertEqual(list(self.dock.bookmarks), ["0", "1"])
            self.assertEqual(self.dock.bookmarks["1"].winfo_y()+self.dock.bookmark_height,
                             work.bottom-8)
            self.dock._wheel(SimpleNamespace(delta=-120))
            self.root.update()
            self.assertEqual(list(self.dock.bookmarks), ["1", "2"])
            self.assertEqual(self.dock.bookmarks["2"].winfo_y()+self.dock.bookmark_height,
                             work.bottom-8)

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

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            target = str(Path(folder) / "notes.json")
            with patch.object(storage, "DATA_DIR", folder), patch.object(storage, "DATA_FILE", target), \
                    patch.object(storage, "BACKUP_DIR", str(Path(folder) / "backup")):
                self.app.data["settings"].update(sideDock=False, sideDockY=0.8,
                                                 sideDockOrder=["1", "0", "2"])
                storage.save_data(self.app.data)
                restored = storage.load_data()
        self.assertFalse(restored["settings"]["sideDock"])
        self.assertEqual(restored["settings"]["sideDockY"], 0.8)
        self.assertEqual(restored["settings"]["sideDockOrder"], ["1", "0", "2"])

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
            self.assertEqual(preference.value, 3)  # DWMWCP_ROUNDSMALL
            self.assertTrue(main.user32.GetWindowLongPtrW(hwnd, -20) & 0x80)
            self.assertTrue(window.attributes("-topmost"))
            self.assertEqual(window.winfo_x()+window.bar.winfo_width(), main.user32.GetSystemMetrics(0))


if __name__ == "__main__":
    unittest.main()
