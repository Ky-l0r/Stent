"""配置中心：config.json + keyring/DPAPI 密钥加密存储。

设计原则（企划书 4.4）：
- 用户只需配置三个字段：API Base URL、API Key、模型名称；
- API Key 不以明文落盘，优先使用系统 keyring（Windows 凭据管理器），
  不可用时降级为 DPAPI 加密文件，并明确告知用户。
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths

log = logging.getLogger(__name__)

KEYRING_SERVICE = "Stent"
KEYRING_USER = "llm_api_key"
FALLBACK_SECRET_FILE = "secret.bin"


# --------------------------------------------------------------------------
# 服务商预设（企划书 4.4 表格）
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str
    model: str

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "base_url": self.base_url, "model": self.model}


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o"),
    ProviderPreset("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
    ProviderPreset("qwen", "通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    ProviderPreset("kimi", "Kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ProviderPreset("zhipu", "智谱", "https://open.bigmodel.cn/api/paas/v4", "glm-4"),
    ProviderPreset("siliconflow", "硅基流动", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3"),
    ProviderPreset("ollama", "Ollama（本地）", "http://localhost:11434/v1", "qwen2.5:7b"),
    ProviderPreset("custom", "中转 / one-api（自定义）", "", ""),
)

PROVIDER_MAP = {p.key: p for p in PROVIDER_PRESETS}


def preset_for(base_url: str) -> ProviderPreset | None:
    """按 Base URL 反查预设服务商。"""
    normalized = (base_url or "").rstrip("/").lower()
    for preset in PROVIDER_PRESETS:
        if preset.base_url and preset.base_url.rstrip("/").lower() == normalized:
            return preset
    return None


# --------------------------------------------------------------------------
# 密钥存储
# --------------------------------------------------------------------------
class _DpapiError(Exception):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(protect: bool, data: bytes) -> bytes:
    """调用 Windows DPAPI 加解密（无需第三方依赖）。"""
    if not hasattr(ctypes, "windll"):
        raise _DpapiError("当前系统不支持 DPAPI")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    blob_in = _DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    args = [ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)]
    if not func(*args):
        raise _DpapiError("DPAPI 调用失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class SecretStore:
    """API Key 安全存储：keyring 优先，DPAPI 文件降级。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: str | None = None

    # -- 后端探测 --------------------------------------------------------
    @property
    def mode(self) -> str:
        """返回 keyring / dpapi / none，并缓存探测结果。"""
        if self._mode is None:
            self._mode = self._detect_mode()
        return self._mode

    def _detect_mode(self) -> str:
        try:
            import keyring  # noqa: PLC0415
            from keyring.backends import Windows  # noqa: F401, PLC0415

            backend = keyring.get_keyring()
            if "fail" not in type(backend).__module__.lower():
                return "keyring"
        except Exception:  # pragma: no cover - 依赖缺失或后端异常
            log.debug("keyring 不可用，降级到 DPAPI", exc_info=True)
        try:
            _dpapi(True, b"probe")
            return "dpapi"
        except Exception:
            return "none"

    def describe_mode(self) -> str:
        return {
            "keyring": "Windows 凭据管理器（keyring）",
            "dpapi": "DPAPI 加密文件（keyring 不可用时的降级方案）",
            "none": "未加密（系统不支持加密存储，密钥仅在本次会话保留）",
        }.get(self.mode, "未知")

    # -- 读写 ------------------------------------------------------------
    def get_api_key(self) -> str:
        with self._lock:
            if self.mode == "keyring":
                try:
                    import keyring  # noqa: PLC0415

                    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
                except Exception:
                    log.warning("keyring 读取失败，尝试降级存储", exc_info=True)
            path = paths.data_dir() / FALLBACK_SECRET_FILE
            if not path.exists():
                return ""
            try:
                raw = path.read_bytes()
                if self.mode == "dpapi":
                    raw = _dpapi(False, raw)
                return raw.decode("utf-8")
            except Exception:
                log.warning("密钥文件读取失败", exc_info=True)
                return ""

    def set_api_key(self, value: str) -> bool:
        """保存密钥。返回 True 表示已加密落盘，False 表示系统不支持加密。"""
        value = value or ""
        with self._lock:
            if not value:
                self.clear_api_key()
                return True
            if self.mode == "keyring":
                try:
                    import keyring  # noqa: PLC0415

                    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, value)
                    self._remove_fallback()
                    return True
                except Exception:
                    log.warning("keyring 写入失败，降级到 DPAPI", exc_info=True)
            if self.mode == "dpapi":
                (paths.data_dir() / FALLBACK_SECRET_FILE).write_bytes(_dpapi(True, value.encode("utf-8")))
                return True
            return False

    def clear_api_key(self) -> None:
        with self._lock:
            if self.mode == "keyring":
                try:
                    import keyring  # noqa: PLC0415

                    keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
                except Exception:
                    pass
            self._remove_fallback()

    def _remove_fallback(self) -> None:
        path = paths.data_dir() / FALLBACK_SECRET_FILE
        if path.exists():
            try:
                path.write_bytes(b"")
                path.unlink(missing_ok=True)
            except Exception:
                log.debug("清理降级密钥文件失败", exc_info=True)


