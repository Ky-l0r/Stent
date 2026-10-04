"""首次启动引导（企划书验收清单第 2 条）。

三步向导：欢迎 → 配置 LLM 并测试连通性 → 完成。
用户也可跳过，之后在「设置」里补配。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...config import PROVIDER_PRESETS, preset_for, secrets
from ...core.llm import LLMClient
from ..components import Card, make_button
from ..context import AppContext
from ..theme import palette
from ..workers import WorkerHost

log = logging.getLogger(__name__)

INTRO = (
    "Stent 把你从「找热点 → 想标题 → 写文案 → 发出去 → 看数据」的重复劳动里解放出来。\n\n"
    "· 热点发现：聚合微博、抖音、知乎、B 站、百度、头条热榜，一键送入创作\n"
    "· 内容创作：按平台体例生成正文、标题候选、简介与标签，流式输出可中断\n"
    "· 发布中心：发布前检查 → 预览 → 人工确认 → 发布，默认不自动提交\n"
    "· 数据分析：同步平台指标、看趋势与排行，并把经验回流到账号画像\n\n"
    "全部数据只保存在你的电脑上。"
)


class OnboardingDialog(QDialog, WorkerHost):
    """首次启动向导。"""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._init_workers()
        self._worker = None

        self.setWindowTitle(f"欢迎使用 Stent v{__version__}")
        self.setMinimumSize(640, 520)
        self.setStyleSheet(self._qss())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(16)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self._build_config())
        self.stack.addWidget(self._build_done())
        layout.addWidget(self.stack, 1)

        self.step_label = QLabel("第 1 / 3 步")
        self.step_label.setObjectName("Faint")
        layout.addWidget(self.step_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.skip_button = make_button("跳过，稍后配置", theme=self.ctx.theme, ghost=True)
        self.skip_button.clicked.connect(self._skip)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        self.back_button = make_button("上一步", theme=self.ctx.theme)
        self.back_button.clicked.connect(self._back)
        self.back_button.setEnabled(False)
        buttons.addWidget(self.back_button)
        self.next_button = make_button("下一步", theme=self.ctx.theme, primary=True)
        self.next_button.clicked.connect(self._next)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def _qss(self) -> str:
        from ..theme import build_qss

        return build_qss(self.ctx.theme)

    def _build_welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        title = QLabel("欢迎使用 Stent")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        body = QLabel(INTRO)
        body.setObjectName("CardHint")
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addStretch(1)
        return page

    def _build_config(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel("配置模型（只需三项）")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Stent 使用 OpenAI 兼容协议，任何兼容服务商或本地 Ollama 都可以。"
            "API Key 会保存到本机加密存储（Windows 凭据管理器），不会明文落盘。"
        )
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        card = Card(padding=16, spacing=10)
        layout.addWidget(card)

        card.add(QLabel("服务商"))
        self.provider_combo = QComboBox()
        for preset in PROVIDER_PRESETS:
            self.provider_combo.addItem(preset.label, preset.key)
        self.provider_combo.currentIndexChanged.connect(self._on_provider)
        card.add(self.provider_combo)

        card.add(QLabel("API Base URL"))
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.deepseek.com/v1")
        card.add(self.base_url_edit)

        card.add(QLabel("API Key"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-...")
        card.add(self.api_key_edit)

        card.add(QLabel("模型名称"))
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("deepseek-chat")
        card.add(self.model_edit)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.test_button = make_button("测试连通性", icon="check", theme=self.ctx.theme)
        self.test_button.clicked.connect(self.test_connection)
        row.addWidget(self.test_button)
        row.addStretch(1)
        card.add_layout(row)

        self.test_result = QLabel("尚未测试")
        self.test_result.setObjectName("CardHint")
        self.test_result.setWordWrap(True)
        card.add(self.test_result)

        self.storage_label = QLabel(f"密钥存储方式：{secrets.describe_mode()}")
        self.storage_label.setObjectName("Faint")
        card.add(self.storage_label)

        layout.addStretch(1)
        self._on_provider()
        return page

    def _build_done(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        title = QLabel("准备就绪")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        body = QLabel(
            "接下来建议：\n\n"
            "1. 到「账号画像」填写你的定位、风格与受众 —— 这一步会显著提升文案质量；\n"
            "2. 到「热点发现」刷新一次热榜，挑一个热点送入创作；\n"
            "3. 在「发布中心」完成登录后，即可体验发布流程。\n\n"
            "发布默认只自动填写表单，最终提交由你在浏览器里确认。"
        )
        body.setObjectName("CardHint")
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def _on_provider(self) -> None:
        preset = next((p for p in PROVIDER_PRESETS if p.key == self.provider_combo.currentData()), None)
        if preset is None or not preset.base_url:
            return
        self.base_url_edit.setText(preset.base_url)
        if preset.model:
            self.model_edit.setText(preset.model)

    def _index(self) -> int:
        return self.stack.currentIndex()

    def _next(self) -> None:
        index = self._index()
        if index == 1 and not self._save_config():
            return
        if index >= self.stack.count() - 1:
            self._finish()
            return
        self.stack.setCurrentIndex(index + 1)
        self._sync_buttons()

    def _back(self) -> None:
        index = self._index()
        if index <= 0:
            return
        self.stack.setCurrentIndex(index - 1)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        index = self._index()
        self.step_label.setText(f"第 {index + 1} / {self.stack.count()} 步")
        self.back_button.setEnabled(index > 0)
        last = index >= self.stack.count() - 1
        self.next_button.setText("开始使用" if last else "下一步")
        self.skip_button.setText("跳过，稍后配置" if not last else "关闭")

    def _save_config(self) -> bool:
        base_url = self.base_url_edit.text().strip()
        model = self.model_edit.text().strip()
        key = self.api_key_edit.text().strip()
        if not base_url or not model:
            self.test_result.setText("请填写 API Base URL 与模型名称，或点击「跳过，稍后配置」")
            self.test_result.setStyleSheet(f"color: {palette(self.ctx.theme).warning};")
            return False
        preset = preset_for(base_url)
        self.ctx.config_manager.update_llm(
            provider=(preset.key if preset else (self.provider_combo.currentData() or "custom")),
            base_url=base_url,
            model=model,
        )
        if key:
            secrets.set_api_key(key)
        self.ctx.reload_llm()
        return True

    def test_connection(self) -> None:
        base_url = self.base_url_edit.text().strip()
        model = self.model_edit.text().strip()
        if not base_url or not model:
            self.test_result.setText("请先填写 Base URL 与模型名称")
            return
        from ...config import LLMConfig

        self.test_button.setEnabled(False)
        self.test_button.setText("测试中…")
        self.test_result.setText("正在请求服务商…")
        client = LLMClient(LLMConfig(base_url=base_url, model=model, timeout=25), self.api_key_edit.text().strip())
        self._worker = self.run_task(
            lambda worker: client.test_connection(),
            on_result=self._on_tested,
            on_error=lambda msg: self._on_tested((False, msg)),
            on_done=self._on_test_done,
            name="onboarding-test",
        )

    def _on_tested(self, result) -> None:
        ok, message = result
        self.test_result.setText(("✓ " if ok else "✗ ") + message)
        self.test_result.setStyleSheet(f"color: {palette(self.ctx.theme).success if ok else palette(self.ctx.theme).danger};")

    def _on_test_done(self) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连通性")
        self._worker = None

    def _finish(self) -> None:
        self._save_config()
        self.ctx.config_manager.update(onboarding_done=True)
        self.accept()

    def _skip(self) -> None:
        if self._index() >= self.stack.count() - 1:
            self._finish()
            return
        self.ctx.config_manager.update(onboarding_done=True)
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.cancel_all()
        try:
            self.ctx.config_manager.update(onboarding_done=True)
        except Exception:  # pragma: no cover
            pass
        super().closeEvent(event)


def should_show_onboarding(ctx: AppContext) -> bool:
    return not bool(getattr(ctx.config_manager.config, "onboarding_done", False))
