"""导出：Markdown 归档、SQLite 重建、JSON / CSV 数据包。"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from . import model
from .store import Archive

#: 各表的整数列（写入时保持数字类型）
INT_COLUMNS: dict[str, set[str]] = {
    "contents": {"cid", "created", "modified", "order", "authorId", "commentsNum", "parent"},
    "comments": {"coid", "cid", "created", "authorId", "ownerId", "parent"},
    "metas": {"mid", "count", "order", "parent"},
    "relationships": {"cid", "mid"},
    "users": {"uid", "created", "activated", "logged"},
    "fields": {"cid", "int_value", "float_value"},
}

#: 表的主键（无主键的表写 None）
PRIMARY_KEYS = {
    "contents": "cid", "comments": "coid", "metas": "mid", "users": "uid",
    "relationships": None, "fields": None,
}

TYPE_SQL = {
    "cid": "INTEGER", "coid": "INTEGER", "mid": "INTEGER", "uid": "INTEGER",
    "created": "INTEGER", "modified": "INTEGER", "activated": "INTEGER", "logged": "INTEGER",
    "authorId": "INTEGER", "ownerId": "INTEGER", "commentsNum": "INTEGER",
    "order": "INTEGER", "parent": "INTEGER", "count": "INTEGER",
    "int_value": "INTEGER", "float_value": "REAL",
    "allowComment": "CHAR(1)", "allowPing": "CHAR(1)", "allowFeed": "CHAR(1)",
    "text": "TEXT", "str_value": "TEXT", "description": "TEXT",
}

TABLE_ORDER = ("contents", "comments", "metas", "relationships", "users", "fields")


def _safe_name(text: str, fallback: str = "untitled") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text or "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:80] or fallback


def _yaml_value(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return "\n" + "\n".join(f"  - {_yaml_scalar(v)}" for v in value)
    return _yaml_scalar(value)


def _yaml_scalar(value) -> str:
    text = str(value)
    if text == "" or text.lower() in {"null", "true", "false", "yes", "no", "~"} \
            or re.search(r'^[\d.+-]+$', text) or re.search(r'[:#\[\]{}&*!|>\'"%@`]', text):
        return json.dumps(text, ensure_ascii=False)
    return text


def export_markdown(archive: Archive, out_dir: str | Path) -> dict:
    """把文章与独立页面导出为带 YAML front matter 的 Markdown。"""
    out = Path(out_dir)
    posts_dir = out / "posts"
    pages_dir = out / "pages"
    posts_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    written: list[dict] = []
    for row in archive.post_rows(include_text=True):
        if row["type"] not in ("post", "page"):
            continue
        created = datetime.fromtimestamp(row["created"], tz=None) if row["created"] else None
        year = created.strftime("%Y") if created else "0000"
        stamp = created.strftime("%Y-%m-%d") if created else ""
        slug = _safe_name(row["slug"] or f"cid-{row['cid']}")
        base = posts_dir if row["type"] == "post" else pages_dir
        folder = base / year if row["type"] == "post" else base
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stamp}-{slug}.md" if stamp else folder / f"{slug}.md"

        body = model.strip_markdown_prefix(row.get("text") or "")
        if not row["isMarkdown"]:
            body = row.get("text") or ""

        front = {
            "title": row["title"],
            "cid": row["cid"],
            "slug": row["slug"],
            "type": row["type"],
            "status": row["status"],
            "date": row["createdText"],
            "modified": row["modifiedText"],
            "author": row["author"],
            "categories": [c["name"] for c in row["categories"]],
            "tags": [t["name"] for t in row["tags"]],
            "template": row["template"],
            "comments": row["commentsNum"],
            "word_count": row["chars"],
        }
        lines = ["---"]
        for key, val in front.items():
            lines.append(f"{key}: {_yaml_value(val)}" if not isinstance(val, (list, tuple))
                         else f"{key}: {_yaml_value(val)}")
        lines.append("---")
        lines.append("")
        path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")

        comments = [c for c in archive.comments if int(c.get("cid") or 0) == row["cid"]]
        written.append({
            "cid": row["cid"], "title": row["title"], "type": row["type"],
            "path": str(path.relative_to(out).as_posix()), "comments": len(comments),
        })
        if comments:
            cpath = out / "comments" / f"{row['cid']}.json"
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps(
                [{
                    "coid": int(c.get("coid") or 0),
                    "author": c.get("author"), "mail": c.get("mail"), "url": c.get("url"),
                    "text": c.get("text"), "created": c.get("created"),
                    "status": c.get("status"), "parent": c.get("parent"),
                } for c in sorted(comments, key=lambda x: int(x.get("created") or 0))],
                ensure_ascii=False, indent=2), encoding="utf-8")

    # 站点索引
    by_year: dict[str, list[dict]] = {}
    for item in written:
        if item["type"] != "post":
            continue
        year = Path(item["path"]).parts[1] if len(Path(item["path"]).parts) > 2 else "—"
        by_year.setdefault(year, []).append(item)

    idx = ["# 文章索引", "", f"共 {len(written)} 篇内容。", ""]
    for year in sorted(by_year, reverse=True):
        idx.append(f"## {year}")
        idx.append("")
        for item in by_year[year]:
            idx.append(f"- [{item['title']}]({item['path']})")
        idx.append("")
    pages = [w for w in written if w["type"] == "page"]
    if pages:
        idx.append("## 独立页面")
        idx.append("")
        for item in pages:
            idx.append(f"- [{item['title']}]({item['path']})")
        idx.append("")
    (out / "index.md").write_text("\n".join(idx), encoding="utf-8")
    return {"count": len(written), "files": written}


def _create_table_sql(table: str, fields: list[str], prefix: str = "typecho_") -> str:
    cols = []
    for name in fields:
        sql_type = TYPE_SQL.get(name, "TEXT")
        quoted = f'"{name}"' if name.lower() in {"order", "group", "type", "count", "parent"} else name
        cols.append(f"  {quoted} {sql_type} DEFAULT NULL")
    pk = PRIMARY_KEYS.get(table)
    if pk:
        for i, name in enumerate(fields):
            if name == pk:
                cols[i] = cols[i].replace(" DEFAULT NULL", " NOT NULL PRIMARY KEY")
    return f'CREATE TABLE "{prefix}{table}" (\n' + ",\n".join(cols) + "\n)"


def export_sqlite(archive: Archive, path: str | Path, prefix: str = "typecho_") -> dict:
    """用备份数据重建一个可直接跑起来的 Typecho SQLite 库。"""
    out = Path(path)
    if out.exists():
        out.unlink()
    con = sqlite3.connect(out)
    cur = con.cursor()
    counts: dict[str, int] = {}

    for table in TABLE_ORDER:
        recs = archive.doc.of(table)
        if not recs:
            continue
        fields = [f.name for f in recs[0].fields]
        cur.execute(_create_table_sql(table, fields, prefix))

        quoted = [f'"{n}"' for n in fields]
        placeholders = ",".join("?" for _ in fields)
        sql = f'INSERT INTO "{prefix}{table}" ({",".join(quoted)}) VALUES ({placeholders})'
        rows = []
        for rec in recs:
            values = []
            for f in rec.fields:
                if f.raw is None:
                    values.append(None)
                elif f.name in INT_COLUMNS.get(table, ()):
                    num = f.number()
                    values.append(num if num is not None else f.text())
                else:
                    values.append(f.text())
            rows.append(tuple(values))
        cur.executemany(sql, rows)
        counts[table] = len(rows)

    cur.execute('CREATE TABLE "tarch_export" (key TEXT PRIMARY KEY, value TEXT)')
    meta = {
        "source_dat": str(archive.doc.path),
        "source_records": str(len(archive.doc.records)),
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tables": json.dumps(counts, ensure_ascii=False),
    }
    cur.executemany('INSERT INTO "tarch_export" VALUES (?,?)', list(meta.items()))
    con.commit()
    con.close()
    return {"path": str(out), "tables": counts, "size": out.stat().st_size}


def export_json(archive: Archive, path: str | Path) -> dict:
    out = Path(path)
    out.write_text(json.dumps(archive.payload(), ensure_ascii=False, indent=1), encoding="utf-8")
    return {"path": str(out), "size": out.stat().st_size}


def export_csv(archive: Archive, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []

    rows = archive.post_rows(include_text=False)
    cols = ["cid", "title", "typeLabel", "statusLabel", "createdText", "modifiedText",
            "author", "chars", "commentsNum", "slug", "template", "attachmentCount",
            "categories", "tags", "excerpt"]
    p = out / "contents.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["categories"] = " / ".join(c["name"] for c in r["categories"])
            r["tags"] = " ".join(t["name"] for t in r["tags"])
            w.writerow(r)
    written.append(p)

    p = out / "comments.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        cols = ["coid", "cid", "postTitle", "author", "mail", "url", "ip", "createdText",
                "status", "type", "parent", "text"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(archive.comment_rows())
    written.append(p)

    for table in TABLE_ORDER:
        recs = archive.doc.of(table)
        if not recs:
            continue
        names = [f.name for f in recs[0].fields]
        p = out / f"{table}.csv"
        with p.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(names)
            for rec in recs:
                w.writerow(["" if f.raw is None else f.text() for f in rec.fields])
        written.append(p)

    return {"dir": str(out), "files": [x.name for x in written]}


def export_all(archive: Archive, out_dir: str | Path) -> dict:
    """一次性导出全部格式，返回清单。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = export_markdown(archive, out / "markdown")
    db = export_sqlite(archive, out / "typecho-rebuilt.db")
    js = export_json(archive, out / "archive.json")
    cs = export_csv(archive, out / "csv")
    return {"dir": str(out), "markdown": md, "sqlite": db, "json": js, "csv": cs}


def zip_dir(src: str | Path, zip_path: str | Path) -> Path:
    src = Path(src)
    out = Path(zip_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file() and f != out:
                zf.write(f, f.relative_to(src).as_posix())
    return out
