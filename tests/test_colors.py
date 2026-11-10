from __future__ import annotations

import unittest

from thinktrellis.colors import contrast_ratio, readable_text_color, safe_hex_color
from thinktrellis.model import DEFAULT_NODE_COLORS


class ColorTests(unittest.TestCase):
    def test_safe_hex_color_accepts_only_six_digit_hex(self) -> None:
        self.assertEqual(safe_hex_color("#3B82F6"), "#3B82F6")
        self.assertEqual(safe_hex_color("3B82F6"), "#3B82F6")
        self.assertEqual(safe_hex_color("azul", "#123456"), "#123456")
        self.assertEqual(safe_hex_color("#12", "#123456"), "#123456")
        self.assertEqual(safe_hex_color("#GGGGGG", "#123456"), "#123456")

    def test_contrast_ratio_extremes(self) -> None:
        self.assertAlmostEqual(contrast_ratio("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(contrast_ratio("#808080", "#808080"), 1.0, places=3)

    def test_dark_and_medium_backgrounds_get_white_text(self) -> None:
        for color in ("#000080", "#2563EB", "#8B5CF6", "#64748B", "#000000"):
            self.assertEqual(readable_text_color(color), "#FFFFFF", color)

    def test_light_backgrounds_get_black_text(self) -> None:
        for color in ("#FFFFFF", "#EAB308", "#10B981", "#F97316", "#C0C0C0"):
            self.assertEqual(readable_text_color(color), "#000000", color)

    def test_invalid_color_does_not_crash(self) -> None:
        self.assertIn(readable_text_color("não é cor"), ("#000000", "#FFFFFF"))

    def test_every_default_node_color_is_readable(self) -> None:
        for color in DEFAULT_NODE_COLORS:
            text_color = readable_text_color(color)
            self.assertGreaterEqual(contrast_ratio(text_color, color), 2.5, color)


if __name__ == "__main__":
    unittest.main()
