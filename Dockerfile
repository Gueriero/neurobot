FROM python:3.12-slim

ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY credentials.json .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN echo "0 1 * * * cd /app && /usr/local/bin/python src/main.py --once >> /var/log/wb_tracker.log 2>&1" > /etc/cron.d/wb-tracker \
    && chmod 0644 /etc/cron.d/wb-tracker \
    && crontab /etc/cron.d/wb-tracker \
    && touch /var/log/wb_tracker.log

ENTRYPOINT ["/entrypoint.sh"]
