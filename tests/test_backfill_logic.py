from datetime import date

from backfill_orders import parse_funnel_response, compute_backfill_range


def test_parse_funnel_response_extracts_nmid_and_selected():
    data = {
        "data": {
            "products": [
                {
                    "product": {"nmId": 123, "title": "X"},
                    "statistic": {"selected": {"orderCount": 5, "orderSum": 1000}},
                },
                {
                    "product": {"nmId": 456},
                    "statistic": {"selected": {"orderCount": 0}},
                },
            ]
        }
    }
    result = parse_funnel_response(data)
    assert result[123]["orderCount"] == 5
    assert result[456]["orderCount"] == 0


def test_parse_funnel_response_handles_nmid_capitalization():
    data = {"data": {"products": [
        {"product": {"nmID": 789}, "statistic": {"selected": {"orderCount": 2}}}
    ]}}
    result = parse_funnel_response(data)
    assert result[789]["orderCount"] == 2


def test_parse_funnel_response_empty_products():
    assert parse_funnel_response({"data": {"products": []}}) == {}
    assert parse_funnel_response({}) == {}


def test_parse_funnel_response_skips_items_without_stat():
    data = {"data": {"products": [
        {"product": {"nmId": 111}, "statistic": {}},
        {"product": {"nmId": 222}},
    ]}}
    assert parse_funnel_response(data) == {}


def test_compute_backfill_range_30_days():
    start, end = compute_backfill_range("2026-04-15", 30)
    assert start == date(2026, 3, 16)
    assert end == date(2026, 4, 14)
