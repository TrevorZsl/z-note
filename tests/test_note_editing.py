"""Windows UI regressions. Run: python -m unittest discover -s tests -v"""
import unittest
from unittest.mock import patch

from PIL import ImageChops, ImageGrab

import main


class NoteEditingTests(unittest.TestCase):
    def setUp(self):
        self._start_hotkeys_impl = main.App._start_hotkeys
        self._start_tray_impl = main.App._start_tray
        self._callback_errors = []
        self.patches = [
            patch.object(main.tk.Tk, "report_callback_exception", side_effect=lambda kind, value, trace:
                         self._callback_errors.append(f"{kind.__name__}: {value}")),
            patch.object(main, "load_data", return_value={
                "settings": dict(main.DEFAULT_SETTINGS), "notes": []}),
            patch.object(main, "save_data"),
            patch.object(main.App, "_start_tray"),
            patch.object(main.App, "_start_hotkeys"),
        ]
        for item in self.patches:
            item.start()
        self.app = main.App()
        self.note = next(iter(self.app.notes.values()))
        self.app.root.update()
        self.text = self.note.text

    def tearDown(self):
        for job in self.app.root.tk.call("after", "info"):
            self.app.root.tk.call("after", "cancel", job)
        self.app.root.destroy()
        for item in reversed(self.patches):
            item.stop()
        self.assertFalse(self._callback_errors, self._callback_errors)

    def _visible_bbox(self, index):
        box = self.text.bbox(index)
        if box:
            y = self.text.winfo_rooty() + box[1] - self.note.body.winfo_rooty()
            if y < self.note.body.winfo_height() and y + box[3] > 0:
                return box[0], y, box[2], box[3]

    def test_new_note_uses_larger_default_size(self):
        note = self.app.new_note()
        self.app.root.update()
        self.assertEqual((note.d["w"], note.d["h"]), (543, 477))
        self.assertEqual((note.winfo_width(), note.winfo_height()), (543, 477))

    def test_new_note_is_visible_without_pinning(self):
        note = self.app.new_note()
        self.app.root.update()
        self.assertEqual(note.d["z"], "bottom")
        self.assertFalse(note.attributes("-topmost"))
        self.assertTrue(note.d["visible"])
        self.assertEqual(note.state(), "normal")

    def test_startup_display_setting_controls_raising_and_preserves_pinning(self):
        from copy import deepcopy
        original = deepcopy(self.note.d)
        for show_on_startup in (True, False):
            with self.subTest(show_on_startup=show_on_startup):
                for note in self.app.notes.values():
                    note.destroy()
                self.app.notes.clear()
                self.app.data["notes"] = [
                    dict(deepcopy(original), id=f"{z}-{visible}", z=z, visible=visible)
                    for z in ("top", "bottom") for visible in (True, False)]
                self.app.show_notes = show_on_startup
                with patch.object(main.NoteWindow, "show", autospec=True,
                                  side_effect=main.NoteWindow.show) as show:
                    self.app._restore_notes()
                self.app.root.update()
                self.assertEqual(show.call_count, 4 if show_on_startup else 0)
                for note_id, note in self.app.notes.items():
                    z, was_visible = note_id.split("-")
                    visible = show_on_startup or was_visible == "True"
                    self.assertEqual(note.d["visible"], visible)
                    self.assertEqual(note.state(), "normal" if visible else "withdrawn")
                    self.assertEqual(note.d["z"], z)
                    self.assertEqual(bool(note.attributes("-topmost")), z == "top")

    def test_first_startup_shows_welcome_note_by_default(self):
        self.assertTrue(main.DEFAULT_SETTINGS["showOnStartup"])
        self.assertTrue(self.app.show_notes)
        self.assertTrue(self.note.d["visible"])
        self.assertEqual(self.note.state(), "normal")
        self.assertTrue(self.note.attributes("-topmost"))

    def test_show_all_raises_notes_and_preserves_pinning(self):
        import subprocess
        import sys

        def window_order():
            handles = []
            callback = main.ctypes.WINFUNCTYPE(main.wintypes.BOOL, main.wintypes.HWND,
                                              main.wintypes.LPARAM)(
                lambda hwnd, _: handles.append(hwnd) or True)
            main.user32.EnumWindows(callback, 0)
            return handles

        pinned = self.app.new_note()
        pinned.set_z("top")
        self.note.set_z("bottom")
        other = subprocess.Popen([sys.executable, "-c", (
            "import tkinter as tk; root = tk.Tk(); "
            "root.geometry('300x240+30+30'); root.update(); "
            "print(root.winfo_id(), flush=True); root.mainloop()")],
            stdout=subprocess.PIPE, text=True)
        def close_other():
            other.terminate()
            other.wait(timeout=5)
            other.stdout.close()
        self.addCleanup(close_other)
        other_id = int(other.stdout.readline())
        other_hwnd = main.user32.GetParent(other_id) or other_id
        def raise_other():
            for layer in (-1, -2):  # HWND_TOPMOST / HWND_NOTOPMOST
                self.assertTrue(main.user32.SetWindowPos(
                    other_hwnd, layer, 0, 0, 0, 0, main.SWP_NOMOVE | main.SWP_NOSIZE | 0x10))
        self.app.hide_all()
        raise_other()
        self.app.root.update()

        self.app.show_all()
        self.app.root.update()
        self.app.root.after(1300, self.app.root.quit)
        self.app.root.mainloop()
        order = window_order()
        for note in (self.note, pinned):
            self.assertTrue(note.d["visible"])
            self.assertLess(order.index(note.hwnd), order.index(other_hwnd))
        self.assertEqual(self.note.d["z"], "bottom")
        self.assertEqual(self.note.attributes("-topmost"), 0)
        self.assertFalse(main.user32.GetWindowLongPtrW(self.note.hwnd, -20) & 0x8)
        self.assertEqual(pinned.d["z"], "top")
        self.assertEqual(pinned.attributes("-topmost"), 1)

        raise_other()
        self.app.root.update()
        order = window_order()
        self.assertGreater(order.index(self.note.hwnd), order.index(other_hwnd))
        self.assertLess(order.index(pinned.hwnd), order.index(other_hwnd))
        self.app.hide_all()
        self.app.root.update()
        for note in (self.note, pinned):
            self.assertFalse(note.d["visible"])
            self.assertEqual(note.state(), "withdrawn")
        self.app.show_all()
        self.app.root.update()
        order = window_order()
        for note in (self.note, pinned):
            self.assertLess(order.index(note.hwnd), order.index(other_hwnd))

    def test_note_blank_regions_move_window_and_save_position(self):
        note = self.note
        w, h = note.winfo_width(), note.winfo_height()
        regions = [(note.cv, 12, h // 2), (note.cv, w - 12, h // 2),
                   (note.cv, w // 2, 10), (note.cv, w - 12, 20),
                   (note.cv, w // 2, h - 8), (note.bar, 12, 13),
                   (note.bar, w - 12, 13), (note.status, 5, 5),
                   (note.toolbar, 5, 5), (note.title_box, 0, 0)]
        for widget, px, py in regions:
            with self.subTest(widget=str(widget), point=(px, py)):
                x, y = note.winfo_x(), note.winfo_y()
                widget.event_generate("<Button-1>", x=px, y=py)
                widget.event_generate("<B1-Motion>", x=px + 15, y=py + 10)
                self.app.root.update()
                widget.event_generate("<ButtonRelease-1>", x=px + 15, y=py + 10)
                self.assertEqual((note.winfo_x(), note.winfo_y()), (x + 15, y + 10))
                self.assertEqual((note.d["x"], note.d["y"]), (x + 15, y + 10))
                self.assertEqual((note.winfo_width(), note.winfo_height()), (w, h))

    def test_show_and_hide_all_are_independent_and_repeatable(self):
        other = self.app.new_note()
        self.note.set_z("bottom")
        other.hide()
        self.app.root.update()
        original = [(w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height(), w.d["z"])
                    for w in self.app.notes.values()]
        for action in ("show", "show", "hide", "hide", "show"):
            with self.subTest(action=action):
                self.app.q.put(action)
                self.app._pump()
                self.app.root.update()
                for w, geometry in zip(self.app.notes.values(), original):
                    self.assertEqual(w.d["visible"], action == "show")
                    self.assertEqual(w.state(), "normal" if action == "show" else "withdrawn")
                    self.assertEqual((w.winfo_x(), w.winfo_y(), w.winfo_width(),
                                      w.winfo_height(), w.d["z"]), geometry)
        self.app.notes.clear()
        self.app.hide_all()
        self.app.show_all()

    def test_tray_has_separate_show_and_hide_actions(self):
        with patch.object(main.pystray, "Icon") as icon:
            self._start_tray_impl(self.app)
        menu = icon.call_args.args[3]
        hide, show = menu.items[1:3]
        self.assertEqual(hide.text, "隐藏所有便签  Ctrl+Alt+H")
        self.assertEqual(show.text, "显示所有便签  Ctrl+Alt+S")
        hide(None)
        show(None)
        self.assertEqual(self.app.q.get_nowait(), "hide")
        self.assertEqual(self.app.q.get_nowait(), "show")

    def test_legacy_toggle_hotkey_migrates_to_hide_without_overwriting_new_setting(self):
        for configured in ({"toggleHotkey": "Ctrl+Shift+H"},
                           {"toggleHotkey": "Ctrl+Shift+H", "hideHotkey": "Alt+F9"}):
            data = {"schemaVersion": 2, "settings": dict(main.DEFAULT_SETTINGS, **configured),
                    "notes": []}
            with patch.object(main, "load_data", return_value=data), \
                 patch.object(main.tk, "Tk", side_effect=RuntimeError("stop before creating UI")):
                with self.assertRaisesRegex(RuntimeError, "stop before creating UI"):
                    main.App()
            self.assertEqual(data["settings"]["hideHotkey"],
                             configured.get("hideHotkey", configured["toggleHotkey"]))
            self.assertNotIn("toggleHotkey", data["settings"])

    def test_note_outer_edge_still_resizes(self):
        note = self.note
        x, y, w, h = note.winfo_x(), note.winfo_y(), note.winfo_width(), note.winfo_height()
        note.cv.event_generate("<Button-1>", x=w - 2, y=h // 2)
        note.cv.event_generate("<B1-Motion>", x=w + 18, y=h // 2)
        self.app.root.update()
        note.cv.event_generate("<ButtonRelease-1>", x=w + 18, y=h // 2)
        self.assertEqual((note.winfo_x(), note.winfo_y()), (x, y))
        self.assertEqual((note.winfo_width(), note.d["w"]), (w + 20, w + 20))

    def test_note_controls_do_not_move_window(self):
        note = self.note
        x, y = note.winfo_x(), note.winfo_y()
        with patch.object(note, "edit_title") as title, \
                patch.object(note, "clear_done") as clear, \
                patch.object(note, "add_todo") as add:
            for widget in (note.title_chip, note.clear_btn, note.add_pill,
                           note.b_pin, note.text, note.scroll_thumb):
                widget.event_generate("<Button-1>", x=5, y=5)
                widget.event_generate("<B1-Motion>", x=20, y=15)
                widget.event_generate("<ButtonRelease-1>", x=20, y=15)
                self.app.root.update()
                self.assertEqual((note.winfo_x(), note.winfo_y()), (x, y))
            title.assert_called_once()
            clear.assert_called_once()
            add.assert_called_once()
        checked = note.d["lines"][1]["checked"]
        note._checkbox_widgets[0].event_generate("<Button-1>", x=5, y=5)
        self.app.root.update()
        self.assertEqual(note.d["lines"][1]["checked"], not checked)
        self.assertEqual((note.winfo_x(), note.winfo_y()), (x, y))

    def test_top_and_left_resize_submit_complete_frames(self):
        note = self.note
        note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i} " + "需要处理的事情" * (i % 3 + 1),
             "checked": i % 2 == 0} for i in range(12)]
        note.render()
        self.text.configure(insertwidth=0)
        self.text.tag_add("sel", "1.3", "1.end")
        note.lift()

        def capture():
            main.dwmapi.DwmFlush()
            x, y = note.winfo_rootx(), note.winfo_rooty()
            return ImageGrab.grab(bbox=(x + 24, y + 6,
                                       x + note.winfo_width() - 24,
                                       y + note.winfo_height() - 6))

        for edge in ("top", "left", "corner"):
            note.geometry("440x440+180+180")
            self.app.root.update()
            x = 2 if edge != "top" else 220
            y = 2 if edge != "left" else 220
            press = type("Pointer", (), {"x": x, "y": y,
                                          "x_root": 180 + x, "y_root": 180 + y})()
            note._edge_press(press)
            for delta in (-45, 30, -20, 250, 0):
                with self.subTest(edge=edge, delta=delta):
                    move = type("Pointer", (), {
                        "x_root": press.x_root + (delta if edge != "top" else 0),
                        "y_root": press.y_root + (delta if edge != "left" else 0)})()
                    note._edge_move(move)
                    self.assertEqual(note.winfo_width(),
                                     max(280, 440 - delta) if edge != "top" else 440)
                    self.assertEqual(note.winfo_height(),
                                     max(230, 440 - delta) if edge != "left" else 440)
                    self.assertEqual(note.winfo_x() + note.winfo_width(), 620)
                    self.assertEqual(note.winfo_y() + note.winfo_height(), 620)
                    frame = capture()
                    # 强制全量重画应与刚提交的缩放帧一致，不能靠后续 Expose 修补残影。
                    note.text.event_generate("<Expose>", x=0, y=0,
                                             width=self.text.winfo_width(),
                                             height=self.text.winfo_height())
                    self.app.root.update()
                    self.assertIsNone(ImageChops.difference(frame, capture()).getbbox())
            note._edge_release(None)
            self.assertEqual((note.d["w"], note.d["h"]), (440, 440))

    def test_typing_keeps_native_text_window_visible_during_layout(self):
        self.note.d["lines"] = [{"type": "text", "text": "", "checked": False}]
        self.note.render()
        self.app.root.update()
        layout = self.note._layout_text
        visible = []

        def record_layout():
            visible.append(bool(main.user32.GetWindowLongPtrW(
                self.text.winfo_id(), -16) & 0x10000000))  # WS_VISIBLE
            layout()

        with patch.object(self.note, "_layout_text", side_effect=record_layout):
            for char in "abc":
                self.text.insert("insert", char)
                self.note._on_keyrelease(None)
                self.app.root.update()
            self.note._begin_preedit(self.text)
            self.note._replace_preedit("zhong", 5)
            self.app.root.update()
            self.note._finish_preedit("中")
            self.app.root.update()
        self.assertTrue(visible)
        self.assertTrue(all(visible), visible)
        self.assertEqual(self.note.d["lines"][0]["text"], "abc中")

    def test_clipboard_shortcuts_sync_immediately_and_support_undo_redo(self):
        from copy import deepcopy
        note, text = self.note, self.text
        original = [{"type": "todo", "text": "原来的待办", "checked": True},
                    {"type": "todo", "text": "下一项", "checked": False}]
        note.d["lines"] = deepcopy(original)
        note.render()
        text.focus_force()
        self.app.root.update()
        try:
            clipboard = text.clipboard_get()
        except main.tk.TclError:
            clipboard = None
        try:
            text.clipboard_clear()
            text.clipboard_append("粘贴第一行\n第二行")
            text.tag_add("sel", "1.3", "1.end")
            text.event_generate("<Control-v>")
            pasted = deepcopy(note.d["lines"])
            self.assertEqual([line["text"] for line in pasted],
                             ["粘贴第一行", "第二行", "下一项"])
            self.assertTrue(pasted[0]["checked"])
            text.event_generate("<Control-z>")
            self.assertEqual(note.d["lines"], original)
            self.assertEqual(len(text.dump("1.0", "end", image=True)), 2)
            text.event_generate("<Control-y>")
            self.assertEqual(note.d["lines"], pasted)
            text.event_generate("<Control-z>")
            text.event_generate("<Control-Shift-Z>")
            self.assertEqual(note.d["lines"], pasted)
            text.tag_remove("sel", "1.0", "end")
            text.tag_add("sel", "1.3", "1.end")
            text.event_generate("<Control-c>")
            self.assertEqual(text.clipboard_get(), "粘贴第一行")
            text.event_generate("<Control-x>")
            self.assertEqual(note.d["lines"][0]["text"], "")
            text.event_generate("<Control-z>")
            self.assertEqual(note.d["lines"], pasted)
            text.event_generate("<Control-a>")
            self.assertEqual(tuple(map(str, text.tag_ranges("sel"))),
                             ("1.0", text.index("end-1c")))
            text.clipboard_clear()
            text.clipboard_append("替换所有内容")
            text.event_generate("<Control-v>")
            self.assertEqual(note.d["lines"], [
                {"type": "text", "text": "替换所有内容", "checked": False}])
            text.event_generate("<Control-z>")
            self.assertEqual(note.d["lines"], pasted)
        finally:
            text.clipboard_clear()
            if clipboard is not None:
                text.clipboard_append(clipboard)

    def test_undo_restores_todo_structure_and_new_edit_clears_redo(self):
        from copy import deepcopy
        note, text = self.note, self.text
        note.d["lines"] = [{"type": "todo", "text": "第一项", "checked": True}]
        note.render()
        original = deepcopy(note.d["lines"])
        text.mark_set("insert", "1.end")
        note._on_enter(None)
        split = deepcopy(note.d["lines"])
        note._undo_edit()
        self.assertEqual(note.d["lines"], original)
        note._undo_edit(redo=True)
        self.assertEqual(note.d["lines"], split)
        note._on_backspace(None)
        self.assertEqual(note.d["lines"], original)
        note._undo_edit()
        self.assertEqual(note.d["lines"], split)
        text.insert("insert", "新的输入")
        note._on_keyrelease(None)
        edited = deepcopy(note.d["lines"])
        note._undo_edit(redo=True)
        self.assertEqual(note.d["lines"], edited)
        note.toggle_done(0)
        note._undo_edit()
        self.assertTrue(note.d["lines"][0]["checked"])

    def test_undo_records_confirmed_ime_text_without_preedit(self):
        from copy import deepcopy
        note, text = self.note, self.text
        original = deepcopy(note.d["lines"])
        text.mark_set("insert", "2.end")
        note._begin_preedit(text)
        note._replace_preedit("zhong", 5)
        note._undo_edit()
        self.assertEqual(note.d["lines"], original)
        note._finish_preedit("中")
        self.app.root.update()
        confirmed = deepcopy(note.d["lines"])
        self.assertEqual(confirmed[1]["text"], original[1]["text"] + "中")
        note._undo_edit()
        self.assertEqual(note.d["lines"], original)
        note._undo_edit(redo=True)
        self.assertEqual(note.d["lines"], confirmed)

    def test_body_preedit_is_inline_and_only_confirmed_text_is_saved(self):
        original = self.note.d["lines"][1]["text"]
        self.text.mark_set("insert", "2.end")
        self.note._begin_preedit(self.text)
        self.note._replace_preedit("zhong", 5)
        self.note._on_keyrelease(None)
        self.assertEqual(self.text.get("2.3", "2.end"), original + "zhong")
        self.assertEqual(self.note.d["lines"][1]["text"], original)
        self.assertEqual(self.text.tag_cget("ime_preedit", "background"), "")
        self.note._finish_preedit("中")
        self.app.root.update()
        self.assertEqual(self.note.d["lines"][1]["text"], original + "中")
        self.assertFalse(self.text.tag_ranges("ime_preedit"))

    def test_cancel_restores_selected_text(self):
        original = self.text.get("2.3", "2.end")
        self.text.tag_add("sel", "2.3", "2.end")
        self.note._begin_preedit(self.text)
        self.note._replace_preedit("test", 2)
        self.note._finish_preedit()
        self.app.root.update()
        self.assertEqual(self.text.get("2.3", "2.end"), original)
        self.assertEqual(self.note.d["lines"][1]["text"], original)

    def test_title_remains_editable_with_a_tooltip_child(self):
        tooltip = main.tk.Toplevel(self.note.title_chip)
        tooltip.withdraw()
        self.note.d["title"] = "原名称"
        self.note.edit_title()
        self.app.root.update()
        entry = next(child for child in self.note.title_chip.winfo_children()
                     if isinstance(child, main.tk.Entry))
        entry.focus_force()
        self.app.root.update()
        self.note._begin_preedit(entry)
        self.note._replace_preedit("xin", 3)
        self.assertEqual(entry.get(), "xin")
        self.note._finish_preedit()
        self.assertEqual(entry.get(), "原名称")
        entry.selection_range(0, "end")
        self.note._begin_preedit(entry)
        self.note._replace_preedit("xin", 3)
        self.note._finish_preedit("新名称")
        entry.event_generate("<Return>")
        self.app.root.update()
        self.assertEqual(self.note.d["title"], "新名称")

    def test_transparency_slider_updates_existing_and_new_notes_in_ten_percent_steps(self):
        settings = main.SettingsWindow(self.app)
        self.app.root.update()
        self.app.opacity_scale.set(27)
        self.app.root.update()
        self.assertEqual(self.app.opacity_scale.get(), 30)
        self.assertEqual(self.app.data["settings"]["opacity"], 0.7)
        self.assertEqual(self.note.d["opacity"], 0.7)
        self.assertAlmostEqual(float(self.note.attributes("-alpha")), 0.7, places=2)
        other = self.app.new_note()
        self.assertEqual(other.d["opacity"], 0.7)
        self.note._ctrl_wheel(type("Wheel", (), {"delta": 120})())
        self.assertEqual(other.d["opacity"], 0.8)
        self.assertEqual(self.app.opacity_scale.get(), 20)
        settings.destroy()

    def test_settings_open_in_front_then_return_to_normal_z_order(self):
        self.app.q.put("settings")
        self.app._pump()
        self.app.root.update()
        settings = self.app.settings_win
        self.assertEqual(settings.state(), "normal")
        self.assertEqual(settings.attributes("-topmost"), 1)
        hwnd = main.user32.GetParent(settings.winfo_id()) or settings.winfo_id()
        self.assertTrue(main.user32.GetWindowLongPtrW(hwnd, -20) & 0x40000)
        self.assertEqual(settings._icon_image.width(), 32)
        settings._minimize_button.event_generate("<Button-1>")
        self.app.root.update()
        self.assertEqual(settings.state(), "iconic")
        self.app.q.put("settings")
        self.app._pump()
        self.assertIs(self.app.settings_win, settings)
        self.assertEqual(settings.state(), "normal")
        self.app.root.after(350, self.app.root.quit)
        self.app.root.mainloop()
        self.assertEqual(settings.attributes("-topmost"), 0)
        settings.destroy()

    def test_only_settings_windows_have_taskbar_entries(self):
        other = self.app.new_note()
        settings = main.SettingsWindow(self.app)
        self.app.root.update()
        for note in (self.note, other):
            for _ in range(2):
                hwnd = main.user32.GetParent(note.winfo_id()) or note.winfo_id()
                style = main.user32.GetWindowLongPtrW(hwnd, -20)
                self.assertEqual(note.hwnd, hwnd)
                self.assertTrue(style & 0x80)  # WS_EX_TOOLWINDOW
                self.assertFalse(style & 0x40000)  # WS_EX_APPWINDOW
                note.hide()
                note.show()
                self.app.root.update()
        hwnd = main.user32.GetParent(settings.winfo_id()) or settings.winfo_id()
        style = main.user32.GetWindowLongPtrW(hwnd, -20)
        self.assertTrue(style & 0x40000)
        self.assertFalse(style & 0x80)
        settings.destroy()

    def test_settings_slider_and_window_drag(self):
        settings = main.SettingsWindow(self.app)
        self.app.root.update()
        scale = self.app.opacity_scale
        scale.event_generate("<Button-1>", x=8, y=14)
        self.assertEqual(scale.get(), 0)
        scale.focus_force()
        self.app.root.update()
        scale.event_generate("<Right>")
        self.assertEqual(scale.get(), 10)
        self.assertEqual(self.note.d["opacity"], 0.9)
        x, y = settings.winfo_x(), settings.winfo_y()
        settings._drag_press(type("Event", (), {"widget": settings, "x_root": x + 20, "y_root": y + 20})())
        settings._drag_move(type("Event", (), {"x_root": x + 35, "y_root": y + 30})())
        self.assertEqual((settings.winfo_x(), settings.winfo_y()), (x + 15, y + 10))
        settings.destroy()

    def test_settings_sliders_snap_to_requested_ranges(self):
        settings = main.SettingsWindow(self.app)
        self.app.root.update()
        pending, scales = list(settings.winfo_children()), []
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, main.SettingsScale):
                scales.append(widget)
        opacity = self.app.opacity_scale
        font = next(scale for scale in scales if scale != opacity)
        self.assertEqual(settings._check_images[False].width(), 22)
        opacity_value = opacity.master.winfo_children()[1]
        font_value = font.master.winfo_children()[1]
        self.assertEqual(opacity_value.cget("text"), "0%")
        self.assertEqual(opacity_value.winfo_rootx() + opacity_value.winfo_width(),
                         font_value.winfo_rootx() + font_value.winfo_width())
        for value, scale in ((opacity_value, opacity), (font_value, font)):
            self.assertLessEqual(abs(value.winfo_rooty() + value.winfo_height() / 2
                                     - scale.winfo_rooty() - scale.winfo_height() / 2), 1)
        for x in range(0, 181):
            opacity.event_generate("<B1-Motion>", x=x, y=14)
            self.assertIn(opacity.get(), range(0, 51, 10))
            font.event_generate("<B1-Motion>", x=x, y=14)
            self.assertIn(font.get(), range(10, 16))
        self.assertEqual(self.app.data["settings"]["fontSize"], 15)
        self.assertEqual((opacity.maximum - opacity.minimum) // opacity.step,
                         (font.maximum - font.minimum) // font.step)
        opacity.set(100)
        self.assertEqual(opacity.get(), 50)
        self.note._ctrl_wheel(type("Wheel", (), {"delta": -120})())
        self.assertEqual(self.app.data["settings"]["opacity"], 0.5)
        font.set(0)
        self.assertEqual(self.app.data["settings"]["fontSize"], 10)
        settings.destroy()

    def test_settings_background_and_text_drag_without_moving_on_slider(self):
        settings = main.SettingsWindow(self.app)
        self.app.root.update()
        pending, labels = list(settings.winfo_children()), []
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, main.tk.Label) and widget.cget("text") == "外观":
                labels.append(widget)
        for widget in (settings, labels[0]):
            x, y = settings.winfo_x(), settings.winfo_y()
            widget.event_generate("<Button-1>", x=5, y=5)
            widget.event_generate("<B1-Motion>", x=35, y=25)
            self.app.root.update()
            self.assertEqual((settings.winfo_x(), settings.winfo_y()), (x + 30, y + 20))
            widget.event_generate("<ButtonRelease-1>", x=35, y=25)
        x, y = settings.winfo_x(), settings.winfo_y()
        scale = self.app.opacity_scale
        scale.event_generate("<Button-1>", x=12, y=14)
        scale.event_generate("<B1-Motion>", x=168, y=14)
        self.app.root.update()
        self.assertEqual((settings.winfo_x(), settings.winfo_y()), (x, y))
        self.assertEqual(scale.get(), 50)
        settings.destroy()

    def test_hotkey_capture_updates_box_and_esc_does_not_close_settings(self):
        settings = main.SettingsWindow(self.app)
        self.app.root.update()
        entry = settings.hotkey_entries["new"]
        entry.focus_force()
        self.app.root.update()
        self.assertEqual(entry.master.itemcget(entry.master.find_all()[0], "outline"), "#94A3B8")
        with patch.object(self.app, "update_hotkey", return_value=None) as update:
            entry.event_generate("<KeyPress>", keycode=75, state=5)
            update.assert_called_once_with("new", "Ctrl+Shift+K")
            self.assertEqual(entry.get(), "Ctrl+Shift+K")
            entry.event_generate("<KeyPress-Escape>")
            self.app.root.update()
            self.assertTrue(settings.winfo_exists())
        entry.focus_force()
        self.app.root.update()
        with patch.object(self.app, "update_hotkey", return_value="快捷键已占用"):
            entry.event_generate("<KeyPress>", keycode=76, state=4)
            self.assertEqual(entry.get(), "Ctrl+Shift+K")
            self.assertEqual(settings._hotkey_hint.cget("text"), "快捷键已占用")
        x, y = settings.winfo_x(), settings.winfo_y()
        entry.event_generate("<Button-1>", x=5, y=5)
        entry.event_generate("<B1-Motion>", x=40, y=25)
        self.app.root.update()
        self.assertEqual((settings.winfo_x(), settings.winfo_y()), (x, y))
        settings.destroy()

    def test_hotkey_replacement_preserves_old_binding_on_conflict_and_saves_success(self):
        with patch.object(main.user32, "RegisterHotKey", return_value=True), patch.object(
                main.user32, "UnregisterHotKey"):
            self._start_hotkeys_impl(self.app)
        with patch.object(main.user32, "RegisterHotKey", return_value=False), patch.object(
                main.user32, "UnregisterHotKey") as unregister:
            error = self.app.update_hotkey("new", "Ctrl+Shift+K")
            self.assertIn("占用", error)
            unregister.assert_not_called()
            self.assertEqual(self.app.hotkey_text("new"), "Ctrl+Alt+N")
        with patch.object(main.user32, "RegisterHotKey", return_value=True) as register, patch.object(
                main.user32, "UnregisterHotKey") as unregister, patch.object(main, "save_data") as save:
            self.assertIsNone(self.app.update_hotkey("new", "Ctrl+Shift+K"))
            register.assert_called_once_with(self.app._hotkey_hwnd, 101, 2 | 4 | 0x4000, 75)
            unregister.assert_called_once_with(self.app._hotkey_hwnd, 1)
            self.assertEqual(self.app.data["settings"]["newHotkey"], "Ctrl+Shift+K")
            save.assert_called_once_with(self.app.data)
            self.assertIn("另一项", self.app.update_hotkey("hide", "Ctrl+Shift+K"))
        self.app._hotkey_proc(self.app._hotkey_hwnd, main.WM_HOTKEY, 101, 0)
        self.assertEqual(self.app.q.get_nowait(), "new")
        self.app._hotkey_proc(self.app._hotkey_hwnd, main.WM_HOTKEY, 2, 0)
        self.assertEqual(self.app.q.get_nowait(), "hide")
        self.app._hotkey_proc(self.app._hotkey_hwnd, main.WM_HOTKEY, 3, 0)
        self.assertEqual(self.app.q.get_nowait(), "show")
        settings = main.SettingsWindow(self.app)
        self.app._hotkey_capture = (settings, "new")
        self.app._hotkey_proc(self.app._hotkey_hwnd, main.WM_HOTKEY, 2, 0)
        with patch.object(self.app, "hide_all") as hide:
            self.app._pump()
            hide.assert_not_called()
        self.assertIn("另一项", settings._hotkey_hint.cget("text"))
        settings.destroy()

    def test_reset_hotkeys_restores_all_defaults_and_keeps_old_bindings_on_conflict(self):
        self.app.data["settings"].update(newHotkey="Ctrl+Alt+H", hideHotkey="Ctrl+Alt+N",
                                         showHotkey="Ctrl+Shift+S")
        self.app._hotkey_hwnd = self.app.root.winfo_id()
        self.app._hotkey_ids = {"new": 1, "hide": 2, "show": 3}
        self.app._active_hotkeys = {"new": "Ctrl+Alt+H", "hide": "Ctrl+Alt+N", "show": "Ctrl+Shift+S"}
        with patch.object(main.user32, "RegisterHotKey", side_effect=[True, False, True, True, True]), \
             patch.object(main.user32, "UnregisterHotKey"), patch.object(main, "save_data") as save:
            self.assertIn("占用", self.app.reset_hotkeys())
            self.assertEqual(self.app.hotkey_text("new"), "Ctrl+Alt+H")
            self.assertEqual(self.app._active_hotkeys["hide"], "Ctrl+Alt+N")
            self.assertEqual(self.app._active_hotkeys["show"], "Ctrl+Shift+S")
            save.assert_not_called()
        with patch.object(main.user32, "RegisterHotKey", return_value=True), \
             patch.object(main.user32, "UnregisterHotKey"), patch.object(main, "save_data") as save:
            settings = main.SettingsWindow(self.app)
            self.app.root.update()
            heading = next(w for w in settings._hotkey_hint.master.winfo_children()
                           if isinstance(w, main.tk.Frame) and any(
                               isinstance(child, main.tk.Label) and child.cget("text") == "快捷键"
                               for child in w.winfo_children()))
            button = next(w for w in heading.winfo_children() if isinstance(w, main.tk.Button))
            label = next(w for w in heading.winfo_children() if isinstance(w, main.tk.Label))
            self.assertEqual(button.winfo_rootx() - label.winfo_rootx() - label.winfo_width(), 16)
            self.assertLess(button.winfo_rooty(), settings.hotkey_entries["new"].winfo_rooty())
            self.assertTrue(button.cget("image"))
            for entry in settings.hotkey_entries.values():
                self.assertIsInstance(entry.master, main.tk.Canvas)
                self.assertEqual(entry.master.type(entry.master.find_all()[0]), "polygon")
            save.reset_mock()
            settings._reset_hotkeys()
            self.assertEqual(settings.hotkey_entries["new"].get(), "Ctrl+Alt+N")
            self.assertEqual(settings.hotkey_entries["hide"].get(), "Ctrl+Alt+H")
            self.assertEqual(settings.hotkey_entries["show"].get(), "Ctrl+Alt+S")
            self.assertEqual(self.app._active_hotkeys,
                             {"new": "Ctrl+Alt+N", "hide": "Ctrl+Alt+H", "show": "Ctrl+Alt+S"})
            save.assert_called_once_with(self.app.data)
            settings.destroy()

    def test_settings_text_fits_at_different_scales_and_long_paths(self):
        original_scaling = self.app.root.tk.call("tk", "scaling")
        try:
            for scale, screen_w, screen_h in ((scale, w, h)
                    for w, h in ((1280, 900), (800, 600))
                    for scale in (1, 1.25, 1.5, 2, 2.5, 3, 4)):
                def work_area(_action, _param, rect, _flags):
                    rect._obj.left, rect._obj.top = 0, 0
                    rect._obj.right, rect._obj.bottom = screen_w, screen_h
                    return True
                with self.subTest(scale=scale, screen=(screen_w, screen_h)), patch.object(
                        main.user32, "SystemParametersInfoW", side_effect=work_area), patch.object(
                        main, "DATA_FILE", "C:/" + "很长的用户目录/" * 20 + "notes.json"):
                    self.app.root.tk.call("tk", "scaling", scale * 96 / 72)
                    settings = main.SettingsWindow(self.app)
                    try:
                        self.app.root.update()
                        self.assertGreaterEqual(settings.winfo_width(), 720)
                        self.assertLessEqual(settings.winfo_width(), screen_w - 32)
                        self.assertLessEqual(settings.winfo_height(), screen_h - 32)
                        for entry in settings.hotkey_entries.values():
                            font = main.tkfont.Font(font=entry.cget("font"))
                            self.assertGreaterEqual(entry.winfo_width() - 6,
                                                    font.measure("Ctrl+Alt+Shift+Backspace"))
                        pending = list(settings.winfo_children())
                        while pending:
                            widget = pending.pop()
                            pending.extend(widget.winfo_children())
                            if isinstance(widget, (main.tk.Label, main.tk.Checkbutton)):
                                self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(),
                                                        str(widget.cget("text")))
                                self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight(),
                                                        str(widget.cget("text")))
                        body = next(w for w in settings.winfo_children()
                                    if isinstance(w, main.tk.Canvas) and w.find_all()
                                    and w.type(w.find_all()[0]) == "window")
                        if scale == 4:
                            self.assertLess(body.yview()[1], 1)
                            body.yview_moveto(1)
                            self.app.root.update()
                            self.assertAlmostEqual(body.yview()[1], 1)
                    finally:
                        settings.destroy()
        finally:
            self.app.root.tk.call("tk", "scaling", original_scaling)

    def test_old_opacity_values_are_reset_once(self):
        old = {"schemaVersion": 1,
               "settings": dict(main.DEFAULT_SETTINGS, opacity=0.6),
               "notes": [dict(self.note.d, opacity=0.7)]}
        with patch.object(main, "load_data", return_value=old), \
             patch.object(main, "save_data") as save:
            app = main.App()
            try:
                self.assertEqual(app.data["schemaVersion"], 2)
                self.assertEqual(app.data["settings"]["opacity"], 1.0)
                self.assertEqual(app.data["notes"][0]["opacity"], 1.0)
                save.assert_called()
            finally:
                for job in app.root.tk.call("after", "info"):
                    app.root.tk.call("after", "cancel", job)
                app.root.destroy()

    def test_delete_moves_whole_note_to_recycle_bin_and_restore_keeps_identity(self):
        note_id = self.note.d["id"]
        lines = [dict(line) for line in self.note.d["lines"]]
        self.note.delete_self()
        self.assertNotIn(note_id, self.app.notes)
        self.assertEqual(self.app.data["deletedNotes"][0]["lines"], lines)
        self.assertTrue(self.app.recover_note(note_id))
        self.assertEqual(self.app.notes[note_id].d["lines"], lines)
        self.assertFalse(self.app.data["deletedNotes"])

    def test_recycle_bin_restore_button_preserves_note_and_handles_save_failure(self):
        from copy import deepcopy
        original = deepcopy(self.note.d)
        note_id = original["id"]
        self.note.delete_self()
        win = main.HistoryWindow(self.app)
        self.app.history_win = win
        win.switch("deletedNotes")
        actions = win.content.winfo_children()[0]
        delete, restore = actions.winfo_children()
        self.assertEqual([delete.cget("text"), restore.cget("text")], ["彻底删除", "还原"])
        with patch.object(main, "save_data", side_effect=OSError("disk full")), \
             patch.object(main.messagebox, "showerror") as error:
            restore.invoke()
        error.assert_called_once()
        self.assertNotIn(note_id, self.app.notes)
        self.assertEqual(self.app.data["deletedNotes"][0]["id"], note_id)
        self.assertEqual(win.ids, [note_id])
        restore.invoke()
        self.assertEqual(self.app.notes[note_id].d, dict(original, visible=True))
        self.assertFalse(self.app.data["deletedNotes"])
        self.assertFalse(win.ids)
        self.assertEqual(win.content.winfo_children()[0].cget("text"), "暂无记录")
        win.destroy()

    def test_clear_preserves_completed_items_and_undo_restores_positions(self):
        self.note.toggle_done(1)
        original = [dict(line) for line in self.note.d["lines"]]
        self.note.clear_done()
        batch = self.app.data["archives"][0]
        self.assertEqual(batch["items"][0]["line"], original[1])
        self.assertEqual(len(self.note.d["lines"]), len(original)-1)
        self.app.return_archive(batch["id"])
        self.assertEqual(self.note.d["lines"], original)
        self.assertFalse(self.app.data["archives"])

    def test_undo_clear_preserves_existing_blank_line(self):
        self.note.d["lines"] = [
            {"type": "text", "text": "", "checked": False},
            {"type": "todo", "text": "完成事项", "checked": True}]
        self.note.render()
        self.note.clear_done()
        self.app.return_archive(self.app.data["archives"][0]["id"])
        self.assertEqual(len(self.note.d["lines"]), 2)
        self.assertEqual(self.note.d["lines"][0]["type"], "text")

    def test_clear_save_failure_keeps_note_and_history(self):
        self.note.toggle_done(1)
        original = [dict(line) for line in self.note.d["lines"]]
        with patch.object(main, "save_data", side_effect=OSError("disk full")), \
             patch.object(main.messagebox, "showerror") as error:
            self.note.clear_done()
        self.assertEqual(self.note.d["lines"], original)
        self.assertFalse(self.app.data["archives"])
        error.assert_called_once()

    def test_history_groups_same_names_by_identity_and_keeps_archive_after_deletion(self):
        self.note.d["title"] = "项目"
        self.note.d["editedAt"] = "2026-10-28T10:00:00"
        self.note.toggle_done(1)
        self.note.clear_done()
        other = self.app.new_note()
        other.d["title"] = "项目"
        other.d["editedAt"] = "2026-10-28T11:00:00"
        other.text.insert("1.end", "另一项")
        other._sync_lines_from_text()
        other.toggle_done(0)
        other.clear_done()
        win = main.HistoryWindow(self.app)
        self.app.history_win = win
        self.app.root.update()
        self.assertEqual(len(win.ids), 2)
        self.assertTrue(all("项目 · " in name for name in win.names))
        self.assertEqual(self.note.d["title"], "项目")
        self.note.delete_self()
        win.switch("deletedNotes")
        self.assertEqual(len(win.ids), 1)
        with patch.object(main.messagebox, "askyesno", return_value=True):
            win.delete_record(self.note.d["id"])
        self.assertEqual(len(self.app.data["archives"]), 2)
        self.assertFalse(self.app.data["deletedNotes"])
        win.destroy()

    def test_history_preview_and_batch_deletion_are_scoped_to_category(self):
        self.note.toggle_done(1)
        self.note.clear_done()
        note_id = self.note.d["id"]
        self.note.delete_self()
        win = main.HistoryWindow(self.app)
        self.app.history_win = win
        self.app.root.update()
        self.assertFalse(any(isinstance(c, main.tk.Button) for c in win.content.winfo_children()))
        win.toggle_batch()
        win.check(note_id, True)
        with patch.object(main.messagebox, "askyesno", return_value=True):
            win.delete_selected()
        self.assertFalse(self.app.data["archives"])
        self.assertEqual(len(self.app.data["deletedNotes"]), 1)
        win.switch("deletedNotes")
        actions = win.content.winfo_children()[0]
        buttons = [c.cget("text") for c in actions.winfo_children()]
        self.assertEqual(buttons, ["彻底删除", "还原"])
        win.toggle_batch()
        win.check(note_id, True)
        with patch.object(main.messagebox, "askyesno", return_value=True):
            win.delete_selected()
        self.assertFalse(self.app.data["deletedNotes"])
        win.destroy()

    def test_history_drag_ignores_controls_and_thumb_matches_view(self):
        win = main.HistoryWindow(self.app)
        self.app.root.update()
        event = lambda widget: type("Event", (), {"widget":widget,"x_root":100,"y_root":100})()
        main.SettingsWindow._drag_press(win, event(win.hint))
        self.assertIsNotNone(win._drag)
        for widget in (win._close_button,win._minimize_button,win.batch_button,win.left.bars["y"]):
            main.SettingsWindow._drag_press(win, event(widget))
            self.assertIsNone(win._drag)
        pane = win.right
        pane.bars["y"].set(0.25, 0.75)
        coordinates = pane.bars["y"].coords("thumb")
        length = pane.bars["y"].winfo_height()-8
        self.assertAlmostEqual(coordinates[3]-coordinates[1], length*.5)
        pane.bars["y"].set(0,1)
        self.assertFalse(pane.bars["y"].find_withtag("thumb"))
        win.destroy()

    def test_history_scrollbar_appears_on_overflow_and_short_content_cannot_scroll(self):
        win = main.HistoryWindow(self.app)
        self.app.history_win = win
        self.app.root.update()
        pane = win.right
        self.assertEqual(set(pane.bars), {"y"})
        self.assertFalse(pane.bars["y"].find_withtag("thumb"))
        event = type("Event", (), {"delta": -120})()
        pane.wheel(event)
        self.app.root.update()
        self.assertEqual(pane.viewport.yview(), (0.0, 1.0))
        for i in range(60):
            win.label("内容 " + str(i))
        self.app.root.update()
        self.assertTrue(pane.bars["y"].find_withtag("thumb"))
        first_thumb = pane.bars["y"].coords("thumb")
        pane.wheel(event)
        self.app.root.update()
        self.assertGreater(pane.viewport.yview()[0], 0)
        self.assertGreater(pane.bars["y"].coords("thumb")[1], first_thumb[1])
        win.select()
        self.app.root.update()
        self.assertFalse(pane.bars["y"].find_withtag("thumb"))
        self.assertEqual(pane.viewport.yview(), (0.0, 1.0))
        win.destroy()

    def test_history_wrapped_row_centers_checkbox_and_click_selects_content(self):
        from copy import deepcopy
        self.note.toggle_done(1)
        self.note.clear_done()
        batch = self.app.data["archives"][0]
        other = deepcopy(batch)
        other.update(id="batch2", noteId="note2", title="很长的便签名称"*18,
                     editedAt="2026-10-28T12:34:56.123456")
        other["items"][0]["line"]["text"] = "第二张便签的内容"
        self.app.data["archives"].append(other)
        win = main.HistoryWindow(self.app)
        self.app.history_win = win
        win.toggle_batch()
        self.app.root.update()
        row = win.rows["note2"]
        checkbox, label = row.winfo_children()
        self.assertGreater(label.winfo_height(), checkbox.winfo_height()*2)
        self.assertAlmostEqual(checkbox.winfo_y()+checkbox.winfo_height()/2,
                               label.winfo_y()+label.winfo_height()/2, delta=2)
        label.event_generate("<Button-1>")
        self.app.root.update()
        self.assertEqual(win.selected_id, "note2")
        self.assertEqual(label.cget("bg"), "#E9EDF2")
        self.assertTrue(any(c.cget("text") == "✓ 第二张便签的内容"
            for c in win.content.winfo_children() if isinstance(c, main.tk.Label)))
        win.destroy()

    def test_history_duplicate_name_suffix_only_shows_seconds(self):
        from copy import deepcopy
        note = deepcopy(self.note.d)
        note.update(id="second", title="同名", editedAt="2026-10-28T12:34:56.123456")
        first = deepcopy(note)
        first.update(id="first", editedAt="2026-10-28T11:22:33.654321")
        self.app.data["deletedNotes"] = [first, note]
        win = main.HistoryWindow(self.app)
        win.switch("deletedNotes")
        self.app.root.update()
        self.assertEqual(win.names, ["同名 · 2026-10-28 11:22:33", "同名 · 2026-10-28 12:34:56"])
        win.choose("second")
        self.assertEqual(win.rows["second"].cget("bg"), "#E9EDF2")
        win.destroy()

    def test_note_scroll_thumb_only_shows_for_overflow_and_tracks_drag(self):
        self.note.d["lines"] = [{"type":"text", "text":"短内容", "checked":False}]
        self.note.render()
        self.app.root.update()
        thumb = self.note.scroll_thumb
        self.assertFalse(thumb.find_withtag("thumb"))
        self.text.event_generate("<MouseWheel>", delta=-120)
        self.app.root.update()
        self.assertEqual(self.note.body.yview(), (0.0,1.0))
        self.note.d["lines"] = [{"type":"todo", "text":f"待办 {i}", "checked":i%2==0} for i in range(50)]
        self.note.render()
        self.app.root.update()
        self.assertTrue(thumb.find_withtag("thumb"))
        self.text.event_generate("<MouseWheel>", delta=-120)
        self.app.root.update()
        self.assertGreater(self.note.body.yview()[0],0)
        event = type("Event", (), {"y":thumb.winfo_height()//2})()
        thumb.move(event)
        self.app.root.update()
        self.assertGreater(self.note.body.yview()[0],.2)
        self.note.d["lines"] = [{"type":"text", "text":"短内容", "checked":False}]
        self.note.render()
        self.app.root.update()
        self.assertFalse(thumb.find_withtag("thumb"))
        self.assertEqual(self.note.body.yview(), (0.0,1.0))

    def test_history_persists_and_readding_keeps_record(self):
        import json
        import tempfile
        from pathlib import Path
        self.note.toggle_done(1)
        self.note.clear_done()
        batch = self.app.data["archives"][0]
        count = len(self.note.d["lines"])
        self.app.return_archive(batch["id"], 0)
        self.assertEqual(len(self.note.d["lines"]), count+1)
        self.assertFalse(self.note.d["lines"][-1]["checked"])
        self.assertEqual(len(self.app.data["archives"]), 1)
        self.note.delete_self()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder, \
             patch.object(main, "DATA_DIR", folder), \
             patch.object(main, "DATA_FILE", str(Path(folder)/"notes.json")):
            self.patches[1].stop()
            try:
                main.save_data(self.app.data)
                saved = json.loads((Path(folder)/"notes.json").read_text(encoding="utf-8"))
            finally:
                self.patches[1].start()
        self.assertEqual(saved["archives"], self.app.data["archives"])
        self.assertEqual(saved["deletedNotes"], self.app.data["deletedNotes"])

    def test_history_content_stays_inside_window_at_large_scaling(self):
        original = self.app.root.tk.call("tk", "scaling")
        try:
            for scaling in (1.0, 2.0):
                self.app.root.tk.call("tk", "scaling", scaling)
                win = main.HistoryWindow(self.app)
                self.app.root.update()
                self.assertLessEqual(win.canvas.winfo_rootx()+win.canvas.winfo_width(),
                                     win.winfo_rootx()+win.winfo_width())
                self.assertGreater(win.canvas.winfo_width(), 200)
                self.assertGreater(win.content.winfo_width(), 200)
                self.assertGreater(win.left.content.winfo_width(), 200)
                win.destroy()
        finally:
            self.app.root.tk.call("tk", "scaling", original)

    def test_large_title_stays_inside_toolbar_above_body(self):
        self.note._chip_font.configure(size=20)
        self.note._draw_chip()
        self.note._last_sz = None
        self.note._relayout()
        self.app.root.update()
        self.assertGreaterEqual(self.note.body.winfo_y(),
                                self.note.toolbar.winfo_y() + self.note.toolbar.winfo_height())
        self.assertGreaterEqual(self.note.title_chip.winfo_height(),
                                self.note._chip_font.metrics("linespace") + 12)

    def test_title_is_slightly_heavier_than_body(self):
        for font in (self.app.base_font, self.note.done_font, self.note._chip_font,
                     main.tkfont.nametofont("TkDefaultFont", root=self.app.root),
                     main.tkfont.nametofont("TkMenuFont", root=self.app.root)):
            self.assertEqual(font.actual("family"), main.UI_FONT)
        self.assertEqual(self.app.base_font.actual("weight"), "normal")
        self.assertEqual(self.note._chip_font.actual("weight"), "bold")

    def test_context_menu_uses_note_style_and_activates_row_action(self):
        self.note._show_context_menu(100, 100,
                                     [("转为文本行", lambda: self.note.convert(0, "text")),
                                      ("在下方插入新行", lambda: self.note.insert_line(0)),
                                      ("删除此行", lambda: self.note.delete_line(0))])
        self.app.root.update()
        popup = self.note._context_menu
        canvas = next(child for child in popup.winfo_children()
                      if isinstance(child, main.tk.Canvas))
        self.assertEqual(canvas.cget("bg"), main.T_COLOR)
        self.assertEqual([canvas.itemcget(item, "text") for item in canvas.find_all()
                          if canvas.type(item) == "text"],
                         ["转为文本行", "在下方插入新行", "删除此行"])
        first = canvas.bbox("item0")
        canvas.event_generate("<Button-1>", x=(first[0] + first[2]) // 2,
                              y=(first[1] + first[3]) // 2)
        self.app.root.update()
        self.assertFalse(popup.winfo_exists())
        self.assertEqual(self.note.d["lines"][0]["type"], "text")

    def test_add_todo_inserts_below_cursor_line(self):
        before = list(self.note.d["lines"])
        self.text.mark_set("insert", "2.end")
        self.note._pill_press(None)
        self.assertEqual(self.note.d["lines"][:2], before[:2])
        self.assertEqual(self.note.d["lines"][2],
                         {"type": "todo", "text": "", "checked": False})
        self.assertEqual(self.note.d["lines"][3:], before[2:])
        self.assertEqual(self.text.index("insert"),
                         f"3.{self.note._prefix_len(self.note.d['lines'][2])}")

    def test_title_text_is_visually_centered_in_chip(self):
        self.note.d["title"] = "10.27"
        self.note._draw_chip()
        self.app.root.update()
        canvas = self.note.title_chip
        border = next(item for item in canvas.find_all() if canvas.type(item) == "polygon")
        text = next(item for item in canvas.find_all() if canvas.type(item) == "text")
        box = canvas.bbox(border)
        label = canvas.bbox(text)
        # 奇数宽度下几何中心落在 x.5，Tk 取整允许 1 物理像素误差
        self.assertLessEqual(abs(box[0] + box[2] - label[0] - label[2]), 2)
        self.assertLessEqual(abs(box[1] + box[3] - label[1] - label[3]), 2)

    def test_todo_checkbox_has_rounded_square_outline(self):
        image = main.ImageTk.getimage(self.note._check_images[False])
        size = image.width
        self.assertGreaterEqual(size, 18)
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertGreater(image.getpixel((size // 4, 1))[3], 0)

    def test_caret_is_unbroken_on_both_sides_of_checkbox(self):
        note, text = self.note, self.text
        note.geometry("440x340+120+120")
        text.configure(insertofftime=0)
        text.focus_force()
        for checked in (False, True):
            note.d["lines"] = [{"type": "todo", "text": "删除行逻辑（三种）", "checked": checked}]
            note.render()
            for index in ("1.0", "1.1"):
                with self.subTest(checked=checked, index=index):
                    text.mark_set("insert", index)
                    self.app.root.update()
                    x, y, width, height = text.bbox(index)
                    rect = (text.winfo_rootx() + x - 2, text.winfo_rooty() + y,
                            text.winfo_rootx() + x + 2, text.winfo_rooty() + y + height)
                    main.dwmapi.DwmFlush()
                    with_caret = ImageGrab.grab(bbox=rect)
                    text.configure(insertwidth=0)
                    self.app.root.update()
                    main.dwmapi.DwmFlush()
                    without_caret = ImageGrab.grab(bbox=rect)
                    difference = ImageChops.difference(with_caret, without_caret)
                    for row in range(height):
                        pixels = difference.crop((0, row, 4, row + 1)).getbbox()
                        self.assertIsNotNone(pixels)
                        self.assertEqual(pixels[2] - pixels[0], 2)
                    text.configure(insertwidth=2)

    def test_todo_wraps_beside_checkbox_and_centers_one_checkbox_for_long_text(self):
        self.note.geometry("300x500")
        self.note.d["lines"] = [{"type": "todo", "text": "对话发出终止后可以重新编辑" * 2, "checked": False}]
        self.note.render()
        self.app.root.update()
        first = self.text.dlineinfo("1.3")
        last = self.text.dlineinfo("1.end")
        self.assertEqual(first[1], self.text.dlineinfo("1.0")[1])
        self.assertGreater(last[1], first[1])
        self.assertEqual(len(self.note._checkbox_widgets), 1)

        long_text = "啊" * 75
        self.note.d["lines"][0]["text"] = long_text
        self.note.render()
        self.app.root.update()
        first = self.text.dlineinfo("1.0")
        last = self.text.dlineinfo("1.end")
        box = self.note._checkbox_widgets[0]
        self.assertEqual(len(self.note._checkbox_widgets), 1)
        self.assertLessEqual(abs(box.winfo_y() + box.winfo_height() / 2 -
                                 (first[1] + last[1] + last[3]) / 2), 1)
        next_line = self.text.index("1.3 +1 display lines display linestart")
        self.assertGreaterEqual(self.text.bbox(next_line)[0], self.text.bbox("1.3")[0])
        self.assertEqual(self.text.get("1.3", "1.end"), long_text)
        self.note._sync_lines_from_text()
        self.assertEqual(self.note.d["lines"], [{"type": "todo", "text": long_text, "checked": False}])
        box.event_generate("<Button-1>")
        self.assertTrue(self.note.d["lines"][0]["checked"])

    def test_todo_keeps_every_wrapped_line_indented_while_typing(self):
        self.note.geometry("300x500")
        self.note.d["lines"] = [{"type": "todo", "text": "对话发出终止后可以重新编辑", "checked": False}]
        self.note.render()
        self.text.insert("1.end", "啊" * 55)
        self.note._on_keyrelease(None)
        self.app.root.update()
        starts = ["1.3"]
        while True:
            next_start = self.text.index(f"{starts[-1]} +1 display lines display linestart")
            if self.text.compare(next_start, "<=", starts[-1]) or self.text.compare(next_start, ">=", "1.end"):
                break
            starts.append(next_start)
        self.assertGreaterEqual(len(starts), 3)
        self.assertEqual([self.text.bbox(index)[0] for index in starts],
                         [self.text.bbox("1.3")[0]] * len(starts))
        self.assertEqual(len(self.note._checkbox_widgets), 1)
        self.assertEqual(len(self.note.d["lines"]), 1)
        first = self.text.dlineinfo("1.0")
        last = self.text.dlineinfo("1.end")
        box = self.note._checkbox_widgets[0]
        self.assertLessEqual(abs(box.winfo_y() + box.winfo_height() / 2 -
                                 (first[1] + last[1] + last[3]) / 2), 1)

    def test_checkbox_stays_visible_when_first_line_scrolls_offscreen(self):
        self.note.geometry("300x300")
        self.note.d["lines"] = [{"type": "todo", "text": "啊" * 140, "checked": False}]
        self.note.render()
        self.app.root.update()
        self.note.body.yview_scroll(4 * self.app.base_font.metrics("linespace"), "units")
        self.app.root.update()
        count = self.text.count("1.0", "1.end", "displaylines")[0]
        middle = self.text.index(f"1.0 +{count // 2} display lines display linestart")
        self.assertLess(self.text.winfo_rooty() + self.text.dlineinfo("1.0")[1],
                        self.note.body.winfo_rooty())
        self.assertEqual(len(self.note._checkbox_widgets), 1)
        box = self.note._checkbox_widgets[0]
        info = self.text.dlineinfo(middle)
        self.assertLessEqual(abs(box.winfo_y() + box.winfo_height() / 2 -
                                 (info[1] + info[3] / 2)), 1)
        box.event_generate("<Button-1>")
        self.assertTrue(self.note.d["lines"][0]["checked"])

    def test_checkbox_position_follows_fixed_middle_line_and_selection_skips_it(self):
        self.note.geometry("300x300")
        self.note.d["lines"] = [{"type": "todo", "text": "啊" * 140, "checked": False}]
        self.note.render()
        self.app.root.update()
        count = self.text.count("1.0", "1.end", "displaylines")[0]
        middle = self.text.index(f"1.0 +{(count + 1) // 2} display lines")
        for scroll in (3, 1, -2):
            self.note.body.yview_scroll(scroll * self.app.base_font.metrics("linespace"), "units")
            self.app.root.update()
            info = self.text.dlineinfo(middle)
            self.assertIsNotNone(info)
            self.assertEqual(len(self.note._checkbox_widgets), 1)
            box = self.note._checkbox_widgets[0]
            expected = info[1] + (0 if count % 2 else info[3] // 2)
            self.assertLessEqual(abs(box.winfo_y() + box.winfo_height() / 2 - expected), 1)
        self.text.tag_add("sel", "1.0", "1.end")
        self.app.root.update()
        self.assertEqual(self.note._checkbox_widgets[0].cget("bg"), self.note._bg)
        self.assertEqual(len(self.note._selection_masks), 1)
        mask = self.note._selection_masks[0]
        self.assertEqual(mask.winfo_x() + mask.winfo_width(), self.text.bbox(middle)[0])
        self.assertLessEqual(mask.winfo_y(), self.note._checkbox_widgets[0].winfo_y())
        self.assertGreaterEqual(mask.winfo_y() + mask.winfo_height(),
                                self.note._checkbox_widgets[0].winfo_y() +
                                self.note._checkbox_widgets[0].winfo_height())
        self.assertFalse(self.text.tag_nextrange("selection_fill", "1.0", "1.3"))
        self.assertTrue(self.text.tag_nextrange("selection_fill", "1.3", "1.end"))
        self.text.tag_remove("sel", "1.0", "1.end")
        self.app.root.update()
        self.assertEqual(self.note._checkbox_widgets[0].cget("bg"), self.note._bg)
        self.assertFalse(self.note._selection_masks)

    def test_scroll_positions_checkboxes_before_next_idle_frame(self):
        self.note.geometry("340x340")
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": i % 2 == 0}
            for i in range(100)]
        self.note.render()
        self.app.root.update()
        for fraction in (.2, .7, .35):
            with self.subTest(fraction=fraction):
                self.note.body.yview_moveto(fraction)
                self.app.root.update()
                row = self.note._body_index(40, 60).split(".")[0]
                box = next(box for box in self.note._checkbox_widgets if box._todo_row == int(row))
                old_y = box.winfo_rooty()
                local_y = box.winfo_y()
                old_text_y = self.text.winfo_rooty() + self.text.dlineinfo(f"{row}.0")[1]
                self.note.body.yview_scroll(7, "units")
                self.app.root.update()
                delta = self.text.winfo_rooty() + self.text.dlineinfo(f"{row}.0")[1] - old_text_y
                self.assertNotEqual(delta, 0)
                self.assertEqual(int(box.place_info()["y"]), local_y)
                self.assertIsNone(self.note._strike_job)
                self.assertEqual(box.winfo_rooty(), old_y + delta)
                box.event_generate("<Button-1>")
                self.assertEqual(self.note.d["lines"][int(row)-1]["checked"], int(row) % 2 == 0)

    def test_thousand_rows_reach_bottom_with_bounded_native_windows(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": False}
            for i in range(1000)]
        self.note.render()
        self.app.root.update()
        self.assertEqual(len(self.note.d["lines"]), 1000)
        with patch.object(self.note, "_draw_strikes", wraps=self.note._draw_strikes) as draw:
            for fraction in (.1, .6, 1, 0):
                self.note.body.yview_moveto(fraction)
                self.app.root.update()
                if fraction == 1:
                    self.assertIsNotNone(self._visible_bbox("1000.3"))
            self.assertLessEqual(draw.call_count, 4)
            self.assertLessEqual(self.text.winfo_height(), 16000)
            self.assertLessEqual(abs(int(self.text.place_info()["y"])), 16000)

    def test_keyboard_page_navigation_keeps_cursor_inside_body(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": False} for i in range(50)]
        self.note.render()
        self.text.mark_set("insert", "1.3")
        for key, shift in (("Next", 0), ("Next", 1), ("Prior", 0)):
            self.note._page_key(type("Key", (), {"keysym": key, "state": shift})())
            self.app.root.update()
            box = self._visible_bbox("insert")
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box[1], 0)
            self.assertLessEqual(box[1] + box[3], self.note.body.winfo_height())
            if shift:
                self.assertTrue(self.text.tag_ranges("sel"))

    def test_long_preedit_at_bottom_keeps_composition_visible(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": False} for i in range(20)]
        self.note.render()
        self.text.mark_set("insert", "20.end")
        self.note._see_text("insert")
        original = self.note.d["lines"][-1]["text"]
        self.note._begin_preedit(self.text)
        self.note._replace_preedit("pinyin" * 30)
        self.app.root.update()
        self.assertIsNotNone(self._visible_bbox("insert"))
        self.assertEqual(self.note.d["lines"][-1]["text"], original)
        self.note._finish_preedit("确认")
        self.app.root.update()
        self.assertEqual(self.note.d["lines"][-1]["text"], original + "确认")
        self.assertIsNotNone(self._visible_bbox("insert"))

    def test_drag_selection_scrolls_outer_body_and_stops_on_release(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": False} for i in range(50)]
        self.note.render()
        self.text.event_generate("<Button-1>", x=70, y=30)
        before = self.note.body.yview()[0]
        y = self.note.body.winfo_height() + 20
        self.text.event_generate("<B1-Motion>", x=70, y=y, state=256)
        self.assertGreater(self.note.body.yview()[0], before)
        self.assertTrue(self.text.tag_ranges("sel"))
        self.assertIsNotNone(self.note._selection_scroll_job)
        self.note._selection_scroll()
        self.text.event_generate("<ButtonRelease-1>", x=70, y=y)
        self.assertIsNone(self.note._selection_scroll_job)
        self.app.root.update()

    def test_scrolling_reuses_checkbox_widgets_instead_of_recreating(self):
        self.note.geometry("300x300")
        self.note.d["lines"] = [{"type": "todo", "text": f"待办{i}", "checked": bool(i % 2)}
                                for i in range(30)]
        self.note.render()
        self.app.root.update()
        first_pass = list(self.note._checkbox_widgets)
        self.assertGreater(len(first_pass), 1)
        self.note.body.yview_scroll(60 * self.app.base_font.metrics("linespace"), "units")
        self.app.root.update()
        for box in self.note._checkbox_widgets:
            self.assertIn(box, first_pass)
        self.note.body.yview_scroll(-60 * self.app.base_font.metrics("linespace"), "units")
        self.app.root.update()
        for box in self.note._checkbox_widgets:
            self.assertIn(box, first_pass)
        self.note._checkbox_widgets[0].event_generate("<Button-1>")
        self.assertTrue(self.note.d["lines"][0]["checked"])

    def test_wheel_uses_pixel_steps_and_keeps_small_deltas(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": i % 2 == 0}
            for i in range(50)]
        self.note.render()
        self.app.root.update()
        for delta in (-120, -15):
            with self.subTest(delta=delta):
                self.note.body.yview_moveto(0)
                self.app.root.update()
                with patch.object(self.note.body, "yview_scroll", wraps=self.note.body.yview_scroll) as scroll:
                    self.note._text_wheel(type("Wheel", (), {"delta": delta})())
                self.assertEqual(scroll.call_args.args[1], "units")
                self.assertGreater(scroll.call_args.args[0], 0)
                self.assertIsNotNone(self.note._wheel_job)
                self.note._stop_wheel()
                self.assertIsNone(self.note._wheel_job)

    def test_scroll_moves_text_checkboxes_and_strikes_as_one_window(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i} " + "需要处理的事情" * 3,
             "checked": i % 2 == 0} for i in range(20)]
        self.note.render()
        self.app.root.update()
        body = self.note.body
        box, bar = self.note._checkbox_widgets[0], self.note._strike_widgets[0]
        native_view = self.text.yview()
        positions = (box.winfo_y(), bar.winfo_y(), self.text.dlineinfo("1.3")[1])
        with patch.object(self.note, "_draw_strikes", wraps=self.note._draw_strikes) as draw:
            for distance in (1, 7, 21, -9, -20):
                before = [widget.winfo_rooty() for widget in (self.text, box, bar)]
                body.yview_scroll(distance, "units")
                self.app.root.update()
                deltas = [widget.winfo_rooty() - y for widget, y in zip((self.text, box, bar), before)]
                self.assertEqual(deltas, [-distance] * 3)
                self.assertEqual((box.winfo_y(), bar.winfo_y(), self.text.dlineinfo("1.3")[1]), positions)
                self.assertEqual(self.text.yview(), native_view)
            clock = [10.0]
            with patch.object(main.time, "monotonic", side_effect=lambda: clock[0]):
                self.note._text_wheel(type("Wheel", (), {"delta": -240})())
                for _ in range(100):
                    if self.note._wheel_job is None:
                        break
                    self.note.after_cancel(self.note._wheel_job)
                    before = [widget.winfo_rooty() for widget in (self.text, box, bar)]
                    clock[0] += .016
                    self.note._step_wheel()
                    deltas = [widget.winfo_rooty() - y for widget, y in zip((self.text, box, bar), before)]
                    self.assertEqual(deltas, [deltas[0]] * 3)
                    self.assertEqual((box.winfo_y(), bar.winfo_y(), self.text.dlineinfo("1.3")[1]), positions)
                self.assertIsNone(self.note._wheel_job)
            draw.assert_not_called()

    def test_buffer_switch_preserves_visible_todo_relative_positions(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": True} for i in range(100)]
        self.note.render()
        self.app.root.update()
        boundary = self.text.winfo_height() - self.note.body.winfo_height() - self.note._buffer_margin - 1
        self.note._scroll_to(boundary)
        self.app.root.update()
        row = self.note._body_index(60, self.note.body.winfo_height() // 2).split(".")[0]
        old_chunk = self.note._chunk_start

        def positions():
            glyph = self.text.bbox(f"{row}.3")
            box = next(w for w in self.note._checkbox_widgets if w._todo_row == int(row))
            bar = next(w for w in self.note._strike_widgets if w._text_start.split(".")[0] == row)
            return self.text.winfo_rooty() + glyph[1], box.winfo_rooty(), bar.winfo_rooty()

        before = positions()
        self.note.body.yview_scroll(2, "units")
        self.app.root.update()
        self.assertNotEqual(self.note._chunk_start, old_chunk)
        self.assertEqual([a - b for a, b in zip(positions(), before)], [-2, -2, -2])
        boundary += self.note._chunk_start
        self.note._scroll_to(boundary)
        self.app.root.update()
        row = self.note._body_index(60, self.note.body.winfo_height() // 2).split(".")[0]
        before, old_chunk, old_top = positions(), self.note._chunk_start, self.note.body.canvasy(0)
        self.note._text_wheel(type("Wheel", (), {"delta": -24})())
        moved = round(self.note.body.canvasy(0) - old_top)
        self.assertNotEqual(self.note._chunk_start, old_chunk)
        self.assertEqual([a - b for a, b in zip(positions(), before)], [-moved] * 3)
        self.assertIsNotNone(self.note._wheel_job)
        self.note._stop_wheel()

    def test_wheel_animation_finishes_and_render_cancels_it(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": False}
            for i in range(50)]
        self.note.render()
        self.app.root.update()
        self.text.event_generate("<MouseWheel>", delta=-120)
        def wait_for_scroll():
            if self.note._wheel_job is None:
                self.app.root.quit()
            else:
                self.app.root.after(10, wait_for_scroll)
        self.app.root.after(10, wait_for_scroll)
        self.app.root.after(2000, self.app.root.quit)
        self.app.root.mainloop()
        self.assertIsNone(self.note._wheel_job)
        self.assertLess(abs(self.note._wheel_remaining), 1)
        self.assertGreater(self.note.body.yview()[0], 0)
        self.text.event_generate("<MouseWheel>", delta=-120)
        self.assertIsNotNone(self.note._wheel_job)
        self.note.render()
        self.assertIsNone(self.note._wheel_job)
        self.note.after_idle(self.note.render)
        self.text.event_generate("<MouseWheel>", delta=-120)
        self.assertIsNone(self.note._wheel_job)
        self.assertEqual(self.note._wheel_remaining, 0)

    def test_wheel_works_over_checkbox_and_strike(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": True}
            for i in range(50)]
        self.note.render()
        self.app.root.update()
        for widget in (self.note._checkbox_widgets[0], self.note._strike_widgets[0]):
            with self.subTest(widget=widget):
                old = self.note.body.yview()[0]
                widget.event_generate("<MouseWheel>", delta=-120)
                self.app.root.update()
                self.assertGreater(self.note.body.yview()[0], old)
                self.note._stop_wheel()

    def test_wheel_distance_matches_across_scaling_fonts_and_input_sizes(self):
        clock = [10.0]
        patterns = [(-120,), (-15,) * 8, (-1,) * 120, (-3, -9, -28, -80)]
        with patch.object(self.note.body, "yview", return_value=(.2, .4)), \
                patch.object(self.note.body, "yview_scroll") as scroll, \
                patch.object(self.note.body, "update_idletasks"), \
                patch.object(self.note, "_text_scrolled"), \
                patch.object(self.note, "after", return_value="frame"), \
                patch.object(self.note, "after_cancel"), \
                patch.object(main.time, "monotonic", side_effect=lambda: clock[0]):
            for dpi in (96, 120, 144, 192, 240, 288):
                self.app.root.tk.call("tk", "scaling", dpi / 72)
                scale = self.text.winfo_fpixels("1i") / 96
                for font_size in (11, 20):
                    self.app.base_font.configure(size=font_size)
                    for deltas in patterns:
                        with self.subTest(dpi=dpi, font=font_size, deltas=len(deltas)):
                            self.note._stop_wheel()
                            scroll.reset_mock()
                            for delta in deltas:
                                self.note._text_wheel(type("Wheel", (), {"delta": delta})())
                            for _ in range(100):
                                if self.note._wheel_job is None:
                                    break
                                clock[0] += .016
                                self.note._step_wheel()
                            self.assertIsNone(self.note._wheel_job)
                            moved = sum(call.args[0] for call in scroll.call_args_list)
                            self.assertLessEqual(abs(moved - scale * 36), 1 + 1e-7)
                            self.assertAlmostEqual((moved + self.note._wheel_remaining) / scale, 36)

    def test_tiny_wheel_deltas_accumulate_instead_of_disappearing(self):
        self.app.root.tk.call("tk", "scaling", 96 / 72)
        with patch.object(self.note.body, "yview", return_value=(.2, .4)), \
                patch.object(self.note.body, "yview_scroll") as scroll:
            event = type("Wheel", (), {"delta": -1})()
            self.note._text_wheel(event)
            scroll.assert_not_called()
            self.assertGreater(self.note._wheel_remaining, 0)
            for _ in range(3):
                self.note._text_wheel(event)
            self.assertGreater(sum(call.args[0] for call in scroll.call_args_list), 0)
        self.note._stop_wheel()

    def test_delayed_scroll_frame_catches_up_with_elapsed_time(self):
        steps = []
        with patch.object(self.note.body, "yview_scroll") as scroll, \
                patch.object(self.note.body, "update_idletasks"), \
                patch.object(self.note, "_text_scrolled"), \
                patch.object(self.note, "after", return_value="frame"), \
                patch.object(self.note, "after_cancel"):
            for elapsed in (.016, .1):
                self.note._wheel_remaining = 100
                self.note._wheel_time = 10
                with patch.object(main.time, "monotonic", return_value=10 + elapsed):
                    self.note._step_wheel()
                steps.append(scroll.call_args.args[0])
            self.assertGreater(steps[1], steps[0])
            self.note._stop_wheel()

    def test_long_todo_scroll_never_remeasures_text(self):
        self.note.geometry("300x340")
        self.note.d["lines"] = [
            {"type": "todo", "text": "很长的已完成待办" * 300, "checked": True}]
        self.note.render()
        self.app.root.update()
        self.note.body.yview_moveto(.7)
        self.app.root.update()
        with patch.object(self.text, "index", wraps=self.text.index) as index:
            with patch.object(self.text, "count", wraps=self.text.count) as count:
                self.note.body.yview_scroll(7, "units")
                self.app.root.update()
        self.assertEqual(index.call_count, 0)
        self.assertEqual(count.call_count, 0)
        self.assertTrue(self.note._strike_widgets)

    def test_scroll_does_not_copy_old_overlay_pixels(self):
        self.note.geometry("536x478+100+100")
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i} " + "需要处理的事情" * (i % 3 + 1),
             "checked": i % 2 == 0} for i in range(20)]
        self.note.render()
        self.text.configure(insertwidth=0)
        self.note.lift()
        self.app.root.update()

        def capture():
            main.dwmapi.DwmFlush()
            body = self.note.body
            x, y = body.winfo_rootx(), body.winfo_rooty()
            return ImageGrab.grab(bbox=(x, y, x + body.winfo_width(), y + body.winfo_height()))

        for delta in (-120, -120, 120):
            with self.subTest(delta=delta):
                self.text.event_generate("<MouseWheel>", delta=delta)
                self.note._stop_wheel()
                scrolled = capture()
                self.text.event_generate("<Expose>", x=0, y=0,
                                         width=self.text.winfo_width(), height=self.text.winfo_height())
                self.app.root.update()
                self.assertIsNone(ImageChops.difference(scrolled, capture()).getbbox())

    def test_checkbox_does_not_move_as_its_line_is_clipped_at_bottom(self):
        self.note.geometry("300x500")
        self.app.base_font.configure(size=16)
        self.note.d["lines"] = [{"type": "todo", "text": "啊" * 40, "checked": False}]
        self.note.render()
        self.app.root.update()
        count = self.text.count("1.0", "1.end", "displaylines")[0]
        self.assertEqual(count % 2, 0)
        middle = f"1.0 +{(count + 1) // 2} display lines"
        info = self.text.dlineinfo(middle)
        original_y = self.note._checkbox_widgets[0].winfo_y()
        self.note.body.place_configure(height=info[1] + info[3] - 5)
        self.app.root.update()
        self.assertEqual(self.note._checkbox_widgets[0].winfo_y(), original_y)
        self.note.body.place_configure(height=original_y + 3)
        self.app.root.update()
        self.assertEqual(self.note._checkbox_widgets[0].winfo_y(), original_y)
        self.note.body.place_configure(height=original_y)
        self.app.root.update()
        self.assertGreaterEqual(self.note._checkbox_widgets[0].winfo_rooty(),
                                self.note.body.winfo_rooty() + self.note.body.winfo_height())

    def test_add_button_border_is_visibly_thick(self):
        image = main.ImageTk.getimage(self.note._pill_image).convert("RGB")
        middle = image.width // 2
        self.assertLess(max(image.getpixel((middle, 1))), 100)
        self.assertLess(max(image.getpixel((middle, 2))), 100)

    def test_queued_result_replaces_pinyin_once(self):
        self.text.mark_set("insert", "2.end")
        original = self.note.d["lines"][1]["text"]
        for kind, value, cursor in [
            ("start", None, None), ("update", "ni", 2),
            ("result", "你", None), ("update", "", 0), ("end", None, None),
        ]:
            self.note._ime_pending.put((kind, self.text, value, cursor))
        def wait_for_result():
            if self.note.d["lines"][1]["text"] == original + "你" and not self.note._ime_active:
                self.app.root.quit()
            else:
                self.app.root.after(10, wait_for_result)
        self.app.root.after(10, wait_for_result)
        self.app.root.after(2000, self.app.root.quit)
        self.app.root.mainloop()
        self.assertEqual(self.text.get("2.3", "2.end"), original + "你")
        self.assertEqual(self.note.d["lines"][1]["text"], original + "你")
        self.assertFalse(self.note._ime_active)

    def test_strike_has_no_padding_offset_and_crosses_text(self):
        self.note.toggle_done(1)
        self.app.root.update()
        bar = self.note._strike_widgets[0]
        x, y, _, height = self.text.bbox("2.3")
        self.assertEqual(bar.winfo_x(), x)
        self.assertTrue(y + height * .35 <= bar.winfo_y() <= y + height * .65)
        self.assertEqual(bar.winfo_height(), 2)
        self.note.toggle_done(1)
        self.app.root.update()
        self.assertFalse(self.note._strike_widgets)

    def test_completed_last_todo_has_strike_without_following_line(self):
        self.note.d["lines"] = [
            {"type": "text", "text": "前一行", "checked": False},
            {"type": "todo", "text": "最后一项", "checked": True},
        ]
        self.note.render()
        self.app.root.update()
        self.assertEqual(len(self.note._strike_widgets), 1)
        self.assertGreater(self.note._strike_widgets[0].winfo_width(), 1)

    def test_completed_wrapped_todo_strikes_every_display_line(self):
        self.note.geometry("300x500")
        self.note.d["lines"] = [{"type": "todo", "text": "啊" * 55, "checked": True}]
        self.note.render()
        self.app.root.update()
        bars = self.note._strike_widgets
        self.assertGreater(len(bars), 1)
        for bar in bars[:-1]:
            self.assertGreater(bar.winfo_width(), self.text.winfo_width() // 2)

    def test_selecting_newline_does_not_paint_the_empty_row_tail(self):
        self.text.tag_add("sel", "2.end", "3.0")
        self.app.root.update()
        self.assertTrue(self.text.tag_ranges("sel"))
        self.assertFalse(self.text.tag_ranges("selection_fill"))
        self.text.tag_add("sel", "2.3", "2.end")
        self.app.root.update()
        self.assertTrue(self.text.tag_ranges("selection_fill"))

    def test_cross_row_delete_stays_deleted_after_redraw(self):
        first, second = self.note.d["lines"][1:3]
        expected = first["text"][:2] + second["text"][2:]
        self.text.mark_set("insert", "2.5")
        self.text.tag_add("sel", "2.5", "3.5")
        self.text.delete("sel.first", "sel.last")
        self.note._on_keyrelease(None)
        self.assertEqual(len(self.note.d["lines"]), 3)
        self.assertEqual(self.note.d["lines"][1]["text"], expected)
        self.note.render()
        self.assertEqual(self.text.get("2.3", "2.end"), expected)

    def test_toggling_wrapped_todos_keeps_exact_pixel_position(self):
        self.note.geometry("340x340")
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i} " + "需要处理的事项" * (i % 4 + 1),
             "checked": i % 3 == 0} for i in range(45)]
        self.note.render()
        self.app.root.update()
        for fraction in (.3, .65, 1):
            with self.subTest(fraction=fraction):
                self.note.body.yview_moveto(fraction)
                self.note.body.yview_scroll(-7, "units")
                self.app.root.update()
                anchor = self.note._body_index()
                top = self.text.winfo_rooty() + self.text.dlineinfo(anchor)[1]
                target = self.note._body_index(30, 100)
                row = int(target.split(".")[0]) - 1
                target_y = self.text.winfo_rooty() + self.text.dlineinfo(target)[1]
                for _ in range(6):
                    self.note.toggle_done(row)
                    self.app.root.update()
                    self.assertEqual(self.note._body_index(), anchor)
                    self.assertEqual(self.text.winfo_rooty() + self.text.dlineinfo(anchor)[1], top)
                    self.assertEqual(self.text.winfo_rooty() + self.text.dlineinfo(target)[1], target_y)

    def test_list_actions_keep_scrolled_view(self):
        actions = ("toggle", "convert", "insert", "delete", "add", "enter",
                   "clear", "restore", "sync")
        for action in actions:
            with self.subTest(action=action):
                self.note.d["lines"] = [
                    {"type": "todo", "text": f"待办 {i}", "checked": i == 26}
                    for i in range(40)]
                self.note.render()
                self.app.root.update()
                if action == "restore":
                    self.note.clear_done()
                    self.app.root.update()
                self.text.mark_set("insert", "28.3")
                self.note._see_text("insert")
                self.app.root.update()
                top_row = int(self.note._body_index().split(".")[0])
                if action == "toggle":
                    # 点击勾选框时，编辑光标可以仍在屏幕外。
                    self.text.mark_set("insert", "1.3")
                    for checked in (True, False):
                        self.note.toggle_done(27)
                        self.app.root.update()
                        self.assertEqual(self.note.d["lines"][27]["checked"], checked)
                        self.assertEqual(self.text.index("insert"), "1.3")
                        self.assertIsNotNone(self._visible_bbox("28.3"))
                        self.assertEqual(int(self.note._body_index().split(".")[0]), top_row)
                elif action == "convert":
                    self.note.convert(27, "text")
                    self.assertEqual(self.text.index("insert"), "28.0")
                elif action == "insert":
                    self.note.insert_line(27)
                    self.assertEqual(self.text.index("insert"), "29.0")
                elif action == "delete":
                    self.note.delete_line(27)
                    self.assertEqual(self.text.index("insert"), "28.3")
                elif action == "add":
                    self.note.add_todo()
                    self.assertEqual(self.text.index("insert"), "29.3")
                elif action == "enter":
                    self.note._on_enter(None)
                    self.assertEqual(self.text.index("insert"), "29.3")
                elif action == "clear":
                    self.note.clear_done()
                    self.assertEqual(len(self.note.d["lines"]), 39)
                elif action == "restore":
                    self.app.return_archive(self.app.data["archives"][-1]["id"])
                    self.assertEqual(len(self.note.d["lines"]), 40)
                elif action == "sync":
                    self.text.delete("28.0", "28.3")
                    self.note._on_keyrelease(None)
                    self.assertEqual(self.note.d["lines"][27]["type"], "text")
                self.app.root.update()
                self.assertLessEqual(
                    abs(int(self.note._body_index().split(".")[0]) - top_row), 1)
                if action not in ("toggle", "clear", "restore"):
                    self.assertIsNotNone(self._visible_bbox("insert"))

    def test_delete_last_line_keeps_neighbor_cursor_and_short_list_cannot_scroll(self):
        self.note.d["lines"] = [
            {"type": "todo", "text": f"待办 {i}", "checked": False}
            for i in range(40)]
        self.note.render()
        self.app.root.update()
        self.note._see_text("end-1c")
        self.note.delete_line(39)
        self.app.root.update()
        self.assertEqual(self.text.index("insert"), "39.3")
        self.assertIsNotNone(self._visible_bbox("insert"))
        for row in range(38, -1, -1):
            self.note.delete_line(row)
        self.app.root.update()
        self.assertEqual(self.note.d["lines"], [
            {"type": "text", "text": "", "checked": False}])
        self.assertEqual(self.text.index("insert"), "1.0")
        self.assertEqual(self.note.body.yview(), (0.0, 1.0))

    def test_backspace_empty_todo_keeps_scrolled_view_and_previous_row_cursor(self):
        for row in (25, 40):
            with self.subTest(row=row):
                self.note.d["lines"] = [
                    {"type": "todo", "text": f"待办 {i}" if i < 15 else "",
                     "checked": i == 0} for i in range(40)]
                self.note.render()
                self.app.root.update()
                self.text.mark_set("insert", f"{row}.3")
                self.note._see_text("insert")
                self.app.root.update()
                for _ in range(3):
                    previous_lines = [dict(line) for line in self.note.d["lines"]]
                    cursor_row = int(self.text.index("insert").split(".")[0])
                    top_row = int(self.note._body_index().split(".")[0])
                    self.assertEqual(self.note._on_backspace(None), "break")
                    self.app.root.update()
                    del previous_lines[cursor_row - 1]
                    self.assertEqual(self.note.d["lines"], previous_lines)
                    self.assertEqual(self.text.index("insert"), f"{cursor_row - 1}.3")
                    self.assertIsNotNone(self._visible_bbox("insert"))
                    self.assertLessEqual(
                        abs(int(self.note._body_index().split(".")[0]) - top_row), 1)

    def test_enter_replaces_selected_text(self):
        original = self.note.d["lines"][1]["text"]
        self.text.tag_add("sel", "2.5", "2.end")
        self.text.mark_set("insert", "2.end")
        self.note._on_enter(None)
        self.assertEqual(self.note.d["lines"][1]["text"], original[:2])
        self.assertEqual(self.note.d["lines"][2]["text"], "")

    def test_enter_at_start_of_checked_todo_keeps_text_and_adds_empty_todo_below(self):
        original = dict(self.note.d["lines"][2])
        self.note.toggle_done(2)
        self.text.mark_set("insert", f"3.{self.note._prefix_len(original)}")
        self.note._on_enter(None)
        self.app.root.update()
        self.assertEqual(self.note.d["lines"][2], dict(original, checked=True))
        self.assertEqual(self.note.d["lines"][3],
                         {"type": "todo", "text": "", "checked": False})
        self.assertEqual(self.text.index("insert"), "4.3")
        self.assertEqual(len(self.note._strike_widgets), 1)

    def test_cancel_cross_row_ime_restores_todo_icons_and_text(self):
        original = [dict(line) for line in self.note.d["lines"]]
        self.text.tag_add("sel", "2.0", "4.end")
        self.note._begin_preedit(self.text)
        self.note._replace_preedit("ni", 2)
        self.note._finish_preedit()
        self.app.root.update()
        self.assertEqual(self.note.d["lines"], original)
        self.assertEqual(len(self.text.image_names()), 3)

    def test_drag_can_start_on_completed_strike(self):
        self.note.toggle_done(1)
        self.app.root.update()
        bar = self.note._strike_widgets[0]
        bar.event_generate("<Button-1>", x=2, y=1, time=1000)
        bar.event_generate("<B1-Motion>", x=bar.winfo_width() - 2, y=1, state=256, time=1100)
        bar.event_generate("<ButtonRelease-1>", x=bar.winfo_width() - 2, y=1, time=1200)
        self.app.root.update()
        self.assertTrue(self.text.tag_ranges("sel"))

    def test_footer_labels_align_with_button_without_overlap(self):
        self.note.geometry("300x340")
        self.app.root.update()
        center = self.note.add_pill.winfo_y() + self.note.add_pill.winfo_height() / 2
        for label in (self.note.status, self.note.clear_btn):
            label_center = label.winfo_rooty() - self.note.winfo_rooty() + label.winfo_height() / 2
            self.assertLessEqual(abs(label_center - center), 1)
        self.assertIs(self.note.status.master, self.note)
        self.assertEqual(self.note.status.winfo_height(), self.note.bar.winfo_height())
        self.assertLess(self.note.bar.winfo_x() + self.note.status.winfo_width(),
                        self.note.add_pill.winfo_x())
        self.assertGreater(self.note.bar.winfo_x() + self.note.clear_btn.winfo_x(),
                           self.note.add_pill.winfo_x() + self.note.add_pill.winfo_width())

    def test_body_uses_space_above_footer_when_resized(self):
        for height in (230, 340, 500):
            self.note.geometry(f"300x{height}")
            self.app.root.update()
            gap = self.note.bar.winfo_y() - (self.note.body.winfo_y() + self.note.body.winfo_height())
            self.assertEqual(gap, 8)

    def test_body_shrinks_before_footer_moves_up(self):
        self.note.geometry("300x500")
        self.app.root.update()
        body_heights_at_footer_move = []
        place_bar = self.note.bar.place

        def record_body_height(**kwargs):
            body_heights_at_footer_move.append(int(self.note.body.place_info()["height"]))
            return place_bar(**kwargs)

        with patch.object(self.note.bar, "place", side_effect=record_body_height):
            self.note.geometry("300x230")
            self.app.root.update()
        self.assertEqual(body_heights_at_footer_move[-1], self.note.body.winfo_height())

    def test_title_grows_and_shrinks_while_editing_without_hiding_buttons(self):
        self.note.d["title"] = "日"
        self.note.edit_title()
        self.app.root.update()
        entry = self.note._title_editor
        short_width = self.note.title_chip.winfo_width()
        entry.insert("end", "常工作安排")
        self.app.root.update()
        self.assertGreater(self.note.title_chip.winfo_width(), short_width)
        long_title = entry.get() + "项目进度" * 30
        entry.delete(0, "end")
        entry.insert(0, long_title)
        self.app.root.update()
        self.assertEqual(self.note.title_chip.winfo_width(), self.note._title_max_width())
        title_right = self.note.title_box.winfo_x() + self.note.title_chip.winfo_width()
        for button in self.note._tool_btns[1:]:
            self.assertTrue(button.winfo_ismapped())
            self.assertGreater(button.winfo_x(), title_right)
        entry.delete(1, "end")
        self.app.root.update()
        self.assertEqual(self.note.title_chip.winfo_width(), short_width)

    def test_long_title_uses_more_space_when_window_widens_and_saves_in_full(self):
        title = "本周任务与项目进度" * 20
        self.note.d["title"] = title
        self.note.edit_title()
        self.app.root.update()
        old_width = self.note.title_chip.winfo_width()
        self.note.geometry("640x340")
        self.app.root.update()
        self.assertGreater(self.note.title_chip.winfo_width(), old_width)
        self.assertEqual(self.note.title_chip.winfo_width(), self.note._title_max_width())
        self.note._title_editor.event_generate("<Return>")
        self.app.root.update()
        self.assertEqual(self.note.d["title"], title)

    def test_title_resizes_during_inline_ime_and_restores_on_cancel(self):
        self.note.d["title"] = "日"
        self.note.edit_title()
        self.app.root.update()
        entry = self.note._title_editor
        entry.focus_force()
        self.app.root.update()
        old_width = self.note.title_chip.winfo_width()
        self.note._begin_preedit(entry)
        self.note._replace_preedit("zhongwenbiaoti" * 10)
        self.app.root.update()
        self.assertGreater(self.note.title_chip.winfo_width(), old_width)
        self.assertAlmostEqual(entry.xview()[1], 1)
        self.note._finish_preedit()
        self.app.root.update()
        self.assertEqual(entry.get(), "日")
        self.assertEqual(self.note.title_chip.winfo_width(), old_width)

    def test_title_entry_keeps_chinese_text_and_saves(self):
        self.note.edit_title()
        self.app.root.update()
        entry = self.note._title_editor
        entry.delete(0, "end")
        entry.insert(0, "中文标题")
        self.app.root.update()
        self.assertEqual(entry.get(), "中文标题")
        self.assertEqual(self.note._ime_target, entry)
        entry.event_generate("<Return>")
        self.app.root.update()
        self.assertEqual(self.note.d["title"], "中文标题")

    def test_title_uses_inline_native_ime_without_system_composition_background(self):
        note = self.note
        note.d["title"] = "原标题"
        note.edit_title()
        self.app.root.update()
        entry = note._title_editor
        self.assertEqual(note._ime_target, entry)
        with patch.object(main.user32, "CallWindowProcW", return_value=0) as forward:
            note._ime_proc(note.winfo_id(), 0x0281, 1, 0xC000000F)
            self.assertEqual(forward.call_args.args[-1], 0x4000000F)
        note._ime_proc(note.winfo_id(), 0x010D, 0, 0)
        with patch.object(main.imm32, "ImmGetContext", return_value=1), \
             patch.object(main.imm32, "ImmReleaseContext"), \
             patch.object(main.imm32, "ImmGetCompositionStringW", return_value=3), \
             patch.object(note, "_ime_string", return_value="xin"):
            note._ime_proc(note.winfo_id(), 0x010F, 0, 0x0008)
        self.assertEqual(entry.get(), "原标题")  # 原生回调只排队，不操作 Tk。
        note._pump_inline_ime()
        self.assertEqual(entry.get(), "xin")
        self.assertEqual(entry.cget("bg"), note._bg)
        self.assertEqual(note.d["title"], "原标题")
        with patch.object(main.imm32, "ImmGetContext", return_value=1), \
             patch.object(main.imm32, "ImmReleaseContext"), \
             patch.object(note, "_ime_string", return_value="新标题"):
            note._ime_proc(note.winfo_id(), 0x010F, 0, 0x0800)
        note._ime_proc(note.winfo_id(), 0x010E, 0, 0)
        entry.event_generate("<Return>")  # 结果仍在队列时不能提前销毁输入框。
        self.assertTrue(entry.winfo_exists())
        note._pump_inline_ime()
        self.assertEqual(entry.get(), "新标题")
        entry.event_generate("<Return>")
        self.app.root.update()
        self.assertEqual(note.d["title"], "新标题")

    def test_title_focus_out_waits_for_ime_result_or_cancel(self):
        note = self.note
        for result in (None, "确认标题"):
            with self.subTest(result=result):
                note.d["title"] = "原标题"
                note.edit_title()
                self.app.root.update()
                entry = note._title_editor
                note._begin_preedit(entry)
                note._replace_preedit("pinyin", 6)
                if result is not None:
                    note._ime_pending.put(("result", entry, result, None))
                note._ime_pending.put(("end", entry, None, None))
                self.text.focus_force()
                entry.event_generate("<FocusOut>")
                self.assertTrue(entry.winfo_exists())
                note._pump_inline_ime()
                self.app.root.update()
                self.assertEqual(note.d["title"], result or "原标题")
                self.assertIsNone(note._title_editor)

    def test_footer_labels_center_in_their_own_side_regions_at_different_widths(self):
        self.note.status.config(font=(main.UI_FONT, 14))
        self.note.clear_btn.config(font=(main.UI_FONT, 14))
        for width, height in ((280, 230), (320, 340), (460, 500)):
            self.note.geometry(f"{width}x{height}")
            self.app.root.update()
            pill = self.note.add_pill
            left = self.note.bar.winfo_x() + self.note.status.winfo_x()
            right = self.note.bar.winfo_x() + self.note.clear_btn.winfo_x()
            left_gap = pill.winfo_x() - left - self.note.status.winfo_width()
            right_gap = right - pill.winfo_x() - pill.winfo_width()
            self.assertLessEqual(abs(left_gap - left), 1)
            right_outer_gap = width - right - self.note.clear_btn.winfo_width()
            self.assertLessEqual(abs(right_gap - right_outer_gap), 1)
            self.assertGreaterEqual(left_gap, 6)
            self.assertGreaterEqual(left, 12)
            self.assertLessEqual(right + self.note.clear_btn.winfo_width(), width - 12)
            self.assertLessEqual(abs(pill.winfo_x() + pill.winfo_width() / 2 - width / 2), .5)
            self.assertGreaterEqual(pill.winfo_width(), pill.winfo_height() + 4)
            self.assertEqual(height - pill.winfo_y() - pill.winfo_height(), 10)
            if width == 280:
                self.assertEqual(self.note.clear_btn.cget("text"), "清除完成")
        self.assertEqual(self.note.clear_btn.cget("text"), "清除已完成")


if __name__ == "__main__":
    unittest.main()
