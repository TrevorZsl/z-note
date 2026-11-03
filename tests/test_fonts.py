"""Bundled font loading and global font-setting regressions (Windows)."""
from copy import deepcopy
import unittest
from unittest.mock import patch

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

    def test_selection_updates_notes_titles_and_new_notes_and_survives_restart(self):
        note = next(iter(self.app.notes.values()))
        note.d["title"] = "中英文 Test"
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
            self.assertEqual(note._chip_font.actual("family"), expected)
            note.edit_title()
            self.assertEqual(main.tkfont.Font(font=note._title_editor.cget("font")).actual("family"), expected)
            self.assertEqual(note.d["lines"], original)
            created = self.app.new_note()
            self.assertEqual(created.done_font.actual("family"), expected)
            self.assertEqual(created._chip_font.actual("family"), expected)
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


if __name__ == "__main__":
    unittest.main()
