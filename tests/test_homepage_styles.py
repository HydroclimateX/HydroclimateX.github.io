"""Homepage visual regression checks."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HomepageStyleTests(unittest.TestCase):
    def test_extreme_photo_has_no_colored_frame_shadow(self) -> None:
        css = (ROOT / "style.css").read_text(encoding="utf-8")
        match = re.search(r"\.extreme-card img\s*\{(?P<body>.*?)\}", css, re.DOTALL)

        self.assertIsNotNone(match, "missing .extreme-card img rule")
        self.assertNotIn("box-shadow", match.group("body"))


if __name__ == "__main__":
    unittest.main()
