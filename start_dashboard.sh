#!/bin/bash
# Start the dashboard locally.
#   - loads .env into the environment
#   - installs FastAPI deps if missing
#   - launches uvicorn on $DASHBOARD_PORT (default 8000)
#   - opens the browser to http://localhost:$DASHBOARD_PORT
set -e
cd "$(dirname "$0")"

# --- env ---
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

export DASHBOARD_PORT="${DASHBOARD_PORT:-8000}"
export DASHBOARD_PASSWORD="${DASHBOARD_PASSWORD:-letmein}"
# Persist a secret across restarts so sessions survive a reboot.
if [ -z "${DASHBOARD_SECRET_KEY:-}" ]; then
  if [ -f .dashboard_secret ]; then
    export DASHBOARD_SECRET_KEY="$(cat .dashboard_secret)"
  else
    DASHBOARD_SECRET_KEY="$(openssl rand -hex 32 2>/dev/null || python3 -c 'import os,binascii;print(binascii.hexlify(os.urandom(32)).decode())')"
    echo "$DASHBOARD_SECRET_KEY" > .dashboard_secret
    chmod 600 .dashboard_secret
    export DASHBOARD_SECRET_KEY
  fi
fi

# --- deps ---
if [ ! -d venv ]; then
  echo "==> Creating venv..."
  python3 -m venv venv
fi
if ! venv/bin/python -c "import fastapi, uvicorn, itsdangerous" 2>/dev/null; then
  echo "==> Installing dependencies..."
  venv/bin/pip install -q -r requirements.txt
fi

# --- preflight ---
if [ -z "${BRIGHT_DATA_API_TOKEN:-}" ]; then
  echo "WARNING: BRIGHT_DATA_API_TOKEN not set. Enrichment will fail."
  echo "         Add BRIGHT_DATA_API_TOKEN=... to .env"
fi
echo "==> Login password: $DASHBOARD_PASSWORD"
echo "==> Dashboard: http://localhost:$DASHBOARD_PORT/login"

# --- open browser shortly after server is up ---
(sleep 2 && open "http://localhost:$DASHBOARD_PORT/login" 2>/dev/null || true) &

# --- run ---
exec venv/bin/python -m uvicorn dashboard.server:app --host 127.0.0.1 --port "$DASHBOARD_PORT"
