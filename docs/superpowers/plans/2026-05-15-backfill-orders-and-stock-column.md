# Backfill Orders + Stock Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-off 30-day historical orders backfill (separate script) and an everyday "Остаток" column F (total stock per article) to the "Заказы и Остатки" Google Sheet.

**Architecture:** Feature B widens the combined sheet from 5 to 6 meta-columns (`META_COLS` becomes a module-level constant) and `update_combined_sheet` writes a total-stock value into column F on each article row, with a migration path that auto-detects the old 5-column layout on read. Feature A rewrites `src/backfill_orders.py` to call the WB `/sales-funnel/products` endpoint day-by-day (sync `requests`, global ≥20s pacer, retry backoff), and writes all collected date columns to the sheet in one pass via a new `update_combined_sheet_bulk` method. The backfill writes only to the sheet, never to the database.

**Tech Stack:** Python 3.12, `requests`, `gspread`, PostgreSQL (`psycopg2`), `pytest` (added for pure-logic unit tests).

---

## File Structure

- `src/sheets_service.py` — **modify**: promote `META_COLS`/`SUMMARY_ROWS` to module-level constants (`META_COLS = 6`); add pure helpers `aggregate_stock_totals` and `_date_sort_key`; rewrite `update_combined_sheet` for column F + old-layout migration; add `update_combined_sheet_bulk`.
- `src/backfill_orders.py` — **rewrite**: WB `/sales-funnel/products` day-by-day client (sync, pacer, retry), pure helpers `parse_funnel_response` and `compute_backfill_range`, orchestration that writes to the sheet only.
- `src/main.py` — **no change** (already passes `stocks_data` into `update_combined_sheet`).
- `tests/conftest.py` — **create**: put `src/` on `sys.path` for tests.
- `tests/test_sheets_logic.py` — **create**: unit tests for `aggregate_stock_totals`, `_date_sort_key`.
- `tests/test_backfill_logic.py` — **create**: unit tests for `parse_funnel_response`, `compute_backfill_range`.
- `requirements.txt` — **modify**: add `pytest>=8.0.0`.
- `CLAUDE.md` — **modify**: document the backfill capability.

Integration with the live WB API and Google Sheets is verified manually (Task 7); only side-effect-free pure functions are unit-tested.

---

### Task 1: Module constants + pure helpers in sheets_service.py

**Files:**
- Modify: `src/sheets_service.py`
- Modify: `requirements.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_sheets_logic.py`

- [ ] **Step 1: Add pytest to requirements**

Edit `requirements.txt` — add this line at the end:

```
pytest>=8.0.0
```

- [ ] **Step 2: Create tests/conftest.py**

Create `tests/conftest.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_sheets_logic.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_sheets_logic.py -v`
Expected: FAIL with `ImportError: cannot import name 'aggregate_stock_totals'`

- [ ] **Step 5: Add module constants and helpers**

In `src/sheets_service.py`, after the `from config import (...)` block (currently ends at line 13), add module-level constants:

```python
META_COLS = 6
SUMMARY_ROWS = 4
```

Then at the end of the file (after `build_stocks_data_for_sheets`), add the two pure helpers:

