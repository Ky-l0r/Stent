"""平台适配器统一接口。

所有平台（小红书 / 抖音 / 知乎 / B 站）实现同一套方法，
发布中心与数据分析模块只依赖本接口，不感知平台细节。

风控约束（企划书 3.2 模块三 / 第七章）：
- publish() 必须由「预览 + 人工确认」后的用户动作触发；
- 不提供全自动发布开关；
- dry_run=True 时只走到「填写完成」这一步，绝不点击最终发布按钮。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LoginState(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass
class PlatformLimits:
    """发布前检查依据（企划书 3.2 模块三「检查项」）。"""

    title_max: int = 20
    body_max: int = 1000
    body_min: int = 1
    tags_max: int = 10
    requires_media: bool = False
    media_max: int = 0
    media_kinds: tuple[str, ...] = ("image",)
    supports_video: bool = False
    notes: str = ""


@dataclass
class PublishPayload:
    """送入平台的一份内容。"""

    title: str = ""
    body: str = ""
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    topic: str = ""
    media: list[str] = field(default_factory=list)  # 本地文件绝对路径
    extra: dict[str, Any] = field(default_factory=dict)

    def full_text(self) -> str:
        """正文 + 话题标签，用于字数检查与预览。"""
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in self.tags if t)
        return f"{self.body}\n\n{tags}".strip() if tags else self.body


@dataclass
class PublishResult:
    ok: bool
    url: str = ""
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricSnapshot:
    """单篇内容的表现数据（企划书 3.2 模块四「展示」）。"""

    views: int = 0
    likes: int = 0
    comments: int = 0
    collects: int = 0
    shares: int = 0
    #: 作品标题（抓取时顺带带回来）。
    #: 「粘贴链接登记」时我们只知道链接，标题只能靠这次抓取补上，
    #: 否则内容排行里显示的会是一串 URL。
    title: str = ""
    #: 该平台是否公开播放量。小红书网页版不公开，此时 views 恒为 0，
    #: UI 据此显示「—」而不是误导性的「0」。
    views_public: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "collects": self.collects,
            "shares": self.shares,
            "raw": self.raw,
        }


@dataclass
class AccountPost:
    """账号下的一篇已发布作品（账号级发现的结果）。

    ``metrics`` 可能为空：有的平台在列表页就带数据，有的需要再逐条拉一次。
    """

    url: str
    title: str = ""
    published_at: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    platform_id: str = ""


class PlatformError(RuntimeError):
    """平台操作失败。"""


class LoginRequired(PlatformError):
    """需要先完成登录。"""


class PlatformAdapter(ABC):
    """平台适配器基类。"""

    key: str = ""
    label: str = ""
    home_url: str = ""
    publish_url: str = ""
    #: 是否需要用户提供本地媒体文件
    needs_media: bool = False
    #: 是否已实现自动化发布（False 时发布中心只提供「人工发布」引导）
    supports_auto_publish: bool = True
    #: 指标拉取依赖登录态的场景说明
    metrics_hint: str = ""
    #: 该平台是否对外公开「播放量」。
    #: 小红书网页版从不公开，此时 views 恒为 0；UI 据此显示「—」，
    #: 免得用户把「平台不给」误读成「这篇真的 0 播放」。
    exposes_views: bool = True
    #: 是否支持「从账号导入作品列表」。
    #: 显式声明而不是靠「有没有重写方法」推断——不支持的平台也要能明确说「不支持」，
    #: 否则用户会看到一个点了没反应的按钮。
    supports_account_import: bool = False

    @property
    def limits(self) -> PlatformLimits:
        return PlatformLimits()

    # -- 登录 ------------------------------------------------------------
    @abstractmethod
    def check_login(self, *, headless: bool = True) -> tuple[bool, str]:
        """检测登录态。返回 (是否已登录, 说明)。"""

    @abstractmethod
    def login(self, *, timeout_sec: int = 300) -> tuple[bool, str]:
        """打开有头浏览器，引导用户扫码/输密码完成登录，并持久化登录态。"""

    def logout(self) -> tuple[bool, str]:
        """清除本地登录态。"""
        return False, "该平台暂不支持一键退出，请手动删除登录态目录"

    # -- 发布 ------------------------------------------------------------
    @abstractmethod
    def publish(
        self,
        payload: PublishPayload,
        *,
        headless: bool = False,
        dry_run: bool = False,
        timeout_sec: int = 300,
        on_progress: Any = None,
    ) -> PublishResult:
        """执行发布。dry_run=True 时只填写表单不提交（人工确认前的预览）。"""

    # -- 数据 ------------------------------------------------------------
    @abstractmethod
    def fetch_metrics(self, post_url: str, *, headless: bool = True) -> MetricSnapshot | None:
        """拉取单篇内容的指标；失败返回 None。"""

    def fetch_account_metrics(self, *, headless: bool = True) -> dict[str, Any] | None:
        """拉取账号级指标（粉丝数等），可选实现。"""
        return None

    def list_account_posts(
        self, *, headless: bool = True, limit: int = 50
    ) -> list[AccountPost]:
        """列出账号下已发布的作品，可选实现。

        实现后，用户不必再逐条粘贴链接——数据分析页可以「从账号导入作品」，
        一次性把历史作品全部纳入指标同步。

        未实现时返回空列表（调用方据此提示「该平台暂不支持自动导入」）。
        """
        return []

    # -- 辅助 ------------------------------------------------------------
    def profile_dir(self) -> str:
        """该平台持久化登录态目录。"""
        from .. import paths  # 局部导入避免循环依赖

        path = paths.browser_dir() / self.key
        path.mkdir(parents=True, exist_ok=True)
        return str(path)


def emit(on_progress: Any, message: str, percent: int | None = None) -> None:
    """向 UI 汇报进度（on_progress 允许为 None 或任意可调用对象）。"""
    if not callable(on_progress):
        return
    try:
        on_progress(message, percent)
    except TypeError:
        try:
            on_progress(message)
        except Exception:  # pragma: no cover - 进度回调不应影响主流程
            pass
    except Exception:  # pragma: no cover
        pass
