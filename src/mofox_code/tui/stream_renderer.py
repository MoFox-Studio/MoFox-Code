"""增量流式 Markdown 渲染器。"""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from rich.console import Console, RenderableType
from rich.markdown import Markdown
from .themes import Theme


class StreamMarkdownRenderer:
    """增量 Markdown 渲染器。

    同时兼容两种服务端流格式：
    1. 每个 chunk 只包含新增文本
    2. 每个 chunk 都是"到目前为止的完整文本"

    自适应节流：初始 50ms 保证响应速度，持续流式超过 _RAMP_THRESHOLD
    后逐步放宽间隔（最高 _MAX_INTERVAL），降低长时间工作时的 CPU 开销。
    """

    _INIT_INTERVAL: float = 0.05   # 50ms — 初始节流
    _MAX_INTERVAL: float = 0.20    # 200ms — 持续流式最大间隔
    _RAMP_THRESHOLD: float = 3.0   # 连续流式 3 秒后逐步放宽节流间隔，降低 CPU 开销
    _RAMP_FACTOR: float = 1.5      # 每次 ramp 乘数

    def __init__(
        self,
        console: Console,
        theme: Theme,
        output: Callable[[RenderableType], None] | None = None,
    ) -> None:
        self._console = console
        self._theme = theme
        self._output = output
        self._content: str = ""
        self._last_emit_time: float = 0.0
        self._throttle_interval: float = self._INIT_INTERVAL
        self._dirty: bool = False
        self._last_emitted_content: str = ""
        self._stream_start_time: float = 0.0
        self._flush_scheduled: bool = False

    def set_output(self, output: Callable[[RenderableType], None]) -> None:
        """设置 output 回调。"""
        self._output = output

    def _emit_markdown(self) -> None:
        """将当前内容渲染为单个 Markdown 并输出。"""
        if not self._content:
            return
        md = Markdown(self._content, style=self._theme.agent_text)
        if self._output:
            self._output(md)
        else:
            self._console.print(md)
        self._last_emitted_content = self._content
        self._last_emit_time = time.monotonic()

    def _adapt_throttle(self) -> None:
        """根据持续流式时长自适应调整节流间隔。"""
        if self._stream_start_time == 0.0:
            self._stream_start_time = time.monotonic()
            return
        elapsed = time.monotonic() - self._stream_start_time
        if elapsed > self._RAMP_THRESHOLD:
            self._throttle_interval = min(
                self._throttle_interval * self._RAMP_FACTOR,
                self._MAX_INTERVAL,
            )

    def _schedule_flush(self) -> None:
        """安排一次延迟 flush，确保 dirty 内容不会因节流而长期不显示。"""
        if self._flush_scheduled:
            return
        self._flush_scheduled = True
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(self._throttle_interval, self._deferred_flush)
        except RuntimeError:
            pass

    def _deferred_flush(self) -> None:
        """延迟 flush：如果仍有未渲染的内容，执行一次渲染。"""
        self._flush_scheduled = False
        if self._dirty and self._content != self._last_emitted_content:
            self._emit_markdown()
            self._dirty = False

    def feed(self, chunk: str) -> None:
        """接收一个流式 chunk。"""
        if not chunk:
            return

        # 累积内容
        if chunk.startswith(self._content):
            self._content = chunk
        else:
            self._content += chunk

        now = time.monotonic()
        # 第一块内容立即渲染
        if self._last_emit_time == 0.0:
            self._stream_start_time = now
            self._emit_markdown()
            return

        if now - self._last_emit_time >= self._throttle_interval:
            self._emit_markdown()
            self._dirty = False
            self._adapt_throttle()
        else:
            self._dirty = True
            self._schedule_flush()

    def finalize(self) -> None:
        """流结束，强制渲染所有剩余缓冲并重置节流。"""
        if self._content and self._content != self._last_emitted_content:
            self._emit_markdown()
        self._dirty = False
        # 重置节流参数，下一轮流式从初始间隔开始
        self._throttle_interval = self._INIT_INTERVAL
        self._stream_start_time = 0.0