```python
def aggregate_stock_totals(stocks_data: dict) -> dict:
    """Sum quantity + in_way_to + in_way_from per (ip, str(nm_id)) across all warehouses."""
    totals = {}
    for (ip, nm_id, _title, _warehouse), info in stocks_data.items():
        key = (ip, str(nm_id))
        total = (
            (info.get('quantity', 0) or 0)
            + (info.get('in_way_to', 0) or 0)
            + (info.get('in_way_from', 0) or 0)
        )
        totals[key] = totals.get(key, 0) + total
    return totals


def _date_sort_key(d: str):
    """Sort key for 'DD.MM.YYYY' date-column labels; unparseable labels sort first."""
    try:
        return datetime.strptime(d, '%d.%m.%Y')
    except ValueError:
        return datetime.min
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_sheets_logic.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Commit**

```bash
git add requirements.txt tests/conftest.py tests/test_sheets_logic.py src/sheets_service.py
git commit -m "feat: add sheets layout constants and pure stock-total helpers"
```

---

### Task 2: Rewrite update_combined_sheet for column F + migration (Feature B)

**Files:**
- Modify: `src/sheets_service.py` — replace the entire `update_combined_sheet` method (currently lines 345-611)

This task has no automated test — the method is pure I/O against Google Sheets and is verified manually in Task 7. Replace the whole method body so it uses the module-level `META_COLS`/`SUMMARY_ROWS`, auto-detects the old 5-column layout on read, and writes column F.

- [ ] **Step 1: Replace the update_combined_sheet method**

In `src/sheets_service.py`, replace the **entire** `update_combined_sheet` method (from `def update_combined_sheet(self, orders_data: dict, stocks_data: dict, date: str):` through the final `print(f"    Combined sheet: ...")` line) with:

```python
    def update_combined_sheet(self, orders_data: dict, stocks_data: dict, date: str):
        """
        Update combined orders+stocks sheet with row grouping.
        Orders rows = empty warehouse. Stock rows = with warehouse, grouped under orders row.
        Rows 1-4: summary. Row 5: headers with auto-filter.
        Column F 'Остаток' = total stock per article on the orders row, blank on warehouse rows.
        Auto-migrates an old 5-meta-column sheet to the 6-column layout on first run.
        """
        ws = self.get_worksheet(SHEET_COMBINED)
        all_data = self._safe_call(ws.get_all_values)

        if all_data and len(all_data) > SUMMARY_ROWS:
            headers = all_data[SUMMARY_ROWS]
            read_meta = 6 if (len(headers) > 5 and headers[5].strip() == 'Остаток') else 5
            existing_rows = all_data[SUMMARY_ROWS + 1:]
            orig_date_columns = [d.strip().lstrip("'") for d in headers[read_meta:]]
        else:
            read_meta = META_COLS
            existing_rows = []
            orig_date_columns = []

        date_columns = list(orig_date_columns)
        if date not in date_columns:
            date_columns.append(date)

        rows_map = {}
        for row in existing_rows:
            if len(row) < 2 or not row[0] or not row[1]:
                continue
            ip, nm_id = row[0], row[1]
            title = row[2] if len(row) > 2 else ''
            mp = row[3] if len(row) > 3 else 'WB'
            warehouse = row[4] if len(row) > 4 else ''
            ostatok = row[5] if (read_meta == 6 and len(row) > 5) else ''
            key = (ip, nm_id, warehouse)
            date_values = row[read_meta:] if len(row) > read_meta else []
            rows_map[key] = {
                'meta': [ip, nm_id, title, mp, warehouse],
                'ostatok': ostatok,
                'dates': {orig_date_columns[i]: date_values[i] if i < len(date_values) else ''
                          for i in range(len(orig_date_columns))},
                'in_way': {}
            }

        for (ip, nm_id, title), orders_count in orders_data.items():
            key = (ip, str(nm_id), '')
            if key in rows_map:
                rows_map[key]['dates'][date] = orders_count
                if title:
                    rows_map[key]['meta'][2] = title
            else:
                rows_map[key] = {
                    'meta': [ip, str(nm_id), title or '', 'WB', ''],
                    'ostatok': '',
                    'dates': {d: '' for d in date_columns},
                    'in_way': {}
                }
                rows_map[key]['dates'][date] = orders_count

        for (ip, nm_id, title, warehouse), stock_info in stocks_data.items():
            key = (ip, str(nm_id), warehouse)
            quantity = stock_info.get('quantity', 0)
            in_way_to = stock_info.get('in_way_to', 0)
            in_way_from = stock_info.get('in_way_from', 0)

            if key in rows_map:
                rows_map[key]['dates'][date] = quantity
                if title:
                    rows_map[key]['meta'][2] = title
            else:
                rows_map[key] = {
                    'meta': [ip, str(nm_id), title or '', 'WB', warehouse],
                    'ostatok': '',
                    'dates': {d: '' for d in date_columns},
                    'in_way': {}
                }
                rows_map[key]['dates'][date] = quantity
            rows_map[key]['in_way'] = {'to': in_way_to, 'from': in_way_from}

            orders_key = (ip, str(nm_id), '')
            if orders_key not in rows_map:
                rows_map[orders_key] = {
                    'meta': [ip, str(nm_id), title or '', 'WB', ''],
                    'ostatok': '',
                    'dates': {d: '' for d in date_columns},
                    'in_way': {}
                }

        cutoff_days = 60
        if len(date_columns) >= cutoff_days:
            recent_dates = sorted(date_columns, reverse=True)[:cutoff_days]
            stale_keys = []
            for key, row_data in rows_map.items():
                if not key[2]:
                    continue
                has_stock = False
                for d in recent_dates:
                    val = row_data['dates'].get(d, '')
                    if val != '' and val != 0 and val != '0':
                        has_stock = True
                        break
                if not has_stock:
                    stale_keys.append(key)
            for key in stale_keys:
                del rows_map[key]
            if stale_keys:
                print(f"    Removed {len(stale_keys)} stale warehouse rows (no stock in {cutoff_days}+ days)")

        stock_totals = aggregate_stock_totals(stocks_data)
        sorted_keys = sorted(rows_map.keys(), key=lambda k: (k[0], str(k[1]), k[2]))

        day_names = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
        row_dow = ['', '', '', '', '', '']
        row_stocks_total = ['', '', '', '', 'Остатки', '']
        row_orders_total = ['', '', '', '', 'Заказы', '']
        row_days_to_sell = ['', '', '', '', 'Дней', '']

        for d in date_columns:
            try:
                dt = datetime.strptime(d, '%d.%m.%Y')
                row_dow.append(day_names[dt.weekday()])
            except ValueError:
                row_dow.append('')

            total_orders = 0
            total_stocks = 0
            for key, row_data in rows_map.items():
                val = row_data['dates'].get(d, '') or 0
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    val = 0
                if not key[2]:
                    total_orders += val
                else:
                    total_stocks += val

            row_stocks_total.append(total_stocks)
            row_orders_total.append(total_orders)
            if total_orders > 0:
                row_days_to_sell.append(round(total_stocks / total_orders * 2))
            else:
                row_days_to_sell.append('')

        new_headers = ['ИП', 'Артикул', 'Наименование', 'МП', 'Склад', 'Остаток'] + ["'" + d for d in date_columns]
        output_rows = [row_days_to_sell, row_orders_total, row_stocks_total, row_dow, new_headers]
        for key in sorted_keys:
            row_data = rows_map[key]
            meta = row_data['meta']
            if key[2]:
                ostatok = ''
            else:
                ostatok = stock_totals.get((key[0], str(key[1])), '')
            dates = [row_data['dates'].get(d, '') or 0 for d in date_columns]
            output_rows.append(meta + [ostatok] + dates)

        self._remove_all_row_groups(ws)
        time.sleep(1)

        try:
            self._safe_call(self.spreadsheet.batch_update, {"requests": [
                {"clearBasicFilter": {"sheetId": ws.id}}
            ]})
            time.sleep(1)
        except Exception:
            pass

        self._safe_call(ws.clear)
        time.sleep(1)

        self._safe_call(self.spreadsheet.batch_update, {"requests": [
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id},
                    "fields": "userEnteredFormat.numberFormat"
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id},
                    "fields": "note"
                }
            }
        ]})
        time.sleep(1)

        self._safe_call(ws.update, 'A1', output_rows, value_input_option='USER_ENTERED')
        time.sleep(1)

        groups = []
        current_article = None
        group_start = None

        for i, key in enumerate(sorted_keys):
            article_key = (key[0], key[1])
            if article_key != current_article:
                if current_article is not None and i - group_start > 1:
                    groups.append({
                        'start': SUMMARY_ROWS + 1 + group_start + 1,
                        'end': SUMMARY_ROWS + 1 + i
                    })
                current_article = article_key
                group_start = i
        if current_article is not None and len(sorted_keys) - group_start > 1:
            groups.append({
                'start': SUMMARY_ROWS + 1 + group_start + 1,
                'end': SUMMARY_ROWS + 1 + len(sorted_keys)
            })

        requests = []

        total_cols = META_COLS + len(date_columns)
        requests.append({
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": SUMMARY_ROWS,
                        "startColumnIndex": 0,
                        "endColumnIndex": total_cols
                    }
                }
            }
        })

        for g in groups:
            requests.append({
                "addDimensionGroup": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": g['start'],
                        "endIndex": g['end']
                    }
                }
            })
        for g in groups:
            requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": g['start'],
                        "endIndex": g['end']
                    },
                    "properties": {"hiddenByUser": True},
                    "fields": "hiddenByUser"
                }
            })

        date_col_api = META_COLS + date_columns.index(date)
        for i, key in enumerate(sorted_keys):
            if key[2]:
                in_way = rows_map[key].get('in_way', {})
                to_val = in_way.get('to', 0)
                from_val = in_way.get('from', 0)
                if to_val or from_val:
                    row_api = SUMMARY_ROWS + 1 + i
                    requests.append({
                        "updateCells": {
                            "range": {
                                "sheetId": ws.id,
                                "startRowIndex": row_api,
                                "endRowIndex": row_api + 1,
                                "startColumnIndex": date_col_api,
                                "endColumnIndex": date_col_api + 1
                            },
                            "rows": [{"values": [{
                                "note": f"В пути к клиенту: {to_val}\nВ пути от клиента: {from_val}"
                            }]}],
                            "fields": "note"
                        }
                    })

        self._safe_call(self.spreadsheet.batch_update, {"requests": requests})

        print(f"    Combined sheet: {len(sorted_keys)} rows, {len(groups)} groups")
