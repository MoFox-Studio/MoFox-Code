"""Bash 命令审批交互 UI。"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .themes import Theme


@dataclass
class ApprovalResult:
    """审批结果。"""
    decision: str   # "allow_once" | "allow_session" | "allow_forever" | "deny" | "auto_review_enable"
    prefix: str     # 用于 session/forever 规则的命令前缀


class ApprovalUI:
    """终端内的 Bash 命令审批交互组件。"""

    def __init__(self, console: Console, theme: Theme) -> None:
        self._console = console
        self._theme = theme

    def build_approval_request(
        self,
        command: str,
        working_dir: str,
        context: str,
        auto_review_result: dict | None = None,
    ) -> Panel:
        """构建审批请求面板，供 TUI 内联渲染。"""
        prefix = self._extract_prefix(command)

        table = Table.grid(padding=(0, 1))
        table.add_row(Text("命令", style=self._theme.dim), Text(command, style="bold"))
        table.add_row(Text("目录", style=self._theme.dim), Text(working_dir or "-"))
        if context:
            table.add_row(Text("上下文", style=self._theme.dim), Text(context, style=self._theme.fg))

        if auto_review_result:
            safe = auto_review_result.get("safe", False)
            reason = auto_review_result.get("reason", "")
            conf = auto_review_result.get("confidence", 0)
            icon = "✅" if safe else "⚠️"
            label = "安全" if safe else "不确定"
            review_text = Text()
            review_text.append(
                f"{icon} {label} ({conf:.2f})",
                style=self._theme.success if safe else self._theme.warning,
            )
            if reason:
                review_text.append(f" — {reason}", style=self._theme.dim)
            table.add_row(Text("自动审查", style=self._theme.dim), review_text)

        options = Text()
        options.append("[a] 允许一次  ", style=self._theme.success)
        options.append(f"[s] 会话允许 {prefix}  ", style=self._theme.accent)
        options.append(f"[f] 永久允许 {prefix}  ", style=self._theme.warning)
        options.append("[d] 拒绝  ", style=self._theme.error)
        options.append("[r] 开启自动审查", style=self._theme.dim)

        return Panel(
            Group(table, Text(""), options),
            title="🔐 命令审批",
            border_style=self._theme.panel_border,
            expand=True,
        )

    def parse_choice(self, choice: str, command: str) -> ApprovalResult | None:
        """解析审批输入。无效输入返回 None。"""
        prefix = self._extract_prefix(command)
        normalized = choice.strip().lower()
        match normalized:
            case "a":
                return ApprovalResult(decision="allow_once", prefix="")
            case "s":
                return ApprovalResult(decision="allow_session", prefix=prefix)
            case "f":
                return ApprovalResult(decision="allow_forever", prefix=prefix)
            case "d":
                return ApprovalResult(decision="deny", prefix="")
            case "r":
                return ApprovalResult(decision="auto_review_enable", prefix="")
            case _:
                return None

    async def show_approval_request(
        self,
        command: str,
        working_dir: str,
        context: str,
        auto_review_result: dict | None = None,
    ) -> ApprovalResult:
        """显示审批请求面板，等待用户输入。

        Args:
            command: 待审批的 shell 命令
            working_dir: 命令执行的工作目录
            context: 上下文说明
            auto_review_result: 自动审查结果（如有）

        Returns:
            ApprovalResult 包含用户决策和命令前缀
        """
        panel = self.build_approval_request(
            command=command,
            working_dir=working_dir,
            context=context,
            auto_review_result=auto_review_result,
        )
        self._console.print(panel)

        # 等待用户输入
        while True:
            try:
                choice = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return ApprovalResult(decision="deny", prefix="")

            result = self.parse_choice(choice, command)
            if result is not None:
                return result
            self._console.print("  请输入 a/s/f/d/r", style=self._theme.dim)

    @staticmethod
    def _extract_prefix(command: str) -> str:
        """提取命令的第一个 token 作为前缀。

        "git diff HEAD~1" → "git"
        "uv run pytest -v" → "uv"
        "python -m myapp" → "python"
        """
        tokens = command.strip().split()
        return tokens[0] if tokens else command.strip()
