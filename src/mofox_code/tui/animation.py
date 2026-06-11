"""TUI 动画效果管理器。

提供 braille spinner、thinking 占位点、进度条流光等动画效果。
通过后台 asyncio task 以 80ms 间隔驱动帧计数，避免阻塞主循环。
"""

from __future__ import annotations

import asyncio
from typing import Callable

# Braille spinner 字符序列（10 帧循环）
_SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class AnimationManager:
    """动画效果管理器。

    维护一个递增帧计数器，通过后台 asyncio task 周期性 tick，
    每 tick 调用 on_tick 回调触发界面刷新。

    工具方法提供纯函数式的帧→字符映射，不依赖实例状态。
    """

    _TICK_INTERVAL = 0.08  # 80ms ≈ 12.5 fps

    def __init__(self, on_tick: Callable[[], None]) -> None:
        """初始化动画管理器。

        Args:
            on_tick: 每帧 tick 后调用的回调（通常传入 layout._invalidate）。
        """
        self._on_tick = on_tick
        self._frame: int = 0
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False

    # ─── 生命周期 ───

    def start(self) -> None:
        """启动动画 tick 循环。幂等：已运行时忽略。"""
        if self._running:
            return
        self._running = True
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._tick_loop())

    def stop(self) -> None:
        """停止动画 tick 循环。幂等：未运行时忽略。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _tick_loop(self) -> None:
        """后台 tick 循环。"""
        while self._running:
            await asyncio.sleep(self._TICK_INTERVAL)
            if not self._running:
                break
            self._frame += 1
            self._on_tick()

    # ─── 帧计数访问 ───

    @property
    def frame(self) -> int:
        """当前帧编号（只读）。"""
        return self._frame

    # ─── 动画工具方法 ───

    @staticmethod
    def spinner_char(frame: int) -> str:
        """braille spinner 字符：⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ 循环。

        Args:
            frame: 当前帧编号（任意整数）。

        Returns:
            单个 braille 字符。
        """
        return _SPINNER_CHARS[frame % len(_SPINNER_CHARS)]

    @staticmethod
    def dots(frame: int, count: int = 3) -> str:
        """循环点动画：'.', '..', '...', '' 循环。

        Args:
            frame: 当前帧编号。
            count: 最大点数（默认 3）。

        Returns:
            点字符串。
        """
        # 每 3 帧切换一次，减缓动画速度
        phase = (frame // 2) % (count + 1)
        return "." * phase

    @staticmethod
    def shimmer_offset(frame: int, bar_width: int) -> int:
        """进度条流光偏移位置。

        Args:
            frame: 当前帧编号。
            bar_width: 进度条总宽度（字符数）。

        Returns:
            流光亮斑的起始偏移（0 ~ bar_width-1 循环）。
        """
        if bar_width <= 0:
            return 0
        # 每 2 帧移动 1 格，减慢流光速度
        return (frame // 2) % max(bar_width, 1)
