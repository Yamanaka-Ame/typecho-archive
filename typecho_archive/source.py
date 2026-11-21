"""源码包扫描：从 Typecho 站点目录中提取主题、插件、配置、路由等信息。

源码包的目录结构可能随站点变动（主题、后台目录、插件都会被改名），
因此这里不依赖固定路径：先递归寻找 `config.inc.php` 定位站点根目录，
再从配置里的常量推导各子目录，最后才回退到常见的默认位置。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIRS = {".git", "__MACOSX", "node_modules", ".idea", ".vscode"}


def _safe_target(tmp: Path, name: str) -> Path | None:
    """把压缩包内成员路径映射到临时目录，越界（../、绝对路径）则拒绝。"""
    clean = name.replace("\\", "/").lstrip("/")
    if not clean or clean.startswith("../") or "/../" in clean:
        return None
    target = Path(tmp, *[p for p in clean.split("/") if p and p != "."])
    try:
        target.resolve().relative_to(tmp.resolve())
    except (ValueError, OSError):
        return None
    return target


def _make_temp_dir() -> Path:
    """挑选一个可写目录作为解包根目录。

    某些受限环境（只读挂载、受管沙箱）允许系统临时目录存在但不允许在其中
    建子目录，因此依次尝试：环境变量 → 系统临时目录 → 当前目录下的隐藏目录。
    """
    candidates: list[Path | None] = []
    env = os.environ.get("TARCH_TMPDIR")
    if env:
        candidates.append(Path(env))
    candidates.append(None)  # 系统临时目录
    candidates.append(Path.cwd() / ".tarch_tmp")
    last: Exception | None = None
    for base in candidates:
        try:
            if base is not None:
                base.mkdir(parents=True, exist_ok=True)
            # 注意：不用 tempfile.mkdtemp —— 它创建的目录在部分受管环境里
            # 不继承可写权限，直接建普通目录更稳。
            root = Path(base) if base else Path(tempfile.gettempdir())
            for _ in range(8):
                target = root / f"tarch_src_{uuid.uuid4().hex[:12]}"
                try:
                    target.mkdir()
                    return target
                except FileExistsError:
                    continue
            raise OSError(f"无法在 {root} 下创建解包目录")
        except OSError as exc:
            last = exc
            continue
    raise ValueError(f"找不到可写的临时目录：{last}")


ARCHIVE_EXTS = (".tar.gz", ".tgz", ".tar", ".zip")


def _extract_archive(path: Path, dest: Path | None = None) -> tuple[Path, bool]:
    """把 tar/tar.gz/zip 安全解到临时目录，返回 (目录, 是否临时)。"""
    tmp = dest or _make_temp_dir()
    tmp.mkdir(parents=True, exist_ok=True)
    name = path.name.lower()
    try:
        if name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(path, "r:*") as tf:
                for member in tf:
                    target = _safe_target(tmp, member.name)
                    if target is None:
                        continue
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        src = tf.extractfile(member)
                        if src is not None:
                            with src, open(target, "wb") as fh:
                                shutil.copyfileobj(src, fh)
        elif name.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for member in zf.infolist():
                    target = _safe_target(tmp, member.filename)
                    if target is None:
                        continue
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as fh:
                            shutil.copyfileobj(src, fh)
        else:
            raise ValueError(f"不支持的源码包格式：{path.name}")
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError(f"解包失败：{exc}") from exc
    return tmp, True


def _looks_like_site(dirpath: Path) -> bool:
    return (dirpath / "config.inc.php").is_file() and (dirpath / "var").is_dir()


def _find_site_root(root: Path, max_depth: int = 4) -> Path | None:
    """广度优先寻找站点根目录，优先浅层。"""
    if _looks_like_site(root):
        return root
    frontier = [(root, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        try:
            children = [c for c in current.iterdir() if c.is_dir() and c.name not in IGNORE_DIRS]
        except (PermissionError, OSError):
            continue
        for child in children:
            if _looks_like_site(child):
                return child
            frontier.append((child, depth + 1))
    # 退而求其次：只要有 config.inc.php 就算
    for cfg in root.rglob("config.inc.php"):
        if cfg.parent.name not in IGNORE_DIRS:
            return cfg.parent
    return None


def _expand_nested(root: Path, rounds: int = 2) -> list[str]:
    """展开内层压缩包。

    站点备份常常是「tar.gz 里面再套一个站点 tar + 一个 typecho.db」，
    所以在外层解包后如果还找不到站点根目录，就继续展开内层压缩包。
    """
    notes: list[str] = []
    done: set[Path] = set()
    for _ in range(rounds):
        if _find_site_root(root):
            return notes
        pending = [f for f in root.rglob("*")
                   if f.is_file() and f.name.lower().endswith(ARCHIVE_EXTS) and f not in done]
        if not pending:
            return notes
        for f in pending:
            done.add(f)
            try:
                _extract_archive(f, root / f"_expand{len(done)}")
                notes.append(f"已展开内层压缩包 {f.name}")
            except ValueError as exc:
                notes.append(f"内层压缩包 {f.name} 展开失败：{exc}")
    return notes


def _php_header(path: Path) -> dict:
    """读取 PHP 文件头部注释里的 @package 等信息。"""
    info: dict[str, str] = {}
    try:
        head = path.read_text("utf-8", errors="replace")[:4000]
    except OSError:
        return info
    m = re.search(r"@package\s+(\S+)", head)
    if m:
        info["package"] = m.group(1)
    m = re.search(r"@author\s+(.+)", head)
    if m:
        info["author"] = m.group(1).strip()
    m = re.search(r"@version\s+(\S+)", head)
    if m:
        info["version"] = m.group(1).strip()
    # 第二行常是名字，形如 " * 有你的世界"；跳过含 HTML/链接的技术性首行
    lines = [ln.strip(" *\t") for ln in head.splitlines()[:12]]
    for ln in lines:
        if not ln or ln.startswith(("/", "*", "<?php", "@", "!", "//", "#")):
            continue
        if "*/" in ln or "<" in ln or "http" in ln.lower():
            continue
        ln = re.sub(r"\s+", " ", ln).strip()
        if 2 <= len(ln) <= 90:
            info.setdefault("title", ln)
            break
    if "title" not in info:
        info["title"] = info.get("package") or path.parent.name
    return info


@dataclass
class SourceTree:
    """一个已解包的 Typecho 站点源码目录。"""

    root: Path
    site_root: Path | None = None
    origin: Path | None = None
    temporary: bool = False
    warnings: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    db_path: Path | None = None
    _options: dict[str, str] | None = None

    # ---------------------------------------------------------------- 打开
    @classmethod
    def open(cls, path: str | Path) -> "SourceTree":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"路径不存在：{p}")
        temp = False
        root = p
        if p.is_file():
            root, temp = _extract_archive(p)
        tree = cls(root=root, temporary=temp, origin=p)
        tree.site_root = _find_site_root(root)
        if tree.site_root is None:
            # 备份包里常有一层套娃：外层 tar.gz 里放着站点 tar 和 typecho.db
            tree.warnings.extend(_expand_nested(root))
            tree.site_root = _find_site_root(root)
        if tree.site_root is None:
            tree.warnings.append("未在源码包中找到 config.inc.php，可能不是完整的站点目录")
        tree._load_config()
        tree._load_options()
        return tree

    def close(self) -> None:
        if self.temporary:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "SourceTree":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- 配置
    def _load_config(self) -> None:
        root = self.site_root or self.root
        cfg = root / "config.inc.php"
        self.config = {
            "root_dir": str(root),
            "file": str(cfg) if cfg.is_file() else None,
        }
        if not cfg.is_file():
            return
        text = cfg.read_text("utf-8", errors="replace")
        for key, const in (
            ("theme_dir", "__TYPECHO_THEME_DIR__"),
            ("plugin_dir", "__TYPECHO_PLUGIN_DIR__"),
            ("admin_dir", "__TYPECHO_ADMIN_DIR__"),
        ):
            m = re.search(r"define\(\s*['\"]" + const + r"['\"]\s*,\s*['\"]([^'\"]*)['\"]", text)
            self.config[key] = m.group(1) if m else None
        m = re.search(r"new\s+\\?Typecho\\Db\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]", text)
        if m:
            self.config["db_adapter"] = m.group(1)
            self.config["db_prefix"] = m.group(2)
        m = re.search(r"['\"]file['\"]\s*=>\s*['\"]([^'\"]+)['\"]", text)
        if m:
            self.config["db_file"] = m.group(1)
        self.config["debug"] = bool(re.search(r"__TYPECHO_DEBUG__['\"]\s*,\s*true", text))
        # 定位真实数据库文件
        if self.config.get("db_file"):
            wanted = Path(self.config["db_file"]).name
            if (root / wanted).is_file():
                self.db_path = root / wanted
            else:
                for cand in self.root.rglob(wanted):
                    if cand.is_file():
                        self.db_path = cand
                        break
        if self.db_path is None:
            for cand in self.root.rglob("*.db"):
                if cand.is_file() and cand.stat().st_size > 4096:
                    self.db_path = cand
                    break

    def _load_options(self) -> None:
        self._options = {}
        if not self.db_path:
            return
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            prefix = self.config.get("db_prefix") or "typecho_"
            rows = con.execute(f'select name, value from "{prefix}options"').fetchall()
            con.close()
        except sqlite3.Error as exc:
            self.warnings.append(f"读取数据库设置失败：{exc}")
            return
        self._options = {name: (value if value is not None else "") for name, value in rows}

    @property
    def options(self) -> dict[str, str]:
        return self._options or {}

    def option(self, name: str, default: str = "") -> str:
        return self.options.get(name, default) or default

    # ---------------------------------------------------------------- 目录
    def _subdir(self, key: str, default: str) -> Path | None:
        rel = self.config.get(key)
        root = self.site_root or self.root
        if rel:
            cand = root / rel.strip("/")
            if cand.is_dir():
                return cand
        fallback = root / default
        return fallback if fallback.is_dir() else None

    @property
    def themes_dir(self) -> Path | None:
        return self._subdir("theme_dir", "usr/themes")

    @property
    def plugins_dir(self) -> Path | None:
        return self._subdir("plugin_dir", "usr/plugins")

    @property
    def uploads_dir(self) -> Path | None:
        root = self.site_root or self.root
        cand = root / "usr" / "uploads"
        return cand if cand.is_dir() else None

    @property
    def admin_dir(self) -> str:
        return self.config.get("admin_dir") or "/admin/"

    # ---------------------------------------------------------------- 内容
    def themes(self) -> list[dict]:
        out: list[dict] = []
        if not self.themes_dir:
            return out
        for d in sorted(self.themes_dir.iterdir()):
            if not d.is_dir():
                continue
            files = [f for f in d.rglob("*") if f.is_file()]
            info = _php_header(d / "index.php") if (d / "index.php").is_file() else {}
            out.append({
                "name": d.name,
                "title": info.get("title") or d.name,
                "author": info.get("author"),
                "version": info.get("version"),
                "files": len(files),
                "size": sum(f.stat().st_size for f in files),
                "active": self.option("theme") == d.name,
                "has_screenshot": any((d / n).is_file() for n in ("screenshot.png", "screenshot.jpg")),
                "dirs": sorted({p.parent.name for p in files if p.parent != d})[:8],
            })
        return out

    def plugins(self) -> list[dict]:
        out: list[dict] = []
        if not self.plugins_dir:
            return out
        activated: set[str] = set()
        try:
            table = json.loads(self.option("plugins", "{}"))
            activated = set((table.get("activated") or {}).keys())
        except json.JSONDecodeError:
            pass
        for d in sorted(self.plugins_dir.iterdir()):
            if not d.is_dir():
                continue
            files = [f for f in d.rglob("*") if f.is_file()]
            entry = d / "Plugin.php"
            info = _php_header(entry) if entry.is_file() else {}
            out.append({
                "name": d.name,
                "title": info.get("title") or d.name,
                "package": info.get("package"),
                "version": info.get("version"),
                "author": info.get("author"),
                "files": len(files),
                "size": sum(f.stat().st_size for f in files),
                "activated": d.name in activated,
            })
        return out

    def uploads(self) -> dict:
        d = self.uploads_dir
        if not d:
            return {"dir": None, "count": 0, "size": 0, "by_year": {}, "files": []}
        files = [f for f in d.rglob("*") if f.is_file()]
        by_year: dict[str, int] = {}
        entries = []
        for f in files:
            rel = f.relative_to(d).as_posix()
            year = rel.split("/")[0] if "/" in rel else "(根)"
            by_year[year] = by_year.get(year, 0) + 1
            entries.append({"path": "/usr/uploads/" + rel, "size": f.stat().st_size})
        return {
            "dir": str(d),
            "count": len(files),
            "size": sum(f.stat().st_size for f in files),
            "by_year": dict(sorted(by_year.items())),
            "files": entries,
        }

    def routing(self) -> list[dict]:
        """从 options.routingTable 还原路由表（自定义永久链接结构）。"""
        raw = self.option("routingTable")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        routes: list[dict] = []
        for group in data.values() if isinstance(data, dict) else []:
            if not isinstance(group, dict):
                continue
            for name, spec in group.items():
                if not isinstance(spec, dict):
                    continue
                routes.append({
                    "name": name,
                    "url": spec.get("url", ""),
                    "widget": (spec.get("widget") or "").replace("\\", ""),
                    "action": spec.get("action", ""),
                })
        return routes

    def file_stats(self) -> dict:
        base = self.site_root or self.root
        counts: dict[str, int] = {}
        php_lines = 0
        total = 0
        for f in base.rglob("*"):
            if not f.is_file() or any(p in IGNORE_DIRS for p in f.parts):
                continue
            total += 1
            ext = f.suffix.lower() or "(无扩展名)"
            counts[ext] = counts.get(ext, 0) + 1
            if ext == ".php":
                try:
                    php_lines += sum(1 for _ in f.open("rb"))
                except OSError:
                    pass
        return {
            "files": total,
            "by_ext": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:12]),
            "php_files": counts.get(".php", 0),
            "php_lines": php_lines,
        }

    # ---------------------------------------------------------------- 汇总
    def summary(self) -> dict:
        interesting = [
            "title", "description", "keywords", "siteUrl", "theme", "generator",
            "timezone", "lang", "charset", "contentType", "rewrite", "pageSize",
            "postsListSize", "commentsListSize", "markdown", "installed",
            "allowXmlRpc", "allowRegister", "commentsRequireModeration",
            "commentsPostInterval", "attachmentTypes",
        ]
        opts = {k: self.option(k) for k in interesting if k in self.options}
        extra = {k: v for k, v in self.options.items()
                 if (k.startswith("plugin:") or k.startswith("theme:"))}
        return {
            "root": str(self.site_root or self.root),
            "origin": str(self.origin or self.root),
            "found": self.site_root is not None,
            "temporary": self.temporary,
            "config": self.config,
            "admin_dir": self.admin_dir,
            "version": self.option("generator", ""),
            "db": {
                "path": str(self.db_path) if self.db_path else None,
                "adapter": self.config.get("db_adapter"),
                "prefix": self.config.get("db_prefix"),
                "size": self.db_path.stat().st_size if self.db_path else 0,
                "options": len(self.options),
            },
            "themes": self.themes(),
            "plugins": self.plugins(),
            "uploads": {k: v for k, v in self.uploads().items() if k != "files"},
            "upload_paths": [e["path"] for e in self.uploads().get("files", [])],
            "routing": self.routing(),
            "files": self.file_stats(),
            "options": opts,
            "extended_options": extra,
            "warnings": self.warnings,
        }

    def upload_paths(self) -> set[str]:
        return {e["path"] for e in self.uploads().get("files", [])}
