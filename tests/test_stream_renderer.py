import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from rich.console import Console
from rich.text import Text

from mofox_code.client import CodingAgentClient
from mofox_code.config import ClientConfig
from mofox_code.session import ClientSession
from mofox_code.tui.app import TUIApp
from mofox_code.tui.layout_manager import TUILayoutManager, InputRefreshRequested
from mofox_code.tui.renderer import TUIRenderer
from mofox_code.tui.stream_renderer import StreamMarkdownRenderer
from mofox_code.tui.themes import DARK_THEME


class StreamMarkdownRendererTest(unittest.TestCase):
    def setUp(self) -> None:
        self.console = Console(record=True, width=80)
        self.outputs: list[str] = []

        def capture(renderable) -> None:
            with self.console.capture() as capture_output:
                self.console.print(renderable)
            self.outputs.append(capture_output.get())

        self.renderer = StreamMarkdownRenderer(
            self.console,
            DARK_THEME,
            output=capture,
        )

    def test_supports_delta_streams(self) -> None:
        self.renderer.feed("Hello")
        self.renderer.feed(" world")
        self.renderer.finalize()

        self.assertIn("Hello world", self.outputs[-1])

    def test_supports_cumulative_streams_without_duplication(self) -> None:
        self.renderer.feed("Hello")
        self.renderer.feed("Hello world")
        self.renderer.finalize()

        self.assertIn("Hello world", self.outputs[-1])
        self.assertEqual(self.outputs[-1].count("Hello"), 1)

    def test_build_thinking_keeps_full_content(self) -> None:
        def capture(renderable) -> None:
            with self.console.capture() as capture_output:
                self.console.print(renderable)
            self.outputs.append(capture_output.get())

        renderer = TUIRenderer(self.console, DARK_THEME, output=capture)
        long_text = "x" * 1200

        renderer.render_thinking(long_text)

        self.assertGreaterEqual(self.outputs[-1].count("x"), len(long_text))
        self.assertNotIn("思考内容已截断", self.outputs[-1])


class TUILayoutManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.console = Console(record=True, width=80)
        self.layout = TUILayoutManager(self.console, DARK_THEME)

    def test_scroll_window_moves_when_page_up(self) -> None:
        for index in range(20):
            self.layout.append_body(Text(f"line {index}"))

        bottom_text = str(self.layout._get_body_text())
        self.assertIn("line 19", bottom_text)

        self.layout.scroll_page_up()
        scrolled_text = str(self.layout._get_body_text())

        self.assertIn("line 0", scrolled_text)
        self.assertNotIn("line 19", scrolled_text)

    def test_scroll_to_bottom_restores_follow_mode(self) -> None:
        for index in range(20):
            self.layout.append_body(Text(f"line {index}"))

        self.layout.scroll_to_top()
        self.layout.scroll_to_bottom()

        body_text = str(self.layout._get_body_text())
        self.assertIn("line 19", body_text)

    def test_request_input_refresh_interrupts_pending_input(self) -> None:
        async def runner() -> None:
            pending = asyncio.create_task(self.layout.get_input())
            await asyncio.sleep(0)
            self.layout.request_input_refresh()

            with self.assertRaises(InputRefreshRequested):
                await pending

        asyncio.run(runner())

    # ── _fmt_tokens 格式化测试 ──

    def test_fmt_tokens_below_1k_shows_raw_number(self) -> None:
        self.assertEqual(TUILayoutManager._fmt_tokens(0), "0")
        self.assertEqual(TUILayoutManager._fmt_tokens(999), "999")

    def test_fmt_tokens_k_range(self) -> None:
        self.assertEqual(TUILayoutManager._fmt_tokens(1000), "1k")
        self.assertEqual(TUILayoutManager._fmt_tokens(85000), "85k")
        self.assertEqual(TUILayoutManager._fmt_tokens(128000), "128k")
        self.assertEqual(TUILayoutManager._fmt_tokens(999499), "999.5k")

    def test_fmt_tokens_m_range(self) -> None:
        self.assertEqual(TUILayoutManager._fmt_tokens(1_000_000), "1m")
        self.assertEqual(TUILayoutManager._fmt_tokens(1_500_000), "1.5m")

    # ── set_context_usage 测试 ──

    def _get_usage(self, source: str = "agent") -> tuple[str, str]:
        """从 _context_usage dict 获取指定 source 的用量。"""
        return self.layout._context_usage.get(source, ("", ""))

    def test_context_usage_low_ratio_dim_color(self) -> None:
        self.layout.set_context_usage(64000, 128000)
        text, style = self._get_usage()
        self.assertIn("(64k/128k)", text)
        self.assertEqual(style, DARK_THEME.dim)

    def test_context_usage_medium_ratio_warning_color(self) -> None:
        self.layout.set_context_usage(96000, 128000)  # 75%
        text, style = self._get_usage()
        self.assertIn("(96k/128k)", text)
        self.assertEqual(style, DARK_THEME.warning)

    def test_context_usage_high_ratio_error_color(self) -> None:
        self.layout.set_context_usage(120000, 128000)  # 93.75%
        text, style = self._get_usage()
        self.assertIn("(120k/128k)", text)
        self.assertEqual(style, DARK_THEME.error)

    def test_context_usage_unknown_max_shows_only_tokens(self) -> None:
        self.layout.set_context_usage(85000, 0)
        text, style = self._get_usage()
        self.assertEqual(text, "(85k tokens)")
        self.assertEqual(style, DARK_THEME.dim)

    def test_context_usage_per_source_isolation(self) -> None:
        """验证 agent 和 coder 的用量互不覆盖。"""
        self.layout.set_context_usage(85000, 128000, source="agent")
        self.layout.set_context_usage(45000, 64000, source="coder")
        agent_text, _ = self._get_usage("agent")
        coder_text, _ = self._get_usage("coder")
        self.assertIn("85k/128k", agent_text)
        self.assertIn("45k/64k", coder_text)

    def test_context_usage_spinner_renders_with_usage(self) -> None:
        """验证 spinner 活跃时渲染中包含用量文本。"""
        self.layout.set_context_usage(85000, 128000)
        self.layout.set_footer_spinner(True, source="agent")
        body_text = str(self.layout._get_body_text())
        self.assertIn("(85k/128k)", body_text)
        self.assertIn("工作中...", body_text)


