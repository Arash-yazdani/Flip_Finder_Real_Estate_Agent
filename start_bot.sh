#!/bin/bash
# Start the telegram bot cleanly with .env sourced.
set -e
cd "$(dirname "$0")"

echo "==> Killing any existing bot..."
pkill -f telegram_bot.py || true
sleep 1

echo "==> Truncating log..."
: > nohup_bot.log

echo "==> Loading .env..."
set -a
source .env
set +a

if [ -z "${BRIGHT_DATA_API_TOKEN:-}" ]; then
  echo "ERROR: BRIGHT_DATA_API_TOKEN not set after sourcing .env"
  echo "Check that .env has a line like: BRIGHT_DATA_API_TOKEN=your_token_here"
  exit 1
fi
echo "    BRIGHT_DATA_API_TOKEN: set (length=${#BRIGHT_DATA_API_TOKEN})"

echo "==> Starting bot..."
nohup venv/bin/python -u telegram_bot.py >> nohup_bot.log 2>&1 &
BOT_PID=$!
echo "    Started with PID $BOT_PID"

sleep 3

if ps -p $BOT_PID > /dev/null; then
  echo "==> Bot is alive."
  echo "--- recent log ---"
  tail -20 nohup_bot.log
else
  echo "ERROR: Bot died within 3 seconds. Recent log:"
  echo "--- log ---"
  tail -40 nohup_bot.log
  exit 1
fi