```

- [ ] **Step 2: Verify the module still imports**

Run: `python -c "import sys; sys.path.insert(0, 'src'); import sheets_service; print('ok')"`
Expected: prints `ok` (no syntax/import errors)

- [ ] **Step 3: Verify existing pure-logic tests still pass**

Run: `python -m pytest tests/ -v`
Expected: PASS (5 passed)

- [ ] **Step 4: Commit**

```bash
git add src/sheets_service.py
git commit -m "feat: add 'Остаток' column F to combined sheet with old-layout migration"
```

---

### Task 3: Add update_combined_sheet_bulk method (Feature A sheet write)

**Files:**
- Modify: `src/sheets_service.py` — add a new method `update_combined_sheet_bulk` immediately after `update_combined_sheet`

This method merges many historical order date-columns in one read/write cycle. It is orders-only: existing warehouse stock cells and column F values are preserved unchanged; date columns are re-sorted chronologically after merge. No automated test — verified manually in Task 7.

- [ ] **Step 1: Add the update_combined_sheet_bulk method**

In `src/sheets_service.py`, immediately after the `update_combined_sheet` method (before `def format_date_for_sheets` at module level — i.e. still inside the `GoogleSheetsService` class), add:

```python
    def update_combined_sheet_bulk(self, orders_by_date: dict):
        """
        Bulk-merge multiple historical order date-columns in ONE read/write cycle.
        orders_by_date: {"DD.MM.YYYY": {(ip, nm_id, title): orders_count}}.
        Orders-only: existing warehouse stock cells and column F 'Остаток' are
        preserved unchanged. Date columns are re-sorted chronologically after merge.
        Auto-migrates an old 5-meta-column sheet to the 6-column layout.
        """
        ws = self.get_worksheet(SHEET_COMBINED)
        all_data = self._safe_call(ws.get_all_values)

        if all_data and len(all_data) > SUMMARY_ROWS:
            headers = all_data[SUMMARY_ROWS]
            read_meta = 6 if (len(headers) > 5 and headers[5].strip() == 'Остаток') else 5
            existing_rows = all_data[SUMMARY_ROWS + 1:]
            orig_date_columns = [d.strip().lstrip("'") for d in headers[read_meta:]]
        else:
            read_meta = META_COLS
            existing_rows = []
            orig_date_columns = []

        date_columns = list(orig_date_columns)
        for date in orders_by_date:
            if date not in date_columns:
                date_columns.append(date)
        date_columns = sorted(date_columns, key=_date_sort_key)

        rows_map = {}
        for row in existing_rows:
            if len(row) < 2 or not row[0] or not row[1]:
                continue
            ip, nm_id = row[0], row[1]
            title = row[2] if len(row) > 2 else ''
            mp = row[3] if len(row) > 3 else 'WB'
            warehouse = row[4] if len(row) > 4 else ''
            ostatok = row[5] if (read_meta == 6 and len(row) > 5) else ''
            key = (ip, nm_id, warehouse)
            date_values = row[read_meta:] if len(row) > read_meta else []
            rows_map[key] = {
                'meta': [ip, nm_id, title, mp, warehouse],
                'ostatok': ostatok,
                'dates': {orig_date_columns[i]: date_values[i] if i < len(date_values) else ''
                          for i in range(len(orig_date_columns))},
            }

        for date, orders in orders_by_date.items():
            for (ip, nm_id, title), orders_count in orders.items():
                key = (ip, str(nm_id), '')
                if key in rows_map:
                    rows_map[key]['dates'][date] = orders_count
                    if title:
                        rows_map[key]['meta'][2] = title
                else:
                    rows_map[key] = {
                        'meta': [ip, str(nm_id), title or '', 'WB', ''],
                        'ostatok': '',
                        'dates': {},
                    }
                    rows_map[key]['dates'][date] = orders_count

        sorted_keys = sorted(rows_map.keys(), key=lambda k: (k[0], str(k[1]), k[2]))

        day_names = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
        row_dow = ['', '', '', '', '', '']
        row_stocks_total = ['', '', '', '', 'Остатки', '']
        row_orders_total = ['', '', '', '', 'Заказы', '']
        row_days_to_sell = ['', '', '', '', 'Дней', '']

        for d in date_columns:
            try:
                dt = datetime.strptime(d, '%d.%m.%Y')
                row_dow.append(day_names[dt.weekday()])
            except ValueError:
                row_dow.append('')

            total_orders = 0
            total_stocks = 0
            for key, row_data in rows_map.items():
                val = row_data['dates'].get(d, '') or 0
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    val = 0
                if not key[2]:
                    total_orders += val
                else:
                    total_stocks += val

            row_stocks_total.append(total_stocks)
            row_orders_total.append(total_orders)
            if total_orders > 0:
                row_days_to_sell.append(round(total_stocks / total_orders * 2))
            else:
                row_days_to_sell.append('')

        new_headers = ['ИП', 'Артикул', 'Наименование', 'МП', 'Склад', 'Остаток'] + ["'" + d for d in date_columns]
        output_rows = [row_days_to_sell, row_orders_total, row_stocks_total, row_dow, new_headers]
        for key in sorted_keys:
            row_data = rows_map[key]
            meta = row_data['meta']
            ostatok = row_data.get('ostatok', '')
            dates = [row_data['dates'].get(d, '') or 0 for d in date_columns]
            output_rows.append(meta + [ostatok] + dates)

        self._remove_all_row_groups(ws)
        time.sleep(1)

        try:
            self._safe_call(self.spreadsheet.batch_update, {"requests": [
                {"clearBasicFilter": {"sheetId": ws.id}}
            ]})
            time.sleep(1)
        except Exception:
            pass

        self._safe_call(ws.clear)
        time.sleep(1)

        self._safe_call(self.spreadsheet.batch_update, {"requests": [
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id},
                    "fields": "userEnteredFormat.numberFormat"
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": ws.id},
                    "fields": "note"
                }
            }
        ]})
        time.sleep(1)

        self._safe_call(ws.update, 'A1', output_rows, value_input_option='USER_ENTERED')
        time.sleep(1)

        groups = []
        current_article = None
        group_start = None

        for i, key in enumerate(sorted_keys):
            article_key = (key[0], key[1])
            if article_key != current_article:
                if current_article is not None and i - group_start > 1:
                    groups.append({
                        'start': SUMMARY_ROWS + 1 + group_start + 1,
                        'end': SUMMARY_ROWS + 1 + i
                    })
                current_article = article_key
                group_start = i
        if current_article is not None and len(sorted_keys) - group_start > 1:
            groups.append({
                'start': SUMMARY_ROWS + 1 + group_start + 1,
                'end': SUMMARY_ROWS + 1 + len(sorted_keys)
            })

        requests = []

        total_cols = META_COLS + len(date_columns)
        requests.append({
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": SUMMARY_ROWS,
                        "startColumnIndex": 0,
                        "endColumnIndex": total_cols
                    }
                }
            }
        })

        for g in groups:
            requests.append({
                "addDimensionGroup": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": g['start'],
                        "endIndex": g['end']
                    }
                }
            })
        for g in groups:
            requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": g['start'],
                        "endIndex": g['end']
                    },
                    "properties": {"hiddenByUser": True},
                    "fields": "hiddenByUser"
                }
            })

        self._safe_call(self.spreadsheet.batch_update, {"requests": requests})

        print(f"    Combined sheet (bulk): {len(sorted_keys)} rows, "
              f"{len(date_columns)} date columns, {len(groups)} groups")
