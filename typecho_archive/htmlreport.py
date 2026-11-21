"""生成单文件离线 HTML 报告。

把界面样式、脚本与归档数据全部内联到一个 .html 里，双击即可阅读，
不需要 Python、不需要联网、不需要任何依赖。
"""

from __future__ import annotations

import json
from pathlib import Path

from .store import Archive

UI_DIR = Path(__file__).parent / "ui"

FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='7' fill='%23b45309'/%3E"
    "%3Ctext x='16' y='22' font-size='17' text-anchor='middle' fill='%23fff'"
    " font-family='serif'%3E%E5%BD%92%3C/text%3E%3C/svg%3E"
)


def build_html(archive: Archive) -> str:
    """返回完整的单文件 HTML 文本。"""
    index = (UI_DIR / "index.html").read_text("utf-8")
    css = (UI_DIR / "app.css").read_text("utf-8")
    js = (UI_DIR / "app.js").read_text("utf-8")
    payload = json.dumps(archive.payload(), ensure_ascii=False)

    html = index.replace(
        '<link rel="stylesheet" href="app.css">',
        f"<style>\n{css}\n</style>",
    ).replace(
        '<script src="app.js"></script>',
        "<script>window.__MODE__='static';window.__ARCHIVE__="
        + payload.replace("</", "<\\/")
        + ";</script>\n<script>\n" + js + "\n</script>",
    ).replace(
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<link rel="icon" href="{FAVICON}">',
    )
    title = (archive.site or {}).get("options", {}).get("title") or "Typecho 归档报告"
    html = html.replace("<title>Typecho 归档台</title>", f"<title>{title} · 归档报告</title>")
    return html


def write_static(archive: Archive, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(archive), encoding="utf-8")
    return out
