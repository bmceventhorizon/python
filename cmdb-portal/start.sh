#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Missing .venv. Run ./install.sh first."
  exit 1
fi

.venv/bin/python app.py
