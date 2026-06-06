#!/bin/bash
# Single-click launcher for the Real Estate Investment Analyzer.
# Double-click this file in Finder to start the bot + dashboard.
# Closing the Terminal window (or Ctrl-C) shuts both down.
set -e
cd "$(dirname "$0")"

START_BOT="${START_BOT:-1}"

cleanup() {
  echo ""
  echo "==> Shutting down..."
  pkill -f telegram_bot.py 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM EXIT

if [ "$START_BOT" = "1" ]; then
  if [ -f .env ] && grep -q '^BRIGHT_DATA_API_TOKEN=' .env; then
    echo "==> Starting Telegram bot..."
    ./start_bot.sh || echo "WARNING: bot failed to start; continuing with dashboard only."
  else
    echo "==> Skipping bot (BRIGHT_DATA_API_TOKEN not in .env). Set START_BOT=0 to silence."
  fi
fi

echo "==> Starting dashboard (Ctrl-C to stop everything)..."
./start_dashboard.sh
