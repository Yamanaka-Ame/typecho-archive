"""归档模型：把 .dat 备份与源码包合成一份可查询、可体检的数据视图。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import dat, model
from .dat import DatDocument, Record
from .source import SourceTree


def _iso(ts: int | None, tz_hours: int) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone(timedelta(hours=tz_hours))).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (OverflowError, OSError, ValueError):
        return ""


@dataclass
class Archive:
    """一份归档档案。

    数据可以来自 `.dat` 备份，也可以来自源码包里的 SQLite 库；
    两种来源都会被转成同样的记录结构，因此后面的统计、体检、导出完全一致。
    源码包是可选的：只给备份时没有站点信息，只给源码包时数据直接读库。
    """

    doc: object
    source: SourceTree | None = None
    tz_hours: int = 8

    contents: dict[int, dict] = field(default_factory=dict)
    comments: list[dict] = field(default_factory=list)
    metas: dict[int, dict] = field(default_factory=dict)
    users: dict[int, dict] = field(default_factory=dict)
    fields: dict[int, dict[str, str]] = field(default_factory=dict)
    rel: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    site: dict | None = None

    # ------------------------------------------------------------- 构建
    @classmethod
    def open(cls, path: str | Path, source_path: str | Path | None = None,
             *, verify: bool = True) -> "Archive":
        """打开一份数据源。

        `path` 可以是 `.dat` 备份、源码包（tar/tar.gz/zip/目录）或 `.db` 数据库，
        类型自动识别——两个文件只会单独上传，所以哪一种都要能独立跑起来。
        """
        from .sources import DbSource, detect_kind, guess_prefix

        p = Path(path)
        kind = detect_kind(p)
        source = None

        if kind == "dat":
            doc = dat.parse(p, verify=verify)
            if source_path:
                source = SourceTree.open(source_path)
        elif kind == "db":
            doc = DbSource(p, prefix=guess_prefix(p))
            if source_path:
                source = SourceTree.open(source_path)
        else:
            source = SourceTree.open(p)
            if not source.db_path:
                raise ValueError(
                    "源码包里没有找到 typecho.db，读不到文章数据。"
                    "请确认压缩包完整，或改用 .dat 备份文件。")
            doc = DbSource(source.db_path,
                           prefix=source.config.get("db_prefix") or guess_prefix(source.db_path))

        archive = cls(doc=doc, source=source)
        archive.build()
        return archive

    def build(self) -> None:
        self.site = self.source.summary() if self.source else None
        if self.site:
            try:
                self.tz_hours = int(int(self.site.get("options", {}).get("timezone") or 28800) / 3600)
            except (TypeError, ValueError):
                self.tz_hours = 8

        for rec in self.doc.of("contents"):
            d = model.record_to_dict(rec)
            d["_cid"] = int(d.get("cid") or 0)
            self.contents[d["_cid"]] = d
        for rec in self.doc.of("comments"):
            d = model.record_to_dict(rec)
            self.comments.append(d)
        for rec in self.doc.of("metas"):
            self.metas[int(model.record_to_dict(rec).get("mid") or 0)] = model.record_to_dict(rec)
        for rec in self.doc.of("users"):
            self.users[int(model.record_to_dict(rec).get("uid") or 0)] = model.record_to_dict(rec)
        for rec in self.doc.of("fields"):
            d = model.record_to_dict(rec)
            cid = int(d.get("cid") or 0)
            self.fields.setdefault(cid, {})[str(d.get("name"))] = d.get("str_value") or ""
        for rec in self.doc.of("relationships"):
            d = model.record_to_dict(rec)
            self.rel[int(d.get("cid") or 0)].append(int(d.get("mid") or 0))

        self.comments.sort(key=lambda c: int(c.get("created") or 0), reverse=True)

    # ------------------------------------------------------------- 便捷访问
    def post_metas(self, cid: int, kind: str | None = None) -> list[dict]:
        out = []
        for mid in self.rel.get(cid, ()):
            m = self.metas.get(mid)
            if m and (kind is None or m.get("type") == kind):
                out.append({
                    "mid": int(m.get("mid") or 0),
                    "name": m.get("name") or "",
                    "slug": m.get("slug") or "",
                    "type": m.get("type") or "",
                    "description": m.get("description") or "",
                })
        return out

    def post_text(self, cid: int) -> str:
        return (self.contents.get(cid) or {}).get("text") or ""

    def attachments_of(self, cid: int) -> list[dict]:
        out = []
        for c in self.contents.values():
            if c.get("type") == "attachment" and int(c.get("parent") or 0) == cid:
                meta = model.parse_attachment(c.get("text")) or {}
                out.append({
                    "cid": int(c.get("cid") or 0),
                    "title": c.get("title") or "",
                    "name": meta.get("name") or c.get("title") or "",
                    "path": meta.get("path") or "",
                    "url": meta.get("url") or "",
                    "size": meta.get("size") or 0,
                    "mime": meta.get("mime") or meta.get("type") or "",
                    "isImage": bool(meta.get("isImage")),
                })
        return out

    def comment_tree(self, cid: int) -> dict:
        rows = [c for c in self.comments if int(c.get("cid") or 0) == cid]
        children: dict[int, list[dict]] = defaultdict(list)
        for c in rows:
            children[int(c.get("parent") or 0)].append(c)
        for group in children.values():
            group.sort(key=lambda c: int(c.get("created") or 0))

        def node(c: dict) -> dict:
            coid = int(c.get("coid") or 0)
            return {
                "coid": coid,
                "author": c.get("author") or "",
                "mail": c.get("mail") or "",
                "url": c.get("url") or "",
                "ip": c.get("ip") or "",
                "agent": c.get("agent") or "",
                "text": c.get("text") or "",
                "status": c.get("status") or "",
                "type": c.get("type") or "",
                "created": int(c.get("created") or 0),
                "createdText": _iso(int(c.get("created") or 0), self.tz_hours),
                "children": [node(x) for x in children.get(coid, ())],
            }

        return {"roots": [node(c) for c in children.get(0, ())], "total": len(rows)}

    # ------------------------------------------------------------- 列表
    def post_rows(self, *, include_text: bool = True) -> list[dict]:
        rows = []
        for c in self.contents.values():
            cid = int(c.get("cid") or 0)
            kind = c.get("type") or ""
            text = c.get("text") or ""
            cats = self.post_metas(cid, "category")
            tags = self.post_metas(cid, "tag")
            file_size = None
            if kind == "attachment":
                meta = model.parse_attachment(text) or {}
                file_size = meta.get("size") or 0
                excerpt = f"{meta.get('name') or c.get('title') or '附件'} · " \
                          f"{meta.get('mime') or meta.get('type') or '未知类型'}"
            else:
                excerpt = model.excerpt(text, 160)
            row = {
                "cid": cid,
                "title": c.get("title") or "(无标题)",
                "slug": c.get("slug") or "",
                "type": kind,
                "typeLabel": model.CONTENT_TYPES.get(kind, kind),
                "status": c.get("status") or "",
                "statusLabel": model.CONTENT_STATUS.get(c.get("status") or "", c.get("status") or ""),
                "created": int(c.get("created") or 0),
                "createdText": _iso(int(c.get("created") or 0), self.tz_hours),
                "modified": int(c.get("modified") or 0),
                "modifiedText": _iso(int(c.get("modified") or 0), self.tz_hours),
                "authorId": int(c.get("authorId") or 0),
                "author": (self.users.get(int(c.get("authorId") or 0)) or {}).get("screenName")
                          or (self.users.get(int(c.get("authorId") or 0)) or {}).get("name") or "",
                "commentsNum": int(c.get("commentsNum") or 0),
                "template": c.get("template"),
                "hasPassword": bool(c.get("password")),
                "order": int(c.get("order") or 0),
                "parent": int(c.get("parent") or 0),
                "categories": cats,
                "tags": tags,
                "excerpt": excerpt,
                "chars": 0 if kind == "attachment" else len(model.plain_text(text)),
                "fileSize": file_size,
                "isMarkdown": model.is_markdown(text),
                "fields": self.fields.get(cid, {}),
                "attachmentCount": 0,
            }
            if include_text:
                row["text"] = text
            rows.append(row)

        counts = Counter()
        for c in self.contents.values():
            if c.get("type") == "attachment":
                counts[int(c.get("parent") or 0)] += 1
        for row in rows:
            row["attachmentCount"] = counts.get(row["cid"], 0)
        rows.sort(key=lambda r: (-r["created"], -r["cid"]))
        return rows

    def meta_rows(self) -> tuple[list[dict], list[dict]]:
        link = Counter()
        for cid, mids in self.rel.items():
            for mid in mids:
                link[mid] += 1
        cats, tags = [], []
        for mid, m in self.metas.items():
            row = {
                "mid": int(m.get("mid") or 0),
                "name": m.get("name") or "",
                "slug": m.get("slug") or "",
                "type": m.get("type") or "",
                "description": m.get("description") or "",
                "count": int(m.get("count") or 0),
                "actual": link.get(int(m.get("mid") or 0), 0),
                "order": int(m.get("order") or 0),
                "parent": int(m.get("parent") or 0),
            }
            (cats if row["type"] == "category" else tags).append(row)
        cats.sort(key=lambda r: (r["order"], r["name"]))
        tags.sort(key=lambda r: (-r["actual"], r["name"]))
        return cats, tags

    def user_rows(self) -> list[dict]:
        posts = Counter(int(c.get("authorId") or 0) for c in self.contents.values())
        comments = Counter(int(c.get("authorId") or 0) for c in self.comments)
        rows = []
        for uid, u in self.users.items():
            group = u.get("group") or ""
            rows.append({
                "uid": int(u.get("uid") or 0),
                "name": u.get("name") or "",
                "screenName": u.get("screenName") or "",
                "mail": u.get("mail") or "",
                "url": u.get("url") or "",
                "group": group,
                "groupLabel": model.USER_GROUPS.get(group, group),
                "created": int(u.get("created") or 0),
                "createdText": _iso(int(u.get("created") or 0), self.tz_hours),
                "logged": int(u.get("logged") or 0),
                "loggedText": _iso(int(u.get("logged") or 0), self.tz_hours),
                "activated": int(u.get("activated") or 0),
                "posts": posts.get(uid, 0),
                "comments": comments.get(uid, 0),
                "hasAuthCode": bool(u.get("authCode")),
            })
        rows.sort(key=lambda r: (-r["posts"], r["name"]))
        return rows

    def comment_rows(self) -> list[dict]:
        titles = {cid: (c.get("title") or "") for cid, c in self.contents.items()}
        rows = []
        for c in self.comments:
            cid = int(c.get("cid") or 0)
            rows.append({
                "coid": int(c.get("coid") or 0),
                "cid": cid,
                "postTitle": titles.get(cid, ""),
                "author": c.get("author") or "",
                "mail": c.get("mail") or "",
                "url": c.get("url") or "",
                "ip": c.get("ip") or "",
                "agent": c.get("agent") or "",
                "text": c.get("text") or "",
                "status": c.get("status") or "",
                "type": c.get("type") or "",
                "parent": int(c.get("parent") or 0),
                "created": int(c.get("created") or 0),
                "createdText": _iso(int(c.get("created") or 0), self.tz_hours),
            })
        return rows

    # ------------------------------------------------------------- 统计
    def stats(self) -> dict:
        texts = [(c.get("type"), c.get("status"), c.get("created"), c.get("text") or "")
                 for c in self.contents.values()]
        by_type = Counter(t for t, _, _, _ in texts)
        by_status = Counter(s for _, s, _, _ in texts)
        posts = [(int(cr or 0), model.plain_text(tx)) for t, s, cr, tx in texts
                 if t in ("post", "page")]
        chars = sum(len(t) for _, t in posts)
        years = Counter()
        months = Counter()
        for cr, _ in posts:
            if cr:
                dt = datetime.fromtimestamp(cr, tz=timezone(timedelta(hours=self.tz_hours)))
                years[str(dt.year)] += 1
                months[dt.strftime("%Y-%m")] += 1
        cats, tags = self.meta_rows()
        users = self.user_rows()
        commenters = Counter(c.get("author") or "(匿名)" for c in self.comments)
        stamps = [cr for cr, _ in posts if cr]
        return {
            "counts": {
                "contents": len(self.contents),
                "posts": by_type.get("post", 0),
                "pages": by_type.get("page", 0),
                "attachments": by_type.get("attachment", 0),
                "comments": len(self.comments),
                "categories": sum(1 for m in self.metas.values() if m.get("type") == "category"),
                "tags": sum(1 for m in self.metas.values() if m.get("type") == "tag"),
                "users": len(self.users),
                "fields": sum(len(v) for v in self.fields.values()),
                "relationships": sum(len(v) for v in self.rel.values()),
                "records": len(self.doc.records),
            },
            "byType": dict(by_type),
            "byStatus": dict(by_status),
            "words": {
                "chars": chars,
                "avgPerPost": round(chars / len(posts)) if posts else 0,
                "longestCid": max(posts, key=lambda p: len(p[1]))[0] if posts else 0,
            },
            "years": dict(sorted(years.items())),
            "months": dict(sorted(months.items())),
            "span": {
                "first": _iso(min(stamps), self.tz_hours) if stamps else "",
                "last": _iso(max(stamps), self.tz_hours) if stamps else "",
                "days": round((max(stamps) - min(stamps)) / 86400) if stamps else 0,
            },
            "topCategories": [{"name": r["name"], "count": r["actual"]}
                              for r in sorted(cats, key=lambda r: (-r["actual"], r["name"]))[:8]],
            "topTags": [{"name": r["name"], "count": r["actual"]} for r in tags[:20]],
            "topAuthors": [{"name": u["screenName"] or u["name"], "count": u["posts"]}
                           for u in users[:8] if u["posts"]],
            "topCommenters": [{"name": n, "count": c} for n, c in commenters.most_common(8)],
            "commentStatus": dict(Counter(c.get("status") or "" for c in self.comments)),
        }

    # ------------------------------------------------------------- 体检
    def integrity(self) -> list[dict]:
        issues: list[dict] = []

        def add(level: str, key: str, title: str, detail: str, items: list | None = None) -> None:
            issues.append({"level": level, "key": key, "title": title,
                           "detail": detail, "items": items or []})

        if getattr(self.doc, "kind", "dat") == "dat":
            failed = self.doc.md5_failures
            if failed:
                add("error", "md5", f"{len(failed)} 条记录 md5 校验失败",
                    "记录内容与内部校验和不符，文件可能损坏或被改动。",
                    [{"text": f"记录 #{r.seq} / {r.table or r.type_id} @ {r.offset}"}
                     for r in failed[:50]])
            else:
                add("ok", "md5", "全部记录 md5 校验通过",
                    f"{len(self.doc.records)} 条记录逐条比对校验和，均一致。")
            if self.doc.footer_ok:
                add("ok", "footer", "文件尾魔数完整",
                    "文件以 %TYPECHO_BACKUP_0001% 结束，未被截断。")
            else:
                add("warn", "footer", "缺少文件尾魔数", "文件可能被截断，导出过程或许未正常完成。")
        else:
            add("ok", "source", "数据直接读自 SQLite 数据库",
                f"{len(self.doc.records)} 条记录来自 typecho.db，不存在需要校验的打包层。")
        for w in getattr(self.doc, "warnings", []):
            add("warn", "warning", "解析告警", w)

        orphan_comments = [c for c in self.comments
                           if int(c.get("cid") or 0) not in self.contents]
        if orphan_comments:
            add("warn", "orphan_comments", f"{len(orphan_comments)} 条评论找不到所属文章",
                "这些评论的 cid 在 contents 表中不存在，恢复后会被丢弃。",
                [{"text": f"评论 #{c.get('coid')} → cid {c.get('cid')}"} for c in orphan_comments[:40]])

        orphan_rel = [(cid, mid) for cid, mids in self.rel.items() for mid in mids
                      if cid not in self.contents or mid not in self.metas]
        if orphan_rel:
            add("warn", "orphan_rel", f"{len(orphan_rel)} 条分类/标签关联指向不存在的对象",
                "关联表引用了已删除的文章或分类标签。",
                [{"text": f"cid {cid} ↔ mid {mid}"} for cid, mid in orphan_rel[:40]])

        mismatch = []
        for mid, m in self.metas.items():
            declared = int(m.get("count") or 0)
            actual = sum(1 for mids in self.rel.values() if mid in mids)
            if declared != actual:
                mismatch.append({"text": f"{model.META_TYPES.get(m.get('type'), m.get('type'))} "
                                         f"「{m.get('name')}」记录 {declared} 实际 {actual}"})
        if mismatch:
            add("info", "meta_count", f"{len(mismatch)} 个分类/标签的计数与关联数不一致",
                "Typecho 的 count 字段是缓存值，恢复后由程序重新统计，一般不影响数据。", mismatch[:40])

        no_author = [c for c in self.contents.values()
                     if int(c.get("authorId") or 0) not in self.users]
        if no_author:
            add("warn", "no_author", f"{len(no_author)} 篇内容指向不存在的作者",
                "authorId 在 users 表中找不到对应用户。",
                [{"text": f"#{c.get('cid')} {c.get('title')}"} for c in no_author[:40]])

        empty = [c for c in self.contents.values()
                 if c.get("type") in ("post", "page") and not model.plain_text(c.get("text"))]
        if empty:
            add("info", "empty_text", f"{len(empty)} 篇文章正文为空",
                "可能是在草稿阶段保存的条目。",
                [{"text": f"#{c.get('cid')} {c.get('title')}"} for c in empty[:40]])

        seen: dict[tuple, list] = defaultdict(list)
        for c in self.contents.values():
            if c.get("type") in ("post", "page"):
                seen[(c.get("type"), (c.get("slug") or "").lower())].append(c)
        dupes = {k: v for k, v in seen.items() if k[1] and len(v) > 1}
        if dupes:
            add("warn", "dup_slug", f"{len(dupes)} 组重复的缩略名",
                "缩略名重复会导致固定链接冲突，后写入的覆盖前面的。",
                [{"text": f"{k[1]} → " + ", ".join(f"#{x.get('cid')}" for x in v)}
                 for k, v in list(dupes.items())[:40]])

        if self.source:
            known = self.source.upload_paths()
            missing = []
            for c in self.contents.values():
                if c.get("type") != "attachment":
                    continue
                meta = model.parse_attachment(c.get("text")) or {}
                path = meta.get("path") or ""
                if path and path not in known:
                    missing.append({"text": f"{path}（#{c.get('cid')}）"})
            if missing:
                add("warn", "missing_upload", f"{len(missing)} 个附件的文件不在源码包中",
                    "备份只含内容数据，文件本体需从 usr/uploads 单独取。", missing[:60])
            else:
                add("ok", "missing_upload", "附件全部能在源码包中找到",
                    f"{self.stats()['counts']['attachments']} 个附件与 usr/uploads 一一对应。")

        pwd = [c for c in self.contents.values() if c.get("password")]
        if pwd:
            add("info", "password", f"{len(pwd)} 篇内容设有访问密码",
                "这些内容在恢复后需要输入密码才能查看。")

        return issues

    # ------------------------------------------------------------- 对账
    def diff_source(self) -> dict | None:
        """把备份与源码包里的 SQLite 库逐表对比。"""
        if not self.source or not self.source.db_path:
            return None
        if getattr(self.doc, "kind", "dat") != "dat":
            # 数据本身就读自这个库，两边必然一致，没有比对的意义
            return None
        import sqlite3
        prefix = self.source.config.get("db_prefix") or "typecho_"
        try:
            con = sqlite3.connect(f"file:{self.source.db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            return {"error": str(exc)}

        def ids(sql: str) -> set:
            try:
                return {r[0] for r in con.execute(sql)}
            except sqlite3.Error:
                return set()

        tables = {}
        plan = {
            "contents": ("cid", set(self.contents)),
            "comments": ("coid", {int(c.get("coid") or 0) for c in self.comments}),
            "metas": ("mid", set(self.metas)),
            "users": ("uid", set(self.users)),
            "relationships": (None, {(cid, mid) for cid, mids in self.rel.items() for mid in mids}),
            "fields": (None, {(cid, name) for cid, f in self.fields.items() for name in f}),
        }
        for table, (key, backup_set) in plan.items():
            if table == "relationships":
                db_set = set(con.execute(f'select cid, mid from "{prefix}relationships"').fetchall())
            elif table == "fields":
                db_set = set(con.execute(f'select cid, name from "{prefix}fields"').fetchall())
            else:
                db_set = ids(f'select {key} from "{prefix}{table}"')
            only_backup = sorted(backup_set - db_set, key=str)
            only_db = sorted(db_set - backup_set, key=str)
            tables[table] = {
                "backup": len(backup_set),
                "db": len(db_set),
                "onlyBackup": only_backup[:200],
                "onlyDb": only_db[:200],
                "onlyBackupCount": len(only_backup),
                "onlyDbCount": len(only_db),
            }
        con.close()

        def describe(table: str, ids_: list) -> list[dict]:
            out = []
            for i in ids_[:60]:
                if table == "contents":
                    c = self.contents.get(i) or {}
                    out.append({"id": i, "text": f"#{i} {c.get('title') or ''}"})
                elif table == "comments":
                    c = next((x for x in self.comments if int(x.get("coid") or 0) == i), {})
                    out.append({"id": i, "text": f"#{i} {c.get('author') or ''}: {(c.get('text') or '')[:40]}"})
                elif table == "metas":
                    m = self.metas.get(i) or {}
                    out.append({"id": i, "text": f"#{i} [{m.get('type')}] {m.get('name')}"})
                elif table == "users":
                    u = self.users.get(i) or {}
                    out.append({"id": i, "text": f"#{i} {u.get('name')}"})
                else:
                    out.append({"id": str(i), "text": str(i)})
            return out

        for table in ("contents", "comments", "metas", "users"):
            tables[table]["onlyBackupDetail"] = describe(table, tables[table]["onlyBackup"])
            tables[table]["onlyDbDetail"] = describe(table, tables[table]["onlyDb"])

        return {"tables": tables, "dbPath": str(self.source.db_path)}

    # ------------------------------------------------------------- 输出
    def payload(self) -> dict:
        cats, tags = self.meta_rows()
        file_info = self.doc.summary()
        file_info["origin"] = str(self.source.origin) if self.source and self.source.origin \
            else str(self.doc.path)
        return {
            "file": file_info,
            "site": self.site,
            "stats": self.stats(),
            "posts": self.post_rows(include_text=True),
            "comments": self.comment_rows(),
            "metas": {"categories": cats, "tags": tags},
            "users": self.user_rows(),
            "integrity": self.integrity(),
            "diff": self.diff_source(),
            "schema": self.schema_view(),
            "timezone": self.tz_hours,
        }

    def schema_view(self) -> dict:
        """还原数据里每张表的字段顺序与覆盖情况。"""
        if getattr(self.doc, "kind", "dat") != "dat":
            # 数据库来源没有「字段在文件里的字节布局」这回事
            return {}
        out = {}
        for table in ("contents", "comments", "metas", "relationships", "users", "fields"):
            recs = self.doc.of(table)
            if not recs:
                continue
            first = recs[0]
            out[table] = {
                "fields": [{"name": f.name, "nulls": sum(1 for r in recs if r.field(f.name) and
                                                         r.field(f.name).raw is None),
                            "bytes": sum(r.field(f.name).length for r in recs if r.field(f.name))}
                           for f in first.fields],
                "rows": len(recs),
            }
        return out

    def raw_record(self, seq: int) -> dict | None:
        if getattr(self.doc, "kind", "dat") != "dat":
            return None
        for r in self.doc.records:
            if r.seq == seq:
                blob = dat.encode_record(r.type_id, r.fields, self.doc.version)
                return {
                    "seq": r.seq,
                    "typeId": r.type_id,
                    "table": r.table,
                    "offset": r.offset,
                    "bodyLen": r.body_len,
                    "md5Ok": r.md5_ok,
                    "fields": [
                        {"name": f.name,
                         "len": f.length if f.raw is not None else None,
                         "null": f.raw is None,
                         "preview": (f.text() or "")[:300]}
                        for f in r.fields
                    ],
                    "hex": dat.iter_raw_preview(blob, 1024),
                    "size": len(blob),
                }
        return None

    def record_index(self) -> list[dict]:
        if getattr(self.doc, "kind", "dat") != "dat":
            return []
        rows = []
        for r in self.doc.records:
            title = ""
            if r.table in ("contents", "comments"):
                t = r.text("title") if r.table == "contents" else r.text("author")
                title = t or ""
            rows.append({
                "seq": r.seq, "typeId": r.type_id, "table": r.table,
                "offset": r.offset, "bodyLen": r.body_len,
                "fields": len(r.fields), "md5Ok": r.md5_ok, "preview": title[:60],
            })
        return rows
