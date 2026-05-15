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
