from analytics_app.map_render import render_usage_map

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_render_usage_map_produces_a_valid_png() -> None:
    rows = [
        {"country_iso3": "AUS", "successful_runs": 1245},
        {"country_iso3": "CHN", "successful_runs": 892},
        {"country_iso3": "USA", "successful_runs": 743},
        {"country_iso3": "BRA", "successful_runs": 40},
    ]

    png = render_usage_map(rows, "successful_runs")

    assert png[:8] == PNG_HEADER
    assert len(png) > 5000


def test_render_usage_map_falls_back_for_unknown_metric_and_empty_rows() -> None:
    png = render_usage_map([], "bogus")

    assert png[:8] == PNG_HEADER
    assert len(png) > 5000
