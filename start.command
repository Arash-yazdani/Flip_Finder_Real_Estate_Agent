#!/bin/bash
# Single-click launcher for the Real Estate Investment Analyzer dashboard.
# Double-click this file in Finder to start the dashboard.
# Closing the Terminal window (or Ctrl-C) shuts it down.
set -e
cd "$(dirname "$0")"

cleanup() {
  echo ""
  echo "==> Shutting down..."
  exit 0
}
trap cleanup INT TERM EXIT

echo "==> Starting dashboard (Ctrl-C to stop)..."
./start_dashboard.sh
