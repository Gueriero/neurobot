import psycopg2
from psycopg2.extras import execute_values
from contextlib import contextmanager
from config import DB_CONFIG


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_cursor():
    """Context manager for database cursors."""
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()


def init_db():
    """Initialize database tables."""
    with get_cursor() as cur:
        # articles table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id SERIAL PRIMARY KEY,
                ip VARCHAR(10) NOT NULL,
                nm_id INTEGER NOT NULL,
                vendor_code VARCHAR(100),
                brand VARCHAR(100),
                title VARCHAR(500),
                subject_name VARCHAR(200),
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(ip, nm_id)
            )
        """)

        # orders_raw table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders_raw (
                id SERIAL PRIMARY KEY,
                ip VARCHAR(10) NOT NULL,
                nm_id INTEGER NOT NULL,
                dt DATE NOT NULL,
                open_card_count INTEGER,
                add_to_cart_count INTEGER,
                orders_count INTEGER,
                orders_sum_rub NUMERIC(12,2),
                buyouts_count INTEGER,
                buyouts_sum_rub NUMERIC(12,2),
                cancel_count INTEGER,
                cancel_sum_rub NUMERIC(12,2),
                add_to_cart_conversion NUMERIC(6,2),
                cart_to_order_conversion NUMERIC(6,2),
                buyout_percent NUMERIC(6,2),
                add_to_wishlist INTEGER,
                currency VARCHAR(10),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # stocks_raw table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stocks_raw (
                id SERIAL PRIMARY KEY,
                ip VARCHAR(10) NOT NULL,
                nm_id INTEGER NOT NULL,
                chrt_id INTEGER,
                warehouse_id INTEGER,
                warehouse_name VARCHAR(200),
                region_name VARCHAR(200),
                quantity INTEGER,
                in_way_to_client INTEGER,
                in_way_from_client INTEGER,
                snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
                created_at TIMESTAMP DEFAULT NOW(),
                CONSTRAINT stocks_unique UNIQUE (ip, nm_id, warehouse_id, snapshot_date)
            )
        """)

        cur.execute("""
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'orders_unique'
        """)
        if not cur.fetchone():
            cur.execute("""
                ALTER TABLE orders_raw
                ADD CONSTRAINT orders_unique UNIQUE (ip, nm_id, dt)
            """)

        cur.execute("""
            DO $$ BEGIN
                ALTER TABLE stocks_raw ADD COLUMN IF NOT EXISTS snapshot_date DATE DEFAULT CURRENT_DATE;
            EXCEPTION WHEN undefined_column THEN NULL;
            END $$
        """)

        cur.execute("""
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'stocks_unique'
        """)
        if not cur.fetchone():
            cur.execute("""
                SELECT ip, nm_id, warehouse_id, snapshot_date, COUNT(*) as cnt
                FROM stocks_raw
                GROUP BY ip, nm_id, warehouse_id, snapshot_date
                HAVING COUNT(*) > 1
            """)
            duplicates = cur.fetchall()
            if duplicates:
                cur.execute("""
                    DELETE FROM stocks_raw a
                    USING stocks_raw b
                    WHERE a.id < b.id
                    AND a.ip = b.ip
                    AND a.nm_id = b.nm_id
                    AND a.warehouse_id = b.warehouse_id
                    AND a.snapshot_date = b.snapshot_date
                """)
            cur.execute("""
                ALTER TABLE stocks_raw ADD CONSTRAINT stocks_unique UNIQUE (ip, nm_id, warehouse_id, snapshot_date)
            """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_stocks_ip_snapshot ON stocks_raw(ip, snapshot_date)")


def insert_articles(articles_data: list):
    """Insert or update articles."""
    with get_cursor() as cur:
        query = """
            INSERT INTO articles (ip, nm_id, vendor_code, brand, title, subject_name, is_active)
            VALUES %s
            ON CONFLICT (ip, nm_id) DO UPDATE SET
                vendor_code = EXCLUDED.vendor_code,
                brand = EXCLUDED.brand,
                title = EXCLUDED.title,
                subject_name = EXCLUDED.subject_name,
                is_active = EXCLUDED.is_active,
                updated_at = NOW()
        """
        execute_values(cur, query, articles_data)


def insert_orders(orders_data: list):
    """Insert orders raw data with deduplication on (ip, nm_id, dt)."""
    if not orders_data:
        return
    with get_cursor() as cur:
        query = """
            INSERT INTO orders_raw (ip, nm_id, dt, open_card_count, add_to_cart_count,
                orders_count, orders_sum_rub, buyouts_count, buyouts_sum_rub,
                cancel_count, cancel_sum_rub, add_to_cart_conversion,
                cart_to_order_conversion, buyout_percent, add_to_wishlist, currency)
            VALUES %s
            ON CONFLICT ON CONSTRAINT orders_unique DO UPDATE SET
                open_card_count = EXCLUDED.open_card_count,
                add_to_cart_count = EXCLUDED.add_to_cart_count,
                orders_count = EXCLUDED.orders_count,
                orders_sum_rub = EXCLUDED.orders_sum_rub,
                buyouts_count = EXCLUDED.buyouts_count,
                buyouts_sum_rub = EXCLUDED.buyouts_sum_rub,
                cancel_count = EXCLUDED.cancel_count,
                cancel_sum_rub = EXCLUDED.cancel_sum_rub,
                add_to_cart_conversion = EXCLUDED.add_to_cart_conversion,
                cart_to_order_conversion = EXCLUDED.cart_to_order_conversion,
                buyout_percent = EXCLUDED.buyout_percent,
                add_to_wishlist = EXCLUDED.add_to_wishlist
        """
        execute_values(cur, query, orders_data)


def insert_stocks(stocks_data: list):
    """Insert stocks raw data with upsert by (ip, nm_id, warehouse_id, snapshot_date)."""
    if not stocks_data:
        return

    seen = {}
    for record in stocks_data:
        ip, nm_id, chrt_id, warehouse_id, warehouse_name, region_name, quantity, in_way_to, in_way_from = record
        key = (ip, nm_id, warehouse_id)
        qty = quantity if quantity else 0
        iwt = in_way_to if in_way_to else 0
        iwf = in_way_from if in_way_from else 0
        if key in seen:
            seen[key]['quantity'] += qty
            seen[key]['in_way_to_client'] += iwt
            seen[key]['in_way_from_client'] += iwf
        else:
            seen[key] = {
                'ip': ip, 'nm_id': nm_id, 'chrt_id': chrt_id,
                'warehouse_id': warehouse_id, 'warehouse_name': warehouse_name,
                'region_name': region_name, 'quantity': qty,
                'in_way_to_client': iwt, 'in_way_from_client': iwf
            }

    deduped = [
        (v['ip'], v['nm_id'], v['chrt_id'], v['warehouse_id'], v['warehouse_name'],
         v['region_name'], v['quantity'], v['in_way_to_client'], v['in_way_from_client'])
        for v in seen.values()
    ]

    with get_cursor() as cur:
        query = """
            INSERT INTO stocks_raw (ip, nm_id, chrt_id, warehouse_id, warehouse_name,
                region_name, quantity, in_way_to_client, in_way_from_client, snapshot_date)
            VALUES %s
            ON CONFLICT ON CONSTRAINT stocks_unique DO UPDATE SET
                chrt_id = EXCLUDED.chrt_id,
                warehouse_name = EXCLUDED.warehouse_name,
                region_name = EXCLUDED.region_name,
                quantity = EXCLUDED.quantity,
                in_way_to_client = EXCLUDED.in_way_to_client,
                in_way_from_client = EXCLUDED.in_way_from_client
        """
        template = '(%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_DATE)'
        execute_values(cur, query, deduped, template=template)


def has_data_for_today(ip: str) -> dict:
    """Check if data was already collected today for this IP."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM stocks_raw WHERE ip = %s AND snapshot_date = CURRENT_DATE",
            (ip,)
        )
        has_stocks = cur.fetchone()[0] > 0

        cur.execute(
            "SELECT COUNT(*) FROM orders_raw WHERE ip = %s AND dt = CURRENT_DATE - INTERVAL '1 day'",
            (ip,)
        )
        has_orders = cur.fetchone()[0] > 0

        return {'stocks': has_stocks, 'orders': has_orders}


def get_articles_with_stocks(ip: str) -> list:
    """Get articles that have stock > 0 or in_way > 0."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT a.ip, a.nm_id, a.title
            FROM articles a
            JOIN stocks_raw s ON a.ip = s.ip AND a.nm_id = s.nm_id
            WHERE a.ip = %s
            AND (s.quantity > 0 OR s.in_way_to_client > 0 OR s.in_way_from_client > 0)
        """, (ip,))
        return cur.fetchall()


def get_articles_all(ip: str) -> list:
    """Get all active articles for an IP."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT ip, nm_id, title FROM articles
            WHERE ip = %s AND is_active = TRUE
        """, (ip,))
        return cur.fetchall()


def get_orders_for_date(ip: str, date) -> list:
    """Get orders for specific date and IP."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT ip, nm_id, dt, orders_count FROM orders_raw
            WHERE ip = %s AND dt = %s
        """, (ip, date))
        return cur.fetchall()


def get_stocks_latest(ip: str) -> list:
    """Get latest stock data for an IP."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (nm_id, warehouse_id)
                ip, nm_id, chrt_id, warehouse_id, warehouse_name,
                region_name, quantity, in_way_to_client, in_way_from_client
            FROM stocks_raw
            WHERE ip = %s
            ORDER BY nm_id, warehouse_id, snapshot_date DESC
        """, (ip,))
        return cur.fetchall()


def get_articles_for_sheets() -> list:
    """Get all articles with titles for sheets."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ip, nm_id, title
            FROM articles
            WHERE is_active = TRUE
            ORDER BY ip, nm_id
        """)
        return cur.fetchall()