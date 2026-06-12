"""Rich 基础渲染器。"""

from __future__ import annotations

from typing import Callable

from pyfiglet import figlet_format
from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from .themes import Theme
from .animation import AnimationManager


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

    # 欢迎面板降级宽度阈值（低于此宽度时用简洁文本替代 figlet 艺术字）
    _WELCOME_FIGLET_MIN_WIDTH = 70

    def render_welcome(self, server_url: str | None = None) -> None:
        """显示欢迎界面。

        Args:
            server_url: 服务端地址。为 None 时显示"会话已就绪"，
                        否则显示"Connecting to {server_url}..."。
        """
        if self._console.width < self._WELCOME_FIGLET_MIN_WIDTH:
            # 窄终端降级：用简洁文本标题替代 figlet 艺术字
            content = Text()
            content.append("Welcome from MoFox Code", style=f"bold {self._theme.accent}")
            if server_url:
                content.append(f"\n  Connecting to {server_url}...\n", style=self._theme.dim)
            else:
                content.append("\n  ✅ 会话已就绪\n", style=self._theme.success)
            panel = Panel(content, title="MoFox Code", border_style=self._theme.panel_border)
            self._print(panel)
            return

        # 宽终端：使用 figlet 艺术字
        art = figlet_format("MoFox Code", font="slant")
        content = Text()
        content.append(art.rstrip(), style=f"bold {self._theme.accent}")
        if server_url:
            content.append(f"\n  Connecting to {server_url}...\n", style=self._theme.dim)
        else:
            content.append("\n  ✅ 会话已就绪\n", style=self._theme.success)
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

    def build_thinking(self, content: str, title: str = "🧠 Thinking", anim_dots: str = "") -> Panel:
        """构建思考过程面板。

        Args:
            content: 思考文本内容。
            title: 面板标题。
            anim_dots: 动画点字符串（空字符串=不显示动画，否则为预计算的呼吸点字符串）。
        """
        display_content = content
        if anim_dots and (not content or content.strip() == "正在思考..."):
            # 占位文本加动画点（由调用方通过 AnimationManager.breathing_dots 预生成）
            display_content = f"正在思考{anim_dots}"
        display_text = Text(display_content, style=self._theme.thinking)
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
    ) -> RenderableType:
        """渲染工具调用提示，返回 renderable 供外部动画管理。"""
        tool_text = self.build_tool_call_animated(
            name, args_summary, source, stage, frame=0
        )
        self._print(tool_text)
        return tool_text

    def build_tool_call_animated(
        self,
        name: str,
        args_summary: str,
        source: str = "agent",
        stage: str = "running",
        frame: int = 0,
    ) -> RenderableType:
        """构建工具调用行 renderable。

        Args:
            name: 工具名称。
            args_summary: 参数摘要。
            source: 来源标识。
            stage: 阶段（"running" 时显示动画 spinner）。
            frame: 动画帧编号（用于 spinner/进度条）。

        Returns:
            工具调用行的 Rich Text renderable。
        """
        normalized_stage = (stage or "running").strip().lower()
        tool_text = Text()
        
        if normalized_stage == "running":
            spinner = AnimationManager.spinner_char(frame)
            tool_text.append(f"  {spinner} ", style=self._theme.accent)
        else:
            tool_text.append("  ⚙ ", style=self._theme.accent)
        
        tool_text.append(f"{name}", style=f"bold {self._theme.accent}")
        if args_summary:
            tool_text.append(f" {args_summary}", style=self._theme.dim)
        if normalized_stage == "planning":
            tool_text.append("  ...", style=self._theme.warning)
        return tool_text

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
        shimmer_offset: int = 0,
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
                shimmer_offset=shimmer_offset,
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
        shimmer_offset: int = 0,
    ) -> Panel:
        """构建 research 进度面板，避免直接写入终端。

        Args:
            shimmer_offset: 流光偏移位置（0 表示不显示流光效果）。
        """
        safe_total = max(total, 1)
        safe_completed = max(0, min(completed, safe_total))
        bar_width = 24
        filled = int((safe_completed / safe_total) * bar_width)

        # 构建带流光效果的进度条
        if shimmer_offset > 0 and filled > 0:
            bar = self._build_shimmer_bar(filled, bar_width, shimmer_offset)
        else:
            bar = "█" * filled + "·" * (bar_width - filled)

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

    @staticmethod
    def _build_shimmer_bar(filled: int, bar_width: int, shimmer_offset: int) -> str:
        """构建带流光效果的进度条。

        已填充部分使用 █/▓/▒ 混合渲染，产生移动亮斑效果。

        Args:
            filled: 已填充格数。
            bar_width: 进度条总宽度。
            shimmer_offset: 流光亮斑的起始位置。

        Returns:
            进度条字符串。
        """
        chars: list[str] = []
        shimmer_len = 3  # 亮斑宽度
        for i in range(bar_width):
            if i >= filled:
                chars.append("·")
            else:
                # 计算相对于亮斑的位置
                pos = i - shimmer_offset
                # 规范到 [0, bar_width) 区间处理循环
                if pos < 0:
                    pos += bar_width
                if 0 <= pos < shimmer_len:
                    # 亮斑区域：▓ █ ▓ 渐变
                    if pos == 1:
                        chars.append("█")
                    else:
                        chars.append("▓")
                else:
                    chars.append("▒")
        return "".join(chars)

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
            self._print(Text(collapsed, style=self._theme.fg))
        elif exit_code is None and len(lines) > 20:
            # 无 exit_code，保守截断
            self._print(Text("\n".join(lines[:10]), style=self._theme.fg))
        elif exit_code is not None and exit_code != 0:
            # 失败输出，完整展开（上限 80 行）
            if len(lines) > 80:
                output = "\n".join(lines[:80])
                output += f"\n... (输出已截断，共 {len(lines)} 行)"
            self._print(Text(output, style=self._theme.fg))
        else:
            # 其他情况（exit_code=0 且 <=5行，或 exit_code=None 且 <=20行）保持原样
            self._print(Text(output, style=self._theme.fg))

    def build_bash_fold_line(
        self, total_lines: int, expanded: bool = False, frame: int = 0
    ) -> RenderableType:
        """构建 bash 输出折叠/展开行，支持箭头动画。

        Args:
            total_lines: 输出总行数。
            expanded: 当前是否展开（True=▾, False=▸）。
            frame: 动画帧编号（用于折叠箭头动画）。

        Returns:
            折叠/展开行的 Rich Text renderable。
        """
        arrow = AnimationManager.fold_arrow(frame, expanded)
        state_label = "bash output"
        text = Text()
        text.append(f"  {arrow} ", style=self._theme.dim)
        text.append(f"{state_label}", style=self._theme.dim)
        text.append(f"  ({total_lines} lines)", style=self._theme.dim)
        return text

    def get_bash_fold_info(
        self, output: str, is_stderr: bool, exit_code: int | None
    ) -> tuple[int, bool]:
        """判断 bash 输出是否需要折叠。

        Returns:
            (total_lines, is_folded): 总行数和是否需要折叠指示器。
        """
        if is_stderr:
            return (0, False)
        lines = output.splitlines()
        if exit_code == 0 and len(lines) > 5:
            return (len(lines), True)
        if exit_code is None and len(lines) > 20:
            return (len(lines), True)
        return (0, False)

    def render_file_change(self, path: str, action: str, diff: str = "") -> None:
        """显示文件变更通知。"""
        change_text = self.build_file_change_animated(path, action, frame=0)
        self._print(change_text)

        if diff:
            self.render_code_diff(path, diff)

    def build_file_change_animated(
        self, path: str, action: str, frame: int = 0, style: str = "mofox"
    ) -> RenderableType:
        """构建文件变更行 renderable，支持 checkmark 弹出动画。

        Args:
            path: 文件路径。
            action: 变更操作（create/modify/delete）。
            frame: 动画帧编号。
            style: checkmark 风格，"classic" 或 "mofox"（默认 mofox ◆◇◆）。

        Returns:
            文件变更行的 Rich Text renderable。
        """
        icons = {"create": "+", "modify": "~", "delete": "-"}
        icon = icons.get(action, "*")
        action_colors = {
            "create": self._theme.success,
            "modify": self._theme.warning,
            "delete": self._theme.error,
        }
        color = action_colors.get(action, self._theme.dim)

        # 动画帧：用 checkmark_pop 字符替换静态图标
        anim_icon = AnimationManager.checkmark_pop(frame, style)

        change_text = Text()
        change_text.append(f"  [{anim_icon}] ", style=color)
        change_text.append(f"{action}: ", style=self._theme.dim)
        change_text.append(path, style=color)
        return change_text

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

    @staticmethod
    def _format_tokens(count: int) -> str:
        """将 token 数量格式化为人类可读字符串。"""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}k"
        return str(count)

    @staticmethod
    def _format_cost(cost: float) -> str:
        """将花费格式化为人类可读字符串。"""
        if cost == 0.0:
            return "$0"
        if cost < 0.01:
            return f"${cost:.4f}"
        return f"${cost:.3f}"

    def render_task_summary(self, records: list[dict]) -> None:
        """渲染本轮任务用量统计。

        按 model_name 分组汇总，显示输入 tokens、cache hit tokens、输出 tokens 和花费。
        """
        if not records:
            return

        # 按 model_name 分组汇总
        aggregated: dict[str, dict] = {}
        for rec in records:
            model = rec.get("model_name", "") or "unknown"
            if model not in aggregated:
                aggregated[model] = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_hit_tokens": 0,
                    "cost": 0.0,
                }
            aggregated[model]["prompt_tokens"] += rec.get("prompt_tokens", 0)
            aggregated[model]["completion_tokens"] += rec.get("completion_tokens", 0)
            aggregated[model]["cache_hit_tokens"] += rec.get("cache_hit_tokens", 0)
            aggregated[model]["cost"] += rec.get("cost", 0.0)

        # 构建内容
        content = Text()
        content.append("📊 本次用量统计\n", style=f"bold {self._theme.accent}")

        for model_name, stats in aggregated.items():
            prompt = stats["prompt_tokens"]
            completion = stats["completion_tokens"]
            cache_hit = stats["cache_hit_tokens"]
            cost = stats["cost"]

            line = f"  🤖 {model_name}  "
            content.append(line, style=self._theme.fg)
            content.append(
                f"输入: {self._format_tokens(prompt)} tokens",
                style=self._theme.fg,
            )
            if cache_hit > 0:
                content.append(
                    f" (cache hit: {self._format_tokens(cache_hit)})",
                    style=self._theme.dim,
                )
            content.append(
                f"  输出: {self._format_tokens(completion)} tokens",
                style=self._theme.fg,
            )
            if cost > 0:
                content.append(
                    f"  花费: {self._format_cost(cost)}",
                    style=self._theme.warning,
                )
            content.append("\n")

        # 去掉末尾换行
        # 使用 Rule 或分隔线包裹
        from rich.rule import Rule
        self._print(Rule(style=self._theme.dim))
        self._print(content)
        self._print(Rule(style=self._theme.dim))
