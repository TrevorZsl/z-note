"""Update protocol tests never contact GitHub or run an installer."""
from copy import deepcopy
import hashlib
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import updater


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent))
        self.enterContext(patch.object(tempfile, "tempdir", root))
        self.cancel = threading.Event()
        self.content = b"fake installer for download tests"
        self.hash = hashlib.sha256(self.content).hexdigest()
        prefix = f"https://github.com/{updater.RELEASE_REPO}/releases/download/v2.10.0/"
        self.data = {
            "tag_name": "v2.10.0", "draft": False, "prerelease": False, "body": "更新说明",
            "assets": [{"name": "z-note-setup-2.10.0.exe", "size": len(self.content),
                        "browser_download_url": prefix + "z-note-setup-2.10.0.exe"},
                       {"name": "SHA256SUMS", "size": 100, "browser_download_url": prefix + "SHA256SUMS"}]}

    def check(self, data=None, current="2.9.0"):
        raw = json.dumps(self.data if data is None else data).encode()
        with patch.object(updater, "_open", return_value=io.BytesIO(raw)):
            return updater.check_update(self.cancel, current)

    def test_numeric_versions_and_invalid_formats(self):
        self.assertGreater(updater.parse_version("v2.10.0"), updater.parse_version("2.9.0"))
        for version in ("2.1", "v2.1.0-beta", "2.01.0", "2.1.0/evil", "2.1２.0", None, 210):
            with self.subTest(version=version), self.assertRaises(updater.UpdateError):
                updater.parse_version(version)

    def test_latest_stable_release_is_selected(self):
        release = self.check()
        self.assertEqual(release.version, "2.10.0")
        self.assertEqual(release.notes, "更新说明")
        self.assertEqual(release.size, len(self.content))

    def test_equal_older_draft_and_prerelease_do_not_update(self):
        for current in ("2.10.0", "3.0.0"):
            self.assertIsNone(self.check(current=current))
        for flag in ("draft", "prerelease"):
            data = deepcopy(self.data)
            data[flag] = True
            self.assertIsNone(self.check(data))

    def test_missing_duplicate_and_foreign_assets_are_rejected(self):
        cases = []
        missing = deepcopy(self.data)
        missing["assets"].pop()
        cases.append(missing)
        duplicate = deepcopy(self.data)
        duplicate["assets"].append(duplicate["assets"][0])
        cases.append(duplicate)
        foreign = deepcopy(self.data)
        foreign["assets"][0]["browser_download_url"] = "https://example.com/setup.exe"
        cases.append(foreign)
        invalid_size = deepcopy(self.data)
        invalid_size["assets"][0]["size"] = True
        cases.append(invalid_size)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(updater.UpdateError):
                self.check(data)

    def test_malformed_and_oversized_release_information(self):
        for raw in (b"not json", b"[]", b"{}", b"x" * (1024 * 1024 + 1)):
            with patch.object(updater, "_open", return_value=io.BytesIO(raw)), self.assertRaises(updater.UpdateError):
                updater.check_update(self.cancel)

    def test_network_errors_have_actionable_messages(self):
        cases = [(URLError("offline"), "网络"), (TimeoutError(), "网络")]
        cases.extend((HTTPError(updater.API_URL, code, "error", {}, None), message)
                     for code, message in ((403, "受限"), (429, "受限"), (404, "正式版本"), (500, "HTTP 500")))
        for error, message in cases:
            with self.subTest(error=error), patch.object(updater, "build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaisesRegex(updater.UpdateError, message):
                    updater.check_update(self.cancel)

    def test_redirects_cannot_leave_github_https(self):
        handler = updater._GitHubRedirects()
        for url in ("http://github.com/file", "https://evil.example/file", "https://github.com.evil.example/file",
                    "https://user@github.com/file", "https://github.com:8443/file", "file:///tmp/setup.exe"):
            with self.subTest(url=url), self.assertRaises(updater.UpdateError):
                handler.redirect_request(Request(updater.API_URL), None, 302, "redirect", {}, url)
        for host in ("release-assets.githubusercontent.com", "objects.githubusercontent.com"):
            url = f"https://{host}/asset?signature=example"
            redirected = handler.redirect_request(Request(updater.API_URL), None, 302, "redirect", {}, url)
            self.assertEqual(redirected.full_url, url)

    def download(self, release, content=None, checksum=None, progress=None):
        sums = f"{self.hash if checksum is None else checksum}  {release.installer_name}\n".encode()
        with patch.object(updater, "_open", side_effect=[io.BytesIO(sums), io.BytesIO(self.content if content is None else content)]):
            return updater.download_update(release, self.cancel, progress or Mock())

    def test_complete_download_is_verified_and_can_be_reverified(self):
        release = self.check()
        path, checksum = self.download(release)
        self.addCleanup(updater.remove_download, path)
        self.assertEqual(path.read_bytes(), self.content)
        self.assertFalse(path.with_suffix(".exe.part").exists())
        updater.verify_installer(path, release, checksum)
        path.write_bytes(b"X" * release.size)
        with self.assertRaisesRegex(updater.UpdateError, "校验失败"):
            updater.verify_installer(path, release, checksum)

    def test_failed_downloads_remove_partial_files_and_directory(self):
        release = self.check()
        for content, checksum, message in ((b"short", self.hash, "大小"),
                                           (self.content + b"extra", self.hash, "大小"),
                                           (self.content, "0" * 64, "校验")):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as root:
                folder = Path(root) / "download"
                folder.mkdir()
                with patch.object(updater.tempfile, "mkdtemp", return_value=str(folder)):
                    with self.assertRaisesRegex(updater.UpdateError, message):
                        self.download(release, content, checksum)
                self.assertFalse(folder.exists())

    def test_cancelled_download_removes_executable(self):
        release = self.check()
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "download"
            folder.mkdir()
            with patch.object(updater.tempfile, "mkdtemp", return_value=str(folder)):
                with self.assertRaises(updater.Cancelled):
                    self.download(release, progress=lambda *_: self.cancel.set())
            self.assertFalse(folder.exists())

    def test_cancel_before_check_does_not_contact_network(self):
        self.cancel.set()
        with patch.object(updater, "_open") as network, self.assertRaises(updater.Cancelled):
            updater.check_update(self.cancel)
        network.assert_not_called()

    def test_missing_checksum_never_downloads_executable(self):
        release = self.check()
        with patch.object(updater, "_open", return_value=io.BytesIO(b"invalid checksums")) as network:
            with self.assertRaisesRegex(updater.UpdateError, "校验值"):
                updater.download_update(release, self.cancel, Mock())
            self.assertEqual(network.call_count, 1)

    def test_interrupted_response_reports_network_error(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = IncompleteRead(b"partial")
        with patch.object(updater, "_open", return_value=response):
            with self.assertRaisesRegex(updater.UpdateError, "网络"):
                updater.check_update(self.cancel)
