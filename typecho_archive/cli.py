"""命令行入口：``python -m typecho_archive <命令>``。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

from . import __version__, dat, export, htmlreport, model
from .store import Archive


# --------------------------------------------------------------------- 输出
def _setup_stdout() -> None:
    """让输出尽量不因编码问题崩掉。

    注意不要强制改写成 UTF-8：中文 Windows 的控制台是 GBK，
    Python 按控制台编码输出才是对的；这里只兜住「编不出来」的情况。
    需要固定编码时由外部设置 PYTHONIOENCODING。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _w(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _w(text))


def _trunc(text: str, width: int) -> str:
    if _w(text) <= width:
        return text
    out = ""
    for ch in text:
        if _w(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


def _table(headers: list[str], rows: list[list[str]], widths: list[int]) -> str:
    lines = ["  ".join(_pad(h, w) for h, w in zip(headers, widths)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(_pad(c, w) for c, w in zip(row, widths)).rstrip())
    return "\n".join(lines)


def _fmt_time(archive: Archive, ts) -> str:
    from .model import fmt_time
    return fmt_time(ts, archive.tz_hours)


# --------------------------------------------------------------------- 载入
def _load(args) -> Archive:
    try:
        return Archive.open(args.dat, getattr(args, "source", None),
                            verify=not getattr(args, "no_verify", False))
    except (dat.DatError, FileNotFoundError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)


# --------------------------------------------------------------------- 命令
def _parse_doc(path, verify: bool = True):
    """解析 .dat，失败时给出中文提示而不是抛栈。"""
    from .sources import detect_kind
    try:
        if detect_kind(path) != "dat":
            print(f"错误：{path} 不是 .dat 备份文件（该命令只处理备份文件）", file=sys.stderr)
            raise SystemExit(2)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
    try:
        return dat.parse(path, verify=verify)
    except dat.DatError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
    except OSError as exc:
        print(f"错误：无法读取 {path} —— {exc}", file=sys.stderr)
        raise SystemExit(2)


def cmd_info(args) -> int:
    a = _load(args)
    s = a.stats()
    c, f = s["counts"], a.doc.summary()
    f.setdefault("origin", str(a.source.origin) if a.source and a.source.origin else f["path"])
    if f.get("kind") == "dat":
        print(f"备份文件   {f['path']}")
        print(f"            {f['size'] / 1024 / 1024:.2f} MB · 格式 {f['version']} · "
              f"{f['records']} 条记录 · md5 {f['md5_ok']}/{f['records']} 通过")
    else:
        where = "源码包内的 SQLite 数据库" if a.source else "SQLite 数据库"
        print(f"数据来源   {f['origin']}")
        print(f"            {f['size'] / 1024 / 1024:.2f} MB · {where} · "
              f"{f['records']} 条记录")
    print(f"时间跨度   {s['span']['first']} — {s['span']['last']}（{s['span']['days']} 天）")
    print(f"内容       {c['posts']} 篇文章 · {c['pages']} 个独立页面 · {c['attachments']} 个附件")
    print(f"互动       {c['comments']} 条评论 · {c['users']} 位用户")
    print(f"组织       {c['categories']} 个分类 · {c['tags']} 个标签 · "
          f"{c['relationships']} 条关联 · {c['fields']} 个自定义字段")
    print(f"正文       {s['words']['chars']:,} 字 · 平均每篇 {s['words']['avgPerPost']:,} 字")
    if a.site:
        site = a.site
        print(f"源码       {site['version'] or '未知版本'} · 主题 {site['options'].get('theme', '?')} · "
              f"后台 {site['admin_dir']}")
        print(f"            {site['files']['files']} 个文件 · "
              f"{len([p for p in site['plugins'] if p['activated']])}/{len(site['plugins'])} 个插件启用")
    print()
    print("按年份：" + "  ".join(f"{y}:{n}" for y, n in s["years"].items()))
    print()
    bad = [i for i in a.integrity() if i["level"] in ("error", "warn")]
    if bad:
        print("需要注意：")
        for i in bad:
            print(f"  [{i['level']}] {i['title']}")
    else:
        print("体检：未发现问题")
    return 0


def cmd_list(args) -> int:
    a = _load(args)
    rows = a.post_rows(include_text=True)
    if args.type:
        kinds = {k.strip() for k in args.type.split(",")}
        rows = [r for r in rows if r["type"] in kinds]
    if args.status:
        rows = [r for r in rows if r["status"] == args.status]
    if args.category:
        rows = [r for r in rows if any(c["slug"] == args.category or c["name"] == args.category
                                       for c in r["categories"])]
    if args.tag:
        rows = [r for r in rows if any(t["slug"] == args.tag or t["name"] == args.tag
                                       for t in r["tags"])]
    if args.search:
        q = args.search.lower()
        rows = [r for r in rows if q in "\n".join([
            r["title"], r["slug"], r["author"], r.get("text") or "",
            " ".join(c["name"] for c in r["categories"]),
            " ".join(t["name"] for t in r["tags"]),
        ]).lower()]
    if args.limit:
        rows = rows[: args.limit]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    table = [[_trunc(r["createdText"][:10], 10), str(r["cid"]),
              _trunc(r["title"], args.width), r["typeLabel"], r["statusLabel"],
              str(r["chars"]), str(r["commentsNum"]), _trunc(r["author"], 12)]
             for r in rows]
    print(_table(["日期", "cid", "标题", "类型", "状态", "字数", "评论", "作者"],
                 table, [10, 5, args.width, 8, 8, 6, 4, 12]))
    print(f"\n共 {len(rows)} 条")
    return 0


def cmd_show(args) -> int:
    a = _load(args)
    row = next((r for r in a.post_rows(include_text=True) if r["cid"] == args.cid), None)
    if row is None:
        print(f"错误：找不到 cid={args.cid}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(row, ensure_ascii=False, indent=1))
        return 0
    print(f"# {row['title']}")
    print(f"cid {row['cid']} · {row['typeLabel']} · {row['statusLabel']} · "
          f"{row['createdText']} · {row['chars']} 字 · 评论 {row['commentsNum']}")
    if row["categories"]:
        print("分类：" + "、".join(c["name"] for c in row["categories"]))
    if row["tags"]:
        print("标签：" + "、".join(t["name"] for t in row["tags"]))
    if row["fields"]:
        print("字段：" + "、".join(f"{k}={v}" for k, v in row["fields"].items() if v))
    print()
    print(model.strip_markdown_prefix(row.get("text") or "").strip())
    if not args.no_comments:
        comments = [c for c in a.comments if int(c.get("cid") or 0) == args.cid]
        if comments:
            print()
            print(f"--- 评论 {len(comments)} ---")
            for c in sorted(comments, key=lambda x: int(x.get("created") or 0)):
                print(f"[{_fmt_time(a, c.get('created'))}] {c.get('author')}: {c.get('text')}")
    return 0


def cmd_search(args) -> int:
    a = _load(args)
    q = args.query.lower()
    hits = []
    for row in a.post_rows(include_text=True):
        text = "\n".join([row["title"], row["slug"], row.get("text") or "",
                          " ".join(c["name"] for c in row["categories"]),
                          " ".join(t["name"] for t in row["tags"])]).lower()
        count = text.count(q)
        if count:
            hits.append((count, row))
    hits.sort(key=lambda x: -x[0])
    if not hits:
        print("没有匹配的内容")
        return 1
    table = [[str(n), str(r["cid"]), _trunc(r["title"], args.width), r["createdText"][:10]]
             for n, r in hits[: args.limit]]
    print(_table(["命中", "cid", "标题", "日期"], table, [6, 5, args.width, 10]))
    print(f"\n共 {len(hits)} 篇匹配")
    return 0


def cmd_verify(args) -> int:
    p = Path(args.dat)
    doc = _parse_doc(p)
    ok = len(doc.records) - len(doc.md5_failures)
    print(f"文件      {p}")
    print(f"大小      {doc.size:,} 字节")
    print(f"格式版本  {doc.version}")
    print(f"记录数    {len(doc.records)}")
    print(f"md5       {ok}/{len(doc.records)} 通过")
    print(f"文件尾    {'完整' if doc.footer_ok else '缺失'}")
    for table, n in doc.counts().items():
        print(f"  {table:<14} {n}")
    if doc.warnings:
        print("\n告警：")
        for w in doc.warnings:
            print(f"  - {w}")
    if args.rebuild:
        out = dat.rebuild(doc, args.rebuild)
        same = out.read_bytes() == p.read_bytes()
        print(f"\n无损重建  {out} —— {'与原文件逐字节一致' if same else '存在差异'}")
        return 0 if same else 1
    return 0 if not doc.md5_failures else 1


def cmd_inspect(args) -> int:
    doc = _parse_doc(args.dat)
    if args.record is None:
        rows = []
        for r in doc.records[: args.limit]:
            rows.append([str(r.seq), str(r.type_id), r.table or "-", str(r.offset),
                         str(r.body_len), str(len(r.fields)), "ok" if r.md5_ok else "FAIL"])
        print(_table(["#", "type", "表", "偏移", "body", "字段", "md5"], rows,
                     [6, 5, 14, 10, 8, 5, 5]))
        print(f"\n共 {len(doc.records)} 条记录（显示前 {min(len(doc.records), args.limit)} 条）")
        return 0
    rec = next((r for r in doc.records if r.seq == args.record), None)
    if rec is None:
        print(f"错误：没有序号为 {args.record} 的记录", file=sys.stderr)
        return 2
    print(f"记录 #{rec.seq} · 类型 {rec.type_id}（{rec.table or '插件数据'}）· "
          f"偏移 {rec.offset} · body {rec.body_len} 字节 · md5 {'通过' if rec.md5_ok else '失败'}")
    print()
    print(_table(["#", "字段", "字节", "值"],
                 [[str(i), f.name, "NULL" if f.raw is None else str(f.length),
                   _trunc((f.text() or "").replace("\n", " "), args.width)]
                  for i, f in enumerate(rec.fields)],
                 [4, 18, 6, args.width]))
    print()
    print(dat.iter_raw_preview(dat.encode_record(rec.type_id, rec.fields, doc.version), 512))
    return 0


def cmd_export(args) -> int:
    a = _load(args)
    out = Path(args.out)

    def target_for(suffix: str, default_name: str) -> Path:
        """`-o` 给的是目录就放默认文件名，给的是带后缀的路径就直接用。"""
        if out.suffix.lower() == suffix or out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            return out
        out.mkdir(parents=True, exist_ok=True)
        return out / default_name

    if args.format == "markdown":
        res = export.export_markdown(a, out)
        print(f"已导出 {res['count']} 个 Markdown 文件到 {out}")
    elif args.format == "sqlite":
        res = export.export_sqlite(a, target_for(".db", "typecho-rebuilt.db"))
        print(f"已重建数据库 {res['path']}（{res['size'] / 1024 / 1024:.2f} MB）")
        for k, v in res["tables"].items():
            print(f"  {k:<14} {v}")
    elif args.format == "json":
        res = export.export_json(a, target_for(".json", "archive.json"))
        print(f"已导出 {res['path']}（{res['size'] / 1024 / 1024:.2f} MB）")
    elif args.format == "csv":
        res = export.export_csv(a, out)
        print(f"已导出 {len(res['files'])} 个 CSV 到 {out}")
    elif args.format == "html":
        p = htmlreport.write_static(a, target_for(".html", "archive-report.html"))
        print(f"已生成离线报告 {p}（{p.stat().st_size / 1024 / 1024:.2f} MB）")
    else:
        res = export.export_all(a, out)
        print(f"已导出到 {out}")
        print(f"  markdown  {res['markdown']['count']} 个文件")
        print(f"  sqlite    {res['sqlite']['path']}")
        print(f"  json      {res['json']['path']}")
        print(f"  csv       {len(res['csv']['files'])} 个文件")
        if args.zip:
            z = export.zip_dir(out, out.parent / (out.name + ".zip"))
            print(f"  压缩包    {z}（{z.stat().st_size / 1024 / 1024:.2f} MB）")
    return 0


def cmd_report(args) -> int:
    a = _load(args)
    out = htmlreport.write_static(a, args.out)
    print(f"已生成单文件报告 {out}（{out.stat().st_size / 1024 / 1024:.2f} MB）")
    print("双击即可用浏览器打开，无需联网。")
    return 0


def cmd_source(args) -> int:
    from .source import SourceTree
    tree = SourceTree.open(args.path)
    try:
        s = tree.summary()
        print(f"站点根目录  {s['root']}")
        print(f"版本        {s['version'] or '未知'}")
        print(f"后台目录    {s['admin_dir']}")
        d = s["db"]
        print(f"数据库      {d['adapter']} · {d['prefix']} · {d['size'] / 1024 / 1024:.2f} MB")
        print(f"文件        {s['files']['files']} 个 · PHP {s['files']['php_files']} 个 / "
              f"{s['files']['php_lines']:,} 行")
        print(f"附件        {s['uploads']['count']} 个 · {s['uploads']['size'] / 1024 / 1024:.1f} MB")
        print()
        print("主题：")
        for t in s["themes"]:
            print(f"  {'*' if t['active'] else ' '} {t['name']:<16} {t['title']}  （{t['files']} 文件）")
        print("插件：")
        for p in s["plugins"]:
            print(f"  {'*' if p['activated'] else ' '} {p['name']:<16} {p['title']}  （{p['files']} 文件）")
        print()
        print("设置：")
        for k, v in s["options"].items():
            print(f"  {k:<16} {_trunc(str(v), 70)}")
        if s["routing"]:
            print()
            print("路由：")
            for r in s["routing"]:
                print(f"  {r['name']:<12} {r['url']}")
        if s["warnings"]:
            print()
            print("提示：")
            for w in s["warnings"]:
                print(f"  - {w}")
    finally:
        tree.close()
    return 0


def cmd_diff(args) -> int:
    a = _load(args)
    if not a.diff_source():
        print("源码包中没有可用的数据库，无法对账", file=sys.stderr)
        return 2
    d = a.diff_source()
    print(f"源码数据库 {d['dbPath']}\n")
    labels = {"contents": "内容", "comments": "评论", "metas": "分类标签",
              "users": "用户", "relationships": "关联", "fields": "自定义字段"}
    rows = []
    for name, t in d["tables"].items():
        rows.append([labels.get(name, name), str(t["backup"]), str(t["db"]),
                     str(t["onlyBackupCount"]), str(t["onlyDbCount"])])
    print(_table(["表", "备份", "数据库", "仅备份有", "仅数据库有"], rows, [10, 8, 8, 10, 10]))
    for name, t in d["tables"].items():
        if not (t.get("onlyBackupDetail") or t.get("onlyDbDetail")):
            continue
        print(f"\n{labels.get(name, name)}：")
        for x in t.get("onlyBackupDetail", []):
            print(f"  + {x['text']}")
        for x in t.get("onlyDbDetail", []):
            print(f"  - {x['text']}")
    return 0


def cmd_selfcheck(args) -> int:
    """端到端自检：换新备份时先跑这个，确认解析链路仍然可靠。"""
    import sqlite3
    import tempfile

    from . import htmlreport
    from .sources import detect_kind
    from .store import Archive

    try:
        if detect_kind(args.dat) != "dat":
            print("自检针对 .dat 备份文件；源码包或数据库请直接用 info / serve 查看。")
            return 2
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    results: list[tuple[bool, str, str]] = []

    def check(name: str, fn):
        try:
            detail = fn() or ""
            results.append((True, name, str(detail)))
        except Exception as exc:  # noqa: BLE001 - 自检要把失败原因原样报出来
            results.append((False, name, f"{type(exc).__name__}: {exc}"))

    tmp = Path(tempfile.mkdtemp(prefix="tarch-check-"))
    doc = _parse_doc(args.dat)

    check("解析备份文件", lambda: f"{len(doc.records)} 条记录 · 格式 {doc.version}")
    check("逐条 md5 校验", lambda: (
        (_ for _ in ()).throw(AssertionError(f"{len(doc.md5_failures)} 条失败"))
        if doc.md5_failures else f"{len(doc.records)}/{len(doc.records)} 通过"))
    check("文件尾魔数", lambda: "完整" if doc.footer_ok else
          (_ for _ in ()).throw(AssertionError("缺少文件尾")))

    def roundtrip():
        out = dat.rebuild(doc, tmp / "rebuilt.dat")
        if out.read_bytes() != Path(args.dat).read_bytes():
            raise AssertionError("重建结果与原文件不一致")
        return "逐字节一致"
    check("无损重建", roundtrip)

    archive = Archive(doc=doc)
    if getattr(args, "source", None):
        from .source import SourceTree
        archive.source = SourceTree.open(args.source)
    check("构建归档模型", lambda: archive.build() or
          f"{len(archive.contents)} 内容 · {len(archive.comments)} 评论 · {len(archive.metas)} 分类标签")

    payload = {}

    def build_payload():
        payload.update(archive.payload())
        json.dumps(payload, ensure_ascii=False)
        return f"{len(json.dumps(payload, ensure_ascii=False)) / 1024 / 1024:.2f} MB JSON"
    check("生成界面数据", build_payload)
    check("体检", lambda: f"{len(payload.get('integrity', []))} 项结论")
    check("原始记录索引", lambda: (
        (_ for _ in ()).throw(AssertionError("数量不符"))
        if len(archive.record_index()) != len(doc.records)
        else f"{len(archive.record_index())} 条"))

    counts = {}

    def sqlite_roundtrip():
        res = export.export_sqlite(archive, tmp / "check.db")
        counts.update(res["tables"])
        con = sqlite3.connect(tmp / "check.db")
        for table, n in res["tables"].items():
            got = con.execute(f'select count(*) from "typecho_{table}"').fetchone()[0]
            if got != n:
                raise AssertionError(f"{table} 行数不符：{got} != {n}")
        con.close()
        return " · ".join(f"{k} {v}" for k, v in res["tables"].items())
    check("重建 SQLite", sqlite_roundtrip)

    def static_report():
        p = htmlreport.write_static(archive, tmp / "report.html")
        text = p.read_text("utf-8")
        if "window.__ARCHIVE__" not in text:
            raise AssertionError("报告缺少内嵌数据")
        return f"{p.stat().st_size / 1024 / 1024:.2f} MB 单文件"
    check("离线报告", static_report)

    print(f"自检对象：{args.dat}")
    print()
    for ok, name, detail in results:
        print(f"  {'通过' if ok else '失败'}  {name:<12} {detail}")
    failed = [r for r in results if not r[0]]
    print()
    if failed:
        print(f"共 {len(results)} 项，{len(failed)} 项失败")
        return 1
    print(f"共 {len(results)} 项全部通过")
    return 0


def cmd_demo(args) -> int:
    from . import demo
    info = demo.write_demo(args.out)
    print(f"已生成演示备份 {info['path']}")
    print(f"  {info['size'] / 1024:.1f} KB · {info['records']} 条记录 · "
          f"{info['posts']} 篇文章 · {info['pages']} 个页面 · {info['comments']} 条评论")
    print()
    print("打开看看：")
    print(f"  python -m typecho_archive serve {info['path']}")
    return 0


def cmd_serve(args) -> int:
    from . import dialogs, server

    target = args.dat
    source = args.source
    interactive = not target

    def ask() -> str | None:
        picked = dialogs.pick_file(
            "选择 Typecho 备份文件或站点源码包",
            "支持的文件 (*.dat;*.tar.gz;*.tgz;*.tar;*.zip;*.db)"
            "|*.dat;*.tar.gz;*.tgz;*.tar;*.zip;*.db"
            "|备份文件 (*.dat)|*.dat"
            "|站点源码包 (*.tar.gz;*.tgz;*.tar;*.zip)|*.tar.gz;*.tgz;*.tar;*.zip"
            "|SQLite 数据库 (*.db)|*.db"
            "|所有文件 (*.*)|*.*")
        return str(picked) if picked else None

    if not target:
        picked = ask()
        if not picked:
            print("没有选择文件，已退出。")
            return 2
        target = picked

    # 不管路径是拖来的还是选来的，都先确认它真的存在。
    # 路径在经过批处理命令行或 PowerShell 管道时都可能被编码破坏，
    # 与其抛一坨异常，不如把收到的原样告诉用户、再给一次选择的机会。
    if not Path(target).exists():
        message = f"找不到这个文件：\n{target}"
        print(f"错误：{message}", file=sys.stderr)
        if dialogs.available():
            dialogs.show_message(
                "Typecho 归档台",
                message + "\n\n路径里的中文可能在传递过程中转码失败。\n接下来请手动选择一次文件。")
            picked = ask()
            if not picked:
                return 2
            target = picked
        if not Path(target).exists():
            print(f"错误：路径仍然不存在：{target}", file=sys.stderr)
            return 2

    try:
        archive = Archive.open(target, source, verify=not args.no_verify)
    except (dat.DatError, FileNotFoundError, ValueError) as exc:
        message = f"无法读取这份数据：{exc}"
        print(f"错误：{message}", file=sys.stderr)
        if interactive:
            dialogs.show_message("Typecho 归档台", message)
        return 2

    server.run(archive, port=args.port, open_window=not args.no_open)
    return 0


# --------------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="typecho-archive",
        description="Typecho 归档台：读取 .dat 备份与站点源码包。",
        epilog="示例：python -m typecho_archive serve 备份.dat --source 源码.tar.gz",
    )
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="命令")

    def add_dat(sp, source=True):
        sp.add_argument("dat", help="数据来源：.dat 备份、源码包（.tar.gz/.tar/.zip/目录）或 .db")
        if source:
            sp.add_argument("-s", "--source", help="可选的站点源码包（.tar.gz / .tar / .zip / 目录）")
        sp.add_argument("--no-verify", action="store_true", help="跳过 md5 校验以加快载入")

    sp = sub.add_parser("info", help="查看备份概况")
    add_dat(sp)
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("list", help="列出内容")
    add_dat(sp)
    sp.add_argument("-t", "--type", help="内容类型：post,page,attachment（逗号分隔）")
    sp.add_argument("-S", "--status", help="状态：publish,hidden,private")
    sp.add_argument("-c", "--category", help="分类（名称或缩略名）")
    sp.add_argument("-g", "--tag", help="标签（名称或缩略名）")
    sp.add_argument("-q", "--search", help="关键词")
    sp.add_argument("-n", "--limit", type=int, default=0, help="最多显示条数")
    sp.add_argument("-w", "--width", type=int, default=52, help="标题列宽")
    sp.add_argument("--json", action="store_true", help="输出 JSON 便于脚本处理")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="显示单篇内容")
    add_dat(sp)
    sp.add_argument("cid", type=int, help="内容 ID")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-comments", action="store_true", help="不显示评论")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("search", help="全文搜索")
    add_dat(sp)
    sp.add_argument("query", help="搜索词")
    sp.add_argument("-n", "--limit", type=int, default=40)
    sp.add_argument("-w", "--width", type=int, default=52)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("verify", help="校验备份文件完整性")
    sp.add_argument("dat")
    sp.add_argument("--rebuild", metavar="路径", help="重建到指定路径并比对是否逐字节一致")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("inspect", help="查看记录结构")
    sp.add_argument("dat")
    sp.add_argument("-r", "--record", type=int, help="查看指定序号的记录详情")
    sp.add_argument("-n", "--limit", type=int, default=40)
    sp.add_argument("-w", "--width", type=int, default=60)
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("export", help="导出为其它格式")
    add_dat(sp)
    sp.add_argument("-o", "--out", default="export", help="输出目录或文件")
    sp.add_argument("-f", "--format", default="all",
                    choices=["all", "markdown", "sqlite", "json", "csv", "html"])
    sp.add_argument("--zip", action="store_true", help="额外打成 zip")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("report", help="生成单文件离线报告")
    add_dat(sp)
    sp.add_argument("-o", "--out", default="archive-report.html")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("source", help="只查看源码包信息")
    sp.add_argument("path", help="源码包路径")
    sp.set_defaults(func=cmd_source)

    sp = sub.add_parser("diff", help="备份与源码数据库对账")
    add_dat(sp)
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("serve", help="打开图形界面")
    sp.add_argument("dat", nargs="?", help="备份文件路径；省略则弹出选择框")
    sp.add_argument("-s", "--source", help="站点源码包（.tar.gz / .tar / .zip / 目录）")
    sp.add_argument("-p", "--port", type=int, default=0, help="端口，默认自动")
    sp.add_argument("--no-open", action="store_true", help="只启动服务，不打开窗口")
    sp.add_argument("--no-verify", action="store_true", help="跳过 md5 校验以加快载入")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("selfcheck", help="端到端自检（换新备份时先跑这个）")
    add_dat(sp)
    sp.set_defaults(func=cmd_selfcheck)

    sp = sub.add_parser("demo", help="生成一份合成演示数据，用来试玩")
    sp.add_argument("out", nargs="?", default="demo.dat", help="输出路径，默认 demo.dat")
    sp.set_defaults(func=cmd_demo)

    return p

def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - 命令行工具不该甩原始栈给普通用户
        if os.environ.get("TARCH_DEBUG"):
            raise
        message = f"{type(exc).__name__}: {exc}"
        print(f"\n出错了：{message}", file=sys.stderr)
        print("设置环境变量 TARCH_DEBUG=1 可以看到完整调用栈。", file=sys.stderr)
        try:
            from . import dialogs
            dialogs.show_message("Typecho 归档台 出错了", message)
        except Exception:  # noqa: BLE001 - 弹窗失败就算了
            pass
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
