#!/usr/bin/env bash
# Startet das Dashboard lokal (legt beim ersten Lauf eine venv an).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "→ Lege virtuelle Umgebung an ..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip -q
  .venv/bin/pip install -r requirements.txt
fi

[ -f .env ] || { cp .env.example .env; echo "→ .env aus .env.example erstellt."; }

echo "→ Dashboard läuft auf http://127.0.0.1:${PORT:-5000}"
exec .venv/bin/python app.py
