"""Manual updates from the public release repositories."""
from dataclasses import dataclass, replace
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from version import APP_VERSION


RELEASE_REPO = "TrevorZsl/z-note-releases"
GITEE_RELEASE_REPO = "trevorzsl/z-note-releases"
RELEASES_URL = f"https://github.com/{RELEASE_REPO}/releases"
API_URL = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
TIMEOUT = 15
MAX_INSTALLER_SIZE = 512 * 1024 * 1024


class UpdateError(Exception):
    pass


class Cancelled(UpdateError):
    pass


def parse_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value):
        raise UpdateError("发布版本号无效，应为 v主版本.次版本.修订号。")
    return tuple(map(int, value.removeprefix("v").split(".")))


def _cancelled(cancel):
    if cancel.is_set():
        raise Cancelled("已取消更新。")


class _GitHubRedirects(HTTPRedirectHandler):
    hosts = {"github.com", "release-assets.githubusercontent.com",
             "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
    source = "GitHub"

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if (url.scheme != "https" or url.username or url.password or url.port not in (None, 443)
                or url.hostname not in self.hosts):
            raise UpdateError(f"更新下载被重定向到非 {self.source} 地址，已停止。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _GiteeRedirects(_GitHubRedirects):
    hosts = {"gitee.com", "foruda.gitee.com"}
    source = "Gitee"


def _open(url):
    parsed = urlsplit(url)
    gitee = parsed.hostname == "gitee.com"
    redirects = _GiteeRedirects() if gitee else _GitHubRedirects()
    accept = "application/octet-stream"
    if url == API_URL:
        accept = "application/vnd.github+json"
    elif gitee and parsed.path.startswith("/api/v5/"):
        accept = "application/json"
    request = Request(url, headers={"User-Agent": f"z-note/{APP_VERSION}",
                                    "Accept": accept})
    try:
        return build_opener(redirects).open(request, timeout=TIMEOUT)
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError(f"{redirects.source} 请求受限，请稍后重试。") from exc
        if exc.code == 404:
            raise UpdateError("尚无可用的正式版本，或发布文件不存在。") from exc
        raise UpdateError(f"更新服务返回错误（HTTP {exc.code}），请稍后重试。") from exc
    except (URLError, OSError) as exc:
        raise UpdateError(f"无法连接 {redirects.source}，请检查网络后重试。") from exc


def _read_small(url, limit, cancel):
    _cancelled(cancel)
    with _open(url) as response:
        value = response.read(limit + 1)
    _cancelled(cancel)
    if len(value) > limit:
        raise UpdateError("更新信息过大，已停止。")
    return value


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    installer_name: str
    installer_url: str
    size: int
    checksum_url: str


def check_update(cancel, current=APP_VERSION):
    if GITEE_RELEASE_REPO:
        try:
            release = _check_gitee(cancel, current)
            if release is not None:
                return release
        except Cancelled:
            raise
        except UpdateError:
            return _check_github(cancel, current)
        # GitHub may already have a newer formal release while Gitee is still in preview.
        try:
            return _check_github(cancel, current)
        except Cancelled:
            raise
        except UpdateError:
            return None
    return _check_github(cancel, current)


def _check_gitee(cancel, current):
    api = f"https://gitee.com/api/v5/repos/{GITEE_RELEASE_REPO}/releases"
    try:
        releases = json.loads(_read_small(api + "?per_page=30", 1024 * 1024, cancel))
        if not isinstance(releases, list):
            raise ValueError("not a release list")
        stable = [(parse_version(item["tag_name"]), item) for item in releases
                  if item.get("prerelease") is False and not item.get("draft")]
        if not stable:
            return None
        version, data = max(stable, key=lambda item: item[0])
        if version <= parse_version(current):
            return None
        tag = data["tag_name"]
        release_id = data["id"]
        if type(release_id) is not int or release_id <= 0:
            raise ValueError("invalid release ID")
        assets = json.loads(_read_small(f"{api}/{release_id}/attach_files", 1024 * 1024, cancel))
        base = f"https://gitee.com/{GITEE_RELEASE_REPO}/releases/download/{tag}"
        return _release_from_assets(data, assets, base, gitee=True)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise UpdateError("Gitee 更新信息格式无效，请联系发布者。") from exc
    except (OSError, URLError, HTTPException) as exc:
        raise UpdateError("读取 Gitee 更新信息失败，请检查网络后重试。") from exc


def _check_github(cancel, current):
    try:
        data = json.loads(_read_small(API_URL, 1024 * 1024, cancel))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        if data.get("draft") or data.get("prerelease"):
            return None
        tag = data["tag_name"]
        version = parse_version(tag)
        if version <= parse_version(current):
            return None
        base = f"https://github.com/{RELEASE_REPO}/releases/download/{tag}"
        return _release_from_assets(data, data["assets"], base)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise UpdateError("更新信息格式无效，请联系发布者。") from exc
    except (OSError, URLError, HTTPException) as exc:
        raise UpdateError("读取更新信息失败，请检查网络后重试。") from exc


def _release_from_assets(data, assets, base, *, gitee=False):
    number = data["tag_name"].removeprefix("v")
    name = f"z-note-setup-{number}.exe"
    sizes = []
    for expected, limit in ((name, MAX_INSTALLER_SIZE), ("SHA256SUMS", 65536)):
        matches = [asset for asset in assets if asset.get("name") == expected]
        if len(matches) != 1:
            raise UpdateError(f"新版缺少有效的 {expected}，请联系发布者。")
        asset = matches[0]
        if not gitee and asset["browser_download_url"] != f"{base}/{expected}":
            raise UpdateError("发布文件地址不属于指定的更新仓库。")
        size = asset["size"]
        if type(size) is not int or not 0 < size <= limit:
            raise UpdateError("发布文件大小无效。")
        sizes.append(size)
    notes = data.get("body") or "此版本暂无更新说明。"
    if not isinstance(notes, str):
        raise ValueError("invalid release notes")
    return Release(number, notes, name, f"{base}/{name}", sizes[0], f"{base}/SHA256SUMS")


def verify_installer(path, release, expected_hash):
    if path.stat().st_size != release.size:
        raise UpdateError("安装包大小不匹配，请重新下载。")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected_hash:
        raise UpdateError("安装包校验失败，请重新下载。")


def remove_download(path):
    """Only remove the file and its now-empty private temporary directory."""
    if path is not None:
        try:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            pass


def download_update(release, cancel, progress):
    try:
        return _download(release, cancel, progress)
    except Cancelled:
        raise
    except UpdateError:
        if urlsplit(release.installer_url).hostname != "gitee.com":
            raise
        base = f"https://github.com/{RELEASE_REPO}/releases/download/v{release.version}"
        fallback = replace(release, installer_url=f"{base}/{release.installer_name}",
                           checksum_url=f"{base}/SHA256SUMS")
        _cancelled(cancel)
        progress(0, release.size)
        return _download(fallback, cancel, progress)


def _download(release, cancel, progress):
    destination = None
    partial = None
    try:
        sums = _read_small(release.checksum_url, 65536, cancel).decode("utf-8")
        hashes = re.findall(r"^([a-fA-F0-9]{64}) [ *]" + re.escape(release.installer_name) + r"\r?$",
                            sums, re.MULTILINE)
        if len(hashes) != 1:
            raise UpdateError("SHA256SUMS 中缺少有效的安装包校验值。")
        expected_hash = hashes[0].lower()
        destination = Path(tempfile.mkdtemp(prefix="z-note-update-")) / release.installer_name
        partial = destination.with_suffix(".exe.part")
        downloaded, last_report = 0, 0
        deadline = time.monotonic() + 1800
        with _open(release.installer_url) as response, partial.open("xb") as output:
            while True:
                _cancelled(cancel)
                if time.monotonic() > deadline:
                    raise UpdateError("下载超时，请重试。")
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > release.size:
                    raise UpdateError("安装包大小超出发布信息，已停止。")
                output.write(chunk)
                now = time.monotonic()
                if now - last_report >= 0.2:
                    progress(downloaded, release.size)
                    last_report = now
        _cancelled(cancel)
        verify_installer(partial, release, expected_hash)
        partial.replace(destination)
        _cancelled(cancel)
        progress(release.size, release.size)
        return destination, expected_hash
    except (OSError, URLError, UnicodeError, HTTPException) as exc:
        raise UpdateError("下载失败，请检查网络及磁盘空间后重试。") from exc
    finally:
        # A failed or cancelled download never leaves an executable behind.
        if partial is not None:
            partial.unlink(missing_ok=True)
        if destination is not None and (cancel.is_set() or not destination.exists()):
            remove_download(destination)