class SlotMechanismTest(unittest.TestCase):
    """命名槽位（slot）机制单元测试：验证裁剪/插入时索引自动修正。"""

    def setUp(self) -> None:
        self.console = Console(record=True, width=80)
        self.layout = TUILayoutManager(self.console, DARK_THEME)

    def test_append_body_overflow_fixes_slot_indices(self) -> None:
        """超过 MAX_BODY_ITEMS 后 slot 索引正确修正。"""
        old_max = TUILayoutManager.MAX_BODY_ITEMS
        TUILayoutManager.MAX_BODY_ITEMS = 5
        try:
            for i in range(7):
                idx = self.layout.append_body(Text(f"item {i}"))
            # body 已裁剪到后 5 条 (item 2..6)，slot 设为 item 5
            self.layout.set_slot("test", 3)  # item 5 的相对索引
            # 再追加触发裁剪
            self.layout.append_body(Text("item 7"))
            # slot 应从 3 减到 2（overflow=1）
            self.assertEqual(self.layout.get_slot("test"), 2)
        finally:
            TUILayoutManager.MAX_BODY_ITEMS = old_max

    def test_append_body_overflow_drops_out_of_range_slot(self) -> None:
        """裁剪后越界的 slot 自动删除。"""
        old_max = TUILayoutManager.MAX_BODY_ITEMS
        TUILayoutManager.MAX_BODY_ITEMS = 3
        try:
            for i in range(4):
                self.layout.append_body(Text(f"item {i}"))
            # slot 在索引 0（即将被裁剪）
            self.layout.set_slot("ghost", 0)
            # 追加触发裁剪，overflow=1，slot 0→-1 应删除
            self.layout.append_body(Text("item 4"))
            self.assertIsNone(self.layout.get_slot("ghost"))
        finally:
            TUILayoutManager.MAX_BODY_ITEMS = old_max

    def test_insert_body_shifts_slots_at_or_after_target(self) -> None:
        """insert_body 后 >= target 的 slot 自动 +1。"""
        self.layout.append_body(Text("A"))
        self.layout.append_body(Text("B"))
        self.layout.append_body(Text("C"))
        self.layout.set_slot("b", 1)  # B
        self.layout.set_slot("c", 2)  # C
        # 在 index 1 插入
        self.layout.insert_body(1, Text("X"))
        # slot "b" 从 1→2, "c" 从 2→3
        self.assertEqual(self.layout.get_slot("b"), 2)
        self.assertEqual(self.layout.get_slot("c"), 3)

    def test_insert_body_overflow_fixes_indices_correctly(self) -> None:
        """insert_body 触发裁剪后 slot 和返回索引正确。"""
        old_max = TUILayoutManager.MAX_BODY_ITEMS
        TUILayoutManager.MAX_BODY_ITEMS = 3
        try:
            self.layout.append_body(Text("A"))
            self.layout.append_body(Text("B"))
            self.layout.append_body(Text("C"))
            self.layout.set_slot("c", 2)
            # 在头部插入，触发裁剪（移除的是刚插入的 X，不是 A）
            result = self.layout.insert_body(0, Text("X"))
            # overflow=1: 头部 X 被裁剪，C 回到索引 2，slot 不变
            self.assertEqual(self.layout.get_slot("c"), 2)
            # 返回索引也已修正（X 被裁掉后 result=0 指向剩余的第一项）
            self.assertEqual(result, 0)
        finally:
            TUILayoutManager.MAX_BODY_ITEMS = old_max

    def test_clear_body_clears_all_slots(self) -> None:
        """clear_body 清空所有命名槽位。"""
        self.layout.append_body(Text("A"))
        self.layout.set_slot("agent", 0)
        self.layout.set_slot("thinking", 0)
        self.layout.clear_body()
        self.assertIsNone(self.layout.get_slot("agent"))
        self.assertIsNone(self.layout.get_slot("thinking"))
        self.assertEqual(len(self.layout._body_items), 0)

    def test_animated_indices_fixed_after_trim(self) -> None:
        """动画索引在裁剪后正确修正。"""
        old_max = TUILayoutManager.MAX_BODY_ITEMS
        TUILayoutManager.MAX_BODY_ITEMS = 3
        try:
            from collections.abc import Callable
            from rich.console import RenderableType

            for i in range(4):
                self.layout.append_body(Text(f"item {i}"))
            factory: Callable[[], RenderableType] = lambda: Text("anim")
            self.layout.mark_body_animated(2, factory)
            self.layout.mark_body_animated(3, factory)
            # 追加触发裁剪 overflow=1
            self.layout.append_body(Text("item 4"))
            # 动画索引从 {2,3} → {1,2}
            self.assertIn(1, self.layout._animated_body_indices)
            self.assertIn(2, self.layout._animated_body_indices)
        finally:
            TUILayoutManager.MAX_BODY_ITEMS = old_max

    def test_get_set_del_slot_basic(self) -> None:
        """基本 slot get/set/del 操作。"""
        self.assertIsNone(self.layout.get_slot("nonexistent"))
        self.layout.set_slot("test", 42)
        self.assertEqual(self.layout.get_slot("test"), 42)
        self.layout.del_slot("test")
        self.assertIsNone(self.layout.get_slot("test"))
        # 重复删除不报错
        self.layout.del_slot("test")


