"""Typecho 数据模型：字段类型、枚举标签、文本处理。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from .dat import Field, Record

#: 各表的整数字段（用于排序与展示）
INT_FIELDS: dict[str, set[str]] = {
    "contents": {"cid", "created", "modified", "order", "authorId", "commentsNum", "parent"},
    "comments": {"coid", "cid", "created", "authorId", "ownerId", "parent"},
    "metas": {"mid", "count", "order", "parent"},
    "relationships": {"cid", "mid"},
    "users": {"uid", "created", "activated", "logged"},
    "fields": {"cid", "int_value", "float_value"},
}

#: 各表的 Unix 时间戳字段
TIME_FIELDS: dict[str, set[str]] = {
    "contents": {"created", "modified"},
    "comments": {"created"},
    "users": {"created", "activated", "logged"},
}

#: 各表的布尔（'0'/'1'）字段
BOOL_FIELDS: dict[str, set[str]] = {
    "contents": {"allowComment", "allowPing", "allowFeed"},
}

#: 各表的字段白名单，顺序与备份导出时一致（见 Widget\Backup::$fields）。
#: 从数据库读数据时也按这份清单取列，多出来的扩展列（views、likes 等）一律忽略，
#: 保证「.dat 备份」与「源码包数据库」两条来源产出的结构完全相同。
TABLE_FIELDS: dict[str, list[str]] = {
    "contents": [
        "cid", "title", "slug", "created", "modified", "text", "order", "authorId",
        "template", "type", "status", "password", "commentsNum",
        "allowComment", "allowPing", "allowFeed", "parent",
    ],
    "comments": [
        "coid", "cid", "created", "author", "authorId", "ownerId",
        "mail", "url", "ip", "agent", "text", "type", "status", "parent",
    ],
    "metas": ["mid", "name", "slug", "type", "description", "count", "order", "parent"],
    "relationships": ["cid", "mid"],
    "users": [
        "uid", "name", "password", "mail", "url", "screenName",
        "created", "activated", "logged", "group", "authCode",
    ],
    "fields": ["cid", "name", "type", "str_value", "int_value", "float_value"],
}

CONTENT_TYPES = {
    "post": "文章",
    "page": "独立页面",
    "attachment": "附件",
    "post_draft": "文章草稿",
    "page_draft": "页面草稿",
    "revision": "修订版本",
}

CONTENT_STATUS = {
    "publish": "已发布",
    "hidden": "草稿/隐藏",
    "private": "私密",
    "waiting": "待审核",
}

COMMENT_STATUS = {
    "approved": "已通过",
    "waiting": "待审核",
    "spam": "垃圾",
}

META_TYPES = {"category": "分类", "tag": "标签"}

USER_GROUPS = {
    "administrator": "管理员",
    "editor": "编辑",
    "contributor": "投稿者",
    "subscriber": "订阅者",
    "visitor": "访客",
}

COMMENT_FIELD_NAMES = {
    "coid": "评论 ID", "cid": "所属文章", "created": "时间", "author": "昵称",
    "authorId": "用户 ID", "ownerId": "作者 ID", "mail": "邮箱", "url": "网址",
    "ip": "IP", "agent": "浏览器", "text": "内容", "type": "类型",
    "status": "状态", "parent": "父评论",
}

CONTENT_FIELD_NAMES = {
    "cid": "内容 ID", "title": "标题", "slug": "缩略名", "created": "创建时间",
    "modified": "修改时间", "text": "正文", "order": "排序", "authorId": "作者 ID",
    "template": "模板", "type": "类型", "status": "状态", "password": "密码",
    "commentsNum": "评论数", "allowComment": "允许评论", "allowPing": "允许引用",
    "allowFeed": "包含在 RSS", "parent": "父级",
}


def fmt_time(ts: int | float | None, tz_offset: int = 8) -> str:
    """把 Unix 时间戳按站点时区偏移（小时）格式化。"""
    if not ts:
        return ""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone(timedelta(hours=tz_offset)))
    except (OverflowError, OSError, ValueError):
        return str(ts)
    return dt.strftime("%Y-%m-%d %H:%M")


def label(table: str, field_name: str, value: str | None) -> str:
    """把枚举值翻译成中文标签。"""
    if value is None:
        return "—"
    if table == "contents":
        if field_name == "type":
            return CONTENT_TYPES.get(value, value)
        if field_name == "status":
            return CONTENT_STATUS.get(value, value)
    elif table == "comments":
        if field_name == "status":
            return COMMENT_STATUS.get(value, value)
        if field_name == "type":
            return {"comment": "评论", "pingback": "引用", "trackback": "Trackback"}.get(value, value)
    elif table == "metas" and field_name == "type":
        return META_TYPES.get(value, value)
    elif table == "users" and field_name == "group":
        return USER_GROUPS.get(value, value)
    return value


_MD_PREFIX = re.compile(r"^\s*<!--\s*markdown\s*-->\s*", re.I)
_MORE = re.compile(r"<!--\s*more\s*-->", re.I)
_TAGS = re.compile(r"<[^>]+>")
_MD_INLINE = re.compile(r"[*_`#>\[\]!~|]")


def strip_markdown_prefix(text: str | None) -> str:
    if not text:
        return ""
    return _MD_PREFIX.sub("", text)


def is_markdown(text: str | None) -> bool:
    return bool(text and _MD_PREFIX.match(text))


def split_more(text: str) -> tuple[str, str | None]:
    """按 `<!--more-->` 拆分摘要与余下正文。"""
    m = _MORE.search(text)
    if not m:
        return text, None
    return text[: m.start()], text[m.end() :]


def plain_text(text: str | None) -> str:
    """去掉 Markdown/HTML 标记，得到纯文本。"""
    if not text:
        return ""
    t = strip_markdown_prefix(text)
    t = _TAGS.sub("", t)
    t = _MD_INLINE.sub("", t)
    t = re.sub(r"\(https?://[^)]*\)", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def excerpt(text: str | None, limit: int = 150) -> str:
    t = plain_text(text)
    return t if len(t) <= limit else t[:limit].rstrip() + "…"


def parse_attachment(text: str | None) -> dict | None:
    """附件记录的正文是 JSON 元数据。"""
    if not text or not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def decode_field(table: str, f: Field) -> object:
    """按字段类型把原始字节转成 Python 值。"""
    if f.raw is None:
        return None
    if table in BOOL_FIELDS and f.name in BOOL_FIELDS[table]:
        n = f.number()
        return bool(n) if n is not None else None
    if f.name in INT_FIELDS.get(table, ()):
        return f.number()
    return f.text()


def record_to_dict(rec: Record) -> dict:
    table = rec.table or ""
    return {f.name: decode_field(table, f) for f in rec.fields}
