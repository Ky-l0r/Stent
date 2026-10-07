"""标签云布局诊断：打印「内容创作」右侧编辑区的关键几何数据。

用途：当出现「标签被裁一半 / 像被底部操作栏盖住 / 标签跑到别处」这类问题时，
在**出问题的机器上**跑一次，把输出（或 ``_data/tagcloud-diag.txt``）发出来即可定位。

用法::

    python tests/ui_tagcloud_diag.py

脚本会：打开主窗口 → 进「内容创作」→ 填入示例内容 → 打印几何 → 截图 → 退出。
不会联网，也不会改动你的数据（只读取本地库）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BODY = (
    "刷到B站热榜那一刻我就知道，AG打LGD.NBW这场，评论区又要分两派吵起来了🔥\n\n"
    "KPL看久了会发现，这两家的对局从来不是单纯比谁操作秀。\n\n"
    "说几个我自己看下来的点👇\n\n"
    "🔥 AG的胜负手不在个人操作\n选手的硬实力摆在那儿，真正卡上限的是前中期节奏怎么选。\n\n"
    "🔥 LGD.NBW很擅长把人拉进自己的节奏\n不跟你硬碰硬。你以为优势在手，回头一看经济差已经被抹平了。\n\n"
    "你们站哪边？评论区蹲一个，赛前先把flag立好👀\n赛后回来对答案，码住不亏。"
)
TITLES = ["AG打LGD.NBW，胜负看这3点🔥", "为什么AG一碰到LGD.NBW就难打🤔", "赛前立flag，赛后对答案"]
SUMMARY = (
    "B站热榜刷到成都AG超玩会对阵杭州LGD.NBW，聊几个真正决定走向的看点："
    "AG的前期节奏、LGD.NBW的运营磨盘，还有谁先沉不住气。你站哪边？"
)


def main() -> int:
    from stent.app import build_application
    from stent.core.db import db

    db.init()

    from stent.config import config_manager

    config_manager.update(onboarding_done=True)

    app = build_application(["stent-tagcloud-diag"])

    from stent import paths
    from stent.ui.context import AppContext
    from stent.ui.main_window import MainWindow

    out_path = Path(paths.data_dir()) / "tagcloud-diag.txt"
    lines: list[str] = []

    def log(text: str = "") -> None:
        print(text, flush=True)
        lines.append(text)

    ctx = AppContext(config_manager)
    ctx.selftest = True
    window = MainWindow(ctx)
    window.show()

    def pump(n: int = 45) -> None:
        for _ in range(n):
            app.processEvents()

    window.navigate("create")
    pump()

    page = window.stack.currentWidget()
    page.body_edit.setPlainText(BODY)
    page._fill_titles(TITLES)
    page.summary_edit.setPlainText(SUMMARY)
    page.tag_cloud.set_tags(["职业联赛", "观赛日常"])
    pump(60)

    from PySide6.QtWidgets import QApplication, QFrame, QScrollArea

    from stent.ui.components import EditableTagChip

    screen = QApplication.primaryScreen()
    log("===== 标签云布局诊断 =====")
    log(f"屏幕         : {screen.geometry().getRect()}  可用: {screen.availableGeometry().getRect()}")
    log(f"设备像素比   : {screen.devicePixelRatio()}  逻辑 DPI: {screen.logicalDotsPerInch()}")
    log(f"窗口(逻辑)   : {window.width()}x{window.height()}")
    log(f"应用字体     : {QApplication.font().family()} {QApplication.font().pointSize()}pt")

    vs = page.view_stack
    tc = page.tag_cloud
    action = page.findChild(QFrame, "ActionBar")
    editor = None
    for area in page.findChildren(QScrollArea):
        if area.widget() is page.body_edit.parentWidget():
            editor = area

    log("")
    log("--- 纵向排布 ---")
    log(f"view_stack   : geo={vs.geometry().getRect()} minimumSizeHint={vs.minimumSizeHint().toTuple()}")
    log(f"ActionBar    : geo={action.geometry().getRect()}")
    vs_bottom = vs.geometry().y() + vs.geometry().height()
    act_top = action.geometry().y()
    log(f"重叠         : {'是（异常）' if vs_bottom > act_top else '否'}  重叠像素={max(0, vs_bottom - act_top)}")

    if editor is not None:
        inner = editor.widget()
        vp = editor.viewport().height()
        log("")
        log("--- 编辑滚动区 ---")
        log(f"viewport={vp}  inner高度={inner.height()}  inner.sizeHint={inner.sizeHint().height()}")
        log(f"需要滚动={inner.height() > vp + 1}")
        overflow = []
        for child in (page.body_edit, page.titles_list, page.summary_edit, tc):
            bottom = child.geometry().y() + child.geometry().height()
            if bottom > inner.height():
                overflow.append(f"{type(child).__name__}(底部 {bottom} > {inner.height()})")
        log(f"越界控件     : {'、'.join(overflow) if overflow else '无'}")
        pos = tc.mapTo(editor.viewport(), tc.rect().topLeft())
        log(f"标签云在视口内完整可见: {pos.y() >= 0 and pos.y() + tc.height() <= vp}")

    log("")
    log("--- 各字段高度 ---")
    log(f"正文={page.body_edit.height()}  标题={page.titles_list.height()}  "
        f"简介={page.summary_edit.height()}  标签云={tc.height()}")

    log("")
    log("--- 标签云内部 ---")
    log(f"tag_cloud geo={tc.geometry().getRect()}  sizeHint={tc.sizeHint().toTuple()}  "
        f"minimumHeight={tc.minimumHeight()}")
    log(f"flow geo={tc.flow.geometry().getRect()}")
    chips = tc.findChildren(EditableTagChip)
    log(f"标签数量={len(chips)}")
    for chip in chips:
        g = chip.geometry()
        inside = (g.x() >= 0 and g.y() >= 0
                  and g.x() + g.width() <= tc.width() and g.y() + g.height() <= tc.height())
        log(f"  {chip.toolTip()!r} geo={g.getRect()} 完整在框内={inside}")
    pill = tc.add_pill
    log(f"  add_pill geo={pill.geometry().getRect()} visible={pill.isVisible()}")

    # 渲染检测：几何正确 ≠ 真的画出来了。
    # 曾经出现过「几何/visibleRegion/isVisible 全部正常，屏幕上却是一片空白」的情况，
    # 所以这里直接按像素统计标签云区域里有多少非背景像素。
    log("")
    log("--- 渲染检测（像素）---")
    window.repaint()
    for _ in range(20):
        app.processEvents()
    image = window.grab().toImage()
    tl = tc.mapTo(window, tc.rect().topLeft())
    total = 0
    white = 0
    for y in range(tl.y(), min(image.height(), tl.y() + tc.height())):
        for x in range(tl.x(), min(image.width(), tl.x() + tc.width())):
            color = image.pixelColor(x, y)
            total += 1
            if color.red() > 250 and color.green() > 250 and color.blue() > 250:
                white += 1
    painted = total - white
    ratio = (painted * 100.0 / total) if total else 0.0
    log(f"标签云区域 {total} 像素，非白 {painted}（{ratio:.1f}%）")
    log(f"胶囊是否真的画出来了: {'是' if ratio > 5 else '否（异常：控件未被绘制）'}")

    shots = Path(paths.data_dir()) / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(shots / "tagcloud-diag-window.png"))
    tc.grab().save(str(shots / "tagcloud-diag-widget.png"))
    log("")
    log(f"截图已保存: {shots / 'tagcloud-diag-window.png'}")
    log(f"数据已保存: {out_path}")

    out_path.write_text("\n".join(lines), encoding="utf-8")

    window.close()
    app.processEvents()

    import os

    from stent.ui.workers import shutdown_workers

    shutdown_workers(2000)
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
