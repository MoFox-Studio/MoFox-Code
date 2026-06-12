"""全屏 TUI 布局管理器。

使用 prompt_toolkit 驱动 header/body/composer 三分区布局，
同时保留 Rich renderable 作为消息内容来源。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from io import StringIO

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from rich.console import Console, RenderableType
from rich.text import Text

from .input_handler import CommandCompleter
from .themes import Theme


_INTERRUPT_SENTINEL = "__mofox_interrupt__"
_EOF_SENTINEL = "__mofox_eof__"
_REFRESH_INPUT_SENTINEL = "__mofox_refresh_input__"

# _invalidate 最小间隔 (秒)，防止重绘风暴阻塞事件循环
_INVALIDATE_MIN_INTERVAL = 0.03  # ~30ms


class InputRefreshRequested(Exception):
    """请求当前输入流程立即返回，由上层决定如何切换输入模式。"""


class TUILayoutManager:
    """全屏 TUI 布局管理器。

    管理 header / body / footer 三分区布局，
    使用 prompt_toolkit 实时刷新屏幕内容与输入框。
    """

    MAX_BODY_ITEMS = 1000

    def __init__(self, console: Console, theme: Theme) -> None:
        self._console = console
        self._theme = theme
        self._body_items: list[tuple[RenderableType, str]] = []
        self._last_render_width: int = 0
        # 动画槽位：需要每帧重新渲染的 body 条目索引 → 工厂函数
        self._animated_body_indices: set[int] = set()
        self._body_anim_factories: dict[int, Callable[[], RenderableType]] = {}
        # 命名槽位：字符串名称 → body 物理索引，裁剪/插入时自动修正
        self._slots: dict[str, int] = {}
        self._application: Application | None = None
        self._application_task: asyncio.Task[None] | None = None
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._input_buffer = Buffer(
            history=InMemoryHistory(),
            completer=CommandCompleter(),
            complete_while_typing=True,
            enable_history_search=True,
            multiline=True,
        )
        self._body_scroll = 0
        self._follow_output = True
        self._footer_spinner_visible: bool = False
        self._footer_spinner_source: str = "agent"
        self._footer_hint = "Enter 发送 | Ctrl+J 换行 | Ctrl+C 中断 | /help"
        self._header_content: RenderableType = Text("")
        self._header_control = FormattedTextControl(self._get_header_text)
        # 动态 header 数据（由 build_header 存入，_get_header_text 动态生成）
        self._header_data: dict = {}
        self._anim_frame: int = 0
        self._body_control = FormattedTextControl(self._get_body_text)
        self._separator_control = FormattedTextControl(self._get_separator_text)
        self._footer_hint_control = FormattedTextControl(self._get_footer_hint_text)
        self._body_window: Window | None = None
        self._input_window = Window(
            BufferControl(buffer=self._input_buffer),
            height=Dimension(min=5, max=10),
            always_hide_cursor=False,
        )

        # ── 性能优化：body 文本缓存 ──
        # 避免每帧对全部 body items 执行 splitlines()，
        # 仅在内容变化时重建一次完整文本。
        self._body_text_cache: str = ""
        self._body_lines_cache: list[str] = []
        self._body_dirty: bool = True

        # ── 性能优化：复用渲染 Console ──
        # 避免每次 _render_to_ansi 创建新 Console 对象（长时间工作
        # 会产生数万个临时对象，GC 压力导致卡顿）。
        self._render_console: Console | None = None
        self._render_console_width: int = 0
        self._render_buffer = StringIO()

        # ── 性能优化：_invalidate 节流 ──
        self._last_invalidate_time: float = 0.0
        self._invalidate_scheduled: bool = False

        # ── 上下文用量显示 ──
        self._context_usage_text: str = ""
        self._context_usage_style: str = ""

    @property
    def console(self) -> Console:
        return self._console

    @property
    def live(self) -> None:
        return None

    # ─── 生命周期 ───

    def start(self) -> None:
        """启动全屏应用。"""
        if self._application_task and not self._application_task.done():
            return

        self._application = self._build_application()
        loop = asyncio.get_running_loop()
        self._application_task = loop.create_task(self._run_application())

    def stop(self) -> None:
        """停止全屏应用。"""
        if self._application and self._application.is_running:
            self._application.exit()
        self._application = None
        self._application_task = None

    # ─── Header ───

    def update_header(self, renderable: RenderableType) -> None:
        """更新 header 面板内容。"""
        self._header_content = renderable
        self._invalidate()

    def build_header(
        self, server: str, project: str, phase: str, auto_review: bool, checkpoint_count: int = 0,
        yolo_mode: bool = False,
        goal_mode: bool = False,
    ) -> None:
        """构建标准 header 内容（存储原始数据，由 _get_header_text 动态生成）。"""
        self._header_data = {
            "server": server,
            "project": project,
            "phase": phase,
            "auto_review": auto_review,
            "checkpoint_count": checkpoint_count,
            "yolo_mode": yolo_mode,
            "goal_mode": goal_mode,
        }
        self._invalidate()

    # ─── Body ───

    def append_body(self, renderable: RenderableType) -> int:
        """追加一条到 body 区域，返回条目索引。
        
        当超过 MAX_BODY_ITEMS 时从头部裁剪旧条目，
        并自动修正所有命名槽位和动画索引。
        """
        ansi = self._render_to_ansi(renderable)
        self._body_items.append((renderable, ansi))
        # 裁剪
        if len(self._body_items) > self.MAX_BODY_ITEMS:
            overflow = len(self._body_items) - self.MAX_BODY_ITEMS
            self._body_items = self._body_items[-self.MAX_BODY_ITEMS:]
            self._fix_indices_after_trim(overflow)
        self._body_dirty = True
        if self._follow_output:
            self._sync_scroll_to_bottom()
        self._invalidate()
        return len(self._body_items) - 1

    def insert_body(self, index: int, renderable: RenderableType) -> int:
        """在指定位置插入一条 body 条目，返回最终索引。
        
        插入后自动修正所有 >= 目标位置的命名槽位和动画索引 +1；
        若触发裁剪则再统一减去 overflow。
        """
        target = max(0, min(index, len(self._body_items)))
        ansi = self._render_to_ansi(renderable)
        self._body_items.insert(target, (renderable, ansi))
        self._fix_indices_after_insert(target)
        if len(self._body_items) > self.MAX_BODY_ITEMS:
            overflow = len(self._body_items) - self.MAX_BODY_ITEMS
            del self._body_items[:overflow]
            self._fix_indices_after_trim(overflow)
            target = max(0, target - overflow)
        self._body_dirty = True
        if self._follow_output:
            self._sync_scroll_to_bottom()
        self._invalidate()
        return target

    def update_body_item(self, index: int, renderable: RenderableType) -> None:
        """按索引替换 body 条目。"""
        if 0 <= index < len(self._body_items):
            ansi = self._render_to_ansi(renderable)
            self._body_items[index] = (renderable, ansi)
            self._body_dirty = True
            if self._follow_output:
                self._sync_scroll_to_bottom()
            self._invalidate()

    def update_body_last(self, renderable: RenderableType) -> None:
        """替换 body 最后一条（流式更新用）。"""
        ansi = self._render_to_ansi(renderable)
        if self._body_items:
            self._body_items[-1] = (renderable, ansi)
        else:
            self._body_items.append((renderable, ansi))
        self._body_dirty = True
        if self._follow_output:
            self._sync_scroll_to_bottom()
        self._invalidate()

    def clear_body(self) -> None:
        """清空 body 内容（包括命名槽位和动画槽位）。"""
        self._body_items.clear()
        self._animated_body_indices.clear()
        self._body_anim_factories.clear()
        self._slots.clear()
        self._body_scroll = 0
        self._follow_output = True
        self._body_dirty = True
        self._invalidate()

    def set_slot(self, name: str, index: int) -> None:
        """将命名槽位绑定到 body 物理索引（由调用方在 append/insert 后注册）。"""
        self._slots[name] = index

    def get_slot(self, name: str) -> int | None:
        """获取命名槽位对应的 body 物理索引，不存在返回 None。"""
        return self._slots.get(name)

    def del_slot(self, name: str) -> None:
        """删除命名槽位（不删除 body 条目本身）。"""
        self._slots.pop(name, None)

    def _fix_indices_after_trim(self, overflow: int) -> None:
        """当 body 头部被裁剪 overflow 条后，修正所有内部索引。

        所有命名槽位和动画索引统一减去 overflow；若某槽位索引变为负数
        （即其对应条目已被裁剪掉），则自动删除该槽位。
        """
        if overflow <= 0:
            return
        # 修正 slots
        for name in list(self._slots):
            new_idx = self._slots[name] - overflow
            if new_idx < 0:
                del self._slots[name]
            else:
                self._slots[name] = new_idx
        # 修正动画槽位索引
        new_animated: set[int] = set()
        for idx in self._animated_body_indices:
            new_idx = idx - overflow
            if new_idx >= 0:
                new_animated.add(new_idx)
        self._animated_body_indices = new_animated
        # 修正动画工厂索引
        new_factories: dict[int, Callable[[], RenderableType]] = {}
        for idx, factory in self._body_anim_factories.items():
            new_idx = idx - overflow
            if new_idx >= 0:
                new_factories[new_idx] = factory
        self._body_anim_factories = new_factories

    def _fix_indices_after_insert(self, at: int) -> None:
        """在 at 位置插入一条后，将所有 >= at 的内部索引 +1。

        调用时机：_body_items.insert(at, ...) 之后、裁剪之前。
        """
        for name in self._slots:
            if self._slots[name] >= at:
                self._slots[name] += 1
        new_animated: set[int] = set()
        for idx in self._animated_body_indices:
            new_animated.add(idx + 1 if idx >= at else idx)
        self._animated_body_indices = new_animated
        new_factories: dict[int, Callable[[], RenderableType]] = {}
        for idx, factory in self._body_anim_factories.items():
            new_idx = idx + 1 if idx >= at else idx
            new_factories[new_idx] = factory
        self._body_anim_factories = new_factories

    def mark_body_animated(self, index: int, factory: Callable[[], RenderableType]) -> None:
        """标记 body 条目为动画槽位，每帧用 factory 重新生成 renderable。

        Args:
            index: body 条目索引。
            factory: 无参回调，返回当前帧应显示的 RenderableType。
        """
        self._animated_body_indices.add(index)
        self._body_anim_factories[index] = factory

    def unmark_body_animated(self, index: int) -> None:
        """移除 body 条目的动画标记。"""
        self._animated_body_indices.discard(index)
        self._body_anim_factories.pop(index, None)

    @staticmethod
    def _format_source_label(source: str) -> str:
        """将 agent source 标识转为显示名称。"""
        normalized = (source or "agent").strip().lower()
        if normalized == "coder":
            return "Coder"
        if normalized in ("main", "agent"):
            return "Agent"
        return normalized.replace("_", " ").title()

    # ─── Footer ───

    def set_footer_prompt(self) -> None:
        """设置标准输入提示 footer。"""
        self._footer_hint = "输入消息或 /help 查看命令"
        self._invalidate()

    def set_footer_hint(self, hint: str) -> None:
        """设置自定义 footer 提示文本。"""
        self._footer_hint = hint
        self._invalidate()

    def set_footer_spinner(self, visible: bool, source: str = "agent") -> None:
        """显示或隐藏 body 底部的 Agent 忙碌旋转指示器。

        当 visible=True 时，body 最底部会固定显示一行 braille spinner，
        直观指示 agent 仍在工作中。幂等：状态未变化时忽略。

        Args:
            visible: 是否显示指示器。
            source: 当前活跃的 agent 标识（如 "agent"、"coder"），决定显示名称和颜色。
        """
        if self._footer_spinner_visible == visible and self._footer_spinner_source == source:
            return
        self._footer_spinner_visible = visible
        self._footer_spinner_source = source
        self._invalidate()

    # ── 上下文用量 ───

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        """将 token 数格式化为人类可读字符串，如 85000 → '85k'。"""
        if n >= 1_000_000:
            val = n / 1_000_000
            # 整数时省略小数点
            if val == int(val):
                return f"{int(val)}m"
            return f"{val:.1f}m"
        if n >= 1000:
            val = n / 1000
            if val == int(val):
                return f"{int(val)}k"
            return f"{val:.1f}k"
        return str(n)

    def set_context_usage(self, total_tokens: int, max_context: int) -> None:
        """设置上下文用量显示文本。

        根据 total_tokens 占 max_context 的比例选择颜色：
        - < 70%: dim 灰色
        - 70-90%: warning 橙色
        - > 90%: error 红色

        spinner 隐藏时不清除数据，下次显示时恢复。
        """
        used_str = self._fmt_tokens(total_tokens)

        if max_context and max_context > 0:
            max_str = self._fmt_tokens(max_context)
            ratio = total_tokens / max_context
            if ratio < 0.7:
                style = self._theme.dim
            elif ratio < 0.9:
                style = self._theme.warning
            else:
                style = self._theme.error
            self._context_usage_text = f"({used_str}/{max_str})"
            self._context_usage_style = style
        else:
            # max_context 未知时仅显示用量
            self._context_usage_text = f"({used_str} tokens)"
            self._context_usage_style = self._theme.dim

        self._invalidate()

    # ─── 输入 ───

    async def get_input(self, prompt: str = " > ") -> str:
        """异步获取用户输入。"""
        self._footer_hint = f"{prompt} Enter 发送 | Ctrl+J 换行 | Ctrl+C 中断 | /help"
        self._invalidate()

        value = await self._input_queue.get()
        if value == _REFRESH_INPUT_SENTINEL:
            raise InputRefreshRequested
        if value == _INTERRUPT_SENTINEL:
            raise KeyboardInterrupt
        if value == _EOF_SENTINEL:
            raise EOFError
        return value

    def request_input_refresh(self, clear_buffer: bool = True) -> None:
        """非致命地打断当前输入等待，用于切换到审批等更高优先级输入。"""
        if clear_buffer:
            self._input_buffer.text = ""
        self._input_queue.put_nowait(_REFRESH_INPUT_SENTINEL)
        self._invalidate()

    def save_input_draft(self) -> str:
        """保存当前输入草稿并清空输入框。"""
        draft = self._input_buffer.text
        self._input_buffer.text = ""
        return draft

    def restore_input_draft(self, draft: str) -> None:
        """恢复输入草稿。"""
        if draft:
            self._input_buffer.text = draft
        self._invalidate()

    # ─── 暂停/恢复（审批弹窗用）───

    def pause(self) -> None:
        """暂停全屏应用。"""
        self.stop()

    def resume(self) -> None:
        """恢复全屏应用。"""
        self.start()

    # ─── 滚动 ───

    def scroll_page_up(self) -> None:
        """向上滚动一页。"""
        self._scroll_body(-self._visible_body_height())

    def scroll_page_down(self) -> None:
        """向下滚动一页。"""
        self._scroll_body(self._visible_body_height())

    def scroll_to_top(self) -> None:
        """跳转到顶部。"""
        self._follow_output = False
        self._body_scroll = 0
        self._invalidate()

    def scroll_to_bottom(self) -> None:
        """跳转到底部。"""
        self._follow_output = True
        self._sync_scroll_to_bottom()
        self._invalidate()

    # ─── 内部 ───

    def _build_application(self) -> Application:
        """构建 prompt_toolkit 应用。"""
        kb = KeyBindings()

        @kb.add("enter")
        def _accept_input(event) -> None:
            text = self._input_buffer.text
            if text.strip():
                self._input_buffer.append_to_history()
            self._input_buffer.text = ""
            self._input_queue.put_nowait(text)
            self._invalidate()

        @kb.add("c-j")
        def _newline_ctrl_j(event) -> None:
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def _interrupt(event) -> None:
            self._input_queue.put_nowait(_INTERRUPT_SENTINEL)
            # 不调用 event.app.exit()，由 app.py 决定是否退出

        @kb.add("c-d")
        def _eof(event) -> None:
            self._input_queue.put_nowait(_EOF_SENTINEL)
            event.app.exit()

        @kb.add("pageup")
        def _page_up(event) -> None:
            self.scroll_page_up()

        @kb.add("pagedown")
        def _page_down(event) -> None:
            self.scroll_page_down()

        @kb.add("home")
        def _home(event) -> None:
            self.scroll_to_top()

        @kb.add("end")
        def _end(event) -> None:
            self.scroll_to_bottom()

        # 创建分隔线（动态宽度）
        separator = Window(
            height=1,
            dont_extend_height=True,
            content=self._separator_control,
        )
        
        # 创建 footer 提示（动态文本）
        footer_hint = Window(
            height=1,
            dont_extend_height=True,
            content=self._footer_hint_control,
        )

        self._body_window = Window(self._body_control, wrap_lines=True)

        root = HSplit(
            [
                # 单行状态栏（无 Frame）
                Window(self._header_control, height=1, dont_extend_height=True),
                separator,
                # Body 区域
                self._body_window,
                separator,
                # Composer 区域
                footer_hint,
                self._input_window,
            ]
        )
        
        # 定义样式
        style = Style.from_dict({
            'status-bar': f'bg:{self._theme.status_bar_bg} {self._theme.status_bar_fg}',
            'status-bar-dim': f'bg:{self._theme.status_bar_bg} {self._theme.dim}',
            'separator': f'{self._theme.dim}',
            'footer-hint': f'{self._theme.dim}',
        })
        
        return Application(
            layout=Layout(root, focused_element=self._input_window),
            key_bindings=kb,
            full_screen=True,
            mouse_support=True,
            style=style,
        )

    async def _run_application(self) -> None:
        """运行 prompt_toolkit 应用。"""
        if not self._application:
            return
        try:
            await self._application.run_async()
        finally:
            self._application = None

    def _invalidate(self) -> None:
        """请求界面重绘（带节流，防止高频更新阻塞事件循环）。

        若两次请求间隔 < _INVALIDATE_MIN_INTERVAL，则跳过本次并安排
        一次延迟补发，确保最后一次更新不会被丢弃（避免画面停住）。
        """
        if self._application and self._application.is_running:
            now = time.monotonic()
            if now - self._last_invalidate_time >= _INVALIDATE_MIN_INTERVAL:
                self._last_invalidate_time = now
                self._application.invalidate()
            elif not self._invalidate_scheduled:
                # 安排一次延迟补发，确保最终状态一定刷新
                self._invalidate_scheduled = True
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_later(
                        _INVALIDATE_MIN_INTERVAL,
                        self._deferred_invalidate,
                    )
                except RuntimeError:
                    pass  # 没有运行中的 loop，忽略

    def _deferred_invalidate(self) -> None:
        """节流失效后的延迟补发。"""
        self._invalidate_scheduled = False
        if self._application and self._application.is_running:
            self._last_invalidate_time = time.monotonic()
            self._application.invalidate()

    def _render_to_ansi(self, renderable: RenderableType) -> str:
        """将 Rich renderable 渲染为 ANSI 文本（复用 Console 实例）。"""
        width = self._render_width()
        self._last_render_width = width
        # 仅在宽度变化时重建 Console，避免长时间运行产生大量临时对象
        if self._render_console is None or width != self._render_console_width:
            self._render_console = Console(
                file=self._render_buffer,
                force_terminal=True,
                color_system="truecolor",
                width=width,
                legacy_windows=False,
            )
            self._render_console_width = width
        # 每次渲染前清空缓冲区，防止终端 resize 时旧内容残留
        self._render_buffer.truncate(0)
        self._render_buffer.seek(0)
        self._render_console.print(renderable)
        return self._render_buffer.getvalue().rstrip("\n")

    def _render_width(self) -> int:
        """计算消息渲染宽度。"""
        app = get_app_or_none()
        if app is not None:
            return max(40, app.output.get_size().columns - 6)
        return max(40, self._console.size.width - 6)

    def _needs_rerender(self) -> bool:
        """检测终端宽度是否变化，需要重渲染 body 条目。"""
        if not self._body_items:
            return False
        return self._render_width() != self._last_render_width

    def _rerender_all_body_items(self) -> None:
        """按当前终端宽度重渲染所有 body 条目。"""
        for i, (renderable, _ansi) in enumerate(self._body_items):
            new_ansi = self._render_to_ansi(renderable)
            self._body_items[i] = (renderable, new_ansi)
        self._body_dirty = True

    def _body_lines(self) -> list[str]:
        """获取当前 body 的全部文本行（使用缓存）。"""
        if not self._body_items:
            return ["等待 Agent 响应..."]

        # 终端宽度变化时，按新宽度重渲染所有条目（修复 resize 导致内容被裁剪的 bug）
        if self._needs_rerender():
            self._rerender_all_body_items()

        # 动画槽位：每帧用工厂函数重新生成 renderable 并渲染
        if self._animated_body_indices:
            for idx in list(self._animated_body_indices):
                factory = self._body_anim_factories.get(idx)
                if factory and 0 <= idx < len(self._body_items):
                    renderable = factory()
                    new_ansi = self._render_to_ansi(renderable)
                    self._body_items[idx] = (renderable, new_ansi)
            self._body_dirty = True

        # 仅在 body 内容变化时重建完整文本和行列表，避免每帧 O(n) 开销
        if self._body_dirty:
            self._body_text_cache = "\n".join(item[1] for item in self._body_items)
            self._body_lines_cache = self._body_text_cache.splitlines() or [""]
            self._body_dirty = False

        return self._body_lines_cache

    def _visible_body_height(self) -> int:
        """估算 body 区域可见高度。"""
        if self._body_window is not None and self._body_window.render_info is not None:
            return max(1, self._body_window.render_info.window_height)

        app = get_app_or_none()
        rows = app.output.get_size().rows if app is not None else 24
        # 首帧 render_info 尚不可用时，保守按输入框最大高度估算。
        # 如果按 min=5 估算而实际 input_window 被分配到 max=10，body 会返回过多行；
        # prompt_toolkit Window 会从返回内容顶部裁剪，导致底部最新输出看起来被“吞掉”。
        # 固定开销: header(1) + separator(1) + separator(1) + footer_hint(1) + input窗口(max 10) = 14
        return max(1, rows - 14)

    def _max_body_scroll(self) -> int:
        """获取 body 最大滚动偏移。"""
        return max(0, len(self._body_lines()) - self._visible_body_height())

    def _sync_scroll_to_bottom(self) -> None:
        """将滚动位置同步到底部。"""
        self._body_scroll = self._max_body_scroll()

    def _scroll_body(self, delta: int) -> None:
        """滚动 body。"""
        max_scroll = self._max_body_scroll()
        if max_scroll <= 0:
            self._body_scroll = 0
            self._follow_output = True
            self._invalidate()
            return

        self._follow_output = False
        self._body_scroll = max(0, min(self._body_scroll + delta, max_scroll))
        if self._body_scroll >= max_scroll:
            self._follow_output = True
        self._invalidate()

    def set_anim_frame(self, frame: int) -> None:
        """设置当前动画帧编号，供 _get_header_text 等动态方法使用。"""
        self._anim_frame = frame

    def _get_header_text(self):
        """动态生成 header 状态栏（含 spinner 动画）。"""
        data = self._header_data
        if data:
            return self._build_header_formatted(data, self._anim_frame)
        # 回退：如果 _header_content 已被外部设置（如 update_header）
        if isinstance(self._header_content, FormattedText):
            return self._header_content
        return ANSI(self._render_to_ansi(self._header_content))

    @staticmethod
    def _build_header_formatted(data: dict, anim_frame: int = 0) -> FormattedText:
        """从 header 数据构建 FormattedText（静态方法，方便测试）。

        当 phase 为 thinking/coding/researching 时，在相位图标旁显示旋转 braille spinner。
        """
        from .animation import AnimationManager

        phase = data.get("phase", "")
        project = data.get("project", "")
        auto_review = data.get("auto_review", False)
        checkpoint_count = data.get("checkpoint_count", 0)
        yolo_mode = data.get("yolo_mode", False)
        goal_mode = data.get("goal_mode", False)

        phase_icons: dict[str, str] = {
            "connecting": "🔌",
            "researching": "🔍",
            "ready": "✅",
            "thinking": "🧠",
            "coding": "⚡",
            "reviewing": "🔍",
            "error": "❌",
        }
        phase_icon = phase_icons.get(phase, "📌")

        # 活跃相位（thinking/coding/researching）显示 spinner
        spinner_phases = {"thinking", "coding", "researching"}
        if phase in spinner_phases:
            spinner_char = AnimationManager.spinner_char(anim_frame)
            phase_display = f"{phase_icon}{spinner_char} {phase}"
        else:
            phase_display = f"{phase_icon} {phase}"

        parts: list[tuple[str, str]] = [
            ("class:status-bar", " MoFox Code "),
            ("class:status-bar-dim", "● "),
            ("class:status-bar", f"{phase_display} "),
            ("class:status-bar", f"{project} "),
            ("class:status-bar", f"auto-review:{'ON' if auto_review else 'OFF'} "),
            ("class:status-bar", f"cp:{checkpoint_count}"),
        ]

        if yolo_mode:
            parts.append(("class:status-bar", " ⚡YOLO"))

        if goal_mode:
            parts.append(("class:status-bar", " 🎯GOAL"))

        return FormattedText(parts)

    def _get_separator_text(self):
        """动态生成分隔线，宽度自适应终端。"""
        app = get_app_or_none()
        width = app.output.get_size().columns if app is not None else 80
        return FormattedText([("class:separator", "─" * width)])

    def _get_footer_hint_text(self):
        """动态生成 footer 提示文本。"""
        return FormattedText([("class:footer-hint", " " + self._footer_hint)])

    def _get_body_text(self):
        # _body_lines 内部已使用缓存，只在 dirty 时重建
        lines = self._body_lines()
        visible_height = self._visible_body_height()

        # 如果 footer spinner 可见，为它预留一行
        spinner_reserved = 1 if self._footer_spinner_visible else 0
        effective_height = max(1, visible_height - spinner_reserved)
        max_scroll = max(0, len(lines) - effective_height)

        if self._follow_output:
            start = max_scroll
            self._body_scroll = max_scroll
        else:
            self._body_scroll = max(0, min(self._body_scroll, max_scroll))
            start = self._body_scroll

        end = start + effective_height
        visible_lines = lines[start:end]

        if self._footer_spinner_visible:
            from .animation import AnimationManager
            from rich.text import Text as RichText
            
            spinner = AnimationManager.spinner_char(self._anim_frame)
            label = self._format_source_label(self._footer_spinner_source)
            # Agent=success绿, Coder=warning橙, 其他=accent蓝
            color = {
                "Agent": self._theme.success,
                "Coder": self._theme.warning,
            }.get(label, self._theme.accent)
            
            spinner_text = RichText()
            spinner_text.append(f"  {spinner} ", style=self._theme.dim)
            spinner_text.append(f"{label}", style=f"bold {color}")
            spinner_text.append(" 工作中...", style=self._theme.dim)
            if self._context_usage_text:
                spinner_text.append(f" {self._context_usage_text}", style=self._context_usage_style)
            visible_lines.append(self._render_to_ansi(spinner_text))

        return ANSI("\n".join(visible_lines))
