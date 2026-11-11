"""Windows UI regressions. Run: python -m unittest discover -s tests -v"""
import unittest
from unittest.mock import patch

from PIL import ImageChops, ImageGrab

import main
import storage


class NoteEditingTests(unittest.TestCase):
    def setUp(self):
        self._start_hotkeys_impl = main.App._start_hotkeys
        self._start_tray_impl = main.App._start_tray
        self._callback_errors = []
        self.patches = [
            patch.object(main.tk.Tk, "report_callback_exception", side_effect=lambda kind, value, trace:
                         self._callback_errors.append(f"{kind.__name__}: {value}")),
            patch.object(main, "load_data", return_value={
                "settings": dict(main.DEFAULT_SETTINGS), "welcomeVersion": main.APP_VERSION,
                "notes": [{"id": "test-note", "x": 100, "y": 90, "w": 300, "h": 340,
                           "z": "top", "opacity": 1.0, "color": "白", "visible": True,
                           "lines": [
                               {"type": "text", "text": "欢迎使用 z-note", "checked": False},
                               {"type": "todo", "text": "点前面的圆框勾选我", "checked": False},
                               {"type": "todo", "text": "右键这行：转换/插入/删除", "checked": False},
                               {"type": "todo", "text": "Ctrl+滚轮 调透明度", "checked": False}]}]}),
            patch.object(main, "save_data"),
            patch.object(main.updater, "check_update", return_value=None),
            patch.object(main.Tooltip, "_font", None),
            patch.object(main.Tooltip, "_cur", None),
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

    def _drag_event(self, x, y, editor=None):
        from types import SimpleNamespace
        body = (editor or self.note.editors[0]).body
        return SimpleNamespace(x_root=round(body.winfo_rootx()+x), y_root=round(body.winfo_rooty()+y))

    def _start_row_drag(self, source, editor=None):
        editor = editor or self.note.editors[0]
        self.app.root.update()
        editor._drag_handle._row = source
        y = int(editor.text.cget("pady")) + sum(
            editor.text.count(f"{i}.0", f"{i+1}.0", "ypixels")[0]
            for i in range(1, source+1)) + 15 - editor.body.canvasy(0)
        event = self._drag_event(editor.body.winfo_width()-44, y, editor)
        editor._row_drag_press(event)
        return event

    def test_drag_mixed_rows_is_preview_only_then_one_undoable_move(self):
        from copy import deepcopy
        editor = self.note.editors[0]
        original = [dict(type="text", text="说明", checked=False),
                    dict(type="todo", text="完成的待办", checked=True),
                    dict(type="text", text="", checked=False),
                    dict(type="todo", text="最后一项", checked=False)]
        editor.lines = deepcopy(original)
        editor.render()
        event = self._start_row_drag(1)
        history = len(editor._edit_history)
        with patch.object(self.app, "schedule_save") as save:
            bottom = self._drag_event(100, editor.body.winfo_height()-2)
            editor._row_drag_motion(bottom)
            self.assertEqual(editor.lines, original)
            self.assertEqual(len(editor._edit_history), history)
            save.assert_not_called()
            self.assertEqual(editor._row_drag["target"], 3)
            editor._row_drag_release(bottom)
            self.assertEqual(editor.lines, [original[i] for i in (0, 2, 3, 1)])
            save.assert_called_once()
        self.assertIsNone(editor._row_drag_job)
        editor._undo_edit()
        self.assertEqual(editor.lines, original)
        editor._undo_edit(redo=True)
        expected = [original[i] for i in (0, 2, 3, 1)]
        self.assertEqual(editor.lines, expected)
        self.app.save_now()
        self.assertEqual(main.save_data.call_args.args[0]["notes"][0]["lines"], expected)
        restored = main.NoteWindow(self.app, deepcopy(self.note.d))
        self.app.root.update()
        self.assertEqual(restored.editors[0].lines, expected)
        restored.editors[0]._sync_lines_from_text()
        self.assertEqual(restored.editors[0].lines, expected)
        restored.destroy()

    def test_drag_cancel_threshold_outside_and_unmap_preserve_rows(self):
        from copy import deepcopy
        editor = self.note.editors[0]
        editor.lines = [dict(type="text", text="第一段", checked=False),
                        dict(type="todo", text="下一项", checked=False)]
        editor.render()
        original = deepcopy(editor.lines)
        for cancel in ("click", "escape", "outside", "unmap"):
            event = self._start_row_drag(0)
            if cancel == "click":
                editor._row_drag_motion(event)
                self.assertIsNone(editor._row_drag["canvas"])
                editor._row_drag_release(event)
            else:
                editor._row_drag_motion(self._drag_event(100, 150))
                if cancel == "escape":
                    self.note.focus_force()
                    self.app.root.update()
                    self.note.event_generate("<Escape>")
                elif cancel == "outside":
                    editor._row_drag_release(self._drag_event(-10, 150))
                else:
                    editor.body.event_generate("<Unmap>")
            self.app.root.update()
            self.assertIsNone(editor._row_drag)
            self.assertIsNone(editor._row_drag_job)
            self.assertEqual(editor.lines, original)
            self.assertIsNone(editor.body.grab_current())

    def test_drag_wrapped_text_and_empty_row_to_top(self):
        editor = self.note.editors[0]
        content = "这是会自动折行的长文字。" * 24
        editor.lines = [dict(type="text", text=content, checked=False),
                        dict(type="text", text="", checked=False),
                        dict(type="todo", text="保留", checked=False)]
        editor.render()
        self.app.root.update()
        editor.text.event_generate("<Motion>", x=80, y=30)
        self.app.root.update()
        handle_y = editor._drag_handle.winfo_y()+editor.text.winfo_y()
        self.assertGreaterEqual(handle_y, 0)
        self.assertLess(handle_y+24, editor.body.winfo_height())
        self._start_row_drag(0)
        self.assertGreater(len(editor._row_drag["rows"][0][1]), 1)
        self.assertEqual("".join(editor._row_drag["rows"][0][1]), content)
        editor._cancel_row_drag()
        editor._scroll_to(0)
        self._start_row_drag(1)
        top = self._drag_event(100, 1)
        editor._row_drag_motion(top)
        editor._row_drag_release(top)
        self.assertEqual([line["text"] for line in editor.lines], ["", content, "保留"])

    def test_drag_autoscroll_and_column_isolation(self):
        from copy import deepcopy
        self.note.toggle_columns()
        editor = self.note.editors[0]
        right = deepcopy(self.note.editors[1].lines)
        editor.lines = [dict(type="text", text=f"段落{i}", checked=False) for i in range(50)]
        editor.render()
        self._start_row_drag(0)
        bottom = self._drag_event(100, editor.body.winfo_height()-2)
        editor._row_drag_motion(bottom)
        before = editor.body.canvasy(0)
        self.app.root.after(120, self.app.root.quit)
        self.app.root.mainloop()
        self.assertGreater(editor.body.canvasy(0), before)
        editor._row_drag_release(bottom)
        self.assertEqual(self.note.editors[1].lines, right)
        self.assertIsNone(editor._row_drag_job)

    def test_cross_column_drag_moves_both_directions_and_is_one_undoable_edit(self):
        self.note.toggle_columns()
        for column in (0, 1):
            for slot in (0, 1, 3):
                with self.subTest(column=column, slot=slot):
                    source, destination = self.note.editors[column], self.note.editors[1-column]
                    original = [{"type": "todo", "text": "完成事项", "checked": True},
                                {"type": "text", "text": "保留文字", "checked": False},
                                {"type": "text", "text": "", "checked": False}]
                    other = [{"type": "text", "text": str(i), "checked": False} for i in range(3)]
                    source.lines, destination.lines = main.deepcopy(original), main.deepcopy(other)
                    source.render(); destination.render()
                    self._start_row_drag(0, source)
                    history = len(self.note._columns_history)
                    row_height = source._row_drag["floating_rows"][source][0]
                    y = 1 if slot == 0 else (destination.body.winfo_height()-2 if slot == 3 else
                        int(destination.text.cget("pady"))+row_height+
                        destination.text.count("1.0", "2.0", "ypixels")[0]+1)
                    event = self._drag_event(80, y, destination)
                    with patch.object(self.app, "schedule_save") as save:
                        source._row_drag_motion(event)
                        self.assertEqual(source._row_drag["target"], slot)
                        self.assertEqual(source.lines, original)
                        self.assertEqual(destination.lines, other)
                        self.assertEqual(len(self.note._columns_history), history)
                        self.assertIs(source._row_drag["destination"], destination)
                        self.assertEqual(source._row_drag["float"].master, self.note)
                        save.assert_not_called()
                        source._row_drag_release(event)
                        save.assert_called_once()
                    expected = main.deepcopy(other)
                    expected.insert(slot, original[0])
                    self.assertEqual(source.lines, original[1:])
                    self.assertEqual(destination.lines, expected)
                    self.assertEqual(destination.text.index("insert"), f"{slot+1}.3")
                    self.assertEqual(len(self.note._columns_history), history+1)
                    source._undo_edit()
                    self.assertEqual(source.lines, original)
                    self.assertEqual(destination.lines, other)
                    destination._undo_edit(redo=True)
                    self.assertEqual(source.lines, original[1:])
                    self.assertEqual(destination.lines, expected)
        self.app.save_now()
        restored = main.NoteWindow(self.app, main.deepcopy(self.note.d))
        self.assertEqual([e.lines for e in restored.editors], [e.lines for e in self.note.editors])
        restored.destroy()
        before = main.deepcopy([e.lines for e in self.note.editors])
        self.note.toggle_columns(); self.note.toggle_columns()
        self.assertEqual([e.lines for e in self.note.editors], before)

    def test_cross_column_drag_replaces_empty_target_and_keeps_source_editable(self):
        self.note.toggle_columns()
        for kind, content in (("todo", "已完成"), ("text", "文字"), ("text", "")):
            left, right = self.note.editors
            line = {"type": kind, "text": content, "checked": kind == "todo"}
            left.lines = [main.deepcopy(line)]
            right.lines = [{"type": "todo", "text": "", "checked": False}]
            left.render(); right.render()
            self._start_row_drag(0, left)
            event = self._drag_event(80, 30, right)
            left._row_drag_motion(event); left._row_drag_release(event)
            self.assertEqual(left.lines, [{"type": "todo", "text": "", "checked": False}])
            self.assertEqual(right.lines, [line])
            self._start_row_drag(0, right)
            event = self._drag_event(80, 30, left)
            right._row_drag_motion(event); right._row_drag_release(event)
            self.assertEqual(left.lines, [line])
            self.assertEqual(right.lines, [{"type": "todo", "text": "", "checked": False}])

    def test_cross_column_undo_orders_later_input_before_the_move(self):
        self.note.toggle_columns()
        left, right = self.note.editors
        left.lines = [{"type": "text", "text": "左", "checked": False}]
        right.lines = [{"type": "text", "text": "右", "checked": False}]
        left.render(); right.render()
        original = main.deepcopy([e.lines for e in self.note.editors])
        left.text.insert("1.end", "先编辑"); left._on_keyrelease(None)
        edited = main.deepcopy([e.lines for e in self.note.editors])
        self._start_row_drag(0, left)
        event = self._drag_event(80, 1, right)
        left._row_drag_motion(event); left._row_drag_release(event)
        moved = main.deepcopy([e.lines for e in self.note.editors])
        right.text.insert("1.end", "后编辑"); right._on_keyrelease(None)
        final = main.deepcopy([e.lines for e in self.note.editors])
        for expected in (moved, edited, original):
            left._undo_edit()
            self.assertEqual([e.lines for e in self.note.editors], expected)
        for expected in (edited, moved, final):
            right._undo_edit(redo=True)
            self.assertEqual([e.lines for e in self.note.editors], expected)
        left._undo_edit()
        left.text.insert("1.end", "新输入"); left._on_keyrelease(None)
        self.assertFalse(self.note._columns_redo)

    def test_cross_column_drag_cancellation_cleans_both_previews_and_capture(self):
        self.note.toggle_columns()
        left, right = self.note.editors
        left.lines = [{"type": "todo", "text": "保留", "checked": True}]
        left.render()
        original = main.deepcopy([e.lines for e in self.note.editors])
        for cancel in ("escape", "divider", "outside", "hide", "columns"):
            left, right = self.note.editors
            self._start_row_drag(0, left)
            left._row_drag_motion(self._drag_event(80, 30, right))
            widgets = [*left._row_drag["canvases"].values(), left._row_drag["float"], left._row_drag["measure"]]
            if cancel == "escape":
                left._cancel_row_drag()
            elif cancel == "divider":
                event = self._drag_event(left.body.winfo_width()+7, 30, left)
                left._row_drag_motion(event)
                self.assertIsNone(left._row_drag["destination"])
                left._row_drag_release(event)
            elif cancel == "outside":
                left._row_drag_release(self._drag_event(-20, 30, left))
            elif cancel == "hide":
                self.note.hide(); self.app.root.update(); self.note.show()
            else:
                self.note.toggle_columns(); self.note.toggle_columns()
            self.app.root.update()
            self.assertEqual([e.lines for e in self.note.editors], original)
            self.assertIsNone(left._row_drag)
            self.assertIsNone(left._row_drag_job)
            self.assertIsNone(self.note.grab_current())
            self.assertTrue(all(not widget.winfo_exists() for widget in widgets))

    def test_cross_column_drag_wraps_using_target_text_metrics_and_scrolls_only_target(self):
        self.note.toggle_columns()
        left, right = self.note.editors
        content = "很长的已完成待办，拖动时保留软换行。" * 30
        left.lines = [{"type": "todo", "text": content, "checked": True}]
        right.lines = [{"type": "text", "text": str(i), "checked": False} for i in range(40)]
        left.render(); right.render()
        self._start_row_drag(0, left)
        event = self._drag_event(80, right.body.winfo_height()-2, right)
        left._row_drag_motion(event)
        self.assertEqual("".join(left._row_drag["floating_rows"][right][1]), content)
        self.assertGreater(len(left._row_drag["floating_rows"][right][1]), 1)
        source_scroll, target_scroll = left.body.canvasy(0), right.body.canvasy(0)
        self.app.root.after(120, self.app.root.quit); self.app.root.mainloop()
        self.assertEqual(left.body.canvasy(0), source_scroll)
        self.assertGreater(right.body.canvasy(0), target_scroll)
        left._cancel_row_drag()
        self.assertEqual(right.body.canvasy(0), 0)

    def test_drag_handle_hover_and_fast_gutter_press_do_not_select_text(self):
        editor = self.note.editors[0]
        editor.lines = [dict(type="text", text="普通文字", checked=False),
                        dict(type="todo", text="待办", checked=False)]
        editor.render()
        self.app.root.update()
        editor.text.event_generate("<Motion>", x=80, y=30)
        self.app.root.update()
        self.assertEqual(editor._drag_handle.winfo_manager(), "place")
        self.assertEqual(editor._drag_handle._row, 0)
        self.assertGreaterEqual(editor.text.winfo_width() -
                                editor._drag_handle.winfo_x()-editor._drag_handle.winfo_width(), 19)
        self.assertEqual(editor._drag_handle.cget("cursor"), self.note.bar.cget("cursor"))
        self.assertIn(editor._drag_handle.winfo_id(), main._DRAG_HWNS)
        self.assertTrue(editor._drag_handle.bind("<Enter>"))
        self.assertIn("_cursor_restore", editor._drag_handle.bind("<Leave>"))
        editor._drag_handle.place_forget()
        x = editor.text.winfo_width()-44
        editor.text.event_generate("<Button-1>", x=x, y=30)
        self.assertIsNotNone(editor._row_drag)
        editor.text.event_generate("<B1-Motion>", x=x, y=120)
        self.assertIsNotNone(editor._row_drag["canvas"])
        self.assertFalse(editor.text.tag_ranges("sel"))
        editor.text.event_generate("<ButtonRelease-1>", x=x, y=120)
        self.assertEqual([line["text"] for line in editor.lines], ["待办", "普通文字"])
        self.assertIsNone(editor._row_drag)

    def test_drag_handle_hides_after_pointer_leaves_note_horizontally(self):
        editor = self.note.editors[0]
        editor.lines = [dict(type="text", text="拖动图标", checked=False)]
        editor.render()
        self.note.geometry("400x300+100+100")
        self.app.root.update()
        y = editor.text.winfo_rooty() + 30
        left = editor.text.winfo_rootx()
        right = left + editor.text.winfo_width()
        for inside, outside in ((right-1, self.note.winfo_rootx()+self.note.winfo_width()+20),
                                (left, self.note.winfo_rootx()-20)):
            with self.subTest(outside=outside):
                editor.text.event_generate("<Motion>", x=80, y=30)
                self.app.root.update()
                with patch.object(editor._drag_handle, "winfo_pointerxy", return_value=(inside, y)):
                    editor._drag_gutter.event_generate("<Leave>")
                    self.app.root.update()
                    self.assertEqual(editor._drag_handle.winfo_manager(), "place")
                # 离开子控件时仍在正文内，随后从窗口边缘离开，不再经过正文控件。
                with patch.object(editor._drag_handle, "winfo_pointerxy", return_value=(outside, y)):
                    self.note.event_generate("<Leave>")
                    self.app.root.update()
                    self.assertFalse(editor._drag_handle.winfo_manager())

    def test_select_all_keeps_wrapped_rows_drag_gutter_unpainted(self):
        editor = self.note.editors[0]
        self.note.geometry("300x380+100+100")
        for kind in ("text", "todo"):
            with self.subTest(kind=kind):
                content = "快捷转待办文字多行选中文字" * 3
                editor.lines = [dict(type=kind, text=content, checked=False)]
                editor.render()
                self.app.root.update()
                x = editor.text.winfo_width() - 44
                editor.text.event_generate("<Motion>", x=x, y=30)
                self.app.root.update()
                handle = editor._drag_handle
                bounds = (editor.text.winfo_rootx() + editor.text.winfo_width() - 60,
                          handle.winfo_rooty(), handle.winfo_rootx() + handle.winfo_width() + 2,
                          handle.winfo_rooty() + handle.winfo_height())
                before = ImageGrab.grab(bounds)
                editor._select_all(None)
                self.app.root.update()
                self.assertTrue(editor.text.tag_ranges("sel"))
                self.assertIsNone(ImageChops.difference(before, ImageGrab.grab(bounds)).getbbox())
                editor.text.event_generate("<<Copy>>")
                self.assertEqual(editor.text.clipboard_get(), content)
                self.assertEqual(self.app.root.winfo_containing(
                    handle.winfo_rootx() + 12, handle.winfo_rooty() + 12), handle)
                handle.place_forget()
                editor._drag_gutter.event_generate("<Button-1>",
                    x=x-editor._drag_gutter.winfo_x(), y=30)
                self.assertIsNotNone(editor._row_drag)
                editor._cancel_row_drag()

    def test_drag_handle_aligns_vertically_with_text_and_todo(self):
        editor = self.note.editors[0]
        editor.lines = [dict(type="text", text="普通文字", checked=False),
                        dict(type="todo", text="待办内容", checked=False),
                        dict(type="text", text="", checked=False)]
        editor.render()
        self.app.root.update()
        for row, line in enumerate(editor.lines, 1):
            with self.subTest(row=row):
                box = editor.text.bbox(f"{row}.{editor._prefix_len(line)}")
                center = box[1]+box[3]/2
                editor.text.event_generate("<Motion>", x=80, y=round(center))
                self.app.root.update()
                handle = editor._drag_handle
                self.assertAlmostEqual(handle.winfo_y()+handle.winfo_height()/2, center, delta=1)
                if line["type"] == "todo":
                    checkbox = next(w for w in editor._checkbox_widgets if w._todo_row == row)
                    self.assertAlmostEqual(handle.winfo_y()+handle.winfo_height()/2,
                                           checkbox.winfo_y()+checkbox.winfo_height()/2, delta=1)

    def test_drag_midpoints_stay_stable_and_keep_wrapped_todo_caret(self):
        editor = self.note.editors[0]
        content = "长待办内容，用来验证折行。" * 8
        editor.lines = [dict(type="text", text="前面的文字", checked=False),
                        dict(type="text", text="后面的文字", checked=False),
                        dict(type="todo", text=content, checked=True)]
        editor.render()
        self.text.mark_set("insert", "3.40")
        self._start_row_drag(2)
        self.assertEqual("".join(editor._row_drag["rows"][2][1]), content)
        top = self._drag_event(100, 20)
        editor._row_drag_motion(top)
        self.assertEqual(editor._row_drag["target"], 0)
        editor._paint_row_drag()
        self.assertEqual(editor._row_drag["target"], 0)
        editor._row_drag_release(top)
        self.assertEqual(editor.lines[0]["text"], content)
        self.assertTrue(editor.lines[0]["checked"])
        self.assertEqual(self.text.index("insert"), "1.40")
        editor._undo_edit()
        self.assertEqual(self.text.index("insert"), "3.40")

    def test_new_note_uses_compact_default_width(self):
        note = self.app.new_note()
        self.app.root.update()
        self.assertEqual((note.d["w"], note.d["h"]), (440, 477))
        self.assertEqual((note.winfo_width(), note.winfo_height()), (440, 477))

    def test_new_note_is_visible_without_pinning(self):
        note = self.app.new_note()
        self.app.root.update()
        self.assertEqual(note.d["z"], "normal")
        self.assertFalse(note.attributes("-topmost"))
        self.assertTrue(note.d["visible"])
        self.assertEqual(note.state(), "normal")

    def test_legacy_unpinned_notes_stay_normal_and_new_bottom_state_survives_restart(self):
        from copy import deepcopy
        original = deepcopy(self.note.d)
        for version, expected in ((2, "normal"), (3, "bottom")):
            for job in self.app.root.tk.call("after", "info"):
                self.app.root.tk.call("after", "cancel", job)
            self.app.root.destroy()
            data = {"schemaVersion": version, "settings": dict(main.DEFAULT_SETTINGS),
                    "notes": [dict(deepcopy(original), z="bottom")],
                    "deletedNotes": [dict(deepcopy(original), id="deleted", z="bottom")]}
            with patch.object(main, "load_data", return_value=data):
                self.app = main.App()
            self.note = next(iter(self.app.notes.values()))
            self.assertEqual(self.note._temporarily_raised, expected == "bottom")
            self.app.root.update()
            self.assertEqual(self.app.data["schemaVersion"], 3)
            self.assertEqual(self.note.d["z"], expected)
            self.assertEqual(self.app.data["deletedNotes"][0]["z"], expected)
            self.assertEqual(self.note.b_bottom._active, expected == "bottom")

    def test_layer_buttons_toggle_and_last_clicked_wins(self):
        note = self.note
        for button, state in ((note.b_bottom, "bottom"), (note.b_pin, "top"),
                              (note.b_pin, "normal"), (note.b_bottom, "bottom"),
                              (note.b_bottom, "normal"), (note.b_pin, "top"),
                              (note.b_bottom, "bottom")):
            button.event_generate("<Button-1>")
            self.app.root.update()
            self.assertEqual(note.d["z"], state)
            self.assertEqual(note.b_pin._active, state == "top")
            self.assertEqual(note.b_bottom._active, state == "bottom")
            self.assertEqual(bool(note.attributes("-topmost")), state == "top")
            for item in (note.b_pin, note.b_bottom):
                expected = item._active_image if item._active else item._image
                self.assertEqual(item.itemcget(item._icon, "image"), str(expected))

    def test_layer_buttons_keep_selected_background_after_pointer_leaves(self):
        for state in ("top", "bottom", "normal"):
            self.note.set_z(state)
            for button, selected in ((self.note.b_pin, state == "top"),
                                     (self.note.b_bottom, state == "bottom")):
                with self.subTest(state=state, button=button):
                    button._on_enter(None)
                    button._on_leave(None)
                    self.assertEqual(button.itemcget(button._active_pill, "state"),
                                     "normal" if selected else "hidden")
                    self.assertEqual(button.itemcget(button._pill, "state"), "hidden")
                    self.assertEqual(button.itemcget(button._active_pill, "fill"), "#E5E9EF")
                    if not selected:
                        button._on_enter(None)
                        self.assertEqual(button.itemcget(button._pill, "state"), "normal")
                        button._on_leave(None)
        self.note.toggle_columns()
        self.assertTrue(self.note.b_columns._active)
        self.assertIsNone(self.note.b_columns._active_pill)

    def test_bottom_note_stays_below_other_windows_when_shown_or_activated(self):
        blocker = main.tk.Toplevel(self.app.root)
        blocker.geometry("300x240+30+30")
        self.app.root.update()
        blocker_hwnd = main.user32.GetParent(blocker.winfo_id()) or blocker.winfo_id()
        self.note.set_z("bottom")
        for action in (self.note.show, self.note.text.focus_force, self.note.lift,
                       lambda: self.note.geometry("500x400+60+60")):
            action()
            self.app.root.update()
            order = []
            callback = main.ctypes.WINFUNCTYPE(main.wintypes.BOOL, main.wintypes.HWND,
                                              main.wintypes.LPARAM)(
                lambda hwnd, _: order.append(hwnd) or True)
            main.user32.EnumWindows(callback, 0)
            self.assertLess(order.index(blocker_hwnd), order.index(self.note.hwnd))
            self.assertFalse(main.user32.GetWindowLongPtrW(self.note.hwnd, -20) & 0x8)
        self.note.toggle_bottom()
        self.note.show()
        self.app.root.update()
        self.assertEqual(self.note.d["z"], "normal")

    def _wait_for_bottom_guard(self):
        self.app.root.after(450, self.app.root.quit)
        self.app.root.mainloop()

    def _external_test_window(self):
        import subprocess
        import sys
        process = subprocess.Popen([sys.executable, "-c",
            "import tkinter as tk; r=tk.Tk(); r.geometry('300x240+30+30'); "
            "r.update(); print(r.winfo_id(), flush=True); r.mainloop()"],
            stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        def close():
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()
        self.addCleanup(close)
        client = int(process.stdout.readline())
        return main.user32.GetParent(client) or client

    def test_bottom_recall_tracks_foreground_events_and_native_window_order(self):
        external = self._external_test_window()
        second = self.app.new_note()
        normal = self.app.new_note()
        pinned = self.app.new_note()
        pinned.set_z("top")
        foreground = self.enterContext(patch.object(main.user32, "GetForegroundWindow", return_value=pinned.hwnd))
        activate = self.enterContext(patch.object(main.user32, "SetForegroundWindow",
                                                wraps=main.user32.SetForegroundWindow))
        for note in (self.note, second):
            note.set_z("bottom")
        self.text.mark_set("insert", "2.3")
        cursor = self.text.index("insert")

        def order():
            handles = []
            hwnd = main.user32.GetTopWindow(None)
            while hwnd:
                handles.append(hwnd)
                hwnd = main.user32.GetWindow(hwnd, 2)
            return handles

        for _ in range(2):
            self.app.q.put("show")
            self._wait_for_bottom_guard()
            activate.assert_called_with(pinned.hwnd)
            for note in (self.note, second):
                self.assertTrue(note._temporarily_raised)
                self.assertEqual(note.d["z"], "bottom")
                self.assertTrue(note.b_bottom._active)
                self.assertFalse(main.user32.GetWindowLongPtrW(note.hwnd, -20) & 0x8)
                self.assertLess(order().index(note.hwnd), order().index(external))
                self.assertLess(order().index(pinned.hwnd), order().index(note.hwnd))
        for window in (self.note, normal, second, pinned):
            foreground.return_value = window.hwnd
            self.app._bottom_event_proc(None, 0x0003, window.hwnd, 0, 0, 0, 0)
            self._wait_for_bottom_guard()
            self.assertTrue(self.note._temporarily_raised)
            self.assertTrue(second._temporarily_raised)
        self.assertEqual(self.text.index("insert"), cursor)
        settings = main.SettingsWindow(self.app)
        foreground.return_value = main.user32.GetParent(settings.winfo_id()) or settings.winfo_id()
        self.app._bottom_event_proc(None, 0x0003, foreground.return_value, 0, 0, 0, 0)
        self._wait_for_bottom_guard()
        self.assertTrue(self.note._temporarily_raised)
        settings.destroy()
        main.user32.SetWindowPos(external, 0, 0, 0, 0, 0,
                                main.SWP_NOMOVE | main.SWP_NOSIZE | 0x0010)
        activate.reset_mock()
        foreground.return_value = external
        self.app._bottom_event_proc(None, 0x0003, external, 0, 0, 0, 0)
        self._wait_for_bottom_guard()
        activate.assert_not_called()
        for note in (self.note, second):
            self.assertFalse(note._temporarily_raised)
            self.assertGreater(order().index(note.hwnd), order().index(external))
            self.assertTrue(main.user32.IsWindowVisible(note.hwnd))
            self.assertFalse(main.user32.IsIconic(note.hwnd))
            self.assertEqual(note.d["z"], "bottom")

    def test_bottom_recall_first_show_raises_bottom_notes_from_an_external_app(self):
        external = self._external_test_window()
        normal = self.app.new_note()
        self.note.set_z("bottom")
        foreground = self.enterContext(patch.object(main.user32, "GetForegroundWindow", return_value=external))
        def activate(hwnd):
            foreground.return_value = hwnd
            return True
        self.enterContext(patch.object(main.user32, "SetForegroundWindow", side_effect=activate))
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                if hidden:
                    self.app.hide_all()
                else:
                    self.note.set_z("normal")
                    self.note.set_z("bottom")
                foreground.return_value = external
                for layer in (-1, -2):
                    main.user32.SetWindowPos(external, layer, 0, 0, 0, 0,
                                            main.SWP_NOMOVE | main.SWP_NOSIZE | 0x0010)
                self.app.root.update_idletasks()
                self.app.show_all()
                self.app.root.update()
                self.app._refresh_bottom_notes()
                order = []
                hwnd = main.user32.GetTopWindow(None)
                while hwnd:
                    order.append(hwnd)
                    hwnd = main.user32.GetWindow(hwnd, 2)
                for note in (self.note, normal):
                    self.assertLess(order.index(note.hwnd), order.index(external))
                self.assertTrue(self.note._temporarily_raised)
                self.assertFalse(main.user32.GetWindowLongPtrW(self.note.hwnd, -20) & 0x8)

    def test_bottom_recall_clears_on_layer_change_hide_and_delete(self):
        second = self.app.new_note()
        self.enterContext(patch.object(main.user32, "GetForegroundWindow", return_value=second.hwnd))
        self.note.set_z("bottom")
        second.set_z("bottom")
        self.app.show_all()
        self.app.root.update()
        self.note.toggle_bottom()
        self.assertEqual(self.note.d["z"], "normal")
        self.assertFalse(self.note._temporarily_raised)
        self.note.toggle_bottom()
        self.assertEqual(self.note.d["z"], "bottom")
        self.assertFalse(self.note._temporarily_raised)
        self.assertTrue(second._temporarily_raised)
        self.app.show_all()
        self.note.set_z("top")
        self.assertFalse(self.note._temporarily_raised)
        self.assertTrue(self.note.attributes("-topmost"))
        self.app.hide_all()
        self.assertFalse(second._temporarily_raised)
        self.app.show_all()
        second.destroy()
        self.app.notes.pop(second.d["id"])
        self.assertFalse(second._temporarily_raised)
        self.app._refresh_bottom_notes()

    def test_bottom_startup_display_recalls_until_an_external_app_is_activated(self):
        external = self._external_test_window()
        self.note.set_z("bottom")
        self.note.hide()
        self.note.destroy()
        self.app.notes.clear()

        def order():
            handles = []
            hwnd = main.user32.GetTopWindow(None)
            while hwnd:
                handles.append(hwnd)
                hwnd = main.user32.GetWindow(hwnd, 2)
            return handles

        with patch.object(main.user32, "GetForegroundWindow", return_value=self.app.root.winfo_id()) as foreground:
            self.app._restore_notes()
            self.note = self.app.notes["test-note"]
            foreground.return_value = self.note.hwnd
            self.app.root.update()
            self.app._refresh_bottom_notes()
            self.assertTrue(self.note._temporarily_raised)
            self.assertLess(order().index(self.note.hwnd), order().index(external))
            self.assertEqual(self.note.d["z"], "bottom")
            self.assertTrue(self.note.b_bottom._active)
            self.assertFalse(main.user32.GetWindowLongPtrW(self.note.hwnd, -20) & 0x8)
            foreground.return_value = external
            self.app._refresh_bottom_notes()
            self.assertFalse(self.note._temporarily_raised)
            self.assertGreater(order().index(self.note.hwnd), order().index(external))
            self.assertEqual(self.note.d["z"], "bottom")
            self.assertTrue(main.user32.IsWindowVisible(self.note.hwnd))
        self.assertNotIn("_temporarily_raised", self.note.d)
        self.note.hide()
        self.note.show()
        self.assertFalse(self.note._temporarily_raised)

    def test_bottom_recall_keeps_owned_input_windows_and_activation_gaps(self):
        self.note.set_z("bottom")
        self.app.show_all()
        def process_id(hwnd, pointer):
            main.ctypes.cast(pointer, main.ctypes.POINTER(main.wintypes.DWORD)).contents.value = (
                main.os.getpid() if hwnd == self.note.hwnd else 0)
            return 1
        with patch.object(main.user32, "GetForegroundWindow", return_value=123) as foreground, \
                patch.object(main.user32, "GetWindowThreadProcessId", side_effect=process_id), \
                patch.object(main.user32, "GetWindow", return_value=self.note.hwnd) as owner:
            self.app._refresh_bottom_notes()
            owner.assert_called_once_with(123, 4)
            self.assertTrue(self.note._temporarily_raised)
            foreground.return_value = None
            self.app._refresh_bottom_notes()
            self.assertTrue(self.note._temporarily_raised)

    def test_bottom_anchor_skips_cloaked_system_windows(self):
        def attribute(hwnd, key, pointer, size):
            main.ctypes.cast(pointer, main.ctypes.POINTER(main.wintypes.DWORD)).contents.value = int(hwnd == 20)
            return 0
        def name(hwnd, buffer, size):
            buffer.value = "Progman" if hwnd == 30 else "STATIC"
            return len(buffer.value)
        with patch.object(main.user32, "GetTopWindow", return_value=10), \
                patch.object(main.user32, "GetWindow", side_effect=lambda hwnd, _: {10: 20, 20: 30}[hwnd]), \
                patch.object(main.user32, "IsWindowVisible", return_value=True), \
                patch.object(main.user32, "IsIconic", return_value=False), \
                patch.object(main.user32, "GetClassNameW", side_effect=name), \
                patch.object(main.user32, "GetWindowLongPtrW", return_value=0), \
                patch.object(main.dwmapi, "DwmGetWindowAttribute", side_effect=attribute):
            self.assertEqual(main._bottom_insert_after(self.note.hwnd), 10)

    def test_bottom_guard_uses_the_next_anchor_when_windows_rejects_insertion(self):
        self.note.set_z("bottom")
        calls = []
        def position(hwnd, anchor, *args):
            calls.append(anchor)
            return anchor != 123
        with patch.object(main, "_bottom_insert_after", return_value=123), \
                patch.object(main.user32, "GetWindow", return_value=456), \
                patch.object(main.user32, "GetWindowLongPtrW", return_value=0), \
                patch.object(main.user32, "SetWindowPos", side_effect=position):
            self.app._refresh_bottom_notes()
        self.assertEqual(calls, [123, 456])

    def _external_show_window(self, note, state):
        import subprocess
        import sys
        process = subprocess.Popen([sys.executable, "-c",
            "import ctypes,sys; u=ctypes.windll.user32; "
            "u.ShowWindow.argtypes=[ctypes.c_void_p,ctypes.c_int]; "
            "u.ShowWindow(int(sys.argv[1]),int(sys.argv[2]))", str(note.hwnd), str(state)],
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(process.wait, 5)

    def test_bottom_note_rejects_minimize_command_and_restores_direct_minimize_without_focus(self):
        self.note.set_z("bottom")
        self.note.geometry("440x360+120+130")
        other = main.tk.Toplevel(self.app.root)
        other.focus_force()
        self.app.root.update()
        foreground = main.user32.GetForegroundWindow()
        before = (self.note.winfo_x(), self.note.winfo_y(), self.note.winfo_width(), self.note.winfo_height())
        saved = dict(self.note.d)
        main.user32.PostMessageW(self.note.hwnd, 0x0112, 0xF020, 0)
        self._wait_for_bottom_guard()
        self.assertFalse(main.user32.IsIconic(self.note.hwnd))
        self._external_show_window(self.note, 6)
        self._wait_for_bottom_guard()
        self.assertFalse(main.user32.IsIconic(self.note.hwnd))
        self.assertTrue(main.user32.IsWindowVisible(self.note.hwnd))
        self.assertEqual(main.user32.GetForegroundWindow(), foreground)
        self.assertEqual((self.note.winfo_x(), self.note.winfo_y(), self.note.winfo_width(),
                          self.note.winfo_height()), before)
        self.assertEqual(self.note.d, saved)
        self.assertFalse(self.note.attributes("-topmost"))
        main.user32.ShowWindow(self.note.hwnd, 6)
        self._wait_for_bottom_guard()
        self.assertFalse(main.user32.IsIconic(self.note.hwnd))

    def test_bottom_guard_does_not_undo_explicit_hide_or_show_hidden_startup_notes(self):
        self.note.set_z("bottom")
        for hide in (self.note.hide, self.app.hide_all):
            self.note.show()
            self.app.root.update()
            self.app._request_bottom_refresh()
            hide()
            self._wait_for_bottom_guard()
            self.assertFalse(self.note.d["visible"])
            self.assertEqual(self.note.state(), "withdrawn")
            self.assertFalse(main.user32.IsWindowVisible(self.note.hwnd))
        self.app._request_bottom_refresh()
        self._wait_for_bottom_guard()
        self.assertFalse(main.user32.IsWindowVisible(self.note.hwnd))
        self.app.show_all()
        self.app.root.update()
        self.assertTrue(main.user32.IsWindowVisible(self.note.hwnd))
        self.assertEqual(self.note.d["z"], "bottom")

    def test_bottom_guard_restores_native_hide_without_undoing_explicit_hide(self):
        self.note.set_z("bottom")
        self.app.root.update()
        self._external_show_window(self.note, 0)  # Shell can hide without minimizing.
        self._wait_for_bottom_guard()
        self.assertTrue(main.user32.IsWindowVisible(self.note.hwnd))
        self.assertTrue(self.note.d["visible"])
        self.note.hide()
        self.app._request_bottom_refresh()
        self._wait_for_bottom_guard()
        self.assertFalse(main.user32.IsWindowVisible(self.note.hwnd))

    def test_only_bottom_note_is_excluded_from_desktop_peek(self):
        values = []
        original = main.dwmapi.DwmSetWindowAttribute
        def set_attribute(hwnd, attribute, value, size):
            if hwnd == self.note.hwnd and attribute == 12:
                values.append(main.ctypes.cast(value, main.ctypes.POINTER(main.wintypes.BOOL)).contents.value)
            return original(hwnd, attribute, value, size)
        with patch.object(main.dwmapi, "DwmSetWindowAttribute", side_effect=set_attribute):
            for state in ("bottom", "normal", "bottom", "top"):
                self.note.set_z(state)
        self.assertEqual(values, [1, 0, 1, 0])

    def test_bottom_guard_moves_note_above_foreground_desktop_without_minimizing(self):
        self.note.set_z("bottom")
        desktop = main.tk.Toplevel(self.app.root)
        desktop.geometry("500x400+100+100")
        desktop.attributes("-topmost", True)
        desktop.focus_force()
        self.app.root.update()
        desktop_hwnd = main.user32.GetParent(desktop.winfo_id()) or desktop.winfo_id()
        original = main.user32.GetClassNameW
        def class_name(hwnd, buffer, length):
            if hwnd == desktop_hwnd:
                buffer.value = "Progman"
                return 7
            return original(hwnd, buffer, length)
        def order():
            result = []
            hwnd = main.user32.GetTopWindow(None)
            while hwnd:
                result.append(hwnd)
                hwnd = main.user32.GetWindow(hwnd, 2)
            return result
        self.assertGreater(order().index(self.note.hwnd), order().index(desktop_hwnd))
        foreground = main.user32.GetForegroundWindow()
        with patch.object(main.user32, "GetClassNameW", side_effect=class_name):
            self.app._refresh_bottom_notes()
            self.assertTrue(main.user32.GetWindowLongPtrW(self.note.hwnd, -20) & 0x8)
            self.note.set_z("normal")
            self.assertFalse(main.user32.GetWindowLongPtrW(self.note.hwnd, -20) & 0x8)
            self.note.set_z("bottom")
            self.app._refresh_bottom_notes()
        handles = order()
        self.assertLess(handles.index(self.note.hwnd), handles.index(desktop_hwnd))
        self.assertEqual(main.user32.GetForegroundWindow(), foreground)
        self.assertFalse(self.note.attributes("-topmost"))
        desktop.destroy()
        self.app.root.update()
        self.app._refresh_bottom_notes()
        self.assertFalse(main.user32.GetWindowLongPtrW(self.note.hwnd, -20) & 0x8)

    def test_bottom_guard_rechecks_layer_and_handles_multiple_notes_and_deletion(self):
        second = self.app.new_note()
        normal = self.app.new_note()
        self.note.set_z("bottom")
        second.set_z("bottom")
        self.app.root.update()
        for note in (self.note, second, normal):
            self._external_show_window(note, 6)
        self.note.set_z("normal")
        self._wait_for_bottom_guard()
        self.assertTrue(main.user32.IsIconic(self.note.hwnd))
        self.assertTrue(main.user32.IsIconic(normal.hwnd))
        self.assertFalse(main.user32.IsIconic(second.hwnd))
        self.app._request_bottom_refresh()
        self.app.notes.pop(second.d["id"])
        second.destroy()
        self._wait_for_bottom_guard()

    @unittest.skipUnless(main.os.environ.get("ZNOTE_TEST_DESKTOP") == "1",
                         "Set ZNOTE_TEST_DESKTOP=1 for interactive Windows shell checks")
    def test_bottom_note_survives_shell_show_desktop_and_minimize_all(self):
        import subprocess
        self.note.set_z("bottom")
        self.note.text.focus_force()
        self.app.root.update()

        def shell(command):
            process = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"(New-Object -ComObject Shell.Application).{command}()"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = main.time.monotonic() + 10
            def wait():
                if process.poll() is None and main.time.monotonic() < deadline:
                    self.app.root.after(30, wait)
                else:
                    self.app.root.after(450, self.app.root.quit)
            wait()
            self.app.root.mainloop()
            if process.poll() is None:
                process.kill()
            _, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, errors.decode(errors="replace"))

        def assert_on_desktop():
            self.assertTrue(self.note.d["visible"])
            self.assertTrue(main.user32.IsWindowVisible(self.note.hwnd))
            self.assertFalse(main.user32.IsIconic(self.note.hwnd))
            cloaked = main.wintypes.DWORD()
            get_cloaked = main.dwmapi.DwmGetWindowAttribute
            get_cloaked.argtypes = [main.wintypes.HWND, main.wintypes.DWORD,
                                   main.ctypes.c_void_p, main.wintypes.DWORD]
            self.assertEqual(get_cloaked(self.note.hwnd, 14, main.ctypes.byref(cloaked),
                                        main.ctypes.sizeof(cloaked)), 0)
            self.assertEqual(cloaked.value, 0)
            order = []
            desktop = []
            name = main.ctypes.create_unicode_buffer(256)
            hwnd = main.user32.GetTopWindow(None)
            while hwnd:
                order.append(hwnd)
                main.user32.GetClassNameW(hwnd, name, len(name))
                if name.value in ("Progman", "WorkerW") and main.user32.IsWindowVisible(hwnd):
                    desktop.append(hwnd)
                hwnd = main.user32.GetWindow(hwnd, 2)
            self.assertTrue(desktop, "An interactive Explorer desktop is required")
            self.assertLess(order.index(self.note.hwnd), min(order.index(h) for h in desktop))

        try:
            shell("ToggleDesktop")
            name = main.ctypes.create_unicode_buffer(256)
            main.user32.GetClassNameW(main.user32.GetForegroundWindow(), name, len(name))
            if name.value not in ("Progman", "WorkerW"):
                self.skipTest("Shell ToggleDesktop did not enter Show Desktop; use the actual taskbar button")
            assert_on_desktop()
        finally:
            shell("ToggleDesktop")
        assert_on_desktop()
        try:
            shell("MinimizeAll")
            assert_on_desktop()
        finally:
            shell("UndoMinimizeALL")
        assert_on_desktop()

    def test_bottom_guard_defers_restore_during_resize_and_respects_hide(self):
        self.note.set_z("bottom")
        self._begin_resize_session()
        main.user32.ShowWindow(self.note.hwnd, 6)
        self.app._refresh_bottom_notes()
        self.assertTrue(main.user32.IsIconic(self.note.hwnd))
        self.note._finish_resize(cancel=True)
        self._wait_for_bottom_guard()
        self.assertFalse(main.user32.IsIconic(self.note.hwnd))
        surface = self._begin_resize_session()
        self.app._request_bottom_refresh()
        self.note.hide()
        self._wait_for_bottom_guard()
        self.assertFalse(self.note.d["visible"])
        self.assertFalse(main.user32.IsWindowVisible(self.note.hwnd))
        self.assertIsNone(surface.hwnd)
        self.assertFalse(self.note._resizing)

    def test_cancel_bottom_raises_active_note_without_making_it_topmost(self):
        blocker = main.tk.Toplevel(self.app.root)
        blocker.geometry("300x240+30+30")
        self.app.root.update()
        blocker_hwnd = main.user32.GetParent(blocker.winfo_id()) or blocker.winfo_id()

        def order():
            handles = []
            callback = main.ctypes.WINFUNCTYPE(main.wintypes.BOOL, main.wintypes.HWND,
                                              main.wintypes.LPARAM)(
                lambda hwnd, _: handles.append(hwnd) or True)
            main.user32.EnumWindows(callback, 0)
            return handles

        for _ in range(2):
            self.note.set_z("bottom")
            self.note.text.focus_force()
            self.app.root.update()
            self.assertLess(order().index(blocker_hwnd), order().index(self.note.hwnd))
            self.note.b_bottom.event_generate("<Button-1>")
            self.app.root.update()
            self.assertEqual(self.note.d["z"], "normal")
            self.assertFalse(self.note.b_bottom._active)
            self.assertLess(order().index(self.note.hwnd), order().index(blocker_hwnd))
            self.assertFalse(main.user32.GetWindowLongPtrW(self.note.hwnd, -20) & 0x8)
            blocker.focus_force()
            self.app.root.update()
            self.assertLess(order().index(blocker_hwnd), order().index(self.note.hwnd))
            self.note.text.focus_force()
            self.app.root.update()
            self.assertLess(order().index(self.note.hwnd), order().index(blocker_hwnd))

    def test_bottom_button_sits_between_pin_and_title_without_overlap(self):
        for width in (300, 320, 543):
            self.note.geometry(f"{width}x340")
            self.app.root.update()
            pin, bottom, title = self.note.b_pin, self.note.b_bottom, self.note.title_box
            self.assertLessEqual(pin.winfo_x() + pin.winfo_width(), bottom.winfo_x())
            self.assertLessEqual(bottom.winfo_x() + bottom.winfo_width(), title.winfo_x())
            self.assertTrue(bottom.winfo_ismapped())
            right = title.winfo_x() + title.winfo_width()
            for button in self.note._tool_btns[2:]:
                self.assertTrue(button.winfo_ismapped())
                self.assertGreaterEqual(button.winfo_x(), right)

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
                    for z in ("top", "normal", "bottom") for visible in (True, False)]
                self.app.show_notes = show_on_startup
                with patch.object(main.NoteWindow, "show", autospec=True,
                                  side_effect=main.NoteWindow.show) as show, \
                        patch.object(main.user32, "GetForegroundWindow",
                                     return_value=self.app.root.winfo_id()):
                    self.app._restore_notes()
                    self.app.root.update()
                self.assertEqual(show.call_count, 6 if show_on_startup else 0)
                for note_id, note in self.app.notes.items():
                    z, was_visible = note_id.split("-")
                    visible = show_on_startup or was_visible == "True"
                    self.assertEqual(note.d["visible"], visible)
                    self.assertEqual(note.state(), "normal" if visible else "withdrawn")
                    self.assertEqual(note.d["z"], z)
                    self.assertEqual(bool(note.attributes("-topmost")), z == "top")
                    self.assertEqual(note._temporarily_raised, show_on_startup and z == "bottom")

    def test_first_startup_shows_welcome_note_by_default(self):
        self.note.destroy()
        self.app.notes.clear()
        self.app.data["notes"].clear()
        self.app.data.pop("welcomeVersion")
        self.app._restore_notes()
        self.note = next(iter(self.app.notes.values()))
        self.app.root.update()
        self.assertTrue(main.DEFAULT_SETTINGS["showOnStartup"])
        self.assertTrue(self.app.show_notes)
        self.assertTrue(self.note.d["visible"])
        self.assertEqual(self.note.state(), "normal")
        self.assertTrue(self.note.attributes("-topmost"))
        self.assertEqual(self.note.winfo_width(), 440)
        self.assertEqual(self.note.d["title"], "使用指南")
        self.assertEqual(self.app.data["welcomeVersion"], main.APP_VERSION)

    def test_upgrade_guide_is_visible_when_startup_notes_are_hidden(self):
        old_id = self.note.d["id"]
        self.note.d["visible"] = False
        self.note.destroy()
        self.app.notes.clear()
        self.app.show_notes = False
        self.app.data["welcomeVersion"] = "1.0.0"
        self.app._restore_notes()
        self.app.root.update()
        self.assertEqual(self.app.notes[old_id].state(), "withdrawn")
        guide = self.app.notes[self.app.data["notes"][-1]["id"]]
        self.assertEqual(guide.state(), "normal")
        self.assertTrue(guide.d["visible"])

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
        self.note.set_z("normal")
        other = subprocess.Popen([sys.executable, "-c", (
            "import tkinter as tk; root = tk.Tk(); "
            "root.geometry('300x240+30+30'); root.update(); "
            "print(root.winfo_id(), flush=True); root.mainloop()")],
            stdout=subprocess.PIPE, text=True)
        def close_other(process):
            process.terminate()
            process.wait(timeout=5)
            process.stdout.close()
        self.addCleanup(close_other, other)
        other_id = int(other.stdout.readline())
        other_hwnd = main.user32.GetParent(other_id) or other_id
        def raise_other(hwnd=other_hwnd):
            for layer in (-1, -2):  # HWND_TOPMOST / HWND_NOTOPMOST
                self.assertTrue(main.user32.SetWindowPos(
                    hwnd, layer, 0, 0, 0, 0, main.SWP_NOMOVE | main.SWP_NOSIZE | 0x10))
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
        self.assertEqual(self.note.d["z"], "normal")
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

        second = subprocess.Popen(other.args, stdout=subprocess.PIPE, text=True)
        self.addCleanup(close_other, second)
        second_id = int(second.stdout.readline())
        second_hwnd = main.user32.GetParent(second_id) or second_id
        raise_other(second_hwnd)
        self.app.root.update()
        order = window_order()
        self.assertLess(order.index(pinned.hwnd), order.index(second_hwnd))
        self.assertLess(order.index(second_hwnd), order.index(self.note.hwnd))
        self.assertLess(order.index(self.note.hwnd), order.index(other_hwnd))

        main.user32.ShowWindow(second_hwnd, 3)  # SW_MAXIMIZE
        self.app.root.after(300, self.app.root.quit)
        self.app.root.mainloop()
        order = window_order()
        self.assertLess(order.index(second_hwnd), order.index(self.note.hwnd))
        self.assertLess(order.index(self.note.hwnd), order.index(other_hwnd))
        self.assertLess(order.index(pinned.hwnd), order.index(second_hwnd))
        main.user32.ShowWindow(second_hwnd, 9)  # SW_RESTORE

        pinned.set_z("normal")
        self.app.root.update()
        self.assertFalse(main.user32.GetWindowLongPtrW(pinned.hwnd, -20) & 0x8)
        self.assertLess(window_order().index(self.note.hwnd), window_order().index(other_hwnd))
        self.app.show_all()
        self.app.root.update()
        for note in (self.note, pinned):
            self.assertLess(window_order().index(note.hwnd), window_order().index(second_hwnd))

    def test_minimizing_foreground_window_activates_normal_note_above_older_window(self):
        windows = [main.tk.Toplevel(self.app.root) for _ in range(2)]
        for window in windows:
            window.geometry("300x240+30+30")
        self.note.set_z("normal")
        self.app.root.update()
        older, foreground = windows
        older.focus_force()
        self.app.root.update()
        self.app.show_all()
        foreground.focus_force()
        self.app.root.update()
        hwnd = main.user32.GetParent(foreground.winfo_id()) or foreground.winfo_id()
        main.user32.GetForegroundWindow.restype = main.wintypes.HWND
        if main.user32.GetForegroundWindow() != hwnd:
            self.skipTest("Windows 未授予测试进程前台权限；需在交互式窗口中验证激活顺序")
        main.user32.PostMessageW(hwnd, 0x0112, 0xF030, 0)  # SC_MAXIMIZE
        self.app.root.after(200, self.app.root.quit)
        self.app.root.mainloop()
        main.user32.PostMessageW(hwnd, 0x0112, 0xF020, 0)  # SC_MINIMIZE
        self.app.root.after(300, self.app.root.quit)
        self.app.root.mainloop()
        self.assertTrue(main.user32.IsIconic(hwnd))
        self.assertEqual(main.user32.GetForegroundWindow(), self.note.hwnd)
        order = []
        callback = main.ctypes.WINFUNCTYPE(main.wintypes.BOOL, main.wintypes.HWND,
                                          main.wintypes.LPARAM)(lambda h, _: order.append(h) or True)
        main.user32.EnumWindows(callback, 0)
        older_hwnd = main.user32.GetParent(older.winfo_id()) or older.winfo_id()
        self.assertLess(order.index(self.note.hwnd), order.index(older_hwnd))

    def test_normal_note_show_and_unpin_keep_other_notes_pinned(self):
        pinned = self.app.new_note()
        pinned.set_z("top")
        normal = self.app.new_note()
        for _ in range(2):
            normal.show()
            self.note.set_z("normal")
            self.note.set_z("top")
            self.note.set_z("normal")
            self.app.hide_all()
            self.app.show_all()
            self.app.root.update()
            self.assertTrue(main.user32.GetWindowLongPtrW(pinned.hwnd, -20) & 0x8)
            for note in (normal, self.note):
                self.assertFalse(main.user32.GetWindowLongPtrW(note.hwnd, -20) & 0x8)

    def test_activating_pinned_note_does_not_raise_normal_note_with_it(self):
        self.note.set_z("normal")
        pinned = self.app.new_note()
        pinned.set_z("top")
        blocker = main.tk.Toplevel(self.app.root)
        blocker.geometry("300x240+30+30")
        self.app.root.update()
        blocker.lift()
        self.app.root.update()
        blocker_hwnd = main.user32.GetParent(blocker.winfo_id()) or blocker.winfo_id()

        def order():
            handles = []
            callback = main.ctypes.WINFUNCTYPE(main.wintypes.BOOL, main.wintypes.HWND,
                main.wintypes.LPARAM)(lambda h, _: handles.append(h) or True)
            main.user32.EnumWindows(callback, 0)
            return handles

        before = order()
        self.assertGreater(before.index(self.note.hwnd), before.index(blocker_hwnd))
        pinned.text.focus_force()
        self.app.root.update()
        after = order()
        self.assertGreater(after.index(self.note.hwnd), after.index(blocker_hwnd))
        self.assertLess(after.index(pinned.hwnd), after.index(blocker_hwnd))
        self.assertNotEqual(main.user32.GetWindowLongPtrW(self.note.hwnd, -8),
                            main.user32.GetWindowLongPtrW(pinned.hwnd, -8))

    def test_note_blank_regions_move_window_and_save_position(self):
        note = self.note
        w, h = note.winfo_width(), note.winfo_height()
        regions = [(note.cv, 12, h // 2), (note.cv, w - 12, h // 2),
                   (note.cv, w // 2, 10), (note.cv, w - 12, main.CORNER + 4),
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
        self.note.set_z("normal")
        other.hide()
        self.app.root.update()
        original = [(w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height(), w.d["z"])
                    for w in self.app.notes.values()]
        for action in ("show", "show", "hide", "hide", "show"):
            with self.subTest(action=action):
                self.app.q.put(action)
                self.app._pump()
                self.app.root.update()
                if self.app._note_motion._active:
                    settled = main.tk.BooleanVar(master=self.app.root)
                    self.app.root.after(200, settled.set, True)
                    self.app.root.wait_variable(settled)
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
        show, hide = menu.items[1:3]
        self.assertEqual(show.text, "显示所有便签  Ctrl+Alt+S")
        self.assertEqual(hide.text, "隐藏所有便签  Ctrl+Alt+H")
        show(None)
        hide(None)
        self.assertEqual(self.app.q.get_nowait(), "show")
        self.assertEqual(self.app.q.get_nowait(), "hide")

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
        self._begin_resize_session(x=438, y=220)
        self._resize_to_pointer(648, 400)
        self.assertEqual(self.note._resize_target[2], 470)
        self.note._finish_resize(cancel=True)

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

    def test_footer_padding_keeps_bottom_edge_resizable(self):
        note = self.note
        bottom = note.bar.winfo_height()-2
        note.bar.event_generate("<Motion>", x=note.winfo_width()//2, y=bottom)
        self.assertEqual(note.bar.cget("cursor"), "size_ns")
        self.assertEqual(note._edge_hit(note.winfo_width()//2, bottom, note.bar), (False, False, False, True))

    def _begin_resize_session(self, x=2, y=220, width=440, height=440):
        note = self.note
        note.geometry(f"{width}x{height}+180+180")
        self.app.root.update()
        self._resize_test_point = [180+x, 180+y]
        for item in (patch.object(main, "cursor_pos", side_effect=lambda: tuple(self._resize_test_point)),
                     patch.object(main.user32, "GetAsyncKeyState", return_value=-32768)):
            item.start()
            self.addCleanup(item.stop)
        event = type("Pointer", (), dict(x=x, y=y, x_root=180+x, y_root=180+y, widget=note.cv))()
        note._edge_press(event)
        self.assertTrue(note._resizing)
        return note._resize_surface

    def _resize_to_pointer(self, x, y):
        note = self.note
        self._resize_test_point[:] = (x, y)
        if note._resize_job is not None:
            note.after_cancel(note._resize_job)
            note._resize_job = None
        note._poll_resize()

    def test_eight_resize_directions_preserve_opposite_anchor_and_limits(self):
        rect = (180, 180, 440, 440)
        for edges in ((True, False, False, False), (False, True, False, False),
                      (False, False, True, False), (False, False, False, True),
                      (True, False, True, False), (False, True, True, False),
                      (True, False, False, True), (False, True, False, True)):
            l, r, t, b = edges
            for amount in (-45, 30, 1000):
                with self.subTest(edges=edges, amount=amount):
                    delta = (amount if l else -amount if r else 0,
                             amount if t else -amount if b else 0)
                    x, y, w, h = self.note._resize_rectangle(rect, delta, edges, (800, 700))
                    self.assertEqual(w, max(280, min(800, 440-amount)) if l or r else 440)
                    self.assertEqual(h, max(230, min(700, 440-amount)) if t or b else 440)
                    self.assertEqual(x+w if l else x, 620 if l else 180)
                    self.assertEqual(y+h if t else y, 620 if t else 180)

    def test_resize_commits_one_complete_surface_and_saves_its_actual_rectangle(self):
        note = self.note
        with patch.object(main.user32, "DefWindowProcW", wraps=main.user32.DefWindowProcW) as nonclient:
            self.assertTrue(note._z_proc(note.hwnd, 0x0086, 1, 0))
            nonclient.assert_called_once_with(note.hwnd, 0x0086, 0, -1)
        original = tuple(note.d[k] for k in ("x", "y", "w", "h"))
        surface = self._begin_resize_session()
        for hwnd in (note.hwnd, surface.hwnd):
            corner = main.ctypes.c_int()
            self.assertEqual(main.dwmapi.DwmGetWindowAttribute(hwnd, 33, main.ctypes.byref(corner),
                                                              main.ctypes.sizeof(corner)), 0)
            self.assertEqual(corner.value, 2)  # DWMWCP_ROUND
        source_size = (note.winfo_width(), note.winfo_height())
        with patch.object(surface, "present", wraps=surface.present) as present, \
                patch.object(main, "save_data") as save:
            self._resize_to_pointer(222, 400)
            rect = main.wintypes.RECT()
            main.user32.GetWindowRect(surface.hwnd, main.ctypes.byref(rect))
            self.assertEqual((rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top), (220, 180, 400, 440))
            self.assertEqual((note.winfo_width(), note.winfo_height()), source_size)
            self.assertEqual(tuple(note.d[k] for k in ("x", "y", "w", "h")), original)
            present.assert_called_once()
            save.assert_not_called()
        from resize_surface import _user32
        get_alpha = _user32.GetLayeredWindowAttributes
        get_alpha.argtypes = [main.wintypes.HWND, main.ctypes.c_void_p,
                             main.ctypes.POINTER(main.wintypes.BYTE), main.ctypes.c_void_p]

        def window_alpha(hwnd):
            alpha = main.wintypes.BYTE()
            self.assertTrue(get_alpha(hwnd, None, main.ctypes.byref(alpha), None))
            return alpha.value

        close, hide = surface.close, surface.hide

        def close_after_hidden_repaint():
            self.assertEqual(window_alpha(note.hwnd), 0)
            close()

        def hide_transparent_preview():
            self.assertEqual(window_alpha(surface.hwnd), 0)
            hide()

        with patch.object(surface, "close", side_effect=close_after_hidden_repaint) as handover, \
                patch.object(surface, "hide", side_effect=hide_transparent_preview) as hide_preview:
            note._edge_release(type("Pointer", (), dict(x_root=222, y_root=400))())
            handover.assert_called_once()
            hide_preview.assert_called_once()
        self.assertEqual(tuple(note.d[k] for k in ("x", "y", "w", "h")), (220, 180, 400, 440))
        self.assertEqual((note.winfo_x(), note.winfo_y(), note.winfo_width(), note.winfo_height()), (220, 180, 400, 440))
        self.assertIsNone(note._resize_surface)
        self.assertIsNone(note._resize_job)
        self.assertIsNone(surface.hwnd)
        self.assertIsNone(surface.bitmap)
        self.assertIsNone(surface.dc)

    def test_height_resize_preserves_footer_anchors_and_reuses_measurements(self):
        note = self.note
        self._begin_resize_session(x=220, y=2)
        controls = (note.status, note.add_pill, note.clear_btn)
        local = [(widget.winfo_x(), widget.winfo_y()) for widget in controls]
        initial = [180 + widget.winfo_rooty()-note.winfo_rooty() for widget in controls]
        height, rows = note._document_height, dict(note._row_layout)
        with patch.object(note, "_draw_pill", wraps=note._draw_pill) as pill, \
                patch.object(note.text, "count", wraps=note.text.count) as count:
            self._resize_to_pointer(400, 222)
            self.assertEqual(note._resize_target, (180, 220, 440, 400))
            self.assertEqual(note._document_height, height)
            self.assertEqual(note._row_layout, rows)
            for widget, xy, y in zip(controls, local, initial):
                self.assertIs(widget.master, note.bar)
                self.assertEqual((widget.winfo_x(), widget.winfo_y()), xy)
                self.assertEqual(note._resize_target[1]+widget.winfo_rooty()-note.winfo_rooty(), y)
            pill.assert_not_called()
            count.assert_not_called()
        note._finish_resize(cancel=True)

    def test_horizontal_resize_toolbar_matches_clean_repaint_without_old_icon_pixels(self):
        from PIL import Image
        note = self.note
        for edge, points in ((438, (578, 648, 500, 618, 578)),
                             (2, (222, 152, 300, 182, 222))):
            surface = self._begin_resize_session(x=edge, y=220)
            try:
                for x in points:
                    with self.subTest(edge=edge, x=x):
                        self._resize_to_pointer(x, 400)
                        width = note._resize_target[2]
                        height = note.toolbar.winfo_y()+note.toolbar.winfo_height()
                        def toolbar_frame():
                            pixels = main.ctypes.string_at(surface.bits,
                                surface.capacity[0]*surface.capacity[1]*4)
                            return Image.frombytes("RGB", surface.capacity, pixels, "raw", "BGRX").crop(
                                (0, 0, width, height))
                        frame = toolbar_frame()
                        # 与清除 Tk 裁剪缓存并完整重绘后的同尺寸画面对照。
                        note._repair_resize_frame()
                        surface.present(note._resize_target)
                        self.assertIsNone(ImageChops.difference(frame, toolbar_frame()).getbbox())
            finally:
                note._finish_resize(cancel=True)

    def test_resize_footer_and_todos_match_clean_repaint_in_all_directions(self):
        self._assert_resize_matches_clean_repaint()

    def test_split_resize_footer_and_todos_match_clean_repaint_in_all_directions(self):
        self.note.toggle_columns()
        self._assert_resize_matches_clean_repaint()

    def _assert_resize_matches_clean_repaint(self):
        from PIL import Image
        note = self.note
        note.d["lines"] = [dict(type="todo", text=text, checked=i % 2 == 0)
                           for i, text in enumerate(("欢迎语", "软件内更新", "便签收纳功能",
                               "长待办换行后勾选框不能留下旧位置" * 2, "最后一项"))]
        note.render()
        if len(note.editors) > 1:
            note.editors[1].lines = [dict(line) for line in note.d["lines"]]
            note.editors[1].render()
        width = 640 if len(note.editors) > 1 else 440
        center, right = width // 2, width - 2
        for edge in ((center, 2), (center, 438), (2, 220), (right, 220),
                     (2, 2), (right, 2), (2, 438), (right, 438)):
            surface = self._begin_resize_session(x=edge[0], y=edge[1], width=width)
            try:
                for amount in (40, -30, -260, 100, 0):
                    with self.subTest(edge=edge, amount=amount):
                        with patch.object(surface, "present", wraps=surface.present) as present:
                            self._resize_to_pointer(180+edge[0]+(amount if edge[0] != center else 0),
                                                    180+edge[1]+(amount if edge[1] != 220 else 0))
                        fades = present.call_args.args[1]
                        def frame():
                            pixels = main.ctypes.string_at(surface.bits,
                                surface.capacity[0]*surface.capacity[1]*4)
                            return Image.frombytes("RGB", surface.capacity, pixels, "raw", "BGRX").crop(
                                (8, 8, note._resize_target[2]-8, note._resize_target[3]-8))
                        actual = frame()
                        note._repair_resize_frame()
                        surface.present(note._resize_target, fades)
                        self.assertIsNone(ImageChops.difference(actual, frame()).getbbox())
            finally:
                note._finish_resize(cancel=True)

    def test_width_resize_reflows_todos_and_preserves_toolbar_and_title_editor(self):
        note = self.note
        note.d["lines"] = [{"type": "todo", "text": "长待办实时换行验证" * 8, "checked": True}]
        note.render()
        note.edit_title()
        self.app.root.update()
        editor, title = note._title_editor, note._title_editor.get()
        self._begin_resize_session(x=438, y=220)
        height = note._document_height
        anchors = [(b.winfo_x(), b.winfo_y()) for b in (note.b_pin, note.b_bottom)]
        self._resize_to_pointer(458, 400)
        self.assertEqual(note._resize_target[2], 280)
        self.assertGreater(note._document_height, height)
        self.assertEqual(anchors, [(b.winfo_x(), b.winfo_y()) for b in (note.b_pin, note.b_bottom)])
        self.assertIs(note._title_editor, editor)
        self.assertEqual(editor.get(), title)
        box = note._checkbox_widgets[0]
        row_height, offset = note._row_layout[1]
        self.assertEqual(box.winfo_y(), int(note.text.cget("pady"))+offset+row_height//2-box.winfo_height()//2)
        self.assertGreater(len(note._strike_widgets), 1)
        note._finish_resize(cancel=True)

    def test_unchanged_rectangle_skips_frame_and_latest_pointer_wins(self):
        note = self.note
        surface = self._begin_resize_session()
        self._resize_to_pointer(202, 400)
        with patch.object(surface, "present", wraps=surface.present) as present:
            self._resize_to_pointer(202, 400)
            present.assert_not_called()
            self._resize_test_point[:] = (212, 400)
            self._resize_test_point[:] = (222, 400)
            self._resize_to_pointer(*self._resize_test_point)
            present.assert_called_once()
            self.assertEqual(note._resize_target, (220, 180, 400, 440))
        note._finish_resize(cancel=True)

    def test_release_coordinates_win_over_cursor_moves_after_mouse_up(self):
        note = self.note
        self._begin_resize_session(x=438, y=220)
        self._resize_test_point[:] = (2000, 1000)
        note._edge_release(type("Pointer", (), dict(x_root=648, y_root=400))())
        self.assertEqual((note.d["x"], note.d["w"]), (180, 470))
        self.assertFalse(note._resizing)

    def test_release_during_frame_layout_commits_last_position(self):
        note = self.note
        self._begin_resize_session()
        update, injected = note.update_idletasks, False
        def release_inside_layout():
            nonlocal injected
            if not injected:
                injected = True
                note._edge_release(type("Pointer", (), dict(x_root=222, y_root=400))())
            return update()
        with patch.object(note, "update_idletasks", side_effect=release_inside_layout):
            self._resize_to_pointer(202, 400)
        self.assertFalse(note._resizing)
        self.assertEqual((note.d["x"], note.d["w"]), (220, 400))
        self.assertIsNone(note._resize_job)

    def test_native_release_position_is_not_replaced_by_a_later_tk_event(self):
        note = self.note
        self._begin_resize_session(x=438, y=220)
        note._resize_release_point = (648, 400)
        note._edge_release(type("Pointer", (), dict(x_root=2000, y_root=1000))())
        self.assertEqual((note.d["x"], note.d["w"]), (180, 470))

    def test_resize_can_grow_past_its_initial_source_allocation(self):
        note = self.note
        surface = self._begin_resize_session(x=438, y=438)
        handle, capacity = surface.hwnd, surface.capacity
        self._resize_to_pointer(918, 878)
        self.assertGreater(surface.capacity[0], capacity[0])
        self.assertGreater(surface.capacity[1], capacity[1])
        self.assertEqual(surface.hwnd, handle)
        self.assertEqual(note._resize_target, (180, 180, 740, 700))
        self.assertEqual(note.bar.winfo_y()+note.bar.winfo_height(), 699)
        note._finish_resize()
        self.assertEqual((note.winfo_width(), note.winfo_height()), (740, 700))

    def test_resize_mouse_coalescing_keeps_last_down_point_and_does_not_run_tk(self):
        note = self.note
        self._begin_resize_session()
        pending = [(1, 350, 410), (0, 1500, 1000)]
        def peek(pointer, hwnd, first, last, flags):
            self.assertIsNone(hwnd)
            self.assertEqual((first, last, flags), (0x0200, 0x0200, 1))
            if not pending:
                return False
            buttons, x, y = pending.pop(0)
            message = main.ctypes.cast(pointer, main.ctypes.POINTER(main.wintypes.MSG)).contents
            message.wParam = buttons
            message.pt.x, message.pt.y = x, y
            return True
        with patch.object(main.user32, "PeekMessageW", side_effect=peek), \
                patch.object(main.user32, "CallWindowProcW") as old, \
                patch.object(note, "_relayout", side_effect=AssertionError("Tk in native callback")):
            self.assertEqual(note._ime_proc(note._resize_client_hwnd, 0x0200, 1, 0), 0)
            self.assertEqual(note._resize_pointer, (350, 410))
            old.assert_not_called()
        note._finish_resize(cancel=True)

    def test_resize_captures_only_its_own_client_and_fades_cannot_cover_footer(self):
        import resize_surface
        note = self.note
        note.d["lines"] = [{"type": "todo", "text": f"待办 {i}", "checked": False} for i in range(40)]
        note.render()
        surface = self._begin_resize_session(x=220, y=2)
        with patch.object(resize_surface._user32, "GetWindowDC", wraps=resize_surface._user32.GetWindowDC) as dc, \
                patch.object(surface, "present", wraps=surface.present) as present:
            self._resize_to_pointer(400, 222)
            self.assertTrue(dc.call_args_list)
            for call in dc.call_args_list:
                self.assertEqual(call.args, (note._resize_client_hwnd,))
            regions = present.call_args.args[1]
            self.assertTrue(regions)
            for x, y, width, height, bottom in regions:
                self.assertGreaterEqual(y, note.body.winfo_y())
                self.assertLessEqual(y+height, note.bar.winfo_y())
            for hwnd in note._edge_fade.windows:
                self.assertFalse(main.user32.GetWindowLongPtrW(hwnd, -16) & 0x10000000)
        note._finish_resize(cancel=True)

    def test_opacity_changes_do_not_reveal_the_large_source_window(self):
        import resize_surface
        note = self.note
        self._begin_resize_session()
        self.app.set_opacity(70)
        self.assertEqual(note.d["opacity"], .7)
        key, alpha, flags = main.wintypes.DWORD(), main.wintypes.BYTE(), main.wintypes.DWORD()
        get_alpha = resize_surface._user32.GetLayeredWindowAttributes
        get_alpha.argtypes = [main.wintypes.HWND, main.ctypes.POINTER(main.wintypes.DWORD),
                             main.ctypes.POINTER(main.wintypes.BYTE), main.ctypes.POINTER(main.wintypes.DWORD)]
        self.assertTrue(get_alpha(note.hwnd, main.ctypes.byref(key), main.ctypes.byref(alpha), main.ctypes.byref(flags)))
        self.assertEqual(alpha.value, 0)
        note._finish_resize(cancel=True)
        self.assertAlmostEqual(note.attributes("-alpha"), .7, places=2)
        self.assertTrue(get_alpha(note.hwnd, main.ctypes.byref(key), main.ctypes.byref(alpha), main.ctypes.byref(flags)))
        self.assertEqual(alpha.value, round(255*.7))

    def test_hidden_controls_and_input_bindings_survive_resize_cancel(self):
        note = self.note
        hidden = main.tk.Label(note.bar, text="hidden")
        hidden.place(x=0, y=0)
        self.app.root.update()
        hidden.place_forget()
        tags = note.text.bindtags()
        self._begin_resize_session()
        self.assertNotEqual(note.text.bindtags(), tags)
        self._resize_to_pointer(222, 400)
        note._finish_resize(cancel=True)
        self.assertEqual(note.text.bindtags(), tags)
        self.assertFalse(main.user32.GetWindowLongPtrW(hidden.winfo_id(), -16) & 0x10000000)
        self.assertEqual((note.winfo_x(), note.winfo_y(), note.winfo_width(), note.winfo_height()), (180, 180, 440, 440))

    def test_resize_surface_preserves_bottom_note_order_below_normal_windows(self):
        note = self.note
        note.set_z("bottom")
        normal = self.app.new_note()
        normal.set_z("normal")
        self.app.root.update()
        surface = self._begin_resize_session()
        self._resize_to_pointer(222, 400)
        handles = []
        @main.ctypes.WINFUNCTYPE(main.wintypes.BOOL, main.wintypes.HWND, main.wintypes.LPARAM)
        def collect(hwnd, _):
            handles.append(hwnd)
            return True
        main.user32.EnumWindows(collect, 0)
        self.assertLess(handles.index(normal.hwnd), handles.index(surface.hwnd))
        self.assertLess(handles.index(surface.hwnd), handles.index(note.hwnd))
        note._finish_resize(cancel=True)

    def test_hiding_during_resize_releases_capture_and_restores_saved_size(self):
        note = self.note
        surface = self._begin_resize_session()
        self._resize_to_pointer(222, 400)
        note.hide()
        self.assertFalse(note._resizing)
        self.assertIsNone(note._resize_surface)
        self.assertIsNone(note._resize_job)
        self.assertIsNone(surface.hwnd)
        note.show()
        self.app.root.update()
        self.assertEqual((note.winfo_x(), note.winfo_y(), note.winfo_width(), note.winfo_height()), (180, 180, 440, 440))
        self.assertIsNot(note.cv.grab_current(), note.cv)

    def test_destroy_during_frame_layout_does_not_leave_callbacks_or_native_buffers(self):
        note = self.note
        surface = self._begin_resize_session()
        update, injected = note.update_idletasks, False
        def destroy_inside_layout():
            nonlocal injected
            if not injected:
                injected = True
                note.destroy()
            return update()
        with patch.object(note, "update_idletasks", side_effect=destroy_inside_layout):
            self._resize_to_pointer(202, 400)
        self.assertFalse(note.winfo_exists())
        self.assertIsNone(note._resize_job)
        self.assertIsNone(surface.hwnd)
        self.assertIsNone(surface.bitmap)
        self.assertIsNone(surface.dc)

    def test_failed_native_buffer_upload_restores_window_and_releases_resources(self):
        import resize_surface
        note = self.note
        note.geometry("440x440+180+180")
        self.app.root.update()
        with patch.object(resize_surface._user32, "UpdateLayeredWindow", return_value=False), \
                patch.object(resize_surface._user32, "DestroyWindow", wraps=resize_surface._user32.DestroyWindow) as destroy, \
                patch.object(resize_surface._gdi32, "DeleteObject", wraps=resize_surface._gdi32.DeleteObject) as bitmap, \
                patch.object(resize_surface._gdi32, "DeleteDC", wraps=resize_surface._gdi32.DeleteDC) as dc:
            event = type("Pointer", (), dict(x=2, y=220, x_root=182, y_root=400, widget=note.cv))()
            with self.assertRaises(OSError):
                note._edge_press(event)
            destroy.assert_called_once()
            bitmap.assert_called_once()
            dc.assert_called_once()
        self.assertFalse(note._resizing)
        self.assertIsNone(note._resize_surface)
        self.assertIsNone(note._resize_job)
        self.assertFalse(main.user32.GetWindowLongPtrW(note.hwnd, -20) & 0x00080000)
        self.assertEqual((note.winfo_width(), note.winfo_height()), (440, 440))

    def test_measurement_invalidation_during_layout_waits_for_next_pass(self):
        note = self.note
        height, rows = note._document_height, dict(note._row_layout)
        note._layout_busy = True
        note._queue_strikes()
        self.assertEqual(note._document_height, height)
        self.assertEqual(note._row_layout, rows)
        note._layout_busy = False
        note._draw_strikes()
        self.app.root.update()
        self.assertGreater(note._document_height, 0)
        self.assertFalse(note._layout_pending)

    def test_stray_motion_and_release_after_resize_do_not_drag_or_overwrite_position(self):
        note = self.note
        with patch.object(note, "geometry") as geometry, patch.object(self.app, "schedule_save") as save:
            event = type("Pointer", (), dict(x_root=400, y_root=300))()
            self.assertEqual(note._edge_move(event), "break")
            self.assertEqual(note._edge_release(event), "break")
            geometry.assert_not_called()
            save.assert_not_called()

    def test_button_padding_protects_artwork_without_moving_visible_image(self):
        note = self.note
        note.geometry("440x440")
        self.app.root.update()
        pill = note.add_pill
        self.assertEqual(pill.winfo_width(), note._pill_image.width()+2*int(pill.cget("padx")))
        self.assertEqual(pill.winfo_height(), note._pill_image.height()+2*int(pill.cget("pady")))
        self.assertEqual(pill.winfo_x()+int(pill.cget("padx")), (440-note._pill_image.width())//2)
        self.assertEqual(pill.winfo_y()+int(pill.cget("pady")), 8)

    def test_typing_keeps_native_text_window_visible_during_layout(self):
        self.note.d["lines"] = [{"type": "text", "text": "", "checked": False}]
        self.note.render()
        self.app.root.update()
        layout = self.note.editors[0]._layout_text
        visible = []

        def record_layout():
            visible.append(bool(main.user32.GetWindowLongPtrW(
                self.text.winfo_id(), -16) & 0x10000000))  # WS_VISIBLE
            layout()

        with patch.object(self.note.editors[0], "_layout_text", side_effect=record_layout):
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

    def test_multiple_todos_copy_and_cut_between_notes_preserve_structure(self):
        from copy import deepcopy
        source = self.note
        original = [
            {"type": "todo", "text": "买牛奶😀", "checked": False},
            {"type": "text", "text": "普通说明", "checked": False},
            {"type": "todo", "text": "已处理", "checked": True},
            {"type": "todo", "text": "", "checked": False}]
        target = self.app.new_note()
        try:
            clipboard = self.text.clipboard_get()
        except main.tk.TclError:
            clipboard = None
        try:
            for command in ("<<Copy>>", "<<Cut>>"):
                for start in ("1.0", "1.3"):
                    with self.subTest(command=command, start=start):
                        source.d["lines"] = deepcopy(original)
                        source.render()
                        source.text.tag_add("sel", start, "end-1c")
                        source.text.event_generate(command)
                        self.assertEqual(source.text.clipboard_get(),
                                         "买牛奶😀\n普通说明\n已处理\n")
                        target.d["lines"] = [{"type": "todo", "text": "旧内容", "checked": True}]
                        target.render()
                        before = deepcopy(target.d["lines"])
                        target.text.tag_add("sel", "1.0", "end-1c")
                        target.text.event_generate("<<Paste>>")
                        self.assertEqual(target.d["lines"], original)
                        self.assertEqual(len(target.text.dump("1.0", "end", image=True)), 3)
                        target.text.event_generate("<<Undo>>")
                        self.assertEqual(target.d["lines"], before)
                        target.text.event_generate("<<Redo>>")
                        self.assertEqual(target.d["lines"], original)
                        if command == "<<Cut>>":
                            source.text.event_generate("<<Undo>>")
                        self.assertEqual(source.d["lines"], original)
        finally:
            self.text.clipboard_clear()
            if clipboard is not None:
                self.text.clipboard_append(clipboard)

    def test_partial_todo_copy_stays_plain_and_structured_paste_keeps_surrounding_text(self):
        note, text = self.note, self.text
        try:
            clipboard = text.clipboard_get()
        except main.tk.TclError:
            clipboard = None
        try:
            note.d["lines"] = [{"type": "todo", "text": "abc", "checked": True}]
            note.render()
            text.tag_add("sel", "1.4", "1.end")
            text.event_generate("<<Copy>>")
            self.assertEqual(text.clipboard_get(), "bc")
            text.tag_remove("sel", "1.0", "end")
            source = self.app.new_note()
            source.d["lines"] = [
                {"type": "todo", "text": "第一项", "checked": False},
                {"type": "todo", "text": "第二项😀", "checked": True}]
            source.render()
            source.text.tag_add("sel", "1.0", "end-1c")
            source.text.event_generate("<<Copy>>")
            for kind in ("text", "todo"):
                with self.subTest(kind=kind):
                    note.d["lines"] = [{"type": kind, "text": "前😀后", "checked": False}]
                    note.render()
                    text.mark_set("insert", f"1.{note._prefix_len(note.d['lines'][0]) + 3}")
                    text.event_generate("<<Paste>>")
                    self.assertEqual(note.d["lines"], [
                        {"type": kind, "text": "前😀", "checked": False},
                        {"type": "todo", "text": "第一项", "checked": False},
                        {"type": "todo", "text": "第二项😀后", "checked": True}])
                    self.assertEqual(text.get("insert", "3.end"), "后")
            # 外部重新复制同样的文字，剪贴板序号已变，不能复用内部待办结构。
            plain = text.clipboard_get()
            text.clipboard_clear()
            text.clipboard_append(plain)
            note.d["lines"] = [{"type": "text", "text": "", "checked": False}]
            note.render()
            text.mark_set("insert", "1.0")
            with patch.object(main.user32, "GetClipboardSequenceNumber",
                              return_value=self.app._clipboard_lines[0] + 1):
                text.event_generate("<<Paste>>")
            self.assertEqual(note.d["lines"], [
                {"type": "text", "text": "第一项", "checked": False},
                {"type": "text", "text": "第二项😀", "checked": False}])
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

    def test_wrapped_preedit_preserves_todo_indentation(self):
        note, text = self.note, self.text
        note.geometry("340x500")
        for kind, original, checked in (("todo", "abc", False), ("todo", "", False),
                                        ("todo", "abc", True), ("text", "abc", False)):
            with self.subTest(kind=kind, original=original, checked=checked):
                note.d["lines"] = [{"type": kind, "text": original, "checked": checked}]
                note.render()
                text.mark_set("insert", "1.end")
                self.app.root.update()
                body_start = "1.3" if kind == "todo" else "1.0"
                left = text.bbox(body_start)[0]
                note._begin_preedit(text)
                for preedit in ("da'shi'da'shi'de" * 4, "pinyin" * 14):
                    note._replace_preedit(preedit)
                    self.app.root.update()
                    index = text.index(f"{body_start} +1 display lines display linestart")
                    self.assertLess(int(index.split(".")[1]), int(text.index("1.end").split(".")[1]))
                    self.assertGreaterEqual(text.bbox(index)[0], left)
                    self.assertEqual("todo_indent" in text.tag_names(index), kind == "todo")
                    self.assertEqual(note.d["lines"][0]["text"], original)
                note._finish_preedit()
                self.app.root.update()
                self.assertEqual(text.get(body_start, "1.end"), original)
                note._begin_preedit(text)
                note._replace_preedit("pinyin" * 14)
                note._finish_preedit("\u786e\u8ba4")
                self.app.root.update()
                self.assertEqual(note.d["lines"][0],
                                 {"type": kind, "text": original + "\u786e\u8ba4", "checked": checked})

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
                self.assertFalse(style & 0x80)  # WS_EX_TOOLWINDOW 会导致便签被跳过。
                self.assertFalse(style & 0x40000)  # WS_EX_APPWINDOW
                owner = main.user32.GetWindowLongPtrW(hwnd, -8)  # GWLP_HWNDPARENT
                self.assertEqual(owner, note._owner_hwnd)
                self.assertTrue(main.user32.IsWindow(owner))
                self.assertFalse(main.user32.IsWindowVisible(owner))
                note.hide()
                note.show()
                self.app.root.update()
        hwnd = main.user32.GetParent(settings.winfo_id()) or settings.winfo_id()
        style = main.user32.GetWindowLongPtrW(hwnd, -20)
        self.assertTrue(style & 0x40000)
        self.assertFalse(style & 0x80)
        settings.destroy()

    def test_destroying_note_releases_its_hidden_owner(self):
        note = self.app.new_note()
        owner = note._owner_hwnd
        self.assertTrue(main.user32.IsWindow(owner))
        note.destroy()
        self.assertFalse(main.user32.IsWindow(owner))

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
        settings._drag_press(type("Event", (), {"widget": settings, "x_root": x + 40, "y_root": y + 20})())
        settings._drag_move(type("Event", (), {"x_root": x + 55, "y_root": y + 30})())
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
            px, py = (settings.winfo_width()//2, 35) if widget is settings else (5, 5)
            widget.event_generate("<Button-1>", x=px, y=py)
            widget.event_generate("<B1-Motion>", x=px+30, y=py+20)
            self.app.root.update()
            self.assertEqual((settings.winfo_x(), settings.winfo_y()), (x + 30, y + 20))
            widget.event_generate("<ButtonRelease-1>", x=px+30, y=py+20)
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

    def test_settings_lists_show_before_hide_and_reports_editor_hotkey_conflicts(self):
        settings = main.SettingsWindow(self.app)
        self.app.root.update()
        self.assertLess(settings.hotkey_entries["show"].winfo_rooty(),
                        settings.hotkey_entries["hide"].winfo_rooty())
        before = settings.hotkey_entries["show"].get()
        with patch.object(main.user32, "RegisterHotKey") as register:
            settings._capture_hotkey(type("Key", (), dict(keysym="c", keycode=67, state=4))(), "show")
            self.assertIn("复制", settings._hotkey_hint.cget("text"))
            self.assertIn("冲突", settings._hotkey_hint.cget("text"))
            self.assertEqual(settings.hotkey_entries["show"].get(), before)
            settings._capture_hotkey(type("Key", (), dict(keysym="Insert", keycode=45, state=1))(), "show")
            self.assertIn("粘贴", settings._hotkey_hint.cget("text"))
            self.assertIn("冲突", settings._hotkey_hint.cget("text"))
            self.assertEqual(settings.hotkey_entries["show"].get(), before)
            register.assert_not_called()
        settings.destroy()

    def test_all_hotkey_actions_protect_editor_shortcuts_without_changing_bindings(self):
        with patch.object(main.user32, "RegisterHotKey", return_value=True), \
                patch.object(main.user32, "UnregisterHotKey"):
            self._start_hotkeys_impl(self.app)
        before = self.app.data["settings"].copy()
        active, identities = self.app._active_hotkeys.copy(), self.app._hotkey_ids.copy()
        with patch.object(main.user32, "RegisterHotKey") as register, \
                patch.object(main.user32, "UnregisterHotKey") as unregister, \
                patch.object(self.app, "save_now") as save:
            for action in main.HOTKEY_LABELS:
                for value in ("Ctrl+A", "Ctrl+C", "Ctrl+X", "Ctrl+V", "Ctrl+Z", "Ctrl+Y",
                              "Ctrl+Shift+Z", "Ctrl+Insert", "Shift+Insert", "Shift+Delete",
                              "Ctrl+Left", "Ctrl+Shift+Home", "Ctrl+Backspace", "Ctrl+Tab"):
                    with self.subTest(action=action, hotkey=value):
                        self.assertIn("冲突", self.app.update_hotkey(action, value))
            register.assert_not_called()
            unregister.assert_not_called()
            save.assert_not_called()
        self.assertEqual(self.app.data["settings"], before)
        self.assertEqual(self.app._active_hotkeys, active)
        self.assertEqual(self.app._hotkey_ids, identities)

    def test_hotkey_modifier_rules_keep_ctrl_alt_and_function_key_combinations(self):
        for value in ("Ctrl+Shift+K", "Alt+F9", "Shift+F9", "F8"):
            with self.subTest(hotkey=value):
                self.assertIsNone(self.app.update_hotkey("convert", value))
                self.assertEqual(self.app.hotkey_text("convert"), value)
        for value in ("Shift+A", "A"):
            with self.subTest(hotkey=value):
                self.assertIn("Ctrl / Alt", self.app.update_hotkey("convert", value))
                self.assertEqual(self.app.hotkey_text("convert"), "F8")

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
        with patch.object(main, "cursor_pos", return_value=(220, 260)):
            self.app._hotkey_proc(self.app._hotkey_hwnd, main.WM_HOTKEY, 101, 0)
        self.assertEqual(self.app.q.get_nowait(), ("new_at_cursor", (220, 260)))
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
                                         showHotkey="Ctrl+Shift+S", convertHotkey="Ctrl+Shift+K")
        self.app._hotkey_hwnd = self.app.root.winfo_id()
        self.app._hotkey_ids = {"new": 1, "hide": 2, "show": 3}
        self.app._active_hotkeys = {"new": "Ctrl+Alt+H", "hide": "Ctrl+Alt+N", "show": "Ctrl+Shift+S"}
        with patch.object(main.user32, "RegisterHotKey", side_effect=[True, False, True, True, True]), \
             patch.object(main.user32, "UnregisterHotKey"), patch.object(main, "save_data") as save:
            self.assertIn("占用", self.app.reset_hotkeys())
            self.assertEqual(self.app.hotkey_text("new"), "Ctrl+Alt+H")
            self.assertEqual(self.app.hotkey_text("convert"), "Ctrl+Shift+K")
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
            self.assertEqual(settings.hotkey_entries["convert"].get(), "Ctrl+Shift+L")
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
                with self.subTest(scale=scale, screen=(screen_w, screen_h)), patch.object(
                        main.SettingsWindow, "_work_area", return_value=main.wintypes.RECT(0, 0, screen_w, screen_h)), patch.object(
                        main, "DATA_FILE", "C:/" + "很长的用户目录/" * 20 + "notes.json"):
                    self.app.root.tk.call("tk", "scaling", scale * 96 / 72)
                    settings = main.SettingsWindow(self.app)
                    try:
                        self.app.root.update()
                        ui_scale = max(1, round(settings.winfo_fpixels("1i"))/96)
                        self.assertGreaterEqual(settings.winfo_width(),
                            min(round(480*ui_scale), screen_w-2*round(16*ui_scale)))
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
                self.assertEqual(app.data["schemaVersion"], 3)
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

    def test_settings_and_history_stay_transparent_until_layout(self):
        for action in ("settings", "history"):
            with self.subTest(action=action):
                self.app.q.put(action)
                self.app._pump()
                win = getattr(self.app, f"{action}_win")
                self.app.root.update_idletasks()
                self.assertEqual(win.attributes("-alpha"), 0.0)
                self.app.root.update()
                content = win._close_button if action == "settings" else win.content.winfo_children()[0]
                self.assertTrue(content.winfo_ismapped())
                self.assertEqual(win.attributes("-alpha"), 1.0)
                hwnd = main.user32.GetParent(win.winfo_id()) or win.winfo_id()
                self.assertFalse(main.user32.GetWindowLongPtrW(hwnd, -20) & 0x00080000)
                win.destroy()

    def test_history_reopen_reuses_preview_and_refreshes_changed_note_name(self):
        note_id = self.note.d["id"]
        self.app.data["archives"] = [{"noteId": note_id, "clearedAt": "2026-11-05T09:00:00", "items": []}]
        self.app.q.put("history")
        self.app._pump()
        win = self.app.history_win
        self.app.root.update()
        preview = win.content.winfo_children()[0]
        win._minimize_button.event_generate("<Button-1>")
        hwnd = main.user32.GetParent(win.winfo_id()) or win.winfo_id()
        self.assertTrue(main.user32.IsIconic(hwnd))
        self.app.q.put("history")
        self.app._pump()
        self.app.root.update()
        self.assertIs(win.content.winfo_children()[0], preview)
        self.assertTrue(preview.winfo_ismapped())
        self.note.d["title"] = "修改后的标题"
        self.app.q.put("history")
        self.app._pump()
        self.assertEqual(win.names, ["修改后的标题"])

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

    def test_history_batch_toggle_reuses_rows_and_preview_in_both_tabs(self):
        self.note.toggle_done(1)
        self.note.clear_done()
        archive = self.app.data["archives"][0]
        self.app.data["archives"] = [
            dict(archive, id=f"archive-{i}", noteId=f"note-{i}", title=f"便签 {i}",
                 items=archive["items"] * 30)
            for i in range(40)
        ]
        self.app.data["deletedNotes"] = [
            dict(self.note.d, id=f"note-{i}", title=f"便签 {i}", lines=self.note.d["lines"] * 30)
            for i in range(40)
        ]
        win = main.HistoryWindow(self.app)
        for mode in ("archives", "deletedNotes"):
            with self.subTest(mode=mode):
                win.switch(mode)
                win.choose(win.ids[20])
                self.app.root.update()
                panes = (win.left.viewport, win.right.viewport)
                for pane in panes:
                    pane.yview_moveto(.4)
                self.app.root.update()
                scroll = tuple(pane.canvasy(0) for pane in panes)
                self.assertTrue(all(offset > 0 for offset in scroll))
                rows = win.rows.copy()
                preview = win.content.winfo_children()
                selected = win.selected_id
                box = win._check_boxes[selected]
                for _ in range(2):
                    win.batch_button.invoke()
                    self.app.root.update()
                    self.assertTrue(box.winfo_manager())
                    self.assertEqual(box.cget("selectcolor"), rows[selected].cget("bg"))
                    box.invoke()
                    self.assertEqual(win.checked, {selected})
                    self.assertEqual(tuple(pane.canvasy(0) for pane in panes), scroll)
                    win.batch_button.invoke()
                    self.app.root.update()
                    self.assertFalse(box.winfo_manager())
                    self.assertFalse(win.checked)
                    self.assertFalse(win._check_vars[selected].get())
                    self.assertEqual(win.rows, rows)
                    self.assertEqual(win.content.winfo_children(), preview)
                    self.assertEqual(win.selected_id, selected)
                    self.assertEqual(tuple(pane.canvasy(0) for pane in panes), scroll)
        win.destroy()

    def test_history_select_all_includes_offscreen_rows_and_stays_in_current_tab(self):
        from copy import deepcopy
        self.note.toggle_done(1)
        self.note.clear_done()
        archive = self.app.data["archives"][0]
        self.app.data["archives"].extend(
            dict(archive, id=f"archive-{i}", noteId=f"note-{i}", title=f"便签 {i}")
            for i in range(40)
        )
        self.app.data["archives"][-1]["items"] = archive["items"] * 40
        self.app.data["deletedNotes"] = [
            dict(deepcopy(self.note.d), id=f"note-{i}", deletedAt="2026-11-10T12:00:00")
            for i in range(2)
        ]
        win = main.HistoryWindow(self.app)
        self.app.history_win = win
        select_all = win.select_all_button
        self.assertEqual(win.batch_button.cget("text"), "批量管理")
        self.assertFalse(select_all.winfo_manager())
        win.batch_button.invoke()
        self.app.root.update()
        self.assertEqual(select_all.cget("text"), "全选")
        narrow_width = select_all.winfo_width()
        self.assertFalse(win.delete_button.winfo_manager())
        win.left.viewport.yview_moveto(.5)
        win.right.viewport.yview_moveto(.5)
        self.app.root.update()
        self.assertGreater(win.left.viewport.yview()[0], 0)
        self.assertGreater(win.right.viewport.yview()[0], 0)
        scroll = win.left.viewport.yview(), win.right.viewport.yview()
        preview = win.selected_id, win.content.winfo_children()

        select_all.invoke()
        self.app.root.update()
        self.assertEqual(win.checked, set(win.ids))
        self.assertEqual(select_all.cget("text"), "取消全选")
        self.assertGreater(select_all.winfo_width(), narrow_width)
        self.assertTrue(win.delete_button.winfo_ismapped())
        for row in win.rows.values():
            box = row.pack_slaves()[0]
            self.assertEqual(win.getvar(box.cget("variable")), 1)
            for option in ("bg", "selectcolor", "activebackground"):
                self.assertEqual(box.cget(option), row.cget("bg"))
        win.rows[win.ids[0]].pack_slaves()[0].invoke()
        self.assertEqual(select_all.cget("text"), "全选")
        select_all.invoke()
        select_all.invoke()
        self.app.root.update()
        self.assertFalse(win.checked)
        self.assertEqual(select_all.winfo_width(), narrow_width)
        self.assertFalse(win.delete_button.winfo_manager())
        self.assertEqual(win.getvar(win.rows[win.ids[-1]].winfo_children()[0].cget("variable")), 0)
        self.assertEqual((win.left.viewport.yview(), win.right.viewport.yview()), scroll)
        self.assertEqual((win.selected_id, win.content.winfo_children()), preview)

        select_all.invoke()
        win.tabs["deletedNotes"].invoke()
        self.assertEqual(win.batch_button.cget("text"), "批量管理")
        self.assertFalse(win.checked)
        self.assertFalse(select_all.winfo_manager())
        win.batch_button.invoke()
        select_all.invoke()
        self.assertEqual(win.checked, {"note-0", "note-1"})
        with patch.object(main.messagebox, "askyesno", side_effect=[False, True]) as confirm:
            win.delete_button.invoke()
            self.assertEqual(len(self.app.data["deletedNotes"]), 2)
            self.assertEqual(select_all.cget("text"), "取消全选")
            win.delete_button.invoke()
        self.assertEqual(confirm.call_count, 2)
        self.assertFalse(self.app.data["deletedNotes"])
        self.assertEqual(len(self.app.data["archives"]), 41)
        self.assertFalse(win.checked)
        self.assertEqual(select_all.cget("state"), "disabled")
        self.assertEqual(select_all.cget("text"), "全选")
        self.assertFalse(win.delete_button.winfo_manager())
        win.destroy()

    def test_history_batch_toolbar_wraps_on_narrow_screen_at_high_scaling(self):
        self.note.toggle_done(1)
        self.note.clear_done()
        original = self.app.root.tk.call("tk", "scaling")
        self.app.root.tk.call("tk", "scaling", 240 / 72)
        work = main.wintypes.RECT(0, 0, 800, 600)
        try:
            with patch.object(main.SettingsWindow, "_work_area", return_value=work):
                win = main.HistoryWindow(self.app)
            win.batch_button.invoke()
            win.select_all_button.invoke()
            self.app.root.update()
            buttons = (win.select_all_button, win.delete_button, win.batch_button)
            tab = win.tabs["deletedNotes"]
            self.assertGreater(buttons[0].winfo_rooty(), tab.winfo_rooty() + tab.winfo_height())
            for button in buttons:
                self.assertTrue(button.winfo_ismapped())
                self.assertEqual(button.winfo_width(), button.winfo_reqwidth())
                self.assertEqual(button.winfo_rooty(), buttons[0].winfo_rooty())
            self.assertLess(buttons[0].winfo_rootx(), buttons[1].winfo_rootx())
            self.assertLess(buttons[1].winfo_rootx(), buttons[2].winfo_rootx())
            win.batch_button.invoke()
            self.app.root.update()
            self.assertFalse(win.checked)
            self.assertEqual(win.batch_button.winfo_rooty(), tab.winfo_rooty())
            win.destroy()
        finally:
            self.app.root.tk.call("tk", "scaling", original)

    def test_history_drag_ignores_controls_and_thumb_matches_view(self):
        win = main.HistoryWindow(self.app)
        self.app.root.update()
        event = lambda widget: type("Event", (), {"widget":widget,
            "x_root":widget.winfo_rootx()+widget.winfo_width()//2,
            "y_root":widget.winfo_rooty()+widget.winfo_height()//2})()
        main.SettingsWindow._drag_press(win, event(win.hint))
        self.assertIsNotNone(win._drag)
        for widget in (win._close_button,win._minimize_button,win.batch_button,win.left.bars["y"]):
            main.SettingsWindow._drag_press(win, event(widget))
            self.assertIsNone(win._drag)
        pane = win.right
        pane.bars["y"].set(0.25, 0.75)
        coordinates = pane.bars["y"].bbox("thumb")
        length = pane.bars["y"].winfo_height()-8
        self.assertAlmostEqual(coordinates[3]-coordinates[1], length*.5+8, delta=1)
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
        checkbox, label = row.pack_slaves()
        self.assertGreater(label.winfo_height(), checkbox.winfo_height()*2)
        self.assertAlmostEqual(checkbox.winfo_y()+checkbox.winfo_height()/2,
                               label.winfo_y()+label.winfo_height()/2, delta=2)
        label.event_generate("<Button-1>")
        self.app.root.update()
        self.assertEqual(win.selected_id, "note2")
        self.assertEqual(label.cget("bg"), "#E9EDF2")
        self.assertTrue(any(isinstance(row, main.tk.Frame) and any(
            isinstance(c, main.tk.Label) and c.cget("text") == "第二张便签的内容"
            for c in row.winfo_children()) for row in win.content.winfo_children()))
        win.destroy()

    def test_history_orders_by_latest_action_and_keeps_same_second_newest_first(self):
        from copy import deepcopy
        self.note.toggle_done(1)
        self.note.clear_done()
        batch = deepcopy(self.app.data["archives"][0])
        first = dict(deepcopy(batch), id="first", noteId="one", clearedAt="2026-11-01T12:00:00")
        second = dict(deepcopy(batch), id="second", noteId="two", clearedAt="2026-11-02T12:00:00")
        latest = dict(deepcopy(batch), id="latest", noteId="one", clearedAt="2026-11-02T12:00:00")
        self.app.data["archives"] = [first, second, latest]
        win = main.HistoryWindow(self.app)
        self.app.root.update()
        self.assertEqual(win.ids, ["one", "two"])
        self.assertEqual([r["id"] for r in win.groups["one"]], ["latest", "first"])
        self.app.data["deletedNotes"] = [
            dict(deepcopy(self.note.d), id=key, deletedAt=stamp)
            for key, stamp in (("newer", "2026-11-02"), ("older", "2026-11-01"), ("newest", "2026-11-02"))]
        win.switch("deletedNotes")
        self.assertEqual(win.ids, ["newest", "newer", "older"])
        win.destroy()

    def test_history_two_column_preview_stacks_full_width_sections(self):
        from copy import deepcopy
        note = deepcopy(self.note.d)
        left_text, right_text = "左栏长文字需要正常换行 " * 24, "右栏长待办需要完整阅读 " * 24
        note.update(twoColumn=True, title="分栏预览", deletedAt="2026-11-03T15:00:00",
                    lines=[{"type": "text", "text": left_text, "checked": False}],
                    lines2=[{"type": "todo", "text": right_text, "checked": True}])
        self.app.data["deletedNotes"] = [deepcopy(note)]
        win = main.HistoryWindow(self.app)
        size = win.winfo_width(), win.winfo_height()
        win.switch("deletedNotes")
        self.app.root.update()
        self.assertEqual((win.winfo_width(), win.winfo_height()), size)
        self.assertAlmostEqual(win.right.winfo_width() / win.left.winfo_width(), 2, delta=.15)
        sections = [child for child in win.content.winfo_children()
                    if isinstance(child, main.tk.Frame)][1:]
        self.assertEqual(len(sections), 2)
        for section, name, content in zip(sections, ("左栏", "右栏"), (left_text, right_text)):
            heading, body = [child for child in section.winfo_children()
                             if not isinstance(child, main.tk.Canvas)]
            self.assertEqual(heading.cget("text"), name)
            labels = list(body.winfo_children())
            if isinstance(labels[0], main.tk.Frame):
                marker, label = labels[0].winfo_children()
                self.assertEqual(marker.cget("image"), str(win._check_images[True]))
            else:
                label = labels[0]
            self.assertEqual(label.cget("text"), content)
            self.assertGreater(label.winfo_width(), win.right.viewport.winfo_width() * .75)
            self.assertLessEqual(label.winfo_rootx() + label.winfo_width(),
                                 win.right.viewport.winfo_rootx() + win.right.viewport.winfo_width())
        self.assertGreaterEqual(sections[1].winfo_y(), sections[0].winfo_y() + sections[0].winfo_height())
        self.assertTrue(win.right.bars["y"].find_withtag("thumb"))
        self.assertEqual(self.app.data["deletedNotes"], [note])
        win.destroy()

    def test_history_two_column_preview_preserves_blank_lines_and_empty_todos(self):
        from copy import deepcopy
        note = deepcopy(self.note.d)
        note.update(twoColumn=True, deletedAt="2026-11-03T15:00:00", lines=[], lines2=[
            {"type": "text", "text": "", "checked": False},
            {"type": "todo", "text": "", "checked": False}])
        self.app.data["deletedNotes"] = [deepcopy(note)]
        win = main.HistoryWindow(self.app)
        win.switch("deletedNotes")
        self.app.root.update()
        sections = [child for child in win.content.winfo_children()
                    if isinstance(child, main.tk.Frame)][1:]
        left, right = [next(child for child in section.winfo_children() if isinstance(child, main.tk.Frame))
                       for section in sections]
        self.assertEqual(left.winfo_children()[0].cget("text"), "暂无内容")
        blank, todo = right.winfo_children()
        self.assertEqual(blank.cget("text"), "")
        self.assertGreater(blank.winfo_height(), 0)
        marker, text = todo.winfo_children()
        self.assertEqual(marker.cget("image"), str(win._check_images[False]))
        self.assertEqual(text.cget("text"), "")
        self.assertEqual(self.app.data["deletedNotes"], [note])
        win.destroy()

    def test_history_multiline_preview_uses_one_centered_note_checkbox(self):
        win = main.HistoryWindow(self.app)
        for checked in (False, True):
            win.preview_line({"type": "todo", "text": "很长的待办" * 40 + "\n第二段", "checked": checked})
        self.app.root.update()
        rows = [c for c in win.content.winfo_children() if isinstance(c, main.tk.Frame)]
        self.assertEqual(len(rows), 2)
        for checked, row in zip((False, True), rows):
            marker, text = row.winfo_children()
            self.assertEqual(marker.cget("image"), str(win._check_images[checked]))
            self.assertEqual(bool(main.tkfont.Font(root=win, font=text.cget("font")).actual("overstrike")), checked)
            self.assertGreater(text.winfo_height(), marker.winfo_height() * 2)
            self.assertAlmostEqual(marker.winfo_y() + marker.winfo_height()/2,
                                   text.winfo_y() + text.winfo_height()/2, delta=2)
        win.destroy()

    def test_dialogs_keep_native_rounded_corners_and_app_window_style(self):
        get_attribute = main.dwmapi.DwmGetWindowAttribute
        get_attribute.argtypes = [main.wintypes.HWND, main.wintypes.DWORD,
                                  main.ctypes.c_void_p, main.wintypes.DWORD]
        get_attribute.restype = main.ctypes.c_long
        for cls in (main.SettingsWindow, main.HistoryWindow):
            with self.subTest(window=cls.__name__):
                win = cls(self.app)
                try:
                    self.app.root.update()
                    hwnd = main.user32.GetParent(win.winfo_id()) or win.winfo_id()
                    preference = main.ctypes.c_int()
                    self.assertEqual(get_attribute(hwnd, 33, main.ctypes.byref(preference),
                                                   main.ctypes.sizeof(preference)), 0)
                    self.assertEqual(preference.value, 2)
                    style = main.user32.GetWindowLongPtrW(hwnd, -20)
                    self.assertTrue(style & 0x40000)  # WS_EX_APPWINDOW
                    self.assertFalse(style & 0x80)  # WS_EX_TOOLWINDOW
                finally:
                    win.destroy()

    def test_dialogs_ignore_all_resize_edges_and_keep_fixed_size(self):
        for cls in (main.SettingsWindow, main.HistoryWindow):
            win = cls(self.app)
            self.app.root.update()
            w, h = win.winfo_width(), win.winfo_height()
            origin = win.winfo_x(), win.winfo_y()
            self.assertEqual(win.minsize(), (w, h))
            self.assertEqual(win.maxsize(), (w, h))
            self.assertFalse(any(win.resizable()))
            for px, py in ((2, h//2), (w-2, h//2), (w//2, 2), (w//2, h-2),
                           (12, 12), (w-12, 12), (12, h-12), (w-12, h-12)):
                with self.subTest(window=cls.__name__, point=(px, py)):
                    event = type("Pointer", (), dict(widget=win, x_root=origin[0]+px, y_root=origin[1]+py))()
                    main.SettingsWindow._drag_press(win, event)
                    event.x_root += 60
                    event.y_root += 40
                    main.SettingsWindow._drag_move(win, event)
                    self.app.root.update()
                    self.assertEqual((win.winfo_x(), win.winfo_y()), origin)
                    self.assertEqual((win.winfo_width(), win.winfo_height()), (w, h))
                    self.assertIsNone(win._resize)
                    self.assertNotIn(win.cget("cursor"), ("size_we", "size_ns", "size_nw_se", "size_ne_sw"))
            win.geometry(f"{w+120}x{h+80}")
            self.app.root.update()
            self.assertEqual((win.winfo_width(), win.winfo_height()), (w, h))
            win.destroy()

    def test_fixed_dialogs_can_still_move_from_the_header(self):
        for cls in (main.SettingsWindow, main.HistoryWindow):
            win = cls(self.app)
            self.app.root.update()
            x, y, w, h = win.winfo_x(), win.winfo_y(), win.winfo_width(), win.winfo_height()
            event = type("Pointer", (), dict(widget=win, x_root=x+w//2, y_root=y+35))()
            main.SettingsWindow._drag_press(win, event)
            event.x_root += 35
            event.y_root += 25
            main.SettingsWindow._drag_move(win, event)
            win.event_generate("<ButtonRelease-1>")
            self.app.root.update()
            self.assertEqual((win.winfo_x(), win.winfo_y()), (x+35, y+25))
            self.assertEqual((win.winfo_width(), win.winfo_height()), (w, h))
            self.assertIsNone(win._drag)
            win.destroy()

    def test_fixed_dialogs_fit_work_area_across_font_dpi_and_screen_sizes(self):
        original_scaling = self.app.root.tk.call("tk", "scaling")
        widths = {main.SettingsWindow: [], main.HistoryWindow: []}
        for dpi in (96, 120, 144, 192, 240):
            with patch.object(main, "load_data", return_value={
                    "settings": dict(main.DEFAULT_SETTINGS, uiDpi=dpi), "notes": []}), \
                    patch.object(main.Tooltip, "_font", None):
                scaled = main.App()
            try:
                for sw, sh in ((800, 600), (1366, 768), (1920, 1080)):
                    work = main.wintypes.RECT(0, 0, sw, sh)
                    for cls in (main.SettingsWindow, main.HistoryWindow):
                        with self.subTest(dpi=dpi, screen=(sw, sh), window=cls.__name__), \
                                patch.object(main.SettingsWindow, "_work_area", return_value=work):
                            win = cls(scaled)
                            scaled.root.update()
                            x, y, w, h = win.winfo_x(), win.winfo_y(), win.winfo_width(), win.winfo_height()
                            self.assertGreater(w, 0)
                            self.assertGreater(h, 0)
                            self.assertGreaterEqual(x, 0)
                            self.assertGreaterEqual(y, 0)
                            self.assertLessEqual(x+w, sw)
                            self.assertLessEqual(y+h, sh)
                            self.assertEqual(win.minsize(), win.maxsize())
                            if sw == 1920:
                                widths[cls].append(w)
                            if cls is main.SettingsWindow:
                                thumb = next(child for child in win.winfo_children() if isinstance(child, main.VerticalThumb))
                                body = thumb.target
                                for entry in win.hotkey_entries.values():
                                    self.assertGreaterEqual(entry.winfo_rootx(), body.winfo_rootx())
                                    self.assertLessEqual(entry.winfo_rootx()+entry.winfo_width(), body.winfo_rootx()+body.winfo_width())
                                body.yview_moveto(1)
                                scaled.root.update()
                                self.assertAlmostEqual(body.yview()[1], 1)
                            else:
                                self.assertLessEqual(win.right.winfo_rootx()+win.right.winfo_width(), win.winfo_rootx()+w)
                                self.assertLessEqual(win.batch_button.winfo_rootx()+win.batch_button.winfo_width(), win.winfo_rootx()+w)
                            win.destroy()
            finally:
                for job in scaled.root.tk.call("after", "info"):
                    scaled.root.tk.call("after", "cancel", job)
                scaled.root.destroy()
                self.app.root.tk.call("tk", "scaling", original_scaling)
        for cls, values in widths.items():
            self.assertEqual(values, sorted(values))
            self.assertGreater(values[-1], values[0]*1.7, cls.__name__)

    def test_dialog_work_area_uses_the_pointer_monitor_and_primary_fallback(self):
        class MonitorInfo(main.ctypes.Structure):
            _fields_ = [("size", main.wintypes.DWORD), ("monitor", main.wintypes.RECT),
                        ("work", main.wintypes.RECT), ("flags", main.wintypes.DWORD)]
        def monitor_info(handle, pointer):
            info = main.ctypes.cast(pointer, main.ctypes.POINTER(MonitorInfo)).contents
            info.work = main.wintypes.RECT(-1920, 40, 0, 1080)
            return True
        with patch.object(main, "cursor_pos", return_value=(-500, 200)), \
                patch.object(main.user32, "MonitorFromPoint", return_value=123) as monitor, \
                patch.object(main.user32, "GetMonitorInfoW", side_effect=monitor_info):
            work = main.SettingsWindow._work_area(self.note)
        self.assertEqual((work.left, work.top, work.right, work.bottom), (-1920, 40, 0, 1080))
        self.assertEqual((monitor.call_args.args[0].x, monitor.call_args.args[0].y), (-500, 200))
        def primary(action, unused, pointer, flags):
            main.ctypes.cast(pointer, main.ctypes.POINTER(main.wintypes.RECT)).contents.right = 1200
            return True
        with patch.object(main.user32, "GetMonitorInfoW", return_value=False), \
                patch.object(main.user32, "SystemParametersInfoW", side_effect=primary) as fallback:
            work = main.SettingsWindow._work_area(self.note)
        self.assertEqual(work.right, 1200)
        self.assertEqual(fallback.call_args.args[0], 48)

    def test_recycle_bin_stacks_edit_time_under_every_title(self):
        from copy import deepcopy
        note = deepcopy(self.note.d)
        note.update(id="second", title="同名", editedAt="2026-10-28T12:34:56.123456")
        first = dict(note, id="first", editedAt="2026-10-28T11:22:33.654321")
        unique = dict(note, id="unique", title="很长的唯一标题" * 4, editedAt="2026-10-28T10:20:30")
        old = dict(note, id="old", title="")
        old.pop("editedAt")
        self.app.data["deletedNotes"] = [first, note, unique, old]
        win = main.HistoryWindow(self.app)
        win.switch("deletedNotes")
        self.app.root.update()
        for key, name, timestamp in (
                ("first", "同名", "2026-10-28 11:22:33"),
                ("second", "同名", "2026-10-28 12:34:56"),
                ("unique", unique["title"], "2026-10-28 10:20:30"),
                ("old", "无标题", "编辑时间未知")):
            with self.subTest(key=key):
                detail, title = win.rows[key].grid_slaves()
                self.assertEqual((title.cget("text"), detail.cget("text")), (name, timestamp))
                self.assertEqual(title.winfo_x(), detail.winfo_x())
                self.assertGreaterEqual(detail.winfo_y(), title.winfo_y() + title.winfo_height())
        row = win.rows["second"]
        detail, title = row.grid_slaves()
        detail.event_generate("<Button-1>")
        self.assertEqual(win.selected_id, "second")
        for widget in (row, title, detail):
            self.assertEqual(widget.cget("bg"), "#E9EDF2")
        label_positions = title.winfo_x(), detail.winfo_x()
        title_padding = int(title.cget("padx"))
        win.toggle_batch()
        self.app.root.update()
        self.assertEqual((title.winfo_x(), detail.winfo_x()), label_positions)
        self.assertGreater(int(title.cget("padx")), title_padding)
        row = win.rows["unique"]
        title = row.grid_slaves(row=0, column=1)[0]
        box = win._check_boxes["unique"]
        self.assertGreater(title.winfo_height(), win.rows["second"].grid_slaves(row=0, column=1)[0].winfo_height())
        self.assertAlmostEqual(box.winfo_y() + box.winfo_height() / 2, row.winfo_height() / 2, delta=2)
        box.invoke()
        self.assertEqual(win.checked, {"unique"})
        title.event_generate("<Button-1>")
        self.assertEqual(win.selected_id, "unique")
        win.destroy()

    def test_scroll_thumb_has_smooth_round_caps_even_for_short_ranges(self):
        thumb = self.note.scroll_thumb
        for first, last in ((0, .5), (.5, 1), (.5, .501)):
            with self.subTest(first=first, last=last):
                thumb.set(first, last)
                image = main.ImageTk.getimage(thumb._thumb_image)
                alpha = image.getchannel("A")
                self.assertTrue(any(0 < value < 255 for value in alpha.tobytes()))
                self.assertLess(alpha.getpixel((1, 1)), alpha.getpixel((4, 1)))
                self.assertGreaterEqual(alpha.getpixel((4, image.height//2)), 250)
                self.assertEqual(alpha.crop((0, 0, 8, 4)).tobytes(),
                                 alpha.transpose(main.Image.Transpose.FLIP_TOP_BOTTOM)
                                      .crop((0, 0, 8, 4)).tobytes())
                bounds = thumb.bbox("thumb")
                self.assertGreaterEqual(bounds[1], 0)
                self.assertLessEqual(bounds[3], thumb.winfo_height())

    def test_settings_scroll_thumb_tracks_drag_wheel_and_overflow(self):
        with patch.object(main.SettingsWindow, "_work_area", return_value=main.wintypes.RECT(0, 0, 1024, 600)):
            win = main.SettingsWindow(self.app)
        self.app.root.update()
        thumb = next(w for w in win.winfo_children() if isinstance(w, main.VerticalThumb))
        body = thumb.target
        min_w, min_h = win.minsize()
        win.geometry(f"{min_w}x{min_h}")
        self.app.root.update()
        self.assertTrue(thumb.winfo_ismapped())
        self.assertEqual(thumb.winfo_width(), 8)
        self.assertTrue(thumb.find_withtag("thumb"))
        position = (win.winfo_x(), win.winfo_y())
        thumb.event_generate("<Button-1>", x=4, y=6)
        thumb.event_generate("<B1-Motion>", x=4, y=thumb.winfo_height()-6)
        thumb.event_generate("<ButtonRelease-1>", x=4, y=thumb.winfo_height()-6)
        self.app.root.update()
        self.assertGreater(body.yview()[0], 0)
        self.assertEqual((win.winfo_x(), win.winfo_y()), position)
        self.assertIsNone(win._resize)
        self.assertIsNone(thumb._drag_offset)
        body.yview_moveto(0)
        thumb.event_generate("<MouseWheel>", delta=-120)
        self.app.root.update()
        self.assertGreater(body.yview()[0], 0)
        body.yview_moveto(0)
        self.app.root.update()
        content = next(w for w in body.winfo_children() if isinstance(w, main.tk.Frame))
        packed = [(child, child.pack_info()) for child in content.winfo_children()]
        for child, _ in packed:
            child.pack_forget()
        placeholder = main.tk.Label(content, text="短内容")
        placeholder.pack()
        self.app.root.update()
        self.assertFalse(thumb.winfo_ismapped())
        self.assertEqual(body.yview(), (0.0, 1.0))
        placeholder.destroy()
        for child, options in packed:
            child.pack(**options)
        self.app.root.update()
        self.assertEqual((win.winfo_width(), win.winfo_height()), (min_w, min_h))
        self.assertTrue(thumb.winfo_ismapped())
        self.assertTrue(thumb.find_withtag("thumb"))
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
             patch.object(storage, "DATA_DIR", folder), \
             patch.object(storage, "DATA_FILE", str(Path(folder)/"notes.json")):
            self.patches[2].stop()
            try:
                main.save_data(self.app.data)
                saved = json.loads((Path(folder)/"notes.json").read_text(encoding="utf-8"))
            finally:
                self.patches[2].start()
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
                self.assertEqual(win.left.content.winfo_width(), win.left.viewport.winfo_width())
                self.assertLessEqual(win.left.content.winfo_rootx() + win.left.content.winfo_width(),
                                     win.left.winfo_rootx() + win.left.winfo_width())
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
        for font in (self.app.base_font, self.note.done_font,
                     main.tkfont.nametofont("TkDefaultFont", root=self.app.root),
                     main.tkfont.nametofont("TkMenuFont", root=self.app.root)):
            self.assertEqual(font.actual("family"), main.UI_FONT)
        self.assertEqual(self.app.base_font.actual("weight"), "normal")
        self.assertEqual(self.note._chip_font.actual("family"), "Times New Roman")
        self.assertEqual(self.note._chip_font.actual("weight"), "bold")

    def test_convert_shortcut_majority_and_repeat_preserve_selection_and_history(self):
        from copy import deepcopy
        note, text = self.note, self.text
        for kinds in (("text",), ("todo",), ("text", "text"), ("todo", "todo"),
                      ("todo", "text"), ("todo", "todo", "text"), ("text", "text", "todo")):
            with self.subTest(kinds=kinds):
                original = [{"type": kind, "text": f"中文😀行 {i}", "checked": kind == "todo"}
                            for i, kind in enumerate(kinds)]
                note.d["lines"] = deepcopy(original)
                note.render()
                text.focus_force()
                self.app.root.update()
                text.tag_add("sel", "1.0", "end-1c")
                text.mark_set("insert", "end-1c")
                history = len(note._edit_history)
                target = "text" if kinds.count("todo") * 2 > len(kinds) else "todo"
                with patch.object(note.editors[0], "render", wraps=note.editors[0].render) as render, \
                        patch.object(self.app, "schedule_save") as save:
                    text.event_generate("<Control-Shift-L>")
                    text.event_generate("<KeyRelease>", keycode=76, state=5)
                    render.assert_called_once()
                    save.assert_called_once()
                expected = [dict(line, type=target, checked=line["checked"] if target == "todo" else False)
                            for line in original]
                self.assertEqual(note.d["lines"], expected)
                self.assertEqual(len(note._edit_history), history + 1)
                self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.0", text.index("end-1c")))
                self.assertEqual(text.index("insert"), text.index("end-1c"))
                text.event_generate("<Control-Shift-L>")
                self.assertTrue(all(line["type"] != target for line in note.d["lines"]))
                text.event_generate("<<Undo>>")
                self.assertEqual(note.d["lines"], expected)
                text.event_generate("<<Undo>>")
                self.assertEqual(note.d["lines"], original)
                text.event_generate("<<Redo>>")
                self.assertEqual(note.d["lines"], expected)

    def test_convert_shortcut_partial_selection_empty_and_wrapped_rows(self):
        note, text = self.note, self.text
        note.d["lines"] = [
            {"type": "text", "text": "中😀abc" * 30, "checked": False},
            {"type": "text", "text": "", "checked": False},
            {"type": "todo", "text": "保持完成", "checked": True}]
        note.render()
        text.focus_force()
        self.app.root.update()
        text.tag_add("sel", "1.3", "3.0")
        text.mark_set("insert", "1.3")
        text.event_generate("<Control-Shift-L>")
        self.assertEqual([line["type"] for line in note.d["lines"]], ["todo"] * 3)
        self.assertTrue(note.d["lines"][2]["checked"])
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.6", "3.0"))
        self.assertEqual(text.index("insert"), "1.6")
        text.event_generate("<Control-Shift-L>")
        self.assertEqual([line["type"] for line in note.d["lines"]], ["text", "text", "todo"])
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.3", "3.0"))
        text.tag_remove("sel", "1.0", "end")
        text.tag_add("sel", "1.1", "1.3")
        text.mark_set("insert", "1.3")
        text.event_generate("<Control-Shift-L>")
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.4", "1.6"))
        text.tag_remove("sel", "1.0", "end")
        text.mark_set("insert", "2.0")
        text.event_generate("<Control-Shift-L>")
        self.assertEqual(note.d["lines"][1], {"type": "todo", "text": "", "checked": False})
        self.assertEqual(text.index("insert"), "2.3")

    def test_convert_menu_uses_selection_inside_and_clicked_row_outside(self):
        note, text = self.note, self.text
        note.d["lines"] = [{"type": "todo", "text": "已完成", "checked": True},
                           {"type": "text", "text": "文字", "checked": False},
                           {"type": "text", "text": "其他行", "checked": False}]
        note.render()
        self.app.root.update()
        text.tag_add("sel", "1.3", "2.end")
        def rightclick(index):
            box = text.bbox(index)
            event = type("Event", (), {"x": box[0] + 1, "y": box[1] + 1,
                                      "x_root": 100, "y_root": 100})()
            with patch.object(note, "_show_context_menu") as menu:
                note._on_rightclick(event)
            return menu.call_args
        call = rightclick("1.3")
        self.assertEqual(call.args[2][0][0], "全部转为待办（2 行）")
        self.assertEqual(call.kwargs["shortcuts"], {0: "Ctrl+Shift+L"})
        call.args[2][0][1]()
        self.assertTrue(note.d["lines"][0]["checked"])
        self.assertEqual(note.d["lines"][1]["type"], "todo")
        call = rightclick("3.0")
        self.assertEqual(call.args[2][0][0], "转为待办")
        self.assertFalse(text.tag_ranges("sel"))
        call.args[2][0][1]()
        self.assertEqual(note.d["lines"][2]["type"], "todo")

    def test_convert_hotkey_is_local_live_configurable_and_rejects_conflicts(self):
        from copy import deepcopy
        self.assertEqual(self.app.hotkey_text("convert"), "Ctrl+Shift+L")
        with patch.object(main.user32, "RegisterHotKey") as register:
            for value in ("Ctrl+C", "Ctrl+Shift+Z", "Ctrl+Alt+N", "Ctrl+Left", "Delete"):
                self.assertIsNotNone(self.app.update_hotkey("convert", value))
                self.assertEqual(self.app.hotkey_text("convert"), "Ctrl+Shift+L")
            self.assertIsNone(self.app.update_hotkey("convert", "Ctrl+Shift+K"))
            self.assertIn("文字／待办", self.app.update_hotkey("new", "Ctrl+Shift+K"))
            register.assert_not_called()
        for note in (self.note, self.app.new_note()):
            note.d["lines"] = [{"type": "text", "text": "正文", "checked": False}]
            note.render()
            note.text.focus_force()
            self.app.root.update()
            note.text.event_generate("<Control-Shift-L>")
            self.assertEqual(note.d["lines"][0]["type"], "text")
            note.text.event_generate("<Control-Shift-K>")
            self.assertEqual(note.d["lines"][0]["type"], "todo")
        settings = main.SettingsWindow(self.app)
        entry = settings.hotkey_entries["convert"]
        entry.focus_force()
        self.app.root.update()
        before = deepcopy(self.note.d["lines"])
        entry.event_generate("<Control-Shift-K>")
        self.assertEqual(self.note.d["lines"], before)
        self.assertEqual(entry.get(), "Ctrl+Shift+K")
        self.note.text.focus_force()
        self.app.root.update()
        with patch.object(self.note, "_ime_composing", return_value=True):
            self.note.text.event_generate("<Control-Shift-K>")
        self.assertEqual(self.note.d["lines"], before)
        settings.destroy()
        self.note.edit_title()
        self.app.root.update()
        self.note._title_editor.event_generate("<Control-Shift-K>")
        self.assertEqual(self.note.d["lines"], before)
        self.note._title_editor.event_generate("<Return>")

    def test_convert_hotkey_capture_persistence_and_local_only_reset(self):
        import tempfile
        from pathlib import Path
        with patch.object(main.user32, "RegisterHotKey", return_value=True) as register, \
                patch.object(main.user32, "UnregisterHotKey"):
            self._start_hotkeys_impl(self.app)
            self.assertEqual(register.call_count, 3)
            self.assertNotIn("convert", self.app._hotkey_ids)
            settings = main.SettingsWindow(self.app)
            entry = settings.hotkey_entries["convert"]
            entry.focus_force()
            self.app.root.update()
            with patch.object(main, "save_data") as save:
                entry.event_generate("<KeyPress>", keycode=75, state=5)
                self.assertEqual(entry.get(), "Ctrl+Shift+K")
                self.assertEqual(self.app.data["settings"]["convertHotkey"], "Ctrl+Shift+K")
                save.assert_called_once_with(self.app.data)
            entry.event_generate("<Escape>")
            self.assertTrue(settings.winfo_exists())
            with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder, \
                    patch.object(storage, "DATA_DIR", folder), \
                    patch.object(storage, "DATA_FILE", str(Path(folder) / "notes.json")), \
                    patch.object(storage, "BACKUP_DIR", str(Path(folder) / "backup")):
                storage.save_data(self.app.data)
                loaded = storage.load_data()
                with patch.object(main, "load_data", return_value=loaded):
                    restored = main.App()
                try:
                    self.assertEqual(restored.hotkey_text("convert"), "Ctrl+Shift+K")
                finally:
                    for job in restored.root.tk.call("after", "info"):
                        restored.root.tk.call("after", "cancel", job)
                    restored.root.destroy()
            register.reset_mock()
            settings._reset_hotkeys()
            self.assertEqual(entry.get(), "Ctrl+Shift+L")
            register.assert_not_called()
            settings.destroy()

    def test_convert_context_menu_displays_right_aligned_accelerator(self):
        self.note._show_context_menu(100, 100,
            [("全部转为待办（100 行）", lambda: None), ("在下方插入新行", lambda: None),
             ("删除此行", lambda: None)], shortcuts={0: "Ctrl+Alt+Shift+Backspace"})
        self.app.root.update()
        popup = self.note._context_menu
        canvas = popup.winfo_children()[0]
        label, shortcut = [item for item in canvas.find_withtag("item0") if canvas.type(item) == "text"]
        self.assertEqual(canvas.itemcget(shortcut, "anchor"), "e")
        self.assertEqual(canvas.itemcget(shortcut, "fill"), main.INK)
        self.assertLess(canvas.bbox(label)[2], canvas.bbox(shortcut)[0])
        self.assertLessEqual(canvas.bbox(shortcut)[2], canvas.winfo_width())
        popup.event_generate("<Escape>")

    def test_convert_shortcut_handles_selection_through_final_newline(self):
        note, text = self.note, self.text
        note.d["lines"] = [{"type": "text", "text": "末行😀", "checked": False}]
        note.render()
        text.focus_force()
        self.app.root.update()
        text.tag_add("sel", "1.0", "end")
        text.event_generate("<KeyPress>", keycode=76, state=7)
        self.assertEqual(note.d["lines"], [{"type": "todo", "text": "末行😀", "checked": False}])
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.0", text.index("end-1c")))

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

    def test_new_and_restored_todo_cursor_starts_after_checkbox(self):
        note = self.app.new_note()
        self.assertEqual(note.text.index("insert"), "1.3")
        note.text.focus_force()
        self.app.root.update()
        note.text.event_generate("<KeyPress-a>")
        note.text.event_generate("<KeyRelease-a>")
        self.assertEqual(note.d["lines"], [
            {"type": "todo", "text": "a", "checked": False}])
        restored = main.NoteWindow(self.app, dict(note.d))
        try:
            self.assertEqual(restored.text.index("insert"), "1.3")
        finally:
            restored.destroy()

    def test_todo_navigation_cannot_enter_checkbox_prefix(self):
        note, text = self.note, self.text
        note.d["lines"] = [
            {"type": "todo", "text": "abc", "checked": False},
            {"type": "todo", "text": "", "checked": True}]
        note.render()
        text.focus_force()
        self.app.root.update()
        for sequence in ("<Left>", "<Shift-Left>", "<Control-Left>",
                         "<Home>", "<Control-Home>", "<Up>", "<Down>"):
            with self.subTest(sequence=sequence):
                text.tag_remove("sel", "1.0", "end")
                text.mark_set("insert", "1.3")
                text.event_generate(sequence)
                # 不等待 idle：连续按键也不能在前缀里输入。
                self.assertGreaterEqual(int(text.index("insert").split(".")[1]), 3)
        text.tag_remove("sel", "1.0", "end")
        text.mark_set("insert", "1.3")
        text.event_generate("<Shift-Left>")
        self.assertFalse(text.tag_ranges("sel"))
        text.mark_set("insert", "1.4")
        text.event_generate("<Shift-Left>")
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.3", "1.4"))
        text.event_generate("<Shift-Right>")
        self.assertFalse(text.tag_ranges("sel"))
        text.event_generate("<Control-a>")
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.0", "2.3"))

    def test_vertical_navigation_uses_visible_position_after_short_row(self):
        note, text = self.note, self.text
        note.geometry("650x500")
        note.d["lines"] = [
            {"type": "todo", "text": value, "checked": False}
            for value in ("回收站界面", "删除行逻辑（三种状态）光标位置", "短", "iiiiiiiiiiii WWWW 中英文混排文字")]
        note.render()
        text.focus_force()
        self.app.root.update()
        text.mark_set("insert", "2.11")
        for key, row in (("Up", 1), ("Down", 2), ("Down", 3), ("Down", 4), ("Up", 3), ("Up", 2)):
            x = text.bbox("insert")[0]
            text.event_generate(f"<{key}>")
            text.event_generate(f"<KeyRelease-{key}>")
            self.app.root.update()
            self.assertEqual(int(text.index("insert").split(".")[0]), row, (key, row, text.index("insert")))
            if row in (1, 3):
                self.assertEqual(text.index("insert"), text.index(f"{row}.end"))
            else:
                self.assertLessEqual(abs(text.bbox("insert")[0] - x), self.app.base_font.measure("字") // 2 + 1)
        text.mark_set("insert", "1.end")
        text.event_generate("<Shift-Down>")
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.8", text.index("insert")))

    def test_vertical_navigation_between_wrapped_display_lines(self):
        note, text = self.note, self.text
        note.geometry("300x500")
        note.d["lines"] = [{"type": "todo", "text": "中英文 Wi 混排 " * 12, "checked": False}]
        note.render()
        text.focus_force()
        self.app.root.update()
        text.mark_set("insert", "1.6")
        x = text.bbox("insert")[0]
        first_y = text.bbox("insert")[1]
        text.event_generate("<Down>")
        self.assertGreater(text.bbox("insert")[1], first_y)
        self.assertLessEqual(abs(text.bbox("insert")[0] - x), self.app.base_font.measure("字") // 2 + 1)
        x = text.bbox("insert")[0]
        text.event_generate("<Up>")
        self.assertEqual(text.bbox("insert")[1], first_y)
        self.assertLessEqual(abs(text.bbox("insert")[0] - x), self.app.base_font.measure("字") // 2 + 1)

    def test_left_at_todo_start_moves_to_previous_item_end(self):
        note, text = self.note, self.text
        note.d["lines"] = [
            {"type": "text", "text": "abc", "checked": False},
            {"type": "todo", "text": "", "checked": True},
            {"type": "todo", "text": "def", "checked": False}]
        note.render()
        text.focus_force()
        self.app.root.update()
        text.mark_set("insert", "3.4")
        for expected in ("3.3", "2.3", "1.3", "1.2"):
            text.event_generate("<Left>")
            self.assertEqual(text.index("insert"), expected)
        text.mark_set("insert", "3.3")
        text.event_generate("<Shift-Left>")
        self.assertEqual(text.index("insert"), "2.3")
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("2.3", "3.3"))
        text.event_generate("<Shift-Left>")
        self.assertEqual(text.index("insert"), "1.3")
        self.assertEqual(tuple(map(str, text.tag_ranges("sel"))), ("1.3", "3.3"))

    def test_left_at_wrapped_todo_start_reaches_previous_todo_end(self):
        note, text = self.note, self.text
        note.geometry("300x500")
        note.d["lines"] = [
            {"type": "todo", "text": "abc", "checked": True},
            {"type": "todo", "text": "long text " * 30, "checked": False}]
        note.render()
        text.focus_force()
        self.app.root.update()
        text.mark_set("insert", "2.3 display lineend +1c")
        wrapped_start = text.index("insert")
        expected = text.index("insert -1c")
        self.assertGreater(int(wrapped_start.split(".")[1]), 3)
        text.event_generate("<Left>")
        self.assertEqual(text.index("insert"), expected)
        text.mark_set("insert", "2.3")
        text.event_generate("<Left>")
        self.assertEqual(text.index("insert"), "1.6")
        text.mark_set("insert", "1.3")
        text.event_generate("<Left>")
        self.assertEqual(text.index("insert"), "1.3")

    def test_deleting_completed_todo_text_to_empty_unchecks_and_supports_undo(self):
        note, text = self.note, self.text
        original = {"type": "todo", "text": "删除行逻辑", "checked": True}
        for deletion in ("backspace", "delete", "selection"):
            with self.subTest(deletion=deletion):
                note.d["lines"] = [dict(original)]
                note.render()
                text.focus_force()
                self.app.root.update()
                if deletion == "selection":
                    text.tag_add("sel", "1.3", "1.end")
                    text.event_generate("<KeyPress-BackSpace>")
                    text.event_generate("<KeyRelease-BackSpace>")
                    before_last = original
                else:
                    before_last = dict(original, text="删" if deletion == "backspace" else "辑")
                    for remaining in range(len(original["text"]) - 1, -1, -1):
                        text.mark_set("insert", "1.end" if deletion == "backspace" else "1.3")
                        key = "BackSpace" if deletion == "backspace" else "Delete"
                        text.event_generate(f"<KeyPress-{key}>")
                        text.event_generate(f"<KeyRelease-{key}>")
                        self.assertEqual(note.d["lines"][0]["checked"], remaining > 0)
                empty = {"type": "todo", "text": "", "checked": False}
                self.assertEqual(note.d["lines"], [empty])
                self.assertEqual(text.image_cget("1.0", "image"), str(note._check_slots[False]))
                self.assertFalse(text.tag_ranges("done"))
                self.assertEqual(note.status.cget("text"), "待办 0/1")
                text.event_generate("<<Undo>>")
                self.assertEqual(note.d["lines"], [before_last])
                text.event_generate("<<Redo>>")
                self.assertEqual(note.d["lines"], [empty])
                text.insert("insert", "新待办")
                note._on_keyrelease(None)
                self.assertEqual(note.d["lines"], [dict(empty, text="新待办")])

    def test_last_letter_delete_hides_strike_before_native_edit_and_before_keyrelease(self):
        note, text = self.note, self.text
        for key, selection, content in (("BackSpace", False, "a"), ("Delete", False, "a"),
                                        ("BackSpace", True, "abc"), ("Delete", True, "abc"),
                                        ("BackSpace", False, "字"), ("BackSpace", False, "😀")):
            with self.subTest(key=key, selection=selection, content=content):
                note.d["lines"] = [{"type": "todo", "text": content, "checked": True}]
                note.render()
                text.focus_force()
                self.app.root.update()
                bars = list(note._strike_widgets)
                self.assertTrue(bars)
                text.mark_set("insert", "1.end" if key == "BackSpace" else "1.3")
                if selection:
                    text.tag_add("sel", "1.3", "1.end")
                snapshots = []
                tags = text.bindtags()
                probe = "BeforeNativeDelete"
                native = tags.index("Text")
                text.bindtags((*tags[:native], probe, *tags[native:]))
                text.bind_class(probe, "<KeyPress>", lambda e: snapshots.append(
                    [bar.winfo_manager() for bar in bars if bar.winfo_exists()]))
                try:
                    text.event_generate(f"<KeyPress-{key}>")
                    self.assertEqual(snapshots, [[""]])
                    self.assertEqual(note.d["lines"], [
                        {"type": "todo", "text": "", "checked": False}])
                    self.app.root.update()
                    self.assertFalse(note._strike_widgets)
                finally:
                    text.bindtags(tags)
                    text.unbind_class(probe, "<KeyPress>")

    def test_last_character_strike_is_hidden_before_layout_flushes_redraws(self):
        note, text = self.note, self.text
        for sync in (False, True):
            with self.subTest(sync=sync):
                note.d["lines"] = [{"type": "todo", "text": "字", "checked": True}]
                note.render()
                self.app.root.update()
                bars = list(note._strike_widgets)
                self.assertTrue(bars)
                self.assertTrue(all(bar.winfo_manager() == "place" for bar in bars))
                text.delete("1.3", "1.end")
                if sync:
                    note._sync_lines_from_text()
                flush = note.body.update_idletasks
                snapshots = []

                def check_before_flush():
                    snapshots.append([bar.winfo_manager() for bar in bars if bar.winfo_exists()])
                    flush()

                with patch.object(note.body, "update_idletasks", side_effect=check_before_flush):
                    note._draw_strikes()
                self.assertTrue(snapshots)
                self.assertTrue(all(not manager for frame in snapshots for manager in frame), snapshots)
                self.assertFalse(note._strike_widgets)

    def test_first_todo_backspace_does_not_delete_checkbox(self):
        note = self.app.new_note()
        text = note.text
        text.focus_force()
        self.app.root.update()
        text.mark_set("insert", "1.3")
        for _ in range(4):
            text.event_generate("<BackSpace>")
            text.event_generate("<KeyRelease-BackSpace>")
        self.assertEqual(text.index("insert"), "1.3")
        self.assertEqual(note.d["lines"], [
            {"type": "todo", "text": "", "checked": False}])
        self.assertEqual(len(text.dump("1.0", "end", image=True)), 1)

    def test_mouse_and_home_keep_cursor_in_todo_body_before_typing(self):
        note, text = self.note, self.text
        note.d["lines"] = [{"type": "todo", "text": "abc", "checked": False}]
        note.render()
        text.focus_force()
        self.app.root.update()
        box = text.bbox("1.0")
        text.event_generate("<B1-Motion>", x=box[0], y=box[1])
        self.assertEqual(text.index("insert"), "1.3")
        text.tag_remove("sel", "1.0", "end")
        text.event_generate("<Home>")
        text.event_generate("<KeyPress-x>")
        text.event_generate("<KeyRelease-x>")
        self.assertEqual(note.d["lines"][0]["type"], "todo")
        self.assertEqual(note.d["lines"][0]["text"], "xabc")
        text.event_generate("<Home>")
        note._begin_preedit(text)
        note._replace_preedit("zhong", 5)
        note._finish_preedit("中")
        self.app.root.update()
        self.assertEqual(note.d["lines"][0]["type"], "todo")
        self.assertEqual(note.d["lines"][0]["text"], "中xabc")

    def test_paste_after_home_preserves_todo_and_plain_text_navigation(self):
        note, text = self.note, self.text
        note.d["lines"] = [
            {"type": "todo", "text": "abc", "checked": False},
            {"type": "text", "text": "plain", "checked": False}]
        note.render()
        text.focus_force()
        self.app.root.update()
        try:
            clipboard = text.clipboard_get()
        except main.tk.TclError:
            clipboard = None
        try:
            text.clipboard_clear()
            text.clipboard_append("pasted")
            text.mark_set("insert", "1.end")
            text.event_generate("<Home>")
            text.event_generate("<<Paste>>")
            self.assertEqual(note.d["lines"][0],
                             {"type": "todo", "text": "pastedabc", "checked": False})
            text.event_generate("<<Undo>>")
            self.assertEqual(note.d["lines"][0]["text"], "abc")
            text.mark_set("insert", "2.end")
            text.event_generate("<Home>")
            self.assertEqual(text.index("insert"), "2.0")
            text.mark_set("insert", "1.end")
            text.event_generate("<Shift-Down>")
            self.assertTrue(text.tag_ranges("sel"))
        finally:
            text.clipboard_clear()
            if clipboard is not None:
                text.clipboard_append(clipboard)

    def test_clicking_outside_checkbox_does_not_toggle_todo(self):
        note, text = self.note, self.text
        note.geometry("340x500")
        for content in ("abc", "long text " * 20):
            note.d["lines"] = [{"type": "todo", "text": content, "checked": False}]
            note.render()
            self.app.root.update()
            box = note._checkbox_widgets[0]
            x, y = box.winfo_x(), box.winfo_y()
            width, height = box.winfo_width(), box.winfo_height()
            points = ((x - 1, y + height // 2),
                      (x + width + 2, y + height // 2),
                      (x + width // 2, y - 1),
                      (x + width // 2, y + height + 1))
            for px, py in points:
                with self.subTest(content=content, point=(px, py)):
                    text.event_generate("<Button-1>", x=px, y=py)
                    text.event_generate("<ButtonRelease-1>", x=px, y=py)
                    self.app.root.update()
                    self.assertFalse(note.d["lines"][0]["checked"])
                    self.assertGreaterEqual(int(text.index("insert").split(".")[1]), 3)
            for px, py in ((width // 2, height // 2), (1, height // 2)):
                checked = note.d["lines"][0]["checked"]
                box.event_generate("<Button-1>", x=px, y=py)
                self.app.root.update()
                self.assertEqual(note.d["lines"][0]["checked"], not checked)

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

    def test_backspace_removes_empty_first_todo_without_changing_following_completion(self):
        for two_column in (False, True):
            if two_column:
                self.note.toggle_columns()
            for editor in self.note.editors:
                with self.subTest(two_column=two_column, column=editor.col):
                    original = [{"type": "todo", "text": "", "checked": False},
                                {"type": "todo", "text": "月报", "checked": True},
                                {"type": "todo", "text": "生息", "checked": True}]
                    editor.lines = main.deepcopy(original)
                    editor.render()
                    editor.text.mark_set("insert", "1.3")
                    self.assertEqual(editor._on_backspace(None), "break")
                    editor._on_keyrelease(None)
                    self.app.root.update()
                    self.assertEqual(editor.lines, original[1:])
                    self.assertEqual(editor.text.index("insert"), "1.3")
                    self.assertEqual(len(editor._strike_widgets), 2)
                    self.assertTrue(all(bar.winfo_ismapped() for bar in editor._strike_widgets))
                    editor._undo_edit()
                    self.assertEqual(editor.lines, original)
                    self.assertEqual(editor.text.index("insert"), "1.3")
                    editor._undo_edit(redo=True)
                    self.assertEqual(editor.lines, original[1:])

    def test_backspace_removes_empty_first_text_and_keeps_following_line(self):
        for two_column in (False, True):
            if two_column:
                self.note.toggle_columns()
            for editor in self.note.editors:
                for kind in ("text", "todo"):
                    with self.subTest(two_column=two_column, column=editor.col, following=kind):
                        original = [{"type": "text", "text": "", "checked": False},
                                    {"type": kind, "text": "月报", "checked": kind == "todo"}]
                        editor.lines = main.deepcopy(original)
                        editor.render(keep_cursor=False)
                        editor.text.focus_force()
                        self.app.root.update()
                        editor.text.mark_set("insert", "1.0")
                        editor.text.event_generate("<KeyPress-BackSpace>")
                        editor.text.event_generate("<KeyRelease-BackSpace>")
                        self.app.root.update()
                        self.assertEqual(editor.lines, original[1:])
                        caret = "1.3" if kind == "todo" else "1.0"
                        self.assertEqual(editor.text.index("insert"), caret)
                        editor._undo_edit()
                        self.assertEqual(editor.lines, original)
                        self.assertEqual(editor.text.index("insert"), "1.0")
                        editor._undo_edit(redo=True)
                        self.assertEqual(editor.lines, original[1:])
                        self.assertEqual(editor.text.index("insert"), caret)

    def test_backspace_at_nonempty_first_todo_keeps_its_checkbox_and_text(self):
        original = [{"type": "todo", "text": "月报", "checked": True},
                    {"type": "todo", "text": "生息", "checked": True}]
        self.note.d["lines"] = main.deepcopy(original)
        self.note.render()
        self.text.mark_set("insert", "1.3")
        self.assertEqual(self.note._on_backspace(None), "break")
        self.assertEqual(self.note.d["lines"], original)

    def test_backspace_merge_keeps_cursor_before_current_text(self):
        for previous_type in ("todo", "text"):
            for previous_text in ("上一项", "上一项😀" * 30, ""):
                with self.subTest(previous_type=previous_type, previous_text=previous_text):
                    self.note.d["lines"] = [
                        {"type": previous_type, "text": previous_text, "checked": previous_type == "todo"},
                        {"type": "todo", "text": "当前待办文字", "checked": False}]
                    self.note.render()
                    self.app.root.update()
                    self.text.mark_set("insert", "2.3")
                    self.assertEqual(self.note._on_backspace(None), "break")
                    self.note._on_keyrelease(None)
                    self.app.root.update()
                    self.assertEqual(self.note.d["lines"], [
                        {"type": previous_type, "text": previous_text + "当前待办文字",
                         "checked": previous_type == "todo" and bool(previous_text)}])
                    self.assertEqual(self.text.get("insert", "1.end"), "当前待办文字")
                    self.assertIsNotNone(self._visible_bbox("insert"))
                    self.text.insert("insert", "插入")
                    self.note._on_keyrelease(None)
                    self.assertEqual(self.note.d["lines"][0]["text"],
                                     previous_text + "插入当前待办文字")

    def test_backspace_into_empty_todo_keeps_current_completion_and_undo_in_each_column(self):
        self.note.toggle_columns()
        for editor in self.note.editors:
            for previous_checked in (False, True):
                for checked in (False, True):
                    with self.subTest(column=editor.col, previous_checked=previous_checked, checked=checked):
                        original = [
                            {"type": "todo", "text": "月报", "checked": False},
                            {"type": "todo", "text": "", "checked": previous_checked},
                            {"type": "todo", "text": "生息", "checked": checked}]
                        editor.lines = main.deepcopy(original)
                        editor.render()
                        editor.text.mark_set("insert", "3.3")
                        self.assertEqual(editor._on_backspace(None), "break")
                        editor._on_keyrelease(None)
                        self.app.root.update()
                        expected = [original[0], original[2]]
                        self.assertEqual(editor.lines, expected)
                        self.assertEqual(editor.text.index("insert"), "2.3")
                        self.assertEqual("done" in editor.text.tag_names("2.3"), checked)
                        self.assertEqual(len(editor._strike_widgets), int(checked))
                        if checked:
                            self.assertEqual(editor._strike_widgets[0]._text_start, "2.3")
                            self.assertTrue(editor._strike_widgets[0].winfo_ismapped())
                        editor._undo_edit()
                        self.assertEqual(editor.lines, original)
                        self.assertEqual(editor.text.index("insert"), "3.3")
                        editor._undo_edit(redo=True)
                        self.app.root.update()
                        self.assertEqual(editor.lines, expected)
                        self.assertEqual(editor.text.index("insert"), "2.3")

    def test_enter_then_backspace_restores_completed_todo_and_status(self):
        original = [{"type": "todo", "text": "月报", "checked": False},
                    {"type": "todo", "text": "生息", "checked": True}]
        self.note.d["lines"] = main.deepcopy(original)
        self.note.render()
        self.text.mark_set("insert", "2.3")
        self.note._on_enter(None)
        self.note._on_backspace(None)
        self.note._on_keyrelease(None)
        self.app.root.update()
        self.assertEqual(self.note.d["lines"], original)
        self.assertEqual(self.text.index("insert"), "2.3")
        self.assertEqual(self.note.status.cget("text"), "待办 1/2")
        self.assertEqual(len(self.note._strike_widgets), 1)
        self.assertEqual(self.note._strike_widgets[0]._text_start, "2.3")

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

    def test_enter_splits_todo_at_cursor(self):
        original = "买牛奶"
        for checked in (False, True):
            for offset in (0, 1, len(original)):
                with self.subTest(checked=checked, offset=offset):
                    self.note.d["lines"] = [
                        {"type": "todo", "text": original, "checked": checked}]
                    self.note.render()
                    prefix = self.note._prefix_len(self.note.d["lines"][0])
                    self.text.mark_set("insert", f"1.{prefix + offset}")
                    self.assertEqual(self.note._on_enter(None), "break")
                    self.app.root.update()
                    self.assertEqual(self.note.d["lines"], [
                        {"type": "todo", "text": original[:offset], "checked": checked and offset != 0},
                        {"type": "todo", "text": original[offset:], "checked": checked and offset == 0}])
                    self.assertEqual(self.text.index("insert"), f"2.{prefix}")

    def test_enter_at_todo_start_keeps_completion_strike_and_undo_in_each_column(self):
        self.note.toggle_columns()
        for editor in self.note.editors:
            for checked in (False, True):
                with self.subTest(column=editor.col, checked=checked):
                    original = {"type": "todo", "text": "生息", "checked": checked}
                    editor.lines = [dict(original)]
                    editor.render()
                    editor.text.mark_set("insert", "1.3")
                    done = sum(line["checked"] for e in self.note.editors for line in e.lines)
                    total = sum(line["type"] == "todo" for e in self.note.editors for line in e.lines)
                    editor._on_enter(None)
                    self.app.root.update()
                    expected = [{"type": "todo", "text": "", "checked": False}, original]
                    self.assertEqual(editor.lines, expected)
                    self.assertEqual(editor.text.index("insert"), "2.3")
                    self.assertEqual(self.note.status.cget("text"), f"待办 {done}/{total+1}")
                    self.assertEqual("done" in editor.text.tag_names("2.3"), checked)
                    self.assertEqual(len(editor._strike_widgets), int(checked))
                    if checked:
                        self.assertEqual(editor._strike_widgets[0]._text_start, "2.3")
                        self.assertTrue(editor._strike_widgets[0].winfo_ismapped())
                    editor._undo_edit()
                    self.assertEqual(editor.lines, [original])
                    self.assertEqual(editor.text.index("insert"), "1.3")
                    editor._undo_edit(redo=True)
                    self.app.root.update()
                    self.assertEqual(editor.lines, expected)
                    self.assertEqual(editor.text.index("insert"), "2.3")
                    self.assertEqual(len(editor._strike_widgets), int(checked))

    def test_enter_keeps_empty_plain_text_and_leading_space_behavior(self):
        for kind, text, checked, offset in (("todo", "", True, 0), ("text", "正文", False, 0),
                                             ("todo", " 生息", True, 1)):
            with self.subTest(kind=kind, text=text):
                self.note.d["lines"] = [{"type": kind, "text": text, "checked": checked}]
                self.note.render()
                prefix = self.note._prefix_len(self.note.d["lines"][0])
                self.text.mark_set("insert", f"1.{prefix+offset}")
                self.note._on_enter(None)
                self.assertEqual(self.note.d["lines"], [
                    {"type": kind, "text": text[:offset], "checked": checked},
                    {"type": kind, "text": text[offset:], "checked": False}])

    def test_enter_at_todo_start_after_selection_deletion_keeps_remaining_completion(self):
        self.note.d["lines"] = [{"type": "todo", "text": "删掉生息", "checked": True}]
        self.note.render()
        self.text.mark_set("insert", "1.3")
        self.text.tag_add("sel", "1.3", "1.5")
        self.note._on_enter(None)
        self.assertEqual(self.note.d["lines"], [
            {"type": "todo", "text": "", "checked": False},
            {"type": "todo", "text": "生息", "checked": True}])
        self.assertEqual(self.text.index("insert"), "2.3")

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

    def test_edge_fade_tracks_overflow_and_releases_native_windows(self):
        self.note.d["lines"] = [{"type": "text", "text": "Short"}]
        self.note.render()
        self.app.root.update()
        fade = self.note._edge_fade
        visible = main.user32.IsWindowVisible
        visible.argtypes = [main.wintypes.HWND]
        self.assertEqual([bool(visible(hwnd)) for hwnd in fade.windows], [False, False])
        self.note.d["lines"] = [{"type": "todo", "text": f"Item {i}", "checked": False}
                                for i in range(40)]
        self.note.render()
        self.app.root.update()
        self.note.body.yview_moveto(0)
        self.app.root.update()
        self.assertEqual([bool(visible(hwnd)) for hwnd in fade.windows], [False, True])
        self.note.body.yview_moveto(.3)
        self.app.root.update()
        self.assertEqual([bool(visible(hwnd)) for hwnd in fade.windows], [True, True])
        self.note.hide()
        self.app.root.update()
        self.assertFalse(any(visible(hwnd) for hwnd in fade.windows))
        self.note.show()
        self.app.root.update()
        self.assertTrue(all(visible(hwnd) for hwnd in fade.windows))
        self.note.body.yview_moveto(1)
        self.app.root.update()
        self.assertEqual([bool(visible(hwnd)) for hwnd in fade.windows], [True, False])
        self.note.d["lines"] = [{"type": "text", "text": "Short"}]
        self.note.render()
        self.app.root.update()
        self.assertFalse(any(visible(hwnd) for hwnd in fade.windows))
        self.note.destroy()
        self.assertEqual(fade.windows, [])

    def test_edge_fade_composites_only_edges_and_passes_mouse_through(self):
        background = main.tk.Toplevel(self.app.root, bg="#406080")
        background.overrideredirect(True)
        background.geometry("700x600+80+80")
        self.app.root.update()
        self.note.geometry("543x477+100+100")
        self.note.d["lines"] = [
            {"type": "todo", "text": f"Item {i} 渐变测试", "checked": i % 2 == 0}
            for i in range(40)]
        self.note.render()
        self.note.show()
        self.text.configure(insertwidth=0)
        self.app.root.update()
        self.note.body.yview_moveto(.17)
        self.app.root.update()
        fade, body = self.note._edge_fade, self.note.body
        x, y = body.winfo_rootx(), body.winfo_rooty()
        width, height = body.winfo_width(), body.winfo_height()

        def capture():
            main.dwmapi.DwmFlush()
            return ImageGrab.grab(bbox=(x, y, x + width, y + height))

        for opacity in (1.0, .7):
            with self.subTest(opacity=opacity):
                self.note.attributes("-alpha", opacity)
                self.app.root.update()
                faded = capture()
                for hwnd in fade.windows:
                    main.user32.ShowWindow(hwnd, 0)
                self.app.root.update()
                plain = capture()
                difference = ImageChops.difference(faded, plain)
                self.assertIsNotNone(difference.crop((0, 0, width, fade.height)).getbbox())
                self.assertIsNotNone(difference.crop((0, height-fade.height, width, height)).getbbox())
                self.assertIsNone(difference.crop((0, fade.height, width, height-fade.height)).getbbox())
                for hwnd in fade.windows:
                    main.user32.ShowWindow(hwnd, 8)
                self.app.root.update()
        for edge_y in (1, height-2):
            hit = main.user32.WindowFromPoint(main.wintypes.POINT(x+30, y+edge_y))
            self.assertNotIn(hit, fade.windows)
            self.assertIn(hit, [body.winfo_id(), self.text.winfo_id()] +
                          [widget.winfo_id() for widget in self.note._checkbox_widgets])

    def test_edge_fade_resizes_without_repainting_during_scroll(self):
        self.note.d["lines"] = [{"type": "text", "text": f"Line {i}"} for i in range(40)]
        self.note.render()
        self.app.root.update()
        fade = self.note._edge_fade
        with patch.object(fade, "_paint", wraps=fade._paint) as paint:
            for position in (.2, .4, .6):
                self.note.body.yview_moveto(position)
                self.app.root.update()
            paint.assert_not_called()
            self.note.geometry("400x300")
            self.app.root.update()
            self.assertGreater(paint.call_count, 0)
        self.assertEqual(fade.height, min(round(self.note.body.winfo_fpixels("18p")),
                                          self.note.body.winfo_height() // 4))
        self.assertEqual(fade._size[0], self.note.body.winfo_width())

    def test_viewport_resize_keeps_edge_fades_inside_body(self):
        note = self.note
        note.d["lines"] = [{"type": "text", "text": f"Line {i}"} for i in range(40)]
        note.render()
        self.app.root.update()
        with patch.object(note._edge_fade, "_paint", wraps=note._edge_fade._paint) as paint:
            note.body.place_configure(height=note.body.winfo_height()-8)
            self.app.root.update_idletasks()
            self.assertEqual(paint.call_count, 2)
        body = main.wintypes.RECT()
        main.user32.GetWindowRect(note.body.winfo_id(), main.ctypes.byref(body))
        for hwnd in note._edge_fade.windows:
            fade = main.wintypes.RECT()
            main.user32.GetWindowRect(hwnd, main.ctypes.byref(fade))
            self.assertGreaterEqual(fade.top, body.top)
            self.assertLessEqual(fade.bottom, body.bottom)
        self.assertEqual(fade.bottom, body.bottom)

    def test_see_text_keeps_caret_outside_edge_fade(self):
        self.note.d["lines"] = [{"type": "text", "text": f"Line {i}"} for i in range(60)]
        self.note.render()
        self.app.root.update()
        for index in ("30.0", "28.0", "33.0", "1.0", "60.0"):
            self.text.mark_set("insert", index)
            self.note._see_text("insert")
            self.app.root.update()
            box = self.text.bbox("insert")
            y = box[1] + self.note._chunk_start - self.note.body.canvasy(0)
            top, bottom = self.note._edge_fade._visible
            pad = self.note._edge_fade.height
            self.assertGreaterEqual(y, pad if top else 0)
            self.assertLessEqual(y+box[3], self.note.body.winfo_height() - (pad if bottom else 0))

    def test_footer_labels_align_with_button_without_overlap(self):
        self.note.geometry("300x340")
        self.app.root.update()
        center = self.note.add_pill.winfo_rooty() + self.note.add_pill.winfo_height() / 2
        for label in (self.note.status, self.note.clear_btn):
            label_center = label.winfo_rooty() + label.winfo_height() / 2
            self.assertLessEqual(abs(label_center - center), 1)
        for widget in (self.note.status, self.note.clear_btn, self.note.add_pill):
            self.assertGreaterEqual(widget.winfo_rooty(), self.note.bar.winfo_rooty())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),
                                 self.note.bar.winfo_rooty()+self.note.bar.winfo_height())
        self.assertLess(self.note.bar.winfo_x() + self.note.status.winfo_width(),
                        self.note.add_pill.winfo_x())
        self.assertGreater(self.note.bar.winfo_x() + self.note.clear_btn.winfo_x(),
                           self.note.add_pill.winfo_x() + self.note.add_pill.winfo_width())

    def test_body_uses_space_above_footer_when_resized(self):
        for height in (230, 340, 500):
            self.note.geometry(f"300x{height}")
            self.app.root.update()
            gap = (self.note.add_pill.winfo_rooty() + int(self.note.add_pill.cget("pady")) - self.note.winfo_rooty() -
                   self.note.body.winfo_y() - self.note.body.winfo_height())
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
        self.note.geometry("543x477")  # 标题伸缩需要余量，按标准便签宽度验证
        self.app.root.update()
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
        for button in self.note._tool_btns[2:]:
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
        self.note.geometry("543x477")  # 标题伸缩需要余量，按标准便签宽度验证
        self.app.root.update()
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
            # 比较可见位图边界；外围背景用于保护缩放时的图像裁剪缓存。
            pad_x, pad_y = int(pill.cget("padx")), int(pill.cget("pady"))
            left = self.note.bar.winfo_x() + self.note.status.winfo_x()
            right = self.note.bar.winfo_x() + self.note.clear_btn.winfo_x()
            left_gap = pill.winfo_x() + pad_x - left - self.note.status.winfo_width()
            right_gap = right - pill.winfo_x() - pill.winfo_width() + pad_x
            self.assertLessEqual(abs(left_gap - left), 1)
            right_outer_gap = width - right - self.note.clear_btn.winfo_width()
            self.assertLessEqual(abs(right_gap - right_outer_gap), 1)
            self.assertGreaterEqual(left_gap, 6)
            self.assertGreaterEqual(left, 12)
            self.assertLessEqual(right + self.note.clear_btn.winfo_width(), width - 12)
            self.assertLessEqual(abs(pill.winfo_x() + pill.winfo_width() / 2 - width / 2), .5)
            self.assertGreaterEqual(pill.winfo_width(), pill.winfo_height() + 4)
            self.assertEqual(height - (pill.winfo_rooty()-self.note.winfo_rooty()) - pill.winfo_height() + pad_y, 10)
            if width == 280:
                self.assertEqual(self.note.clear_btn.cget("text"), "清除完成")
        self.assertEqual(self.note.clear_btn.cget("text"), "清除已完成")

    # ---- 单双栏切换 ----
    def test_two_column_toggle_edits_and_merges_back(self):
        note = self.note
        original_width = note.winfo_width()
        note.d["lines"] = [{"type": "text", "text": "左栏", "checked": False},
                           {"type": "todo", "text": "任务A", "checked": False}]
        note.render()
        note.toggle_columns()
        self.app.root.update()
        self.assertTrue(note.d["twoColumn"])
        self.assertEqual(len(note.editors), 2)
        self.assertEqual(note.d["lines2"], [{"type": "todo", "text": "", "checked": False}])
        self.assertEqual(len(note.editors[1]._checkbox_widgets), 1)
        self.assertGreater(note.winfo_width(), original_width)  # 切换时自动加宽
        self.assertEqual(note.winfo_width(), max(520, round(original_width * 1.3)))
        self.assertEqual([l["text"] for l in note.d["lines"]], ["左栏", "任务A"])
        # 两栏布局：左右等宽、中间隔条的圆头线上下留边不顶满
        e0, e1 = note.editors
        self.assertEqual(e0.body.winfo_width(), e1.body.winfo_width())
        self.assertIsNotNone(note.divider)
        (line,) = note.divider.find_all()
        _, line_top, _, line_bottom = note.divider.coords(line)
        self.assertGreater(line_top, 8)
        self.assertLess(line_bottom, note.divider.winfo_height() - 8)
        # 右栏独立编辑
        e1.text.insert("end", "右栏内容")
        e1._on_keyrelease(None)
        self.app.root.update()
        self.assertEqual(note.d["lines2"][0]["text"], "右栏内容")
        # 切回单栏：合并右栏内容，并恢复分栏前的宽度。
        note.toggle_columns()
        self.app.root.update()
        self.assertFalse(note.d["twoColumn"])
        self.assertNotIn("lines2", note.d)
        self.assertEqual(len(note.editors), 1)
        self.assertEqual(note.winfo_width(), original_width)
        self.assertEqual([l["text"] for l in note.d["lines"]], ["左栏", "任务A", "右栏内容"])
        note.toggle_columns()
        self.app.root.update()
        self.assertEqual([l["text"] for l in note.d["lines"]], ["左栏", "任务A"])
        self.assertEqual(note.d["lines2"], [{"type": "todo", "text": "右栏内容", "checked": False}])

    def test_column_toggle_width_respects_minimum_and_screen_limit(self):
        note = self.note
        for width, screen_width, expected_width in ((320, 1200, 520), (600, 1200, 780),
                                                     (700, 800, 776)):
            with self.subTest(width=width, screen_width=screen_width):
                note.geometry(f"{width}x477+100+100")
                self.app.root.update()
                with patch.object(note, "winfo_screenwidth", return_value=screen_width):
                    note.toggle_columns()
                self.app.root.update()
                self.assertEqual(note.winfo_width(), expected_width)
                self.assertLessEqual(note.winfo_x() + note.winfo_width(), screen_width)
                note.toggle_columns()
                self.app.root.update()
                self.assertEqual(note.winfo_width(), width)

    def test_column_toggle_does_not_append_empty_right_placeholder(self):
        original = [{"type": "todo", "text": "月报", "checked": False},
                    {"type": "todo", "text": "生息", "checked": True}]
        self.note.d["lines"] = main.deepcopy(original)
        self.note.render()
        for _ in range(3):
            self.note.toggle_columns()
            self.assertTrue(self.note.editors[1]._is_empty_todo_column())
            self.note.toggle_columns()
            self.assertEqual(self.note.d["lines"], original)
            self.assertEqual(self.note.status.cget("text"), "待办 1/2")
            self.assertNotIn("lines2", self.note.d)

    def test_column_toggle_preserves_nonplaceholder_empty_right_rows(self):
        left = [{"type": "text", "text": "左栏", "checked": False}]
        for right in ([{"type": "text", "text": "", "checked": False}],
                      [{"type": "todo", "text": "", "checked": True}],
                      [{"type": "todo", "text": "", "checked": False}] * 2):
            with self.subTest(right=right):
                self.note.d["lines"] = main.deepcopy(left)
                self.note.render()
                self.note.toggle_columns()
                self.note.editors[1].lines = main.deepcopy(right)
                self.note.editors[1].render()
                self.note.toggle_columns()
                self.assertEqual(self.note.d["lines"], left + [dict(line, mergedColumn=1) for line in right])
                self.note.toggle_columns()
                self.assertEqual(self.note.d["lines2"], right)
                self.note.toggle_columns()

    def test_column_round_trip_preserves_duplicate_text_blank_lines_and_checks(self):
        from copy import deepcopy
        note = self.note
        note.toggle_columns()
        left = [{"type": "text", "text": "相同内容", "checked": False},
                {"type": "text", "text": "", "checked": False}]
        right = [{"type": "todo", "text": "相同内容", "checked": True},
                 {"type": "text", "text": "", "checked": False},
                 {"type": "todo", "text": "", "checked": False}]
        note.d["lines"], note.d["lines2"] = deepcopy(left), deepcopy(right)
        note.render()
        for _ in range(3):
            note.toggle_columns()
            self.app.root.update()
            self.assertEqual([l["text"] for l in note.d["lines"]],
                             [l["text"] for l in left + right])
            note.toggle_columns()
            self.app.root.update()
            self.assertEqual(note.d["lines"], left)
            self.assertEqual(note.d["lines2"], right)

    def test_collapsed_column_edits_survive_save_reopen_and_split(self):
        import json
        note = self.note
        note.toggle_columns()
        note.d["lines"] = [{"type": "text", "text": "左", "checked": False}]
        note.d["lines2"] = [{"type": "todo", "text": "右", "checked": True}]
        note.render()
        note.toggle_columns()
        text = note.text
        text.mark_set("insert", "1.end")
        note._on_enter(None)
        text.insert("insert", "左侧新增")
        note._on_keyrelease(None)
        text.insert("3.end", "侧修改")
        note._on_keyrelease(None)
        text.mark_set("insert", "3.end")
        note.add_todo()
        text.insert("insert", "右侧新增")
        note._on_keyrelease(None)
        note._undo_edit()
        note._undo_edit(redo=True)
        saved = json.loads(json.dumps(note.d))
        note.destroy()
        note = main.NoteWindow(self.app, saved)
        self.app.notes[saved["id"]] = note
        self.app.root.update()
        note.toggle_columns()
        self.app.root.update()
        self.assertEqual([l["text"] for l in note.d["lines"]], ["左", "左侧新增"])
        self.assertEqual(note.d["lines2"], [
            {"type": "todo", "text": "右侧修改", "checked": True},
            {"type": "todo", "text": "右侧新增", "checked": False}])

    def test_pasting_at_start_of_collapsed_right_text_keeps_its_column(self):
        note = self.note
        note.toggle_columns()
        note.d["lines"] = [{"type": "text", "text": "左", "checked": False}]
        note.d["lines2"] = [{"type": "text", "text": "右", "checked": False}]
        note.render()
        note.toggle_columns()
        note.text.clipboard_clear()
        note.text.clipboard_append("首行\n")
        note.text.mark_set("insert", "2.0")
        note.editors[0]._clipboard_edit("tk_textPaste")
        note.toggle_columns()
        self.app.root.update()
        self.assertEqual([l["text"] for l in note.d["lines"]], ["左"])
        self.assertEqual([l["text"] for l in note.d["lines2"]], ["首行", "右"])

    def test_column_toggle_restores_width_after_resize_and_reopen(self):
        from copy import deepcopy
        note = self.note
        note.geometry("440x477+100+100")
        self.app.root.update()
        for _ in range(3):
            note.toggle_columns()
            self.app.root.update()
            note.geometry("900x477+100+100")
            self.app.root.update()
            saved = deepcopy(note.d)
            note.destroy()
            note = main.NoteWindow(self.app, saved)
            self.app.notes[saved["id"]] = note
            self.app.root.update()
            note.toggle_columns()
            self.app.root.update()
            self.assertEqual(note.winfo_width(), 440)
            self.assertEqual(note.d["w"], 440)

    def test_both_columns_resize_equally_from_either_edge(self):
        note = self.note
        note.geometry("440x477+100+100")
        self.app.root.update()
        note.toggle_columns()
        self.app.root.update()
        width = note.winfo_width()
        for edge in ("right", "left"):
            start_x = width - 2 if edge == "right" else 2
            self._begin_resize_session(x=start_x, width=width)
            left, right = (editor.body for editor in note.editors)
            box = note.editors[1]._checkbox_widgets[0]
            local_anchor = box.winfo_rootx() - right.winfo_rootx()
            for delta in (35, 80, 15, -20, -184, 0):
                pointer_delta = delta if edge == "right" else -delta
                self._resize_to_pointer(180 + start_x + pointer_delta, 400)
                available = note._resize_target[2] - 2 * (main.RADIUS + 4) - 14
                self.assertEqual(left.winfo_width(), available // 2)
                self.assertEqual(right.winfo_width(), available - available // 2)
                self.assertEqual(right.winfo_x(), left.winfo_x() + left.winfo_width() + 14)
                self.assertEqual(note.divider.winfo_x(), left.winfo_x() + left.winfo_width())
                self.assertEqual(box.winfo_rootx() - right.winfo_rootx(), local_anchor)
            note._finish_resize(cancel=True)

    def test_legacy_two_column_note_can_return_to_compact_single_column(self):
        note = self.note
        note.toggle_columns()
        note.d.pop("singleColumnWidth")
        note.geometry("792x477+100+100")
        self.app.root.update()
        note.toggle_columns()
        self.app.root.update()
        self.assertEqual(note.winfo_width(), 440)

    def test_right_checkbox_stays_complete_while_both_columns_resize(self):
        note = self.note
        note.geometry("440x477+100+100")
        self.app.root.update()
        note.toggle_columns()
        note.d["lines2"] = [
            {"type": "todo", "text": "", "checked": False},
            {"type": "todo", "text": "已完成", "checked": True}]
        note.editors[1].render()
        self.app.root.update()
        width = note.winfo_width()
        surface = self._begin_resize_session(x=width - 2, width=width)

        def checkbox_pixels():
            frame = main.Image.frombytes("RGB", surface.capacity,
                main.ctypes.string_at(surface.bits, surface.capacity[0] * surface.capacity[1] * 4),
                "raw", "BGRX")
            crops = []
            for box in note.editors[1]._checkbox_widgets:
                x = box.winfo_rootx() - note.winfo_rootx()
                y = box.winfo_rooty() - note.winfo_rooty()
                crops.append(frame.crop((x, y, x + box.winfo_width(), y + box.winfo_height())))
            return crops

        note._render_resize_frame()
        reference = checkbox_pixels()
        self.assertEqual(len(reference), 2)
        for target_width in (700, 650, *range(624, 519, -4), 560, 704):
            self._resize_to_pointer(180 + target_width - 2, 400)
            actual = checkbox_pixels()
            self.assertEqual(len(actual), len(reference))
            for before, after in zip(reference, actual):
                self.assertIsNone(ImageChops.difference(before, after).getbbox(), target_width)
        note._finish_resize(cancel=True)

    def test_shrinking_columns_clears_old_divider_and_scrollbar_pixels(self):
        note = self.note
        note.geometry("440x400+100+100")
        note.d["lines"] = [
            {"type": "todo", "text": "测试 " * (i % 3 + 2), "checked": False}
            for i in range(30)]
        note.render()
        self.app.root.update()
        note.toggle_columns()
        self.app.root.update()
        width = note.winfo_width()
        surface = self._begin_resize_session(x=width - 2, width=width, height=400)
        for target_width in (650, 600, 560, 520, 560, 704):
            self._resize_to_pointer(180 + target_width - 2, 400)
            right = note.editors[1].body
            frame = main.Image.frombytes("RGB", surface.capacity,
                main.ctypes.string_at(surface.bits, surface.capacity[0] * surface.capacity[1] * 4),
                "raw", "BGRX")
            blank = frame.crop((right.winfo_x(), right.winfo_y(),
                                right.winfo_x() + right.winfo_width() - 12,
                                right.winfo_y() + right.winfo_height()))
            for box in note.editors[1]._checkbox_widgets:
                x = box.winfo_rootx() - right.winfo_rootx()
                y = box.winfo_rooty() - right.winfo_rooty()
                main.ImageDraw.Draw(blank).rectangle(
                    (x, y, x + box.winfo_width() - 1, y + box.winfo_height() - 1), fill="white")
            self.assertEqual(blank.getextrema(), ((255, 255),) * 3, target_width)
            divider = note.divider
            gap = frame.crop((divider.winfo_x(), divider.winfo_y(),
                              divider.winfo_x() + divider.winfo_width(),
                              divider.winfo_y() + divider.winfo_height()))
            main.ImageDraw.Draw(gap).rectangle((4, 0, 10, gap.height - 1), fill="white")
            self.assertEqual(gap.getextrema(), ((255, 255),) * 3, target_width)
            thumb = note.editors[0].scroll_thumb
            self.assertLessEqual(thumb.winfo_x() + thumb.winfo_width(), note.divider.winfo_x() - 2)
        note._finish_resize(cancel=True)

    def test_two_column_clear_done_and_return_to_its_column(self):
        note = self.note
        note.toggle_columns()
        self.app.root.update()
        note.d["lines"] = [{"type": "todo", "text": "L", "checked": True}]
        note.d["lines2"] = [{"type": "todo", "text": "R", "checked": True}]
        note.render()
        self.assertEqual(note.status.cget("text"), "待办 2/2")  # 状态汇总两栏
        note.clear_done()
        self.app.root.update()
        batch = self.app.data["archives"][-1]
        self.assertEqual([item["col"] for item in batch["items"]], [0, 1])
        self.assertFalse(any(l["type"] == "todo" and l["checked"]
                             for l in note.d["lines"] + note.d["lines2"]))
        self.app.return_archive(batch["id"])
        self.app.root.update()
        self.assertEqual([l["text"] for l in note.d["lines"]], ["L"])
        self.assertEqual([l["text"] for l in note.d["lines2"]], ["R"])

    def test_two_column_note_round_trips_through_recycle_bin(self):
        note = self.note
        note.toggle_columns()
        left = [{"type": "text", "text": "左栏内容", "checked": False}]
        right = [{"type": "todo", "text": "右栏已完成", "checked": True},
                 {"type": "todo", "text": "", "checked": False}]
        note.d["lines"], note.d["lines2"] = left, right
        note.render()
        self.app.root.update()
        note_id = note.d["id"]
        note.delete_self()
        self.app.root.update()
        deleted = next(n for n in self.app.data["deletedNotes"] if n["id"] == note_id)
        self.assertTrue(deleted.get("twoColumn"))
        self.assertTrue(self.app.recover_note(note_id))
        self.app.root.update()
        self.assertEqual(len(self.app.notes[note_id].editors), 2)
        self.assertEqual(self.app.notes[note_id].d["lines"], left)
        self.assertEqual(self.app.notes[note_id].d["lines2"], right)


if __name__ == "__main__":
    unittest.main()
