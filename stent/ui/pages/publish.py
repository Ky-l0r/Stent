"""发布中心页（企划书 3.2 模块三）。

流程：选平台 → 格式适配 → 发布前检查 → 预览 → **人工确认** → 发布。
风控：默认强制人工确认，默认动作是「自动填写但不提交」；不提供全自动发布开关。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.db import db
from ...platforms import platform_label
from ...services.publisher import PublishService, publish_service
from ..components import (
    Card,
    CardTitle,
    ConfirmBar,
    EmptyState,
    PageHeader,
    attach_platform_delegate,
    make_button,
    platform_item,
)
from ..theme import level_color, palette
from .base import BasePage

log = logging.getLogger(__name__)


class PublishPage(BasePage):
    key = "publish"
    title = "发布中心"
    icon = "send"

    def build(self) -> None:
        self._contents: list[dict] = []
        self._preparation = None
        self._media: list[str] = []
        self._worker = None
        self._pending_content_id: int | None = None

        header = PageHeader(
            "发布中心",
            "发布前检查 → 预览 → 人工确认 → 发布；默认只自动填写表单，最终提交由你在浏览器中确认",
        )
        header.add_action(self.status_light)
        self.refresh_button = make_button("刷新内容", icon="refresh", theme=self.ctx.theme, ghost=True)
        self.refresh_button.clicked.connect(self.reload_contents)
        header.add_action(self.refresh_button)
        self.add(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        self.add(splitter, 3)

        self.add(self._build_history(), 2)

    # ------------------------------------------------------------------
    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        content_card = Card(padding=14, spacing=8)
        content_card.add(CardTitle("1 · 选择内容", icon="pen-line", hint="来自创作页的草稿", theme=self.ctx.theme))
        self.content_list = QListWidget()
        self.content_list.setMinimumHeight(150)
        self.content_list.currentRowChanged.connect(self._on_content_changed)
        content_card.add(self.content_list, 1)
        layout.addWidget(content_card, 3)

        platform_card = Card(padding=14, spacing=10)
        platform_card.add(CardTitle("2 · 目标平台与素材", icon="send", theme=self.ctx.theme))

        self.platform_combo = QComboBox()
        for item in PublishService.platforms():
            suffix = "" if item["available"] else "（适配器不可用）"
            self.platform_combo.addItem(f"{item['label']}{suffix}", item["key"])
        self.platform_combo.currentIndexChanged.connect(lambda _: self.prepare())
        platform_card.add(self.platform_combo)

        login_row = QHBoxLayout()
        login_row.setSpacing(8)
        self.login_status = QLabel("尚未检测登录状态")
        self.login_status.setObjectName("CardHint")
        login_row.addWidget(self.login_status, 1)
        self.check_login_button = make_button("检测登录", icon="login", theme=self.ctx.theme, ghost=True)
        self.check_login_button.clicked.connect(self.check_login)
        login_row.addWidget(self.check_login_button)
        self.login_button = make_button("去登录", icon="login", theme=self.ctx.theme)
        self.login_button.clicked.connect(self.do_login)
        login_row.addWidget(self.login_button)
        platform_card.add_layout(login_row)

        media_row = QHBoxLayout()
        media_row.setSpacing(8)
        self.media_label = QLabel("未选择媒体文件")
        self.media_label.setObjectName("CardHint")
        self.media_label.setWordWrap(True)
        media_row.addWidget(self.media_label, 1)
        pick_button = make_button("选择图片/视频", icon="folder", theme=self.ctx.theme, ghost=True)
        pick_button.clicked.connect(self.pick_media)
        media_row.addWidget(pick_button)
        clear_button = make_button("清空", theme=self.ctx.theme, ghost=True)
        clear_button.clicked.connect(self.clear_media)
        media_row.addWidget(clear_button)
        platform_card.add_layout(media_row)

        layout.addWidget(platform_card, 2)
        return panel

    def _build_right(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(14)

        check_card = Card(padding=14, spacing=8)
        check_title = CardTitle("3 · 发布前检查", icon="check", hint="敏感词、字数、格式、媒体附件", theme=self.ctx.theme)
        self.check_score = QLabel("—")
        self.check_score.setObjectName("ScoreBadge")
        check_title.add_action(self.check_score)
        self.recheck_button = make_button("重新检查", icon="refresh", theme=self.ctx.theme, ghost=True)
        self.recheck_button.clicked.connect(lambda: self.prepare(force=True))
        check_title.add_action(self.recheck_button)
        check_card.add(check_title)
        self.check_list = QListWidget()
        self.check_list.setMinimumHeight(120)
        check_card.add(self.check_list, 1)
        layout.addWidget(check_card, 3)

        preview_card = Card(padding=14, spacing=8)
        preview_title = CardTitle("4 · 预览", icon="eye", theme=self.ctx.theme)
        self.copy_preview_button = make_button("复制可粘贴文案", icon="copy", theme=self.ctx.theme, ghost=True)
        self.copy_preview_button.clicked.connect(self.copy_text)
        preview_title.add_action(self.copy_preview_button)
        preview_card.add(preview_title)
        self.preview_edit = QTextEdit()
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setPlaceholderText("选择内容与平台后，这里会显示发布后的样子")
        preview_card.add(self.preview_edit, 1)
        layout.addWidget(preview_card, 3)

        self.confirm_bar = ConfirmBar(
            "我已确认预览与检查结果，并自行承担发布风险",
            "确认发布",
        )
        self.confirm_bar.triggered.connect(self.execute_default)
        self.confirm_bar.confirmed.connect(lambda _: None)
        layout.addWidget(self.confirm_bar)

        advanced = QHBoxLayout()
        advanced.setSpacing(8)
        advanced.addWidget(QLabel("高级："))
        self.auto_publish_button = make_button("直接自动点击发布（不推荐）", theme=self.ctx.theme, danger=True)
        self.auto_publish_button.setEnabled(False)
        self.auto_publish_button.clicked.connect(self.execute_auto)
        advanced.addWidget(self.auto_publish_button)
        advanced.addStretch(1)
        self.confirm_bar.confirmed.connect(self.auto_publish_button.setEnabled)
        layout.addLayout(advanced)

        warning = QLabel(
            "提示：小红书等平台的自动化存在风控风险。默认行为是自动填写表单后停在发布前，"
            "由你在浏览器里做最终确认；Stent 不提供全自动批量发布。"
        )
        warning.setObjectName("Faint")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        return panel

    def _build_history(self) -> QWidget:
        card = Card(padding=14, spacing=8)
        title = CardTitle(
            "发布记录",
            icon="clock",
            hint="每次操作都有审计留痕；手动在浏览器完成发布后可回到这里标记为已发布",
            theme=self.ctx.theme,
        )
        self.mark_button = make_button("标记为已发布", icon="check", theme=self.ctx.theme, ghost=True)
        self.mark_button.clicked.connect(self.mark_published)
        title.add_action(self.mark_button)
        self.open_button = make_button("打开作品链接", icon="external", theme=self.ctx.theme, ghost=True)
        self.open_button.clicked.connect(self.open_selected_link)
        title.add_action(self.open_button)
        self.log_button = make_button("查看审计日志", theme=self.ctx.theme, ghost=True)
        self.log_button.clicked.connect(self.show_logs)
        title.add_action(self.log_button)
        card.add(title)

        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["编号", "平台", "标题", "状态", "时间", "链接"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setShowGrid(False)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.history_table.setColumnWidth(0, 56)
        self.history_table.setColumnWidth(1, 78)
        self.history_table.setColumnWidth(3, 84)
        self.history_table.setColumnWidth(4, 132)
        self.history_table.setColumnWidth(5, 160)
        attach_platform_delegate(self.history_table, 1)
        card.add(self.history_table, 1)
        self.history_empty = EmptyState("还没有发布记录", hint="完成一次发布后，记录会出现在这里", icon="send", theme=self.ctx.theme)
        card.add(self.history_empty)
        self.history_empty.setVisible(False)
        return card

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def on_first_show(self) -> None:
        self.reload_contents()
        self._check_browser()

    def _check_browser(self) -> None:
        """缺少 Chromium 内核时提前告知，避免用户点发布才失败。"""
        try:
            from ...services.publisher import browser_status

            ok, detail = browser_status()
        except Exception:  # noqa: BLE001
            return
        if not ok:
            self.banner.show_message(
                f"发布功能尚未就绪：{detail}", "warn", summary="未安装浏览器内核"
            )

    def receive(self, **kwargs) -> None:
        content_id = kwargs.get("content_id")
        if content_id:
            self._pending_content_id = int(content_id)
            self.reload_contents()

    def reload_contents(self) -> None:
        self._contents = db.list_contents(limit=200)
        self.content_list.clear()
        for content in self._contents:
            title = content.get("title") or content.get("topic") or "（无标题）"
            label = platform_label(content.get("platform", "")) if content.get("platform") else "未指定"
            item = QListWidgetItem(
                f"#{content['id']}　[{label}]　{title[:36]}\n"
                f"　　{content.get('updated_at', '')}　·　{len(content.get('body', ''))} 字"
            )
            item.setToolTip(content.get("body", "")[:500])
            self.content_list.addItem(item)

        if not self._contents:
            self.banner.show_message("还没有可发布的内容，请先到「内容创作」生成并保存草稿", "warn")
        self._select_pending()
        self.reload_history()

    def _select_pending(self) -> None:
        if not self._contents:
            return
        target_row = 0
        if self._pending_content_id is not None:
            for row, content in enumerate(self._contents):
                if int(content["id"]) == self._pending_content_id:
                    target_row = row
                    break
            self._pending_content_id = None
        self.content_list.setCurrentRow(target_row)

    def reload_history(self) -> None:
        posts = db.list_posts(limit=100)
        self.history_table.setRowCount(len(posts))
        status_text = {
            "published": "已发布",
            "pending": "进行中",
            "failed": "失败",
            "draft_filled": "待人工提交",
        }
        for row, post in enumerate(posts):
            cells = [
                str(post.get("id", "")),
                platform_label(post.get("platform", "")),
                (post.get("title") or "")[:60],
                status_text.get(post.get("status", ""), post.get("status", "")),
                post.get("published_at") or post.get("created_at") or "",
                post.get("url") or (post.get("error") or "")[:40],
            ]
            for column, text in enumerate(cells):
                if column == 1:
                    cell = platform_item(text, post.get("platform", ""))
                else:
                    cell = QTableWidgetItem(text)
                self.history_table.setItem(row, column, cell)
        has = bool(posts)
        self.history_table.setVisible(has)
        self.history_empty.setVisible(not has)

    # ------------------------------------------------------------------
    # 选择与准备
    # ------------------------------------------------------------------
    def _current_content(self) -> dict | None:
        row = self.content_list.currentRow()
        if row < 0 or row >= len(self._contents):
            return None
        return self._contents[row]

    def _on_content_changed(self) -> None:
        content = self._current_content()
        if content is None:
            return
        index = self.platform_combo.findData(content.get("platform", ""))
        if index >= 0 and self.platform_combo.currentIndex() != index:
            self.platform_combo.blockSignals(True)
            self.platform_combo.setCurrentIndex(index)
            self.platform_combo.blockSignals(False)
        self.prepare()

    def pick_media(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要发布的图片或视频",
            "",
            "媒体文件 (*.jpg *.jpeg *.png *.webp *.gif *.mp4 *.mov *.mkv);;所有文件 (*.*)",
        )
        if files:
            self._media = list(dict.fromkeys(self._media + files))
            self._refresh_media_label()
            self.prepare()

    def clear_media(self) -> None:
        self._media = []
        self._refresh_media_label()
        self.prepare()

    def _refresh_media_label(self) -> None:
        if not self._media:
            self.media_label.setText("未选择媒体文件")
        else:
            names = "、".join(p.split("\\")[-1] for p in self._media[:3])
            more = f" 等 {len(self._media)} 个" if len(self._media) > 3 else ""
            self.media_label.setText(f"已选择：{names}{more}")

    def prepare(self, *, force: bool = False) -> None:
        content = self._current_content()
        if content is None:
            return
        platform = self.platform_combo.currentData()
        if not platform:
            return
        try:
            self._preparation = publish_service.prepare(
                int(content["id"]),
                platform,
                media=self._media if self._media else None,
                overwrite_platform=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("发布准备失败")
            self.banner.show_message(f"发布准备失败：{exc}", "error")
            self._preparation = None
            return
        self._render_preparation()

    def _render_preparation(self) -> None:
        prep = self._preparation
        if prep is None:
            return
        self.preview_edit.setPlainText(prep.preview)
        self.confirm_bar.reset()

        self.check_list.clear()
        report = prep.report
        if report is None:
            self.check_list.addItem(QListWidgetItem("尚未执行检查"))
        else:
            colors = {
                "error": palette(self.ctx.theme).danger,
                "warn": palette(self.ctx.theme).warning,
                "warning": palette(self.ctx.theme).warning,
                "info": palette(self.ctx.theme).accent,
            }
            for item in getattr(report, "items", []):
                text = f"[{_level_label(item.level)}] {item.title}"
                if item.detail:
                    text += f"　—　{item.detail}"
                if item.suggestion:
                    text += f"\n　　建议：{item.suggestion}"
                row = QListWidgetItem(text)
                try:
                    row.setForeground(_color(colors.get(item.level, palette(self.ctx.theme).text_sub)))
                except Exception:
                    pass
                self.check_list.addItem(row)
            if not getattr(report, "items", []):
                self.check_list.addItem(QListWidgetItem("未发现问题，可以发布"))

        score = prep.score
        passed = prep.passed
        color = palette(self.ctx.theme).success if passed else palette(self.ctx.theme).danger
        self.check_score.setText(f"{'通过' if passed else '有阻断项'} · {score} 分")
        self.check_score.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: 10px; padding: 2px 8px;"
        )

        notes = list(prep.adapted_notes)
        if prep.support_note:
            notes.append(prep.support_note)
        if notes:
            self.banner.show_message("；".join(notes), "warn", summary=f"{len(notes)} 项适配提示")
        elif passed:
            self.banner.show_message(
                f"检查通过（{score} 分）。确认无误后点击下方「确认发布」",
                "success",
                summary=f"检查通过 · {score} 分",
            )
        else:
            self.banner.show_message(
                "检查存在阻断问题，请先回到创作页修改内容", "error", summary="检查未通过"
            )

        supported = prep.supported and passed
        self.confirm_bar.button.setEnabled(supported and self.confirm_bar.checkbox.isChecked())
        self.auto_publish_button.setEnabled(supported and self.confirm_bar.checkbox.isChecked())

    def copy_text(self) -> None:
        if self._preparation is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(publish_service.export_text(self._preparation))
        self.toast("文案已复制，可手动粘贴发布", "success")

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------
    def check_login(self) -> None:
        platform = self.platform_combo.currentData()
        if not platform:
            return
        self.login_status.setText("检测中…")
        self.check_login_button.setEnabled(False)
        self._worker = self.run_task(
            lambda worker: publish_service.check_login(platform),
            on_result=self._on_login_checked,
            on_error=lambda msg: self._on_login_checked((False, msg)),
            on_done=lambda: self.check_login_button.setEnabled(True),
            name="check-login",
        )

    def _on_login_checked(self, result) -> None:
        ok, message = result
        p = palette(self.ctx.theme)
        self.login_status.setText(("已登录：" if ok else "未登录：") + message[:60])
        self.login_status.setStyleSheet(f"color: {p.success if ok else p.warning};")

    def do_login(self) -> None:
        platform = self.platform_combo.currentData()
        if not platform:
            return
        answer = QMessageBox.question(
            self,
            "开始登录",
            f"将打开浏览器窗口展示{platform_label(platform)}登录页，请在其中扫码或输入账号完成登录。\n\n"
            "Stent 只保存本地浏览器登录态，不会读取或上传你的账号密码。\n\n现在开始吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.login_button.setEnabled(False)
        self.login_button.setText("等待登录…")
        self.banner.show_message("已打开登录窗口，请在浏览器中完成扫码……", "info", closable=False)

        self._worker = self.run_task(
            lambda worker: publish_service.login(platform),
            on_result=self._on_login_done,
            on_error=lambda msg: self._on_login_done((False, msg)),
            on_done=self._on_login_button_reset,
            name="login",
        )

    def _on_login_done(self, result) -> None:
        ok, message = result
        p = palette(self.ctx.theme)
        self.login_status.setText(("已登录：" if ok else "登录未完成：") + message[:60])
        self.login_status.setStyleSheet(f"color: {p.success if ok else p.warning};")
        self.banner.show_message(message, "success" if ok else "warn")

    def _on_login_button_reset(self) -> None:
        self.login_button.setEnabled(True)
        self.login_button.setText("去登录")

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------
    def execute_default(self) -> None:
        """默认动作：自动填写表单，停在发布前由用户确认。"""
        self._execute(dry_run=True)

    def execute_auto(self) -> None:
        answer = QMessageBox.warning(
            self,
            "确认自动发布",
            "你选择了「直接自动点击发布」。\n\n"
            "该操作会绕过浏览器中的最终人工确认，平台自动化存在限流或账号风险，"
            "Stent 默认不推荐这样做。\n\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._execute(dry_run=False)

    def _execute(self, *, dry_run: bool) -> None:
        prep = self._preparation
        if prep is None:
            self.toast("请先选择内容与平台", "warn")
            return
        if not self.confirm_bar.checkbox.isChecked():
            self.toast("请先勾选人工确认", "warn")
            return
        if self._worker is not None and self._worker.isRunning():
            self.toast("上一步操作尚未完成", "warn")
            return

        self.confirm_bar.set_busy(True, "执行中…")
        self.banner.show_message(
            "正在打开浏览器并填写表单…" if dry_run else "正在打开浏览器并执行发布…",
            "info",
            closable=False,
        )
        self.ctx.db.log_action(prep.platform, "ui_confirm", f"dry_run={dry_run}")

        def job(worker):
            return publish_service.publish(
                prep,
                confirmed=True,
                dry_run=dry_run,
                on_progress=lambda message, percent=-1: worker.emit_progress(message, percent),
            )

        self._worker = self.run_task(
            job,
            on_progress=lambda msg, pct: self._on_publish_progress(msg, pct),
            on_result=self._on_publish_result,
            on_error=self._on_publish_error,
            on_done=self._on_publish_done,
            name="publish",
        )

    def _on_publish_progress(self, message: str, percent: int) -> None:
        text = message if percent is None or percent < 0 else f"{message}（{percent}%）"
        self.banner.show_message(text, "info", closable=False)

    def _on_publish_result(self, outcome) -> None:
        if outcome.ok:
            if outcome.dry_run:
                self.banner.show_message(
                    f"{outcome.message}。请到浏览器窗口检查内容并点击发布；完成后回到这里点「标记为已发布」。",
                    "success",
                )
            else:
                self.banner.show_message(f"发布成功：{outcome.url or '（未取到链接）'}", "success")
            self.toast("操作完成", "success")
        else:
            self.banner.show_message(f"发布未完成：{outcome.message}", "error")
            self.toast("发布未完成", "error")
        self.reload_history()
        self.ctx.notify_data_changed("posts")

    def _on_publish_error(self, message: str) -> None:
        self.banner.show_message(f"发布异常：{message}", "error")

    def _on_publish_done(self) -> None:
        self.confirm_bar.set_busy(False)
        self.confirm_bar.reset()
        self._worker = None

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------
    def _selected_post(self) -> dict | None:
        row = self.history_table.currentRow()
        if row < 0:
            return None
        item = self.history_table.item(row, 0)
        if item is None:
            return None
        return db.get_post(int(item.text()))

    def mark_published(self) -> None:
        post = self._selected_post()
        if post is None:
            self.toast("请先在发布记录中选择一条", "warn")
            return
        url = post.get("url") or ""
        if not url:
            from PySide6.QtWidgets import QInputDialog

            url, ok = QInputDialog.getText(self, "作品链接", "粘贴作品链接（可留空）：")
            if not ok:
                return
        publish_service.mark_published_manually(int(post["id"]), url)
        self.reload_history()
        self.toast("已标记为已发布，可在「数据分析」中同步指标", "success")

    def open_selected_link(self) -> None:
        post = self._selected_post()
        if post is None or not post.get("url"):
            self.toast("该记录没有链接", "warn")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(post["url"]))

    def show_logs(self) -> None:
        logs = db.recent_logs(limit=200)
        dialog = QDialog(self)
        dialog.setWindowTitle("发布审计日志")
        dialog.resize(760, 480)
        layout = QVBoxLayout(dialog)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(
            "\n".join(
                f"{row['created_at']}　[{platform_label(row['platform']) if row['platform'] else '系统'}]　"
                f"{row['action']}　{row['detail']}"
                for row in logs
            )
            or "暂无日志"
        )
        layout.addWidget(view)
        dialog.exec()


def _level_label(level: str) -> str:
    return {"error": "必须修改", "warn": "建议修改", "warning": "建议修改", "info": "提示"}.get(level, "提示")


def _color(hex_color: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(hex_color))
