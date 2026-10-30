"""Storage regressions use temporary folders, never the user's notes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)))
        self.file = self.folder / "notes.json"
        self.backup = self.folder / "backup"
        self.enterContext(patch.object(storage, "DATA_DIR", str(self.folder)))
        self.enterContext(patch.object(storage, "DATA_FILE", str(self.file)))
        self.enterContext(patch.object(storage, "BACKUP_DIR", str(self.backup)))

    def test_missing_file_returns_independent_defaults_without_writing(self):
        data = storage.load_data()
        self.assertEqual(data, {"schemaVersion": 2, "settings": storage.DEFAULT_SETTINGS, "notes": []})
        data["settings"]["fontSize"] = 99
        self.assertEqual(storage.load_data()["settings"]["fontSize"], 11)
        self.assertFalse(self.file.exists())

    def test_round_trip_preserves_notes_settings_and_history(self):
        data = {"schemaVersion": 2, "settings": dict(storage.DEFAULT_SETTINGS),
                "notes": [{"id": "note", "lines": [{"type": "todo", "text": "中文", "checked": True}]}],
                "archives": [{"id": "archive"}], "deletedNotes": [{"id": "deleted"}]}
        storage.save_data(data)
        self.assertEqual(storage.load_data(), data)
        self.assertFalse(Path(str(self.file) + ".tmp").exists())
        self.assertEqual(len(list(self.backup.glob("notes.*.json"))), 1)

    def test_backups_preserve_original_bytes_and_keep_latest_five(self):
        raw = '{"notes": [], "title": "中文"}\n'
        self.file.write_text(raw, encoding="utf-8")
        self.backup.mkdir()
        for index in range(6):
            (self.backup / f"notes.20000101-00000{index}.json").write_text("old", encoding="utf-8")
        unrelated = self.backup / "keep.txt"
        unrelated.write_text("keep", encoding="utf-8")
        with patch("time.strftime", return_value="20990101-000000"):
            self.assertEqual(storage.load_data(), json.loads(raw))
        backups = sorted(self.backup.glob("notes.*.json"))
        self.assertEqual(len(backups), 5)
        self.assertEqual(backups[0].name, "notes.20000101-000002.json")
        self.assertEqual(backups[-1].read_text(encoding="utf-8"), raw)
        self.assertTrue(unrelated.exists())

    def test_corrupt_file_is_backed_up_before_returning_defaults(self):
        self.file.write_text("invalid JSON", encoding="utf-8")
        self.assertEqual(storage.load_data()["notes"], [])
        backup = next(self.backup.glob("notes.*.json"))
        self.assertEqual(backup.read_text(encoding="utf-8"), "invalid JSON")

    def test_backup_failure_still_loads_existing_data(self):
        self.file.write_text('{"notes": []}', encoding="utf-8")
        with patch.object(storage.os, "makedirs", side_effect=OSError("read only")):
            self.assertEqual(storage.load_data(), {"notes": []})

    def test_replace_failure_preserves_previous_saved_file(self):
        storage.save_data({"notes": ["old"]})
        before = self.file.read_bytes()
        with patch.object(storage.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                storage.save_data({"notes": ["new"]})
        self.assertEqual(self.file.read_bytes(), before)
