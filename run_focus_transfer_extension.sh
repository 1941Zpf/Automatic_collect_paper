#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

ENV_OVERRIDE_KEYS=(
  OPENROUTER_API_KEY
  OPENROUTER_API_BASE
  OPENROUTER_MODEL
  OPENAI_API_KEY
  KIMI_API_KEY
  OPENAI_BASE_URL
  KIMI_API_BASE
  OPENAI_MODEL
  KIMI_MODEL
  TRANSLATE_MODEL
  FOCUS_TRANSFER_API_KEY
  FOCUS_TRANSFER_API_BASE
  FOCUS_TRANSFER_MODEL
  FOCUS_TRANSFER_MEMORY_DIR
  FOCUS_TRANSFER_MEMORY_CONTEXT_CHARS
)
for key in "${ENV_OVERRIDE_KEYS[@]}"; do
  if [[ -n "${!key+x}" ]]; then
    export "__DIGEST_OVERRIDE_${key}=1"
    export "__DIGEST_VALUE_${key}=${!key}"
  fi
done

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

looks_like_redacted_secret() {
  local value="${1-}"
  [[ -z "$value" ]] && return 0
  [[ "$value" == *"*"* ]] && return 0
  [[ "$value" =~ ^[Xx]+$ ]] && return 0
  [[ "$value" =~ ^[Rr][Ee][Dd][Aa][Cc][Tt][Ee][Dd]$ ]] && return 0
  return 1
}

prefer_real_secret() {
  local current="${1-}"
  local fallback="${2-}"
  if ! looks_like_redacted_secret "$current"; then
    printf '%s' "$current"
    return 0
  fi
  if ! looks_like_redacted_secret "$fallback"; then
    printf '%s' "$fallback"
    return 0
  fi
  printf '%s' "${current:-$fallback}"
}

secret_status() {
  local value="${1-}"
  if looks_like_redacted_secret "$value"; then
    printf 'missing'
  else
    printf 'set'
  fi
}

infer_focus_transfer_model_default() {
  local chosen_base="${FOCUS_TRANSFER_API_BASE:-}"
  local normalized="${chosen_base,,}"
  if [[ -n "$normalized" && "$normalized" != *"openrouter"* && "$normalized" != *"openai"* && "$normalized" != *"moonshot"* && "$normalized" != *"kimi"* ]]; then
    printf 'auto'
    return 0
  fi
  printf '%s' "${OPENROUTER_MODEL:-${OPENAI_MODEL:-${KIMI_MODEL:-moonshot-v1-32k}}}"
}

: "${FOCUS_TRANSFER_DIGEST_JSON:=$ROOT_DIR/data/last_success_digest.json}"
: "${FOCUS_TRANSFER_OUTPUT_SUFFIX:=}"
: "${FOCUS_TRANSFER_REPORT_ROOT:=reports/focus_transfer}"
: "${FOCUS_TRANSFER_DATA_ROOT:=data/focus_transfer}"
: "${FOCUS_TRANSFER_ANALYSIS_BACKEND:=local}"
: "${OPENROUTER_API_BASE:=https://openrouter.ai/api/v1}"
: "${OPENROUTER_MODEL:=openrouter/elephant-alpha}"
: "${FOCUS_TRANSFER_API_BASE:=${OPENROUTER_API_BASE:-${OPENAI_BASE_URL:-${KIMI_API_BASE:-https://api.moonshot.cn/v1}}}}"
: "${FOCUS_TRANSFER_API_KEY:=${OPENROUTER_API_KEY:-${OPENAI_API_KEY:-${KIMI_API_KEY:-}}}}"
: "${FOCUS_TRANSFER_MODEL:=$(infer_focus_transfer_model_default)}"
: "${FOCUS_TRANSFER_ENDPOINT:=chat}"
: "${FOCUS_TRANSFER_MESSAGE_STYLE:=normal}"
: "${FOCUS_TRANSFER_STREAM:=0}"
: "${FOCUS_TRANSFER_TIMEOUT_SECONDS:=300}"
: "${FOCUS_TRANSFER_MAX_OUTPUT_TOKENS:=1800}"
: "${FOCUS_TRANSFER_FOCUS_CONTEXT_LIMIT:=48}"
: "${FOCUS_TRANSFER_CANDIDATE_LIMIT:=-1}"
: "${FOCUS_TRANSFER_MEMORY_DIR:=data/focus_memory}"
: "${FOCUS_TRANSFER_MEMORY_CONTEXT_CHARS:=6000}"

