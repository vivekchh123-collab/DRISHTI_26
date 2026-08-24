#!/usr/bin/env bash
# DRISHTI - start the application (macOS / Linux).
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "Python 3.9+ is required."; exit 1; }

if ! python3 -c "import fastapi, uvicorn, numpy" 2>/dev/null; then
  echo "Installing dependencies..."
  python3 -m pip install -r requirements.txt
fi

if [ ! -f frontend/vendor/leaflet.js ]; then
  echo "Vendoring front-end assets (one time, needs network)..."
  python3 scripts/vendor_assets.py || true
fi

echo
echo "  DRISHTI running at http://127.0.0.1:8000"
echo "  API documentation   http://127.0.0.1:8000/docs"
echo "  Press Ctrl+C to stop."
echo
cd backend
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
