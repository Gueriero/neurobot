#!/usr/bin/env python3
"""
Backfill historical orders from WB Sales Funnel API.

Reads the earliest date from the combined Google Sheet,
goes back ~45 days, fetches orders in 7-day chunks,
saves to DB, then updates the sheet with historical columns.

Usage:
    python src/backfill_orders.py [--days 45] [--dry-run]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import time
from datetime import datetime, timedelta

from config import WB_API_KEYS, ORDERS_BATCH_SIZE, ORDERS_RATE_LIMIT_SEC
from db import init_db, insert_orders, get_orders_for_date, get_articles_for_sheets
from wb_api import OrdersClient
from sheets_service import GoogleSheetsService, format_date_for_sheets


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


def get_orders_for_period(ip_key: str, nm_ids: list, start: str, end: str) -> list:
    """
    Fetch orders from sales funnel for explicit date range.
    start/end in 'YYYY-MM-DD' format.
    """
    client = OrdersClient(ip_key)
    all_orders = []

    total_batches = (len(nm_ids) + ORDERS_BATCH_SIZE - 1) // ORDERS_BATCH_SIZE if nm_ids else 0
    print(f"  [{ip_key}] {start} → {end}: {len(nm_ids)} nmIDs, {total_batches} батчей")

    for i in range(0, len(nm_ids), ORDERS_BATCH_SIZE):
        batch = nm_ids[i:i + ORDERS_BATCH_SIZE]
        batch_num = i // ORDERS_BATCH_SIZE + 1

        payload = {
            'selectedPeriod': {
                'start': start,
                'end': end,
            },
            'nmIds': batch,
            'skipDeletedNm': True,
        }

        try:
            import requests as req
            response = req.post(
                client.BASE_URL,
                headers=client._headers(),
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            parsed = client.parse_orders(data)
            all_orders.extend(parsed)
            print(f"    [{ip_key}] Батч {batch_num}/{total_batches} → {len(parsed)} записей")

            if i + ORDERS_BATCH_SIZE < len(nm_ids):
                print(f"    [{ip_key}] Ждём {ORDERS_RATE_LIMIT_SEC}с...")
                time.sleep(ORDERS_RATE_LIMIT_SEC)

        except Exception as e:
            print(f"    [{ip_key}] Батч {batch_num}/{total_batches} ОШИБКА: {e}")
            continue

    return all_orders


def get_earliest_sheet_date(sheets: GoogleSheetsService) -> str:
    """Read combined sheet, return earliest date column as 'YYYY-MM-DD'."""
    from config import SHEET_COMBINED
    SUMMARY_ROWS = 4
    META_COLS = 5

    ws = sheets.get_worksheet(SHEET_COMBINED)
    all_data = sheets._safe_call(ws.get_all_values)

    if not all_data or len(all_data) <= SUMMARY_ROWS:
        return None

    headers = all_data[SUMMARY_ROWS]
    date_columns = [d.strip().lstrip("'") for d in headers[META_COLS:] if d.strip()]

    if not date_columns:
        return None

    dates = []
    for d in date_columns:
        try:
            dt = datetime.strptime(d, '%d.%m.%Y')
            dates.append(dt)
        except ValueError:
            continue

    if not dates:
        return None

    earliest = min(dates)
    print(f"Earliest date on sheet: {earliest.strftime('%d.%m.%Y')}")
    return earliest.strftime('%Y-%m-%d')


def get_all_nm_ids() -> dict:
    """Get all active nm_ids per IP from articles table."""
    from db import get_cursor
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


def backfill(days_back: int = 45, dry_run: bool = False):
    print(f"=== Backfill orders: {days_back} days back ===")
    print(f"Dry run: {dry_run}")

    init_db()

    sheets = GoogleSheetsService()
    earliest_str = get_earliest_sheet_date(sheets)

    if not earliest_str:
        print("No dates found on sheet. Nothing to backfill from.")
        return

    earliest = datetime.strptime(earliest_str, '%Y-%m-%d')
    backfill_start = earliest - timedelta(days=days_back)
    backfill_end = earliest - timedelta(days=1)

    print(f"Backfill range: {backfill_start.strftime('%d.%m.%Y')} → {backfill_end.strftime('%d.%m.%Y')}")

    ips_nm_ids = get_all_nm_ids()
    if not ips_nm_ids:
        print("No articles in DB. Run main collection first.")
        return

    for ip, nm_ids in ips_nm_ids.items():
        print(f"\n[{ip}] {len(nm_ids)} артикулов")

    if dry_run:
        print("\n--- DRY RUN: would fetch orders for dates above ---")
        return

    all_collected = {}

    chunk_start = backfill_start
    while chunk_start <= backfill_end:
        chunk_end = min(chunk_start + timedelta(days=6), backfill_end)
        start_str = chunk_start.strftime('%Y-%m-%d')
        end_str = chunk_end.strftime('%Y-%m-%d')

        print(f"\n--- Chunk: {start_str} → {end_str} ---")

        for ip, nm_ids in ips_nm_ids.items():
            orders = get_orders_for_period(ip, nm_ids, start_str, end_str)
            if orders:
                insert_orders(orders)
                print(f"  [{ip}] Saved {len(orders)} records to DB")

                for record in orders:
                    ip_r, nm_id, dt = record[0], record[1], record[2]
                    orders_count = record[5]
                    date_key = dt if isinstance(dt, str) else dt.strftime('%Y-%m-%d') if hasattr(dt, 'strftime') else str(dt)
                    all_collected.setdefault(date_key, {})
                    key = (ip_r, str(nm_id))
                    all_collected[date_key][key] = all_collected[date_key].get(key, 0) + (orders_count or 0)

        chunk_start = chunk_end + timedelta(days=1)
        if chunk_start <= backfill_end:
            print("Пауза 5с между чанками...")
            time.sleep(5)

    print(f"\n=== Updating Google Sheet with {len(all_collected)} historical dates ===")

    article_titles = {}
    for row in get_articles_for_sheets():
        article_titles[(row[0], str(row[1]))] = row[2]

    sorted_dates = sorted(all_collected.keys())
    for date_str in sorted_dates:
        date_display = format_date_for_sheets(date_str)
        orders_for_date = all_collected[date_str]

        orders_data = {}
        for (ip, nm_id_str), count in orders_for_date.items():
            title = article_titles.get((ip, nm_id_str), '')
            orders_data[(ip, int(nm_id_str), title)] = count

        print(f"  Sheet update: {date_display} — {len(orders_data)} артикулов")
        sheets.update_combined_sheet(orders_data, {}, date_display)
        time.sleep(2)

    print(f"\n=== Backfill complete ===")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Backfill historical orders from WB Sales Funnel')
    parser.add_argument('--days', type=int, default=45, help='Days to go back from earliest sheet date (default: 45)')
    parser.add_argument('--dry-run', action='store_true', help='Show plan without fetching')
    args = parser.parse_args()

    backfill(days_back=args.days, dry_run=args.dry_run)
