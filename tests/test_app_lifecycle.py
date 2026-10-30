"""Normal startup, tray dispatch and graceful exit regressions."""
import unittest
from unittest.mock import Mock, call, patch

import main


class AppLifecycleTests(unittest.TestCase):
    def test_duplicate_instance_keeps_notification_and_exits(self):
        with patch.object(main.kernel32, "CreateMutexW"), \
             patch.object(main.ctypes, "get_last_error", return_value=183), \
             patch.object(main.tk, "Tk") as root, \
             patch.object(main.messagebox, "showinfo") as dialog, \
             patch.object(main.os, "_exit", side_effect=SystemExit(0)) as exit_process:
            with self.assertRaises(SystemExit):
                main.ensure_single_instance()
        root.return_value.withdraw.assert_called_once_with()
        dialog.assert_called_once()
        exit_process.assert_called_once_with(0)

    def test_normal_instance_starts_without_notification(self):
        with patch.object(main.kernel32, "CreateMutexW"), \
             patch.object(main.ctypes, "get_last_error", return_value=0), \
             patch.object(main.messagebox, "showinfo") as dialog, \
             patch.object(main.os, "_exit") as exit_process:
            main.ensure_single_instance()
        dialog.assert_not_called()
        exit_process.assert_not_called()

    def test_tray_actions_are_dispatched_and_polling_continues(self):
        app = object.__new__(main.App)
        app.q = main.queue.Queue()
        app.root = Mock()
        for action in ("new", "hide", "show"):
            app.q.put(action)
        with patch.object(app, "new_note") as new, \
             patch.object(app, "hide_all") as hide, \
             patch.object(app, "show_all") as show:
            app._pump()
        for action in (new, hide, show):
            action.assert_called_once_with()
        app.root.after.assert_called_once_with(120, app._pump)

    def test_exit_saves_positions_before_destroying_ui_and_exiting(self):
        app = object.__new__(main.App)
        note = Mock(d={}, winfo_x=Mock(return_value=12), winfo_y=Mock(return_value=34))
        app.notes = {"note": note}
        app.icon, app.root = Mock(), Mock()
        order = Mock()
        with patch.object(app, "save_now") as save, \
             patch.object(main.os, "_exit", side_effect=SystemExit(0)) as exit_process:
            for name, callback in (("save", save), ("stop", app.icon.stop),
                                   ("destroy", app.root.destroy), ("exit", exit_process)):
                order.attach_mock(callback, name)
            with self.assertRaises(SystemExit):
                app.quit()
        self.assertEqual(note.d, {"x": 12, "y": 34})
        self.assertEqual(order.mock_calls, [call.save(), call.stop(), call.destroy(), call.exit(0)])
