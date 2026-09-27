#!/bin/bash
# Double-click on macOS to open the Ichimoku dashboard at http://127.0.0.1:8000
# It updates itself on start and every hour: GitHub paper trades, latest prices, scan, backtest, reports.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: creating .venv and installing requirements..."
  python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt
fi
./.venv/bin/python main.py serve
