#!/bin/bash
set -e

# Pass environment variables to cron
printenv | grep -v "no_proxy" >> /etc/environment

# Initialize database
cd /app
python src/main.py --init-db

# Start cron in background
cron

echo "=== WB Tracker started. Cron scheduled at 06:00 MSK ==="
echo "=== Tailing log... ==="

# Keep container alive and show logs
tail -f /var/log/wb_tracker.log
