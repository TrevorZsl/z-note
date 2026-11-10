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
        self.window.app = Mock(q=queue.Queue(), _available_update=None)
        self.window._update_cancel = threading.Event()
        self.window._update_busy = False
        self.window._waiting_for_update_check = False
        self.window._update_installing = False
        self.window._update_download = self.window._update_release = None
        self.window._update_state = "idle"
        self.window._update_button = Mock()
        self.window._update_cancel_button = Mock()
        self.window._update_status = Mock()
        self.window._update_status.pack_info.return_value = {"in": self.window._update_status.master}
        self.release = updater.Release("2.2.0", "更新说明", "setup.exe", "url", 100, "sums")
        self.confirm = self.enterContext(patch.object(main.messagebox, "askyesno", return_value=False))

    def receive(self, kind, value):
        if kind == "checked":
            self.window.app._available_update = value
        self.window._receive_update(self.window._update_cancel, kind, value)

    def test_release_download_and_ready_states(self):
        self.receive("checked", self.release)
        self.assertEqual(self.window._update_state, "found")
        self.window._update_button.configure.assert_called_with(text="下载并安装")
        self.window._update_status.configure.assert_called_with(text="可更新至 v2.2.0")
        self.receive("progress", (50, 100))
        self.assertIn("50%", self.window._update_status.configure.call_args.kwargs["text"])
        self.receive("downloaded", (Path("setup.exe"), "hash"))
        self.assertEqual(self.window._update_state, "ready")
        self.window._update_button.configure.assert_called_with(text="立即安装")
        self.confirm.assert_called_once()
        self.window.app.install_update.assert_not_called()
        self.confirm.return_value = True
        self.window._update_action()
        self.window.app.install_update.assert_called_once_with(Path("setup.exe"), self.release, "hash")

    def test_download_completion_prompts_and_installs_when_confirmed(self):
        self.receive("checked", self.release)
        self.confirm.return_value = True
        self.receive("downloaded", (Path("setup.exe"), "hash"))
        self.window.app.install_update.assert_called_once_with(Path("setup.exe"), self.release, "hash")

    def test_install_launch_failure_keeps_download_for_retry(self):
        self.receive("checked", self.release)
        self.confirm.return_value = True
        self.window.app.install_update.side_effect = OSError("启动失败")
        self.receive("downloaded", (Path("setup.exe"), "hash"))
        self.assertEqual(self.window._update_state, "ready")
        self.window._update_status.configure.assert_called_with(text="无法安装更新：启动失败")

    def test_no_update_and_error_allow_retry(self):
        self.receive("checked", None)
        self.assertEqual(self.window._update_state, "idle")
        self.window._update_status.configure.assert_called_with(text="已是最新版本。")
        self.receive("error", "网络错误")
        self.assertFalse(self.window._update_busy)
        self.window._update_status.configure.assert_called_with(text="网络错误")

    def test_manual_check_can_cancel_waiting_without_cancelling_app_check(self):
        self.window._update_action()
        self.window._update_action()
        self.window.app._check_for_update.assert_called_once_with(manual=True)
        self.window._cancel_update()
        self.assertFalse(self.window._update_busy)
        self.window._update_status.configure.assert_called_with(text="已取消更新。")
        self.window._receive_update_check("checked", self.release)
        self.window._update_status.configure.assert_called_with(text="可更新至 v2.2.0")

    def test_manual_errors_are_visible_and_background_errors_are_silent(self):
        self.window._receive_update_check("error", "连接失败")
        self.window._update_status.configure.assert_not_called()
        self.window._update_action()
        self.window._receive_update_check("error", "连接失败")
        self.window._update_status.configure.assert_called_with(text="连接失败")
        self.assertFalse(self.window._update_busy)

    def test_background_result_leaves_download_and_ready_state_intact(self):
        self.receive("checked", self.release)
        self.window._update_busy = True
        self.window.app._available_update = None
        self.window._receive_update_check("checked", None)
        self.assertIs(self.window._update_release, self.release)
        self.receive("downloaded", (Path("setup.exe"), "hash"))
        self.window._receive_update_check("checked", None)
        self.assertIs(self.window._update_release, self.release)
        self.assertEqual(self.window._update_state, "ready")

    def test_source_and_portable_modes_download_without_opening_browser(self):
        self.receive("checked", self.release)
        with patch.object(main.sys, "frozen", False, create=True), \
             patch.object(main.webbrowser, "open") as browser, patch.object(main.threading, "Thread") as thread:
            with patch.object(updater, "download_update", return_value=(Path("setup.exe"), "hash")) as download:
                self.window._update_action()
                thread.call_args.kwargs["target"]()
        browser.assert_not_called()
        download.assert_called_once()
        self.assertEqual(self.window.app.q.get_nowait()[3], "downloaded")

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
        self.start_tray = main.App._start_tray
        for item in (patch.object(main, "load_data", return_value={"settings": dict(main.DEFAULT_SETTINGS), "notes": []}),
                     patch.object(main, "save_data"), patch.object(main.App, "_start_tray"),
                     patch.object(main.App, "_start_hotkeys"), patch.object(main.App, "_start_bottom_guard")):
            self.enterContext(item)
        self.errors = []
        self.enterContext(patch.object(main.tk.Tk, "report_callback_exception",
                                     side_effect=lambda *args: self.errors.append(str(args))))
        after = main.tk.Tk.after
        self.startup_checks = []

        def capture_startup(root, delay, callback=None, *args):
            if delay == 10_000:
                self.startup_checks.append(callback)
            return after(root, delay, callback, *args)

        with patch.object(main.tk.Tk, "after", capture_startup):
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

    def test_update_section_omits_release_notes_and_wraps_status(self):
        window = self.window
        cancel = window._update_cancel = threading.Event()
        release = updater.Release("9.9.9", "改进更新体验。" * 100, "setup.exe", "url", 100, "sums")
        window._receive_update(cancel, "checked", release)
        self.app.root.update()
        status = window._update_status
        self.assertEqual(status.cget("text"), "可更新至 v9.9.9")
        self.assertEqual(window._update_button.cget("text"), "下载并安装")
        siblings = status.master.winfo_children()
        link = siblings[siblings.index(status) + 1]
        self.assertEqual(link.cget("text"), "查看发布页面 ↗")
        self.assertEqual(link.winfo_manager(), "pack")
        initial_height = status.winfo_height()
        window._receive_update(cancel, "error", "连接失败，请重试。" * 20)
        self.app.root.update()
        self.assertGreater(status.winfo_height(), initial_height)
        self.assertLessEqual(status.winfo_width(), window.winfo_width())
        self.assertEqual(window._update_button.cget("state"), "normal")

    def test_up_to_date_status_sits_beside_check_button(self):
        window = self.window
        button, status = window._update_button, window._update_status
        with patch.object(self.app, "_check_for_update"):
            button.invoke()
            window._receive_update_check("checked", None)
            self.app.root.update()
            self.assertEqual(status.cget("text"), "已是最新版本。")
            self.assertGreater(status.winfo_rootx(), button.winfo_rootx() + button.winfo_width())
            button_center = button.winfo_rooty() + button.winfo_height() / 2
            status_center = status.winfo_rooty() + status.winfo_height() / 2
            self.assertAlmostEqual(status_center, button_center, delta=1)
            self.assertEqual(status.winfo_width(), status.winfo_reqwidth())

            button.invoke()
            window._receive_update_check("error", "网络错误")
            self.app.root.update()
            self.assertEqual(status.cget("text"), "网络错误")
            self.assertGreater(status.winfo_rooty(), button.winfo_rooty() + button.winfo_height())

    def test_startup_check_uses_worker_and_remembers_result_across_settings(self):
        release = updater.Release("9.9.9", "演示说明", "setup.exe", "url", 100, "sums")
        with patch.object(main.pystray, "Icon") as icon:
            self.start_tray(self.app)
        item = next(item for item in icon.call_args.args[3].items if item.text == "设置…")
        with patch.object(main.threading, "Thread") as thread, \
             patch.object(updater, "check_update", return_value=release) as check:
            self.assertEqual(len(self.startup_checks), 1)
            self.startup_checks[0]()
            self.window._update_action()
            thread.assert_called_once()
            check.assert_not_called()
            cancel = self.app._update_check_cancel
            self.window.destroy()
            self.assertFalse(cancel.is_set())
            thread.call_args.kwargs["target"]()
            self.app._pump()
            self.app.icon.update_menu.assert_called_once()
            self.assertEqual(item.text, "设置（有更新）…")
            item(None)
            self.app._pump()
            self.window = self.app.settings_win
            self.assertEqual(self.window._update_status.cget("text"), "可更新至 v9.9.9")
            self.assertEqual(self.window._update_button.cget("text"), "下载并安装")
            thread.assert_called_once()

    def test_settings_refresh_is_throttled_and_errors_keep_known_release(self):
        release = updater.Release("9.9.9", "演示说明", "setup.exe", "url", 100, "sums")
        self.app.icon = Mock()
        with patch.object(main.threading, "Thread") as thread, patch.object(main.time, "monotonic") as clock:
            def open_settings(now):
                clock.return_value = now
                self.app.q.put("settings")
                self.app._pump()

            def finish(kind, value):
                self.app.q.put(("update_check", self.app._update_check_cancel, kind, value))
                self.app._pump()

            open_settings(0)
            finish("checked", release)
            self.startup_checks[0]()
            open_settings(86_399)
            thread.assert_called_once()
            open_settings(86_400)
            self.assertEqual(thread.call_count, 2)
            finish("error", "连接失败")
            self.assertEqual(self.window._update_status.cget("text"), "可更新至 v9.9.9")
            self.app.icon.update_menu.assert_called_once()
            open_settings(172_800)
            finish("checked", None)
            self.assertEqual(self.window._update_button.cget("text"), "检查更新")
            self.assertEqual(self.app.icon.update_menu.call_count, 2)
            self.window._update_action()
            self.assertEqual(thread.call_count, 4)
