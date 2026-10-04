"""应用上下文：页面之间共享的状态与信号总线。

页面不直接互相引用，一律通过 ``AppContext`` 通信（导航、主题、提示），
降低耦合，也便于后续模块独立演进。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from ..config import ConfigManager, config_manager as default_config
from ..core.db import db
from ..core.llm import LLMClient as _LLMClient

log = logging.getLogger(__name__)


class AppContext(QObject):
    """全局上下文。"""

    theme_changed = Signal(str)
    navigate_requested = Signal(str)  # 页面 key
    toast_requested = Signal(str, str)  # (文本, 级别)
    config_changed = Signal()  # LLM 配置变化，页面可据此刷新可用性
    data_changed = Signal(str)  # 数据变更，携带领域名（drafts/posts/metrics/hot）

    def __init__(self, config: ConfigManager | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config_manager: ConfigManager = config or default_config
        self.db = db
        #: 冒烟自检模式：跳过一切网络请求与浏览器操作
        self.selftest = False
        self._llm: _LLMClient | None = None
        self._llm_signature: tuple | None = None

    # -- 主题 ------------------------------------------------------------
    @property
    def theme(self) -> str:
        return getattr(self.config_manager.config, "theme", "light") or "light"

    def set_theme(self, theme: str) -> None:
        theme = "dark" if str(theme).lower() == "dark" else "light"
        if theme == self.theme:
            return
        self.config_manager.update(theme=theme)
        self.theme_changed.emit(theme)

    def toggle_theme(self) -> str:
        new_theme = "dark" if self.theme == "light" else "light"
        self.set_theme(new_theme)
        return new_theme

    # -- 导航与提示 ------------------------------------------------------
    def navigate(self, key: str) -> None:
        self.navigate_requested.emit(key)

    def toast(self, text: str, level: str = "info") -> None:
        self.toast_requested.emit(text, level)

    def notify_data_changed(self, domain: str) -> None:
        self.data_changed.emit(domain)

    # -- LLM -------------------------------------------------------------
    @property
    def llm(self) -> _LLMClient | None:
        """按当前配置构造 LLM 客户端；未配置完成时返回 None。"""
        cfg = self.config_manager.config.llm
        key = self.config_manager.api_key()
        signature = (cfg.base_url, cfg.model, bool(key), cfg.timeout, cfg.retries)
        if self._llm is None or self._llm_signature != signature:
            self._llm = _LLMClient(cfg, key)
            self._llm_signature = signature
        return self._llm

    def require_llm(self) -> _LLMClient | None:
        """取 LLM 客户端；未配置时给出提示并返回 None。"""
        if not self.config_manager.ready():
            self.toast("尚未配置 LLM：请到「设置」填写 API Base URL、Key 与模型名称", "warn")
            return None
        return self.llm

    def reload_llm(self) -> None:
        self._llm = None
        self._llm_signature = None
        self.config_changed.emit()