```

- [ ] **Step 2: Verify the module still imports**

Run: `python -c "import sys; sys.path.insert(0, 'src'); from sheets_service import GoogleSheetsService; print('ok')"`
Expected: prints `ok`

- [ ] **Step 3: Commit**

```bash
git add src/sheets_service.py
git commit -m "feat: add update_combined_sheet_bulk for multi-date historical writes"
```

---

### Task 4: Backfill pure helpers + tests

**Files:**
- Create: `tests/test_backfill_logic.py`
- Modify: `src/backfill_orders.py` (this task adds only the two pure helpers; the full rewrite is Task 5)

To keep this task self-contained and testable, Task 4 writes the failing tests and the two pure helper functions. Task 5 then rewrites the rest of the file around them. The two helpers written here are the final versions and are not changed in Task 5.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backfill_logic.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_backfill_logic.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_funnel_response'`

- [ ] **Step 3: Add the two pure helpers to backfill_orders.py**

This step is superseded by the full file written in Task 5, but to satisfy the red-green loop now, append these two functions to the existing `src/backfill_orders.py` (after the existing imports, before `def get_orders_for_period`):

```python
def parse_funnel_response(data: dict) -> dict:
    """Extract {nmId: statistic.selected} from a /sales-funnel/products response.

    Empty products list on HTTP 200 is normal (no traffic) and yields {}.
    """
    result = {}
    products = (data or {}).get('data', {}).get('products', []) or []
    for item in products:
        product = item.get('product', {}) or {}
        nm_id = product.get('nmId') or product.get('nmID')
        stat = (item.get('statistic', {}) or {}).get('selected')
        if nm_id and stat:
            result[int(nm_id)] = stat
    return result


def compute_backfill_range(earliest_str: str, days: int):
    """Given the earliest sheet date ('YYYY-MM-DD'), return (start_date, end_date)
    covering the `days` days immediately before it. Both are datetime.date."""
    from datetime import datetime as _dt, timedelta as _td
    earliest = _dt.strptime(earliest_str, '%Y-%m-%d').date()
    start = earliest - _td(days=days)
    end = earliest - _td(days=1)
    return start, end
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_backfill_logic.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_backfill_logic.py src/backfill_orders.py
git commit -m "feat: add backfill pure helpers (funnel parse, date range)"
```

