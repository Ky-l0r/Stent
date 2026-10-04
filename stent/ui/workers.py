"""后台任务线程封装。

所有耗时操作（拉热榜、LLM 创作、发布、指标同步）都在 QThread 中执行，
通过 Qt 信号回主线程更新 UI，保证界面不卡死（企划书 5.3「所有耗时操作有 loading 状态」）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from ..core.llm import LLMCancelled

log = logging.getLogger(__name__)

#: 已从宿主移除但底层线程仍未结束的 worker，保留引用以避免 QThread 析构崩溃
_ORPHANS: list["TaskWorker"] = []


def friendly_error(exc: BaseException) -> str:
    """把异常转成中文提示。"""
    if isinstance(exc, LLMCancelled):
        return "已中断"
    text = str(exc).strip()
    name = type(exc).__name__
    if not text:
        return name
    if name in {"ConnectionError", "ConnectTimeout", "ReadTimeout", "TimeoutError"}:
        return f"网络异常：{text}"
    return text if len(text) < 300 else text[:300] + "…"


class TaskWorker(QThread):
    """把任意可调用对象放到后台线程执行。

    ``fn`` 会收到 worker 自身，可用于上报进度与流式片段::

        TaskWorker(lambda wk: svc.fetch(on_progress=lambda msg, pct: wk.progress.emit(msg, pct)))
    """

    result = Signal(object)
    error = Signal(str)
    progress = Signal(str, int)
    stream = Signal(str, str)
    done = Signal()

    def __init__(self, fn: Callable[["TaskWorker"], Any], parent: Any = None, name: str = "") -> None:
        super().__init__(parent)
        self._fn = fn
        self._cancel_event = threading.Event()
        self.name = name or "task"

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def cancel(self) -> None:
        self._cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def emit_progress(self, message: str, percent: int = -1) -> None:
        try:
            self.progress.emit(message, int(percent))
        except RuntimeError:  # 对象已销毁
            pass

    def emit_stream(self, section: str, text: str) -> None:
        try:
            self.stream.emit(section, text)
        except RuntimeError:
            pass

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            output = self._fn(self)
            if not self._cancel_event.is_set():
                self.result.emit(output)
        except LLMCancelled:
            self.error.emit("已中断")
        except Exception as exc:  # noqa: BLE001 - 后台线程必须兜住所有异常
            log.exception("后台任务失败：%s", self.name)
            self.error.emit(friendly_error(exc))
        finally:
            self.done.emit()


class WorkerHost:
    """混入窗口/页面，统一管理后台任务的生命周期（防 GC、退出时中断）。"""

    def _init_workers(self) -> None:
        self._workers: list[TaskWorker] = []

    def run_task(
        self,
        fn: Callable[[TaskWorker], Any],
        *,
        on_result: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[str, int], None] | None = None,
        on_stream: Callable[[str, str], None] | None = None,
        on_done: Callable[[], None] | None = None,
        name: str = "task",
    ) -> TaskWorker:
        """启动后台任务并自动接线信号。"""
        # 注意：不设置 parent。QThread 若随宿主控件被 C++ 析构，而线程仍在运行，
        # 会直接终止进程；这里统一由 _workers 列表持有引用，并在 done 后 wait。
        worker = TaskWorker(fn, parent=None, name=name)
        if on_result:
            worker.result.connect(on_result)
        if on_error:
            worker.error.connect(on_error)
        if on_progress:
            worker.progress.connect(on_progress)
        if on_stream:
            worker.stream.connect(on_stream)
        if on_done:
            worker.done.connect(on_done)
        worker.done.connect(lambda w=worker: self._forget(w))
        self._workers.append(worker)
        worker.start()
        return worker

    def _forget(self, worker: TaskWorker) -> None:
        try:
            self._workers.remove(worker)
        except (ValueError, AttributeError):
            pass
        # 等线程真正退出再放手，否则 QThread 被析构时会终止进程
        if not worker.wait(6000):
            _ORPHANS.append(worker)

    def cancel_all(self) -> None:
        for worker in list(getattr(self, "_workers", [])):
            worker.cancel()

    def busy(self) -> bool:
        return any(w.isRunning() for w in getattr(self, "_workers", []))

    def wait_all(self, timeout_ms: int = 6000) -> None:
        for worker in list(getattr(self, "_workers", [])):
            try:
                if not worker.wait(timeout_ms):
                    _ORPHANS.append(worker)
            except Exception:  # pragma: no cover
                pass


def pending_workers() -> list["TaskWorker"]:
    """仍在运行的残留任务。"""
    return [worker for worker in _ORPHANS if worker.isRunning()]


def shutdown_workers(timeout_ms: int = 8000) -> bool:
    """等待全部残留后台任务结束。

    返回 True 表示已全部结束；False 表示仍有线程在跑——此时**不能**让解释器
    正常退出（QThread 在运行中被析构会直接终止进程），调用方应改用 ``os._exit``。
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    for worker in list(_ORPHANS):
        remain_ms = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            worker.wait(remain_ms)
        except Exception:  # pragma: no cover
            pass
    alive = [worker for worker in _ORPHANS if worker.isRunning()]
    _ORPHANS.clear()
    _ORPHANS.extend(alive)
    return not alive
