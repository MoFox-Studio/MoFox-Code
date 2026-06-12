"""测试 /usage 命令触发 render_session_usage。"""

import unittest

from rich.console import Console

from mofox_code.tui.input_handler import InputHandler, BuiltinCommandType, BuiltinCommandResult
from mofox_code.tui.themes import DARK_THEME


class UsageCommandParsingTest(unittest.TestCase):
    """测试 /usage 命令解析。"""

    def setUp(self) -> None:
        self.console = Console(record=True, width=120)
        self.outputs: list[str] = []

        def capture(renderable) -> None:
            with self.console.capture() as cap:
                self.console.print(renderable)
            self.outputs.append(cap.get())

        self.handler = InputHandler(self.console, DARK_THEME, output=capture)

    def test_parse_usage_command(self) -> None:
        """解析 /usage 命令。"""
        self.assertTrue(self.handler.is_builtin_command("/usage"))
        result = self.handler.parse_builtin_command("/usage")
        self.assertEqual(result.type, BuiltinCommandType.USAGE)

    def test_parse_usage_with_extra_text(self) -> None:
        """解析 /usage 加额外文本（忽略多余部分）。"""
        result = self.handler.parse_builtin_command("/usage extra")
        self.assertEqual(result.type, BuiltinCommandType.USAGE)


class UsageCommandHelpTest(unittest.TestCase):
    """测试 /usage 出现在帮助中。"""

    def setUp(self) -> None:
        self.console = Console(record=True, width=120)
        self.outputs: list[str] = []

        def capture(renderable) -> None:
            with self.console.capture() as cap:
                self.console.print(renderable)
            self.outputs.append(cap.get())

        self.handler = InputHandler(self.console, DARK_THEME, output=capture)

    def test_help_includes_usage(self) -> None:
        """帮助信息中包含 /usage。"""
        self.handler.render_help()
        combined = "\n".join(self.outputs)
        self.assertIn("/usage", combined)
        self.assertIn("会话累计用量", combined)


class UsageCommandEmptyTest(unittest.TestCase):
    """测试空 _session_usage 时 render_session_usage 不渲染。"""

    def test_empty_dict_skips_render(self) -> None:
        """空 dict 不产生任何输出。"""
        from mofox_code.tui.renderer import TUIRenderer

        console = Console(record=True, width=120)
        outputs: list[str] = []

        def capture(renderable) -> None:
            with console.capture() as cap:
                console.print(renderable)
            outputs.append(cap.get())

        renderer = TUIRenderer(console, DARK_THEME, output=capture)
        renderer.render_session_usage({})
        self.assertEqual(len(outputs), 0)
