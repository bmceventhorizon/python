#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit .env or use the browser connection form."
fi

echo "Install complete."
echo "Run ./start.sh, then open http://127.0.0.1:8000"
