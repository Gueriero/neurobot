from sheets_service import (
    aggregate_stock_totals,
    _date_sort_key,
    _build_month_layout,
    _is_parseable_date,
)


def test_aggregate_sums_quantity_and_in_way_across_warehouses():
    stocks_data = {
        ('us', 111, 'A', 'Краснодар'): {'quantity': 5, 'in_way_to': 1, 'in_way_from': 2},
        ('us', 111, 'A', 'Казань'): {'quantity': 3, 'in_way_to': 0, 'in_way_from': 0},
        ('kuz', 222, 'B', 'Тула'): {'quantity': 10, 'in_way_to': 4, 'in_way_from': 0},
    }
    totals = aggregate_stock_totals(stocks_data)
    assert totals[('us', '111')] == 11
    assert totals[('kuz', '222')] == 14


def test_aggregate_handles_missing_and_none_values():
    stocks_data = {
        ('nov', 333, 'C', 'Москва'): {'quantity': None},
    }
    totals = aggregate_stock_totals(stocks_data)
    assert totals[('nov', '333')] == 0


def test_aggregate_empty():
    assert aggregate_stock_totals({}) == {}


def test_date_sort_key_orders_chronologically():
    dates = ['15.04.2026', '01.03.2026', '31.12.2025']
    assert sorted(dates, key=_date_sort_key) == ['31.12.2025', '01.03.2026', '15.04.2026']


def test_date_sort_key_unparseable_sorts_first():
    dates = ['15.04.2026', 'garbage']
    assert sorted(dates, key=_date_sort_key)[0] == 'garbage'


def test_is_parseable_date():
    assert _is_parseable_date('15.04.2026') is True
    assert _is_parseable_date('апрель 26') is False
    assert _is_parseable_date('') is False
    assert _is_parseable_date('garbage') is False


def test_build_month_layout_empty():
    display, meta, groups = _build_month_layout([])
    assert display == []
    assert meta == {}
    assert groups == []


def test_build_month_layout_single_incomplete_month():
    dates = ['01.05.2026', '02.05.2026', '03.05.2026']
    display, meta, groups = _build_month_layout(dates)
    assert display == dates
    assert meta == {}
    assert groups == []


def test_build_month_layout_one_complete_one_partial():
    dates = ['28.04.2026', '29.04.2026', '30.04.2026', '01.05.2026', '02.05.2026']
    display, meta, groups = _build_month_layout(dates)
    assert display == [
        '28.04.2026', '29.04.2026', '30.04.2026',
        'апрель 26',
        '01.05.2026', '02.05.2026',
    ]
    assert 'апрель 26' in meta
    assert meta['апрель 26']['year'] == 2026
    assert meta['апрель 26']['month'] == 4
    assert meta['апрель 26']['dates'] == ['28.04.2026', '29.04.2026', '30.04.2026']
    assert groups == [{'start': 0, 'end': 3}]


def test_build_month_layout_two_completed_months():
    dates = [
        '30.03.2026',
        '01.04.2026', '15.04.2026', '30.04.2026',
        '01.05.2026',
    ]
    display, meta, groups = _build_month_layout(dates)
    assert display == [
        '30.03.2026',
        'март 26',
        '01.04.2026', '15.04.2026', '30.04.2026',
        'апрель 26',
        '01.05.2026',
    ]
    assert set(meta.keys()) == {'март 26', 'апрель 26'}
    assert meta['март 26']['dates'] == ['30.03.2026']
    assert meta['апрель 26']['dates'] == ['01.04.2026', '15.04.2026', '30.04.2026']
    assert groups == [
        {'start': 0, 'end': 1},
        {'start': 2, 'end': 5},
    ]


def test_build_month_layout_skips_unparseable_dates():
    dates = ['01.04.2026', 'garbage', '01.05.2026']
    display, meta, groups = _build_month_layout(dates)
    assert 'апрель 26' in meta
    assert 'garbage' not in display
