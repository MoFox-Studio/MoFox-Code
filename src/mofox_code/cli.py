"""CLI 入口。"""

from __future__ import annotations

import argparse
import asyncio

from .config import ClientConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mofox-code",
        description="Neo-MoFox Coding Agent TUI",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="后端 WebSocket 地址 (默认: ws://localhost:8765/coding-agent/ws)",
    )
    parser.add_argument(
        "--auto-review",
        action="store_true",
        default=False,
        help="启动时开启自动命令审查模式",
    )
    parser.add_argument(
        "--theme",
        choices=["dark", "light", "monokai"],
        default=None,
        help="配色主题 (默认: dark)",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="项目目录 (默认: 当前目录)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="显示详细调试信息",
    )
    return parser.parse_args()


def main() -> None:
    """CLI 入口。"""
    args = parse_args()
    config = ClientConfig.load(overrides={
        "server_url": args.server,
        "auto_review": args.auto_review,
        "theme": args.theme,
        "project_dir": args.project_dir,
        "verbose": args.verbose,
    })

    from .tui.app import TUIApp
    app = TUIApp(config)
    asyncio.run(app.run())