FOCUS_TRANSFER_API_KEY="$(prefer_real_secret "${FOCUS_TRANSFER_API_KEY:-}" "${OPENROUTER_API_KEY:-${OPENAI_API_KEY:-${KIMI_API_KEY:-}}}")"
if [[ -z "${FOCUS_TRANSFER_API_BASE:-}" ]]; then
  FOCUS_TRANSFER_API_BASE="${OPENROUTER_API_BASE:-${OPENAI_BASE_URL:-${KIMI_API_BASE:-https://api.moonshot.cn/v1}}}"
fi
if [[ -z "${FOCUS_TRANSFER_MODEL:-}" ]]; then
  FOCUS_TRANSFER_MODEL="$(infer_focus_transfer_model_default)"
fi

PRINT_CONFIG=0
for arg in "$@"; do
  if [[ "$arg" == "--print-config" ]]; then
    PRINT_CONFIG=1
    break
  fi
done

if [[ "$PRINT_CONFIG" == "1" || "${FOCUS_TRANSFER_PRINT_CONFIG:-0}" == "1" ]]; then
  echo "[CONFIG] focus_transfer_backend=${FOCUS_TRANSFER_ANALYSIS_BACKEND}"
  echo "[CONFIG] focus_transfer_api_base=${FOCUS_TRANSFER_API_BASE}"
  echo "[CONFIG] focus_transfer_model=${FOCUS_TRANSFER_MODEL}"
  if [[ "${FOCUS_TRANSFER_MODEL}" == "auto" ]]; then
    echo "[CONFIG] focus_transfer_model_note=will auto-detect from ${FOCUS_TRANSFER_API_BASE%/}/models"
  fi
  echo "[CONFIG] focus_transfer_api_key=$(secret_status "$FOCUS_TRANSFER_API_KEY")"
  echo "[CONFIG] focus_transfer_endpoint=${FOCUS_TRANSFER_ENDPOINT}"
  echo "[CONFIG] focus_transfer_message_style=${FOCUS_TRANSFER_MESSAGE_STYLE}"
  exit 0
fi

if [[ "$FOCUS_TRANSFER_ANALYSIS_BACKEND" != "none" ]]; then
  if [[ "$FOCUS_TRANSFER_API_BASE" == *"openrouter"* || "$FOCUS_TRANSFER_API_BASE" == *"moonshot"* || "$FOCUS_TRANSFER_API_BASE" == *"kimi"* ]]; then
    if looks_like_redacted_secret "$FOCUS_TRANSFER_API_KEY"; then
      echo "[ERROR] Focus-transfer extension did not find a usable API key from the shared config." >&2
      echo "[ERROR] Priority: FOCUS_TRANSFER_API_KEY -> OPENROUTER_API_KEY -> OPENAI_API_KEY -> KIMI_API_KEY." >&2
      echo "[ERROR] Put one real key in .env.digest or your shell environment." >&2
      exit 2
    fi
  fi
fi

python3 scripts/arxiv_research_workbench.py \
  --digest-json "$FOCUS_TRANSFER_DIGEST_JSON" \
  --output-suffix "$FOCUS_TRANSFER_OUTPUT_SUFFIX" \
  --report-root "$FOCUS_TRANSFER_REPORT_ROOT" \
  --data-root "$FOCUS_TRANSFER_DATA_ROOT" \
  --analysis-backend "$FOCUS_TRANSFER_ANALYSIS_BACKEND" \
  --analysis-api-base "$FOCUS_TRANSFER_API_BASE" \
  --analysis-api-key "$FOCUS_TRANSFER_API_KEY" \
  --analysis-model "$FOCUS_TRANSFER_MODEL" \
  --analysis-endpoint-mode "$FOCUS_TRANSFER_ENDPOINT" \
  --analysis-message-style "$FOCUS_TRANSFER_MESSAGE_STYLE" \
  --analysis-stream "$FOCUS_TRANSFER_STREAM" \
  --analysis-timeout "$FOCUS_TRANSFER_TIMEOUT_SECONDS" \
  --analysis-max-output-tokens "$FOCUS_TRANSFER_MAX_OUTPUT_TOKENS" \
  --focus-context-limit "$FOCUS_TRANSFER_FOCUS_CONTEXT_LIMIT" \
  --candidate-limit "$FOCUS_TRANSFER_CANDIDATE_LIMIT" \
  --focus-memory-dir "$FOCUS_TRANSFER_MEMORY_DIR" \
  --focus-memory-context-chars "$FOCUS_TRANSFER_MEMORY_CONTEXT_CHARS" \
  "$@"
