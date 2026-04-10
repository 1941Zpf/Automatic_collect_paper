#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CRON_MARK="# ARXIV_DAILY_DIGEST"
CRON_JOB="0 8 * * * cd \"$ROOT_DIR\" && /bin/bash ./run_daily_digest.sh >> ./reports/digest.log 2>&1 $CRON_MARK"

TMP_FILE="$(mktemp)"
if crontab -l >/dev/null 2>&1; then
  crontab -l | grep -v "$CRON_MARK" > "$TMP_FILE"
else
  : > "$TMP_FILE"
fi

echo "$CRON_JOB" >> "$TMP_FILE"
crontab "$TMP_FILE"
rm -f "$TMP_FILE"

echo "[OK] Installed cron job at 08:00 daily."
crontab -l | grep "$CRON_MARK"
