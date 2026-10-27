"""Windows UI regressions. Run: python -m unittest discover -s tests -v"""
import unittest
from unittest.mock import patch

import main


class NoteEditingTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
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

    def test_opacity_slider_updates_existing_and_new_notes_in_five_percent_steps(self):
        settings = main.SettingsWindow(self.app)
        self.app.root.update()
        self.app.opacity_scale.set(73)
        self.app.root.update()
        self.assertEqual(self.app.opacity_scale.get(), 75)
        settings._op(self.app.opacity_scale.get())
        self.assertEqual(self.app.data["settings"]["opacity"], 0.75)
        self.assertEqual(self.note.d["opacity"], 0.75)
        self.assertAlmostEqual(float(self.note.attributes("-alpha")), 0.75, places=2)
        other = self.app.new_note()
        self.assertEqual(other.d["opacity"], 0.75)
        self.note._ctrl_wheel(type("Wheel", (), {"delta": 120})())
        self.assertEqual(other.d["opacity"], 0.8)
        self.assertEqual(self.app.opacity_scale.get(), 80)
        settings.destroy()

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

    def test_delete_dialog_keeps_message_above_buttons(self):
        original_scaling = self.app.root.tk.call("tk", "scaling")
        try:
            for scaling in (1.0, 2.0):
                self.app.root.tk.call("tk", "scaling", scaling)
                self.note.delete_self()
                self.app.root.update()
                popup = next(child for child in self.note.winfo_children()
                             if isinstance(child, main.tk.Toplevel))
                canvas = next(child for child in popup.winfo_children()
                              if isinstance(child, main.tk.Canvas))
                self.assertLess(canvas.bbox("message")[3], canvas.bbox("confirm")[1])
                self.assertLess(canvas.bbox("message")[2], popup.winfo_width())
                popup.grab_release()
                popup.destroy()
        finally:
            self.app.root.tk.call("tk", "scaling", original_scaling)

    def test_large_title_stays_inside_toolbar_above_body(self):
        self.note._chip_font.configure(size=20)
        self.note._draw_chip()
        self.note._last_sz = None
        self.note._relayout()
        self.app.root.update()
        self.assertGreaterEqual(self.note.text.winfo_y(),
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
        self.app.root.after(30, self.app.root.quit)
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

    def test_enter_replaces_selected_text(self):
        original = self.note.d["lines"][1]["text"]
        self.text.tag_add("sel", "2.5", "2.end")
        self.text.mark_set("insert", "2.end")
        self.note._on_enter(None)
        self.assertEqual(self.note.d["lines"][1]["text"], original[:2])
        self.assertEqual(self.note.d["lines"][2]["text"], "")

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
            label_center = (self.note.bar.winfo_y() + label.winfo_y() + label.winfo_height() / 2)
            self.assertLessEqual(abs(label_center - center), 1)
        self.assertLess(self.note.bar.winfo_x() + self.note.status.winfo_width(),
                        self.note.add_pill.winfo_x())
        self.assertGreater(self.note.bar.winfo_x() + self.note.clear_btn.winfo_x(),
                           self.note.add_pill.winfo_x() + self.note.add_pill.winfo_width())

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
        self.assertNotEqual(self.note._ime_target, entry)
        entry.event_generate("<Return>")
        self.app.root.update()
        self.assertEqual(self.note.d["title"], "中文标题")

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
