"""设置页：LLM 配置中心（企划书 4.4）+ 平台账号 + 通用设置 + 存储与安全信息。

用户只需配置三个字段：API Base URL、API Key、模型名称；
API Key 走 keyring / DPAPI 加密存储，不明文落盘。

界面要点（v1.0.6 打磨）：

- 每个栏目都是可点击展开的折叠卡：条目只会越来越多，折叠后一屏就能看全
- 新增「平台账号」栏目：逐平台查看登录状态，支持检测 / 登录 / 清除登录态
"""

from __future__ import annotations

import logging
import os
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ... import __version__, paths
from ...config import PROVIDER_PRESETS, preset_for, secrets
from ...core.llm import LLMClient
from ...platforms import keys as platform_keys, platform_label
from ...services.hotsearch import DEFAULT_PLATFORMS, PLATFORM_LABELS
from .. import icons
from ..components import CollapsibleCard, PageHeader, ScrollArea, make_button
from ..theme import level_color, palette, platform_color
from .base import BasePage

log = logging.getLogger(__name__)

#: LLM 接入栏目的标题（单独提出来，避免源码里出现成对的方括号标记）
LLM_CARD_TITLE = "LLM 接入（[OI] 兼容协议）"
LLM_CARD_HINT = "支持 [OI]、DeepSeek、通义千问、Kimi、智谱、硅基流动、Ollama 本地模型与自建中转"


