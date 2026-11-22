"""Bundled font loading and global font-setting regressions (Windows)."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from PIL import ImageGrab, ImageStat

import main
from bundled_fonts import FONT_CHOICES, FONT_FAMILIES


class FontTests(unittest.TestCase):
    def setUp(self):
        self.data = {"schemaVersion": 3, "settings": dict(main.DEFAULT_SETTINGS), "notes": []}
        self.patches = [patch.object(main, "load_data", side_effect=lambda: deepcopy(self.data)),
                        patch.object(main, "save_data"),
                        patch.object(main.App, "_start_tray"),
                        patch.object(main.App, "_start_hotkeys")]
        for item in self.patches:
            item.start()
        self.app = main.App()
        self.app.root.update()

    def tearDown(self):
        for job in self.app.root.tk.call("after", "info"):
            self.app.root.tk.call("after", "cancel", job)
        self.app.root.destroy()
        for item in reversed(self.patches):
            item.stop()

    def test_bundled_families_render_both_scripts_without_system_fallback(self):
        for family in FONT_FAMILIES.values():
            for weight in ("normal", "bold"):
                font = main.tkfont.Font(root=self.app.root, family=family, size=11, weight=weight)
                for char in "Az09中文便签，。":
                    with self.subTest(family=family, weight=weight, char=char):
                        self.assertEqual(self.app.root.tk.call("font", "actual", font.name,
                                                              "-family", "--", char), family)

    def test_title_entry_renders_heavier_chinese_and_latin_strokes(self):
        note = self.app.new_note()
        note.edit_title()
        entry = note._title_editor
        entry.configure(insertwidth=0)
        for choice in FONT_CHOICES:
            self.app.data["settings"]["fontFamily"] = choice
            self.app.update_fonts()
            for sample in ("命名", "Ab"):
                with self.subTest(choice=choice, sample=sample):
                    note._title_var.set(sample)
                    ink = {}
                    for weight in ("normal", "bold"):
                        note._chip_font.configure(weight=weight)
                        note._draw_chip()
                        self.app.root.update()
                        x, y = entry.winfo_rootx(), entry.winfo_rooty()
                        box = (x, y, x + entry.winfo_width(), y + entry.winfo_height())
                        pixels = ImageGrab.grab(box, all_screens=True).convert("L")
                        ink[weight] = 255 * pixels.width * pixels.height - ImageStat.Stat(pixels).sum[0]
                    self.assertGreater(ink["normal"], 0)
                    self.assertGreater(ink["bold"], ink["normal"] * 1.05)

    def test_selection_updates_notes_titles_and_new_notes_and_survives_restart(self):
        note = self.app.new_note()
        note.d["title"] = "办公助手 ABC-11.06"
        note.d["lines"] = [{"type": "todo", "text": "中文 ABC", "checked": True}]
        note.render()
        win = main.SettingsWindow(self.app)
        original = deepcopy(note.d["lines"])
        for choice, label in FONT_CHOICES.items():
            win._font_family(label)
            self.app.root.update()
            expected = self.app.note_font_family()
            self.assertEqual(self.app.base_font.actual("family"), expected)
            self.assertEqual(note.done_font.actual("family"), expected)
            self.assertEqual(note._chip_font.cget("family"), expected)
            for char in "办A1":
                self.assertEqual(self.app.root.tk.call("font", "actual", note._chip_font.name,
                                                       "-weight", "--", char), "bold")
            note.edit_title()
            self.assertEqual(note._title_editor.cget("font"), note._chip_font.name)
            actual = self.app.root.tk.call
            for char in "命名Ab":
                self.assertEqual(actual("font", "actual", note._title_editor.cget("font"), "-family", "--", char),
                                 actual("font", "actual", self.app.base_font.name, "-family", "--", char))
            self.assertEqual(note.d["lines"], original)
            created = self.app.new_note()
            self.assertEqual(created.done_font.actual("family"), expected)
            self.assertEqual(created._chip_font.cget("family"), expected)
        win._font_family(FONT_CHOICES["yahei"])
        self.app.save_now()
        self.data = deepcopy(self.app.data)
        for job in self.app.root.tk.call("after", "info"):
            self.app.root.tk.call("after", "cancel", job)
        self.app.root.destroy()
        self.app = main.App()
        self.assertEqual(self.app.data["settings"]["fontFamily"], "yahei")
        self.assertEqual(self.app.base_font.actual("family"), FONT_FAMILIES["yahei"])

    def test_legacy_settings_default_to_current_style(self):
        self.assertEqual(self.app.data["settings"]["fontFamily"], "classic")
        self.assertEqual(self.app.base_font.actual("family"), FONT_FAMILIES["classic"])
        self.data["settings"].pop("fontFamily")
        for job in self.app.root.tk.call("after", "info"):
            self.app.root.tk.call("after", "cancel", job)
        self.app.root.destroy()
        self.app = main.App()
        self.assertEqual(self.app.data["settings"]["fontFamily"], "classic")

    def test_font_popup_click_keyboard_and_dismiss_release_grab(self):
        win = main.SettingsWindow(self.app)
        self.app.root.update()
        field = win.font_selector
        self.assertEqual(win.font_choice.get(), "Times New Roman")
        field.event_generate("<Button-1>")
        self.app.root.update()
        popup = field._popup
        self.assertIsNotNone(popup)
        self.assertEqual(popup.grab_current(), popup)
        popup.focus_force()
        popup.event_generate("<KeyPress-Down>")
        popup.event_generate("<KeyPress-Return>")
        self.app.root.update()
        self.assertEqual(self.app.data["settings"]["fontFamily"], "yahei")
        self.assertEqual(win.font_choice.get(), "微软雅黑")
        self.assertIsNone(field._popup)
        self.assertIsNone(win.grab_current())
        field._open()
        self.app.root.update()
        popup = field._popup
        canvas = popup.winfo_children()[0]
        # Click the third visible option, rather than invoking its callback.
        row_height = max(30, field._font.metrics("linespace") + 14)
        canvas.event_generate("<Button-1>", x=20, y=6 + 2 * row_height + row_height // 2)
        self.app.root.update()
        self.assertEqual(self.app.data["settings"]["fontFamily"], "simsun")
        field._open()
        self.app.root.update()
        field._popup.event_generate("<Button-1>", x=-20, y=-20)
        self.app.root.update()
        self.assertIsNone(field._popup)
        field._open()
        self.app.root.update()
        field._popup.focus_force()
        field._popup.event_generate("<KeyPress-Escape>")
        self.app.root.update()
        self.assertIsNone(field._popup)
        field._open()
        self.app.root.update()
        win.destroy()
        self.assertIsNone(self.app.root.grab_current())

    def test_ui_text_follows_font_choice_in_open_windows(self):
        note = self.app.new_note()
        note.d["lines"] = [{"type": "todo", "text": "中文 ABC", "checked": True}]
        note.render()
        win = self.app.settings_win = main.SettingsWindow(self.app)
        self.app.root.update()
        family = lambda widget: self.app.root.tk.splitlist(widget.cget("font"))[0]
        labels = []
        pending = list(win.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, main.tk.Label) and widget.cget("text") in ("字体", "设置"):
                labels.append(widget)
        body = next(label for label in labels if label.cget("text") == "字体")
        title = next(label for label in labels if label.cget("text") == "设置")
        self.assertEqual(family(note.status), main.CLASSIC_FAMILY)
        self.assertEqual(family(title), main.CLASSIC_FAMILY)
        for choice in ("yahei", "simsun", "system", "classic"):
            win._font_family(FONT_CHOICES[choice])
            self.app.root.update()
            expected = self.app.note_font_family()
            self.assertEqual(main.UI_FONT, expected)
            self.assertEqual(family(note.status), expected)
            self.assertEqual(family(note.clear_btn), expected)
            self.assertEqual(family(body), expected)
            self.assertEqual(family(title), expected)
            for button in (win._update_button, win._update_cancel_button):
                self.assertEqual(button.tk.call("font", "actual", button.cget("font"), "-family"),
                                 expected)
            self.assertEqual(win.font_selector._font.cget("family"), expected)
            self.assertEqual(main.tkfont.nametofont("TkDefaultFont", root=self.app.root).cget("family"), expected)
            self.assertEqual(self.app.dock.font.cget("family"), expected)
            for border in win._hotkey_borders:
                entry = border.winfo_children()[0]
                self.assertEqual(int(border.cget("width")), entry.winfo_reqwidth() + 12)
        win.destroy()

    def test_font_popup_click_does_not_drag_slider_below(self):
        win = main.SettingsWindow(self.app)
        self.app.root.update()
        field = win.font_selector
        opacity = self.app.opacity_scale
        sizes = [w for w in opacity.master.master.winfo_children()
                 for w in w.winfo_children() if isinstance(w, main.SettingsScale) and w is not opacity]
        field._open()
        self.app.root.update()
        canvas = field._popup.winfo_children()[0]
        row_height = max(30, field._font.metrics("linespace") + 14)
        canvas.event_generate("<Button-1>", x=20, y=6 + row_height + row_height // 2)
        self.app.root.update()
        self.assertEqual(self.app.data["settings"]["fontFamily"], "yahei")
        # 弹窗已在按下时关闭，按住未松开时的移动落到下方滑块上。
        for scale in [opacity] + sizes:
            scale.event_generate("<B1-Motion>", x=168, y=14)
            scale.event_generate("<ButtonRelease-1>", x=168, y=14)
        self.assertEqual(opacity.get(), 0)
        self.assertEqual(self.app.data["settings"]["fontSize"], 11)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
