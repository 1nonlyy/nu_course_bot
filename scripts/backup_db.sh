#!/usr/bin/env bash
# Snapshot the SQLite DB into data/backups/ and prune backups older than 7 days.
#
# Why sqlite3 .backup instead of plain cp:
#   bot/db/database.py opens connections in WAL mode (PRAGMA journal_mode=WAL).
#   A naive `cp` of just the .db file while the bot is running can capture an
#   incoherent state (committed pages may still live in the .db-wal file). The
#   `.backup` command performs an online-consistent copy that is safe to run
#   while writers are active. We fall back to cp only if `sqlite3` is missing.
#
# Configurable via env:
#   DB_PATH      path to the live SQLite file (default: data/nu_bot.db)
#   BACKUP_DIR   destination directory (default: data/backups)
#   RETENTION_DAYS   prune backups older than this (default: 7)
#
# Schedule via cron — see README "Резервное копирование SQLite" section.

set -euo pipefail

DB_PATH="${DB_PATH:-data/nu_bot.db}"
BACKUP_DIR="${BACKUP_DIR:-data/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

if [[ ! -f "$DB_PATH" ]]; then
    echo "backup_db: source DB not found at $DB_PATH" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

ts="$(date +%Y%m%d_%H%M%S)"
target="${BACKUP_DIR}/nu_bot_${ts}.db"

if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_PATH" ".backup '$target'"
else
    echo "backup_db: sqlite3 CLI not found, falling back to cp (NOT WAL-safe)" >&2
    cp "$DB_PATH" "$target"
fi

# -mtime +N matches files modified strictly more than N*24h ago.
find "$BACKUP_DIR" -name 'nu_bot_*.db' -type f -mtime +"$RETENTION_DAYS" -delete

echo "backup_db: wrote $target"