class SettingsPage(BasePage):
    key = "settings"
    title = "设置"
    icon = "settings"

    def build(self) -> None:
        self._worker = None
        self._loading = False
        self._account_rows: dict[str, dict] = {}
        self._account_worker = None
        self._accounts_checked = False

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        # 默认展开最常用的两项，其余收起——条目变多也不会一屏塞满
        self.llm_card = self._build_llm_card()
        self.accounts_card = self._build_accounts_card()
        self.general_card = self._build_general_card()
        self.storage_card = self._build_storage_card()
        self.about_card = self._build_about_card()
        for card in (
            self.llm_card,
            self.accounts_card,
            self.general_card,
            self.storage_card,
            self.about_card,
        ):
            layout.addWidget(card)
        layout.addStretch(1)

        header = PageHeader("设置", "配置 LLM 接入、平台账号、通用偏好与本地存储；所有配置只保存在本机")
        header.add_action(self.status_light)
        self.save_button = make_button("保存设置", icon="save", theme=self.ctx.theme, primary=True)
        self.save_button.clicked.connect(self.save)
        header.add_action(self.save_button)
        self._root.insertWidget(0, header)

        self.add(ScrollArea(inner), 1)

    # ------------------------------------------------------------------
    # LLM 接入
    # ------------------------------------------------------------------
    def _build_llm_card(self) -> QWidget:
        card = CollapsibleCard(
            LLM_CARD_TITLE,
            icon="sparkles",
            hint=LLM_CARD_HINT,
            theme=self.ctx.theme,
            expanded=True,
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.provider_combo = QComboBox()
        for preset in PROVIDER_PRESETS:
            self.provider_combo.addItem(preset.label, preset.key)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("服务商", self.provider_combo)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        form.addRow("API Base URL", self.base_url_edit)

        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(8)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-...（仅保存在本机加密存储中）")
        key_layout.addWidget(self.api_key_edit, 1)
        self.show_key_button = make_button("显示", theme=self.ctx.theme, ghost=True)
        self.show_key_button.setCheckable(True)
        self.show_key_button.toggled.connect(
            lambda checked: (
                self.api_key_edit.setEchoMode(
                    QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
                ),
                self.show_key_button.setText("隐藏" if checked else "显示"),
            )
        )
        key_layout.addWidget(self.show_key_button)
        form.addRow("API Key", key_row)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("deepseek-chat")
        form.addRow("模型名称", self.model_edit)

        self.embedding_edit = QLineEdit()
        self.embedding_edit.setPlaceholderText("可留空；留空时记忆检索降级为关键词匹配")
        form.addRow("Embedding 模型", self.embedding_edit)

        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(0.7)
        form.addRow("温度", self.temperature_spin)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 32768)
        self.max_tokens_spin.setSingleStep(256)
        self.max_tokens_spin.setValue(4096)
        form.addRow("最大输出 tokens", self.max_tokens_spin)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 600)
        self.timeout_spin.setValue(90)
        self.timeout_spin.setSuffix(" 秒")
        form.addRow("请求超时", self.timeout_spin)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 5)
        self.retries_spin.setValue(2)
        self.retries_spin.setSuffix(" 次")
        form.addRow("失败自动重试", self.retries_spin)

        card.add_layout(form)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.test_button = make_button("测试连通性", icon="check", theme=self.ctx.theme)
        self.test_button.clicked.connect(self.test_connection)
        row.addWidget(self.test_button)
        self.models_button = make_button("拉取模型列表", icon="refresh", theme=self.ctx.theme, ghost=True)
        self.models_button.clicked.connect(self.list_models)
        row.addWidget(self.models_button)
        row.addStretch(1)
        self.test_result = QLabel("尚未测试")
        self.test_result.setObjectName("CardHint")
        row.addWidget(self.test_result)
        card.add_layout(row)

        self.key_storage_label = QLabel()
        self.key_storage_label.setObjectName("Faint")
        self.key_storage_label.setWordWrap(True)
        card.add(self.key_storage_label)
        return card

    # ------------------------------------------------------------------
    # 平台账号
    # ------------------------------------------------------------------
    def _build_accounts_card(self) -> QWidget:
        card = CollapsibleCard(
            "平台账号",
            icon="user",
            hint="查看各平台登录状态；发布与数据同步都依赖这里的登录态",
            theme=self.ctx.theme,
            expanded=True,
        )

        intro = QLabel(
            "Stent 只保存本机浏览器登录态，不会读取或上传你的账号密码。"
            "登录会打开一个浏览器窗口，扫码或输入账号即可。"
        )
        intro.setObjectName("FieldHint")
        intro.setWordWrap(True)
        card.add(intro)

        # 表头
        head = QHBoxLayout()
        head.setSpacing(10)
        for text, width in (("平台", 0), ("登录状态", 0)):
            label = QLabel(text)
            label.setObjectName("FieldLabel")
            head.addWidget(label, 1 if width == 0 else 0)
        head.addSpacing(160)
        card.add_layout(head)

        for key in platform_keys():
            card.add(self._build_account_row(key))

        row = QHBoxLayout()
        row.setSpacing(8)
        self.check_accounts_button = make_button(
            "检测全部平台", icon="refresh", theme=self.ctx.theme
        )
        self.check_accounts_button.setToolTip(
            "逐个平台启动浏览器做权威校验，需要较长时间；平时进页面会自动做本地快速判断"
        )
        self.check_accounts_button.clicked.connect(lambda: self.check_accounts(force=True))
        row.addWidget(self.check_accounts_button)
        row.addStretch(1)
        self.accounts_summary = QLabel("尚未检测")
        self.accounts_summary.setObjectName("CardHint")
        row.addWidget(self.accounts_summary)
        card.add_layout(row)
        return card

    def _build_account_row(self, key: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        icon = QLabel()
        icon.setFixedSize(16, 16)
        icon.setPixmap(icons.pixmap("user", platform_color(key), 16))
        layout.addWidget(icon)

        name = QLabel(platform_label(key))
        name.setObjectName("FieldLabel")
        name.setMinimumWidth(72)
        layout.addWidget(name)

        status = QLabel("尚未检测")
        status.setObjectName("CardHint")
        status.setWordWrap(True)
        layout.addWidget(status, 1)

        check = make_button("检测", theme=self.ctx.theme, ghost=True)
        check.clicked.connect(lambda _=False, k=key: self.check_account(k))
        layout.addWidget(check)

        login = make_button("登录", icon="login", theme=self.ctx.theme)
        login.clicked.connect(lambda _=False, k=key: self.login_account(k))
        layout.addWidget(login)

        logout = make_button("清除登录态", theme=self.ctx.theme, ghost=True)
        logout.setToolTip("删除本机保存的该平台登录态（不会影响你在平台上的账号）")
        logout.clicked.connect(lambda _=False, k=key: self.logout_account(k))
        layout.addWidget(logout)

        self._account_rows[key] = {
            "status": status,
            "check": check,
            "login": login,
            "logout": logout,
        }
        return row

    def check_accounts(self, *, force: bool = False) -> None:
        """检测全部平台的登录状态。

        默认走**本地快速判断**（读持久化目录里的 Cookie，毫秒级）；
        只有用户明确点「检测全部平台」时才启动浏览器做权威校验——
        后者每个平台都要拉起一次 Chromium，四个平台跑下来要几十秒，
        放在进页面时自动执行会让设置页看起来像卡死了。
        """
        if self._account_worker is not None and self._account_worker.isRunning():
            self.toast("正在检测中，请稍候", "info")
            return
        self._accounts_checked = True

        if not force:
            from ...services.publisher import account_overview

            ok_count = 0
            for item in account_overview():
                row = self._account_rows.get(item["key"])
                if row is None:
                    continue
                if item["logged_in"]:
                    ok_count += 1
                    self._set_account_status(row["status"], "已登录", "success")
                    row["status"].setToolTip(item["note"])
                    row["logout"].setEnabled(True)
                else:
                    self._set_account_status(row["status"], f"未登录 · {item['note']}", "idle")
                    row["status"].setToolTip(item["note"])
                    row["logout"].setEnabled(False)
            total = len(self._account_rows)
            self.accounts_summary.setText(f"{ok_count} / {total} 个平台已登录（本地记录）")
            return

        self.check_accounts_button.setEnabled(False)
        self.check_accounts_button.setText("检测中…")
        self.accounts_summary.setText("正在逐个平台校验（需要启动浏览器，请稍候）…")
        for row in self._account_rows.values():
            row["check"].setEnabled(False)
            self._set_account_status(row["status"], "检测中…", "busy")

        def job(worker):
            results: dict[str, tuple[bool, str]] = {}
            for key in platform_keys():
                if getattr(worker.cancel_event, "is_set", lambda: False)():
                    break
                try:
                    from ...services.publisher import publish_service

                    results[key] = publish_service.check_login(key)
                except Exception as exc:  # noqa: BLE001
                    results[key] = (False, f"检测失败：{exc}")
            return results

        self._account_worker = self.run_task(
            job,
            on_result=self._on_accounts_checked,
            on_error=self._on_accounts_error,
            on_done=self._on_accounts_done,
            name="check-accounts",
        )

    def _on_accounts_checked(self, results: dict) -> None:
        ok_count = 0
        for key, (ok, message) in (results or {}).items():
            row = self._account_rows.get(key)
            if row is None:
                continue
            if ok:
                ok_count += 1
                self._set_account_status(row["status"], "已登录", "success")
                row["status"].setToolTip(message or "")
                row["logout"].setEnabled(True)
            else:
                self._set_account_status(row["status"], f"未登录 · {str(message)[:40]}", "warn")
                row["status"].setToolTip(str(message))
                row["logout"].setEnabled(False)
        total = len(self._account_rows)
        self.accounts_summary.setText(f"{ok_count} / {total} 个平台已登录")

    def _on_accounts_error(self, message: str) -> None:
        self.accounts_summary.setText(f"检测失败：{message[:60]}")

    def _on_accounts_done(self) -> None:
        self._account_worker = None
        self.check_accounts_button.setEnabled(True)
        self.check_accounts_button.setText("检测全部平台")
        for row in self._account_rows.values():
            row["check"].setEnabled(True)

    def check_account(self, key: str) -> None:
        row = self._account_rows.get(key)
        if row is None:
            return
        row["check"].setEnabled(False)
        self._set_account_status(row["status"], "检测中…", "busy")

        from ...services.publisher import publish_service

        self.run_task(
            lambda worker: publish_service.check_login(key),
            on_result=lambda result: self._on_account_checked(key, result),
            on_error=lambda msg: self._on_account_checked(key, (False, msg)),
            on_done=lambda: row["check"].setEnabled(True),
            name=f"check-{key}",
        )

    def _on_account_checked(self, key: str, result) -> None:
        row = self._account_rows.get(key)
        if row is None:
            return
        ok, message = result
        if ok:
            self._set_account_status(row["status"], "已登录", "success")
            row["logout"].setEnabled(True)
        else:
            self._set_account_status(row["status"], f"未登录 · {str(message)[:40]}", "warn")
            row["logout"].setEnabled(False)
        row["status"].setToolTip(str(message))

    def _set_account_status(self, label: QLabel, text: str, level: str) -> None:
        label.setText(text)
        label.setStyleSheet(
            f"color: {level_color(self.ctx.theme, level)}; background: transparent;"
        )

    def login_account(self, key: str) -> None:
        label = platform_label(key)
        answer = QMessageBox.question(
            self,
            f"登录{label}",
            f"将打开浏览器窗口展示{label}登录页，请在其中扫码或输入账号完成登录。\n\n"
            "Stent 只保存本地浏览器登录态，不会读取或上传你的账号密码。\n\n现在开始吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        row = self._account_rows.get(key)
        if row is not None:
            row["login"].setEnabled(False)
            row["login"].setText("等待登录…")
            self._set_account_status(row["status"], "已打开浏览器，等待你完成登录…", "busy")

        from ...services.publisher import publish_service

        self.run_task(
            lambda worker: publish_service.login(key),
            on_result=lambda result: self._on_account_login_done(key, result),
            on_error=lambda msg: self._on_account_login_done(key, (False, msg)),
            on_done=lambda: self._on_account_login_reset(key),
            name=f"login-{key}",
        )

    def _on_account_login_done(self, key: str, result) -> None:
        row = self._account_rows.get(key)
        if row is None:
            return
        ok, message = result
        if ok:
            self._set_account_status(row["status"], "已登录", "success")
            row["logout"].setEnabled(True)
            self.toast(f"{platform_label(key)} 登录成功", "success")
        else:
            self._set_account_status(row["status"], f"登录未完成 · {str(message)[:40]}", "warn")
            self.toast(f"{platform_label(key)} 登录未完成", "warn")
        row["status"].setToolTip(str(message))
        self._refresh_accounts_summary()

    def _on_account_login_reset(self, key: str) -> None:
        row = self._account_rows.get(key)
        if row is not None:
            row["login"].setEnabled(True)
            row["login"].setText("登录")

    def logout_account(self, key: str) -> None:
        label = platform_label(key)
        answer = QMessageBox.question(
            self,
            f"清除{label}登录态",
            f"将删除本机保存的{label}登录态，下次发布或同步数据前需要重新登录。\n\n"
            "这不会影响你在平台上的账号本身。确定清除吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        row = self._account_rows.get(key)
        if row is not None:
            row["logout"].setEnabled(False)
        from ...services.publisher import publish_service

        self.run_task(
            lambda worker: publish_service.logout(key),
            on_result=lambda result: self._on_account_logout_done(key, result),
            on_error=lambda msg: self._on_account_logout_done(key, (False, msg)),
            name=f"logout-{key}",
        )

    def _on_account_logout_done(self, key: str, result) -> None:
        row = self._account_rows.get(key)
        if row is None:
            return
        ok, message = result
        if ok:
            self._set_account_status(row["status"], "已清除登录态", "idle")
            self.toast(f"{platform_label(key)} 登录态已清除", "success")
        else:
            self._set_account_status(row["status"], f"清除失败 · {str(message)[:40]}", "warn")
        row["status"].setToolTip(str(message))
        self._refresh_accounts_summary()

    def _refresh_accounts_summary(self) -> None:
        rows = self._account_rows
        if not rows:
            return
        ok_count = sum(1 for r in rows.values() if r["status"].text() == "已登录")
        self.accounts_summary.setText(f"{ok_count} / {len(rows)} 个平台已登录")

    # ------------------------------------------------------------------
    # 通用
    # ------------------------------------------------------------------
    def _build_general_card(self) -> QWidget:
        card = CollapsibleCard(
            "通用", icon="settings", hint="主题、热榜缓存与窗口动画", theme=self.ctx.theme, expanded=False
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("浅色", "light")
        self.theme_combo.addItem("暗色", "dark")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_pick)
        form.addRow("界面主题", self.theme_combo)

        self.cache_spin = QSpinBox()
        self.cache_spin.setRange(60, 3600)
        self.cache_spin.setSingleStep(60)
        self.cache_spin.setSuffix(" 秒")
        form.addRow("热榜缓存时长", self.cache_spin)

        card.add_layout(form)

        card.add(QLabel("默认聚合的热榜平台"))
        platforms_row = QHBoxLayout()
        platforms_row.setSpacing(10)
        self.platform_checks: dict[str, QCheckBox] = {}
        for key in DEFAULT_PLATFORMS:
            box = QCheckBox(PLATFORM_LABELS.get(key, key))
            self.platform_checks[key] = box
            platforms_row.addWidget(box)
        platforms_row.addStretch(1)
        card.add_layout(platforms_row)

        self.auto_sync_box = QCheckBox("启动后自动同步一次数据分析指标")
        card.add(self.auto_sync_box)

        self.animations_box = QCheckBox("启用窗口过渡动画（最大化 / 还原 / 最小化）")
        self.animations_box.setToolTip(
            "Qt 界面为 CPU 渲染，1920×1080 单帧重绘约 29ms，动画帧率上限 35~40fps，\n"
            "大窗口下可能不够跟手，因此默认关闭（瞬间切换更利落）。\n"
            "开启后 Snap、圆角、阴影等其余行为不受影响。"
        )
        card.add(self.animations_box)
        return card

    def _build_storage_card(self) -> QWidget:
        card = CollapsibleCard(
            "存储与安全", icon="folder", hint="本地数据目录与诊断导出", theme=self.ctx.theme, expanded=False
        )

        self.paths_label = QLabel()
        self.paths_label.setObjectName("CardHint")
        self.paths_label.setWordWrap(True)
        self.paths_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.add(self.paths_label)

        row = QHBoxLayout()
        row.setSpacing(8)
        open_button = make_button("打开数据目录", icon="folder", theme=self.ctx.theme)
        open_button.clicked.connect(self.open_data_dir)
        row.addWidget(open_button)
        export_button = make_button("导出诊断信息", icon="external", theme=self.ctx.theme, ghost=True)
        export_button.clicked.connect(self.export_diagnostics)
        row.addWidget(export_button)
        row.addStretch(1)
        card.add_layout(row)

        note = QLabel(
            "API Key 不会以明文写入任何文件；Stent 也不会读取或上传你的平台账号密码，"
            "平台登录态保存在本机浏览器目录中。"
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)
        card.add(note)
        return card

    def _build_about_card(self) -> QWidget:
        card = CollapsibleCard("关于", icon="info", theme=self.ctx.theme, expanded=False)
        text = QLabel(
            f"Stent v{__version__}　·　基于 Easel（ZJU-REAL/Easel，Apache-2.0）提取重构的桌面端社媒内容智能体\n"
            "四大模块：热点发现 · 内容创作 · 发布中心 · 数据分析\n"
            "本软件以 Apache-2.0 许可发布，复用的上游代码版权归原作者所有，详见随包 NOTICE 文件。"
        )
        text.setObjectName("CardHint")
        text.setWordWrap(True)
        card.add(text)
        return card

    # ------------------------------------------------------------------
    def on_first_show(self) -> None:
        self.reload()
        if not self._accounts_checked:
            # 本地快速判断，毫秒级返回，不会让页面看起来像卡住
            self.check_accounts(force=False)

    def apply_theme(self, theme: str) -> None:
        for card in (
            self.llm_card,
            self.accounts_card,
            self.general_card,
            self.storage_card,
            self.about_card,
        ):
            card.apply_theme(theme)

    def reload(self) -> None:
        self._loading = True
        cfg = self.ctx.config_manager.config
        llm = cfg.llm

        index = self.provider_combo.findData(llm.provider)
        if index < 0:
            index = self.provider_combo.findData("custom")
        self.provider_combo.setCurrentIndex(max(0, index))
        self.base_url_edit.setText(llm.base_url)
        self.model_edit.setText(llm.model)
        self.embedding_edit.setText(getattr(llm, "embedding_model", "") or "")
        self.temperature_spin.setValue(float(llm.temperature))
        self.max_tokens_spin.setValue(int(llm.max_tokens))
        self.timeout_spin.setValue(int(llm.timeout))
        self.retries_spin.setValue(int(llm.retries))

        key = self.ctx.config_manager.api_key()
        self.api_key_edit.setText(key)
        self.test_result.setText("已保存密钥" if key else "尚未配置密钥")
        self.key_storage_label.setText(
            f"密钥存储方式：{secrets.describe_mode()}"
            + ("" if secrets.mode != "none" else "　⚠ 当前系统不支持加密存储，密钥仅在本次会话中有效")
        )

        self.theme_combo.setCurrentIndex(1 if self.ctx.theme == "dark" else 0)
        self.cache_spin.setValue(int(cfg.hotlist_cache_ttl))
        enabled = set(cfg.hotlist_platforms or DEFAULT_PLATFORMS)
        for key_name, box in self.platform_checks.items():
            box.setChecked(key_name in enabled)
        self.auto_sync_box.setChecked(bool(cfg.analytics_auto_sync))
        self.animations_box.setChecked(bool(getattr(cfg, "ui_animations", False)))

        self.paths_label.setText(
            f"数据目录：{paths.data_dir()}\n"
            f"数据库：{paths.db_path()}\n"
            f"浏览器登录态：{paths.browser_dir()}\n"
            f"日志：{paths.log_path()}"
        )
        self._loading = False

    # ------------------------------------------------------------------
    def _on_provider_changed(self) -> None:
        if self._loading:
            return
        preset = next((p for p in PROVIDER_PRESETS if p.key == self.provider_combo.currentData()), None)
        if preset is None or not preset.base_url:
            return
        self.base_url_edit.setText(preset.base_url)
        if preset.model:
            self.model_edit.setText(preset.model)

    def _on_theme_pick(self) -> None:
        if self._loading:
            return
        self.ctx.set_theme(self.theme_combo.currentData() or "light")

    def _collect(self) -> dict:
        return {
            "provider": self.provider_combo.currentData() or "custom",
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "embedding_model": self.embedding_edit.text().strip(),
            "temperature": float(self.temperature_spin.value()),
            "max_tokens": int(self.max_tokens_spin.value()),
            "timeout": int(self.timeout_spin.value()),
            "retries": int(self.retries_spin.value()),
        }

    def save(self) -> None:
        llm_fields = self._collect()
        base_url = llm_fields["base_url"]
        model = llm_fields["model"]
        if not base_url or not model:
            self.banner.show_message("API Base URL 与模型名称不能为空", "error")
            return

        preset = preset_for(base_url)
        if preset is not None:
            llm_fields["provider"] = preset.key
        self.ctx.config_manager.update_llm(**llm_fields)

        key_text = self.api_key_edit.text().strip()
        encrypted = secrets.set_api_key(key_text)
        if key_text and not encrypted:
            self.banner.show_message(
                "配置已保存，但当前系统不支持加密存储，API Key 未能落盘（重启后需重新输入）", "warn"
            )
        else:
            self.banner.show_message("设置已保存", "success")

        self.ctx.config_manager.update(
            hotlist_cache_ttl=int(self.cache_spin.value()),
            hotlist_platforms=[k for k, b in self.platform_checks.items() if b.isChecked()] or list(DEFAULT_PLATFORMS),
            analytics_auto_sync=bool(self.auto_sync_box.isChecked()),
            ui_animations=bool(self.animations_box.isChecked()),
        )
        self.ctx.reload_llm()
        self.ctx.notify_data_changed("settings")
        self.toast("设置已保存", "success")
        self.key_storage_label.setText(f"密钥存储方式：{secrets.describe_mode()}")

    # ------------------------------------------------------------------
    def _build_client(self) -> LLMClient | None:
        llm_fields = self._collect()
        if not llm_fields["base_url"] or not llm_fields["model"]:
            self.banner.show_message("请先填写 API Base URL 与模型名称", "warn")
            return None
        from ...config import LLMConfig

        cfg = LLMConfig(**{k: v for k, v in llm_fields.items() if k != "provider"})
        return LLMClient(cfg, self.api_key_edit.text().strip())

    def test_connection(self) -> None:
        client = self._build_client()
        if client is None:
            return
        self.test_button.setEnabled(False)
        self.test_button.setText("测试中…")
        self.test_result.setText("正在测试…")
        self._worker = self.run_task(
            lambda worker: client.test_connection(),
            on_result=self._on_tested,
            on_error=lambda msg: self._on_tested((False, msg)),
            on_done=self._on_test_done,
            name="test-llm",
        )

    def _on_tested(self, result) -> None:
        ok, message = result
        p = palette(self.ctx.theme)
        self.test_result.setText(message)
        self.test_result.setStyleSheet(f"color: {p.success if ok else p.danger};")
        self.banner.show_message(message, "success" if ok else "error")

    def _on_test_done(self) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连通性")
        self._worker = None

    def list_models(self) -> None:
        client = self._build_client()
        if client is None:
            return
        self.models_button.setEnabled(False)
        self.models_button.setText("拉取中…")
        self._worker = self.run_task(
            lambda worker: client.list_models(),
            on_result=self._on_models,
            on_error=lambda msg: self.banner.show_message(f"拉取失败：{msg}", "error"),
            on_done=self._on_models_done,
            name="list-models",
        )

    def _on_models(self, result) -> None:
        ok, payload = result
        if not ok:
            self.banner.show_message(f"拉取模型列表失败（不影响使用）：{payload}", "warn")
            return
        models = payload if isinstance(payload, list) else []
        self.banner.show_message(f"该服务商共有 {len(models)} 个模型，例如：{', '.join(models[:6])}", "success")

    def _on_models_done(self) -> None:
        self.models_button.setEnabled(True)
        self.models_button.setText("拉取模型列表")
        self._worker = None

    # ------------------------------------------------------------------
    def open_data_dir(self) -> None:
        path = str(paths.data_dir())
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            self.toast(f"打开失败：{exc}", "error")

    def export_diagnostics(self) -> None:
        import json
        import platform as py_platform

        from PySide6.QtWidgets import QFileDialog

        default = paths.exports_dir() / "diagnostics.json"
        path, _ = QFileDialog.getSaveFileName(self, "导出诊断信息", str(default), "JSON (*.json)")
        if not path:
            return
        cfg = self.ctx.config_manager.config
        info = {
            "version": __version__,
            "python": py_platform.python_version(),
            "platform": py_platform.platform(),
            "llm": {
                "provider": cfg.llm.provider,
                "base_url": cfg.llm.base_url,
                "model": cfg.llm.model,
                "has_key": bool(self.ctx.config_manager.api_key()),
            },
            "secret_storage": secrets.describe_mode(),
            "data_dir": str(paths.data_dir()),
            "hotlist_platforms": cfg.hotlist_platforms,
            "theme": cfg.theme,
            "platform_accounts": {
                key: row["status"].text() for key, row in self._account_rows.items()
            },
        }
        try:
            from pathlib import Path

            Path(path).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.toast(f"导出失败：{exc}", "error")
            return
        self.toast("诊断信息已导出（不含密钥明文）", "success")
