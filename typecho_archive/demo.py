"""生成一份合成演示数据（.dat 备份）。

用途：
- 仓库里不带任何真实站点数据，截图与文档用这份演示档案；
- 新用户可以先跑 ``demo`` 再打开界面，不必先有备份；
- 测试与回归可以用确定的内容，不受真实站点变动影响。

所有内容都是虚构的，时间戳固定，因此每次生成的字节完全一致。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Iterable

from . import dat

TZ = timezone(timedelta(hours=8))
BASE = datetime(2025, 3, 1, 9, 0, tzinfo=TZ)


def _ts(days: int = 0) -> str:
    return str(int((BASE + timedelta(days=days)).timestamp()))


def _md(body: str) -> str:
    return "<!--markdown-->" + body


# slug, 标题, 分类 slug, 标签, 天数偏移, 状态, 正文
POSTS: list[tuple[str, str, str, list[str], int, str, str]] = [
    ("hello-world", "欢迎使用 Typecho", "default", ["Typecho", "随笔"], 0, "publish", _md(
        "如果你看到这篇文章，说明这个站点已经跑起来了。\n\n"
        "这是一篇用于占位的示例文章，可以在后台删除或直接改写。\n\n"
        "## 接下来可以做什么\n\n"
        "1. 到 **设置 → 基本** 里把站点标题和副标题改成自己的；\n"
        "2. 到 **主题** 里挑一个顺眼的模板；\n"
        "3. 把这篇删掉，写下第一篇真正属于自己的文章。\n\n"
        "> 内容比模板重要得多。"
    )),
    ("markdown-notes", "用 Markdown 写博客的一点心得", "notes", ["Markdown", "写作"], 3, "publish", _md(
        "Markdown 的好处是：**写的时候只管内容**，格式交给渲染器。\n\n"
        "## 三条习惯\n\n"
        "- 标题只用来分层，不用来放大字号；\n"
        "- 代码块一定标注语言，高亮才准确；\n"
        "- 图片写替代文字，图挂了也知道是什么。\n\n"
        "```python\n"
        "def greet(name: str) -> str:\n"
        "    return f\"你好，{name}\"\n"
        "```\n\n"
        "| 语法 | 用途 | 备注 |\n"
        "|---|---|---|\n"
        "| `**粗体**` | 强调 | 别整段加粗 |\n"
        "| `code` | 行内代码 | 短片段 |\n"
        "| `> 引用` | 摘录 | 也可以放提示 |\n"
    )),
    ("backup-restore", "备份与恢复：把内容握在自己手里", "tutorial", ["备份", "Typecho"], 6, "publish", _md(
        "站点的内容数据就那么几张表，备份下来其实很小。\n\n"
        "## 备份包含什么\n\n"
        "文章、独立页面、附件记录、评论、分类标签、用户与自定义字段。\n"
        "它**不包含**主题、插件和站点设置——这些在源码包里。\n\n"
        "## 恢复之前先做的事\n\n"
        "1. 备份文件留一份在别处；\n"
        "2. 确认源码包里带着数据库文件；\n"
        "3. 恢复会清空现有内容表，别在正式站上试。\n\n"
        "[note type=\"warning\"] 恢复操作是不可逆的，务必先在测试环境验证一遍。[/note]"
    )),
    ("theme-layout", "主题改造：三栏布局的取舍", "tutorial", ["主题", "前端"], 9, "publish", _md(
        "三栏好看，但对阅读不友好。\n\n"
        "## 我的结论\n\n"
        "- 正文栏宽度控制在 720–800px；\n"
        "- 侧栏只在宽屏出现，小屏折叠到页脚；\n"
        "- 目录、标签、最近文章三选二就够了。\n\n"
        "改主题之前先备份，改砸了能退回去。"
    )),
    ("migration-log", "一次数据迁移的记录", "notes", ["备份", "运维"], 14, "publish", _md(
        "从旧空间搬到新机器，记几件容易忘的事。\n\n"
        "1. 先迁数据库，再迁 `usr/uploads`；\n"
        "2. 文件权限：目录 755、文件 644，`config.inc.php` 单独收紧；\n"
        "3. 域名换了记得改 `siteUrl`，否则后台跳转全错；\n"
        "4. DNS 生效前先用 hosts 验证一遍。\n\n"
        "整站加起来不到 200MB，比想象中轻。"
    )),
    ("comment-rules", "关于评论区的几条约定", "notes", ["随笔"], 18, "publish", _md(
        "评论区是站点的一部分，所以也要有规矩。\n\n"
        "- 允许不同意见，不允许人身攻击；\n"
        "- 广告和外链一律删除；\n"
        "- 需要审核的留言会晚一点出现，不是没收到。\n\n"
        "就这些。"
    )),
    ("plugin-minimalism", "插件真的需要那么多吗", "essay", ["插件"], 23, "publish", _md(
        "每装一个插件，就多一份要跟着升级的负担。\n\n"
        "## 我的取舍\n\n"
        "| 功能 | 是否装插件 | 原因 |\n"
        "|---|---|---|\n"
        "| 代码高亮 | 否 | 主题自带 |\n"
        "| 访问统计 | 否 | 用独立统计服务 |\n"
        "| 评论过滤 | 是 | 垃圾评论太烦 |\n"
        "| 内容遮罩 | 是 | 有些内容需要提示 |\n\n"
        "能不加就不加，是我这些年唯一的经验。"
    )),
    ("static-assets", "图片托管与静态资源", "tutorial", ["前端", "运维", "教程"], 27, "publish", _md(
        "图片放在同一台机器上最省事，但要注意几点。\n\n"
        "1. 上传目录单独备份；\n"
        "2. 开启浏览器缓存，`expires` 设一年；\n"
        "3. 大图先压到 200KB 以内；\n"
        "4. 用 WebP，体积能小一截。\n\n"
        "站点慢，十有八九是图太大。"
    )),
    ("writing-pace", "写作的节奏", "essay", ["随笔", "写作"], 32, "publish", _md(
        "以前总想一次写完一篇长文，结果什么都没写出来。\n\n"
        "后来改成：每天三百字，写完就存草稿。一个月下来，反而攒出了三四篇。\n\n"
        "> 完成比完美更重要。"
    )),
    ("html-sample", "一篇直接写 HTML 的文章", "notes", ["随笔"], 36, "publish",
     "<p>这篇没有用 Markdown，正文直接是 HTML。</p>"
     "<h2>为什么要这样</h2>"
     "<p>有些排版 Markdown 表达不了，比如这种并排的两块：</p>"
     "<div style=\"display:flex;gap:12px\">"
     "<div style=\"flex:1;border:1px solid #ddd;border-radius:8px;padding:12px\">左边一块</div>"
     "<div style=\"flex:1;border:1px solid #ddd;border-radius:8px;padding:12px\">右边一块</div>"
     "</div>"),
    ("draft-idea", "草稿：还没想好的题目", "notes", ["随笔"], 8, "hidden", _md(
        "先占个位置，想到再写。"
    )),
    ("private-note", "私密：只有自己看得到", "notes", ["随笔"], 10, "private", _md(
        "这是一篇私密文章，前台不会出现。"
    )),
]

# slug, 标题, 标签, 排序, 状态, 正文
PAGES: list[tuple[str, str, list[str], int, str, str]] = [
    ("about", "关于", [], 1, "publish", _md(
        "这是一个演示站点。\n\n"
        "所有内容均为虚构，用于展示 Typecho 备份阅读工具的效果。\n\n"
        "## 联系\n\n"
        "可以在留言板留言，或者直接发邮件。"
    )),
    ("links", "友链", [], 2, "publish", _md(
        "## 朋友们\n\n"
        "* [示例链接](https://example.com) — 一个占位链接\n"
        "* [另一个示例](https://example.org)"
    )),
    ("message-board", "留言板", [], 3, "publish", _md(
        "有什么想说的都可以写在这里。\n\n"
        "[note type=\"info\"] 留言会先进入审核队列，通过后显示。[/note]"
    )),
]

# 文件名, 标题, slug, 所属文章 cid, 字节数, MIME
ATTACHMENTS = [
    ("cover.png", "cover.png", "cover-png", 2, 184320, "image/png"),
    ("banner.webp", "screenshot.webp", "screenshot-webp", 4, 96256, "image/webp"),
]

# coid, 所属内容 cid, 父评论 coid, 作者, 内容, 天数
COMMENTS = [
    (1, 2, 0, "读者甲", "写得很清楚，收藏了。", -60),
    (2, 2, 1, "编辑乙", "第三条建议很实用。", -59),
    (3, 4, 0, "路人", "路过留个脚印。", -50),
    (4, 4, 0, "访客", "请问主题是哪一个？", -40),
    (5, 4, 4, "Demo Owner", "用的是默认模板改的。", -39),
    (6, 3, 0, "读者甲", "备份那篇救了我一次。", -30),
    (7, 5, 0, "访客", "迁移那几条太真实了。", -20),
    (8, 7, 0, "编辑乙", "同意，插件越少越省心。", -12),
    (9, 10, 0, "路人", "HTML 排版确实自由。", -6),
    (10, 9, 0, "读者甲", "期待更新。", -3),
]

USERS = [
    (1, "demo", "Demo Owner", "demo@example.com", "administrator", 0),
    (2, "reader", "读者甲", "reader@example.com", "subscriber", 40),
    (3, "editor", "编辑乙", "editor@example.com", "editor", 35),
]

CATEGORIES = [("默认分类", "default"), ("笔记", "notes"), ("教程", "tutorial"), ("随笔", "essay")]
TAGS = ["Typecho", "Markdown", "写作", "备份", "主题", "前端", "插件", "运维", "教程", "随笔"]


def _content_cids() -> list[tuple[int, str, list[str], str]]:
    """内容 cid 与它的分类/标签 slug，顺序固定：文章 → 页面 → 附件。"""
    plan: list[tuple[int, str, list[str], str]] = []
    cid = 0
    for slug, _title, cat, tags, _days, _status, _body in POSTS:
        cid += 1
        plan.append((cid, cat, list(tags), "post"))
    for slug, _title, tags, _order, _status, _body in PAGES:
        cid += 1
        plan.append((cid, "", list(tags), "page"))
    for _name, _title, _slug, _parent, _size, _mime in ATTACHMENTS:
        cid += 1
        plan.append((cid, "", [], "attachment"))
    return plan


def _records() -> tuple[list[dat.Record], dict]:
    """构造全部记录，返回 (记录列表, 索引信息)。"""
    records: list[dat.Record] = []

    def add(table: str, values: Iterable[tuple[str, object]]) -> None:
        rec = dat.make_record(dat.TABLE_IDS[table], list(values))
        rec.seq = len(records)
        records.append(rec)

    plan = _content_cids()

    # ---- 用户 -------------------------------------------------------
    for uid, name, screen, mail, group, days in USERS:
        add("users", [
            ("uid", uid), ("name", name), ("password", "$P$BdemoHashPlaceholder0000000000000000"),
            ("mail", mail), ("url", None), ("screenName", screen),
            ("created", _ts(-360 + days)), ("activated", _ts(-360 + days)),
            ("logged", _ts(days)), ("group", group), ("authCode", None),
        ])

    # ---- 分类与标签（计数 = 实际关联数）------------------------------
    mid = 0
    cat_ids: dict[str, int] = {}
    tag_ids: dict[str, int] = {}
    links: list[tuple[int, int]] = []

    for order, (name, slug) in enumerate(CATEGORIES):
        mid += 1
        cat_ids[slug] = mid
    for name in TAGS:
        mid += 1
        tag_ids[name] = mid

    for cid, cat, tags, kind in plan:
        if cat:
            links.append((cid, cat_ids[cat]))
        for tag in tags:
            if tag in tag_ids:
                links.append((cid, tag_ids[tag]))

    mid_counts: dict[int, int] = {}
    for _cid, m in links:
        mid_counts[m] = mid_counts.get(m, 0) + 1

    mid = 0
    for order, (name, slug) in enumerate(CATEGORIES):
        mid += 1
        add("metas", [("mid", mid), ("name", name), ("slug", slug), ("type", "category"),
                      ("description", None), ("count", mid_counts.get(mid, 0)),
                      ("order", order), ("parent", 0)])
    for order, name in enumerate(TAGS):
        mid += 1
        add("metas", [("mid", mid), ("name", name), ("slug", f"tag-{mid}"), ("type", "tag"),
                      ("description", None), ("count", mid_counts.get(mid, 0)),
                      ("order", order), ("parent", 0)])

    # ---- 文章与页面 --------------------------------------------------
    comment_counts: dict[int, int] = {}
    for _coid, cid, _parent, _author, _text, _days in COMMENTS:
        comment_counts[cid] = comment_counts.get(cid, 0) + 1

    cid = 0
    for slug, title, _cat, _tags, days, status, body in POSTS:
        cid += 1
        created = _ts(-120 + days * 3)
        add("contents", [
            ("cid", cid), ("title", title), ("slug", slug), ("created", created),
            ("modified", _ts(-120 + days * 3 + 1)), ("text", body),
            ("order", 0), ("authorId", 1), ("template", None), ("type", "post"),
            ("status", status), ("password", None),
            ("commentsNum", comment_counts.get(cid, 0)),
            ("allowComment", 1), ("allowPing", 0), ("allowFeed", 1), ("parent", 0),
        ])

    for slug, title, _tags, order, status, body in PAGES:
        cid += 1
        add("contents", [
            ("cid", cid), ("title", title), ("slug", slug),
            ("created", _ts(-100 + order * 2)), ("modified", _ts(-100 + order * 2)),
            ("text", body), ("order", order), ("authorId", 1),
            ("template", None), ("type", "page"), ("status", status), ("password", None),
            ("commentsNum", comment_counts.get(cid, 0)),
            ("allowComment", 1), ("allowPing", 0), ("allowFeed", 1), ("parent", 0),
        ])

    for name, title, slug, parent, size, mime in ATTACHMENTS:
        cid += 1
        meta = {
            "name": name, "path": f"/usr/uploads/2025/03/{600000 + cid}.{name.rsplit('.', 1)[-1]}",
            "size": size, "type": mime.split("/")[-1], "mime": mime, "isImage": True,
            "url": f"https://example.com/usr/uploads/2025/03/{600000 + cid}.{name.rsplit('.', 1)[-1]}",
        }
        add("contents", [
            ("cid", cid), ("title", title), ("slug", slug), ("created", _ts(-90)),
            ("modified", _ts(-90)), ("text", json.dumps(meta, ensure_ascii=False)),
            ("order", 0), ("authorId", 1), ("template", None), ("type", "attachment"),
            ("status", "publish"), ("password", None), ("commentsNum", 0),
            ("allowComment", 0), ("allowPing", 0), ("allowFeed", 0), ("parent", parent),
        ])

    # ---- 关联 -------------------------------------------------------
    seen: set[tuple[int, int]] = set()
    for c, m in links:
        if (c, m) in seen:
            continue
        seen.add((c, m))
        add("relationships", [("cid", c), ("mid", m)])

    # ---- 评论 -------------------------------------------------------
    for coid, cid_target, parent, author, text, days in COMMENTS:
        add("comments", [
            ("coid", coid), ("cid", cid_target), ("created", _ts(60 + days)),
            ("author", author), ("authorId", 0), ("ownerId", 0),
            ("mail", None), ("url", None), ("ip", "192.0.2.1"),
            ("agent", "Mozilla/5.0 (Demo)"),
            ("text", text), ("type", "comment"), ("status", "approved"),
            ("parent", parent),
        ])

    # ---- 自定义字段 --------------------------------------------------
    custom = [
        (2, "thumb", "https://example.com/cover.png"),
        (2, "summaryContent", "Markdown 写作习惯的三点总结"),
        (4, "ShowToc", "show"),
        (4, "CopyRight", "on"),
        (7, "ShowReward", "off"),
        (9, "NoCover", "off"),
    ]
    for c, name, value in custom:
        add("fields", [("cid", c), ("name", name), ("type", "str"),
                       ("str_value", value), ("int_value", 0), ("float_value", 0.0)])

    index = {
        "posts": len(POSTS),
        "pages": len(PAGES),
        "comments": len(COMMENTS),
        "records": len(records),
    }
    return records, index


def write_demo(path: str = "demo.dat") -> dict:
    """生成演示备份文件，返回统计信息。"""
    records, index = _records()
    out = dat.write(path, records, sort_by_table=True)
    counts: dict[str, int] = {}
    for rec in records:
        key = rec.table or ""
        counts[key] = counts.get(key, 0) + 1
    return {"path": str(out), "size": out.stat().st_size, "tables": counts, **index}


if __name__ == "__main__":  # pragma: no cover
    print(write_demo())
