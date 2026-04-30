#!/usr/bin/env python3
"""
Wildberries Sales & Stock Tracker
Main entry point
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import WB_API_KEYS
from db import (
    init_db, insert_articles, insert_orders, insert_stocks,
    get_orders_for_date, get_stocks_latest, get_articles_for_sheets,
)
from wb_api import fetch_all_data_for_ip
from sheets_service import GoogleSheetsService, format_date_for_sheets

def run_collection():
    """
    Main data collection routine.
    Runs at 06:00 every day.
    """
    print(f"=== Starting collection at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    print("Initializing database...")
    init_db()

    ips = list(WB_API_KEYS.keys())
    results = {}

    print(f"Fetching data for {len(ips)} IPs: {ips}")

    with ThreadPoolExecutor(max_workers=len(ips)) as executor:
        futures = {executor.submit(fetch_all_data_for_ip, ip, 7): ip for ip in ips}

        for future in as_completed(futures):
            ip = futures[future]
            try:
                results[ip] = future.result()
                print(f"✓ {ip} completed")
            except Exception as e:
                print(f"✗ {ip} failed: {e}")
                results[ip] = {'articles': [], 'stocks': [], 'orders': []}

    # Save to database
    print("\nSaving to database...")
    for ip, data in results.items():
        if data['articles']:
            insert_articles(data['articles'])
            print(f"  {ip}: saved {len(data['articles'])} articles")
        if data['stocks']:
            insert_stocks(data['stocks'])
            print(f"  {ip}: saved {len(data['stocks'])} stock records")
        if data['orders']:
            insert_orders(data['orders'])
            print(f"  {ip}: saved {len(data['orders'])} order records")

    # Update Google Sheets
    print("\nUpdating Google Sheets...")
    update_google_sheets()

    print(f"=== Collection completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")


def update_google_sheets():
    """Update Google Sheets from DB data."""
    sheets = GoogleSheetsService()

    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    date_display = format_date_for_sheets(yesterday)
    ips = list(WB_API_KEYS.keys())

    print(f"  Дата для таблицы: {date_display}")

    article_titles = {}
    for row in get_articles_for_sheets():
        article_titles[(row[0], row[1])] = row[2]
    print(f"  Загружено {len(article_titles)} артикулов из БД")

    orders_data = {}
    for ip in ips:
        rows = get_orders_for_date(ip, yesterday) 
        for row in rows:
            key = (row[0], row[1], article_titles.get((row[0], row[1]), ''))
            orders_count = row[3] if row[3] else 0
            if key in orders_data:
                orders_data[key] += orders_count
            else:
                orders_data[key] = orders_count
    print(f"  Заказы из БД: {len(orders_data)} артикулов за {yesterday}")

    stocks_data = {}
    for ip in ips:
        for stock in get_stocks_latest(ip):
            key = (ip, stock[1], article_titles.get((ip, stock[1]), ''), stock[4])
            qty = stock[6] if stock[6] else 0
            iwt = stock[7] if stock[7] else 0
            iwf = stock[8] if stock[8] else 0
            if key in stocks_data:
                stocks_data[key]['quantity'] += qty
                stocks_data[key]['in_way_to'] += iwt
                stocks_data[key]['in_way_from'] += iwf
            else:
                stocks_data[key] = {
                    'quantity': qty,
                    'in_way_to': iwt,
                    'in_way_from': iwf,
                }
    print(f"  Остатки из БД: {len(stocks_data)} позиций по складам")

    sheets.update_combined_sheet(orders_data, stocks_data, date_display)

    update_cost_total_sheet(sheets, stocks_data, article_titles)


def update_cost_total_sheet(sheets: GoogleSheetsService, stocks_data: dict, article_titles: dict):
    """Update the cost total sheet."""
    totals = []
    seen = {}

    for (ip, nm_id, title, _warehouse), stock_info in stocks_data.items():
        key = (ip, str(nm_id))
        if key in seen:
            seen[key]['qty'] += stock_info['quantity'] + stock_info['in_way_to'] + stock_info['in_way_from']
            continue
        seen[key] = {
            'title': title or article_titles.get((ip, nm_id), ''),
            'qty': stock_info['quantity'] + stock_info['in_way_to'] + stock_info['in_way_from'],
        }

    for key, info in seen.items():
        if str(key[1]).startswith(','):
            continue
        totals.append((key[0], key[1], info['title'], info['qty']))

    if totals:
        sheets.update_cost_total_sheet(totals)
        print(f"  Себестоимость: {len(totals)} артикулов")


def run_once():
    """Run collection once (for manual run)."""
    run_collection()


def run_scheduler():
    """Run as scheduler (check every minute)."""
    import schedule
    import time

    # Schedule for 06:00 Moscow time
    schedule.every().day.at("01:00").do(run_collection)

    print("Scheduler started. Running at 01:00 daily.")
    print("Press Ctrl+C to stop.")

    while True:
        schedule.run_pending()
        time.sleep(60)


def init_database():
    """Initialize the database tables."""
    print("Initializing database...")
    init_db()
    print("Database initialized successfully.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Wildberries Sales & Stock Tracker')
    parser.add_argument('--init-db', action='store_true', help='Initialize database tables')
    parser.add_argument('--once', action='store_true', help='Run collection once')
    parser.add_argument('--schedule', action='store_true', help='Run as scheduler')
    args = parser.parse_args()

    if args.init_db:
        init_database()
    elif args.once:
        run_once()
    elif args.schedule:
        run_scheduler()
    else:
        # Default: run once
        run_once()