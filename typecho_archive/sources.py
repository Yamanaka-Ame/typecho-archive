"""数据来源：可以直接吃 `.dat` 备份，也可以吃源码包里的 SQLite 库。

两边的表结构、字段顺序、类型都按备份的白名单对齐，所以上层（统计、体检、
导出、界面）完全不需要关心数据到底来自哪一种文件。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import dat, model
from .dat import Field, Record

ARCHIVE_EXTS = (".tar.gz", ".tgz", ".tar", ".zip")


def detect_kind(path: str | Path) -> str:
    """判断一个路径是备份、源码包还是数据库。

    返回 ``dat`` / ``source`` / ``db``；无法识别时抛 ``ValueError``。
    """
    p = Path(path)
    if p.is_dir():
        return "source"
    if not p.is_file():
        raise ValueError(f"路径不存在：{p}")

    name = p.name.lower()
    if name.endswith(ARCHIVE_EXTS):
        return "source"
    if name.endswith((".db", ".sqlite", ".sqlite3")):
        return "db"

    # 按内容判断，优先级最高：备份文件有固定魔数
    try:
        with p.open("rb") as fh:
            head = fh.read(dat.MAGIC_LEN)
    except OSError as exc:
        raise ValueError(f"无法读取 {p}：{exc}") from exc
    if head.startswith(b"%TYPECHO_BACKUP_"):
        return "dat"
    if head.startswith(b"SQLite format 3"):
        return "db"
    raise ValueError(f"无法识别的文件类型：{p.name}（既不是 .dat 备份，也不是源码包或数据库）")


def guess_prefix(db_path: str | Path) -> str:
    """从库里推断表前缀（常见是 typecho_，但也可能被改过）。"""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        names = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'")]
        con.close()
    except sqlite3.Error:
        return "typecho_"
    for name in names:
        if name.endswith("contents"):
            return name[: -len("contents")]
    return "typecho_"


class DbSource:
    """把 Typecho 的 SQLite 库包装成与 `DatDocument` 相同的接口。"""

    kind = "db"

    def __init__(self, path: str | Path, *, prefix: str = "typecho_"):
        self.path = Path(path)
        self.prefix = prefix or "typecho_"
        self.version = "001"
        self.footer_ok = True
        self.warnings: list[str] = []
        self.records: list[Record] = []
        self._rows: dict[str, int] = {}
        self._load()

    # ------------------------------------------------------------- 载入
    def _load(self) -> None:
        try:
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ValueError(f"无法打开数据库 {self.path}：{exc}") from exc
        con.row_factory = sqlite3.Row
        try:
            for table, fields in model.TABLE_FIELDS.items():
                try:
                    rows = con.execute(f'select * from "{self.prefix}{table}"').fetchall()
                except sqlite3.Error:
                    self.warnings.append(f"数据库中没有表 {self.prefix}{table}，已跳过")
                    continue
                self._rows[table] = len(rows)
                for row in rows:
                    present = set(row.keys())
                    values: list[Field] = []
                    for name in fields:
                        if name not in present:
                            values.append(Field(name, None))
                            continue
                        value = row[name]
                        if value is None:
                            values.append(Field(name, None))
                        else:
                            values.append(Field(name, str(value).encode("utf-8")))
                    self.records.append(Record(
                        seq=len(self.records),
                        type_id=dat.TABLE_IDS[table],
                        offset=0,
                        fields=values,
                        body_len=sum(f.length for f in values),
                        md5_ok=None,
                    ))
        finally:
            con.close()

    # ------------------------------------------------------------- 接口
    def of(self, table: str) -> list[Record]:
        type_id = dat.TABLE_IDS.get(table)
        return [r for r in self.records if r.type_id == type_id]

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def md5_failures(self) -> list[Record]:
        return []

    @property
    def types(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for r in self.records:
            counts[r.type_id] = counts.get(r.type_id, 0) + 1
        return counts

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table, n in self._rows.items():
            out[table] = n
        return out

    def summary(self) -> dict:
        return {
            "path": str(self.path),
            "size": self.size,
            "version": self.version,
            "kind": self.kind,
            "records": len(self.records),
            "tables": self.counts(),
            "footer_ok": True,
            "md5_ok": None,
            "md5_failed": 0,
            "warnings": list(self.warnings),
        }
