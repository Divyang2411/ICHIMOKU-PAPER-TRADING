#!/bin/bash
# Double-click on macOS to open the Ichimoku dashboard at http://127.0.0.1:8000
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: creating .venv and installing requirements..."
  python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt
fi
git pull --ff-only -q 2>/dev/null
./.venv/bin/python main.py serve
