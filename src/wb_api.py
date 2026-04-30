import time
import requests
from typing import List, Dict, Any
from config import (
    WB_API_KEYS,
    WB_API_BASE,
    WB_CONTENT_API_BASE,
    ORDERS_BATCH_SIZE,
    ORDERS_RATE_LIMIT_SEC,
)


class WBAPIClient:
    """Base client for Wildberries API."""

    def __init__(self, ip_key: str):
        self.api_key = WB_API_KEYS.get(ip_key)
        if not self.api_key:
            raise ValueError(f"No API key for IP: {ip_key}")
        self.ip = ip_key

    def _headers(self) -> dict:
        return {
            'Authorization': self.api_key,
            'Content-Type': 'application/json'
        }

    def _rate_limit(self):
        """Apply rate limiting between requests."""
        time.sleep(ORDERS_RATE_LIMIT_SEC)


class CardsListClient(WBAPIClient):
    """Client for /content/v2/get/cards/list endpoint."""

    BASE_URL = f'{WB_CONTENT_API_BASE}/content/v2/get/cards/list'

    def get_all_cards(self) -> List[Dict]:
        """Get all cards with pagination."""
        all_cards = []
        cursor = None

        while True:
            payload = {
                'settings': {
                    'sort': {'ascending': True},
                    'cursor': {
                        'limit': 100,
                        **(cursor or {})
                    },
                    'filter': {
                        'withPhoto': -1
                    }
                }
            }

            response = requests.post(
                self.BASE_URL,
                headers=self._headers(),
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            cards = data.get('cards', [])
            all_cards.extend(cards)

            # Check if there are more pages
            cursor_data = data.get('cursor', {})
            if cursor_data.get('total', 0) < cursor_data.get('limit', 100):
                break

            cursor = {
                'updatedAt': cursor_data.get('updatedAt'),
                'nmID': cursor_data.get('nmID')
            }

        return all_cards

    def parse_cards(self, cards: List[Dict]) -> List[tuple]:
        """Parse cards into articles data tuples."""
        return [
            (
                self.ip,
                card.get('nmID'),
                card.get('vendorCode'),
                card.get('brand'),
                card.get('title'),
                card.get('subjectName'),
                True  # is_active
            )
            for card in cards
        ]


class StocksClient(WBAPIClient):
    """Client for /api/analytics/v1/stocks-report/wb-warehouses endpoint."""

    BASE_URL = f'{WB_API_BASE}/api/analytics/v1/stocks-report/wb-warehouses'

    def get_stocks(self) -> List[Dict]:
        """Get current stocks with warehouse breakdown."""
        # This endpoint has all optional fields, returns all stocks by default
        payload = {}

        response = requests.post(
            self.BASE_URL,
            headers=self._headers(),
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()

        return data.get('data', {}).get('items', [])

    def parse_stocks(self, stocks: List[Dict]) -> List[tuple]:
        """Parse stocks into data tuples."""
        return [
            (
                self.ip,
                stock.get('nmId'),
                stock.get('chrtId'),
                stock.get('warehouseId'),
                stock.get('warehouseName'),
                stock.get('regionName'),
                stock.get('quantity', 0),
                stock.get('inWayToClient', 0),
                stock.get('inWayFromClient', 0)
            )
            for stock in stocks
        ]


class OrdersClient(WBAPIClient):
    """Client for /api/analytics/v3/sales-funnel/products/history endpoint."""

    BASE_URL = f'{WB_API_BASE}/api/analytics/v3/sales-funnel/products/history'

    def get_orders_history(self, nm_ids: List[int], days_back: int = 7) -> List[Dict]:
        """
        Get orders history for given nmIds.

        Args:
            nm_ids: List of nmID values
            days_back: Number of days to look back (max 7 for this API)
        """
        from datetime import datetime, timedelta

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

        payload = {
            'selectedPeriod': {
                'start': start_date,
                'end': end_date
            },
            'nmIds': nm_ids,
            'skipDeletedNm': True
        }

        response = requests.post(
            self.BASE_URL,
            headers=self._headers(),
            json=payload,
            timeout=60
        )
        response.raise_for_status()

        return response.json()

    def parse_orders(self, data: List[Dict]) -> List[tuple]:
        """Parse orders history into data tuples."""
        result = []

        for product in data:
            product_info = product.get('product', {})
            history = product.get('history', [])
            nm_id = product_info.get('nmId')

            # Skip if no nm_id
            if not nm_id:
                continue

            for day_data in history:
                date = day_data.get('date')
                if not date:
                    continue

                result.append((
                    self.ip,
                    nm_id,
                    date,
                    day_data.get('openCardCount'),
                    day_data.get('addToCartCount'),
                    day_data.get('orderCount'),
                    day_data.get('orderSum'),
                    day_data.get('buyoutCount'),
                    day_data.get('buyoutSum'),
                    day_data.get('cancelCount'),
                    day_data.get('cancelSum'),
                    day_data.get('addToCartConversion'),
                    day_data.get('cartToOrderConversion'),
                    day_data.get('buyoutPercent'),
                    day_data.get('addToWishlist'),
                    day_data.get('currency', 'RUB')
                ))

        return result


def fetch_articles_for_ip(ip_key: str) -> List[tuple]:
    """Fetch all articles for an IP."""
    client = CardsListClient(ip_key)
    cards = client.get_all_cards()
    return client.parse_cards(cards)


def fetch_stocks_for_ip(ip_key: str) -> List[tuple]:
    """Fetch all stocks for an IP."""
    client = StocksClient(ip_key)
    stocks = client.get_stocks()
    return client.parse_stocks(stocks)


def fetch_orders_for_ip(ip_key: str, nm_ids: List[int], days_back: int = 7) -> List[tuple]:
    """Fetch orders for an IP in batches."""
    client = OrdersClient(ip_key)
    all_orders = []

    total_batches = (len(nm_ids) + ORDERS_BATCH_SIZE - 1) // ORDERS_BATCH_SIZE if nm_ids else 0
    print(f"    [{ip_key}] Sales funnel: {len(nm_ids)} nmIDs → {total_batches} батчей по {ORDERS_BATCH_SIZE}")

    for i in range(0, len(nm_ids), ORDERS_BATCH_SIZE):
        batch = nm_ids[i:i + ORDERS_BATCH_SIZE]
        batch_num = i // ORDERS_BATCH_SIZE + 1

        try:
            data = client.get_orders_history(batch, days_back)
            parsed = client.parse_orders(data)
            all_orders.extend(parsed)
            print(f"    [{ip_key}] Батч {batch_num}/{total_batches} — {len(batch)} nmIDs → {len(parsed)} записей")

            if i + ORDERS_BATCH_SIZE < len(nm_ids):
                print(f"    [{ip_key}] Ждём {ORDERS_RATE_LIMIT_SEC}с (rate limit)...")
                client._rate_limit()

        except Exception as e:
            print(f"    [{ip_key}] Батч {batch_num}/{total_batches} ОШИБКА: {e}")
            continue

    print(f"    [{ip_key}] Sales funnel готов: {len(all_orders)} записей итого")
    return all_orders


def fetch_all_data_for_ip(ip_key: str, days_back: int = 7) -> dict:
    """Fetch all data for a single IP."""
    print(f"Fetching data for IP: {ip_key}")

    print(f"  [{ip_key}] Fetching articles...")
    articles = fetch_articles_for_ip(ip_key)
    print(f"  [{ip_key}] Found {len(articles)} articles")

    stocks = []
    try:
        print(f"  [{ip_key}] Fetching stocks...")
        stocks = fetch_stocks_for_ip(ip_key)
        print(f"  [{ip_key}] Found {len(stocks)} stock records")
    except Exception as e:
        print(f"  [{ip_key}] ⚠ Stocks failed: {e}")
        print(f"  [{ip_key}] Продолжаем с артикулами для заказов")

    if stocks:
        order_nm_ids = list(set([s[1] for s in stocks if s[6] > 0 or s[7] > 0 or s[8] > 0]))
    else:
        order_nm_ids = [a[1] for a in articles]
    print(f"  [{ip_key}] {len(order_nm_ids)} nmIDs для заказов")

    print(f"  [{ip_key}] Fetching orders ({len(order_nm_ids)} nmIDs in batches of {ORDERS_BATCH_SIZE})...")
    orders = fetch_orders_for_ip(ip_key, order_nm_ids, days_back)
    print(f"  [{ip_key}] Found {len(orders)} order records")

    return {
        'articles': articles,
        'stocks': stocks,
        'orders': orders
    }