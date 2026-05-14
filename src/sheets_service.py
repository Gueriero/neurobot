import time
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from config import (
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_SHEETS_ID,
    SHEET_ORDERS,
    SHEET_STOCKS,
    SHEET_COST,
    SHEET_COST_TOTAL,
    SHEET_COMBINED,
)

META_COLS = 6
SUMMARY_ROWS = 4


class GoogleSheetsService:
    """Service for working with Google Sheets."""

    def __init__(self):
        self.client = self._authenticate()
        self.spreadsheet = self.client.open_by_key(GOOGLE_SHEETS_ID)

    def _authenticate(self):
        """Authenticate using service account credentials."""
        scopes = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE,
            scopes=scopes
        )
        return gspread.authorize(credentials)

    def get_worksheet(self, name: str):
        """Get or create worksheet by name."""
        try:
            return self.spreadsheet.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            return self.spreadsheet.add_worksheet(name, rows=1, cols=1)

    def _safe_call(self, func, *args, **kwargs):
        """Execute a gspread call with retry on 429."""
        delay = 1
        for attempt in range(5):
            try:
                return func(*args, **kwargs)
            except gspread.exceptions.APIError as e:
                if hasattr(e, 'response') and e.response and e.response.status_code == 429:
                    print(f"    429 rate limit, waiting {delay}s...")
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
        raise Exception("Max retries exceeded")

    def update_orders_sheet(self, data: dict, date: str):
        """
        Update orders sheet with new date column.
        Uses batch operations to minimize API calls.
        """
        ws = self.get_worksheet(SHEET_ORDERS)

        # Get headers with retry
        headers = self._safe_call(ws.row_values, 1)
        existing_dates = headers[4:] if len(headers) > 4 else []

        if date in existing_dates:
            col_idx = 4 + existing_dates.index(date) + 1
        else:
            col_idx = len(headers) + 1
            self._safe_call(ws.insert_cols, [['']], col=col_idx)
            time.sleep(1)
            self._safe_call(ws.update_cell, 1, col_idx, ' ' + date)
            time.sleep(1)

        # Build lookup: (ip, nm_id) -> row_number
        all_rows = self._safe_call(ws.get_all_values)
        row_lookup = {}
        for r_idx, row in enumerate(all_rows[1:], start=2):
            if len(row) >= 2 and row[0] and row[1]:
                key = (row[0], row[1])
                row_lookup[key] = r_idx

        max_row = len(all_rows) + 1

        # Separate into existing and new
        existing_updates = []
        new_rows = []
        for (ip, nm_id, title), orders_count in data.items():
            key = (ip, str(nm_id))
            if key in row_lookup:
                existing_updates.append((row_lookup[key], orders_count))
            else:
                new_rows.append((ip, nm_id, title or '', orders_count))

        # Batch update existing rows
        if existing_updates:
            batch = []
            for r, orders_count in existing_updates:
                cell_ref = gspread.utils.rowcol_to_a1(r, col_idx)
                batch.append({
                    'range': cell_ref,
                    'values': [[orders_count]]
                })
            if batch:
                self._safe_call(ws.batch_update, batch)
                time.sleep(1)

        # Insert ALL new rows at once, then update quantities
        if new_rows:
            # Build all new row data with empty quantity column first
            rows_with_empty_qty = []
            for ip, nm_id, title, _ in new_rows:
                rows_with_empty_qty.append([ip, str(nm_id), title, 'WB', ''])

            # Insert all rows in one API call
            self._safe_call(ws.insert_rows, rows_with_empty_qty, max_row)
            time.sleep(1)

            # Now batch update all quantity cells
            batch = []
            for i, (_, _, _, orders_count) in enumerate(new_rows):
                row_num = max_row + i
                cell_ref = gspread.utils.rowcol_to_a1(row_num, col_idx)
                batch.append({
                    'range': cell_ref,
                    'values': [[orders_count]]
                })
            if batch:
                self._safe_call(ws.batch_update, batch)
                time.sleep(1)

        print(f"    Updated {len(existing_updates)} rows, added {len(new_rows)} new rows")

    def update_stocks_sheet(self, data: dict, date: str):
        """
        Update stocks sheet with new date column.
        Uses batch operations to minimize API calls.
        """
        ws = self.get_worksheet(SHEET_STOCKS)

        headers = self._safe_call(ws.row_values, 1)
        existing_dates = headers[5:] if len(headers) > 5 else []

        if date in existing_dates:
            col_idx = 5 + existing_dates.index(date) + 1
        else:
            col_idx = len(headers) + 1
            self._safe_call(ws.insert_cols, [['']], col=col_idx)
            time.sleep(1)
            self._safe_call(ws.update_cell, 1, col_idx, ' ' + date)
            time.sleep(1)

        # Build lookup: (ip, nm_id, warehouse) -> row_number
        all_rows = self._safe_call(ws.get_all_values)
        row_lookup = {}
        for r_idx, row in enumerate(all_rows[1:], start=2):
            if len(row) >= 5 and row[0] and row[1]:
                key = (row[0], row[1], row[4])
                row_lookup[key] = r_idx

        # Separate into existing and new
        existing_updates = []
        new_rows_data = []
        for (ip, nm_id, title, warehouse), stock_data in data.items():
            key = (ip, str(nm_id), warehouse)
            if key in row_lookup:
                existing_updates.append((row_lookup[key], stock_data))
            else:
                new_rows_data.append((ip, nm_id, title, warehouse, stock_data))

        # Batch update existing rows
        if existing_updates:
            batch_values = []
            notes = []
            for r, stock_data in existing_updates:
                quantity = stock_data.get('quantity', 0)
                in_way_to = stock_data.get('in_way_to', 0)
                in_way_from = stock_data.get('in_way_from', 0)
                cell_ref = gspread.utils.rowcol_to_a1(r, col_idx)
                batch_values.append({
                    'range': cell_ref,
                    'values': [[quantity]],
                })
                if in_way_to or in_way_from:
                    notes.append((r, col_idx, f"В пути к клиенту: {in_way_to}\nВ пути от клиента: {in_way_from}"))

            if batch_values:
                self._safe_call(ws.batch_update, batch_values)
                time.sleep(1)
            for row, col, note in notes:
                self._safe_call(ws.update_note, gspread.utils.rowcol_to_a1(row, col), note)
                time.sleep(1)

        # Insert ALL new rows at once
        if new_rows_data:
            next_row = len(all_rows) + 1

            # Build all new row data with empty quantity column
            rows_with_empty_qty = []
            for ip, nm_id, title, warehouse, _ in new_rows_data:
                rows_with_empty_qty.append([ip, str(nm_id), title or '', 'WB', warehouse, ''])

            # Insert all rows in one API call
            self._safe_call(ws.insert_rows, rows_with_empty_qty, next_row)
            time.sleep(1)

            # Now batch update all quantity cells
            batch_values = []
            notes = []
            for i, (_, _, _, _, stock_data) in enumerate(new_rows_data):
                row_num = next_row + i
                quantity = stock_data.get('quantity', 0)
                in_way_to = stock_data.get('in_way_to', 0)
                in_way_from = stock_data.get('in_way_from', 0)
                cell_ref = gspread.utils.rowcol_to_a1(row_num, col_idx)
                batch_values.append({
                    'range': cell_ref,
                    'values': [[quantity]],
                })
                if in_way_to or in_way_from:
                    notes.append((row_num, col_idx, f"В пути к клиенту: {in_way_to}\nВ пути от клиента: {in_way_from}"))

            if batch_values:
                self._safe_call(ws.batch_update, batch_values)
                time.sleep(1)
            for row, col, note in notes:
                self._safe_call(ws.update_note, gspread.utils.rowcol_to_a1(row, col), note)
                time.sleep(1)

        print(f"    Updated {len(existing_updates)} rows, added {len(new_rows_data)} new rows")

    def update_cost_total_sheet(self, data: list):
        """
        Update cost total sheet with current stock values.
        data rows: (ip, nm_id, title, qty) — columns D and F are formulas.
        """
        ws = self.get_worksheet(SHEET_COST_TOTAL)

        headers = ['ИП', 'Артикул', 'Наименование', 'Себестоимость единицы', 'Всего остатков', 'Общая себестоимость']

        cost_formula_tpl = (
            '=LET('
            '  currentRow; ROW();'
            '  result1; IFERROR('
            '    SUM(FILTER('
            '      IMPORTRANGE("1O00PnJ455zWblxR10E-S0HNN-k3b2J8dUjws8_y3Bjs"; "свод без формул!AF:AF");'
            '      IMPORTRANGE("1O00PnJ455zWblxR10E-S0HNN-k3b2J8dUjws8_y3Bjs"; "свод без формул!E:E") = INDEX($B:$B; currentRow);'
            '      IMPORTRANGE("1O00PnJ455zWblxR10E-S0HNN-k3b2J8dUjws8_y3Bjs"; "свод без формул!L:L") <> 0'
            '    ))'
            '    /'
            '    SUM(FILTER('
            '      IMPORTRANGE("1O00PnJ455zWblxR10E-S0HNN-k3b2J8dUjws8_y3Bjs"; "свод без формул!L:L") - IMPORTRANGE("1O00PnJ455zWblxR10E-S0HNN-k3b2J8dUjws8_y3Bjs"; "свод без формул!AA:AA");'
            '      IMPORTRANGE("1O00PnJ455zWblxR10E-S0HNN-k3b2J8dUjws8_y3Bjs"; "свод без формул!E:E") = INDEX($B:$B; currentRow);'
            '      IMPORTRANGE("1O00PnJ455zWblxR10E-S0HNN-k3b2J8dUjws8_y3Bjs"; "свод без формул!L:L") <> 0'
            '    ));'
            '  0);'
            '  result2; LET('
            '    data; IMPORTRANGE('
            '      "1ddf2XDkdNNC_uRCnXQv56ed4w9HLg_ojO21exxgrvMM";'
            '      "Закупки_Китай!B:AO"'
            '    );'
            '    key; INDEX($B:$B; currentRow);'
            '    rowNum; MATCH(key; INDEX(data;;1); 0);'
            '    yVal; INDEX(data; rowNum; 24);'
            '    aoVal; INDEX(data; rowNum; 40);'
            '    IFERROR(yVal + aoVal + 100; 0)'
            '  );'
            '  IF(result1 <> 0; result1; result2)'
            ')'
        )

        today_str = datetime.now().strftime('%d.%m.%Y')
        summary_row = [today_str, '', '', '', '', '=SUBTOTAL(9;F3:F)']
        rows = [summary_row, headers]
        for i, row in enumerate(data):
            ip, nm_id, title, qty = row
            r = i + 3
            rows.append([ip, nm_id, title, cost_formula_tpl, qty, f'=D{r}*E{r}'])

        self._safe_call(ws.clear)
        if data:
            self._safe_call(ws.update, 'A1', rows, value_input_option='USER_ENTERED')
            self._set_basic_filter(ws, len(rows))


    def _set_basic_filter(self, ws, row_count):
        """Set basic filter on header row (row 2) covering all data."""
        try:
            self.spreadsheet.batch_update({
                'requests': [{
                    'clearBasicFilter': {
                        'sheetId': ws.id
                    }
                }]
            })
        except Exception:
            pass
        self.spreadsheet.batch_update({
            'requests': [{
                'setBasicFilter': {
                    'filter': {
                        'range': {
                            'sheetId': ws.id,
                            'startRowIndex': 1,
                            'endRowIndex': row_count,
                            'startColumnIndex': 0,
                            'endColumnIndex': 6,
                        }
                    }
                }
            }]
        })

    def _remove_all_row_groups(self, ws):
        """Remove all existing row groups and unhide all rows."""
        try:
            meta = self.spreadsheet.fetch_sheet_metadata()
        except Exception:
            return
        for sheet in meta.get('sheets', []):
            if sheet['properties']['sheetId'] == ws.id:
                row_count = sheet['properties']['gridProperties']['rowCount']
                row_groups = sheet.get('rowGroups', [])
                requests = []
                if row_groups:
                    row_groups.sort(key=lambda g: g.get('depth', 0), reverse=True)
                    requests.extend([{"deleteDimensionGroup": {"range": g['range']}} for g in row_groups])
                requests.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": ws.id,
                            "dimension": "ROWS",
                            "startIndex": 1,
                            "endIndex": row_count
                        },
                        "properties": {"hiddenByUser": False},
                        "fields": "hiddenByUser"
                    }
                })
                self._safe_call(self.spreadsheet.batch_update, {"requests": requests})
                return

    def update_combined_sheet(self, orders_data: dict, stocks_data: dict, date: str):
        """
        Update combined orders+stocks sheet with row grouping.
        Orders rows = empty warehouse. Stock rows = with warehouse, grouped under orders row.
        Rows 1-4: summary (day of week, total stocks, total orders, days to sell).
        Row 5: headers with auto-filter.
        """
        META_COLS = 5
        SUMMARY_ROWS = 4
        ws = self.get_worksheet(SHEET_COMBINED)

        all_data = self._safe_call(ws.get_all_values)

        if all_data and len(all_data) > SUMMARY_ROWS:
            headers = all_data[SUMMARY_ROWS]
            existing_rows = all_data[SUMMARY_ROWS + 1:]
            date_columns = [d.strip() for d in headers[META_COLS:]]
        else:
            headers = ['ИП', 'Артикул', 'Наименование', 'МП', 'Склад']
            existing_rows = []
            date_columns = []

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
            key = (ip, nm_id, warehouse)
            date_values = row[META_COLS:] if len(row) > META_COLS else []
            rows_map[key] = {
                'meta': [ip, nm_id, title, mp, warehouse],
                'dates': {date_columns[i]: date_values[i] if i < len(date_values) else ''
                          for i in range(len(date_columns))},
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
                    'dates': {d: '' for d in date_columns},
                    'in_way': {}
                }
                rows_map[key]['dates'][date] = quantity
            rows_map[key]['in_way'] = {'to': in_way_to, 'from': in_way_from}

            orders_key = (ip, str(nm_id), '')
            if orders_key not in rows_map:
                rows_map[orders_key] = {
                    'meta': [ip, str(nm_id), title or '', 'WB', ''],
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

        sorted_keys = sorted(rows_map.keys(), key=lambda k: (k[0], str(k[1]), k[2]))

        day_names = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
        row_dow = ['', '', '', '', '']
        row_stocks_total = ['', '', '', '', 'Остатки']
        row_orders_total = ['', '', '', '', 'Заказы']
        row_days_to_sell = ['', '', '', '', 'Дней']

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

        new_headers = ['ИП', 'Артикул', 'Наименование', 'МП', 'Склад'] + ["'" + d for d in date_columns]
        output_rows = [row_days_to_sell, row_orders_total, row_stocks_total, row_dow, new_headers]
        for key in sorted_keys:
            row_data = rows_map[key]
            meta = row_data['meta']
            dates = [row_data['dates'].get(d, '') or 0 for d in date_columns]
            output_rows.append(meta + dates)

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


def format_date_for_sheets(date_str: str) -> str:
    """Format date for Google Sheets column header."""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return dt.strftime('%d.%m.%Y')


def build_orders_data_for_sheets(orders_records: list) -> dict:
    """Build dict for orders sheet update."""
    result = {}
    for record in orders_records:
        ip, nm_id, dt, orders_count = record[:4]
        key = (ip, nm_id)
        if key not in result:
            result[key] = 0
        result[key] += orders_count if orders_count else 0
    return result


def build_stocks_data_for_sheets(stocks_records: list) -> dict:
    """Build dict for stocks sheet update. Aggregates multiple chrt_ids per warehouse."""
    result = {}
    for record in stocks_records:
        ip, nm_id, warehouse_name, quantity, in_way_to, in_way_from = record[0], record[1], record[4], record[6], record[7], record[8]
        key = (ip, nm_id, warehouse_name)
        qty = quantity if quantity else 0
        iwt = in_way_to if in_way_to else 0
        iwf = in_way_from if in_way_from else 0
        if key in result:
            result[key]['quantity'] += qty
            result[key]['in_way_to'] += iwt
            result[key]['in_way_from'] += iwf
        else:
            result[key] = {
                'quantity': qty,
                'in_way_to': iwt,
                'in_way_from': iwf
            }
    return result


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
