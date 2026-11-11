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
        self.window._update_details = Mock()
        self.window._update_error = None
        self.window._layout_update_status = Mock(side_effect=lambda _width:
            self.window._update_status.configure(text=self.window._update_status_choices[0]))
        self.release = updater.Release("2.2.0", "更新说明", "setup.exe", "url", 100, "sums")
        self.confirm = self.enterContext(patch.object(main.messagebox, "askyesno", return_value=False))

    def receive(self, kind, value):
        if kind == "checked":
            self.window.app._available_update = value
        self.window._receive_update(self.window._update_cancel, kind, value)

    def test_release_download_and_ready_states(self):
        self.receive("checked", self.release)
        self.assertEqual(self.window._update_state, "found")
        self.window._update_button.configure.assert_called_with(text="立即更新")
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
        self.window._update_status.configure.assert_called_with(text="安装启动失败。")
        self.assertEqual(self.window._update_error, "启动失败")

    def test_no_update_and_error_allow_retry(self):
        self.receive("checked", None)
        self.assertEqual(self.window._update_state, "idle")
        self.window._update_status.configure.assert_called_with(text="已是最新版本。")
        self.receive("error", "网络错误")
        self.assertFalse(self.window._update_busy)
        self.window._update_status.configure.assert_called_with(text="检查失败，请重试。")
        self.assertEqual(self.window._update_error, "网络错误")

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
        self.window._update_status.configure.assert_called_with(text="检查失败，请重试。")
        self.assertEqual(self.window._update_error, "连接失败")
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

    def test_long_update_error_stays_on_one_line_and_keeps_full_details(self):
        window = self.window
        cancel = window._update_cancel = threading.Event()
        release = updater.Release("9.9.9", "改进更新体验。" * 100, "setup.exe", "url", 100, "sums")
        window._receive_update(cancel, "checked", release)
        self.app.root.update()
        status = window._update_status
        self.assertEqual(status.cget("text"), "可更新至 v9.9.9")
        self.assertEqual(window._update_button.cget("text"), "立即更新")
        link = status.master.master.pack_slaves()[-1]
        self.assertEqual(link.cget("text"), "查看发布页面 ↗")
        initial_link_y = link.winfo_rooty()
        initial_height = status.winfo_height()
        error = ("Gitee：下载连接中断，请检查网络后重试。\n"
                 "GitHub：如果问题仍然存在，可以从下方的发布页面手动下载安装包。")
        window._receive_update(cancel, "error", error)
        self.app.root.update()
        details = window._update_details
        self.assertEqual(status.cget("text"), "下载失败，请重试。")
        self.assertEqual(status.winfo_height(), initial_height)
        self.assertLessEqual(status.winfo_x() + status.winfo_width(), status.master.winfo_width())
        self.assertGreater(details.winfo_x(), status.winfo_x() + status.winfo_width())
        self.assertEqual(link.winfo_rooty(), initial_link_y)
        self.assertGreater(link.winfo_rooty(), status.winfo_rooty() + status.winfo_height())
        self.assertEqual(window._update_button.cget("state"), "normal")
        status.master.master.master.yview_moveto(1)
        self.app.root.update()
        self.assertEqual(str(window._update_button.tk_focusNext()), str(details))
        details.focus_force()
        self.app.root.update()
        with patch.object(main.messagebox, "showerror") as show:
            details.event_generate("<KeyPress-Return>")
            show.assert_called_once_with("更新详情", error, parent=window)
        with patch.object(main.threading, "Thread"):
            window._update_button.invoke()
        self.assertEqual(details.winfo_manager(), "")
        self.assertIsNone(window._update_error)

    def test_update_states_share_right_side_and_keep_release_link_in_place(self):
        window = self.window
        button, status = window._update_button, window._update_status
        cancel = window._update_cancel_button
        link = status.master.master.pack_slaves()[-1]
        initial_link_y = link.winfo_rooty()
        self.assertEqual(initial_link_y - (button.winfo_rooty() + button.winfo_height()), 8)
        release = updater.Release("9.9.9", "演示说明", "setup.exe", "url", 100, "sums")
        self.app._available_update = release

        def check_layout(text, left_widget=button):
            self.app.root.update()
            self.assertIn(text, status.cget("text"))
            self.assertGreater(status.winfo_rootx(), left_widget.winfo_rootx() + left_widget.winfo_width())
            button_center = button.winfo_rooty() + button.winfo_height() / 2
            status_center = status.winfo_rooty() + status.winfo_height() / 2
            self.assertAlmostEqual(status_center, button_center, delta=1)
            self.assertEqual(link.winfo_rooty(), initial_link_y)

        with patch.object(self.app, "_check_for_update"), \
             patch.object(main.threading, "Thread"), \
             patch.object(main.messagebox, "askyesno", return_value=False) as confirm, \
             patch.object(updater, "remove_download"):
            check_layout("点击检查正式版本更新")
            button.invoke()
            check_layout("正在检查更新", cancel)
            cancel.invoke()
            check_layout("已取消更新")
            button.invoke()
            window._receive_update_check("checked", None)
            check_layout("已是最新版本")

            button.invoke()
            window._receive_update_check("error", "网络错误")
            check_layout("检查失败，请重试。")

            window._receive_update_check("checked", release)
            self.assertEqual(button.cget("text"), "立即更新")
            check_layout("可更新至 v9.9.9")
            button.invoke()
            check_layout("正在下载更新", cancel)
            window._receive_update(window._update_cancel, "progress", (5 * 1048576, 10 * 1048576))
            check_layout("50%（5.0 / 10.0 MB）", cancel)
            window._receive_update(window._update_cancel, "downloaded", (Path("setup.exe"), "hash"))
            self.assertEqual(button.cget("text"), "立即安装")
            check_layout("下载校验完成，可立即安装。")
            confirm.assert_called_once()
            confirm.return_value = True
            with patch.object(self.app, "install_update", side_effect=OSError("启动失败")):
                button.invoke()
            check_layout("安装启动失败。")
            confirm.return_value = False
            button.invoke()
            check_layout("下载校验完成，可立即安装。")
            self.assertEqual(window._update_details.winfo_manager(), "")
            window._update_download = None

        with patch.object(main.webbrowser, "open") as browser:
            link.event_generate("<Button-1>", x=1, y=1)
            browser.assert_called_once_with(updater.RELEASES_URL)

    def test_progress_keeps_most_useful_text_that_fits_narrow_windows(self):
        window = self.window
        button, status = window._update_button, window._update_status
        cancel = window._update_cancel_button
        release = updater.Release("9.9.9", "", "setup.exe", "url", 100, "sums")
        window._receive_update_check("checked", release)
        with patch.object(main.threading, "Thread"):
            button.invoke()
        window._receive_update(window._update_cancel, "progress", (5 * 1048576, 10 * 1048576))
        fixed_width = button.winfo_reqwidth() + cancel.winfo_reqwidth() + 16
        fixed_width += window.winfo_width() - status.master.winfo_width()
        measure = window._update_status_font.measure
        height = window.winfo_height()
        for expected in ("50%（5.0 / 10.0 MB）", "50%"):
            width = fixed_width + measure(expected) + 2
            window._lock_size(width, height)
            window.geometry(f"{width}x{height}")
            self.app.root.update()
            self.assertEqual(status.cget("text"), expected)
            self.assertLessEqual(status.winfo_x() + status.winfo_width(), status.master.winfo_width())

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
            self.assertEqual(self.window._update_button.cget("text"), "立即更新")
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
