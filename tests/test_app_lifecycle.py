"""Normal startup, tray dispatch and graceful exit regressions."""
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import main


class AppLifecycleTests(unittest.TestCase):
    def update_app(self):
        app = object.__new__(main.App)
        app.notes = {"note": Mock(d={}, _ime_active=False, _resizing=False, _title_editor=None,
                                  winfo_x=Mock(return_value=12), winfo_y=Mock(return_value=34))}
        app.data = {"notes": [app.notes["note"].d]}
        app.settings_win = Mock(_update_installing=False)
        self.enterContext(patch.object(main.SettingsWindow, "_is_installed", return_value=True))
        return app

    def test_update_saves_and_backs_up_before_starting_installer_and_quitting(self):
        app = self.update_app()
        order = Mock()
        with patch.object(main.updater, "verify_installer") as verify, \
             patch.object(main, "save_data") as save, \
             patch.object(main, "backup_before_update") as backup, \
             patch.object(main.subprocess, "Popen") as start, \
             patch.object(app, "quit") as quit:
            for name, action in (("verify", verify), ("sync", app.notes["note"]._sync_lines_from_text),
                                 ("save", save), ("backup", backup), ("start", start), ("quit", quit)):
                order.attach_mock(action, name)
            app.install_update(Path("setup.exe"), "release", "hash")
        self.assertEqual(order.mock_calls, [call.verify(Path("setup.exe"), "release", "hash"), call.sync(),
                                          call.save(app.data), call.backup(),
                                          call.start(["setup.exe"], cwd="."), call.quit(saved=True)])
        self.assertEqual(app.notes["note"].d, {"x": 12, "y": 34})
        self.assertTrue(app.settings_win._update_installing)

    def test_update_failures_leave_application_running(self):
        for failure in ("verify", "save", "backup", "start"):
            with self.subTest(failure=failure):
                app = self.update_app()
                with patch.object(main.updater, "verify_installer") as verify, \
                     patch.object(main, "save_data") as save, \
                     patch.object(main, "backup_before_update") as backup, \
                     patch.object(main.subprocess, "Popen") as start, patch.object(app, "quit") as quit:
                    actions = {"verify": verify, "save": save, "backup": backup, "start": start}
                    actions[failure].side_effect = OSError("failure")
                    with self.assertRaises(OSError):
                        app.install_update(Path("setup.exe"), "release", "hash")
                    quit.assert_not_called()
                    if failure != "start":
                        start.assert_not_called()
                    self.assertFalse(app.settings_win._update_installing)

    def test_update_during_ime_composition_does_not_save_or_install(self):
        app = self.update_app()
        app.notes["note"]._ime_active = True
        with patch.object(main.updater, "verify_installer"), patch.object(main, "save_data") as save, \
             patch.object(main.subprocess, "Popen") as start:
            with self.assertRaisesRegex(main.updater.UpdateError, "输入法"):
                app.install_update(Path("setup.exe"), "release", "hash")
        save.assert_not_called()
        start.assert_not_called()

    def test_saved_update_shutdown_does_not_resave_hidden_resize_source(self):
        app = self.update_app()
        app.icon, app.root = Mock(), Mock()
        with patch.object(app, "_save_positions") as positions, patch.object(app, "save_now") as save, \
             patch.object(main.os, "_exit", side_effect=SystemExit(0)):
            with self.assertRaises(SystemExit):
                app.quit(saved=True)
        positions.assert_not_called()
        save.assert_not_called()

    def test_bottom_events_are_coalesced_and_stopped_after_shutdown(self):
        app = object.__new__(main.App)
        app.q = main.queue.Queue()
        app._quitting = app._bottom_refresh_pending = False
        app._bottom_hooks = [11, 12]
        app._bottom_reference_hwnd = 13
        app._request_bottom_refresh()
        app._request_bottom_refresh()
        self.assertEqual(app.q.get_nowait(), "refresh_bottom")
        self.assertTrue(app.q.empty())
        with patch.object(main.user32, "UnhookWinEvent") as unhook, \
             patch.object(main.user32, "DestroyWindow") as destroy:
            app._stop_bottom_guard()
            app._stop_bottom_guard()
        self.assertEqual(unhook.call_args_list, [call(11), call(12)])
        destroy.assert_called_once_with(13)
        self.assertIsNone(app._bottom_reference_hwnd)
        app._bottom_refresh_pending = False
        app._request_bottom_refresh()
        self.assertTrue(app.q.empty())
        app.notes = {"note": Mock()}
        app._refresh_bottom_notes()
        self.assertEqual(app.notes["note"].mock_calls, [])

    def test_partial_bottom_hook_installation_is_cleaned_up(self):
        app = object.__new__(main.App)
        app._bottom_hooks = []
        with patch.object(main.user32, "SetWinEventHook", side_effect=[11, 0]), \
             patch.object(main.user32, "UnhookWinEvent") as unhook:
            with self.assertRaises(OSError):
                app._start_bottom_guard()
        unhook.assert_called_once_with(11)
        self.assertEqual(app._bottom_hooks, [])

    def test_bottom_anchor_stays_above_desktop_and_below_remaining_windows(self):
        # The desktop can be above windows hidden by Show Desktop. Such
        # windows must not become the anchor or the note stays behind it.
        classes = {10: "OtherApp", 20: "Note", 30: "Progman", 40: "OtherApp"}
        def class_name(hwnd, buffer, length):
            buffer.value = classes[hwnd]
            return len(buffer.value)
        with patch.object(main.user32, "GetTopWindow", return_value=10), \
             patch.object(main.user32, "GetWindow", side_effect=lambda hwnd, _: {10: 20, 20: 30, 30: 40, 40: 0}[hwnd]), \
             patch.object(main.user32, "IsWindowVisible", return_value=True), \
             patch.object(main.user32, "IsIconic", return_value=False), \
             patch.object(main.user32, "GetWindowLongPtrW", return_value=0), \
             patch.object(main.user32, "GetClassNameW", side_effect=class_name):
            self.assertEqual(main._bottom_insert_after(20), 10)
            with patch.object(main.user32, "GetTopWindow", return_value=30):
                self.assertEqual(main._bottom_insert_after(20), 0)
            with patch.object(main.user32, "GetTopWindow", return_value=0):
                self.assertEqual(main._bottom_insert_after(20), 1)

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
        note = Mock(d={}, _resizing=False, winfo_x=Mock(return_value=12), winfo_y=Mock(return_value=34))
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

    def test_exit_finishes_resize_before_saving_without_reading_hidden_source_position(self):
        app = object.__new__(main.App)
        note = Mock(d={"x": 120, "y": 130}, _resizing=True, _resize_cancel_requested=False)
        app.notes = {"note": note}
        app.icon, app.root = Mock(), Mock()
        order = Mock()
        with patch.object(app, "save_now") as save, \
             patch.object(main.os, "_exit", side_effect=SystemExit(0)):
            order.attach_mock(note._finish_resize, "finish")
            order.attach_mock(save, "save")
            with self.assertRaises(SystemExit):
                app.quit()
        self.assertEqual(order.mock_calls, [call.finish(cancel=False, redraw=False, hidden=True), call.save()])
        note.winfo_x.assert_not_called()
        note.winfo_y.assert_not_called()
        self.assertEqual(note.d, {"x": 120, "y": 130})
