#!/usr/bin/env bash
# Startet das Dashboard lokal (legt beim ersten Lauf eine venv an).
#
#   ./run.sh                 nur auf diesem Rechner erreichbar
#   HOST=0.0.0.0 ./run.sh    auch fuer Handy/Tablet im gleichen WLAN
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 wurde nicht gefunden. Auf macOS: 'brew install python' oder von python.org installieren." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "→ Lege virtuelle Umgebung an ..."
  "$PYTHON" -m venv .venv
  .venv/bin/pip install --upgrade pip -q
  .venv/bin/pip install -r requirements.txt
fi

[ -f .env ] || { cp .env.example .env; echo "→ .env aus .env.example erstellt."; }

PORT="${PORT:-5000}"
echo "→ Dashboard läuft auf http://127.0.0.1:${PORT}"
if [ "${HOST:-127.0.0.1}" = "0.0.0.0" ]; then
  IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
  [ -n "${IP:-}" ] && echo "→ Im gleichen WLAN erreichbar unter http://${IP}:${PORT}"
fi

exec .venv/bin/python app.py
