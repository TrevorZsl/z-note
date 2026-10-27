"""Development reload exits through the app's normal save path."""
import unittest
from unittest.mock import Mock, patch

import dev
import main


class DevReloadTests(unittest.TestCase):
    def test_launcher_waits_for_existing_instance_then_starts(self):
        results = [Mock(returncode=76), Mock(returncode=75), Mock(returncode=0)]
        with patch.object(dev.subprocess, "run", side_effect=results) as launch, \
             patch.object(dev.time, "sleep") as sleep, \
             patch.object(dev, "close_legacy_note", return_value=True) as close:
            dev.main()
        self.assertEqual(launch.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        close.assert_called_once_with()

    def test_dev_instance_requests_takeover_without_duplicate_dialog(self):
        with patch.dict(main.os.environ, {"Z_NOTE_DEV": "1"}), \
             patch.object(main.kernel32, "CreateMutexW"), \
             patch.object(main.ctypes, "get_last_error", return_value=183), \
             patch.object(main.kernel32, "OpenEventW", return_value=1), \
             patch.object(main.kernel32, "SetEvent") as signal, \
             patch.object(main.kernel32, "CloseHandle"), \
             patch.object(main.messagebox, "showinfo") as dialog, \
             patch.object(main.os, "_exit", side_effect=SystemExit(75)):
            with self.assertRaises(SystemExit):
                main.ensure_single_instance()
        signal.assert_called_once_with(1)
        dialog.assert_not_called()

    def test_old_instance_waits_without_duplicate_dialog(self):
        with patch.dict(main.os.environ, {"Z_NOTE_DEV": "1"}), \
             patch.object(main.kernel32, "CreateMutexW"), \
             patch.object(main.ctypes, "get_last_error", return_value=183), \
             patch.object(main.kernel32, "OpenEventW", return_value=0), \
             patch.object(main.messagebox, "showinfo") as dialog, \
             patch.object(main.os, "_exit", side_effect=SystemExit(76)):
            with self.assertRaises(SystemExit):
                main.ensure_single_instance()
        dialog.assert_not_called()

    def test_changed_source_requests_graceful_restart(self):
        app = object.__new__(main.App)
        app._dev_mtime = 1
        app._takeover_event = None
        with patch("main.os.path.getmtime", return_value=2), \
             patch.object(app, "quit", side_effect=SystemExit(75)) as quit_app:
            with self.assertRaises(SystemExit):
                app._pump()
        quit_app.assert_called_once_with(75)

    def test_takeover_request_exits_through_save_path(self):
        app = object.__new__(main.App)
        app._dev_mtime = None
        app._takeover_event = 1
        with patch.object(main.kernel32, "WaitForSingleObject", return_value=0), \
             patch.object(app, "quit", side_effect=SystemExit(0)) as quit_app:
            with self.assertRaises(SystemExit):
                app._pump()
        quit_app.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
