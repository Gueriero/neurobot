import os
from dotenv import load_dotenv

load_dotenv()

# API keys
WB_API_KEYS = {
    k: v for k, v in {
        'us': os.getenv('WB_API_KEY_US'),
        'kuz': os.getenv('WB_API_KEY_KUZ'),
        'nov': os.getenv('WB_API_KEY_NOV'),
    }.items() if v
}

# Google Sheets
GOOGLE_CREDENTIALS_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json')
GOOGLE_SHEETS_ID = os.getenv('GOOGLE_SHEETS_ID')

# Database
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'dbname': os.getenv('DB_NAME', 'wb_tracker'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', ''),
}

# WB API endpoints
WB_API_BASE = 'https://seller-analytics-api.wildberries.ru'
WB_CONTENT_API_BASE = 'https://content-api.wildberries.ru'

# Limits
ORDERS_BATCH_SIZE = 20
ORDERS_RATE_LIMIT_SEC = 20

# Sheet names
SHEET_ORDERS = 'Заказы'
SHEET_STOCKS = 'Остатки по складам'
SHEET_COST = 'Себестоимость'
SHEET_COST_TOTAL = 'Себестоимость всех остатков'
SHEET_COMBINED = 'Заказы и Остатки'