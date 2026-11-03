"""Web 前端的真实浏览器冒烟：起接口服务 + 用 Chromium 打开页面。

与 ``tests/selftest_api.py``（只打接口）互补：这里验证**渲染**——
静态资源、token 注入、ES Module 加载、页面切换、主题切换、控制台报错。

运行::

    python tests/ui_web_smoke.py            # 无头
    python tests/ui_web_smoke.py --headed   # 看着它跑

需要 ``pip install playwright`` 与 ``python -m playwright install chromium``；
缺任意一项时脚本打印原因并以 0 退出（不算失败，CI 不该因为没装浏览器就红）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="stent-webui-")
os.environ["STENT_DATA_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from stent.api import build_app  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("未安装 playwright，跳过 Web 界面冒烟（pip install playwright）")
        return 0

    shots = Path(_TMP) / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)

    app = build_app()
    url = app.start()
    print(f"接口服务：{url}")

    headed = "--headed" in sys.argv
    problems: list[str] = []

    def launch(pw, headless: bool):
        """优先用内置 Chromium；版本对不上时退回系统 Edge / Chrome。

        本机常见的尴尬：装了 playwright，但内核是另一个版本（或压根没下载）——
        直接失败会让人误以为界面坏了，而系统 Edge 完全能跑同一套页面。
        """
        for kwargs in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                return pw.chromium.launch(headless=headless, **kwargs)
            except Exception:  # noqa: BLE001 - 逐个候选试
                continue
        return None

    try:
        with sync_playwright() as pw:
            browser = launch(pw, not headed)
            if browser is None:
                print("没有可用的 Chromium / Edge，跳过冒烟")
                return 0

            page = browser.new_page(viewport={"width": 1380, "height": 854})
            page.on(
                "console", lambda msg: problems.append(msg.text) if msg.type == "error" else None
            )
            page.on("pageerror", lambda exc: problems.append(str(exc)))

            print("\n=== 热点发现 ===")
            page.goto(f"{url}#/hot", wait_until="domcontentloaded")
            page.wait_for_selector(".page-title", timeout=15000)
            check("页面标题渲染", page.inner_text(".page-title") == "热点发现")
            check(
                "侧栏高亮正确",
                page.get_attribute('[data-route="hot"]', "class").find("active") >= 0,
            )

            print("\n=== 内容创作 ===")
            page.click('[data-route="create"]')
            page.wait_for_selector("#topic", timeout=15000)
            check("创作页挂载", page.is_visible("#topic"))
            check(
                "平台下拉有选项",
                page.eval_on_selector_all("#platform option", "els => els.length") >= 4,
            )
            page.fill("#topic", "冒烟测试主题")
            check("输入可写入", page.input_value("#topic") == "冒烟测试主题")
            page.screenshot(path=str(shots / "web-create.png"))

            print("\n=== 账号画像 ===")
            page.click('[data-route="profile"]')
            page.wait_for_selector("[data-dim]", timeout=15000)
            check("六维表单渲染", page.eval_on_selector_all("[data-dim]", "els => els.length") == 5)

            print("\n=== 设置与主题 ===")
            page.click('[data-route="settings"]')
            page.wait_for_selector("#base_url", timeout=15000)
            check("设置页渲染", page.is_visible("#api_key"))
            before = page.get_attribute("html", "data-theme")
            page.click("#theme-btn")
            page.wait_for_timeout(300)
            after = page.get_attribute("html", "data-theme")
            check("主题可切换", before != after, f"{before} -> {after}")
            page.screenshot(path=str(shots / "web-settings-dark.png"))
            page.click("#theme-btn")
            page.wait_for_timeout(200)

            print("\n=== 发布中心 ===")
            page.click('[data-route="publish"]')
            page.wait_for_selector("#body", timeout=15000)
            page.fill("#body", "这是一段用于检查的正文，长度足够触发字数检查以外的规则。")
            page.click("#check")
            page.wait_for_selector(".score", timeout=20000)
            check("检查结果渲染", page.is_visible(".score"))
            page.screenshot(path=str(shots / "web-publish.png"))

            print("\n=== 控制台 ===")
            real = [p for p in problems if "favicon" not in p.lower()]
            check("无 JS 报错", not real, "；".join(real[:3]))

            browser.close()
    finally:
        app.stop()

    print(f"\n===== Web 界面冒烟：{PASS} 通过 / {FAIL} 失败 =====")
    print(f"截图：{shots}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2) from None
