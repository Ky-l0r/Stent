"""B 站投稿页选择器校准工具（只读，不改任何数据）。

平台前端改版是**必然**发生的（README「已知限制」第 1 条就写了），
届时 ``stent/platforms/bilibili.py`` 里的选择器会失效。这个脚本用来快速取证：
打开真实投稿页，把可编辑元素、分区树、草稿列表 dump 下来，
照着结果改选择器即可，不用靠猜。

它**只读取**：不选文件、不填表单、不点任何提交按钮，随时可以中断。

用法::

    python tests/bili_probe.py                     # 投稿页表单结构（默认）
    python tests/bili_probe.py --partitions        # 实时分区树（archive/pre 接口）
    python tests/bili_probe.py --drafts            # 稿件管理里的草稿列表
    python tests/bili_probe.py --dev               # 用开发态 _data/browser 下的登录态

产物写入 ``_data/bili_probe/``。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"
DRAFT_URL = "https://member.bilibili.com/platform/upload-manager/article"
PARTITION_API = "https://member.bilibili.com/x/vupre/web/archive/pre"
OUT_DIR = Path(__file__).resolve().parent.parent / "_data" / "bili_probe"

#: 把页面里所有「可能被当成表单控件」的元素连属性带祖先类名一起 dump。
PROBE_JS = """
() => {
  const out = [];
  const seen = new Set();
  const push = (el, kind) => {
    if (!el || seen.has(el)) return;
    seen.add(el);
    const attrs = {};
    for (const a of el.attributes) attrs[a.name] = a.value;
    let cls = '';
    let node = el;
    let depth = 0;
    while (node && depth < 5) {
      if (node.className && typeof node.className === 'string') {
        cls = node.className.trim().split(/\\s+/).slice(0, 3).join('.') + (cls ? ' < ' + cls : '');
      }
      node = node.parentElement;
      depth += 1;
    }
    let rect = null;
    let visible = false;
    try {
      const r = el.getBoundingClientRect();
      visible = r.width > 0 && r.height > 0;
      rect = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    } catch (e) {}
    out.push({
      kind, tag: el.tagName.toLowerCase(), attrs,
      text: (el.innerText || el.value || '').slice(0, 80),
      placeholder: el.getAttribute('placeholder') || '',
      maxlength: el.getAttribute('maxlength') || '',
      contenteditable: el.getAttribute('contenteditable') || '',
      cls, visible, rect,
    });
  };
  document.querySelectorAll('input, textarea, [contenteditable="true"], [contenteditable=""]')
    .forEach((el) => push(el, 'field'));
  document.querySelectorAll('[class*="desc"], [class*="Desc"], [class*="remark"]')
    .forEach((el) => push(el, 'desc-node'));
  document.querySelectorAll('[class*="tag"], [class*="Tag"]').forEach((el) => push(el, 'tag-node'));
  document.querySelectorAll('[class*="select"], [class*="drop"]').forEach((el) => push(el, 'partition-node'));
  document.querySelectorAll('button, [class*="submit"], [class*="btn"]').forEach((el) => push(el, 'button'));
  return out;
}
"""

#: 在已登录页面里取实时分区树（和适配器走的是同一个接口）
PARTITION_JS = """
async () => {
  const out = {api_code: null, typelist: [], error: null};
  try {
    const resp = await fetch('//member.bilibili.com/x/vupre/web/archive/pre', {credentials: 'include'});
    const data = await resp.json();
    out.api_code = data.code;
    out.typelist = (data.data && data.data.typelist) || [];
  } catch (err) {
    out.error = String(err);
  }
  return out;
}
"""


def _profile(dev: bool) -> Path:
    if dev:
        from stent.paths import browser_dir

        return Path(browser_dir()) / "bilibili"
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Stent" / "browser" / "bilibili"


def _dump_fields(page, tag: str) -> None:
    rows = page.evaluate(PROBE_JS)
    (OUT_DIR / f"{tag}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / f"{tag}.html").write_text(page.content(), encoding="utf-8")
    print(f"\n===== {tag} · {page.url} · {len(rows)} 个节点 =====", flush=True)
    for row in rows:
        if row["kind"] == "field" and (row["visible"] or row["tag"] == "textarea"):
            print(
                f"  [field/{row['tag']}] vis={row['visible']} rect={row['rect']} "
                f"ph={row['placeholder']!r} maxlength={row['maxlength']!r} "
                f"ce={row['contenteditable']!r}\n"
                f"      attrs={row['attrs']}\n      cls={row['cls'][:130]}",
                flush=True,
            )
        elif row["kind"].endswith("-node") and row["visible"] and row["cls"]:
            print(f"  [{row['kind']}] cls={row['cls'][:110]}", flush=True)
        elif row["kind"] == "button" and row["visible"] and row["text"]:
            print(f"  [button] {row['text'][:40]!r} cls={row['cls'][:80]}", flush=True)


def _dump_partitions(page) -> None:
    data = page.evaluate(PARTITION_JS)
    (OUT_DIR / "partitions.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tree = data.get("typelist") or []
    print(f"\n===== 实时分区树 · 接口 code={data.get('api_code')} · "
          f"一级 {len(tree)} 个 =====", flush=True)
    if data.get("error"):
        print(f"  接口错误：{data['error']}", flush=True)
    for parent in tree:
        kids = parent.get("children") or []
        names = "、".join(f"{k.get('name')}({k.get('id')})" for k in kids[:10])
        print(f"  - {parent.get('name')} (id={parent.get('id')}) → {names}", flush=True)


def main() -> int:
    mode = "drafts" if "--drafts" in sys.argv else ("partitions" if "--partitions" in sys.argv else "form")
    dev = "--dev" in sys.argv
    profile = _profile(dev)
    print(f"登录态目录 = {profile}  存在 = {profile.exists()}", flush=True)
    if not profile.exists():
        print("登录态目录不存在：先在「发布中心 → 去登录」里扫码登录一次再探测", flush=True)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=True, locale="zh-CN",
            viewport={"width": 1600, "height": 1000},
            args=["--disable-blink-features=AutomationControlled", "--no-proxy-server"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(DRAFT_URL if mode == "drafts" else UPLOAD_URL,
                      wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(9_000)
            if mode == "partitions":
                _dump_fields(page, "form")
                _dump_partitions(page)
            else:
                _dump_fields(page, mode)
                if mode == "form":
                    _dump_partitions(page)
        finally:
            ctx.close()

    print(f"\n产物目录：{OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
