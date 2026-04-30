# Wildberries Sales & Stock Tracker

Система для ежедневного сбора данных по заказам и остаткам с Wildberries.

## Установка

### 1. Зависимости

```bash
pip install -r requirements.txt
```

### 2. Настройка .env

Скопируйте `.env.example` в `.env` и заполните:

```env
# API ключи WB (по одному на ИП)
WB_API_KEY_US=your_token
WB_API_KEY_KUZ=your_token
WB_API_KEY_NOV=your_token

# Google Sheets
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_SHEETS_ID=your_spreadsheet_id

# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=wb_tracker
DB_USER=postgres
DB_PASSWORD=your_password
```

### 3. Google Cloud Setup

1. Создайте проект в Google Cloud Console
2. Скачайте JSON-файл сервисного аккаунта как `credentials.json`
3. Поделите доступом к таблице Google Sheets с сервисным аккаунтом

### 4. PostgreSQL

Создайте базу данных:

```sql
CREATE DATABASE wb_tracker;
```

### 5. Инициализация базы

```bash
python -m src.init_db
```

## Запуск

### Вручную (один раз)

```bash
python -m src.main --once
```

### По расписанию (06:00 ежедневно)

```bash
python -m src.main --schedule
```

Или используйте cron (Linux) / Task Scheduler (Windows):

```bash
# Linux: добавьте в crontab
0 6 * * * /path/to/python /path/to/src/main.py --once
```

## Структура данных

### Google Sheets

| Лист | Описание |
|------|----------|
| Заказы | Динамика заказов по артикулам и дням |
| Остатки по складам | Динамика остатков с разбивкой по складам |
| Себестоимость | Справочник себестоимости (ручной ввод) |
| Себестоимость всех остатков | Расчёт общей стоимости остатков |

### PostgreSQL Tables

- `articles` - справочник артикулов
- `orders_raw` - сырые данные заказов
- `stocks_raw` - сырые данные остатков