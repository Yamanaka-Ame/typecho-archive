"""回归测试：拿到新备份时先跑这一组。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from typecho_archive import dat, demo, export, htmlreport
from typecho_archive.sources import DbSource, detect_kind, guess_prefix
from typecho_archive.store import Archive


class ArchiveTestCase(unittest.TestCase):
    """整条链路：生成 → 解析 → 校验 → 重建 → 导出。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="tarch_test_"))
        cls.dat_path = cls.tmp / "demo.dat"
        cls.info = demo.write_demo(str(cls.dat_path))

    # ------------------------------------------------------------ 格式
    def test_demo_is_deterministic(self) -> None:
        other = self.tmp / "demo2.dat"
        demo.write_demo(str(other))
        self.assertEqual(self.dat_path.read_bytes(), other.read_bytes())

    def test_parse_counts(self) -> None:
        doc = dat.parse(self.dat_path)
        self.assertEqual(len(doc.records), self.info["records"])
        self.assertTrue(doc.footer_ok)
        self.assertEqual(doc.md5_failures, [])
        self.assertEqual(len(doc.of("contents")), 17)
        self.assertEqual(len(doc.of("comments")), 10)

    def test_roundtrip_is_byte_identical(self) -> None:
        doc = dat.parse(self.dat_path)
        rebuilt = dat.rebuild(doc, self.tmp / "rebuilt.dat")
        self.assertEqual(rebuilt.read_bytes(), self.dat_path.read_bytes())

    def test_truncated_file_is_reported(self) -> None:
        raw = self.dat_path.read_bytes()
        broken = self.tmp / "broken.dat"
        broken.write_bytes(raw[: len(raw) // 2])
        doc = dat.parse(broken)
        self.assertFalse(doc.footer_ok)
        self.assertTrue(doc.warnings)
        self.assertLess(len(doc.records), self.info["records"])

    def test_rejects_non_backup(self) -> None:
        junk = self.tmp / "junk.dat"
        junk.write_text("not a backup", encoding="utf-8")
        with self.assertRaises(dat.DatError):
            dat.parse(junk)
        with self.assertRaises(ValueError):
            detect_kind(junk)

    # ------------------------------------------------------------ 模型
    def test_archive_model(self) -> None:
        archive = Archive.open(self.dat_path)
        rows = archive.post_rows()
        self.assertEqual(len(rows), 17)
        posts = [r for r in rows if r["type"] == "post"]
        self.assertEqual(len(posts), 12)
        self.assertEqual(sum(1 for r in posts if r["status"] == "hidden"), 1)
        self.assertEqual(sum(1 for r in posts if r["status"] == "private"), 1)

        stats = archive.stats()
        self.assertEqual(stats["counts"]["comments"], 10)
        self.assertEqual(stats["counts"]["categories"], 4)

    def test_integrity_has_no_errors(self) -> None:
        archive = Archive.open(self.dat_path)
        errors = [i for i in archive.integrity() if i["level"] == "error"]
        self.assertEqual(errors, [])

    def test_payload_is_json_serialisable(self) -> None:
        import json
        archive = Archive.open(self.dat_path)
        payload = archive.payload()
        text = json.dumps(payload, ensure_ascii=False)
        self.assertGreater(len(text), 5000)
        self.assertEqual(len(payload["posts"]), 17)
        self.assertEqual(payload["file"]["kind"], "dat")

    def test_record_index_matches(self) -> None:
        archive = Archive.open(self.dat_path)
        self.assertEqual(len(archive.record_index()), self.info["records"])
        first = archive.raw_record(0)
        self.assertIsNotNone(first)
        self.assertEqual(first["typeId"], 1)
        self.assertEqual(first["table"], "contents")
        self.assertGreater(first["size"], 8)          # 8 字节记录头 + header + body + 32 字节 md5
        self.assertTrue(first["hex"].startswith("00000000"))

    # ------------------------------------------------------------ 导出
    def test_sqlite_export_roundtrip(self) -> None:
        archive = Archive.open(self.dat_path)
        target = self.tmp / "rebuilt.db"
        result = export.export_sqlite(archive, target)
        self.assertEqual(result["tables"]["contents"], 17)

        con = sqlite3.connect(target)
        try:
            self.assertEqual(con.execute("select count(*) from typecho_contents").fetchone()[0], 17)
            self.assertEqual(con.execute("select count(*) from typecho_comments").fetchone()[0], 10)
            self.assertEqual(con.execute("pragma integrity_check").fetchone()[0], "ok")
            # 导出的库可以被重新当成数据源读回来
            self.assertGreater(len(DbSource(target, prefix=guess_prefix(target)).records), 0)
        finally:
            con.close()

    def test_markdown_export(self) -> None:
        archive = Archive.open(self.dat_path)
        out = self.tmp / "md"
        result = export.export_markdown(archive, out)
        self.assertEqual(result["count"], 15)  # 12 篇文章 + 3 个页面
        self.assertTrue((out / "index.md").is_file())

    def test_static_report(self) -> None:
        archive = Archive.open(self.dat_path)
        path = htmlreport.write_static(archive, self.tmp / "report.html")
        text = path.read_text("utf-8")
        self.assertIn("window.__ARCHIVE__", text)
        self.assertIn("<title>", text)


if __name__ == "__main__":
    unittest.main()
