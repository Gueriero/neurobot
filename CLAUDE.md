# CLAUDE.md

!!! НЕ ЧИТАЙ ДАННЫЕ В ПАПКЕ swagwe_api_wb и не анализируй их, сожрет миллиард токенов!!!!

## Project Type

Python-приложение для сбора данных с Wildberries API, хранения в PostgreSQL и выгрузки в Google Sheets. Деплой через Docker Compose.

## Architecture

**Entry point**: `src/main.py` — CLI с аргументами `--once`, `--schedule`, `--init-db`.

**Модули** (`src/`):
- `config.py` — загрузка `.env`, константы API, листов, лимитов
- `wb_api.py` — клиенты WB API (Cards, Stocks, Orders), сбор данных с батчированием
- `db.py` — PostgreSQL: init, upsert articles/orders/stocks, проверка `has_data_for_today()`
- `sheets_service.py` — Google Sheets: объединённый лист с группировкой, себестоимость
- `init_db.py` — скрипт инициализации БД

**Data flow** (ежедневно в 06:00 МСК):
1. Для каждого ИП параллельно: Cards API → Stocks API → Orders API (батчи по 20)
2. Проверка `has_data_for_today()` — если данные уже есть, API-вызовы пропускаются
3. Upsert в PostgreSQL (articles, stocks_raw, orders_raw)
4. Обновление Google Sheets — объединённый лист + себестоимость

## Three IPs

- `us` — Усатюк
- `kuz` — Кузнецова
- `nov` — Новгородцев

API-ключи в `.env`: `WB_API_KEY_US`, `WB_API_KEY_KUZ`, `WB_API_KEY_NOV`. Пустые ключи автоматически фильтруются.

## Database

PostgreSQL с тремя таблицами:
- `articles` — справочник артикулов, UNIQUE(ip, nm_id), upsert
- `orders_raw` — заказы, UNIQUE(ip, nm_id, dt), upsert с обновлением всех полей
- `stocks_raw` — остатки, UNIQUE(ip, nm_id, warehouse_id, snapshot_date), upsert. Явная колонка `snapshot_date`

Суммы (`orders_sum_rub`, `buyouts_sum_rub`, `cancel_sum_rub`) — тип NUMERIC(12,2). Конверсии — NUMERIC(6,2).

## Google Sheets

### Объединённый лист "Заказы и Остатки"
- Колонки: ИП, Артикул, Наименование, МП, Склад, [даты]
- Строка с пустым складом = заказы (orders_count). Строки со складом = остатки (quantity)
- Строки складов **группируются под плюсик** — свёрнуты по умолчанию, раскрываются по клику
- Сортировка: ИП → Артикул → Склад (пустой первый = строка заказов сверху)
- Примечания на ячейках остатков: "В пути к клиенту: X / от клиента: Y"
- Автоочистка: склады без остатков 60+ дней удаляются
- Каждый запуск: читаем всё → мержим новые данные → сортируем → clear → write → группируем (5-6 API-вызовов)

### Прочие листы
- `Себестоимость` — заполняется вручную
- `Себестоимость всех остатков` — расчёт: себестоимость × (quantity + inWay)
- `Заказы`, `Остатки по складам` — старые раздельные листы (методы в коде остались, но не вызываются из main.py)

## API Rate Limits

- Orders: макс 20 nmIds/запрос, 20 сек между батчами, 3 запроса/мин
- Stocks: 3 запроса/мин, 20 сек интервал
- Cards List: 100/мин, пагинация по 100

## Deployment

**Docker Compose** (`docker-compose.yml`):
- `app` — Python 3.12 + cron (06:00 МСК), timezone Europe/Moscow
- `db` — PostgreSQL 16, данные в volume `pgdata`
- `restart: unless-stopped` — автоперезапуск после ребута

**Деплой на VPS**: `bash deploy.sh user@host` — rsync + SSH + docker compose up

**Локально** (Windows): Task Scheduler задача `WB_Tracker_Daily` запускает `--once` в 06:00

## Environment

- `.env` — API-ключи, креды БД, Google Sheets ID (gitignored)
- `credentials.json` — сервисный аккаунт Google (gitignored)
- `.env.example` — шаблон

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

## Legacy

Папка `swagwe_api_wb/` — старая версия на Google Apps Script (`.gs` файлы), yaml-спецификации API WB. НЕ ЧИТАТЬ — огромный объём, нерелевантно.
