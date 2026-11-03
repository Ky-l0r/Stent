"""HTTP 服务本体：路由、鉴权、静态资源、SSE。

刻意用标准库 ``http.server`` 而不是 FastAPI/Flask：

- 本地单人使用，QPS 以「每分钟」计，线程池完全够；
- 少一个运行时依赖，打包体积与冷启动都受益（项目对这两件事一直很计较）；
- 路由只有十几条，手写表比引入框架更透明。

代价是没有自动的参数校验与 OpenAPI，这由 ``handlers.py`` 里显式的中文报错补上。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .. import paths

log = logging.getLogger(__name__)

#: 请求体上限：本地接口最大的载荷是「正文 + 标签」，8MB 足够，也挡住误发大文件。
MAX_BODY_BYTES = 8 * 1024 * 1024

#: Windows 注册表常把 .js 映射成 text/plain，会导致 ES Module 被浏览器拒绝加载。
_FORCED_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}


class ApiError(Exception):
    """业务可预期的失败：翻译成 HTTP 状态码 + 中文说明返回给前端。"""

    def __init__(self, message: str, *, status: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


@dataclass
class Response:
    """普通 JSON 响应。"""

    body: Any = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class StreamResponse:
    """SSE 响应：``events`` 产出 ``(事件名, 可序列化数据)``。

    生成器被关闭（客户端断开）时，``close()`` 会触发 ``GeneratorExit``，
    处理器正是在 ``finally`` 里置位取消信号——这是「中断真的停掉上游」的关键。
    """

    events: Iterator[tuple[str, Any]]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Request:
    """一次 /api 请求的只读视图。"""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: Any
    server: ApiServer
    body: bytes = b""

    def json(self) -> dict[str, Any]:
        """解析 JSON 对象请求体；空体返回空字典。"""
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise ApiError("请求体必须是 JSON 对象")
        return data

    def q(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return values[0] if values else default

    def flag(self, name: str) -> bool:
        return self.q(name).lower() in {"1", "true", "yes", "on"}


Handler = Callable[[Request], Response | StreamResponse]


class ApiServer:
    """本机 API 服务：注册路由、启动/停止、生成访问 URL。"""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        #: 启动时生成，注入页面；本机其它进程拿不到，也就读不到接口。
        self.token = secrets.token_urlsafe(32)
        self._routes: dict[tuple[str, str], Handler] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- 路由 ------------------------------------------------------------
    def route(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def decorator(func: Handler) -> Handler:
            self._routes[(method.upper(), path)] = func
            return func

        return decorator

    def resolve(self, method: str, path: str) -> Handler | None:
        return self._routes.get((method.upper(), path))

    @property
    def routes(self) -> list[tuple[str, str]]:
        return sorted(self._routes)

    # -- 生命周期 --------------------------------------------------------
    def start(self) -> str:
        """启动服务并返回基地址（非阻塞）。"""
        if self._httpd is not None:
            return self.url

        httpd = ThreadingHTTPServer((self.host, self.port), _RequestHandler)
        httpd.daemon_threads = True
        httpd.api = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, name="stent-api", daemon=True)
        self._thread.start()
        log.info("接口服务已启动：%s（%d 条路由）", self.url, len(self._routes))
        return self.url

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        log.info("接口服务已停止")

    @property
    def url(self) -> str:
        port = self._httpd.server_address[1] if self._httpd is not None else self.port
        return f"http://{self.host}:{port}/"

    # -- 供处理器使用 ----------------------------------------------------
    def static_root(self) -> Path:
        return paths.web_dir()

    def serve_file(self, relative: str) -> tuple[bytes, str] | None:
        """读取静态文件；返回 ``(内容, MIME)``，不存在或越界返回 ``None``。"""
        root = self.static_root()
        target = (root / unquote(relative)).resolve()
        try:
            # 防目录穿越：解析后必须仍在静态目录内
            target.relative_to(root.resolve())
        except ValueError:
            return None
        if not target.is_file():
            return None
        try:
            data = target.read_bytes()
        except OSError:
            return None
        if target.suffix == ".html":
            # Token 只出现在返回给窗口的 HTML 里，不落盘
            data = data.replace(b"__STENT_TOKEN__", self.token.encode("ascii"))
        mime = _FORCED_TYPES.get(target.suffix.lower())
        if mime is None:
            guessed, _ = mimetypes.guess_type(target.name)
            mime = guessed or "application/octet-stream"
        return data, mime


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


class _RequestHandler(BaseHTTPRequestHandler):
    """单请求处理：鉴权 → 取路由 → 调处理器 → 写回。"""

    server_version = "StentApi/1.0"
    sys_version = ""

    # http.server 默认把访问日志打到 stderr；这里收进 logging，避免污染控制台。
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 基类签名
        log.debug("%s %s", self.address_string(), fmt % args)

    @property
    def api(self) -> ApiServer:
        return self.server.api  # type: ignore[attr-defined]

    # -- 动词 ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - 基类命名
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    # -- 主流程 ----------------------------------------------------------
    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if not path.startswith("/api"):
            self._serve_static(path)
            return

        if not self._authorized():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "forbidden", "message": "缺少或错误的访问令牌"}},
            )
            return

        length = 0
        raw_length = self.headers.get("Content-Length")
        if raw_length:
            try:
                length = int(raw_length)
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": {"code": "bad_request", "message": "Content-Length 非法"}},
                )
                return
        if length > MAX_BODY_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": {"code": "too_large", "message": "请求体过大（上限 8MB）"}},
            )
            return
        body = self.rfile.read(length) if length else b""

        request = Request(
            method=method,
            path=path,
            query=query,
            headers=self.headers,
            server=self.api,
            body=body,
        )

        handler = self.api.resolve(method, path)
        if handler is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "not_found", "message": f"没有这个接口：{method} {path}"}},
            )
            return

        try:
            result = handler(request)
        except ApiError as exc:
            self._send_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
            return
        except Exception:  # noqa: BLE001 - 兜底：任何未预期异常都要变成可读错误
            log.exception("接口执行失败：%s %s", method, path)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": {
                        "code": "internal_error",
                        "message": "服务端处理失败，详情见日志（_data/logs/stent.log）",
                    }
                },
            )
            return

        if isinstance(result, StreamResponse):
            self._send_stream(result)
        else:
            self._send_json(result.status, result.body, result.headers)

    def _authorized(self) -> bool:
        if not _is_loopback(self.api.host):
            # 非回环绑定属于配置错误，宁可拒绝也不要把接口暴露出去
            return False
        token = self.headers.get("X-Stent-Token") or ""
        return secrets.compare_digest(token, self.api.token)

    # -- 写回 ------------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
        found = self.api.serve_file(relative)
        if found is None:
            # 前端是 hash 路由，未知路径一律回 index.html
            found = self.api.serve_file("index.html")
        if found is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "not_found", "message": "静态资源缺失，请重新安装"}},
            )
            return
        data, mime = found
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 窗口刷新 / 切换页面时浏览器会主动掐断旧连接，这不是错误
            log.debug("静态资源写回时客户端断开：%s", path)

    def _send_json(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(payload)
        # ConnectionAbortedError（WinError 10053）在窗口关闭时必现：
        # 浏览器已断开，socketserver 会把它当未处理异常打一整段栈到 stderr。
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log.debug("客户端在写回 JSON 时断开")

    def _send_stream(self, response: StreamResponse) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()

        try:
            for event, data in response.events:
                frame = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 用户点了「中断」或关掉了窗口：让生成器的 finally 去置位取消信号
            log.info("流式响应被客户端中断")
        finally:
            close = getattr(response.events, "close", None)
            if callable(close):
                close()


def free_port() -> int:
    """探一个空闲端口（仅用于测试与固定端口失败时的兜底）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