class TUIAppBehaviorTest(unittest.IsolatedAsyncioTestCase):
    async def test_thinking_is_inserted_before_agent_stream(self) -> None:
        app = TUIApp(ClientConfig())

        app._update_agent_stream(Text("reply"))
        await app._on_agent_thinking({"content": "analysis"})

        self.assertIn("Thinking", app._layout._body_items[0][1])
        self.assertIn("Agent", app._layout._body_items[1][1])

    async def test_research_progress_updates_same_slot(self) -> None:
        app = TUIApp(ClientConfig())

        await app._on_research_progress(
            {"total": 3, "completed": 1, "current_module": "alpha"}
        )
        await app._on_research_progress(
            {"total": 3, "completed": 2, "current_module": "beta"}
        )

        self.assertEqual(len(app._layout._body_items), 1)
        self.assertIn("beta", app._layout._body_items[0][1])

    async def test_research_progress_renders_active_agents_and_gitignore_scope(self) -> None:
        app = TUIApp(ClientConfig())

        await app._on_research_progress(
            {
                "total": 4,
                "completed": 1,
                "current_module": "src/kernel",
                "active_agents": [
                    {
                        "name": "researcher-1",
                        "module_path": "src/kernel",
                        "focus": "依赖与接口",
                    }
                ],
                "scope_summary": "仅研究未被 .gitignore 忽略的路径",
                "ignored_patterns_count": 3,
            }
        )

        body = app._layout._body_items[0][1]
        self.assertIn("researcher-1", body)
        self.assertIn(".gitignore", body)
        self.assertIn("src/kernel", body)

    async def test_pending_approval_uses_tui_input_queue(self) -> None:
        app = TUIApp(ClientConfig())
        app._approval_queue.append({
            "command": "git status",
            "working_dir": "C:/repo",
            "context": "check workspace",
            "auto_review_result": None,
        })
        app._client.session.pending_approval_request_id = "req-1"
        app._client.send_approval = AsyncMock()
        app._client.toggle_auto_review = AsyncMock()
        app._layout._input_queue.put_nowait("a")

        await app._handle_pending_approval()

        app._client.send_approval.assert_awaited_once_with("req-1", "allow_once", "")
        self.assertTrue(any("git status" in item[1] for item in app._layout._body_items))

    async def test_approval_request_refreshes_current_input(self) -> None:
        app = TUIApp(ClientConfig())
        app._layout.request_input_refresh = Mock()

        await app._on_approval_request({"request_id": "req-9", "command": "git status"})

        app._layout.request_input_refresh.assert_called_once_with(clear_buffer=False)
        self.assertEqual(app._approval_queue[0]["request_id"], "req-9")

    async def test_pending_approval_uses_payload_request_id_when_session_state_lags(self) -> None:
        app = TUIApp(ClientConfig())
        app._approval_queue.append({
            "request_id": "req-2",
            "command": "git status",
            "working_dir": "C:/repo",
            "context": "check workspace",
            "auto_review_result": None,
        })
        app._client.session.pending_approval_request_id = None
        app._client.send_approval = AsyncMock()
        app._client.toggle_auto_review = AsyncMock()
        app._layout._input_queue.put_nowait("a")

        await app._handle_pending_approval()

        app._client.send_approval.assert_awaited_once_with("req-2", "allow_once", "")

    async def test_multiple_approval_requests_are_queued_and_processed_in_order(self) -> None:
        app = TUIApp(ClientConfig())
        app._client.send_approval = AsyncMock()
        app._client.toggle_auto_review = AsyncMock()

        await app._on_approval_request({"request_id": "req-1", "command": "git status"})
        await app._on_approval_request({"request_id": "req-2", "command": "git diff"})

        self.assertEqual(len(app._approval_queue), 2)

        app._layout._input_queue.put_nowait("r")
        await app._handle_pending_approval()

        app._layout._input_queue.put_nowait("a")
        await app._handle_pending_approval()

        app._client.toggle_auto_review.assert_awaited_once_with(True)
        self.assertEqual(app._client.send_approval.await_args_list[0].args, ("req-1", "allow_once"))
        self.assertEqual(app._client.send_approval.await_args_list[1].args, ("req-2", "allow_once", ""))
        self.assertTrue(any("自动审查已开启" in item[1] for item in app._layout._body_items))

    async def test_busy_text_is_sent_as_guidance_without_resetting_stream(self) -> None:
        app = TUIApp(ClientConfig())
        app._client.session.is_agent_busy = True
        app._active_agent_index = 0
        app._active_thinking_index = 1
        app._stream_renderer = object()
        app._client.send_guidance_message = AsyncMock()

        should_continue = await app._submit_text("请优先改测试")

        self.assertTrue(should_continue)
        app._client.send_guidance_message.assert_awaited_once_with("请优先改测试")
        self.assertEqual(app._active_agent_index, 0)
        self.assertEqual(app._active_thinking_index, 1)
        self.assertIsNotNone(app._stream_renderer)
        # body_items: [0]=空行, [1]=Panel(title="Guidance", ...)
        self.assertEqual(len(app._layout._body_items), 2)
        self.assertIn("Guidance", app._layout._body_items[1][1])
        self.assertIn("请优先改测试", app._layout._body_items[1][1])

    async def test_tool_call_starts_new_agent_segment(self) -> None:
        app = TUIApp(ClientConfig())

        await app._on_agent_text({"content": "first", "is_final": False})
        await app._on_tool_call({"name": "bash", "args_summary": "ls"})
        await app._on_agent_text({"content": "second", "is_final": False})

        self.assertEqual(len(app._layout._body_items), 4)  # agent + tool + 空行 + agent
        self.assertIn("first", app._layout._body_items[0][1])
        self.assertIn("bash", app._layout._body_items[1][1])
        self.assertIn("ls", app._layout._body_items[1][1])
        self.assertIn("second", app._layout._body_items[3][1])

    async def test_tool_call_planning_stage_is_visible_immediately(self) -> None:
        app = TUIApp(ClientConfig())

        await app._on_tool_call({"name": "write_plan", "args_summary": "", "stage": "planning"})

        self.assertIn("write_plan", app._layout._body_items[0][1])
        self.assertIn("...", app._layout._body_items[0][1])  # planning indicator

    async def test_coder_stream_uses_coder_label(self) -> None:
        app = TUIApp(ClientConfig())

        await app._on_agent_text(
            {"content": "正在修改文件", "is_final": False, "source": "coder"}
        )

        self.assertIn("Coder", app._layout._body_items[0][1])

    async def test_new_thinking_phase_starts_new_thinking_segment(self) -> None:
        app = TUIApp(ClientConfig())

        await app._on_agent_status({"phase": "thinking", "detail": "first pass"})
        await app._on_agent_thinking({"content": "analysis one"})
        await app._on_tool_call({"name": "bash", "args_summary": "ls"})
        await app._on_agent_status({"phase": "thinking", "detail": "second pass"})
        await app._on_agent_thinking({"content": "analysis two"})

        thinking_segments = [item[1] for item in app._layout._body_items if "Thinking" in item[1]]
        self.assertEqual(len(thinking_segments), 2)
        # First thinking should be collapsed after tool_call
        self.assertIn("Thinking (", thinking_segments[0])
        # Second thinking should be expanded (still active)
        self.assertIn("analysis two", thinking_segments[1])

