#!/usr/bin/env bash
# Start the viewer. Defaults to serving on the LAN.
set -euo pipefail
cd "$(dirname "$0")"

HOST="${PDFV_HOST:-0.0.0.0}"
PORT="${PDFV_PORT:-8800}"
PY=".venv/bin/python"

[ -x "$PY" ] || { echo "No virtualenv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

if [ ! -f data/library.db ]; then
  echo "No index yet — building it first (this takes a couple of minutes)..."
  "$PY" scripts/index_library.py
fi

echo "Serving on http://${HOST}:${PORT}  (Ctrl-C to stop)"
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
