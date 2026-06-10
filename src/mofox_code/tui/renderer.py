"""Rich 基础渲染器。"""

from __future__ import annotations

from typing import Callable

from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from .themes import Theme


class TUIRenderer:
    """基于 Rich 的终端渲染引擎。

    支持通过 output 回调将渲染结果发送到外部容器（如 Live Layout）。
    """

    def __init__(
        self,
        console: Console,
        theme: Theme,
        output: Callable[[RenderableType], None] | None = None,
    ) -> None:
        self._console = console
        self._theme = theme
        self._output = output

    def set_output(self, output: Callable[[RenderableType], None]) -> None:
        """设置 output 回调。"""
        self._output = output

    def _print(self, renderable: RenderableType, **kwargs) -> None:
        """统一的输出入口。"""
        if self._output:
            self._output(renderable)
        else:
            self._console.print(renderable, **kwargs)

    def render_welcome(self, server_url: str) -> None:
        """显示欢迎界面。"""
        ascii_art = (
            "  ╔╦╗╔═╗╔═╗╔═╗═╗╔     ╔═╗╔═╗╔╦╗╔═╗\n"
            "  ║║║║ ║╠╣ ║ ║╔╩╗     ║  ║ ║ ║║╠╣  \n"
            "  ╩ ╩╚═╝╩  ╚═╝╩ ╩     ╚═╝╚═╝═╩╝╚═╝\n"
        )
        content = Text()
        content.append(ascii_art, style=f"bold {self._theme.accent}")
        content.append(f"\n  Connecting to {server_url}...\n", style=self._theme.dim)
        panel = Panel(content, title="MoFox Code", border_style=self._theme.panel_border)
        self._print(panel)

    def render_status_bar(
        self, server: str, project: str, phase: str, auto_review: bool
    ) -> None:
        """渲染顶部状态栏（用于 /status 命令）。"""
        status_text = Text()
        status_text.append("  ", style=self._theme.dim)
        status_text.append(server, style=self._theme.dim)
        status_text.append(" │ ", style=self._theme.dim)
        status_text.append(project, style=self._theme.accent)
        status_text.append(" │ ", style=self._theme.dim)

        phase_icons = {
            "connecting": "Connecting",
            "researching": "Researching",
            "ready": "Ready",
            "thinking": "Thinking",
            "coding": "Coding",
            "reviewing": "Reviewing",
        }
        status_text.append(phase_icons.get(phase, phase), style=self._theme.accent)

        review_text = f"  Auto-review: {'ON' if auto_review else 'OFF'}"
        panel = Panel(
            Text.from_ansi(f"{status_text}\n{review_text}"),
            title="MoFox Code",
            border_style=self._theme.panel_border,
        )
        self._print(panel)

    def render_agent_text(self, content: str) -> None:
        """渲染 agent 完整回复。"""
        self._print(Text(""))
        self._print(Text("  Agent:", style=f"bold {self._theme.accent}"))
        md = Markdown(content)
        self._print(md)

    def render_thinking(self, content: str) -> None:
        """渲染思考过程。"""
        self._print(self.build_thinking(content))

    def build_thinking(self, content: str, title: str = "🧠 Thinking") -> Panel:
        """构建思考过程面板。"""
        display_text = Text(content, style=self._theme.thinking)
        return Panel(
            display_text,
            title=title,
            border_style=self._theme.dim,
            title_align="left",
            expand=True,
        )

    def build_thinking_collapsed(self, duration_seconds: float) -> Text:
        """构建折叠的 thinking 摘要行。"""
        return Text(
            f"  🧠 Thinking ({duration_seconds:.1f}s)",
            style=self._theme.dim,
        )

    def render_tool_call(
        self,
        name: str,
        args_summary: str,
        source: str = "agent",
        stage: str = "running",
    ) -> None:
        """渲染工具调用提示。"""
        tool_text = Text()
        tool_text.append("  ⚙ ", style=self._theme.accent)
        tool_text.append(f"{name}", style=f"bold {self._theme.accent}")
        if args_summary:
            tool_text.append(f" {args_summary}", style=self._theme.dim)
        normalized_stage = (stage or "running").strip().lower()
        if normalized_stage == "planning":
            tool_text.append("  ...", style=self._theme.warning)
        self._print(tool_text)

    def render_agent_status(self, detail: str, source: str = "agent") -> None:
        """渲染 agent 状态信息。"""
        status_text = Text()
        status_text.append("  ℹ ", style=self._theme.dim)
        status_text.append(f"{self._format_source_label(source)}: ", style=self._theme.dim)
        status_text.append(detail, style=self._theme.dim)
        self._print(status_text)

    def render_code_diff(self, path: str, diff: str) -> None:
        """渲染文件变更 Diff。"""
        syntax = Syntax(diff, "diff", theme=self._theme.code_theme)
        panel = Panel(syntax, title=f"  {path}", border_style=self._theme.panel_border)
        self._print(panel)

    def render_plan(self, content: str) -> None:
        """渲染落地计划。"""
        md = Markdown(content)
        panel = Panel(md, title="📝 Implementation Plan", border_style=self._theme.accent)
        self._print(panel)

    def render_research_progress(
        self,
        total: int,
        completed: int,
        current: str,
        active_agents: list[dict] | None = None,
        scope_summary: str = "",
        ignored_patterns_count: int = 0,
    ) -> None:
        """渲染研究进度。"""
        self._print(
            self.build_research_progress(
                total,
                completed,
                current,
                active_agents=active_agents,
                scope_summary=scope_summary,
                ignored_patterns_count=ignored_patterns_count,
            )
        )

    def build_research_progress(
        self,
        total: int,
        completed: int,
        current: str,
        active_agents: list[dict] | None = None,
        scope_summary: str = "",
        ignored_patterns_count: int = 0,
    ) -> Panel:
        """构建 research 进度面板，避免直接写入终端。"""
        safe_total = max(total, 1)
        safe_completed = max(0, min(completed, safe_total))
        filled = int((safe_completed / safe_total) * 24)
        bar = "█" * filled + "·" * (24 - filled)

        content = Text()
        content.append("  Research ", style=f"bold {self._theme.accent}")
        content.append(f"[{safe_completed}/{safe_total}]\n", style=self._theme.dim)
        content.append(f"  {bar}\n", style=self._theme.accent)
        content.append(
            f"  当前模块: {current or '准备中...'}",
            style=self._theme.fg,
        )
        if scope_summary:
            content.append(f"\n  范围: {scope_summary}", style=self._theme.dim)
        if ignored_patterns_count:
            content.append(
                f"\n  .gitignore 规则: {ignored_patterns_count}",
                style=self._theme.dim,
            )

        if active_agents:
            content.append("\n\n  活跃 Research Agents:", style=f"bold {self._theme.accent}")
            for agent in active_agents[:6]:
                name = agent.get("name", "researcher")
                module_path = agent.get("module_path", "准备中")
                focus = agent.get("focus", "")
                content.append(
                    f"\n  - {name}: {module_path}",
                    style=self._theme.fg,
                )
                if focus:
                    content.append(f" ({focus})", style=self._theme.dim)

        return Panel(
            content,
            title="🔍 Project Research",
            border_style=self._theme.panel_border,
            expand=True,
        )

    @staticmethod
    def _format_source_label(source: str) -> str:
        normalized = (source or "agent").strip().lower()
        if normalized == "coder":
            return "Coder"
        if normalized == "main":
            return "Agent"
        if normalized == "agent":
            return "Agent"
        return normalized.replace("_", " ").title()

    def render_bash_output(self, output: str, is_stderr: bool, exit_code: int | None = None) -> None:
        """渲染 bash 命令输出。"""
        if is_stderr:
            # stderr 不折叠，完整显示（截断上限 100 行）
            lines = output.splitlines()
            if len(lines) > 100:
                output = "\n".join(lines[:100])
                output += f"\n... (stderr 已截断，共 {len(lines)} 行)"
            self._print(Text(output, style=self._theme.error))
            return

        lines = output.splitlines()
        if exit_code == 0 and len(lines) > 5:
            # 成功输出，折叠
            collapsed = "\n".join(lines[:3])
            output = collapsed + f"\n... (共 {len(lines)} 行，已折叠)"
        elif exit_code is None and len(lines) > 20:
            # 无 exit_code，保守截断
            output = "\n".join(lines[:10]) + f"\n... (共 {len(lines)} 行)"
        elif exit_code is not None and exit_code != 0:
            # 失败输出，完整展开（上限 80 行）
            if len(lines) > 80:
                output = "\n".join(lines[:80])
                output += f"\n... (输出已截断，共 {len(lines)} 行)"
        # 其他情况（exit_code=0 且 <=5行，或 exit_code=None 且 <=20行）保持原样
        
        self._print(Text(output, style=self._theme.fg))

    def render_file_change(self, path: str, action: str, diff: str = "") -> None:
        """显示文件变更通知。"""
        icons = {"create": "+", "modify": "~", "delete": "-"}
        icon = icons.get(action, "*")
        action_colors = {
            "create": self._theme.success,
            "modify": self._theme.warning,
            "delete": self._theme.error,
        }
        color = action_colors.get(action, self._theme.dim)

        change_text = Text()
        change_text.append(f"  [{icon}] ", style=color)
        change_text.append(f"{action}: ", style=self._theme.dim)
        change_text.append(path, style=color)
        self._print(change_text)

        if diff:
            self.render_code_diff(path, diff)

    def render_checkpoint_created(
        self, step: int, tool: str, description: str, reversible: bool
    ) -> None:
        """显示 checkpoint 创建通知。"""
        rev = "reversible" if reversible else "irreversible"
        cp_text = Text()
        cp_text.append(f"  #[{step}] ", style=self._theme.dim)
        cp_text.append(description, style=self._theme.accent)
        cp_text.append(f" [{rev}]", style=self._theme.dim)
        self._print(cp_text)

    def render_rollback_result(
        self, restored_files: list, warnings: list
    ) -> None:
        """显示回滚结果。"""
        self._print(Text("  Rollback complete:", style=f"bold {self._theme.success}"))
        for f in restored_files:
            self._print(Text(f"    restored: {f}", style=self._theme.success))
        for w in warnings:
            self._print(Text(f"    warning: {w}", style=self._theme.warning))

    def render_error(self, message: str) -> None:
        """显示错误。"""
        self._print(Text(f"  ❌ Error: {message}", style=f"bold {self._theme.error}"))

    def render_user_prompt(self) -> None:
        """输出用户输入提示符。"""
        self._print(Text(""))
        self._print(Text("  You:", style=f"bold {self._theme.user_prompt}"))
