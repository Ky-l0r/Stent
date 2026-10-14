"""发布中心「发布前检查」栏布局回归（真实窗口）。

锁住三个曾经出问题的点：

1. 展开「建议修改」后，卡片必须待在与阻断项同一个滚动区里，
   **不能**把卡片顶到检查卡片外面被裁掉（用户看到的「显示不全」）；
2. 滚动区里的每张结果卡都不允许被压扁（高度必须 >= 最小需要高度）；
3. 窗口变窄时，「确认发布」条上的安全模式徽章要主动让位，
   保证「我已确认…」这句必须看清的话不被截断。

用法::

    python tests/ui_publish_layout.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="stent-publayout-")
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


def _seed() -> int:
    from stent.core.db import db

    body = (
        "很多人在做副业时都踩过同一个坑：先花钱买课，再花时间学，最后发现根本没人买。\n"
        "下面这三步是我自己验证过的：第一步，列出你身边 10 个真实存在的问题；"
        "第二步，挑一个你已经解决过的问题；第三步，把它做成最小可交付的形态卖出去。\n"
        "顺便说一句：包治百病、稳赚不赔、一夜暴富这类词平台会判定为违规宣传，要主动规避。"
    )
    return db.create_content(
        topic="副业避坑三步法",
        platform="bilibili",
        title="副业避坑：先找买家再选品，这三步少走三年弯路（标题故意写长用来触发字数检查）",
        titles=[],
        body=body,
        summary="简介占位",
        tags=[f"标签{i}" for i in range(1, 14)],
        status="ready",
        source="manual",
        extra="必须提到三个具体品牌；不要出现价格",
        params={"goal": "直接带货", "tone": "犀利点评"},
    )


def main() -> int:
    from stent.app import build_application
    from stent.core.db import db

    db.init()
    content_id = _seed()

    from stent.config import config_manager

    config_manager.update(onboarding_done=True, theme="light")

    app = build_application(["stent-publayout"])

    from PySide6.QtCore import QRect, QTimer
    from PySide6.QtWidgets import QApplication

    from stent.ui.context import AppContext
    from stent.ui.main_window import MainWindow

    ctx = AppContext(config_manager)
    ctx.selftest = True
    window = MainWindow(ctx)
    area = QApplication.primaryScreen().availableGeometry()
    width, height = min(1120, area.width() - 40), min(720, area.height() - 40)
    window.resize(width, height)
    window.move(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2)
    window.show()

    steps = {"i": 0}

    def pump(times: int = 8) -> None:
        for _ in range(times):
            app.processEvents()

    def run_checks(page) -> None:
        view = page.check_view
        card = view.parentWidget()
        column = view.column
        toggle = view.toggle

        print("\n=== 检查栏：折叠状态 ===")
        check("检查栏有建议可展开", toggle.isVisible() and len(view._advisories) > 0,
              f"建议 {len(view._advisories)} 条")
        viewport_h = view.scroll.viewport().height()
        check("折叠时可视区至少看得下一条结果卡（不被压成一条缝）",
              viewport_h >= 90, f"可视区高={viewport_h}")

        # 展开建议：这是「显示不全」最容易复现的路径
        toggle.click()
        pump(10)
        print("\n=== 检查栏：展开建议后 ===")
        check("展开后建议卡进入滚动区",
              view._suggest_open and column.count() >= len(view._errors) + len(view._advisories),
              f"column={column.count()} 错误={len(view._errors)} 建议={len(view._advisories)}")
        check("展开后出现滚动条（内容确实超出视口）",
              view.scroll.verticalScrollBar().maximum() > 0,
              f"max={view.scroll.verticalScrollBar().maximum()}")

        squashed = []
        for i in range(column.count()):
            w = column.itemAt(i).widget()
            if w is None or not w.isVisible():
                continue
            need = w.minimumSizeHint().height()
            if w.height() < need:
                squashed.append(f"{w.objectName()} {w.height()}<{need}")
        check("滚动区里的卡片都没有被压扁", not squashed, "；".join(squashed))

        # 检查栏自己的控件（标题行 / 滚动区 / toggle）不能被父卡片裁掉
        bounds = card.rect()
        stray = []
        for child in (view.scroll, toggle):
            if not child.isVisible():
                continue
            top_left = child.mapTo(card, child.rect().topLeft())
            geo = QRect(top_left, child.size())
            if not bounds.contains(geo):
                stray.append(f"{type(child).__name__} {geo.getRect()} 超出 {bounds.getRect()}")
        check("检查栏自身控件没有被裁切", not stray, "；".join(stray))

        # 检查栏与预览卡在分栏器里各占一槽，绝不能互相压叠：
        # 两块都有自己的最小高度，加起来超过分栏器可用高度时，
        # QSplitter 会让先建的那块溢出槽位、直接盖到另一块上。
        splitter = card.parentWidget()
        preview_card = splitter.widget(1)
        gap = preview_card.geometry().top() - card.geometry().bottom()
        check("检查栏没有压到预览卡上", gap >= 0,
              f"间隙={gap}px 检查高={card.height()} 预览高={preview_card.height()} "
              f"分栏器={splitter.height()}")
        check("预览框没有被挤到最小高度之下",
              page.preview_edit.height() >= page.preview_edit.minimumHeight(),
              f"高={page.preview_edit.height()} 最小={page.preview_edit.minimumHeight()}")

        # 「发布环境未就绪」提示卡展开后，分栏器只剩两百多像素，这是最挤的一种状态：
        # 两块卡片的最小高度之和一旦超过分栏器自身高度，QSplitter 就会让先建的那块
        # 溢出槽位、直接盖到另一块上（曾经实测检查卡压住预览卡 9px）。
        # 这里主动展开提示卡再验一遍，锁住「装不下时两块一起让位」。
        print("\n=== 环境提示卡展开（最挤的状态）===")
        page.env_card.setVisible(True)
        page._relayout()
        pump(10)
        cramped_gap = preview_card.geometry().top() - card.geometry().bottom()
        check("提示卡展开后两块仍不压叠", cramped_gap >= 0,
              f"间隙={cramped_gap}px 分栏器={splitter.height()} 检查高={card.height()} "
              f"预览高={preview_card.height()}")
        check("提示卡展开后检查栏可视区仍不为 0",
              view.scroll.viewport().height() > 0,
              f"可视区={view.scroll.viewport().height()}")
        check("提示卡展开后预览框不消失",
              page.preview_edit.height() > 0,
              f"预览框高={page.preview_edit.height()}")
        page.env_card.setVisible(False)
        page._relayout()
        pump(10)

        print("\n=== 确认发布条：窄窗口下不截断确认文案 ===")
        bar = page.confirm_bar
        bar_width = bar.width()
        need = (
            bar.checkbox.sizeHint().width()
            + bar.badge_label.sizeHint().width()
            + bar.button.sizeHint().width()
            + 60
        )
        check(
            "窄窗口下安全模式徽章主动收起",
            (bar_width < need and not bar.badge_label.isVisible())
            or (bar_width >= need and bar.badge_label.isVisible()),
            f"条宽={bar_width} 需要={need} 徽章可见={bar.badge_label.isVisible()}",
        )
        check(
            "确认文案没有被截断",
            bar.checkbox.width() >= bar.checkbox.sizeHint().width() - 2,
            f"实际={bar.checkbox.width()} 需要={bar.checkbox.sizeHint().width()}",
        )

        print("\n=== 预览：B 站简介必须出现在预览里 ===")
        preview = page.preview_edit.toPlainText()
        check("预览包含【简介】段", "【简介】简介占位" in preview, preview[:80].replace("\n", " / "))

        # 留一张截图供人工核对（展开建议后的状态最容易看出排版问题）。
        # 用 window.grab() 而不是 screen.grabWindow(0)：无边框窗口带 DWM 圆角与阴影，
        # 屏幕抓图偶尔会拍到上一帧的残留，看起来像文字被裁了一半。
        shot_dir = Path(__file__).resolve().parent.parent / "_data" / "screenshots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        window.repaint()
        pump()
        window.grab().save(str(shot_dir / "publish-layout-expanded.png"))
        print(f"截图：{shot_dir / 'publish-layout-expanded.png'}")

        window.close()
        pump()
        from stent.ui.workers import shutdown_workers

        shutdown_workers(3000)
        print(f"\n===== 检查栏布局回归：{PASS} 通过 / {FAIL} 失败 =====")
        for item in FAILURES:
            print("  -", item)
        sys.stdout.flush()
        os._exit(0 if FAIL == 0 else 1)

    def tick() -> None:
        steps["i"] += 1
        page = window.stack.currentWidget()
        prep = getattr(page, "_preparation", None)
        if prep is None and steps["i"] < 40:
            QTimer.singleShot(250, tick)
            return
        run_checks(page)

    def start() -> None:
        window.navigate("publish")
        page = window.stack.currentWidget()
        page._pending_content_id = content_id
        page.reload_contents()
        QTimer.singleShot(500, tick)

    QTimer.singleShot(600, start)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
