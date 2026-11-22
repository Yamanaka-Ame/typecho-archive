"""渲染回归：用演示数据里那几篇「刁钻样例」跑一遍真实浏览器。

    python -m unittest tests.test_render -v

没有任何真实站点数据也能跑——样例都在 `typecho_archive/demo.py` 里。
没有可用浏览器时自动跳过，所以不影响纯解析相关的测试。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from typecho_archive import demo
from tools import render_check


class RenderRegressionTest(unittest.TestCase):
    """把演示档案整本渲染一遍，断言不变量。"""

    @classmethod
    def setUpClass(cls) -> None:
        if render_check.find_browser() is None:
            raise unittest.SkipTest("没有可用的 Edge / Chrome")
        cls.tmp = Path(tempfile.mkdtemp(prefix="tarch_render_test_"))
        cls.dat = cls.tmp / "demo.dat"
        demo.write_demo(str(cls.dat))
        cls.results, cls.elapsed, cls.browser = render_check.run_check(
            str(cls.dat), jobs=3, timeout=90, quiet=True)

    def test_every_post_renders(self) -> None:
        self.assertTrue(self.results, "没有渲染任何文章")
        failed = [r for r in self.results if r["issues"]]
        detail = "; ".join(f"cid={r['cid']}: {' / '.join(r['issues'])}" for r in failed)
        self.assertEqual(failed, [], f"渲染自检发现问题：{detail}")

    def test_nothing_times_out(self) -> None:
        hung = [r["cid"] for r in self.results if r.get("hang")]
        self.assertEqual(hung, [], f"以下文章渲染超时（可能是死循环）：{hung}")

    def test_tricky_fixtures_have_components(self) -> None:
        """演示数据里那几篇 !!! 样例应当真的产出组件块。"""
        by_cid = {r["cid"]: r for r in self.results}
        rows = demo.POSTS

        def result_for(slug: str) -> dict:
            index = next(i for i, p in enumerate(rows) if p[0] == slug)
            result = by_cid.get(index + 1)
            self.assertIsNotNone(result, f"没有找到 {slug} 的渲染结果")
            return result

        component = result_for("stress-component")
        self.assertGreaterEqual(
            component.get("raw_blocks", 0), 2,
            f"stress-component 应当渲染出 2 个组件块，实际 {component.get('raw_blocks', 0)}")

        # !!! 里包 <!--SPOILER … --> 是 Spoiler 插件的标准写法，应当折叠而不是显示源码
        notes = result_for("stress-notes")
        self.assertGreaterEqual(
            notes.get("spoilers", 0), 1,
            f"stress-notes 应当渲染出至少 1 个遮罩，实际 {notes.get('spoilers', 0)}")


if __name__ == "__main__":
    unittest.main()
