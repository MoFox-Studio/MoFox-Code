"""用户输入处理。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from prompt_toolkit.completion import Completer, Completion
from rich.console import Console, RenderableType

from .themes import Theme


class CommandCompleter(Completer):
    """Tab 补全器，为 / 开头的命令提供补全。"""
    
    COMMANDS = [
        "/exit", "/clear", "/undo", "/rollback", "/history",
        "/status", "/auto-review on", "/auto-review off",
        "/help", "/session", "/session resume", "/session delete",
        "/link", "/new", "/yolo", "/goal",
    ]
    
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            for cmd in self.COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))


class BuiltinCommandType(str, Enum):
    """内置命令类型。"""
    EXIT = "exit"
    CLEAR = "clear"
    UNDO = "undo"
    ROLLBACK = "rollback"
    HISTORY = "history"
    STATUS = "status"
    AUTO_REVIEW = "auto_review"
    YOLO = "yolo"
    GOAL = "goal"
    HELP = "help"
    SESSION = "session"
    LINK = "link"
    NEW = "new"
    NONE = "none"  # 非内置命令


@dataclass
class BuiltinCommandResult:
    """内置命令解析结果。"""
    type: BuiltinCommandType
    args: dict = field(default_factory=dict)


class InputHandler:
    """用户输入处理器。

    输入获取由 layout_manager.get_input() 驱动，
    本类只负责命令解析和帮助/历史渲染。
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

    def is_builtin_command(self, text: str) -> bool:
        """判断是否是内置命令。"""
        return text.startswith("/")

    def parse_builtin_command(self, text: str) -> BuiltinCommandResult:
        """解析内置命令。

        /exit, /quit → EXIT
        /clear → CLEAR
        /undo → UNDO
        /rollback → ROLLBACK
        /history → HISTORY
        /status → STATUS
        /auto-review on → AUTO_REVIEW {enabled: True}
        /auto-review off → AUTO_REVIEW {enabled: False}
        /help → HELP
        其他 / 开头 → NONE（不认识的命令）
        """
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        match cmd:
            case "/exit" | "/quit":
                return BuiltinCommandResult(BuiltinCommandType.EXIT)
            case "/clear":
                return BuiltinCommandResult(BuiltinCommandType.CLEAR)
            case "/undo":
                return BuiltinCommandResult(BuiltinCommandType.UNDO)
            case "/rollback":
                return BuiltinCommandResult(BuiltinCommandType.ROLLBACK)
            case "/history":
                return BuiltinCommandResult(BuiltinCommandType.HISTORY)
            case "/status":
                return BuiltinCommandResult(BuiltinCommandType.STATUS)
            case "/auto-review":
                enabled = arg.lower() in ("on", "true", "1", "yes")
                return BuiltinCommandResult(
                    BuiltinCommandType.AUTO_REVIEW, {"enabled": enabled}
                )
            case "/session":
                sub = arg.strip().lower()
                if sub in ("", "list"):
                    return BuiltinCommandResult(
                        BuiltinCommandType.SESSION, {"action": "list"}
                    )
                if sub.startswith("resume"):
                    sid = sub[len("resume"):].strip()
                    return BuiltinCommandResult(
                        BuiltinCommandType.SESSION,
                        {"action": "resume", "session_id": sid},
                    )
                if sub.startswith("delete"):
                    sid = sub[len("delete"):].strip()
                    return BuiltinCommandResult(
                        BuiltinCommandType.SESSION,
                        {"action": "delete", "session_id": sid},
                    )
                return BuiltinCommandResult(BuiltinCommandType.NONE)
            case "/link":
                if arg.strip():
                    return BuiltinCommandResult(
                        BuiltinCommandType.LINK, {"path": arg.strip()}
                    )
                return BuiltinCommandResult(BuiltinCommandType.NONE)
            case "/new":
                return BuiltinCommandResult(BuiltinCommandType.NEW)
            case "/yolo":
                return BuiltinCommandResult(BuiltinCommandType.YOLO)
            case cmd if cmd.startswith("/goal"):
                goal_text = arg.strip()
                if not goal_text:
                    return BuiltinCommandResult(BuiltinCommandType.NONE)
                return BuiltinCommandResult(BuiltinCommandType.GOAL, {"text": goal_text})
            case "/help":
                return BuiltinCommandResult(BuiltinCommandType.HELP)
            case _:
                return BuiltinCommandResult(BuiltinCommandType.NONE)

    def render_help(self) -> None:
        """显示帮助信息。"""
        from rich.table import Table

        table = Table(title="快捷命令", show_header=True)
        table.add_column("命令", style="bold")
        table.add_column("说明")
        table.add_row("/exit", "退出程序")
        table.add_row("/undo", "回滚最后一步操作")
        table.add_row("/rollback", "选择回滚到指定步骤")
        table.add_row("/history", "查看操作历史")
        table.add_row("/status", "查看当前状态")
        table.add_row("/auto-review on/off", "切换自动命令审查")
        table.add_row("/yolo", "切换无审查模式（跳过所有命令审批）")
        table.add_row("/goal <目标>", "进入目标模式，自动审查直到达标")
        table.add_row("/clear", "清屏")
        table.add_row("/link <路径>", "关联外部项目目录")
        table.add_row("/new", "开启新对话")
        table.add_row("/session", "列出/恢复/删除历史会话")
        table.add_row("直接输入", "Agent 工作中可追加补充引导，当前回合结束后处理")
        table.add_row("/help", "显示此帮助")
        self._print(table)

    def render_checkpoint_list(self, checkpoints: list) -> None:
        """显示 checkpoint 列表供回滚选择。"""
        from rich.table import Table

        table = Table(title="⏪ 操作历史", show_header=True)
        table.add_column("#", style="bold")
        table.add_column("工具")
        table.add_column("描述")
        table.add_column("状态")
        for cp in checkpoints:
            status = "✅ 可回滚" if cp.reversible else "⚠️ 不可逆"
            table.add_row(str(cp.step), cp.tool, cp.description, status)
        self._print(table)
