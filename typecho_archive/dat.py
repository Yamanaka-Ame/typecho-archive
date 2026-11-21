"""Typecho 备份文件 (.dat) 的解析与重建。

文件结构（version 0001）::

    %TYPECHO_BACKUP_0001%          21 字节魔数（文件头）
    ┌ 记录 ───────────────────────────────────────────────┐
    │ meta    = pack('vvV', type, headerLen, bodyLen)      │ 8 字节，小端
    │ header  = JSON {"字段名": 字节长度 | null}            │ headerLen 字节
    │ body    = 各字段原始字节按 header 顺序首尾相接         │ bodyLen 字节
    │ md5     = md5(meta + header + body) 的 32 字节十六进制 │ 32 字节
    └────────────────────────────────────────────────────┘
    %TYPECHO_BACKUP_0001%          21 字节魔数（文件尾）

记录之间没有任何分隔符：字段边界只能由 header 中记录的长度还原，
`null` 表示该字段为 SQL NULL（在 body 中不占字节）。因此 bodyLen
必须等于 header 中各长度之和，这也是最可靠的完整性判据。

版本 `FILE`（早期格式）meta 为 6 字节且 bodyLen 需由 header 求和得出。
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

MAGIC_TEMPLATE = "%TYPECHO_BACKUP_XXXX%"
MAGIC_LEN = len(MAGIC_TEMPLATE)  # 21
DEFAULT_VERSION = "0001"
SUPPORTED_VERSIONS = ("0001", "FILE")

#: 记录类型编号 → Typecho 数据表名。6 以外的编号由插件自定义。
TABLES: dict[int, str] = {
    1: "contents",
    2: "comments",
    3: "metas",
    4: "relationships",
    5: "users",
    6: "fields",
}

TABLE_IDS: dict[str, int] = {v: k for k, v in TABLES.items()}


class DatError(Exception):
    """备份文件无法解析。"""


def table_name(type_id: int) -> str | None:
    return TABLES.get(type_id)


def magic(version: str = DEFAULT_VERSION) -> bytes:
    return MAGIC_TEMPLATE.replace("XXXX", version).encode("ascii")


@dataclass(slots=True)
class Field:
    """一个字段的原始字节；`raw is None` 表示 SQL NULL。"""

    name: str
    raw: bytes | None

    @property
    def is_null(self) -> bool:
        return self.raw is None

    @property
    def length(self) -> int:
        return 0 if self.raw is None else len(self.raw)

    def text(self, encoding: str = "utf-8") -> str | None:
        if self.raw is None:
            return None
        return self.raw.decode(encoding, "replace")

    def number(self) -> int | float | None:
        """按数字解释；失败时返回 None。"""
        t = self.text()
        if t is None or t.strip() == "":
            return None
        try:
            if any(c in t for c in ".eE"):
                return float(t)
            return int(t)
        except ValueError:
            return None

    def flag(self) -> bool | None:
        n = self.number()
        return None if n is None else bool(n)


@dataclass(slots=True)
class Record:
    """一条记录，对应一行数据。"""

    seq: int
    type_id: int
    offset: int
    fields: list[Field] = field(default_factory=list)
    body_len: int = 0
    md5_ok: bool | None = None

    @property
    def table(self) -> str | None:
        return TABLES.get(self.type_id)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    def field(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def text(self, name: str) -> str | None:
        f = self.field(name)
        return None if f is None else f.text()

    def number(self, name: str) -> int | float | None:
        f = self.field(name)
        return None if f is None else f.number()

    def mapping(self) -> dict[str, str | None]:
        return {f.name: f.text() for f in self.fields}

    def nulls(self) -> list[str]:
        return [f.name for f in self.fields if f.raw is None]

    def encoded_size(self) -> int:
        return 8 + len(self.schema_json()) + self.body_len + 32

    def schema_json(self) -> bytes:
        schema = {f.name: (None if f.raw is None else len(f.raw)) for f in self.fields}
        return json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def body_bytes(self) -> bytes:
        return b"".join(f.raw for f in self.fields if f.raw is not None)


@dataclass
class DatDocument:
    """解析后的备份文件。"""

    path: Path
    version: str = DEFAULT_VERSION
    kind: str = "dat"
    records: list[Record] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    footer_ok: bool = False
    size: int = 0

    def of(self, table: str) -> list[Record]:
        return [r for r in self.records if r.table == table]

    @property
    def types(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for r in self.records:
            counts[r.type_id] = counts.get(r.type_id, 0) + 1
        return counts

    @property
    def md5_failures(self) -> list[Record]:
        return [r for r in self.records if r.md5_ok is False]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.records:
            key = r.table or f"type#{r.type_id}"
            out[key] = out.get(key, 0) + 1
        return out

    def summary(self) -> dict:
        return {
            "path": str(self.path),
            "size": self.size,
            "version": self.version,
            "kind": self.kind,
            "records": len(self.records),
            "tables": self.counts(),
            "footer_ok": self.footer_ok,
            "md5_ok": len(self.records) - len(self.md5_failures),
            "md5_failed": len(self.md5_failures),
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


def _read_magic(data: bytes, pos: int) -> str | None:
    if pos + MAGIC_LEN > len(data):
        return None
    chunk = data[pos : pos + MAGIC_LEN]
    if chunk[:16] != b"%TYPECHO_BACKUP_":
        return None
    if chunk[16:20].decode("ascii", "replace") == "XXXX":
        return None
    if not chunk.endswith(b"%"):
        return None
    return chunk[16:20].decode("ascii", "replace")


def parse(path: str | Path, *, verify: bool = True, encoding: str = "utf-8") -> DatDocument:
    """解析备份文件。

    `verify=False` 时跳过 md5 校验（大文件可提速）。
    """
    p = Path(path)
    data = p.read_bytes()
    size = len(data)

    if size < MAGIC_LEN * 2:
        raise DatError(f"文件过小，不是 Typecho 备份文件：{p}")

    head_version = _read_magic(data, 0)
    if head_version is None:
        raise DatError(f"文件头魔数无效，不是 Typecho 备份文件：{p}")

    version = head_version
    meta_len = 6 if version == "FILE" else 8
    doc = DatDocument(path=p, version=version, size=size)
    if version not in SUPPORTED_VERSIONS:
        doc.warnings.append(f"未知格式版本 {version}，按 0001 规则尝试解析")

    pos = MAGIC_LEN
    seq = 0
    # PHP 的 pack('vvV') 小端等价于 Python 的 '<HHI'（'V' 仅是 PHP 的格式符）
    name_fmt = "HHH" if version == "FILE" else "HHI"

    while pos + meta_len <= size:
        # 文件尾魔数
        if size - pos == MAGIC_LEN and _read_magic(data, pos):
            doc.footer_ok = True
            pos += MAGIC_LEN
            break

        start = pos
        meta = data[pos : pos + meta_len]
        pos += meta_len
        parts = struct.unpack("<" + name_fmt, meta)
        type_id = parts[0]
        header_len = parts[1]
        body_len = parts[2] if version != "FILE" else 0

        if header_len and pos + header_len > size:
            doc.warnings.append(f"记录 #{seq} 在偏移 {start} 处头部越界，文件可能被截断")
            break

        header = data[pos : pos + header_len]
        pos += header_len

        try:
            schema = json.loads(header)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            doc.warnings.append(f"记录 #{seq} 在偏移 {start} 处 header 不是合法 JSON：{exc}")
            break

        if not isinstance(schema, dict):
            doc.warnings.append(f"记录 #{seq} 在偏移 {start} 处 header 结构异常")
            break

        total = sum(v for v in schema.values() if isinstance(v, int))
        if version == "FILE":
            body_len = total
        elif total != body_len:
            doc.warnings.append(
                f"记录 #{seq} 在偏移 {start} 处长度不自洽：header 求和 {total} ≠ bodyLen {body_len}"
            )

        body = data[pos : pos + body_len]
        pos += body_len

        stored = data[pos : pos + 32]
        pos += 32

        md5_ok: bool | None = None
        if verify:
            expect = hashlib.md5(meta + header + body).hexdigest().encode("ascii")
            md5_ok = stored == expect
            if not md5_ok:
                doc.warnings.append(f"记录 #{seq}（{table_name(type_id) or type_id}）md5 校验失败")

        fields: list[Field] = []
        cursor = 0
        for key, length in schema.items():
            if length is None:
                fields.append(Field(key, None))
            else:
                fields.append(Field(key, body[cursor : cursor + length]))
                cursor += length

        doc.records.append(
            Record(
                seq=seq,
                type_id=type_id,
                offset=start,
                fields=fields,
                body_len=body_len,
                md5_ok=md5_ok,
            )
        )
        seq += 1

    if not doc.footer_ok and pos >= size:
        doc.warnings.append("未找到文件尾魔数，文件可能被截断或由旧版本生成")
    return doc


# --------------------------------------------------------------------------
# 重建
# --------------------------------------------------------------------------


def encode_record(type_id: int, fields: list[Field], version: str = DEFAULT_VERSION) -> bytes:
    """把一条记录编码为原始字节，与 PHP 端输出逐字节一致。"""
    schema = {f.name: (None if f.raw is None else len(f.raw)) for f in fields}
    header = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = b"".join(f.raw for f in fields if f.raw is not None)
    if version == "FILE":
        meta = struct.pack("<HHH", type_id, len(header), 0)
    else:
        meta = struct.pack("<HHI", type_id, len(header), len(body))
    return meta + header + body + hashlib.md5(meta + header + body).hexdigest().encode("ascii")


def write(
    path: str | Path,
    records: list[Record],
    *,
    version: str = DEFAULT_VERSION,
    sort_by_table: bool = True,
) -> Path:
    """把记录写回 .dat 文件。`sort_by_table` 会按表分组（与官方导出一致）。"""
    out = Path(path)
    ordered = records
    if sort_by_table:
        order = {tid: i for i, tid in enumerate(sorted(TABLE_IDS.values()))}
        ordered = sorted(records, key=lambda r: (order.get(r.type_id, 99), r.seq))
    buf = bytearray(magic(version))
    for rec in ordered:
        buf += encode_record(rec.type_id, rec.fields, version)
    buf += magic(version)
    out.write_bytes(bytes(buf))
    return out


def rebuild(doc: DatDocument, path: str | Path) -> Path:
    """按原顺序重建文件，用于校验解析是否无损。"""
    return write(path, doc.records, version=doc.version, sort_by_table=False)


# --------------------------------------------------------------------------
# 编辑辅助
# --------------------------------------------------------------------------


def make_record(type_id: int, values: list[tuple[str, str | bytes | None]], seq: int = 0) -> Record:
    """构造新记录：`values` 为 (字段名, 值) 序列，`None` 表示 NULL。"""
    fields: list[Field] = []
    for key, val in values:
        if val is None:
            fields.append(Field(key, None))
        elif isinstance(val, bytes):
            fields.append(Field(key, val))
        else:
            fields.append(Field(key, str(val).encode("utf-8")))
    return Record(seq=seq, type_id=type_id, offset=0, fields=fields,
                  body_len=sum(f.length for f in fields))


def set_field(record: Record, name: str, value: str | bytes | None) -> None:
    """就地修改字段，保持字段顺序与其它字段不动。"""
    for f in record.fields:
        if f.name == name:
            if value is None:
                f.raw = None
            elif isinstance(value, bytes):
                f.raw = value
            else:
                f.raw = str(value).encode("utf-8")
            record.body_len = sum(x.length for x in record.fields)
            return
    raise KeyError(f"记录中没有字段 {name}")


def iter_raw_preview(data: bytes, limit: int = 512) -> str:
    """把一段字节渲染成 hexdump 文本。"""
    lines = []
    for i in range(0, min(len(data), limit), 16):
        chunk = data[i : i + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:08x}  {hexpart:<47}  {asc}")
    return "\n".join(lines)
