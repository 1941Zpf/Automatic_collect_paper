#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

ENV_OVERRIDE_KEYS=(
  OPENAI_API_KEY
  KIMI_API_KEY
  OPENAI_BASE_URL
  KIMI_API_BASE
  OPENAI_MODEL
  KIMI_MODEL
  TRANSLATE_MODEL
)
for key in "${ENV_OVERRIDE_KEYS[@]}"; do
  if [[ -n "${!key+x}" ]]; then
    export "__DIGEST_OVERRIDE_${key}=1"
    export "__DIGEST_VALUE_${key}=${!key}"
  fi
done

# Optional secrets/config file for non-interactive runs (e.g., cron).
if [[ -f "$ROOT_DIR/.env.digest" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.digest"
  set +a
fi

for key in "${ENV_OVERRIDE_KEYS[@]}"; do
  override_flag="__DIGEST_OVERRIDE_${key}"
  override_value="__DIGEST_VALUE_${key}"
  if eval "[[ -n \${$override_flag+x} ]]"; then
    value="$(eval "printf '%s' \"\${$override_value}\"")"
    export "$key=$value"
    unset "$override_flag"
    unset "$override_value"
  fi
done

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
: "${GOOGLE_TRANSLATE_FULL_ABSTRACT:=1}"
: "${IGNORE_FETCHED_ARTICLES:=1}"
: "${REPORT_FILE_SUFFIX:=}"
: "${ENABLE_FOCUS_TRANSFER_EXTENSION:=1}"

WITH_FOCUS_TRANSFER="${ENABLE_FOCUS_TRANSFER_EXTENSION}"
FOCUS_TRANSFER_SELECTION_EXPLICIT=0
DIGEST_ARGS=()

prompt_yes_no() {
  local prompt="$1"
  local default="${2:-y}"
  local suffix="[是/否，默认是]"
  local value=""
  if [[ "$default" == "n" ]]; then
    suffix="[是/否，默认否]"
  fi
  read -r -p "$prompt $suffix: " value
  value="${value:-$default}"
  case "$value" in
    y|Y|yes|YES|是|对|好) return 0 ;;
    *) return 1 ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-focus-transfer)
      WITH_FOCUS_TRANSFER="1"
      FOCUS_TRANSFER_SELECTION_EXPLICIT=1
      shift
      ;;
    --without-focus-transfer)
      WITH_FOCUS_TRANSFER="0"
      FOCUS_TRANSFER_SELECTION_EXPLICIT=1
      shift
      ;;
    --focus-transfer-backend)
      export FOCUS_TRANSFER_ANALYSIS_BACKEND="${2:-local}"
      shift 2
      ;;
    --focus-transfer-backend=*)
      export FOCUS_TRANSFER_ANALYSIS_BACKEND="${1#--focus-transfer-backend=}"
      shift
      ;;
    --focus-transfer-candidate-limit)
      export FOCUS_TRANSFER_CANDIDATE_LIMIT="${2:--1}"
      shift 2
      ;;
    --focus-transfer-candidate-limit=*)
      export FOCUS_TRANSFER_CANDIDATE_LIMIT="${1#--focus-transfer-candidate-limit=}"
      shift
      ;;
    *)
      DIGEST_ARGS+=("$1")
      shift
      ;;
  esac
done

CLI_DOMAIN=""
HAS_CLI_CATEGORIES="0"
for ((i=0; i<${#DIGEST_ARGS[@]}; i++)); do
  arg="${DIGEST_ARGS[$i]}"
  case "$arg" in
    --domain)
      next_index=$((i + 1))
      CLI_DOMAIN="${DIGEST_ARGS[$next_index]:-}"
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

if [[ "$FOCUS_TRANSFER_SELECTION_EXPLICIT" != "1" && -t 0 && -t 1 ]]; then
  if prompt_yes_no "是否在本次日报生成完成后继续进行可迁移性分析？" "y"; then
    WITH_FOCUS_TRANSFER="1"
  else
    WITH_FOCUS_TRANSFER="0"
  fi
fi

DIGEST_PY_ARGS=(
  --tz "$DIGEST_TZ"
  --domain "$DIGEST_DOMAIN"
  --categories "$ARXIV_CATEGORIES"
  --arxiv-mode "$ARXIV_MODE"
  --day-window-days "$DAY_WINDOW_DAYS"
  --daily-limit-per-cat "$DAILY_LIMIT_PER_CAT"
  --page-size "$ARXIV_PAGE_SIZE"
  --max-scan "$ARXIV_MAX_SCAN"
  --focus-latest "$FOCUS_LATEST_N"
  --focus-hot "$FOCUS_HOT_N"
  --focus-api-enable "$FOCUS_API_ENABLE"
  --focus-recent-scan "$FOCUS_RECENT_SCAN"
  --focus-terms "$FOCUS_TERMS_OVERRIDE"
  --focus-terms-extra "$FOCUS_TERMS_EXTRA"
  --venue-latest "$VENUE_LATEST_N"
  --venue-watch-limit "$VENUE_WATCH_LIMIT"
  --abs-enrich-limit "$ABS_ENRICH_LIMIT"
  --focus-abs-enrich-limit "$FOCUS_ABS_ENRICH_LIMIT"
  --report-abs-enrich-limit "$REPORT_ABS_ENRICH_LIMIT"
  --translate-backend "$TRANSLATE_BACKEND"
  --model "$TRANSLATE_MODEL"
  --llm-limit "$LLM_LIMIT"
  --llm-max-retries "$LLM_MAX_RETRIES"
  --llm-failed-cooldown-hours "$LLM_FAILED_COOLDOWN_HOURS"
  --llm-timeout "$LLM_TIMEOUT_SECONDS"
  --google-timeout "$GOOGLE_TRANSLATE_TIMEOUT_SECONDS"
  --google-limit "$GOOGLE_TRANSLATE_LIMIT"
  --google-summary-sentences "$GOOGLE_SUMMARY_SENTENCES"
  --google-full-abstract "$GOOGLE_TRANSLATE_FULL_ABSTRACT"
  --ignore-fetched "$IGNORE_FETCHED_ARTICLES"
  --output-suffix "$REPORT_FILE_SUFFIX"
)
if [[ ${#DIGEST_ARGS[@]} -gt 0 ]]; then
  DIGEST_PY_ARGS+=("${DIGEST_ARGS[@]}")
fi

python3 scripts/arxiv_daily_digest.py "${DIGEST_PY_ARGS[@]}"

if [[ "$WITH_FOCUS_TRANSFER" == "1" ]]; then
  echo "[INFO] Focus-transfer extension enabled."
  if ! "$ROOT_DIR/run_focus_transfer_extension.sh" --digest-json "$ROOT_DIR/data/last_success_digest.json"; then
    echo "[WARN] Focus-transfer extension failed, but the main digest report was already generated successfully." >&2
  fi
fi
