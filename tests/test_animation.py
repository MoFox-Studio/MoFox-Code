"""AnimationManager 动画原语单元测试。"""

import unittest

from mofox_code.tui.animation import AnimationManager


class BreathingDotsTest(unittest.TestCase):
    """breathing_dots 各风格帧序列测试。"""

    def test_classic_cycle(self) -> None:
        """classic: '.' → '..' → '...' → '' 循环。"""
        results = [AnimationManager.breathing_dots(f, "classic") for f in range(16)]
        # 每 2 帧切换一次，4 相循环（'', '.', '..', '...'）
        self.assertEqual(results[0], "")
        self.assertEqual(results[1], "")
        self.assertEqual(results[2], ".")
        self.assertEqual(results[3], ".")
        self.assertEqual(results[4], "..")
        self.assertEqual(results[5], "..")
        self.assertEqual(results[6], "...")
        self.assertEqual(results[7], "...")
        self.assertEqual(results[8], "")  # 循环回到开头

    def test_classic_is_dots_equivalent(self) -> None:
        """classic 风格与 dots(frame, 3) 输出一致。"""
        for f in range(20):
            self.assertEqual(
                AnimationManager.breathing_dots(f, "classic"),
                AnimationManager.dots(f, 3),
            )

    def test_moe_cycle(self) -> None:
        """moe: '·' → '⋅' → '∙' → '⋅' → '' 循环。"""
        results = [AnimationManager.breathing_dots(f, "moe") for f in range(16)]
        # 每 2 帧切换，4 相循环：'', '·', '⋅', '∙'
        self.assertEqual(results[0], "")
        self.assertEqual(results[1], "")
        self.assertEqual(results[2], "·")
        self.assertEqual(results[3], "·")
        self.assertEqual(results[4], "⋅")
        self.assertEqual(results[5], "⋅")
        self.assertEqual(results[6], "∙")
        self.assertEqual(results[7], "∙")
        self.assertEqual(results[8], "")  # 循环回到开头

    def test_star_cycle(self) -> None:
        """star: '✦' → '✧' → '' → '✦' 循环。"""
        results = [AnimationManager.breathing_dots(f, "star") for f in range(18)]
        # 每 3 帧切换，3 相循环：'', '✦', '✧'
        self.assertEqual(results[0], "")
        self.assertEqual(results[1], "")
        self.assertEqual(results[2], "")
        self.assertEqual(results[3], "✦")
        self.assertEqual(results[4], "✦")
        self.assertEqual(results[5], "✦")
        self.assertEqual(results[6], "✧")
        self.assertEqual(results[7], "✧")
        self.assertEqual(results[8], "✧")
        self.assertEqual(results[9], "")   # 循环回到开头

    def test_default_style_is_classic(self) -> None:
        """默认风格为 classic。"""
        self.assertEqual(
            AnimationManager.breathing_dots(4, "classic"),
            AnimationManager.breathing_dots(4),
        )


class PipelineBarTest(unittest.TestCase):
    """pipeline_bar 帧序列测试。"""

    def test_pipeline_bar_cycle(self) -> None:
        """验证 pipeline_bar 循环输出完整的 PIPELINE_FRAMES。"""
        for i in range(len(AnimationManager.PIPELINE_FRAMES)):
            expected = AnimationManager.PIPELINE_FRAMES[i]
            self.assertEqual(AnimationManager.pipeline_bar(i), expected)

    def test_pipeline_bar_wraps(self) -> None:
        """验证 frame 超出范围时正确 wrap。"""
        length = len(AnimationManager.PIPELINE_FRAMES)
        self.assertEqual(
            AnimationManager.pipeline_bar(0),
            AnimationManager.pipeline_bar(length),
        )
        self.assertEqual(
            AnimationManager.pipeline_bar(1),
            AnimationManager.pipeline_bar(length + 1),
        )


class CursorCharTest(unittest.TestCase):
    """cursor_char 帧序列测试。"""

    def test_cursor_char_cycle(self) -> None:
        """验证 cursor_char 循环输出完整的 CURSOR_FRAMES。"""
        for i in range(len(AnimationManager.CURSOR_FRAMES)):
            expected = AnimationManager.CURSOR_FRAMES[i]
            self.assertEqual(AnimationManager.cursor_char(i), expected)

    def test_cursor_char_wraps(self) -> None:
        """验证 frame 超出范围时正确 wrap。"""
        length = len(AnimationManager.CURSOR_FRAMES)
        self.assertEqual(
            AnimationManager.cursor_char(0),
            AnimationManager.cursor_char(length),
        )


class CheckmarkPopTest(unittest.TestCase):
    """checkmark_pop 各风格帧序列测试。"""

    def test_classic_frame_sequence(self) -> None:
        """classic 风格帧序列：✓ → ✔ → ✓ 循环。"""
        self.assertEqual(AnimationManager.checkmark_pop(0, "classic"), "✓")
        self.assertEqual(AnimationManager.checkmark_pop(1, "classic"), "✔")
        self.assertEqual(AnimationManager.checkmark_pop(2, "classic"), "✓")
        self.assertEqual(AnimationManager.checkmark_pop(3, "classic"), "✓")  # wrap

    def test_mofox_frame_sequence(self) -> None:
        """MoFox 风格帧序列：◆ → ◇ → ◆ 循环。"""
        self.assertEqual(AnimationManager.checkmark_pop(0, "mofox"), "◆")
        self.assertEqual(AnimationManager.checkmark_pop(1, "mofox"), "◇")
        self.assertEqual(AnimationManager.checkmark_pop(2, "mofox"), "◆")
        self.assertEqual(AnimationManager.checkmark_pop(3, "mofox"), "◆")  # wrap

    def test_default_style_is_classic(self) -> None:
        """默认风格为 classic。"""
        self.assertEqual(
            AnimationManager.checkmark_pop(0, "classic"),
            AnimationManager.checkmark_pop(0),
        )


class FoldArrowTest(unittest.TestCase):
    """fold_arrow 展开/折叠帧序列测试。"""

    def test_expanded_arrow_sequence(self) -> None:
        """展开状态：▾ → ▹ → ▾ 循环。"""
        self.assertEqual(AnimationManager.fold_arrow(0, expanded=True), "▾")
        self.assertEqual(AnimationManager.fold_arrow(1, expanded=True), "▹")
        self.assertEqual(AnimationManager.fold_arrow(2, expanded=True), "▾")
        self.assertEqual(AnimationManager.fold_arrow(3, expanded=True), "▾")  # wrap

    def test_collapsed_arrow_sequence(self) -> None:
        """折叠状态：▸ → ▹ → ▸ 循环。"""
        self.assertEqual(AnimationManager.fold_arrow(0, expanded=False), "▸")
        self.assertEqual(AnimationManager.fold_arrow(1, expanded=False), "▹")
        self.assertEqual(AnimationManager.fold_arrow(2, expanded=False), "▸")
        self.assertEqual(AnimationManager.fold_arrow(3, expanded=False), "▸")  # wrap

    def test_expanded_vs_collapsed_different(self) -> None:
        """展开和折叠的初始帧不同。"""
        self.assertNotEqual(
            AnimationManager.fold_arrow(0, expanded=True),
            AnimationManager.fold_arrow(0, expanded=False),
        )


if __name__ == "__main__":
    unittest.main()
