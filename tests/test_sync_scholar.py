"""Regression tests for the Google Scholar sync script and publication types."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_scholar import classify_publication, parse_rows  # noqa: E402

ALLOWED_TYPES = {"article", "conference", "software", "thesis", "book chapter"}

# A minimal Google Scholar row shaped like the parser expects.
SAMPLE_ROW = """
<tr class="gsc_a_tr">
  <td class="gsc_a_t">
    <a class="gsc_a_at" href="...">Refining predictor spectral representation</a>
  </td>
  <td class="gsc_a_c"><a ...>39</a></td>
  <td class="gsc_a_y"><span class="gsc_a_h gsc_a_hc gsc_rsb_std">2020</span></td>
  <td><div class="gs_gray">Z Jiang, A Sharma, F Johnson</div></td>
  <td><div class="gs_gray">Water Resources Research 56 (3), e2019WR026962</div></td>
</tr>
"""


class ClassifyPublicationTests(unittest.TestCase):
    def test_classifies_all_five_types(self) -> None:
        cases = [
            (
                "Water Resources Research 56 (3), e2019WR026962",
                "Refining predictor spectral representation using wavelet theory",
                "article",
            ),
            (
                "EGU General Assembly Conference Abstracts, EGU25-7620",
                "Enhancing Seasonal Flood Forecasts through Spectral Transformation",
                "conference",
            ),
            (
                "https://cran.r-project.org/web/packages/WASP/index.html",
                "WASP: WAvelet System Prediction",
                "software",
            ),
            (
                "University of New South Wales, Sydney",
                "Implications of spectral transformations in hydro-climatology (PhD Thesis)",
                "thesis",
            ),
            (
                "Towards a Resilient ASEAN 1, 37-52",
                "ASEAN Food Security under Climate Change",
                "book chapter",
            ),
        ]
        for venue, title, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_publication(venue, title), expected)

    def test_conference_marker_wins_over_book_marker(self) -> None:
        # SimHydro is a conference even though its proceedings were a book.
        self.assertEqual(
            classify_publication(
                "Advances in Hydroinformatics: SimHydro 2017-Choosing The Right Model in ...",
                "Flood modelling framework for Kuching City",
            ),
            "conference",
        )

    def test_empty_and_garbage_venues_default_to_article(self) -> None:
        self.assertEqual(classify_publication("", "A paper"), "article")
        self.assertEqual(classify_publication("", ""), "article")
        self.assertEqual(
            classify_publication(
                "< bound method Organization. get_name_with_acronym of< Organization ...",
                "ASEAN Food Security Under Climate Change Scenarios",
            ),
            "article",
        )


class ParseRowsTests(unittest.TestCase):
    def test_parse_rows_includes_type(self) -> None:
        pubs = parse_rows(SAMPLE_ROW)
        self.assertEqual(len(pubs), 1)
        self.assertEqual(pubs[0]["title"], "Refining predictor spectral representation")
        self.assertEqual(pubs[0]["year"], 2020)
        self.assertEqual(pubs[0]["citations"], 39)
        self.assertEqual(pubs[0]["type"], "article")

    def test_parse_rows_types_conference_row(self) -> None:
        row = SAMPLE_ROW.replace(
            "Water Resources Research 56 (3), e2019WR026962",
            "EGU General Assembly Conference Abstracts, EGU25-7620",
        )
        self.assertEqual(parse_rows(row)[0]["type"], "conference")


class PublicationsJsonTests(unittest.TestCase):
    def test_every_entry_has_a_valid_type(self) -> None:
        path = ROOT / "data" / "scholar-publications.json"
        entries = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreater(len(entries), 0)
        for entry in entries:
            with self.subTest(title=entry.get("title")):
                self.assertIn(entry.get("type"), ALLOWED_TYPES)
                self.assertTrue(entry.get("title"))


if __name__ == "__main__":
    unittest.main()
