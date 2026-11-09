"""后台任务注册表：长任务（发布、指标同步、归因）在这里跑，HTTP 只查进度。

为什么需要它：发布要开浏览器、同步指标要逐条访问平台页面，单个请求可能跑几分钟。
把这类活放进工作线程，接口立刻返回一个 ``job_id``，前端按 1 秒一次的节奏轮询
``GET /api/jobs?id=...`` —— 与 ``docs/web-architecture.md`` 里「长任务走轮询」的约定一致。

这里**不碰业务**：``fn(job)`` 由 handlers 提供，任务表只负责状态、进度、取消与回收。
"""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

#: 终态：到了这些状态就不会再变，前端可以停止轮询。
TERMINAL = frozenset({"done", "error", "cancelled"})

#: 最多保留多少个任务（含运行中的）；超出的先丢最老的终态任务。
MAX_JOBS = 48

#: 终态任务保留时长（秒）。超过就回收，避免长时间挂着窗口把内存攒满。
KEEP_SECONDS = 2 * 60 * 60


@dataclass
class Job:
    """一次后台任务的运行状态。"""

    id: str
    kind: str
    label: str
    status: str = "running"
    message: str = "处理中…"
    percent: int = -1
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)

    # -- 供工作线程调用 --------------------------------------------------
    def progress(self, message: str, percent: int = -1) -> None:
        """更新进度；百分比只前进不后退（多段任务共用一个进度条时不会跳）。"""
        if message:
            self.message = str(message)
        if percent is not None and int(percent) >= 0:
            self.percent = max(self.percent, min(100, int(percent)))

    def cancelled(self) -> bool:
        return self.cancel.is_set()

    # -- 供接口序列化 ----------------------------------------------------
    def snapshot(self, *, with_result: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "message": self.message,
            "percent": self.percent,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "elapsed_ms": int(((self.finished_at or time.time()) - self.created_at) * 1000),
            "terminal": self.status in TERMINAL,
        }
        if with_result and self.status == "done":
            data["result"] = _plain(self.result)
        return data


def _plain(value: Any) -> Any:
    """把 dataclass / 时间 / 路径等转成可 JSON 化的结构（与 handlers._dict 同口径）。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


class JobRegistry:
    """线程安全的任务表。"""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    # -- 生命周期 --------------------------------------------------------
    def start(
        self,
        kind: str,
        label: str,
        fn: Callable[[Job], Any],
        *,
        message: str = "处理中…",
    ) -> Job:
        """起一个后台任务并立即返回（不等待）。

        ``fn`` 抛出的任何异常都会落到 ``status="error"`` 与中文 ``error`` 上，
        调用方不需要再套 try/except——前端只认任务状态。
        """
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label, message=message)
        with self._lock:
            self._prune_locked()
            self._jobs[job.id] = job

        def runner() -> None:
            try:
                result = fn(job)
            except Exception as exc:  # noqa: BLE001 - 任务边界：失败要变成可读状态
                job.status = "cancelled" if job.cancelled() else "error"
                job.error = str(exc) or type(exc).__name__
                job.message = job.error
            else:
                if job.cancelled():
                    job.status = "cancelled"
                    job.message = job.message or "已取消"
                else:
                    job.status = "done"
                    job.result = result
                    job.percent = 100
                    job.message = job.message or "完成"
            finally:
                job.finished_at = time.time()

        threading.Thread(target=runner, name=f"stent-job-{kind}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(str(job_id or ""))

    def cancel(self, job_id: str) -> Job | None:
        """请求取消；真正的收尾由工作线程在下一个检查点完成。"""
        job = self.get(job_id)
        if job is None:
            return None
        if job.status not in TERMINAL:
            job.cancel.set()
            job.message = "正在取消…"
        return job

    def running(self, kind: str | None = None) -> list[Job]:
        with self._lock:
            return [
                job
                for job in self._jobs.values()
                if job.status not in TERMINAL and (kind is None or job.kind == kind)
            ]

    # -- 回收 ------------------------------------------------------------
    def _prune_locked(self) -> None:
        now = time.time()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in TERMINAL and now - job.finished_at > KEEP_SECONDS
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)
        if len(self._jobs) <= MAX_JOBS:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.status in TERMINAL),
            key=lambda job: job.finished_at,
        )
        for job in finished[: max(0, len(self._jobs) - MAX_JOBS)]:
            self._jobs.pop(job.id, None)


#: 进程内单例：接口层与测试共用同一张表。
jobs = JobRegistry()
