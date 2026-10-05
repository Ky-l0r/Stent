"""发布中心页（企划书 3.2 模块三）。

流程：选平台 → 格式适配 → 发布前检查 → 预览 → **人工确认** → 发布。
风控：默认强制人工确认，默认动作是「自动填写但不提交」；不提供全自动发布开关。

界面要点（v1.0.5 打磨）：

- 环境没装好是致命阻塞，顶部给醒目提示卡 + 一键安装；装好后不再有布局跳动
- 平台由草稿决定，模块 2 只**只读展示**，把版面留给「账号状态」和「媒体素材」
- 检查结果一条一张浅色卡：级别交给左侧色条，文字回归常规深灰；
  「去修改」按钮直接放进对应的问题卡，建议项默认折叠成一行
- 「默认安全模式」压成一行小徽章贴在「确认发布」旁边
- 发布记录仍在右侧抽屉里，状态徽章 + 待处理计数
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.db import db
from ...platforms import platform_label
from ...services.publisher import (
    POST_STATUS_OPEN,
    PublishService,
    environment_status,
    post_status_label,
    post_status_tone,
    publish_service,
)
from .. import icons
from ..components import (
    Card,
    CardTitle,
    CheckReportView,
    ConfirmBar,
    EmptyState,
    PageHeader,
    StatusBadgeDelegate,
    TwoLineItemDelegate,
    attach_platform_delegate,
    make_button,
    platform_item,
    status_item,
)
from ..theme import palette
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
        self._env: object | None = None
        self._prepare_token = 0
        self._login_state = "unknown"
        self._state_action = "check"
        self._platform_override = ""

        header = PageHeader(
            "发布中心",
            "发布前检查 → 预览 → 人工确认 → 发布；默认只自动填写表单，最终提交由你在浏览器中确认",
        )
        header.add_action(self.status_light)
        self.history_button = make_button("发布记录", icon="clock", theme=self.ctx.theme)
        self.history_button.clicked.connect(self.open_history)
        header.add_action(self.history_button)
        self.refresh_button = make_button("刷新内容", icon="refresh", theme=self.ctx.theme, ghost=True)
        self.refresh_button.clicked.connect(self.reload_contents)
        header.add_action(self.refresh_button)
        self.add(header)

        self.env_card = self._build_env_card()
        self.add(self.env_card)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        # 右侧（检查 + 预览）是主战场，给它更多宽度
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 7)
        self.add(splitter, 1)

    # ------------------------------------------------------------------
    # 运行环境阻塞卡
    # ------------------------------------------------------------------
    def _build_env_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("EnvCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.env_icon = QLabel()
        self.env_icon.setFixedSize(20, 20)
        head.addWidget(self.env_icon)
        self.env_title = QLabel("发布环境未就绪")
        self.env_title.setObjectName("EnvTitle")
        head.addWidget(self.env_title)
        head.addStretch(1)
        layout.addLayout(head)

        self.env_detail = QLabel()
        self.env_detail.setObjectName("EnvDetail")
        self.env_detail.setWordWrap(True)
        layout.addWidget(self.env_detail)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.install_button = make_button("一键安装环境", icon="download", theme=self.ctx.theme, primary=True)
        self.install_button.clicked.connect(self.install_environment)
        row.addWidget(self.install_button)
        self.copy_command_button = make_button("复制安装命令", icon="copy", theme=self.ctx.theme, ghost=True)
        self.copy_command_button.setToolTip("自动安装失败时，可把命令粘贴到终端手动执行")
        self.copy_command_button.clicked.connect(self._copy_install_command)
        row.addWidget(self.copy_command_button)
        self.env_progress = QProgressBar()
        self.env_progress.setObjectName("EnvProgress")
        self.env_progress.setRange(0, 100)
        self.env_progress.setValue(0)
        self.env_progress.setTextVisible(False)
        self.env_progress.setVisible(False)
        row.addWidget(self.env_progress, 1)
        layout.addLayout(row)

        # 状态行常驻（空文本也占位）：安装过程中文字长短变化不会让整块版面跳动
        self.env_status = QLabel(" ")
        self.env_status.setObjectName("EnvDetail")
        self.env_status.setWordWrap(True)
        self.env_status.setMinimumHeight(18)
        layout.addWidget(self.env_status)

        card.setVisible(False)
        return card

    def _copy_install_command(self) -> None:
        command = getattr(self._env, "command", "") or "python -m playwright install chromium"
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(command)
        self.toast("安装命令已复制", "success")

    def install_environment(self) -> None:
        """一键安装：把「该敲哪条命令」变成一次点击。"""
        if self._worker is not None and self._worker.isRunning():
            self.toast("已有任务在执行，请稍候", "info")
            return
        self.install_button.setEnabled(False)
        self.install_button.setText("安装中…")
        self.copy_command_button.setEnabled(False)
        self.env_progress.setVisible(True)
        self.env_progress.setRange(0, 0)  # 不确定进度：下载耗时不可预测
        self._show_env_status("正在准备安装，请保持网络畅通…", "info")

        self._worker = self.run_task(
            lambda worker: publish_service.install_environment(
                on_progress=lambda msg, pct: worker.emit_progress(msg, pct),
                cancel=worker.cancel_event,
            ),
            on_progress=self._on_install_progress,
            on_result=self._on_install_result,
            on_error=self._on_install_error,
            on_done=self._on_install_done,
            name="install-env",
        )

    def _on_install_progress(self, message: str, percent: int) -> None:
        self._show_env_status(message, "info")

    def _on_install_result(self, result) -> None:
        ok, message = result
        self._show_env_status(message, "success" if ok else "error")
        if ok:
            self.toast("发布环境已就绪", "success")
            # 适配器此前可能因为缺内核被缓存成 None，装完要重建平台列表
            self._refresh_env_row()
            self.check_login()
        else:
            self.toast("安装未完成，可复制命令手动执行", "error")

    def _on_install_error(self, message: str) -> None:
        self._show_env_status(f"安装失败：{message}", "error")

    def _on_install_done(self) -> None:
        self._worker = None
        self.install_button.setEnabled(True)
        self.install_button.setText("一键安装环境")
        self.copy_command_button.setEnabled(True)
        self.env_progress.setRange(0, 100)
        self.env_progress.setVisible(False)
        self._refresh_env_card()
        # 提示卡收起后强制重排一次：否则上下两块会停在动画前的尺寸上，出现错位
        self._relayout()

    def _relayout(self) -> None:
        """提示卡收起/展开后强制重排。

        QVBoxLayout 在子控件显隐后不会立刻重算，分栏器也会保留旧尺寸，
        表现为「上面缩了、下面还停在原位」的错位。
        """
        root = self.layout()
        if root is not None:
            root.invalidate()
            root.activate()
        for splitter in self.findChildren(QSplitter):
            splitter.updateGeometry()
        self.updateGeometry()
        self.update()

    def _show_env_status(self, message: str, level: str) -> None:
        from ..theme import level_color

        self.env_status.setText(message or " ")
        self.env_status.setStyleSheet(
            f"color: {level_color(self.ctx.theme, level)}; background: transparent;"
        )

    def _refresh_env_card(self) -> None:
        """环境就绪就收起提示卡；未就绪则把用户看得懂的说明摆出来。"""
        self._env = environment_status()
        status = self._env
        ready = bool(getattr(status, "ready", True))
        self.env_card.setVisible(not ready)
        if ready:
            return
        p = palette(self.ctx.theme)
        self.env_icon.setPixmap(icons.pixmap("alert", p.warning, 20))
        self.env_title.setText(str(getattr(status, "title", "发布环境未就绪")))
        detail = str(getattr(status, "detail", ""))
        hint = getattr(status, "size_hint", "")
        suffix = f"（{hint}，只需安装一次）" if hint else "（只需安装一次）"
        self.env_detail.setText(f"{detail}{suffix} 装好后登录与发布即可正常使用。")
        self.env_status.setText(" ")

    # ------------------------------------------------------------------
    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        content_card = Card(padding=14, spacing=8)
        content_card.add(
            CardTitle("1 · 选择内容", icon="pen-line", hint="来自创作页的草稿", theme=self.ctx.theme)
        )
        self.content_list = QListWidget()
        self.content_list.setMinimumHeight(150)
        self.content_list.setItemDelegate(TwoLineItemDelegate(self.content_list))
        self.content_list.currentRowChanged.connect(self._on_content_changed)
        content_card.add(self.content_list, 1)
        # 空态：刚进页面时用户并不知道草稿在哪，这里直接给一条去路
        self.content_empty = EmptyState(
            "还没有可发布的草稿",
            hint="先在「内容创作」生成一篇内容并保存，草稿就会出现在这里",
            icon="pen-line",
            theme=self.ctx.theme,
            action=("去创作页生成内容", lambda: self._goto_create()),
        )
        content_card.add(self.content_empty)
        self.content_empty.setVisible(False)
        layout.addWidget(content_card, 3)

        # ---- 模块 2：平台由草稿决定，这里只读展示；版面留给账号与素材 ----
        platform_card = Card(padding=14, spacing=10)
        platform_card.add(CardTitle("2 · 发布设置", icon="send", theme=self.ctx.theme))

        platform_row = QHBoxLayout()
        platform_row.setSpacing(8)
        self.platform_icon = QLabel()
        self.platform_icon.setFixedSize(16, 16)
        platform_row.addWidget(self.platform_icon)
        self.platform_value = QLabel("未选择内容")
        self.platform_value.setObjectName("CardTitle")
        platform_row.addWidget(self.platform_value, 1)
        self.change_platform_button = make_button("更换", theme=self.ctx.theme, ghost=True)
        self.change_platform_button.setToolTip("默认发布到草稿绑定的平台，需要时可以改")
        self.change_platform_button.clicked.connect(self._pick_platform)
        platform_row.addWidget(self.change_platform_button)
        platform_card.add_layout(platform_row)

        self.platform_hint = QLabel("平台由所选草稿决定")
        self.platform_hint.setObjectName("FieldHint")
        platform_card.add(self.platform_hint)

        # 账号状态：说人话 + 一个能立刻点的动作按钮
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        self.state_icon = QLabel()
        self.state_icon.setFixedSize(16, 16)
        state_row.addWidget(self.state_icon)
        self.login_status = QLabel("尚未检测登录状态")
        self.login_status.setObjectName("CardHint")
        self.login_status.setWordWrap(True)
        state_row.addWidget(self.login_status, 1)
        self.state_button = make_button("检测登录", icon="login", theme=self.ctx.theme)
        self.state_button.clicked.connect(self._on_state_action)
        state_row.addWidget(self.state_button)
        platform_card.add_layout(state_row)

        # 媒体素材
        media_head = QHBoxLayout()
        media_head.setSpacing(8)
        media_title = QLabel("媒体素材")
        media_title.setObjectName("FieldLabel")
        media_head.addWidget(media_title)
        media_head.addStretch(1)
        pick_button = make_button("选择图片/视频", icon="folder", theme=self.ctx.theme, ghost=True)
        pick_button.clicked.connect(self.pick_media)
        media_head.addWidget(pick_button)
        clear_button = make_button("清空", theme=self.ctx.theme, ghost=True)
        clear_button.clicked.connect(self.clear_media)
        media_head.addWidget(clear_button)
        platform_card.add_layout(media_head)

        self.media_label = QLabel("未选择媒体文件")
        self.media_label.setObjectName("CardHint")
        self.media_label.setWordWrap(True)
        platform_card.add(self.media_label)

        layout.addWidget(platform_card, 2)
        return panel

    def _build_right(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(12)

        check_card = Card(padding=14, spacing=8)
        check_title = CardTitle(
            "3 · 发布前检查", icon="check", hint="敏感词、字数、格式、媒体附件", theme=self.ctx.theme
        )
        self.check_score = QLabel("—")
        self.check_score.setObjectName("ScoreBadge")
        check_title.add_action(self.check_score)
        self.recheck_button = make_button("重新检查", icon="refresh", theme=self.ctx.theme, ghost=True)
        self.recheck_button.clicked.connect(lambda: self.prepare(force=True))
        check_title.add_action(self.recheck_button)
        check_card.add(check_title)
        self.check_view = CheckReportView(theme=self.ctx.theme)
        self.check_view.fix_requested.connect(lambda _item: self._goto_create())
        check_card.add(self.check_view, 1)
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
        layout.addWidget(preview_card, 4)

        self.confirm_bar = ConfirmBar(
            "我已确认预览与检查结果，并自行承担发布风险",
            "确认发布",
            button_style="ConfirmButton",
            badge="🛡 安全模式：最终发布需你确认",
        )
        self.confirm_bar.triggered.connect(self.execute_default)
        self.confirm_bar.set_allowed(False)
        layout.addWidget(self.confirm_bar)

        advanced = QHBoxLayout()
        advanced.setSpacing(8)
        self.advanced_button = make_button("高级选项…", icon="sliders", theme=self.ctx.theme, ghost=True)
        self.advanced_button.setToolTip("包含「直接自动发布」等高风险操作")
        self.advanced_button.clicked.connect(self.open_advanced_options)
        advanced.addWidget(self.advanced_button)
        advanced.addStretch(1)
        layout.addLayout(advanced)
        return panel

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def on_first_show(self) -> None:
        self.reload_contents()
        self._refresh_env_card()
        self._refresh_env_row()

    def apply_theme(self, theme: str) -> None:
        self._refresh_env_card()
        self._refresh_env_row()
        self._render_preparation()

    # ------------------------------------------------------------------
    # 平台（只读展示 + 可选更换）
    # ------------------------------------------------------------------
    def _content_platform(self) -> str:
        content = self._current_content()
        return (content or {}).get("platform", "") or ""

    def _platform(self) -> str:
        return self._platform_override or self._content_platform() or "xiaohongshu"

    def _refresh_platform_row(self) -> None:
        platform = self._platform()
        label = platform_label(platform) if platform else "未选择内容"
        from ..theme import platform_color

        p = palette(self.ctx.theme)
        if platform:
            self.platform_icon.setPixmap(icons.pixmap("send", platform_color(platform), 16))
        else:
            self.platform_icon.setPixmap(icons.pixmap("send", p.text_faint, 16))
        self.platform_value.setText(f"发布到 {label}" if platform else "未选择内容")
        overridden = bool(self._platform_override) and self._platform_override != self._content_platform()
        self.platform_hint.setText(
            "已手动更换平台，发布时会按新平台重新适配" if overridden else "平台由所选草稿决定"
        )

    def _pick_platform(self) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        current = self._platform()
        for item in PublishService.platforms():
            action = menu.addAction(item["label"])
            action.setCheckable(True)
            action.setChecked(item["key"] == current)
            action.triggered.connect(lambda _=False, k=item["key"]: self._set_platform(k))
        if self._platform_override:
            menu.addSeparator()
            menu.addAction("恢复为草稿绑定的平台", lambda: self._set_platform(""))
        menu.exec(self.change_platform_button.mapToGlobal(
            self.change_platform_button.rect().bottomLeft()
        ))

    def _set_platform(self, key: str) -> None:
        self._platform_override = key or ""
        self._refresh_platform_row()
        self._refresh_env_row()
        self.prepare()

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
            item = QListWidgetItem(title)
            # 两行式：第一行标题，第二行小字元信息，编号/平台不再随标题长度错位
            item.setData(
                TwoLineItemDelegate.TITLE_ROLE, title,
            )
            item.setData(
                TwoLineItemDelegate.META_ROLE,
                f"#{content['id']}　{label}　{content.get('updated_at', '')}　"
                f"{len(content.get('body', ''))} 字",
            )
            item.setToolTip(content.get("body", "")[:500])
            self.content_list.addItem(item)

        has = bool(self._contents)
        self.content_list.setVisible(has)
        self.content_empty.setVisible(not has)
        if not has:
            self._clear_preparation()
        self._select_pending()
        self._refresh_platform_row()
        self._refresh_history_badge()

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

    def _refresh_history_badge(self) -> None:
        """把「还有几条等着你处理」直接写在入口按钮上。"""
        try:
            posts = db.list_posts(limit=200)
        except Exception:  # noqa: BLE001
            return
        pending = len([p for p in posts if p.get("status") in POST_STATUS_OPEN])
        self.history_button.setText(
            f"发布记录 · {pending} 条待处理" if pending else f"发布记录（{len(posts)}）"
        )

    # ------------------------------------------------------------------
    # 选择与准备
    # ------------------------------------------------------------------
    def _current_content(self) -> dict | None:
        row = self.content_list.currentRow()
        if row < 0 or row >= len(self._contents):
            return None
        return self._contents[row]

    def _goto_create(self) -> None:
        """跳到创作页修改：带上内容 id，创作页会直接载入这条草稿。"""
        content = self._current_content()
        window = self.window()
        if not hasattr(window, "open_page_with"):
            return
        if content is not None:
            window.open_page_with("create", content_id=int(content["id"]))  # type: ignore[attr-defined]
        else:
            window.open_page_with("create")  # type: ignore[attr-defined]

    def _on_content_changed(self) -> None:
        content = self._current_content()
        if content is None:
            return
        # 换草稿时撤销手动更换的平台，回到「跟随草稿」
        self._platform_override = ""
        self._refresh_platform_row()
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
        """执行「适配 → 检查 → 预览」。

        放到后台线程跑：检查器首次使用要加载词库，放主线程会让界面卡一下；
        这里顺便把「正在检查…」的 loading 状态显示出来。
        """
        content = self._current_content()
        if content is None:
            self._clear_preparation()
            return
        platform = self._platform()
        if not platform:
            return
        self._prepare_token += 1
        token = self._prepare_token
        self._show_checking()

        def job(_worker):
            return publish_service.prepare(
                int(content["id"]),
                platform,
                media=self._media if self._media else None,
                overwrite_platform=True,
            )

        self.run_task(
            job,
            on_result=lambda prep: self._on_prepared(prep, token),
            on_error=lambda msg: self._on_prepare_failed(msg, token),
            name="prepare",
        )

    def _on_prepared(self, prep, token: int) -> None:
        if token != self._prepare_token:
            return  # 用户已经切到别的内容，丢弃过期结果
        self._preparation = prep
        self._render_preparation()

    def _on_prepare_failed(self, message: str, token: int) -> None:
        if token != self._prepare_token:
            return
        log.warning("发布准备失败：%s", message)
        self._preparation = None
        self.check_view.set_message("准备失败，请重新选择内容", "error")
        self.check_score.setText("—")
        self.banner.show_message(f"发布准备失败：{message}", "error")

    def _show_checking(self) -> None:
        self.check_view.set_message("正在检查…", "info")
        self.check_score.setText("检查中…")
        self.check_score.setStyleSheet("")

    def _clear_preparation(self) -> None:
        self._preparation = None
        self.preview_edit.clear()
        self.check_view.set_message("选择一条草稿后自动开始检查", "info")
        self.check_score.setText("—")
        self.check_score.setStyleSheet("")
        self.confirm_bar.reset()
        self.confirm_bar.set_allowed(False)

    def _render_preparation(self) -> None:
        prep = self._preparation
        if prep is None:
            return
        self.preview_edit.setPlainText(prep.preview)
        self.confirm_bar.reset()
        self._render_check_view(prep)

        score = prep.score
        passed = prep.passed
        p = palette(self.ctx.theme)
        color = p.success if passed else p.danger
        tint = p.tint_success if passed else p.tint_error
        self.check_score.setText(f"{'通过' if passed else '有阻断项'} · {score} 分")
        # 用浅色底而不是红边框：红框 + 红字叠加起来像报错弹窗，噪音太大
        self.check_score.setStyleSheet(
            f"color: {color}; background: {tint}; border: none;"
            f" border-radius: 10px; padding: 3px 10px;"
        )

        notes = list(prep.adapted_notes)
        if prep.support_note:
            notes.append(prep.support_note)
        if notes:
            self.banner.show_message("；".join(notes), "warn", summary=f"{len(notes)} 项适配提示")
        elif passed:
            self.banner.show_message(
                f"检查通过（{score} 分）。确认无误后勾选下方确认并发布",
                "success",
                summary=f"检查通过 · {score} 分",
            )
        else:
            self.banner.show_message(
                "检查存在阻断问题，可在对应卡片右侧点「去修改」", "error", summary="检查未通过"
            )

        supported = prep.supported and passed
        self.confirm_bar.set_allowed(supported)
        self._refresh_env_row()

    def _render_check_view(self, prep) -> None:
        """检查结果交给 CheckReportView：浅色卡 + 色条 + 卡内「去修改」。"""
        report = prep.report
        if report is None:
            self.check_view.set_message("尚未执行检查", "info")
            return
        self.check_view.set_items(list(getattr(report, "items", []) or []))

    def copy_text(self) -> None:
        if self._preparation is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(publish_service.export_text(self._preparation))
        self.toast("文案已复制，可手动粘贴发布", "success")

    # ------------------------------------------------------------------
    # 环境 / 登录状态行
    # ------------------------------------------------------------------
    def _refresh_env_row(self) -> None:
        """把技术报错翻译成「发生了什么 + 我能点什么」。

        状态机：env（环境缺失）→ unsupported（适配器不可用）→
        unknown / ok / fail（登录检测的三种结果），每种状态配一个明确的动作按钮。
        """
        p = palette(self.ctx.theme)
        platform = self._platform()
        label = platform_label(platform) if platform else "该平台"
        status = environment_status()
        self._env = status

        if not status.ready:
            self._login_state = "env"
            self._set_state("alert", p.warning, "未检测到运行环境，发布功能暂时不可用")
            self._set_state_button("一键配置", "download", "install")
            return

        if PublishService.adapter(platform) is None:
            self._login_state = "unsupported"
            self._set_state("alert", p.warning, f"{label}的自动化暂不可用，可复制文案后手动发布")
            self._set_state_button("复制文案", "copy", "copy")
            return

        if self._login_state == "ok":
            self._set_state("check", p.success, f"已登录{label}")
            self._set_state_button("重新检测", "refresh", "check")
        elif self._login_state == "fail":
            self._set_state("alert", p.warning, f"{label}账号未登录，发布前需要先登录")
            self._set_state_button(f"去登录{label}", "login", "login")
        else:
            self._login_state = "unknown"
            self._set_state("info", p.text_sub, "尚未检测登录状态")
            self._set_state_button("检测登录", "login", "check")

    def _set_state(self, icon_name: str, color: str, text: str) -> None:
        self.state_icon.setPixmap(icons.pixmap(icon_name, color, 16))
        self.login_status.setText(text)
        self.login_status.setStyleSheet(f"color: {color}; background: transparent;")

    def _set_state_button(self, text: str, icon_name: str, action: str) -> None:
        p = palette(self.ctx.theme)
        self.state_button.setText(text)
        self.state_button.setIcon(icons.icon(icon_name, p.text_sub, 16))
        self.state_button.setEnabled(True)
        self._state_action = action

    def _on_state_action(self) -> None:
        action = getattr(self, "_state_action", "check")
        if action == "install":
            self.install_environment()
        elif action == "copy":
            self.copy_text()
        elif action == "login":
            self.do_login()
        else:
            self.check_login()

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------
    def check_login(self) -> None:
        platform = self._platform()
        if not platform:
            return
        self.state_button.setEnabled(False)
        self._set_state("info", palette(self.ctx.theme).text_sub, "正在检测登录状态…")
        self.run_task(
            lambda worker: publish_service.check_login(platform),
            on_result=self._on_login_checked,
            on_error=lambda msg: self._on_login_checked((False, msg)),
            on_done=self._refresh_env_row,
            name="check-login",
        )

    def _on_login_checked(self, result) -> None:
        ok, message = result
        self._login_state = "ok" if ok else "fail"
        self._refresh_env_row()
        if message:
            self.login_status.setToolTip(message)

    def do_login(self) -> None:
        platform = self._platform()
        if not platform:
            return
        label = platform_label(platform)
        answer = QMessageBox.question(
            self,
            f"登录{label}",
            f"将打开浏览器窗口展示{label}登录页，请在其中扫码或输入账号完成登录。\n\n"
            "Stent 只保存本地浏览器登录态，不会读取或上传你的账号密码。\n\n现在开始吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.state_button.setEnabled(False)
        self.state_button.setText("等待登录…")
        self.banner.show_message("已打开登录窗口，请在浏览器中完成扫码……", "info", closable=False)

        self.run_task(
            lambda worker: publish_service.login(platform),
            on_result=self._on_login_done,
            on_error=lambda msg: self._on_login_done((False, msg)),
            on_done=self._refresh_env_row,
            name="login",
        )

    def _on_login_done(self, result) -> None:
        ok, message = result
        self._login_state = "ok" if ok else "fail"
        self._refresh_env_row()
        self.banner.show_message(message, "success" if ok else "warn")

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------
    def execute_default(self) -> None:
        """默认动作：自动填写表单，停在发布前由用户确认。"""
        self._execute(dry_run=True)

    def open_advanced_options(self) -> None:
        """高风险操作收进弹窗，并且必须先勾选风险知情。"""
        dialog = AutoPublishDialog(self.ctx.theme, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.execute_auto()
        dialog.setParent(None)
        dialog.deleteLater()

    def execute_auto(self) -> None:
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
                    f"{outcome.message}。请到浏览器窗口检查内容并点击发布；"
                    "完成后回到这里，在「发布记录」里把它标记为已发布。",
                    "success",
                )
            else:
                self.banner.show_message(f"发布成功：{outcome.url or '（未取到链接）'}", "success")
            self.toast("操作完成", "success")
        else:
            self.banner.show_message(f"发布未完成：{outcome.message}", "error")
            self.toast("发布未完成", "error")
        self._refresh_history_badge()
        self.ctx.notify_data_changed("posts")

    def _on_publish_error(self, message: str) -> None:
        self.banner.show_message(f"发布异常：{message}", "error")

    def _on_publish_done(self) -> None:
        self.confirm_bar.set_busy(False)
        self.confirm_bar.reset()
        self._worker = None
        self._refresh_history_badge()

    # ------------------------------------------------------------------
    # 发布记录抽屉
    # ------------------------------------------------------------------
    def open_history(self) -> None:
        drawer = HistoryDrawer(self)
        drawer.changed.connect(self._refresh_history_badge)
        drawer.exec()
        drawer.setParent(None)
        drawer.deleteLater()
        self._refresh_history_badge()

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
        dialog.setParent(None)
        dialog.deleteLater()


class AutoPublishDialog(QDialog):
    """「直接自动发布」的风险知情确认。

    这个能力本身是高风险动作，因此不再用红色按钮摆在主界面上诱导点击：
    收进高级选项，并且必须先勾选风险知情才允许继续。
    """

    def __init__(self, theme: str = "light", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("高级选项")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("高级选项")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        strip = QFrame()
        strip.setObjectName("SafeStrip")
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(12, 9, 12, 9)
        strip_layout.setSpacing(10)
        icon = QLabel()
        icon.setFixedSize(18, 18)
        icon.setPixmap(icons.pixmap("shield", palette(theme).success, 18))
        strip_layout.addWidget(icon)
        safe = QLabel("推荐保持默认：自动填写表单，最终由你在浏览器里点击发布。")
        safe.setObjectName("SafeStripText")
        safe.setWordWrap(True)
        strip_layout.addWidget(safe, 1)
        layout.addWidget(strip)

        risk = QLabel(
            "「直接自动点击发布」会绕过浏览器中的最终人工确认，由程序替你按下发布按钮。\n\n"
            "· 小红书等平台会检测自动化行为，可能导致限流、内容被删甚至账号被封禁；\n"
            "· 一旦发出无法撤回，出错内容会直接对粉丝可见；\n"
            "· 该操作同样会写入审计日志，出问题时可回溯。"
        )
        risk.setObjectName("EnvDetail")
        risk.setWordWrap(True)
        layout.addWidget(risk)

        self.ack = QCheckBox("我已知晓账号可能被限流或封禁的风险，仍要继续")
        self.ack.stateChanged.connect(self._sync)
        layout.addWidget(self.ack)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.auto_button = QPushButton("直接自动发布")
        self.auto_button.setObjectName("Danger")
        self.auto_button.setEnabled(False)
        self.auto_button.clicked.connect(self.accept)
        row.addWidget(self.auto_button)
        layout.addLayout(row)

    def _sync(self) -> None:
        self.auto_button.setEnabled(self.ack.isChecked())


class HistoryDrawer(QDialog):
    """发布记录抽屉：贴在窗口右侧，主界面因此可以把高度全留给预览。

    记录不再只是「一串历史」：状态是闭环的核心，因此每条都带状态徽章，
    并且补齐「标记失败 / 取消这次发布」这些以前缺失的出口。
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("发布记录")
        self.setMinimumWidth(560)
        self._posts: list[dict] = []
        # 抽屉跟着页面的主题走，不要写死浅色
        self._theme = getattr(getattr(parent, "ctx", None), "theme", "light")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("DrawerHeader")
        head_layout = QHBoxLayout(header)
        head_layout.setContentsMargins(16, 14, 16, 12)
        head_layout.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)
        title = QLabel("发布记录")
        title.setObjectName("DrawerTitle")
        title_box.addWidget(title)
        self.summary = QLabel()
        self.summary.setObjectName("StatusSummary")
        self.summary.setWordWrap(True)
        title_box.addWidget(self.summary)
        head_layout.addLayout(title_box, 1)
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部", "all")
        self.filter_combo.addItem("待处理", "open")
        self.filter_combo.addItem("已发布", "published")
        self.filter_combo.addItem("失败 / 已取消", "closed")
        self.filter_combo.currentIndexChanged.connect(lambda _: self.reload())
        head_layout.addWidget(self.filter_combo)
        layout.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 14)
        body_layout.setSpacing(10)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["编号", "平台", "标题", "状态", "时间", "链接"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 54)
        self.table.setColumnWidth(1, 74)
        self.table.setColumnWidth(3, 96)
        self.table.setColumnWidth(4, 128)
        self.table.setColumnWidth(5, 140)
        attach_platform_delegate(self.table, 1)
        self.table.setItemDelegateForColumn(3, StatusBadgeDelegate(self.table))
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        body_layout.addWidget(self.table, 1)

        self.empty = EmptyState(
            "还没有发布记录",
            hint="完成一次发布后，记录会出现在这里",
            icon="send",
            theme=self._theme,
        )
        body_layout.addWidget(self.empty)
        self.empty.setVisible(False)

        hint = QLabel(
            "「待你确认」表示表单已自动填好、正等你在浏览器里点发布。"
            "发完了就标记为已发布；没发成可标记失败或直接取消。"
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        body_layout.addWidget(hint)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.mark_button = make_button("标记为已发布", icon="check", theme=self._theme)
        self.mark_button.clicked.connect(self.mark_published)
        actions.addWidget(self.mark_button)
        self.fail_button = make_button("标记失败", icon="alert", theme=self._theme, ghost=True)
        self.fail_button.clicked.connect(self.mark_failed)
        actions.addWidget(self.fail_button)
        self.cancel_button = make_button("取消这次发布", theme=self._theme, ghost=True)
        self.cancel_button.clicked.connect(self.cancel_post)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        self.open_button = make_button("打开链接", icon="external", theme=self._theme, ghost=True)
        self.open_button.clicked.connect(self.open_link)
        actions.addWidget(self.open_button)
        self.log_button = make_button("审计日志", theme=self._theme, ghost=True)
        self.log_button.clicked.connect(self.show_logs)
        actions.addWidget(self.log_button)
        body_layout.addLayout(actions)

        layout.addWidget(body, 1)

        self._position()
        self.reload()

    # -- 位置：贴在父窗口右侧，形成「抽屉」的观感 ------------------------
    def _position(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        try:
            geo = parent.window().frameGeometry()
            screen = parent.screen()
            area = screen.availableGeometry() if screen is not None else geo
        except Exception:  # noqa: BLE001
            return
        width = min(640, max(520, geo.width() // 2))
        height = min(geo.height() - 60, area.height() - 60)
        x = min(geo.right() - width - 24, area.right() - width)
        y = max(area.top() + 20, geo.top() + 30)
        self.resize(width, height)
        self.move(max(area.left(), x), y)

    # -- 数据 ------------------------------------------------------------
    def reload(self) -> None:
        posts = db.list_posts(limit=200)
        mode = self.filter_combo.currentData()
        if mode == "open":
            posts = [p for p in posts if p.get("status") in POST_STATUS_OPEN]
        elif mode == "published":
            posts = [p for p in posts if p.get("status") == "published"]
        elif mode == "closed":
            posts = [p for p in posts if p.get("status") in ("failed", "cancelled")]
        self._posts = posts

        self.table.setRowCount(len(posts))
        for row, post in enumerate(posts):
            status = post.get("status", "")
            cells = [
                str(post.get("id", "")),
                platform_label(post.get("platform", "")),
                (post.get("title") or "")[:60],
                None,  # 状态列交给 StatusBadgeDelegate
                post.get("published_at") or post.get("created_at") or "",
                post.get("url") or (post.get("error") or "")[:40],
            ]
            for column, text in enumerate(cells):
                if column == 1:
                    cell = platform_item(text, post.get("platform", ""))
                elif column == 3:
                    cell = status_item(post_status_label(status), post_status_tone(status))
                else:
                    cell = QTableWidgetItem(str(text or ""))
                self.table.setItem(row, column, cell)

        has = bool(posts)
        self.table.setVisible(has)
        self.empty.setVisible(not has)
        self._refresh_summary()
        self._sync_buttons()

    def _refresh_summary(self) -> None:
        all_posts = db.list_posts(limit=200)
        pending = len([p for p in all_posts if p.get("status") in POST_STATUS_OPEN])
        published = len([p for p in all_posts if p.get("status") == "published"])
        failed = len([p for p in all_posts if p.get("status") == "failed"])
        parts = [f"共 {len(all_posts)} 条"]
        if pending:
            parts.append(f"{pending} 条待你处理")
        parts.append(f"已发布 {published}")
        if failed:
            parts.append(f"失败 {failed}")
        self.summary.setText("　·　".join(parts))

    # -- 选中与操作 ------------------------------------------------------
    def _selected_post(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._posts):
            return None
        return db.get_post(int(self._posts[row]["id"]))

    def _sync_buttons(self) -> None:
        post = self._selected_post()
        status = (post or {}).get("status", "")
        # 只有「还没落地」的记录才需要人工收口；已发布的不用再标记
        open_state = status in POST_STATUS_OPEN
        self.mark_button.setEnabled(bool(post) and open_state)
        self.fail_button.setEnabled(bool(post) and open_state)
        self.cancel_button.setEnabled(bool(post) and open_state)
        self.open_button.setEnabled(bool(post and post.get("url")))

    def mark_published(self) -> None:
        post = self._selected_post()
        if post is None:
            return
        url = post.get("url") or ""
        if not url:
            from PySide6.QtWidgets import QInputDialog

            url, ok = QInputDialog.getText(self, "作品链接", "粘贴作品链接（可留空）：")
            if not ok:
                return
        publish_service.mark_published_manually(int(post["id"]), url)
        self.reload()
        self.changed.emit()

    def mark_failed(self) -> None:
        post = self._selected_post()
        if post is None:
            return
        from PySide6.QtWidgets import QInputDialog

        reason, ok = QInputDialog.getText(
            self, "标记失败", "为什么没发成？（可留空，仅用于记录）："
        )
        if not ok:
            return
        publish_service.mark_failed_manually(int(post["id"]), reason)
        self.reload()
        self.changed.emit()

    def cancel_post(self) -> None:
        post = self._selected_post()
        if post is None:
            return
        answer = QMessageBox.question(
            self,
            "取消这次发布",
            "将把这条记录归档为「已取消」，不再出现在待处理里。\n\n"
            "如果内容还留在浏览器草稿箱，建议先自行清理。确定取消吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        publish_service.cancel_post(int(post["id"]))
        self.reload()
        self.changed.emit()

    def open_link(self) -> None:
        post = self._selected_post()
        if post is None or not post.get("url"):
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(post["url"]))

    def show_logs(self) -> None:
        logs = db.recent_logs(limit=200)
        dialog = QDialog(self)
        dialog.setWindowTitle("发布审计日志")
        dialog.resize(720, 460)
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
        dialog.setParent(None)
        dialog.deleteLater()
