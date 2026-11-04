"""Update state, main-thread dispatch and real Tk layout regressions."""
from pathlib import Path
import queue
import threading
import unittest
from unittest.mock import Mock, patch

import main
import updater


class UpdateStateTests(unittest.TestCase):
    def setUp(self):
        self.window = object.__new__(main.SettingsWindow)
        self.window.app = Mock(q=queue.Queue())
        self.window._update_cancel = threading.Event()
        self.window._update_busy = False
        self.window._update_installing = False
        self.window._update_download = self.window._update_release = None
        self.window._update_state = "idle"
        self.window._update_button = Mock()
        self.window._update_cancel_button = Mock()
        self.window._update_status = Mock()
        self.window._update_notes = Mock()
        self.release = updater.Release("2.2.0", "更新说明", "setup.exe", "url", 100, "sums")
        self.enterContext(patch.object(main.SettingsWindow, "_is_installed", return_value=True))

    def receive(self, kind, value):
        self.window._receive_update(self.window._update_cancel, kind, value)

    def test_release_download_and_ready_states(self):
        self.receive("checked", self.release)
        self.assertEqual(self.window._update_state, "found")
        self.window._update_button.configure.assert_called_with(text="下载并安装")
        self.window._update_notes.configure.assert_called_with(text="更新说明")
        self.receive("progress", (50, 100))
        self.assertIn("50%", self.window._update_status.configure.call_args.kwargs["text"])
        self.receive("downloaded", (Path("setup.exe"), "hash"))
        self.assertEqual(self.window._update_state, "ready")
        self.window._update_button.configure.assert_called_with(text="立即安装")
        self.window._update_action()
        self.window.app.install_update.assert_called_once_with(Path("setup.exe"), self.release, "hash")

    def test_no_update_and_error_allow_retry(self):
        self.receive("checked", None)
        self.assertEqual(self.window._update_state, "idle")
        self.window._update_status.configure.assert_called_with(text="已是最新版本。")
        self.receive("error", "网络错误")
        self.assertFalse(self.window._update_busy)
        self.window._update_status.configure.assert_called_with(text="网络错误")

    def test_check_runs_in_worker_and_duplicate_click_is_ignored(self):
        with patch.object(main.threading, "Thread") as thread, patch.object(updater, "check_update", return_value=self.release) as check:
            self.window._update_action()
            self.window._update_action()
            thread.assert_called_once()
            check.assert_not_called()
            thread.call_args.kwargs["target"]()
        event = self.window.app.q.get_nowait()
        self.assertEqual(event, ("update", self.window, self.window._update_cancel, "checked", self.release))

    def test_source_mode_opens_release_page_instead_of_downloading(self):
        self.window._update_state = "found"
        with patch.object(main.SettingsWindow, "_is_installed", return_value=False), \
             patch.object(main.webbrowser, "open") as browser, patch.object(main.threading, "Thread") as thread:
            self.window._update_action()
        browser.assert_called_once_with(updater.RELEASES_URL)
        thread.assert_not_called()

    def test_cancellation_wins_over_queued_completion(self):
        self.window._update_cancel.set()
        with patch.object(updater, "remove_download") as remove:
            self.receive("downloaded", (Path("setup.exe"), "hash"))
        remove.assert_called_once_with(Path("setup.exe"))
        self.assertNotEqual(self.window._update_state, "ready")
        self.assertFalse(self.window._update_busy)

    def test_destroy_cancels_and_cleans_up_except_after_installer_launch(self):
        self.window._update_download = (Path("setup.exe"), "hash")
        with patch.object(updater, "remove_download") as remove:
            self.window._destroy_update(Mock(widget=self.window))
            self.assertTrue(self.window._update_cancel.is_set())
            remove.assert_called_once_with(Path("setup.exe"))
            remove.reset_mock()
            self.window._update_installing = True
            self.window._destroy_update(Mock(widget=self.window))
            remove.assert_not_called()

    def test_closed_window_completion_is_cleaned_on_main_thread(self):
        app = object.__new__(main.App)
        app.root = Mock()
        app.q = queue.Queue()
        window = Mock(winfo_exists=Mock(return_value=False))
        app.q.put(("update", window, self.window._update_cancel, "downloaded", (Path("setup.exe"), "hash")))
        with patch.object(updater, "remove_download") as remove:
            app._pump()
        window._receive_update.assert_not_called()
        remove.assert_called_once_with(Path("setup.exe"))


class UpdateLayoutTests(unittest.TestCase):
    def setUp(self):
        for item in (patch.object(main, "load_data", return_value={"settings": dict(main.DEFAULT_SETTINGS), "notes": []}),
                     patch.object(main, "save_data"), patch.object(main.App, "_start_tray"),
                     patch.object(main.App, "_start_hotkeys"), patch.object(main.App, "_start_bottom_guard")):
            self.enterContext(item)
        self.errors = []
        self.enterContext(patch.object(main.tk.Tk, "report_callback_exception",
                                     side_effect=lambda *args: self.errors.append(str(args))))
        self.app = main.App()
        self.addCleanup(self.close)
        self.window = main.SettingsWindow(self.app)
        self.app.settings_win = self.window
        self.app.root.update()

    def close(self):
        self.window.destroy()
        for note in self.app.notes.values():
            note.destroy()
        for job in self.app.root.tk.call("after", "info"):
            self.app.root.tk.call("after", "cancel", job)
        self.app.root.destroy()
        self.assertFalse(self.errors, self.errors)

    def test_update_section_wraps_and_expands_scroll_region(self):
        window = self.window
        cancel = window._update_cancel = threading.Event()
        release = updater.Release("2.2.0", "改进更新体验。" * 100, "setup.exe", "url", 100, "sums")
        window._receive_update(cancel, "checked", release)
        self.app.root.update()
        self.assertEqual(window._update_button.cget("text"), "前往下载")
        self.assertGreater(window._update_notes.winfo_height(), window._update_status.winfo_height())
        self.assertGreater(int(window._update_notes.cget("wraplength")), 0)
        self.assertLessEqual(window._update_notes.winfo_width(), window.winfo_width())
        self.assertEqual(window._update_notes.cget("bg"), main.NOTE_BG)
        window._receive_update(cancel, "error", "连接失败，请重试。")
        self.app.root.update()
        self.assertEqual(window._update_button.cget("state"), "normal")
