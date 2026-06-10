"""全屏 TUI 布局管理器。

使用 prompt_toolkit 驱动 header/body/composer 三分区布局，
同时保留 Rich renderable 作为消息内容来源。
"""

from __future__ import annotations

import asyncio
import time
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
        self._body_items: list[str] = []
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
        self._footer_hint = "Enter 发送 | Ctrl+J 换行 | Ctrl+C 中断 | /help"
        self._header_content: RenderableType = Text("")
        self._header_control = FormattedTextControl(self._get_header_text)
        self._body_control = FormattedTextControl(self._get_body_text)
        self._separator_control = FormattedTextControl(self._get_separator_text)
        self._footer_hint_control = FormattedTextControl(self._get_footer_hint_text)
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
        self, server: str, project: str, phase: str, auto_review: bool, checkpoint_count: int = 0
    ) -> None:
        """构建标准 header 内容。"""
        phase_icons = {
            "connecting": "🔌",
            "researching": "🔍",
            "ready": "✅",
            "thinking": "🧠",
            "coding": "⚡",
            "reviewing": "🔍",
            "error": "❌",
        }
        phase_icon = phase_icons.get(phase, "📌")
        
        # 使用 FormattedText 构建带背景色的单行状态栏
        header_parts = [
            ("class:status-bar", " MoFox Code "),
            ("class:status-bar-dim", "● "),
            ("class:status-bar", f"{phase_icon} {phase} "),
            ("class:status-bar", f"{project} "),
            ("class:status-bar", f"auto-review:{'ON' if auto_review else 'OFF'} "),
            ("class:status-bar", f"cp:{checkpoint_count}"),
        ]
        
        header_text = FormattedText(header_parts)
        self.update_header(header_text)

    # ─── Body ───

    def append_body(self, renderable: RenderableType) -> int:
        """追加一条到 body 区域，返回条目索引。"""
        self._body_items.append(self._render_to_ansi(renderable))
        # 裁剪
        if len(self._body_items) > self.MAX_BODY_ITEMS:
            self._body_items = self._body_items[-self.MAX_BODY_ITEMS:]
        self._body_dirty = True
        if self._follow_output:
            self._sync_scroll_to_bottom()
        self._invalidate()
        return len(self._body_items) - 1

    def insert_body(self, index: int, renderable: RenderableType) -> int:
        """在指定位置插入一条 body 条目，返回最终索引。"""
        target = max(0, min(index, len(self._body_items)))
        self._body_items.insert(target, self._render_to_ansi(renderable))
        if len(self._body_items) > self.MAX_BODY_ITEMS:
            overflow = len(self._body_items) - self.MAX_BODY_ITEMS
            del self._body_items[:overflow]
            target = max(0, target - overflow)
        self._body_dirty = True
        if self._follow_output:
            self._sync_scroll_to_bottom()
        self._invalidate()
        return target

    def update_body_item(self, index: int, renderable: RenderableType) -> None:
        """按索引替换 body 条目。"""
        if 0 <= index < len(self._body_items):
            self._body_items[index] = self._render_to_ansi(renderable)
            self._body_dirty = True
            if self._follow_output:
                self._sync_scroll_to_bottom()
            self._invalidate()

    def update_body_last(self, renderable: RenderableType) -> None:
        """替换 body 最后一条（流式更新用）。"""
        if self._body_items:
            self._body_items[-1] = self._render_to_ansi(renderable)
        else:
            self._body_items.append(self._render_to_ansi(renderable))
        self._body_dirty = True
        if self._follow_output:
            self._sync_scroll_to_bottom()
        self._invalidate()

    def clear_body(self) -> None:
        """清空 body 内容。"""
        self._body_items.clear()
        self._body_scroll = 0
        self._follow_output = True
        self._body_dirty = True
        self._invalidate()

    # ─── Footer ───

    def set_footer_prompt(self) -> None:
        """设置标准输入提示 footer。"""
        self._footer_hint = "输入消息或 /help 查看命令"
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

        root = HSplit(
            [
                # 单行状态栏（无 Frame）
                Window(self._header_control, height=1, dont_extend_height=True),
                separator,
                # Body 区域
                Window(self._body_control, wrap_lines=True),
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
            elif not getattr(self, '_invalidate_scheduled', False):
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
        else:
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

    def _body_lines(self) -> list[str]:
        """获取当前 body 的全部文本行（使用缓存）。"""
        if not self._body_items:
            return ["等待 Agent 响应..."]

        # 仅在 body 内容变化时重建完整文本和行列表，避免每帧 O(n) 开销
        if self._body_dirty:
            self._body_text_cache = "\n".join(self._body_items)
            self._body_lines_cache = self._body_text_cache.splitlines() or [""]
            self._body_dirty = False

        return self._body_lines_cache

    def _visible_body_height(self) -> int:
        """估算 body 区域可见高度。"""
        app = get_app_or_none()
        rows = app.output.get_size().rows if app is not None else 24
        # 固定开销: header(1) + separator(1) + separator(1) + footer_hint(1) + input窗口(min 5) = 9
        return max(6, rows - 9)

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

    def _get_header_text(self):
        # 如果已经是 FormattedText，直接返回
        if isinstance(self._header_content, FormattedText):
            return self._header_content
        # 否则转换为 ANSI
        return ANSI(self._render_to_ansi(self._header_content))

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
        max_scroll = max(0, len(lines) - visible_height)

        if self._follow_output:
            start = max_scroll
            self._body_scroll = max_scroll
        else:
            self._body_scroll = max(0, min(self._body_scroll, max_scroll))
            start = self._body_scroll

        end = start + visible_height
        visible_lines = lines[start:end]
        return ANSI("\n".join(visible_lines))
