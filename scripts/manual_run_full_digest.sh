#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

cat <<'MSG'
[INFO] This script prepares the full command.
[INFO] It does NOT auto-run unless you pass --run.

Recommended steps:
1) Put your keys in .env.digest (project root), for example:
   KIMI_API_KEY=...
   KIMI_API_BASE=https://api.moonshot.cn/v1
   KIMI_MODEL=moonshot-v1-8k

2) Run:
   ./scripts/manual_run_full_digest.sh --run
MSG

CMD=(
  "./run_daily_digest.sh"
  "--categories" "cs.CV"
  "--day-window-days" "2"
  "--daily-limit-per-cat" "260"
  "--focus-latest" "100"
  "--focus-hot" "0"
  "--focus-recent-scan" "1200"
  "--venue-latest" "0"
  "--venue-watch-limit" "100"
  "--llm-limit" "-1"
  "--llm-max-retries" "4"
)

echo "[READY COMMAND] ${CMD[*]}"

if [[ "${1:-}" == "--run" ]]; then
  "${CMD[@]}"
else
  echo "[SKIP] Dry-run only. Add --run to execute."
fi