secrets = SecretStore()


# --------------------------------------------------------------------------
# 配置模型
# --------------------------------------------------------------------------
@dataclass
class LLMConfig:
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 90
    retries: int = 2  # 失败自动重试次数（企划书 4.3）
    embedding_model: str = ""  # 留空则降级为关键词检索（企划书 4.4）

    def apply_preset(self, key: str) -> None:
        preset = PROVIDER_MAP.get(key)
        if not preset:
            return
        self.provider = key
        if preset.base_url:
            self.base_url = preset.base_url
        if preset.model:
            self.model = preset.model


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    theme: str = "light"  # light | dark
    onboarding_done: bool = False
    hotlist_cache_ttl: int = 300  # 秒，企划书要求 5~10 分钟
    hotlist_platforms: list[str] = field(
        default_factory=lambda: ["weibo", "douyin", "zhihu", "bilibili", "baidu", "toutiao"]
    )
    publish_platforms: list[str] = field(default_factory=lambda: ["xiaohongshu", "douyin", "zhihu", "bilibili"])
    embedding_model: str = ""  # 留空则使用关键词检索降级（企划书 4.4）
    analytics_auto_sync: bool = True
    minimize_to_tray: bool = False
    #: 窗口最大化 / 还原 / 最小化的过渡动画。
    #: Qt widgets 走 CPU 光栅化，大窗口下单帧重绘约 29ms、帧率上限 35~40fps；
    #: 与其给一个掉帧的动画，不如默认瞬间切换来得利落，需要时可在设置里开启。
    ui_animations: bool = False
    last_media_dir: str = ""

    # -- 序列化 ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        data = dict(data or {})
        llm_raw = data.pop("llm", None) or {}
        cfg = cls()
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key in known and value is not None:
                setattr(cfg, key, value)
        llm_known = {f.name for f in fields(LLMConfig)}
        for key, value in llm_raw.items():
            if key in llm_known and value is not None:
                setattr(cfg.llm, key, value)
        return cfg


class ConfigManager:
    """config.json 读写（线程安全，写入使用临时文件原子替换）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.config_path()
        self._lock = threading.RLock()
        self._config = self._load()

    def _load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return AppConfig.from_dict(raw)
        except Exception:
            log.exception("配置文件损坏，使用默认配置：%s", self.path)
            return AppConfig()

    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    def reload(self) -> AppConfig:
        with self._lock:
            self._config = self._load()
            return self._config

    def save(self) -> None:
        with self._lock:
            data = json.dumps(self._config.to_dict(), ensure_ascii=False, indent=2)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(self.path)

    def update(self, **kwargs: Any) -> AppConfig:
        """批量更新顶层字段并保存。"""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
            self.save()
            return self._config

    def update_llm(self, **kwargs: Any) -> AppConfig:
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config.llm, key):
                    setattr(self._config.llm, key, value)
            self.save()
            return self._config

    # -- 便捷访问 --------------------------------------------------------
    def api_key(self) -> str:
        return secrets.get_api_key()

    def ready(self) -> bool:
        """是否已具备调用 LLM 的最小配置。"""
        llm = self._config.llm
        return bool(llm.base_url and llm.model and self.api_key())


def _b64(data: bytes) -> str:  # pragma: no cover - 调试辅助
    return base64.b64encode(data).decode("ascii")


config_manager = ConfigManager()
