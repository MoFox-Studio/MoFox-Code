"""测试会话累计用量：_on_context_usage 累计/非累计、session 切换清空。"""

import unittest

from rich.console import Console

from mofox_code.config import ClientConfig
from mofox_code.tui.app import TUIApp
from mofox_code.tui.themes import DARK_THEME


class ContextUsageCumulativeTest(unittest.IsolatedAsyncioTestCase):
    """测试 _on_context_usage 处理 is_cumulative 标志。"""

    def setUp(self) -> None:
        # 使用 record=True 的 console 避免终端输出
        self._dummy_console = Console(record=True, width=120)
        # 在 __init__ 里初始化 app，console 通过 monkey-patch 注入
        self.app = TUIApp(ClientConfig())
        # 注入 record console 避免实际终端输出
        self.app._console = self._dummy_console
        self.app._renderer._console = self._dummy_console

    async def test_cumulative_replaces_by_model(self) -> None:
        """is_cumulative=True 时直接按 model_name 替换（不追加）。"""
        # 先设一个初始值
        self.app._session_usage = {
            "deepseek-v4-pro": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
                "cache_hit_tokens": 200,
                "cost": 0.001,
            }
        }

        # 发送累计值（后端累计到现在的总和）
        await self.app._on_context_usage({
            "model_name": "deepseek-v4-pro",
            "is_cumulative": True,
            "prompt_tokens": 85000,
            "completion_tokens": 2500,
            "total_tokens": 87500,
            "cache_hit_tokens": 12000,
            "cost": 0.023,
        })

        usage = self.app._session_usage
        self.assertIn("deepseek-v4-pro", usage)
        self.assertEqual(usage["deepseek-v4-pro"]["prompt_tokens"], 85000)
        self.assertEqual(usage["deepseek-v4-pro"]["completion_tokens"], 2500)
        self.assertEqual(usage["deepseek-v4-pro"]["total_tokens"], 87500)
        self.assertEqual(usage["deepseek-v4-pro"]["cache_hit_tokens"], 12000)
        self.assertEqual(usage["deepseek-v4-pro"]["cost"], 0.023)

    async def test_non_cumulative_appends(self) -> None:
        """is_cumulative=False 时按旧逻辑累加到现有值。"""
        self.app._session_usage = {
            "deepseek-v4-pro": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
                "cache_hit_tokens": 200,
                "cost": 0.001,
            }
        }

        # 发送增量值
        await self.app._on_context_usage({
            "model_name": "deepseek-v4-pro",
            "is_cumulative": False,
            "prompt_tokens": 500,
            "completion_tokens": 200,
            "total_tokens": 700,
            "cache_hit_tokens": 100,
            "cost": 0.0005,
        })

        usage = self.app._session_usage
        self.assertEqual(usage["deepseek-v4-pro"]["prompt_tokens"], 1500)
        self.assertEqual(usage["deepseek-v4-pro"]["completion_tokens"], 700)
        self.assertEqual(usage["deepseek-v4-pro"]["total_tokens"], 2200)
        self.assertEqual(usage["deepseek-v4-pro"]["cache_hit_tokens"], 300)
        self.assertEqual(usage["deepseek-v4-pro"]["cost"], 0.0015)

    async def test_cumulative_adds_new_model(self) -> None:
        """is_cumulative=True 时新模型直接写入 dict。"""
        self.app._session_usage = {}

        await self.app._on_context_usage({
            "model_name": "claude-sonnet-4",
            "is_cumulative": True,
            "prompt_tokens": 50000,
            "completion_tokens": 3000,
            "total_tokens": 53000,
            "cache_hit_tokens": 0,
            "cost": 0.08,
        })

        self.assertIn("claude-sonnet-4", self.app._session_usage)
        self.assertEqual(self.app._session_usage["claude-sonnet-4"]["prompt_tokens"], 50000)

    async def test_cumulative_overwrites_across_models(self) -> None:
        """累计模式下多次发送同一模型的不同值，最后一次覆盖。"""
        self.app._session_usage = {}

        await self.app._on_context_usage({
            "model_name": "gpt-4o",
            "is_cumulative": True,
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "total_tokens": 1100,
            "cache_hit_tokens": 0,
            "cost": 0.001,
        })

        # 后端累计增加到 3000
        await self.app._on_context_usage({
            "model_name": "gpt-4o",
            "is_cumulative": True,
            "prompt_tokens": 3000,
            "completion_tokens": 300,
            "total_tokens": 3300,
            "cache_hit_tokens": 500,
            "cost": 0.003,
        })

        usage = self.app._session_usage["gpt-4o"]
        self.assertEqual(usage["prompt_tokens"], 3000)
        self.assertEqual(usage["completion_tokens"], 300)
        self.assertEqual(usage["total_tokens"], 3300)


class SessionSwitchClearsUsageTest(unittest.IsolatedAsyncioTestCase):
    """测试 session 切换时清空 _session_usage。"""

    def setUp(self) -> None:
        self._dummy_console = Console(record=True, width=120)
        self.app = TUIApp(ClientConfig())
        self.app._console = self._dummy_console
        self.app._renderer._console = self._dummy_console

    async def test_session_switch_clears_usage(self) -> None:
        """session_id 变化时 _session_usage 被清空。"""
        # 模拟已有用量和当前 session
        self.app._session_usage = {
            "deepseek-v4-pro": {
                "prompt_tokens": 5000,
                "completion_tokens": 500,
                "total_tokens": 5500,
                "cache_hit_tokens": 0,
                "cost": 0.005,
            }
        }
        self.app._last_session_id = "session-old-123"

        # 收到新 session 的 session.ready
        await self.app._on_session_ready({
            "session_id": "session-new-456",
            "title": "新对话",
        })

        # _session_usage 应该被清空
        self.assertEqual(self.app._session_usage, {})
        self.assertEqual(self.app._last_session_id, "session-new-456")

    async def test_same_session_preserves_usage(self) -> None:
        """同一个 session_id 不清空用量（如重连恢复）。"""
        self.app._session_usage = {
            "deepseek-v4-pro": {
                "prompt_tokens": 5000,
                "completion_tokens": 500,
                "total_tokens": 5500,
                "cache_hit_tokens": 0,
                "cost": 0.005,
            }
        }
        self.app._last_session_id = "session-same-789"

        # 收到同一个 session 的 session.ready（如重连）
        await self.app._on_session_ready({
            "session_id": "session-same-789",
            "title": "恢复的对话",
        })

        # _session_usage 应该保持不变
        self.assertIn("deepseek-v4-pro", self.app._session_usage)
        self.assertEqual(self.app._session_usage["deepseek-v4-pro"]["prompt_tokens"], 5000)

    async def test_first_session_ready_does_not_clear(self) -> None:
        """首次 session.ready（_last_session_id 为 None）不清空。"""
        self.app._session_usage = {}
        self.app._last_session_id = None

        await self.app._on_session_ready({
            "session_id": "first-session-abc",
            "title": "",
        })

        # 首次不应该触发清空逻辑
        self.assertEqual(self.app._session_usage, {})
        self.assertEqual(self.app._last_session_id, "first-session-abc")
