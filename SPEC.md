# Wildberries Sales & Stock Tracker

## Overview

Система для ежедневного сбора данных по заказам и остаткам с Wildberries по трём ИП (Усатюк, Кузнецова, Новгородцев). Данные хранятся в PostgreSQL и выгружаются в Google Sheets.

## Architecture

### Data Flow
1. **06:00 МСК** — запуск сбора данных
2. Для каждого ИП (параллельно):
   - Cards List API → обновление справочника артикулов
   - Stocks API → остатки по складам
   - Orders API → данные по заказам (батчами по 20)
3. Данные сохраняются в PostgreSQL (raw)
4. Формируются сводные данные для Google Sheets
5. Google Sheets обновляется (добавляются колонки за новый день)

### API Endpoints

| Цель | Endpoint | Лимит |
|------|----------|-------|
| Список артикулов | `POST /content/v2/get/cards/list` | 100/мин |
| Остатки (со складами) | `POST /api/v2/stocks-report/products/products` | 3/мин, 20 сек интервал |
| Заказы (по дням) | `POST /api/analytics/v3/sales-funnel/products/history` | 3/мин, 20 сек интервал, макс 20 nmIds/батч |

### Environment (.env)

```env
WB_API_KEY_US=<token>
WB_API_KEY_KUZ=<token>
WB_API_KEY_NOV=<token>
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_SHEETS_ID=<spreadsheet_id>
```

## Database Schema (PostgreSQL)

### Table: articles
```sql
CREATE TABLE articles (
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
);
```

### Table: orders_raw
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

### Table: stocks_raw
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

## Google Sheets Structure

### Sheet: "Заказы"
| ИП | Артикул | Наименование | Маркетплейс | День1 | День2 | ... |
|---|---|---|---|---|---|---|

- Колонки: ИП, Артикул, Наименование, Маркетплейс, затем колонки с датами
- В ячейках: ordersCount (штуки)
- Инкрементальное обновление: добавляется колонка за новый день

### Sheet: "Остатки по складам"
| ИП | Артикул | Наименование | Маркетплейс | Склад | День1 | День2 | ... |
|---|---|---|---|---|---|---|---|

- Колонки: ИП, Артикул, Наименование, Маркетплейс, Склад, затем колонки с датами
- В ячейках: quantity (остаток на складе)
- В примечании (hover): "В пути к клиенту: X\nВ пути от клиента: Y"
- Инкрементальное обновление

### Sheet: "Себестоимость"
| ИП | Артикул | Наименование | Себестоимость единицы |
|---|---|---|---|

- Заполняется вручную
- Данные берутся для листа "Себестоимость всех остатков"

### Sheet: "Себестоимость всех остатков"
| ИП | Артикул | Наименование | Себестоимость единицы | Всего остатков | Общая себестоимость |
|---|---|---|---|---|---|

- Всего остатков = quantity + inWayToClient + inWayFromClient (текущий срез)
- Общая себестоимость = Себестоимость единицы × Всего остатков
- Обновляется в 06:00 вместе с остатками

## Process Flow

### Daily Collection (06:00)
1. Для каждого ИП параллельно:
   - Получить список артикулов (Cards List) → обновить таблицу articles
   - Получить остатки (Stocks API) → записать в stocks_raw
   - Для артикулов с остатками (qty > 0 or inWay > 0):
     - Получить данные заказов (Orders API, батчи по 20) → записать в orders_raw
2. Обновить Google Sheets (добавить колонку дня, обновить ячейки)

### Timing
- Orders: 250 артикулов / 20 = 12.5 батчей × 20 сек = ~250 сек ≈ 4 мин на ИП
- Stocks: 1 запрос на ИП (или пагинация) ≈ секунды
- Cards List: пагинация, лимит 100 на запрос
- Общее время: ~4-5 мин на 3 ИП параллельно

## Files

```
src/
  config.py        - настройки из .env
  db.py            - PostgreSQL operations
  wb_api.py        - Wildberries API clients
  sheets_service.py - Google Sheets operations
  main.py          - точка входа, scheduler
.env                - API ключи
requirements.txt    - зависимости
```