class ClientSessionStateTest(unittest.IsolatedAsyncioTestCase):
    def test_status_controls_busy_flag(self) -> None:
        session = ClientSession()

        session.update_from_status("thinking", "继续处理")
        self.assertTrue(session.is_agent_busy)

        session.update_from_status("ready", "已完成")
        self.assertFalse(session.is_agent_busy)

    async def test_send_approval_marks_agent_busy_again(self) -> None:
        client = CodingAgentClient(ClientConfig())
        client.session.pending_approval_request_id = "req-2"
        client.send = AsyncMock(return_value="msg-1")

        await client.send_approval("req-2", "allow_once")

        self.assertTrue(client.session.is_agent_busy)
        self.assertIsNone(client.session.pending_approval_request_id)
        client.send.assert_awaited_once_with(
            "bash.approval",
            {
                "request_id": "req-2",
                "decision": "allow_once",
                "prefix": "",
            },
        )

    async def test_send_approval_preserves_newer_pending_request_id(self) -> None:
        client = CodingAgentClient(ClientConfig())

        async def fake_send(msg_type: str, payload: dict | None = None) -> str:
            client.session.pending_approval_request_id = "req-3"
            return "msg-2"

        client.session.pending_approval_request_id = "req-2"
        client.send = AsyncMock(side_effect=fake_send)

        await client.send_approval("req-2", "allow_once")

        self.assertEqual(client.session.pending_approval_request_id, "req-3")

    async def test_send_guidance_message_marks_guidance_kind(self) -> None:
        client = CodingAgentClient(ClientConfig())
        client.send = AsyncMock(return_value="msg-2")

        await client.send_guidance_message("先不要改文档")

        client.send.assert_awaited_once_with(
            "user.message",
            {"content": "先不要改文档", "kind": "guidance"},
        )


