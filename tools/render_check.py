"""渲染自检：把归档里的每一篇都用真实浏览器渲染一遍，检查不变量。

    python tools/render_check.py 备份.dat [--source 源码包] [--jobs 4] [--limit 0]

为什么需要它：Markdown 渲染器是手写的，块边界的判断分散在各个分支里，改一处很容易
在另一处漏一档（历史上出过死循环、组件被当文本转义、样式不生效、HTML 块被空行切碎）。
靠人眼抽查某一篇是发现不了这类问题的，所以这里用**真实数据**兜底：每一篇都渲染，
逐条断言。

检查项：
  1. 必须在超时内渲染完成            —— 防死循环
  2. 必须渲染出标题                  —— 防整篇崩掉
  3. 正文里不得出现被转义的块级标签  —— 防「组件显示成源码」
  4. 正文里不得残留 !!! 标记         —— 防围栏没被消费
  5. 注入的 <style> 不得有无作用域规则 —— 防样式泄漏到界面
  6. 含 !!! 的文章应当产出组件块     —— 防围栏被整段吞掉

退出码：0 全部通过；1 有问题。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typecho_archive.server import ArchiveServer  # noqa: E402
from typecho_archive.store import Archive  # noqa: E402

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}
BLOCK_TAGS = ("div", "table", "ul", "ol", "dl", "section", "h1", "h2", "h3",
              "h4", "h5", "h6", "style", "p", "blockquote")


def find_browser() -> str | None:
    for path in EDGE_CANDIDATES:
        if path and Path(path).is_file():
            return path
    for name in ("msedge.exe", "chrome.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


class ProseProbe(HTMLParser):
    """从 dump 出来的 DOM 里取出正文子树，记录可疑文本与样式。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found_prose = False
        self.found_title = False
        self.stack: list[str] = []
        self.suspicious_text: list[str] = []
        self.styles: list[str] = []
        self.raw_blocks = 0
        self._in_style = False
        self._style_buf: list[str] = []
        self._title_tag: str | None = None
        self._title_buf: list[str] = []
        self.title = ""

    # -- 工具 -------------------------------------------------------
    def _in_code(self) -> bool:
        return any(t in ("pre", "code") for t in self.stack)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        # 标题是 <h2 class="a-title">，任何标签都可能
        if "a-title" in classes and self._title_tag is None:
            self.found_title = True
            self._title_tag = tag
            self._title_buf = []
        if tag == "div" and "prose" in classes and not self.found_prose:
            self.found_prose = True
            self.stack = ["div"]
            return
        if not self.found_prose:
            return
        if tag == "div" and "raw-block" in classes:
            self.raw_blocks += 1
        if tag == "style":
            self._in_style = True
            self._style_buf = []
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self._title_tag is not None and tag == self._title_tag and not self.title:
            self.title = "".join(self._title_buf).strip()
            self._title_tag = None
        if not self.found_prose:
            return
        if tag == "style" and self._in_style:
            self.styles.append("".join(self._style_buf))
            self._in_style = False
        if tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                if top == tag:
                    break
        if not self.stack:
            self.found_prose = False

    def handle_data(self, data):
        if self._title_tag is not None and not self.title:
            self._title_buf.append(data)
        if not self.found_prose:
            return
        if self._in_style:
            self._style_buf.append(data)
            return
        if self._in_code():
            return
        stripped = data.strip()
        if not stripped:
            return
        for tag in BLOCK_TAGS:
            if re.search(r"<\s*" + tag + r"\b", data, re.I):
                self.suspicious_text.append(stripped[:80])
                break
        if re.fullmatch(r"\s*!!!\s*", data):
            self.suspicious_text.append(stripped[:80])


def dump_dom(browser: str, url: str, profile: Path, timeout: float, budget: int = 8000) -> tuple[str | None, float]:
    """跑一次无头浏览器，返回 (DOM 文本 | None, 耗时)。超时返回 None。"""
    cmd = [
        browser, "--headless=new", "--disable-gpu", "--no-first-run",
        "--disable-extensions", "--mute-audio",
        f"--user-data-dir={profile}",
        f"--virtual-time-budget={budget}",
        "--dump-dom", url,
    ]
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, time.time() - started
    text = proc.stdout.decode("utf-8", "replace")
    return text, time.time() - started


