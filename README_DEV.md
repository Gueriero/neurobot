# Wildberries Sales & Stock Tracker - Development Documentation

## Overview

Система для ежедневного сбора данных по заказам и остаткам с Wildberries для 3 ИП (Усатюк, Кузнецова, Новгородцев).

**Дата создания:** 28.04.2026

---

## Стек технологий

- **Python 3.14** (Windows)
- **PostgreSQL 18** (user: cardgit)
- **gspread** - работа с Google Sheets
- **psycopg2** - работа с PostgreSQL
- **requests** - HTTP клиент

---

## Структура проекта

```
f:\neurobot\
├── SPEC.md              # Спецификация проекта
├── README.md            # Краткая документация
├── README_DEV.md        # Этот файл
├── requirements.txt     # Python зависимости
├── .env                 # API ключи и настройки (НЕ комитить!)
├── credentials.json     # Google Service Account (НЕ комитить!)
├── CONVERSATION.txt    # История переговоров
└── src/
    ├── __init__.py
    ├── config.py       # Загрузка настроек из .env
    ├── db.py           # Операции с PostgreSQL
    ├── wb_api.py       # Клиенты для WB API
    ├── sheets_service.py # Работа с Google Sheets
    ├── main.py         # Точка входа
    └── init_db.py      # Инициализация БД
```

---

## База данных PostgreSQL

### Подключение

```python
psycopg2.connect(
    host='localhost',
    port=5432,
    database='wb_tracker',
    user='cardgit',
    password='cardgit_secret'
)
```

### Таблицы

#### 1. articles - справочник артикулов
```sql
CREATE TABLE articles (
    id SERIAL PRIMARY KEY,
    ip VARCHAR(10) NOT NULL,           -- 'us', 'kuz', 'nov'
    nm_id INTEGER NOT NULL,            -- Артикул WB
    vendor_code VARCHAR(100),
    brand VARCHAR(100),
    title VARCHAR(500),
    subject_name VARCHAR(200),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ip, nm_id)
);
```

