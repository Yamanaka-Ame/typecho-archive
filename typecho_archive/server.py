"""本地服务：把归档数据以只读 API + 界面提供出来，并可选地开一个独立窗口。"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import export as export_mod
from .store import Archive

UI_DIR = Path(__file__).parent / "ui"


def _find_browser() -> str | None:
    """找一个 Chromium 内核浏览器，用来开无地址栏的应用窗口。"""
    candidates = [
        os.environ.get("PROGRAMFILES", "") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("PROGRAMFILES(X86)", "") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Microsoft\Edge\Application\msedge.exe",
        os.environ.get("PROGRAMFILES", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("PROGRAMFILES(X86)", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    for name in ("msedge.exe", "chrome.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ArchiveServer:
    """封装 HTTP 服务，便于嵌入或测试。"""

    def __init__(self, archive: Archive, *, host: str = "127.0.0.1", port: int = 0,
                 export_root: Path | None = None):
        self.archive = archive
        self.host = host
        self.port = port or _free_port()
        self.export_root = export_root or (Path(tempfile.gettempdir()) / "typecho-archive-exports")
        self.export_root.mkdir(parents=True, exist_ok=True)
        self.httpd: ThreadingHTTPServer | None = None
        self._payload: bytes | None = None
        self._records: bytes | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 数据缓存
    def payload(self) -> bytes:
        if self._payload is None:
            data = self.archive.payload()
            self._payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return self._payload

    def record_index(self) -> bytes:
        if self._records is None:
            self._records = json.dumps(self.archive.record_index(),
                                       ensure_ascii=False).encode("utf-8")
        return self._records

    # ------------------------------------------------------------ 服务
    def serve_forever(self) -> None:
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        self.httpd.serve_forever()

    def start_background(self) -> ThreadingHTTPServer:
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        return self.httpd

    def shutdown(self) -> None:
        if self.httpd:
            self.httpd.shutdown()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "TypechoArchive/1.0"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # 静默
                pass

            # -- 工具 --------------------------------------------------
            def _send(self, body: bytes, ctype: str, code: int = 200,
                      extra: dict | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _json(self, obj, code: int = 200) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self._send(body, "application/json; charset=utf-8", code)

            def _file(self, path: Path, ctype: str | None = None,
                      download: str | None = None) -> None:
                if not path.is_file():
                    self._json({"error": "文件不存在"}, 404)
                    return
                data = path.read_bytes()
                ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                extra = {}
                if download:
                    from urllib.parse import quote
                    extra["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(download)}"
                self._send(data, ctype, 200, extra)

            # -- 路由 --------------------------------------------------
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                route = parsed.path
                query = parse_qs(parsed.query)

                if route in ("/", "/index.html"):
                    self._file(UI_DIR / "index.html", "text/html; charset=utf-8")
                elif route == "/app.css":
                    self._file(UI_DIR / "app.css", "text/css; charset=utf-8")
                elif route == "/app.js":
                    self._file(UI_DIR / "app.js", "application/javascript; charset=utf-8")
                elif route == "/api/archive":
                    self._send(server.payload(), "application/json; charset=utf-8")
                elif route == "/api/records":
                    self._send(server.record_index(), "application/json; charset=utf-8")
                elif route.startswith("/api/record/"):
                    try:
                        seq = int(route.rsplit("/", 1)[-1])
                    except ValueError:
                        self._json({"error": "序号无效"}, 400)
                        return
                    rec = server.archive.raw_record(seq)
                    self._json(rec or {"error": "记录不存在"})
                elif route == "/api/upload":
                    self._serve_upload(query.get("p", [""])[0])
                elif route == "/api/download":
                    self._serve_download(query.get("f", [""])[0])
                elif route == "/favicon.ico":
                    self._send(b"", "image/x-icon", 204)
                else:
                    self._json({"error": "未找到"}, 404)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                if parsed.path == "/api/export":
                    self._handle_export(query.get("kind", ["bundle"])[0])
                else:
                    self._json({"error": "未找到"}, 404)

            # -- 具体动作 ----------------------------------------------
            def _serve_upload(self, rel: str) -> None:
                src = server.archive.source
                if not src or not src.uploads_dir:
                    self._json({"error": "未关联源码包"}, 404)
                    return
                base = Path(src.uploads_dir)
                target = (base / rel.replace("/usr/uploads/", "").lstrip("/\\")).resolve()
                try:
                    target.relative_to(base.resolve())
                except ValueError:
                    self._json({"error": "路径越界"}, 403)
                    return
                self._file(target)

            def _serve_download(self, rel: str) -> None:
                base = server.export_root
                target = (base / rel).resolve()
                try:
                    target.relative_to(base.resolve())
                except ValueError:
                    self._json({"error": "路径越界"}, 403)
                    return
                self._file(target, download=target.name)

            def _handle_export(self, kind: str) -> None:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                out = server.export_root / f"{kind}-{stamp}"
                arch = server.archive
                try:
                    if kind == "markdown":
                        res = export_mod.export_markdown(arch, out)
                        detail = f"{res['count']} 个 Markdown 文件"
                        target = out
                    elif kind == "sqlite":
                        out.mkdir(parents=True, exist_ok=True)
                        res = export_mod.export_sqlite(arch, out / "typecho-rebuilt.db")
                        detail = " · ".join(f"{k} {v}" for k, v in res["tables"].items())
                        target = out
                    elif kind == "json":
                        out.mkdir(parents=True, exist_ok=True)
                        export_mod.export_json(arch, out / "archive.json")
                        detail = "完整数据包"
                        target = out
                    elif kind == "csv":
                        res = export_mod.export_csv(arch, out)
                        detail = " · ".join(res["files"])
                        target = out
                    elif kind == "html":
                        from . import htmlreport
                        out.mkdir(parents=True, exist_ok=True)
                        path = htmlreport.write_static(arch, out / "archive-report.html")
                        detail = "单文件离线报告"
                        target = out
                    else:
                        res = export_mod.export_all(arch, out)
                        detail = "Markdown / SQLite / JSON / CSV"
                        target = out
                    archive_path = export_mod.zip_dir(out, server.export_root / f"{out.name}.zip")
                    self._json({
                        "path": str(archive_path),
                        "file": archive_path.name,
                        "size": archive_path.stat().st_size,
                        "detail": detail,
                        "download": "/api/download?f=" + archive_path.name,
                    })
                except Exception as exc:  # 导出失败要让界面看到原因
                    self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        return Handler


def _open_app_window(url: str) -> subprocess.Popen | None:
    """开一个没有地址栏和标签页的应用窗口，并返回其进程。"""
    browser = _find_browser()
    if not browser:
        return None
    profile = Path(tempfile.gettempdir()) / f"tarch-profile-{os.getpid()}"
    args = [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI",
        "--window-size=1440,940",
    ]
    try:
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def run(archive: Archive, *, port: int = 0, open_window: bool = True,
        export_root: Path | None = None) -> None:
    """启动服务；`open_window` 时窗口关闭后服务自动退出。"""
    server = ArchiveServer(archive, port=port, export_root=export_root)
    server.start_background()
    url = server.url
    print(f"归档台已启动：{url}")
    if not open_window:
        print("未打开窗口，请用浏览器访问上面的地址；按 Ctrl+C 退出")
    else:
        print("关闭窗口即可退出")

    proc = None
    if open_window:
        proc = _open_app_window(url)
        if proc is None:
            import webbrowser
            webbrowser.open(url)

    try:
        if proc is not None:
            proc.wait()
            print("窗口已关闭，正在退出…")
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