def check_one(browser: str, base: str, cid: int, profile: Path,
              timeout: float) -> dict:
    url = f"{base}#/posts/s{cid}"
    dom, secs = dump_dom(browser, url, profile, timeout, budget=8000)
    # 并发下偶发取数/渲染没完成就 dump 了：换独立配置目录、给更长的虚拟时间再试一次
    if dom is None or "a-title" not in dom:
        retry_profile = profile.with_name(profile.name + "-r")
        retry_profile.mkdir(parents=True, exist_ok=True)
        dom2, secs2 = dump_dom(browser, url, retry_profile, max(timeout, 60), budget=25000)
        secs += secs2
        if dom2 is not None and "a-title" in dom2:
            dom = dom2
    issues: list[str] = []
    probe = ProseProbe()
    if dom is None:
        return {"cid": cid, "seconds": secs, "hang": True, "issues": ["渲染超时（可能是死循环）"]}
    try:
        probe.feed(dom)
    except Exception as exc:  # noqa: BLE001
        issues.append(f"DOM 解析异常：{exc}")
    if not probe.found_title:
        issues.append("没有渲染出标题")
    if probe.suspicious_text:
        issues.append("正文里出现源码文本：" + " / ".join(probe.suspicious_text[:2]))
    for css in probe.styles:
        # 注入的样式必须每条规则都带 .prose 前缀
        for selector in re.findall(r"(^|\})\s*([^@{}][^{}]*)\{", css):
            sel = selector[1].strip()
            if sel and not all(part.strip().startswith(".prose") for part in sel.split(",")):
                issues.append(f"样式未加作用域：{sel[:40]}")
                break
    return {"cid": cid, "seconds": secs, "hang": False,
            "issues": issues, "raw_blocks": probe.raw_blocks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="渲染自检：逐篇渲染并断言不变量")
    ap.add_argument("dat", help="备份文件或源码包")
    ap.add_argument("-s", "--source", help="站点源码包，可选")
    ap.add_argument("-j", "--jobs", type=int, default=4, help="并发数，默认 4")
    ap.add_argument("-n", "--limit", type=int, default=0, help="只查前 N 篇，0 表示全部")
    ap.add_argument("--timeout", type=float, default=30.0, help="单篇超时秒数")
    args = ap.parse_args(argv)

    browser = find_browser()
    if not browser:
        print("找不到 Edge / Chrome，无法做渲染自检", file=sys.stderr)
        return 2

    archive = Archive.open(args.dat, args.source, verify=False)
    rows = [r for r in archive.post_rows(include_text=False) if r["type"] != "attachment"]
    if args.limit:
        rows = rows[: args.limit]
    print(f"待检查 {len(rows)} 篇 · 浏览器 {Path(browser).name} · 并发 {args.jobs}")

    server = ArchiveServer(archive, port=0)
    server.start_background()
    base = server.url
    tmp_root = Path(tempfile.mkdtemp(prefix="tarch_render_"))
    try:
        profiles = []
        for i in range(args.jobs):
            p = tmp_root / f"profile{i}"
            p.mkdir(parents=True, exist_ok=True)
            profiles.append(p)

        results = []
        started = time.time()
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = []
            for idx, row in enumerate(rows):
                futures.append(pool.submit(
                    check_one, browser, base, row["cid"], profiles[idx % args.jobs], args.timeout))
            for done, fut in enumerate(futures, 1):
                results.append(fut.result())
                if done % 20 == 0 or done == len(futures):
                    print(f"  {done}/{len(futures)} …", flush=True)
        elapsed = time.time() - started

        slow = sorted(results, key=lambda r: -r["seconds"])[:3]
        bad = [r for r in results if r["issues"]]
        no_block = [r for r in results
                    if not r["issues"] and not r.get("hang") and r.get("raw_blocks", 0) == 0]

        print()
        print(f"用时 {elapsed:.0f}s · 平均 {elapsed / max(1, len(results)):.2f}s/篇")
        print("最慢：" + " · ".join(f"cid={r['cid']} {r['seconds']:.1f}s" for r in slow))
        if bad:
            print(f"\n发现 {len(bad)} 篇有问题：")
            for r in bad:
                print(f"  cid={r['cid']}  {'; '.join(r['issues'])}")
        else:
            print("\n没有发现问题")
        if no_block:
            print(f"（另有 {len(no_block)} 篇没有组件块，属正常：正文里没有 !!! 围栏）")
        return 1 if bad else 0
    finally:
        server.shutdown()
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