---

### Task 5: Rewrite backfill_orders.py (funnel client + orchestration)

**Files:**
- Modify: `src/backfill_orders.py` — full rewrite

Replace the **entire** contents of `src/backfill_orders.py`. The two pure helpers from Task 4 (`parse_funnel_response`, `compute_backfill_range`) are carried over unchanged inside the new file. No automated test for the I/O paths — verified manually in Task 7.

- [ ] **Step 1: Replace the whole file**

Overwrite `src/backfill_orders.py` with:

```python
#!/usr/bin/env python3
"""
Backfill historical orders into the "Заказы и Остатки" Google Sheet.

Uses WB Sales Funnel endpoint POST /api/analytics/v3/sales-funnel/products
queried day-by-day (selectedPeriod start == end → one-day aggregate).
Depth up to 365 days. Does NOT use /products/history (max ~1 week, no cancel data).

Reads the earliest date column from the combined sheet, goes back N days,
fetches orders for every day in that window, and writes all historical
columns to the sheet in one bulk pass. Writes ONLY to the sheet — the
database (orders_raw) is never touched.

Usage:
    python src/backfill_orders.py [--days 30] [--dry-run]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import time
from datetime import datetime, timedelta

import requests

from config import WB_API_KEYS, WB_API_BASE, SHEET_COMBINED
from db import get_cursor, get_articles_for_sheets
from sheets_service import GoogleSheetsService, format_date_for_sheets

WB_FUNNEL_URL = f'{WB_API_BASE}/api/analytics/v3/sales-funnel/products'
FUNNEL_BATCH_NM_IDS = 1000          # max nmIds per request
FUNNEL_MIN_INTERVAL_SEC = 20.0      # >= 20s between ANY funnel requests (process-wide)
FUNNEL_ATTEMPTS = 5
FUNNEL_BACKOFF = (30.0, 60.0, 120.0, 240.0, 480.0)

_last_request_at = 0.0


class FunnelApiError(Exception):
    """WB returned a non-retryable error (400/401/403) or retries were exhausted."""


def parse_funnel_response(data: dict) -> dict:
    """Extract {nmId: statistic.selected} from a /sales-funnel/products response.

    Empty products list on HTTP 200 is normal (no traffic) and yields {}.
    """
    result = {}
    products = (data or {}).get('data', {}).get('products', []) or []
    for item in products:
        product = item.get('product', {}) or {}
        nm_id = product.get('nmId') or product.get('nmID')
        stat = (item.get('statistic', {}) or {}).get('selected')
        if nm_id and stat:
            result[int(nm_id)] = stat
    return result


def compute_backfill_range(earliest_str: str, days: int):
    """Given the earliest sheet date ('YYYY-MM-DD'), return (start_date, end_date)
    covering the `days` days immediately before it. Both are datetime.date."""
    earliest = datetime.strptime(earliest_str, '%Y-%m-%d').date()
    start = earliest - timedelta(days=days)
    end = earliest - timedelta(days=1)
    return start, end


def _funnel_pace():
    """Global pacer: ensure >= FUNNEL_MIN_INTERVAL_SEC between any two funnel requests."""
    global _last_request_at
    wait = _last_request_at + FUNNEL_MIN_INTERVAL_SEC - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def fetch_funnel_day(nm_ids: list, day: str, api_key: str) -> dict:
    """
    Fetch the sales funnel for ONE day via /sales-funnel/products.

    nm_ids is chunked by <= FUNNEL_BATCH_NM_IDS. Retries 5x with backoff
    30/60/120/240/480s on 429/5xx/timeout. 400/401/403 -> FunnelApiError.
    Exhausted retries -> FunnelApiError.
    Returns {nmId: statistic_selected}. Empty response -> {} (normal).
    """
    result = {}
    headers = {'Authorization': api_key, 'Content-Type': 'application/json'}

    for i in range(0, len(nm_ids), FUNNEL_BATCH_NM_IDS):
        chunk = nm_ids[i:i + FUNNEL_BATCH_NM_IDS]
        payload = {
            'selectedPeriod': {'start': day, 'end': day},
            'nmIds': chunk,
            'limit': FUNNEL_BATCH_NM_IDS,
        }

        last_exc = None
        chunk_ok = False
        for attempt in range(FUNNEL_ATTEMPTS):
            try:
                _funnel_pace()
                resp = requests.post(WB_FUNNEL_URL, headers=headers, json=payload, timeout=60)

                if resp.status_code == 429 or resp.status_code >= 500:
                    delay = FUNNEL_BACKOFF[attempt]
                    print(f"  funnel HTTP {resp.status_code} attempt {attempt + 1}/{FUNNEL_ATTEMPTS}, "
                          f"sleep {delay:.0f}s")
                    time.sleep(delay)
                    continue
                if resp.status_code in (400, 401, 403):
                    raise FunnelApiError(f"HTTP {resp.status_code} day={day}: {resp.text[:300]}")
                resp.raise_for_status()

                data = resp.json() if resp.content else {}
                result.update(parse_funnel_response(data))
                chunk_ok = True
                break
            except FunnelApiError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(FUNNEL_BACKOFF[attempt])

        if not chunk_ok:
            raise FunnelApiError(f"day={day} failed after {FUNNEL_ATTEMPTS} attempts: {last_exc}")

    return result


def get_earliest_sheet_date(sheets: GoogleSheetsService):
    """Read combined sheet, return earliest date column as 'YYYY-MM-DD' (or None)."""
    SUMMARY_ROWS = 4
    ws = sheets.get_worksheet(SHEET_COMBINED)
    all_data = sheets._safe_call(ws.get_all_values)

    if not all_data or len(all_data) <= SUMMARY_ROWS:
        return None

    headers = all_data[SUMMARY_ROWS]
    read_meta = 6 if (len(headers) > 5 and headers[5].strip() == 'Остаток') else 5
    date_columns = [d.strip().lstrip("'") for d in headers[read_meta:] if d.strip()]

    dates = []
    for d in date_columns:
        try:
            dates.append(datetime.strptime(d, '%d.%m.%Y'))
        except ValueError:
            continue

    if not dates:
        return None

    earliest = min(dates)
    print(f"Earliest date on sheet: {earliest.strftime('%d.%m.%Y')}")
    return earliest.strftime('%Y-%m-%d')


def get_all_nm_ids() -> dict:
    """Get all active nm_ids per IP from the articles table."""
    result = {}
    with get_cursor() as cur:
        cur.execute("""
            SELECT ip, nm_id FROM articles
            WHERE is_active = TRUE
            ORDER BY ip, nm_id
        """)
        for ip, nm_id in cur.fetchall():
            result.setdefault(ip, []).append(nm_id)
    return result


def backfill(days_back: int = 30, dry_run: bool = False):
    print(f"=== Backfill orders: {days_back} days back (sheet only, no DB) ===")
    print(f"Dry run: {dry_run}")

    sheets = GoogleSheetsService()
    earliest_str = get_earliest_sheet_date(sheets)
    if not earliest_str:
        print("No dates found on sheet. Nothing to backfill from.")
        return

    start, end = compute_backfill_range(earliest_str, days_back)
    print(f"Backfill range: {start.strftime('%d.%m.%Y')} -> {end.strftime('%d.%m.%Y')}")

    ips_nm_ids = get_all_nm_ids()
    if not ips_nm_ids:
        print("No active articles in DB. Run the main collection first.")
        return

    for ip, nm_ids in ips_nm_ids.items():
        print(f"  [{ip}] {len(nm_ids)} active nmIds")

    if dry_run:
        print("DRY RUN: would fetch the range above. Exiting.")
        return

    article_titles = {}
    for row in get_articles_for_sheets():
        article_titles[(row[0], str(row[1]))] = row[2]

    orders_by_date = {}   # "DD.MM.YYYY" -> {(ip, nm_id, title): orders_count}
    aborted_ips = set()

    d = start
    while d <= end:
        day_iso = d.isoformat()
        date_display = format_date_for_sheets(day_iso)
        for ip, nm_ids in ips_nm_ids.items():
            if ip in aborted_ips:
                continue
            try:
                stats = fetch_funnel_day(nm_ids, day_iso, WB_API_KEYS[ip])
            except FunnelApiError as exc:
                print(f"  [{ip}] aborted, skipping remaining days: {exc}")
                aborted_ips.add(ip)
                continue
            bucket = orders_by_date.setdefault(date_display, {})
            for nm_id, stat in stats.items():
                count = int(stat.get('orderCount') or 0)
                title = article_titles.get((ip, str(nm_id)), '')
                bucket[(ip, nm_id, title)] = count
            print(f"  [{ip}] {day_iso}: {len(stats)} cards")
        d += timedelta(days=1)

    if not orders_by_date:
        print("No data collected. Nothing to write.")
        return

    print(f"\n=== Writing {len(orders_by_date)} historical date columns to sheet ===")
    sheets.update_combined_sheet_bulk(orders_by_date)
    print("=== Backfill complete ===")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Backfill historical orders from WB Sales Funnel')
    parser.add_argument('--days', type=int, default=30,
                        help='Days to go back from the earliest sheet date (default: 30)')
    parser.add_argument('--dry-run', action='store_true', help='Show plan without fetching')
    args = parser.parse_args()

    backfill(days_back=args.days, dry_run=args.dry_run)
```