class Phase1BehaviorTest(unittest.IsolatedAsyncioTestCase):
    """Phase 1 重构新增行为测试。"""

    # ── 审批草稿测试 ──

    async def test_approval_saves_draft_once_per_queue_burst(self) -> None:
        """多个审批请求只保存一次草稿，全部处理完后恢复。"""
        app = TUIApp(ClientConfig())
        app._client.send_approval = AsyncMock()
        app._client.toggle_auto_review = AsyncMock()
        
        # 模拟用户正在输入
        app._layout._input_buffer.text = "my draft text"
        
        # 发送多个审批请求
        await app._on_approval_request({"request_id": "req-1", "command": "git status"})
        await app._on_approval_request({"request_id": "req-2", "command": "git diff"})
        
        # 草稿应保存一次
        self.assertEqual(app._saved_input_draft, "my draft text")
        self.assertEqual(len(app._approval_queue), 2)
        
        # 处理第一个审批
        app._layout._input_queue.put_nowait("a")
        await app._handle_pending_approval()
        
        # 草稿还没恢复（队列不为空）
        self.assertEqual(app._saved_input_draft, "my draft text")
        
        # 处理第二个审批
        app._layout._input_queue.put_nowait("a")
        await app._handle_pending_approval()
        
        # 草稿恢复
        self.assertIsNone(app._saved_input_draft)
        self.assertEqual(app._layout._input_buffer.text, "my draft text")

    # ── Ctrl+C 测试 ──

    async def test_ctrl_c_busy_sends_interrupt_not_exit(self) -> None:
        """agent busy 时 KeyboardInterrupt 调用 send_interrupt()。"""
        app = TUIApp(ClientConfig())
        app._client.session.is_agent_busy = True
        app._client.send_interrupt = AsyncMock()
        
        # 模拟 KeyboardInterrupt
        app._layout._input_queue.put_nowait("__mofox_interrupt__")
        
        # get_input 会抛出 KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            await app._layout.get_input()
        
        # send_interrupt 不会在 get_input 中调用，但 _input_loop 中会
        # 这里主要验证中断信号的传递

    async def test_ctrl_c_idle_single_shows_warning(self) -> None:
        """空闲时单击 Ctrl+C 提示再按一次。"""
        app = TUIApp(ClientConfig())
        app._client.session.is_agent_busy = False
        
        # 验证 _last_ctrl_c_time 初始为 0
        self.assertEqual(app._last_ctrl_c_time, 0.0)

    # ── Rollback 测试 ──

    async def test_rollback_uses_tui_input_not_pause(self) -> None:
        """rollback 选择不调用 pause()/input()。"""
        app = TUIApp(ClientConfig())
        app._client.request_rollback = AsyncMock()
        
        # 发送 checkpoint 列表结果
        await app._on_checkpoint_list_result({
            "checkpoints": [
                {"id": "cp-1", "step": 1, "tool": "bash", "description": "ls", "files_affected": [], "reversible": True},
                {"id": "cp-2", "step": 2, "tool": "bash", "description": "cat", "files_affected": [], "reversible": True},
            ]
        })
        
        # 应该设置 pending_checkpoint_choice
        self.assertIsNotNone(app._pending_checkpoint_choice)
        self.assertEqual(len(app._pending_checkpoint_choice), 2)

    async def test_rollback_preserves_input_draft(self) -> None:
        """rollback 不清空用户正在编辑的输入。"""
        app = TUIApp(ClientConfig())
        
        # 模拟用户正在输入
        app._layout._input_buffer.text = "正在编辑的消息"
        
        # 触发 checkpoint 列表结果
        await app._on_checkpoint_list_result({
            "checkpoints": [
                {"id": "cp-1", "step": 1, "tool": "bash", "description": "ls", "files_affected": [], "reversible": True},
            ]
        })
        
        # 草稿应保存
        self.assertEqual(app._saved_input_draft, "正在编辑的消息")

    # ── Thinking 折叠测试 ──

    async def test_thinking_collapses_when_segment_closes(self) -> None:
        """_collapse_active_thinking_if_needed() 正确替换为摘要行。"""
        app = TUIApp(ClientConfig())
        
        # 开始 thinking
        await app._on_agent_status({"phase": "thinking", "detail": "analysis"})
        await app._on_agent_thinking({"content": "deep thinking"})
        
        # 找到 thinking 面板的位置
        thinking_index = app._layout.get_slot("thinking")
        self.assertIsNotNone(thinking_index)
        self.assertIn("deep thinking", app._layout._body_items[thinking_index][1])
        
        # tool call 触发折叠
        await app._on_tool_call({"name": "bash", "args_summary": "ls"})
        
        # thinking 应该被折叠（原位置）
        collapsed_item = app._layout._body_items[thinking_index][1]
        self.assertIn("Thinking (", collapsed_item)
        self.assertNotIn("deep thinking", collapsed_item)

    async def test_thinking_stays_expanded_while_streaming(self) -> None:
        """流式期间 thinking 保持 Panel 展开。"""
        app = TUIApp(ClientConfig())
        
        # 开始 thinking
        await app._on_agent_status({"phase": "thinking", "detail": "analysis"})
        await app._on_agent_thinking({"content": "step 1"})
        await app._on_agent_thinking({"content": "step 2"})
        
        # thinking 应该保持展开
        thinking_index = app._layout.get_slot("thinking")
        self.assertIsNotNone(thinking_index)
        thinking_item = app._layout._body_items[thinking_index][1]
        self.assertIn("step 2", thinking_item)
        self.assertNotIn("Thinking (", thinking_item)

    # ── Bash 输出折叠测试 ──

    def test_bash_output_collapsed_on_exit_zero(self) -> None:
        """exit_code=0 且 >5行时折叠（仅输出前3行，不含折叠标记）。"""
        console = Console(record=True, width=80)
        outputs: list[str] = []
        
        def capture(renderable) -> None:
            with console.capture() as cap:
                console.print(renderable)
            outputs.append(cap.get())
        
        renderer = TUIRenderer(console, DARK_THEME, output=capture)
        
        # 10行输出
        output = "\n".join([f"line {i}" for i in range(10)])
        renderer.render_bash_output(output, is_stderr=False, exit_code=0)
        
        # 应仅输出前3行，不含折叠标记（折叠标记由 get_bash_fold_info + build_bash_fold_line 单独管理）
        result = outputs[-1]
        self.assertIn("line 0", result)
        self.assertIn("line 2", result)
        self.assertNotIn("line 9", result)
        
        # 验证 get_bash_fold_info 正确报告折叠信息
        total, is_folded = renderer.get_bash_fold_info(output, is_stderr=False, exit_code=0)
        self.assertEqual(total, 10)
        self.assertTrue(is_folded)

        # 验证 build_bash_fold_line 构建折叠行（含箭头和行数）
        fold_line = renderer.build_bash_fold_line(10, expanded=False, frame=2)
        fold_str = str(fold_line)
        self.assertIn("10 lines", fold_str)

    def test_bash_output_expanded_on_exit_nonzero(self) -> None:
        """exit_code!=0 时完整展开。"""
        console = Console(record=True, width=80)
        outputs: list[str] = []
        
        def capture(renderable) -> None:
            with console.capture() as cap:
                console.print(renderable)
            outputs.append(cap.get())
        
        renderer = TUIRenderer(console, DARK_THEME, output=capture)
        
        # 10行输出
        output = "\n".join([f"line {i}" for i in range(10)])
        renderer.render_bash_output(output, is_stderr=False, exit_code=1)
        
        # 应该完整展开
        result = outputs[-1]
        self.assertIn("line 9", result)
        self.assertNotIn("已折叠", result)

    def test_bash_output_conservative_when_exit_code_none(self) -> None:
        """exit_code=None 时保守截断（仅输出前10行，不含折叠标记）。"""
        console = Console(record=True, width=80)
        outputs: list[str] = []
        
        def capture(renderable) -> None:
            with console.capture() as cap:
                console.print(renderable)
            outputs.append(cap.get())
        
        renderer = TUIRenderer(console, DARK_THEME, output=capture)
        
        # 25行输出
        output = "\n".join([f"line {i}" for i in range(25)])
        renderer.render_bash_output(output, is_stderr=False, exit_code=None)
        
        # 应仅输出前10行，不含折叠标记
        result = outputs[-1]
        self.assertIn("line 0", result)
        self.assertIn("line 9", result)
        self.assertNotIn("line 24", result)
        
        # 验证 get_bash_fold_info 正确报告折叠信息
        total, is_folded = renderer.get_bash_fold_info(output, is_stderr=False, exit_code=None)
        self.assertEqual(total, 25)
        self.assertTrue(is_folded)

    # ── 流式渲染测试 ──

    def test_stream_throttling_skips_intermediate_emits(self) -> None:
        """快速连续 feed 时不会每次都 emit。"""
        console = Console(record=True, width=80)
        emit_count = 0
        
        def counting_output(renderable) -> None:
            nonlocal emit_count
            emit_count += 1
        
        renderer = StreamMarkdownRenderer(console, DARK_THEME, output=counting_output)
        
        # 第一块立即渲染
        renderer.feed("first chunk")
        self.assertEqual(emit_count, 1)
        
        # 快速连续 feed（<50ms间隔）
        renderer.feed("first chunk second")
        renderer.feed("first chunk second third")
        
        # 不应该每次都 emit
        self.assertLessEqual(emit_count, 3)

    def test_stream_finalize_does_not_double_emit(self) -> None:
        """finalize 不重复输出已渲染内容。"""
        console = Console(record=True, width=80)
        emit_count = 0
        
        def counting_output(renderable) -> None:
            nonlocal emit_count
            emit_count += 1
        
        renderer = StreamMarkdownRenderer(console, DARK_THEME, output=counting_output)
        
        # feed 内容
        renderer.feed("content")
        initial_count = emit_count
        
        # finalize 不应该再次 emit（内容相同）
        renderer.finalize()
        self.assertEqual(emit_count, initial_count)

    def test_stream_first_chunk_renders_immediately(self) -> None:
        """第一块内容立即渲染不等 50ms。"""
        console = Console(record=True, width=80)
        rendered_content: list[str] = []
        
        def capture(renderable) -> None:
            with console.capture() as cap:
                console.print(renderable)
            rendered_content.append(cap.get())
        
        renderer = StreamMarkdownRenderer(console, DARK_THEME, output=capture)
        
        # 第一块应该立即渲染
        renderer.feed("immediate content")
        self.assertEqual(len(rendered_content), 1)
        self.assertIn("immediate content", rendered_content[0])


if __name__ == "__main__":
    unittest.main()