from sheets_service import aggregate_stock_totals, _date_sort_key


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