- [ ] **Step 2: Verify the module imports and the helper tests still pass**

Run: `python -c "import sys; sys.path.insert(0, 'src'); import backfill_orders; print('ok')"`
Expected: prints `ok`

Run: `python -m pytest tests/ -v`
Expected: PASS (10 passed)

- [ ] **Step 3: Verify the CLI parses --dry-run without crashing on arg parsing**

Run: `python src/backfill_orders.py --help`
Expected: prints usage text showing `--days` and `--dry-run` options, exit 0

- [ ] **Step 4: Commit**

```bash
git add src/backfill_orders.py
git commit -m "feat: rewrite backfill on /sales-funnel/products day-by-day, sheet-only"
```

---

### Task 6: Document the backfill capability in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a backfill section**

In `CLAUDE.md`, the `## Legacy` section is currently the last section. Insert a new section immediately **before** `## Legacy`:

```markdown
## Backfill заказов (одноразово)

`src/backfill_orders.py` — отдельный скрипт, основной поток (`main.py`) не трогает.

Доливает исторические заказы на лист "Заказы и Остатки": читает самую раннюю
колонку-дату, уходит на N дней назад, тянет заказы по дням через WB endpoint
`POST /api/analytics/v3/sales-funnel/products` (`selectedPeriod` с `start == end`
= суточный агрегат, глубина до 365 дней). Эндпоинт `/products/history` НЕ
используется — максимум ~неделя, нет данных по отменам.

- Пишет **только в лист**, в БД (`orders_raw`) не пишет.
- nmIds — все активные артикулы из таблицы `articles`.
- Глобальный пейсер ≥20 сек между любыми запросами, retry с backoff на 429/5xx.
- Запись в лист одним bulk-проходом (`update_combined_sheet_bulk`).

Запуск: `python src/backfill_orders.py [--days 30] [--dry-run]`

## Лист "Заказы и Остатки": колонка F "Остаток"

Meta-колонок 6: ИП, Артикул, Наименование, МП, Склад, **Остаток**. Колонка F на
строке артикула (пустой Склад) = общий остаток по артикулу (`quantity + inWay`
по всем складам, то же число, что на листе "Себестоимость всех остатков").
Перезаписывается каждый запуск. На строках складов колонка F пустая.
`update_combined_sheet` авто-мигрирует старый 5-колоночный лист при первом запуске.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document orders backfill and column F in CLAUDE.md"
```

