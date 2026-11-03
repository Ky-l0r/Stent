"""本机接口自检：起一个真服务，用 HTTP 打一遍端点（不需要窗口、不联网）。

运行::

    python tests/selftest_api.py

为什么不用 pytest：仓库里的自检脚本都是可直接 `python` 跑的形态，
CI 也是这么跑的；这里保持同一套习惯，不额外引入测试框架。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
from pathlib import Path

# 必须在 import stent 之前设置：配置与数据库要落到临时目录
_TMP = tempfile.mkdtemp(prefix="stent-api-")
os.environ["STENT_DATA_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 输出全是中文：英文版 Windows 的控制台编码是 cp1252，直接 print 会中断自检
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import stent.config as config_module  # noqa: E402
from stent.api import build_app  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []

BASE = ""
TOKEN = ""


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def call(method: str, path: str, payload: dict | None = None, *, token: bool = True):
    """返回 (状态码, 解析后的 JSON 或原始文本)。"""
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Stent-Token"] = TOKEN
    request = urllib.request.Request(
        BASE.rstrip("/") + path, data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    text = raw.decode("utf-8", "replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def main() -> int:
    global BASE, TOKEN

    # 自检不碰真实 LLM：把密钥读口堵死（keyring 是全局的，不能假设测试机没配过）
    config_module.config_manager.api_key = lambda: ""  # type: ignore[method-assign]

    app = build_app()
    BASE = app.start()
    TOKEN = app.token
    print(f"接口服务：{BASE}（{len(app.routes)} 条路由）")

    try:
        section("健康检查与鉴权")
        status, payload = call("GET", "/api/health")
        check("health 返回 200", status == 200, str(status))
        check("health 带版本号", bool(isinstance(payload, dict) and payload.get("version")))

        status, payload = call("GET", "/api/health", token=False)
        check("缺 token 被拒绝（403）", status == 403, str(status))

        status, payload = call("GET", "/api/does-not-exist")
        check("未知接口返回 404", status == 404, str(status))

        section("元信息")
        status, payload = call("GET", "/api/meta")
        check("meta 返回 200", status == 200, str(status))
        check("meta 含创作平台", len(payload.get("creation_platforms") or []) >= 4)
        check("meta 含发布平台", len(payload.get("publish_platforms") or []) >= 4)
        check(
            "发布平台带 limits", all("limits" in p for p in payload.get("publish_platforms") or [])
        )

        section("静态资源")
        status, payload = call("GET", "/")
        check("首页可访问", status == 200, str(status))
        check("首页注入了本次 token", isinstance(payload, str) and TOKEN in payload)
        check("首页不是占位符", isinstance(payload, str) and "__STENT_TOKEN__" not in payload)
        status, payload = call("GET", "/assets/app.css")
        check("样式表可访问", status == 200, str(status))
        status, payload = call("GET", "/assets/pages/create.js")
        check("页面脚本可访问", status == 200, str(status))

        section("设置")
        status, payload = call("GET", "/api/settings")
        check("读取设置 200", status == 200, str(status))
        check("设置不含密钥明文", "api_key" not in (payload or {}))
        check("设置标明密钥是否已存", "key_set" in (payload or {}))

        status, payload = call(
            "PUT", "/api/settings", {"theme": "dark", "llm": {"temperature": 0.5}}
        )
        check("保存设置 200", status == 200, str(status))
        check("主题已写入", payload.get("theme") == "dark", str(payload.get("theme")))
        check("温度已写入", abs(float(payload["llm"]["temperature"]) - 0.5) < 1e-6)

        status, payload = call("PUT", "/api/settings", {"llm": {"temperature": "abc"}})
        check("非法数值被拒绝（400）", status == 400, str(status))
        check(
            "错误信息是中文",
            isinstance(payload, dict) and "必须是数字" in json.dumps(payload, ensure_ascii=False),
        )

        status, payload = call("POST", "/api/settings/test")
        check("未配置模型时测试返回 409", status == 409, str(status))
        check("提示可操作", "API Base URL" in json.dumps(payload, ensure_ascii=False))

        section("账号画像")
        status, payload = call("GET", "/api/profile")
        check("读取画像 200", status == 200, str(status))
        check("六维定义齐全", len(payload.get("dimensions") or []) == 6)

        status, payload = call(
            "PUT", "/api/profile", {"positioning": "测试定位", "style": "口语化"}
        )
        check("保存画像 200", status == 200, str(status))
        check("定位已写入", payload["profile"]["positioning"] == "测试定位")
        check("完整度提升", payload.get("completeness", 0) > 0, str(payload.get("completeness")))

        status, payload = call("POST", "/api/profile/memory", {"text": "标题带数字点击更高"})
        check("追加记忆 200", status == 200, str(status))
        check(
            "记忆已追加",
            any("数字" in json.dumps(m, ensure_ascii=False) for m in payload["profile"]["memory"]),
        )

        status, payload = call("DELETE", "/api/profile/memory")
        check("清空记忆 200", status == 200, str(status))
        check("记忆已清空", payload["profile"]["memory"] == [])

        section("发布前检查")
        status, payload = call(
            "POST", "/api/publish/prepare", {"platform": "xiaohongshu", "body": "短"}
        )
        check("准备接口 200", status == 200, str(status))
        check("返回预览文本", bool((payload or {}).get("preview")))
        check("返回检查结果", isinstance((payload or {}).get("checks"), dict))
        check("检查项可序列化", isinstance((payload or {}).get("checks", {}).get("items"), list))
        check("短正文被判为问题", not (payload or {}).get("checks", {}).get("passed", True))
        check("返回平台限制", bool((payload or {}).get("limits")))

        status, payload = call(
            "POST", "/api/publish/prepare", {"platform": "xiaohongshu", "body": ""}
        )
        check("空正文被拒绝（400）", status == 400, str(status))

        section("创作（未配置模型时的边界）")
        status, payload = call("POST", "/api/create/stream", {"topic": "测试主题"})
        check("未配置模型返回 409", status == 409, str(status))
        status, payload = call("POST", "/api/create/stream", {"topic": ""})
        check("空主题被拒绝（400）", status == 400, str(status))
        status, payload = call("POST", "/api/create/titles", {"topic": "t", "body": "正文"})
        check("换标题未配置模型返回 409", status == 409, str(status))

        section("请求体校验")
        request = urllib.request.Request(
            BASE.rstrip("/") + "/api/settings",
            data=b"not-json",
            method="PUT",
            headers={"Content-Type": "application/json", "X-Stent-Token": TOKEN},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        check("非法 JSON 返回 400", status == 400, str(status))

    finally:
        app.stop()

    print(f"\n===== 接口自检结果：{PASS} 通过 / {FAIL} 失败 =====")
    if FAILURES:
        print("失败项：")
        for item in FAILURES:
            print("  -", item)
    print(f"临时数据目录：{_TMP}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2) from None