#### 2. orders_raw - сырые данные заказов
```sql
CREATE TABLE orders_raw (
    id SERIAL PRIMARY KEY,
    ip VARCHAR(10) NOT NULL,
    nm_id INTEGER NOT NULL,
    dt DATE NOT NULL,
    open_card_count INTEGER,
    add_to_cart_count INTEGER,
    orders_count INTEGER,
    orders_sum_rub INTEGER,
    buyouts_count INTEGER,
    buyouts_sum_rub INTEGER,
    cancel_count INTEGER,
    cancel_sum_rub INTEGER,
    add_to_cart_conversion INTEGER,
    cart_to_order_conversion INTEGER,
    buyout_percent INTEGER,
    add_to_wishlist INTEGER,
    currency VARCHAR(10),
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### 3. stocks_raw - сырые данные остатков
```sql
CREATE TABLE stocks_raw (
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
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Индексы
```sql
CREATE INDEX idx_orders_ip_nm_dt ON orders_raw(ip, nm_id, dt);
CREATE INDEX idx_stocks_ip_nm ON stocks_raw(ip, nm_id);
CREATE INDEX idx_articles_ip_nm ON articles(ip, nm_id);
```

---

## Google Sheets

### Spreadsheet ID
`1n2u_4ruDrKoOffxC4k7zfvlrWiB8ETjyLI1p3eIEVmQ`

### Листы

#### 1. "Заказы"
- **Структура:** ИП | Артикул | Наименование | Маркетплейс | День1 | День2 | ...
- **В ячейках:** ordersCount (штуки)
- **Обновление:** инкрементальное, добавляется колонка за новый день

#### 2. "Остатки по складам"
- **Структура:** ИП | Артикул | Наименование | Маркетплейс | Склад | День1 | День2 | ...
- **В ячейках:** quantity (остаток на складе)
- **В примечании (hover):** "В пути к клиенту: X\nВ пути от клиента: Y"
- **Обновление:** инкрементальное

#### 3. "Себестоимость"
- **Структура:** ИП | Артикул | Наименование | Себестоимость единицы
- **Заполнение:** вручную пользователем

#### 4. "Себестоимость всех остатков"
- **Структура:** ИП | Артикул | Наименование | Себестоимость единицы | Всего остатков | Общая себестоимость
- **Всего остатков** = quantity + inWayToClient + inWayFromClient (текущий срез)
- **Общая себестоимость** = Себестоимость единицы × Всего остатков

---

## Wildberries API

### Эндпоинты

#### 1. Cards List - список артикулов
- **URL:** `POST https://content-api.wildberries.ru/content/v2/get/cards/list`
- **Токен:** категория "Контент" или "Продвижение"
- **Лимит:** 100 запросов/мин
- **Пагинация:** через cursor, limit=100
- **Возвращает:** nmID, vendorCode, brand, title, subjectName

#### 2. Stocks - остатки по складам
- **URL:** `POST https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses`
- **Токен:** категория "Аналитика"
- **Лимит:** 3 запроса/мин, интервал 20 сек, burst 1
- **Все поля опциональны** - без payload возвращает все данные
- **Возвращает:** nmId, chrtId, warehouseId, warehouseName, regionName, quantity, inWayToClient, inWayFromClient

#### 3. Orders - история заказов
- **URL:** `POST https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products/history`
- **Токен:** категория "Аналитика"
- **Лимит:** 3 запроса/мин, интервал 20 сек, batch 20 nmIds
- **max days_back:** 7
- **Важно:** API возвращает nmId (camelCase), НЕ nmID!

### Rate Limits (КРИТИЧНО!)

| Эндпоинт | Лимит | Интервал | Батч |
|-----------|-------|----------|-------|
| Orders | 3/мин | 20 сек | 20 nmIds |
| Stocks | 3/мин | 20 сек | - |
| Cards | 100/мин | - | 100 |

**НЕ отправлять запросы чаще чем каждые 20 секунд на каждый IP!**

При превышении лимита - 429 Too Many Requests, блокировка на ~1 минуту.

---

## Запуск

### Инициализация БД
```bash
python -c "import sys; sys.path.insert(0, 'src'); from db import init_db; init_db()"
```

### Ручной запуск
```powershell
& c:\python314\python.exe f:/neurobot/src/main.py
```

### С параметрами
```powershell
python -m src.main --once    # один раз
python -m src.main --schedule # по расписанию
python -m src.init_db         # инициализация БД
```

---

## known Issues и решения

### 1. PostgreSQL permissions
**Проблема:** "no access to schema public"
**Решение:**
```sql
GRANT ALL ON SCHEMA public TO cardgit;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO cardgit;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO cardgit;
ALTER TABLE articles OWNER TO cardgit;
ALTER TABLE orders_raw OWNER TO cardgit;
ALTER TABLE stocks_raw OWNER TO cardgit;
```

### 2. pg_hba.conf trust
Для временного изменения аутентификации:
```
host    all    all    127.0.0.1/32    trust
```
После изменений вернуть обратно!

### 3. Orders API nmId vs nmID
API возвращает `nmId` (camelCase), а не `nmID`!
В коде парсинга использовать `product.get('nmId')`

### 4. Stocks API payload
Версия `/api/v1/stocks-report/wb-warehouses` принимает пустой payload.
Версия `/api/v2/stocks-report/products/products` требует offset, limit и другие поля.

### 5. Google Sheets API limits
Лимит ~60 запросов/мин на пользователя.
Оптимизировать: собирать данные в батчи, использовать get_all_values() вместо множественных cell().

---

## Текущий статус (28.04.2026)

### Работает:
- ✅ KUZ токен - валиден
- ✅ NOV токен - валиден
- ❌ US токен - пустой (нужен новый)
- ✅ Сбор артикулов
- ✅ Сбор остатков
- ❌ Сбор заказов - был 0 из-за nmId vs nmID (исправлено)
- ✅ Сохранение в PostgreSQL (без дублей)
- ✅ Обновление Google Sheets (оптимизировано)

### TODO:
1. Получить US токен из ЛК Wildberries
2. Протестировать полный цикл сбора данных
3. Настроить scheduler на 06:00

---

## Файлы памяти Claude Code

```
C:\Users\gueri\.claude\projects\f--neurobot\memory\
├── MEMORY.md
├── user_role.md
└── feedback_wb_api_limits.md
```

---

## Конфигурация .env

```env
# Wildberries API keys
WB_API_KEY_US=          # Пустой - нужен новый
WB_API_KEY_KUZ=<key>    # Рабочий
WB_API_KEY_NOV=<key>   # Рабочий

# Google Sheets
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_SHEETS_ID=1n2u_4ruDrKoOffxC4k7zfvlrWiB8ETjyLI1p3eIEVmQ

# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=wb_tracker
DB_USER=cardgit
DB_PASSWORD=cardgit_secret
```

---

## Полезные SQL запросы

```sql
-- Проверить данные в БД
SELECT ip, COUNT(*) FROM articles GROUP BY ip;
SELECT ip, COUNT(*) FROM stocks_raw GROUP BY ip;
SELECT ip, COUNT(*) FROM orders_raw GROUP BY ip;

-- Очистить таблицы
TRUNCATE TABLE orders_raw;
DELETE FROM stocks_raw WHERE DATE(created_at) = CURRENT_DATE;
DELETE FROM articles; -- осторожно!

-- Проверить最新的 данные
SELECT * FROM stocks_raw ORDER BY created_at DESC LIMIT 10;
SELECT * FROM orders_raw ORDER BY created_at DESC LIMIT 10;
```