---

### Task 7: Manual integration verification

**Files:** none (manual checklist)

The WB API and Google Sheets paths cannot be unit-tested. Verify them manually against the live system. If anything fails, stop and report — do not claim completion.

- [ ] **Step 1: Run the full unit-test suite**

Run: `python -m pytest tests/ -v`
Expected: PASS (10 passed)

- [ ] **Step 2: Backfill dry-run**

Run: `python src/backfill_orders.py --dry-run --days 30`
Expected: prints the earliest sheet date, a backfill range spanning 30 days ending the day before it, and the active nmId count per IP. No API calls, no sheet writes.

- [ ] **Step 3: Feature B — daily run writes column F**

Run the normal daily collection: `python src/main.py --once`
Then open the "Заказы и Остатки" sheet and confirm:
- a new column `F` titled `Остаток` exists between `Склад` and the first date column;
- on article rows (empty `Склад`) column F shows a number matching that article's "Всего остатков" on the "Себестоимость всех остатков" sheet;
- on warehouse rows column F is empty;
- row grouping (plus/minus), the auto-filter, and ИП→Артикул→Склад sorting still work;
- date columns and their values are unchanged.

- [ ] **Step 4: Feature B — second run does not duplicate column F**

Run `python src/main.py --once` again. Confirm there is still exactly one `Остаток` column and the layout is intact (the migration branch must now read `headers[5] == 'Остаток'`).

