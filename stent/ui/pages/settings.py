"""设置页：LLM 配置中心（企划书 4.4）+ 通用设置 + 存储与安全信息。

用户只需配置三个字段：API Base URL、API Key、模型名称；
API Key 走 keyring / DPAPI 加密存储，不明文落盘。
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
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ... import __version__, paths
from ...config import PROVIDER_PRESETS, preset_for, secrets
from ...core.llm import LLMClient
from ...services.hotsearch import DEFAULT_PLATFORMS, PLATFORM_LABELS
from ..components import Card, CardTitle, PageHeader, ScrollArea, make_button
from ..theme import palette
from .base import BasePage

log = logging.getLogger(__name__)


class SettingsPage(BasePage):
    key = "settings"
    title = "设置"
    icon = "settings"

    def build(self) -> None:
        self._worker = None
        self._loading = False

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)

        layout.addWidget(self._build_llm_card())
        layout.addWidget(self._build_general_card())
        layout.addWidget(self._build_storage_card())
        layout.addWidget(self._build_about_card())
        layout.addStretch(1)

        header = PageHeader("设置", "配置 LLM 接入、通用偏好与本地存储；所有配置只保存在本机")
        header.add_action(self.status_light)
        self.save_button = make_button("保存设置", icon="save", theme=self.ctx.theme, primary=True)
        self.save_button.clicked.connect(self.save)
        header.add_action(self.save_button)
        self._root.insertWidget(0, header)

        self.add(ScrollArea(inner), 1)

    # ------------------------------------------------------------------
    def _build_llm_card(self) -> QWidget:
        card = Card(padding=16, spacing=12)
        card.add(
            CardTitle(
                "LLM 接入（OpenAI 兼容协议）",
                icon="sparkles",
                hint="支持 OpenAI、DeepSeek、通义千问、Kimi、智谱、硅基流动、Ollama 本地模型与自建中转",
                theme=self.ctx.theme,
            )
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

    def _build_general_card(self) -> QWidget:
        card = Card(padding=16, spacing=12)
        card.add(CardTitle("通用", icon="settings", theme=self.ctx.theme))

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
        return card

    def _build_storage_card(self) -> QWidget:
        card = Card(padding=16, spacing=10)
        card.add(CardTitle("存储与安全", icon="folder", theme=self.ctx.theme))

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
        card = Card(padding=16, spacing=8)
        card.add(CardTitle("关于", icon="info", theme=self.ctx.theme))
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
        }
        try:
            from pathlib import Path

            Path(path).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.toast(f"导出失败：{exc}", "error")
            return
        self.toast("诊断信息已导出（不含密钥明文）", "success")

    def apply_theme(self, theme: str) -> None:
        pass
