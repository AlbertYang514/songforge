#!/bin/bash
# Double-click this file on macOS to start SongForge and open the page.
set -u
cd "$(dirname "$0")" || exit 1

URL="http://127.0.0.1:4173"
if curl -s -o /dev/null "$URL/"; then
  open "$URL"
  exit 0
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js 20+ is required. Install it, then double-click again."
  read -r -p "Press Return to close..."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Python dependencies are not installed yet."
  echo "Run: python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  read -r -p "Press Return to close..."
  exit 1
fi

exec npm start
