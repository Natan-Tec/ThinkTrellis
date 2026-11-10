from __future__ import annotations

import re
import unittest

from thinktrellis.app import DEFAULT_THEME, THEMES
from thinktrellis.colors import contrast_ratio

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ThemeTests(unittest.TestCase):
    def test_default_theme_exists(self) -> None:
        self.assertIn(DEFAULT_THEME, THEMES)

    def test_all_themes_define_the_same_keys(self) -> None:
        keys = [set(palette) for palette in THEMES.values()]
        self.assertTrue(all(item == keys[0] for item in keys))

    def test_every_color_is_a_valid_hex_value(self) -> None:
        for theme, palette in THEMES.items():
            for key, value in palette.items():
                if key == "name":
                    continue
                self.assertRegex(value, HEX, f"{theme}.{key}")

    def test_text_is_readable_on_every_surface(self) -> None:
        for theme, palette in THEMES.items():
            self.assertGreaterEqual(contrast_ratio(palette["text"], palette["surface"]), 7, theme)
            self.assertGreaterEqual(contrast_ratio(palette["text"], palette["field"]), 7, theme)
            self.assertGreaterEqual(contrast_ratio(palette["muted"], palette["surface"]), 4.5, theme)
            self.assertGreaterEqual(contrast_ratio(palette["primary_text"], palette["primary"]), 4.5, theme)
            self.assertGreaterEqual(contrast_ratio(palette["danger"], palette["surface"]), 4.5, theme)

    def test_borders_are_visible_but_soft(self) -> None:
        for theme, palette in THEMES.items():
            ratio = contrast_ratio(palette["border"], palette["surface"])
            self.assertGreater(ratio, 1.1, theme)
            self.assertLess(ratio, 3, theme)


if __name__ == "__main__":
    unittest.main()
