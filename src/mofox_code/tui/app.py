"""TUI 应用主循环。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from rich.align import Align
from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from ..client import CodingAgentClient
from ..config import ClientConfig
from ..session import CheckpointInfo
from .approval_ui import ApprovalUI, ApprovalResult
from .input_handler import InputHandler, BuiltinCommandType
from .layout_manager import TUILayoutManager, InputRefreshRequested
from .renderer import TUIRenderer
from .stream_renderer import StreamMarkdownRenderer
from .themes import get_theme


class TUIApp:
    """TUI 应用主入口。

    使用 TUILayoutManager 管理全屏三分区布局，
    所有渲染通过 output 回调发送到 body 区域。
    """

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        self._theme = get_theme(config.theme)
        self._console = Console()

        # 布局管理器
        self._layout = TUILayoutManager(self._console, self._theme)

        # 渲染组件（注入 output 回调）
        self._renderer = TUIRenderer(
            self._console, self._theme, output=self._append_body_output
        )
        self._stream_renderer: StreamMarkdownRenderer | None = None
        self._input_handler = InputHandler(
            self._console, self._theme, output=self._append_body_output
        )
        self._approval_ui = ApprovalUI(self._console, self._theme)
        self._client = CodingAgentClient(config)
        self._running = True

        # 审批队列；同一轮多个 bash 可能并发发来多个审批请求
        self._approval_queue: deque[dict] = deque()
        self._active_agent_index: int | None = None
        self._active_thinking_index: int | None = None
        self._active_research_index: int | None = None
        self._active_stream_source = "agent"
        self._last_status_phase: str | None = None
        self._session_list_cache: list[dict] = []  # 缓存的会话列表（供 resume/delete 编号引用）
        
        # 新增属性
        self._saved_input_draft: str | None = None
        self._handling_approval: bool = False
        self._pending_checkpoint_choice: list[CheckpointInfo] | None = None
        self._thinking_start_time: float = 0.0
        self._last_ctrl_c_time: float = 0.0

        # 注册消息处理器
        self._client.on("session.ready", self._on_session_ready)
        self._client.on("agent.status", self._on_agent_status)
        self._client.on("agent.text", self._on_agent_text)
        self._client.on("agent.thinking", self._on_agent_thinking)
        self._client.on("tool.call", self._on_tool_call)
        self._client.on("bash.approval_request", self._on_approval_request)
        self._client.on("bash.output", self._on_bash_output)
        self._client.on("file.change", self._on_file_change)
        self._client.on("plan.present", self._on_plan_present)
        self._client.on("research.progress", self._on_research_progress)
        self._client.on("checkpoint.created", self._on_checkpoint_created)
        self._client.on("checkpoint.rollback_result", self._on_rollback_result)
        self._client.on("checkpoint.list_result", self._on_checkpoint_list_result)
        self._client.on("session.list_result", self._on_session_list_result)
        self._client.on("session.delete_result", self._on_session_delete_result)
        self._client.on("link.result", self._on_link_result)
        self._client.on("error", self._on_error)

    async def run(self) -> None:
        """主入口。"""
        # 启动全屏布局
        self._layout.start()
        self._refresh_header(phase="connecting")
        self._layout.set_footer_prompt()

        # 欢迎信息
        self._renderer.render_welcome(self._config.server_url)

        try:
            await self._client.connect()
        except Exception as e:
            self._renderer.render_error(f"无法连接到后端: {e}")
            self._layout.stop()
            return

        # 启动后台接收
        recv_task = asyncio.create_task(self._client.receive_loop())

        # 初始化会话
        await self._client.init_session()

        # 立即更新 header 为 Ready（不等 session.ready 异步到达）
        self._refresh_header(phase="ready")

        if self._config.auto_review:
            await self._client.toggle_auto_review(True)

        # 主输入循环
        try:
            await self._input_loop()
        except (EOFError, KeyboardInterrupt):
            self._layout.append_body(
                Text("  👋 再见！", style=self._theme.dim)
            )
        finally:
            self._running = False
            await self._client.close_session()
            await self._client.disconnect()
            recv_task.cancel()
            self._layout.stop()

    def _refresh_header(self, phase: str | None = None) -> None:
        """统一封装 header 刷新，避免各处理器散落调用。"""
        session = self._client.session
        self._layout.build_header(
            server=self._config.server_url,
            project=session.project_name,
            phase=phase or session.phase,
            auto_review=session.auto_review_enabled,
            checkpoint_count=len(session.checkpoints),
        )

    # ─── 输入循环 ───

    async def _input_loop(self) -> None:
        """用户输入循环。"""
        while self._running:
            if self._approval_queue or self._client.session.pending_approval_request_id:
                await self._handle_pending_approval()
                continue

            # 处理 rollback 选择
            if self._pending_checkpoint_choice is not None:
                await self._handle_checkpoint_choice()
                continue

            # 更新 footer 为输入提示
            self._set_input_footer(self._client.session.is_agent_busy)

            # 获取用户输入
            try:
                prompt = " guide> " if self._client.session.is_agent_busy else " > "
                text = await self._layout.get_input(prompt)
            except InputRefreshRequested:
                continue
            except KeyboardInterrupt:
                # Ctrl+C 处理
                now = time.monotonic()
                if self._client.session.is_agent_busy:
                    await self._client.send_interrupt()
                    self._layout.append_body(
                        Text("  ⏸ 已发送中断信号", style=self._theme.warning)
                    )
                    continue
                if now - self._last_ctrl_c_time < 2.0:
                    raise  # 双击退出
                self._last_ctrl_c_time = now
                self._layout.append_body(
                    Text("  再按一次 Ctrl+C 退出", style=self._theme.dim)
                )
                continue
            except EOFError:
                raise

            text = text.strip()
            if not text:
                continue

            should_continue = await self._submit_text(text)
            if not should_continue:
                break

    async def _handle_checkpoint_choice(self) -> None:
        """处理 rollback checkpoint 选择。"""
        checkpoints = self._pending_checkpoint_choice
        self._pending_checkpoint_choice = None
        try:
            choice = await self._layout.get_input(" rollback(#|Enter 取消)> ")
        except (EOFError, KeyboardInterrupt):
            self._layout.restore_input_draft(self._saved_input_draft)
            self._saved_input_draft = None
            return
        choice = choice.strip()
        if choice.isdigit():
            step = int(choice)
            target = next((cp for cp in checkpoints if cp.step == step), None)
            if target:
                await self._client.request_rollback("to_id", target.id)
            else:
                self._layout.append_body(Text("  未找到该步骤", style=self._theme.dim))
        # 恢复草稿
        if self._saved_input_draft is not None:
            self._layout.restore_input_draft(self._saved_input_draft)
            self._saved_input_draft = None

    async def _submit_text(self, text: str) -> bool:
        """处理一次文本输入，支持普通消息和工作中引导。"""

        busy = self._client.session.is_agent_busy

        # 内置命令
        if self._input_handler.is_builtin_command(text):
            result = self._input_handler.parse_builtin_command(text)
            return await self._handle_builtin(result)

        if not busy:
            self._active_agent_index = None
            self._active_thinking_index = None
            self._stream_renderer = None

        self._append_user_message(text, is_guidance=busy)

        if busy:
            await self._client.send_guidance_message(text)
        else:
            await self._client.send_user_message(text)

        return True

    # ─── 内置命令处理 ───

    async def _handle_builtin(self, cmd: Any) -> bool:
        """处理内置命令，返回 False 表示退出。"""
        match cmd.type:
            case BuiltinCommandType.EXIT:
                self._layout.append_body(
                    Text("  👋 再见！", style=self._theme.dim)
                )
                return False
            case BuiltinCommandType.CLEAR:
                self._layout.clear_body()

            case BuiltinCommandType.UNDO:
                self._layout.append_body(
                    Text("  ⏪ 正在回滚最后一步...", style=self._theme.dim)
                )
                await self._client.request_rollback("last")

            case BuiltinCommandType.ROLLBACK:
                await self._client.request_checkpoint_list()

            case BuiltinCommandType.HISTORY:
                checkpoints = self._client.session.checkpoints
                if not checkpoints:
                    self._layout.append_body(
                        Text("  暂无操作历史", style=self._theme.dim)
                    )
                else:
                    self._input_handler.render_checkpoint_list(checkpoints)

            case BuiltinCommandType.STATUS:
                session = self._client.session
                self._renderer.render_status_bar(
                    self._config.server_url,
                    session.project_name,
                    session.phase,
                    session.auto_review_enabled,
                )

            case BuiltinCommandType.AUTO_REVIEW:
                enabled = cmd.args.get("enabled", False)
                await self._client.toggle_auto_review(enabled)
                state = "ON" if enabled else "OFF"
                self._layout.append_body(
                    Text(
                        f"  🔒 自动审查已{'开启' if enabled else '关闭'} ({state})",
                        style=self._theme.accent,
                    )
                )
                # 更新 header
                self._refresh_header()

            case BuiltinCommandType.SESSION:
                action = cmd.args.get("action", "")
                if action == "list":
                    await self._client.list_sessions()
                elif action == "resume":
                    raw_id = cmd.args.get("session_id", "")
                    sid = self._resolve_session_id(raw_id)
                    if sid:
                        self._layout.append_body(
                            Text(f"  🔄 正在恢复会话 {sid[:8]}...", style=self._theme.dim)
                        )
                        await self._client.resume_session(sid)
                    else:
                        self._layout.append_body(
                            Text(f"  未找到会话: {raw_id}", style=self._theme.dim)
                        )
                elif action == "delete":
                    raw_id = cmd.args.get("session_id", "")
                    sid = self._resolve_session_id(raw_id)
                    if sid:
                        await self._client.delete_session(sid)
                    else:
                        self._layout.append_body(
                            Text(f"  未找到会话: {raw_id}", style=self._theme.dim)
                        )

            case BuiltinCommandType.LINK:
                path = cmd.args.get("path", "")
                if path:
                    self._layout.append_body(
                        Text(f"  🔗 正在关联项目: {path}", style=self._theme.dim)
                    )
                    await self._client.send_link(path)
                else:
                    self._layout.append_body(
                        Text("  用法: /link <路径>", style=self._theme.dim)
                    )

            case BuiltinCommandType.NEW:
                self._layout.append_body(
                    Text("  🆕 正在开启新对话...", style=self._theme.dim)
                )
                # 重置前端状态
                self._layout.clear_body()
                self._stream_renderer = None
                self._active_agent_index = None
                self._active_thinking_index = None
                self._active_stream_source = "agent"
                self._refresh_header(phase="connecting")
                await self._client.new_session()

            case BuiltinCommandType.HELP:
                self._input_handler.render_help()

            case BuiltinCommandType.NONE:
                self._layout.append_body(
                    Text("  未知命令，输入 /help 查看帮助", style=self._theme.dim)
                )

        return True

    # ─── 审批处理 ───

    async def _handle_pending_approval(self) -> None:
        """处理待审批请求。复用主输入框，避免切换终端输入模式。"""
        if not self._approval_queue:
            await asyncio.sleep(0.1)
            return

        self._handling_approval = True
        try:
            payload = self._approval_queue.popleft()

            command = payload.get("command", "")
            working_dir = payload.get("working_directory") or payload.get("working_dir", "")
            context = payload.get("context", "")
            auto_review_result = payload.get("auto_review_result")
            request_id = payload.get("request_id") or self._client.session.pending_approval_request_id or ""
            self._layout.append_body(
                self._approval_ui.build_approval_request(
                    command=command,
                    working_dir=working_dir,
                    context=context,
                    auto_review_result=auto_review_result,
                )
            )

            result = None
            while result is None:
                try:
                    choice = await self._layout.get_input(" approval(a/s/f/d/r)> ")
                except InputRefreshRequested:
                    continue
                except (EOFError, KeyboardInterrupt):
                    result = ApprovalResult(decision="deny", prefix="")
                    break

                result = self._approval_ui.parse_choice(choice, command)
                # 空输入仍需重试；非快捷键输入已由 parse_choice 自动转为 deny

            if result is None:
                return

            if result.decision == "deny" and result.reason:
                self._layout.append_body(
                    Text(f"  ❌ 已拒绝 — {result.reason}", style=self._theme.warning)
                )

            if result.decision == "auto_review_enable":
                await self._client.toggle_auto_review(True)
                self._layout.append_body(
                    Text(
                        "  🔒 自动审查已开启；后续命令若审查通过将直接执行，不再弹出审批",
                        style=self._theme.accent,
                    )
                )
                await self._client.send_approval(request_id, "allow_once")
            else:
                if result.reason:
                    await self._client.send_approval(
                        request_id, result.decision, result.prefix, result.reason
                    )
                else:
                    await self._client.send_approval(
                        request_id, result.decision, result.prefix
                    )
        finally:
            self._handling_approval = False
            if not self._approval_queue and self._saved_input_draft is not None:
                self._layout.restore_input_draft(self._saved_input_draft)
                self._saved_input_draft = None

    # ─── 消息处理器 ───

    async def _on_session_ready(self, payload: dict) -> None:
        project = payload.get("project_name", "")
        title = payload.get("title", "")
        self._refresh_header(phase="ready")
        if title:
            self._layout.append_body(
                Text(
                    f"  ✅ 会话已恢复 — 标题: {title}",
                    style=self._theme.success,
                )
            )
        else:
            self._layout.append_body(
                Text(
                    f"  ✅ 会话已就绪 — 项目: {project}",
                    style=self._theme.success,
                )
            )

        # 渲染历史消息（恢复模式）
        history = payload.get("history", [])
        if history:
            for msg in history:
                role = msg.get("role", "")
                text = msg.get("content", "")
                if not text or not text.strip():
                    continue
                if role == "user":
                    self._append_user_message(text.strip(), is_guidance=False)
                elif role == "assistant":
                    self._layout.append_body(Markdown(text.strip()))
                    self._layout.append_body(Text(""))

    async def _on_agent_status(self, payload: dict) -> None:
        phase = payload.get("phase", "")
        detail = payload.get("detail", "")
        source = payload.get("source", "agent")
        # 更新 header
        self._refresh_header(phase=phase)
        # 在 body 中显示状态信息
        self._renderer.render_agent_status(detail, source=source)

        if phase == "thinking":
            if self._last_status_phase != "thinking" and (
                self._active_agent_index is not None
                or self._active_thinking_index is not None
            ) and self._stream_renderer is None:
                # 仅当没有活跃流时才关闭上轮残留（避免截断正在输出的文本）
                self._close_active_response_segment()

            if self._active_thinking_index is None:
                self._active_stream_source = source
                placeholder = self._renderer.build_thinking(
                    "正在思考...",
                    title=self._thinking_title(source),
                )
                self._active_thinking_index = self._layout.append_body(placeholder)
                self._thinking_start_time = time.monotonic()
        elif phase == "ready":
            self._collapse_active_thinking_if_needed()
            self._active_research_index = None

        self._last_status_phase = phase

    async def _on_agent_text(self, payload: dict) -> None:
        content = payload.get("content", "")
        is_final = payload.get("is_final", False)
        source = payload.get("source", "agent")

        if not is_final:
            if self._active_stream_source != source and (
                self._active_agent_index is not None
                or self._active_thinking_index is not None
            ):
                self._close_active_response_segment()
            self._active_stream_source = source
            # 流式输出
            if self._stream_renderer is None:
                self._stream_renderer = StreamMarkdownRenderer(
                    self._console,
                    self._theme,
                    output=lambda renderable: self._update_agent_stream(renderable, source),
                )
            self._stream_renderer.feed(content)
        else:
            # 流结束
            if self._stream_renderer:
                if content:
                    self._stream_renderer.feed(content)
                self._stream_renderer.finalize()
                self._stream_renderer = None
            elif content and content.strip():
                self._update_agent_stream(
                    Markdown(content, style=self._theme.agent_text)
                )
            else:
                # 如果 _active_agent_index 不为 None，说明之前已有流式渲染
                # 跳过显示"模型未返回文本输出"，避免覆盖已流式输出的内容
                if self._active_agent_index is None:
                    self._layout.append_body(
                        Text("  （模型未返回文本输出）", style=self._theme.dim)
                    )

            self._collapse_active_thinking_if_needed()
            self._active_agent_index = None
            self._active_thinking_index = None
            self._active_stream_source = "agent"
            # 添加空行分隔
            self._layout.append_body(Text(""))

    async def _on_agent_thinking(self, payload: dict) -> None:
        content = payload.get("content", "")
        source = payload.get("source", "agent")
        if content and content.strip():
            if self._active_stream_source != source and (
                self._active_agent_index is not None
                or self._active_thinking_index is not None
            ):
                self._close_active_response_segment()
            self._active_stream_source = source
            
            # 兜底设置 thinking_start_time
            if self._thinking_start_time == 0.0:
                self._thinking_start_time = time.monotonic()
            
            thinking_panel = self._renderer.build_thinking(
                content,
                title=self._thinking_title(source),
            )
            if self._active_thinking_index is None:
                if self._active_agent_index is not None:
                    self._active_thinking_index = self._layout.insert_body(
                        self._active_agent_index,
                        thinking_panel,
                    )
                    self._active_agent_index += 1
                else:
                    self._active_thinking_index = self._layout.append_body(
                        thinking_panel
                    )
            else:
                self._layout.update_body_item(
                    self._active_thinking_index, thinking_panel
                )

    async def _on_tool_call(self, payload: dict) -> None:
        # tool.call 是一段 assistant 文本的边界：
        # 工具执行后的下一段 agent.text 必须开新槽位，否则会继续更新工具前的旧 Panel，
        # 当界面跟随底部滚动时，这会表现为“工具后的 agent 文本被吞掉”。
        self._close_active_response_segment()

        name = payload.get("name", "")
        args_summary = payload.get("args_summary", "")
        source = payload.get("source", "agent")
        stage = payload.get("stage", "running")
        self._renderer.render_tool_call(name, args_summary, source=source, stage=stage)
        self._layout.append_body(Text(""))

    async def _on_approval_request(self, payload: dict) -> None:
        was_empty = not self._approval_queue and not self._handling_approval
        self._approval_queue.append(payload)
        if was_empty and self._saved_input_draft is None:
            self._saved_input_draft = self._layout.save_input_draft()
        self._layout.request_input_refresh(clear_buffer=False)
        queued = len(self._approval_queue)
        suffix = f"（队列中还有 {queued - 1} 个）" if queued > 1 else ""
        self._layout.append_body(
            Align.center(
                Text(f"  🔐 收到命令审批请求，等待你的选择...{suffix}", style=self._theme.warning)
            )
        )

    async def _on_bash_output(self, payload: dict) -> None:
        content = payload.get("content", "")
        is_stderr = payload.get("stream") == "stderr"
        exit_code = payload.get("exit_code")
        self._renderer.render_bash_output(content, is_stderr, exit_code)
        self._layout.append_body(Text(""))

    async def _on_file_change(self, payload: dict) -> None:
        self._renderer.render_file_change(
            payload.get("path", ""),
            payload.get("action", ""),
            payload.get("diff", ""),
        )
        self._layout.append_body(Text(""))

    async def _on_plan_present(self, payload: dict) -> None:
        self._renderer.render_plan(payload.get("content", ""))
        self._layout.append_body(Text(""))

    async def _on_research_progress(self, payload: dict) -> None:
        total = payload.get("total", 0)
        completed = payload.get("completed", 0)
        current_module = payload.get("current_module", "")
        active_agents = payload.get("active_agents", [])
        scope_summary = payload.get("scope_summary", "")
        ignored_patterns_count = payload.get("ignored_patterns_count", 0)
        panel = self._renderer.build_research_progress(
            total,
            completed,
            current_module,
            active_agents=active_agents,
            scope_summary=scope_summary,
            ignored_patterns_count=ignored_patterns_count,
        )
        if self._active_research_index is None:
            self._active_research_index = self._layout.append_body(panel)
        else:
            self._layout.update_body_item(self._active_research_index, panel)

        if total and completed >= total:
            self._active_research_index = None

    async def _on_checkpoint_created(self, payload: dict) -> None:
        self._renderer.render_checkpoint_created(
            payload.get("step", 0),
            payload.get("tool", ""),
            payload.get("description", ""),
            payload.get("reversible", True),
        )
        self._layout.append_body(Text(""))

    async def _on_rollback_result(self, payload: dict) -> None:
        self._renderer.render_rollback_result(
            payload.get("restored_files", []),
            payload.get("warnings", []),
        )
        self._layout.append_body(Text(""))

    async def _on_checkpoint_list_result(self, payload: dict) -> None:
        """收到 checkpoint 列表后，展示并等待用户选择回滚目标。"""
        checkpoints_raw = payload.get("checkpoints", [])
        checkpoints = [CheckpointInfo(**cp) for cp in checkpoints_raw]
        self._pending_checkpoint_choice = checkpoints
        if self._saved_input_draft is None:
            self._saved_input_draft = self._layout.save_input_draft()
        self._layout.request_input_refresh(clear_buffer=False)
        self._input_handler.render_checkpoint_list(checkpoints)

    async def _on_session_list_result(self, payload: dict) -> None:
        """渲染会话列表。"""
        from rich.table import Table
        from datetime import datetime

        sessions = payload.get("sessions", [])
        if not sessions:
            self._layout.append_body(
                Text("  📋 暂无历史会话", style=self._theme.dim)
            )
            return

        table = Table(title="📋 历史会话", show_header=True)
        table.add_column("#", style="bold", width=4)
        table.add_column("标题")
        table.add_column("消息数", justify="right", width=8)
        table.add_column("时间", width=20)

        for i, s in enumerate(sessions, 1):
            title = s.get("title", "") or "(无标题)"
            msg_count = str(s.get("message_count", 0))
            ts = s.get("last_active_at", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "-"
            table.add_row(str(i), title, msg_count, time_str)

        self._layout.append_body(table)
        self._layout.append_body(
            Text(
                "  使用 /session resume <#> 恢复，/session delete <#> 删除",
                style=self._theme.dim,
            )
        )
        # 缓存会话列表供 resume/delete 使用
        self._session_list_cache = sessions

    async def _on_session_delete_result(self, payload: dict) -> None:
        sid = payload.get("session_id", "")
        self._layout.append_body(
            Text(f"  🗑️ 会话 {sid[:8]} 已删除", style=self._theme.dim)
        )

    async def _on_link_result(self, payload: dict) -> None:
        """处理 link.result 消息。"""
        status = payload.get("status", "")
        path = payload.get("path", "")
        message = payload.get("message", "")
        
        if status == "ok":
            project_name = payload.get("project_name", "")
            virtual_env = payload.get("virtual_environment", "")
            research_triggered = payload.get("research_triggered", False)
            
            # 显示成功信息
            success_text = f"  ✅ 项目已链接: {project_name}"
            if virtual_env and virtual_env != "未检测到":
                success_text += f" (虚拟环境: {virtual_env})"
            self._layout.append_body(
                Text(success_text, style=self._theme.success)
            )
            
            if research_triggered:
                self._layout.append_body(
                    Text("  🔍 已触发项目研究", style=self._theme.dim)
                )
            
        elif status == "already_linked":
            self._layout.append_body(
                Text(f"  ℹ️ {message}", style=self._theme.accent)
            )
        else:
            # 错误情况
            error_text = f"  ❌ 链接失败: {message}" if message else f"  ❌ 链接失败: {path}"
            self._layout.append_body(
                Text(error_text, style=self._theme.error)
            )

    async def _on_error(self, payload: dict) -> None:
        self._renderer.render_error(payload.get("message", "未知错误"))

    def _resolve_session_id(self, raw_id: str) -> str:
        """将会话编号或 ID 解析为完整的 session_id。

        如果 raw_id 是数字，从缓存列表中查找；否则直接返回（可能是完整 ID）。
        """
        if raw_id.isdigit() and self._session_list_cache:
            idx = int(raw_id) - 1
            if 0 <= idx < len(self._session_list_cache):
                return self._session_list_cache[idx].get("session_id", "")
        # 可能是完整 UUID 或前缀
        if raw_id:
            # 尝试在缓存中匹配前缀
            for s in self._session_list_cache:
                if s.get("session_id", "").startswith(raw_id):
                    return s["session_id"]
        return raw_id  # 直接返回原始输入（可能是完整 ID）

    def _append_body_output(self, renderable: RenderableType) -> None:
        """将 renderable 追加到 body，供 renderer/input_handler 复用。"""
        self._layout.append_body(renderable)

    def _set_input_footer(self, busy: bool) -> None:
        """根据当前是否忙碌更新输入提示。"""
        if busy:
            self._layout._footer_hint = "Agent 工作中，可直接输入补充引导；系统会在当前回合结束后继续处理"
            self._layout._invalidate()
            return
        self._layout.set_footer_prompt()

    def _append_user_message(self, text: str, is_guidance: bool = False) -> None:
        """渲染用户输入。"""
        label = "Guidance" if is_guidance else "You"
        style = self._theme.warning if is_guidance else self._theme.user_prompt
        panel = Panel(
            Text(text, style=self._theme.fg),
            title=f"  {label}",
            border_style=style,
            title_align="right",
            expand=True,
        )
        self._layout.append_body(Text(""))
        self._layout.append_body(panel)

    def _close_active_response_segment(self) -> None:
        """结束当前一轮 agent/thinking 的流式更新槽位。

        连续工具调用会把多轮 LLM 输出串在一个 session 里。
        收到 tool.call 时没有 final 事件，因此需要在前端手动收口当前槽位，
        让工具后的下一轮回复追加为新消息，而不是覆盖上一轮内容。
        """
        if self._stream_renderer:
            self._stream_renderer.finalize()
        self._collapse_active_thinking_if_needed()
        self._stream_renderer = None
        self._active_agent_index = None
        self._active_thinking_index = None
        self._active_stream_source = "agent"

    def _collapse_active_thinking_if_needed(self) -> None:
        """如果当前有活跃的 thinking 段，将其折叠为摘要行。"""
        if self._active_thinking_index is not None and self._thinking_start_time > 0:
            duration = time.monotonic() - self._thinking_start_time
            collapsed = self._renderer.build_thinking_collapsed(duration)
            self._layout.update_body_item(self._active_thinking_index, collapsed)
            self._active_thinking_index = None
            self._thinking_start_time = 0.0

    def _update_agent_stream(self, renderable: RenderableType, source: str = "agent") -> None:
        """就地更新当前 agent 回复，Agent 用绿色边框，Coder 用橙色边框。"""
        label = self._source_label(source)
        # Agent=success绿, Coder=warning橙, 其他=accent蓝
        border_color = {
            "Agent": self._theme.success,
            "Coder": self._theme.warning,
        }.get(label, self._theme.accent)
        
        message = Panel(
            renderable,
            title=f"  🤖 {label}",
            border_style=border_color,
            title_align="left",
            expand=True,
        )
        if self._active_agent_index is None:
            self._active_agent_index = self._layout.append_body(message)
        else:
            self._layout.update_body_item(self._active_agent_index, message)

    @staticmethod
    def _source_label(source: str) -> str:
        normalized = (source or "agent").strip().lower()
        if normalized == "coder":
            return "Coder"
        if normalized in {"agent", "main"}:
            return "Agent"
        return normalized.replace("_", " ").title()

    def _thinking_title(self, source: str) -> str:
        label = self._source_label(source)
        if label == "Agent":
            return "🧠 Thinking"
        return f"🧠 {label} Thinking"
