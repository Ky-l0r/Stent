"""草稿往返回归：从草稿箱载回后，补充要求与快捷参数必须还在。

用户的原话是「内容创作那里从草稿箱导入，之前写的补充要求就没了，选择的快捷参数
也没了不方便我用 ai 重新生成」——这两项是「用同一组设定再生成一次」的上下文，
只存成品不存输入，载回来就等于从零重来。

用法::

    python tests/ui_draft_reload.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="stent-draft-")
os.environ["STENT_DATA_DIR"] = _TMP

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}" + (f"　{detail}" if detail else ""))
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    from stent.app import build_application
    from stent.core.db import db

    db.init()

    from stent.config import config_manager

    config_manager.update(onboarding_done=True, theme="light")

    app = build_application(["stent-draft-reload"])

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from stent.ui.context import AppContext
    from stent.ui.main_window import MainWindow

    ctx = AppContext(config_manager)
    ctx.selftest = True
    window = MainWindow(ctx)
    area = QApplication.primaryScreen().availableGeometry()
    window.resize(min(1280, area.width() - 40), min(820, area.height() - 40))
    window.show()

    def pump(times: int = 10) -> None:
        for _ in range(times):
            app.processEvents()

    def run(page) -> None:
        print("\n=== 创作页：保存草稿时连输入一起存 ===")
        page.topic_edit.setPlainText("副业避坑三步法")
        page.source_edit.setPlainText("素材：某乎高赞回答")
        page.extra_edit.setPlainText("必须提到三个具体品牌；不要出现价格；结尾引导评论")
        page.param_chips["goal"].set_value("直接带货", emit=True)
        page.param_chips["tone"].set_value("犀利点评", emit=True)
        page.param_chips["audience"].set_value("职场新人", emit=True)
        page.body_edit.setPlainText("这是正文，用于测试草稿往返。")
        page.summary_edit.setPlainText("这是简介")
        page.tag_cloud.set_tags(["副业", "避坑"])
        i = page.platform_combo.findData("bilibili")
        if i >= 0:
            page.platform_combo.setCurrentIndex(i)
        pump()

        page.save_draft(to_publish=False)
        pump()
        contents = db.list_contents(limit=5)
        check("草稿已入库", bool(contents), f"{len(contents)} 条")
        saved = contents[0]
        check(
            "草稿存下了补充要求",
            saved["extra"].startswith("必须提到三个具体品牌"),
            repr(saved["extra"]),
        )
        check("草稿存下了快捷参数", saved["params"].get("goal") == "直接带货", str(saved["params"]))
        check(
            "空值参数也一并存下（便于判断没设过）",
            "audience" in saved["params"],
            str(saved["params"]),
        )

        print("\n=== 载回草稿：输入区必须完整恢复 ===")
        # 先把输入区清干净，模拟「关掉程序再打开」
        page.topic_edit.setPlainText("")
        page.source_edit.setPlainText("")
        page.extra_edit.setPlainText("")
        for chip in page.param_chips.values():
            chip.set_value("")
        page.body_edit.setPlainText("")
        page.summary_edit.setPlainText("")
        page.tag_cloud.set_tags([])
        pump()

        page._load_content(db.get_content(int(saved["id"])))
        pump()

        check(
            "补充要求已恢复",
            page.extra_edit.toPlainText().strip()
            == "必须提到三个具体品牌；不要出现价格；结尾引导评论",
            repr(page.extra_edit.toPlainText()),
        )
        check(
            "快捷参数-内容目标已恢复",
            page.param_chips["goal"].value() == "直接带货",
            page.param_chips["goal"].value(),
        )
        check(
            "快捷参数-语气风格已恢复",
            page.param_chips["tone"].value() == "犀利点评",
            page.param_chips["tone"].value(),
        )
        check(
            "快捷参数-目标受众已恢复",
            page.param_chips["audience"].value() == "职场新人",
            page.param_chips["audience"].value(),
        )
        check(
            "参数胶囊文字同步更新",
            "直接带货" in page.param_chips["goal"].text(),
            page.param_chips["goal"].text(),
        )

        print("\n=== 再生成一次：请求里带的是原设定的参数 ===")
        req = page._request()
        check("请求可构造", req is not None)
        if req is not None:
            check("请求带上补充要求", req.extra.startswith("必须提到三个具体品牌"), repr(req.extra))
            check("请求带上内容目标", req.goal == "直接带货", req.goal)
            check("请求带上语气风格", req.tone == "犀利点评", req.tone)
            check("请求带上目标受众", req.audience == "职场新人", req.audience)
            check("请求带上原主题", req.topic == "副业避坑三步法", req.topic)

        print("\n=== 不存输入的老草稿也不能炸 ===")
        legacy_id = db.create_content(
            topic="老草稿",
            platform="bilibili",
            title="t",
            body="老正文",
            status="ready",
        )
        page._load_content(db.get_content(legacy_id))
        pump()
        check("老草稿载入不报错", page.body_edit.toPlainText().strip() == "老正文")
        check(
            "老草稿的补充要求为空而不是 None",
            page.extra_edit.toPlainText() == "",
            repr(page.extra_edit.toPlainText()),
        )
        check(
            "老草稿的参数全部清空（不会串到上一份草稿）",
            all(chip.value() == "" for chip in page.param_chips.values()),
            str({k: c.value() for k, c in page.param_chips.items()}),
        )

        window.close()
        pump()
        from stent.ui.workers import shutdown_workers

        shutdown_workers(3000)
        print(f"\n===== 草稿往返回归：{PASS} 通过 / {FAIL} 失败 =====")
        for item in FAILURES:
            print("  -", item)
        sys.stdout.flush()
        os._exit(0 if FAIL == 0 else 1)

    def start() -> None:
        window.navigate("create")
        page = window.stack.currentWidget()
        pump()
        run(page)

    QTimer.singleShot(600, start)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
