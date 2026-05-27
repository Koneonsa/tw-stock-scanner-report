#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
.venv/bin/python -m scanner.cli update --backfill --days 300
.venv/bin/python -m scanner.cli report
if [ -d "$HOME/.stockscanner-bot/data" ]; then
  cp data/market.duckdb "$HOME/.stockscanner-bot/data/market.duckdb"
fi
.venv/bin/python -m scanner.cli notify-telegram
