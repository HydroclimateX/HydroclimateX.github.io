#!/usr/bin/env python3
"""
sync_scholar.py — Fetch the public Google Scholar profile and write static JSON.

Author  : HydroclimateX Lab
Date    : 2026-08-01
Purpose : Pull citation metrics and the full publication list from a public
          Google Scholar profile and persist them as static JSON under data/.
          Runs in GitHub Actions (Python stdlib only — no pip dependencies).

Usage
-----
  python3 scripts/sync_scholar.py --write --retries 3
  python3 scripts/sync_scholar.py --user <scholar-id> --write
  python3 scripts/sync_scholar.py            # dry-run: print JSON to stdout

Exit codes
----------
  0  — data written / printed successfully
  1  — fetch or parse failed after retries (existing data files are left
       untouched so the site keeps the last verified snapshot)
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "https://scholar.google.com/citations"
DEFAULT_USER = "4iVouPYAAAAJ"  # Ze Jiang
PAGESIZE = 100  # Scholar caps a page at 100 rows; profile has far fewer

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _strip(text: str) -> str:
    """Strip tags, unescape entities, collapse whitespace."""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# Venue substrings that mark a conference contribution (matched on lowercase
# venue text). Conference venues on Google Scholar are formatted like
# "EGU General Assembly Conference Abstracts, EGU25-7620" or
# "2024 Hydrology and Water Resources Symposium (HWRS 2024), 371-374".
_CONFERENCE_MARKERS = (
    "conference",
    "symposium",
    "abstracts",
    "assembly",
    "meeting",
    "proceedings",
    "workshop",
    "modsim",
    "aogs",
    "egu",
    "agu",
    "hwrs",
    "hydroinformatics",
    "simhydro",
    "statistics in hydrology",
)

# Book titles whose chapters appear in the venue field.
_BOOK_MARKERS = ("towards a resilient asean",)


def classify_publication(venue: str, title: str) -> str:
    """
    Classify a publication into a reference type.

    Returns one of: article, conference, software, thesis, book chapter.
    Software is detected from a URL venue (CRAN/GitHub/etc.); a thesis from a
    "(PhD Thesis)" title; conferences and book chapters from venue keywords;
    everything else is treated as a journal article.
    """
    venue_l = (venue or "").lower()
    title_l = (title or "").lower()

    if venue_l.startswith("http"):
        return "software"
    if "thesis" in title_l:
        return "thesis"
    if any(marker in venue_l for marker in _CONFERENCE_MARKERS):
        return "conference"
    if any(marker in venue_l for marker in _BOOK_MARKERS):
        return "book chapter"
    return "article"


def _get(url: str, retries: int, timeout: int) -> str:
    """GET a URL, retrying with backoff on transient failures."""
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(attempt)  # 1s, 2s, 3s ...
    raise RuntimeError(f"failed to fetch {url} after {retries} attempts: {last_err}")


def parse_metrics(html_text: str) -> dict | None:
    """Parse the citation-metrics table (#gsc_rsb_st). Second cell = all-time."""
    table = re.search(r'<table id="gsc_rsb_st".*?</table>', html_text, re.S)
    if not table:
        return None
    metrics: dict[str, int] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S):
        cells = [_strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 2:
            continue
        label, value = cells[0], cells[1].replace(",", "")
        if not value.isdigit():
            continue
        if label == "Citations":
            metrics["citations"] = int(value)
        elif label == "h-index":
            metrics["hIndex"] = int(value)
        elif label == "i10-index":
            metrics["i10Index"] = int(value)
    if {"citations", "hIndex", "i10Index"} <= set(metrics):
        return metrics
    return None


def parse_rows(html_text: str) -> list[dict]:
    """Parse publication rows (#gsc_a_b)."""
    pubs: list[dict] = []
    seen: set[str] = set()
    for row in re.findall(r'<tr class="gsc_a_tr".*?</tr>', html_text, re.S):
        title_m = re.search(r'<a[^>]*class="gsc_a_at"[^>]*>(.*?)</a>', row, re.S)
        if not title_m:
            continue
        title = _strip(title_m.group(1))
        if not title or title in seen:
            continue
        seen.add(title)

        grays = re.findall(r'<div class="gs_gray">(.*?)</div>', row, re.S)
        authors = _strip(grays[0]) if len(grays) > 0 else ""
        venue = _strip(grays[1]) if len(grays) > 1 else ""
        venue = re.sub(r",\s*\d{4}\s*$", "", venue).strip()

        year_m = re.search(
            r'<td class="gsc_a_y".*?<span[^>]*class="gsc_a_h[^>]*>(\d{4})', row, re.S
        )
        year = int(year_m.group(1)) if year_m else None
        if not year:
            y_m = re.search(r",\s*(\d{4})\s*$", _strip(grays[1]) if len(grays) > 1 else "")
            if y_m:
                year = int(y_m.group(1))

        cit_cell = re.search(r'<td class="gsc_a_c"[^>]*>(.*?)</td>', row, re.S)
        cit_text = re.sub(r"<[^>]+>", "", cit_cell.group(1)) if cit_cell else ""
        cit_digits = re.search(r"(\d[\d,]*)", cit_text)
        citations = int(cit_digits.group(1).replace(",", "")) if cit_digits else 0

        pubs.append(
            {
                "title": title,
                "authors": authors,
                "venue": venue,
                "year": year,
                "citations": citations,
                "type": classify_publication(venue, title),
            }
        )
    return pubs


def fetch_profile(user: str, retries: int, timeout: int) -> tuple[dict, list[dict]]:
    """Fetch metrics + full publication list, paginating as needed."""
    stats: dict | None = None
    name = ""
    pubs: list[dict] = []
    cstart = 0

    while True:
        url = f"{BASE}?hl=en&user={user}&cstart={cstart}&pagesize={PAGESIZE}"
        page = _get(url, retries, timeout)
        if cstart == 0:
            stats = parse_metrics(page)
            name_m = re.search(r'<div id="gsc_prf_in"[^>]*>(.*?)</div>', page, re.S)
            name = _strip(name_m.group(1)) if name_m else ""
        rows = parse_rows(page)
        if not rows:
            break
        pubs.extend(rows)
        if len(rows) < PAGESIZE:
            break
        cstart += PAGESIZE

    if stats is None:
        raise RuntimeError(
            "Google Scholar metrics table not found — profile may be blocked or layout changed"
        )
    stats["name"] = name
    return stats, pubs


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Google Scholar profile to JSON.")
    parser.add_argument("--user", default=DEFAULT_USER, help="Google Scholar user id")
    parser.add_argument("--write", action="store_true", help="write data/scholar-*.json")
    parser.add_argument("--retries", type=int, default=3, help="fetch retry count")
    parser.add_argument("--timeout", type=int, default=20, help="per-request timeout (s)")
    parser.add_argument("--stats-out", default="data/scholar-stats.json")
    parser.add_argument("--pubs-out", default="data/scholar-publications.json")
    args = parser.parse_args()

    try:
        stats, pubs = fetch_profile(args.user, args.retries, args.timeout)
    except Exception as exc:  # noqa: BLE001 — report and bail; keep last verified data
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    pubs.sort(key=lambda p: (-(p["year"] or 0), -(p["citations"] or 0)))

    stats_json = {
        "user": args.user,
        "name": stats.get("name", ""),
        "citations": stats.get("citations", 0),
        "hIndex": stats.get("hIndex", 0),
        "i10Index": stats.get("i10Index", 0),
        "updatedAt": datetime.date.today().isoformat(),
    }
    pubs_json = pubs

    if args.write:
        for path, payload in ((args.stats_out, stats_json), (args.pubs_out, pubs_json)):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        print(
            f"Synced {len(pubs)} publications for '{stats_json['name']}': "
            f"{stats_json['citations']} citations, h-index {stats_json['hIndex']}, "
            f"i10-index {stats_json['i10Index']} (updated {stats_json['updatedAt']})"
        )
    else:
        print(json.dumps({"stats": stats_json, "publications": pubs_json}, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
