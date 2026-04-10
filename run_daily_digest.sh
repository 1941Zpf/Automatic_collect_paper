#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# Optional secrets/config file for non-interactive runs (e.g., cron).
if [[ -f "$ROOT_DIR/.env.digest" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.digest"
  set +a
fi

: "${DIGEST_TZ:=Asia/Shanghai}"
: "${DIGEST_DOMAIN:=cv}"
: "${ARXIV_CATEGORIES:=}"
: "${ARXIV_MODE:=recent_only}"
: "${DAY_WINDOW_DAYS:=2}"
: "${DAILY_LIMIT_PER_CAT:=260}"
: "${ARXIV_PAGE_SIZE:=200}"
: "${ARXIV_MAX_SCAN:=5000}"
: "${FOCUS_LATEST_N:=100}"
: "${FOCUS_HOT_N:=0}"
: "${FOCUS_API_ENABLE:=0}"
: "${FOCUS_RECENT_SCAN:=1200}"
: "${FOCUS_TERMS_OVERRIDE:=}"
: "${FOCUS_TERMS_EXTRA:=}"
: "${VENUE_LATEST_N:=0}"
: "${VENUE_WATCH_LIMIT:=100}"
: "${ABS_ENRICH_LIMIT:=-1}"
: "${FOCUS_ABS_ENRICH_LIMIT:=0}"
: "${REPORT_ABS_ENRICH_LIMIT:=-1}"
: "${TRANSLATE_BACKEND:=google}"
: "${TRANSLATE_MODEL:=moonshot-v1-8k}"
: "${LLM_LIMIT:=-1}"
: "${LLM_MAX_RETRIES:=2}"
: "${LLM_FAILED_COOLDOWN_HOURS:=24}"
: "${LLM_TIMEOUT_SECONDS:=25}"
: "${GOOGLE_TRANSLATE_TIMEOUT_SECONDS:=12}"
: "${GOOGLE_TRANSLATE_LIMIT:=-1}"
: "${GOOGLE_SUMMARY_SENTENCES:=3}"
: "${GOOGLE_TRANSLATE_FULL_ABSTRACT:=0}"
: "${IGNORE_FETCHED_ARTICLES:=1}"
: "${REPORT_FILE_SUFFIX:=}"

CLI_DOMAIN=""
HAS_CLI_CATEGORIES="0"
for ((i=1; i<=$#; i++)); do
  arg="${!i}"
  case "$arg" in
    --domain)
      next_index=$((i + 1))
      CLI_DOMAIN="${!next_index:-}"
      ;;
    --domain=*)
      CLI_DOMAIN="${arg#--domain=}"
      ;;
    --categories|--categories=*)
      HAS_CLI_CATEGORIES="1"
      ;;
  esac
done

EFFECTIVE_DOMAIN="${CLI_DOMAIN:-$DIGEST_DOMAIN}"

if [[ -z "${ARXIV_CATEGORIES}" && "${HAS_CLI_CATEGORIES}" != "1" ]]; then
  case "${EFFECTIVE_DOMAIN}" in
    ai|AI)
      ARXIV_CATEGORIES="cs.AI"
      ;;
    both|BOTH)
      ARXIV_CATEGORIES="cs.CV,cs.AI"
      ;;
    *)
      ARXIV_CATEGORIES="cs.CV"
      ;;
  esac
fi

python3 scripts/arxiv_daily_digest.py \
  --tz "$DIGEST_TZ" \
  --domain "$DIGEST_DOMAIN" \
  --categories "$ARXIV_CATEGORIES" \
  --arxiv-mode "$ARXIV_MODE" \
  --day-window-days "$DAY_WINDOW_DAYS" \
  --daily-limit-per-cat "$DAILY_LIMIT_PER_CAT" \
  --page-size "$ARXIV_PAGE_SIZE" \
  --max-scan "$ARXIV_MAX_SCAN" \
  --focus-latest "$FOCUS_LATEST_N" \
  --focus-hot "$FOCUS_HOT_N" \
  --focus-api-enable "$FOCUS_API_ENABLE" \
  --focus-recent-scan "$FOCUS_RECENT_SCAN" \
  --focus-terms "$FOCUS_TERMS_OVERRIDE" \
  --focus-terms-extra "$FOCUS_TERMS_EXTRA" \
  --venue-latest "$VENUE_LATEST_N" \
  --venue-watch-limit "$VENUE_WATCH_LIMIT" \
  --abs-enrich-limit "$ABS_ENRICH_LIMIT" \
  --focus-abs-enrich-limit "$FOCUS_ABS_ENRICH_LIMIT" \
  --report-abs-enrich-limit "$REPORT_ABS_ENRICH_LIMIT" \
  --translate-backend "$TRANSLATE_BACKEND" \
  --model "$TRANSLATE_MODEL" \
  --llm-limit "$LLM_LIMIT" \
  --llm-max-retries "$LLM_MAX_RETRIES" \
  --llm-failed-cooldown-hours "$LLM_FAILED_COOLDOWN_HOURS" \
  --llm-timeout "$LLM_TIMEOUT_SECONDS" \
  --google-timeout "$GOOGLE_TRANSLATE_TIMEOUT_SECONDS" \
  --google-limit "$GOOGLE_TRANSLATE_LIMIT" \
  --google-summary-sentences "$GOOGLE_SUMMARY_SENTENCES" \
  --google-full-abstract "$GOOGLE_TRANSLATE_FULL_ABSTRACT" \
  --ignore-fetched "$IGNORE_FETCHED_ARTICLES" \
  --output-suffix "$REPORT_FILE_SUFFIX" \
  "$@"
