"""TUI 主题配色。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """TUI 配色主题。"""
    name: str

    # 基础色
    bg: str            # 背景色（仅供参考，终端通常使用自身背景）
    fg: str            # 前景色
    dim: str           # 次要文本色
    accent: str        # 强调色
    success: str       # 成功色
    warning: str       # 警告色
    error: str         # 错误色

    # 语义色
    agent_text: str    # Agent 回复文本
    user_prompt: str   # 用户输入提示符
    thinking: str      # 思考过程
    status_bar_bg: str # 状态栏背景
    status_bar_fg: str # 状态栏前景
    panel_border: str  # 面板边框
    diff_add: str      # Diff 新增行
    diff_remove: str   # Diff 删除行

    # 代码高亮主题名（Rich Syntax 用）
    code_theme: str


DARK_THEME = Theme(
    name="dark",
    bg="#1e1e2e", fg="#cdd6f4", dim="#6c7086", accent="#89b4fa",
    success="#a6e3a1", warning="#fab387", error="#f38ba8",
    agent_text="#cdd6f4", user_prompt="#89b4fa", thinking="#6c7086",
    status_bar_bg="#313244", status_bar_fg="#cdd6f4",
    panel_border="#45475a", diff_add="#a6e3a1", diff_remove="#f38ba8",
    code_theme="monokai",
)

LIGHT_THEME = Theme(
    name="light",
    bg="#eff1f5", fg="#4c4f69", dim="#9ca0b0", accent="#1e66f5",
    success="#40a02b", warning="#df8e1d", error="#d20f39",
    agent_text="#4c4f69", user_prompt="#1e66f5", thinking="#9ca0b0",
    status_bar_bg="#ccd0da", status_bar_fg="#4c4f69",
    panel_border="#bcc0cc", diff_add="#40a02b", diff_remove="#d20f39",
    code_theme="github-dark",
)

MONOKAI_THEME = Theme(
    name="monokai",
    bg="#272822", fg="#f8f8f2", dim="#75715e", accent="#66d9ef",
    success="#a6e22e", warning="#fd971f", error="#f92672",
    agent_text="#f8f8f2", user_prompt="#66d9ef", thinking="#75715e",
    status_bar_bg="#3e3d32", status_bar_fg="#f8f8f2",
    panel_border="#49483e", diff_add="#a6e22e", diff_remove="#f92672",
    code_theme="monokai",
)


THEMES: dict[str, Theme] = {
    "dark": DARK_THEME,
    "light": LIGHT_THEME,
    "monokai": MONOKAI_THEME,
}


def get_theme(name: str) -> Theme:
    return THEMES.get(name, DARK_THEME)
