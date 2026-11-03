"""本机 HTTP 接口层：把 ``services`` 暴露成 JSON / SSE。

设计约定见 ``docs/web-architecture.md``。三条底线：

1. **只绑回环地址**，端口交给系统分配（``port=0``），避免与用户其它程序抢端口；
2. 每个 ``/api/*`` 请求都要带启动时生成的 Token，挡住本机其它进程顺手读配置与草稿；
3. 这一层只做协议转换与参数校验，**不写业务规则**——业务逻辑一行都不许搬进来。

模块划分：
- ``server.py``：HTTP 服务、路由表、静态资源、SSE 写回；
- ``handlers.py``：各端点的实现，调用 ``services`` 并序列化。

对外入口是 :func:`stent.api.build_app`，返回一个已注册好路由的 :class:`ApiServer`。
"""

from __future__ import annotations

from .handlers import build_app
from .server import ApiError, ApiServer, Request, Response, StreamResponse

__all__ = [
    "ApiError",
    "ApiServer",
    "Request",
    "Response",
    "StreamResponse",
    "build_app",
]