- [ ] **Step 5: Feature A — small real backfill**

Run a narrow backfill to limit API time: `python src/backfill_orders.py --days 3`
Then confirm on the sheet:
- 3 new date columns appear, dated the 3 days immediately before the previously-earliest column;
- date columns are in chronological order left-to-right;
- order values appear on article rows at the intersection with the correct article;
- existing warehouse stock cells and column F values are unchanged;
- grouping and filter still work.

- [ ] **Step 6: Final commit if any fixups were needed**

If steps 3-5 required code fixes, commit them:

```bash
git add -A
git commit -m "fix: address issues found during backfill/column-F verification"
```

---

## Notes for the implementer

- **Rate limits:** a real 30-day backfill across 3 IPs is ~90 requests at ≥20s each ≈ 30 minutes. This is expected; do not "optimize" the pacer away — 4 requests inside 20s triggers WB 429 "Limited by global limiter, per seller; code 461".
- **`limit` is mandatory** in the funnel payload — without it WB caps the response at 50 cards and silently drops the rest. It is already set to `FUNNEL_BATCH_NM_IDS` in `fetch_funnel_day`.
- **400/401/403 must raise**, never return empty — a silent empty result makes a failure look like success.
- **Do not touch `OrdersClient` in `src/wb_api.py`** — it is still used by the daily collection. The backfill script issues its own `requests.post` to the correct endpoint.
- **`main.py` needs no changes** — it already builds and passes `stocks_data` to `update_combined_sheet`.
```
