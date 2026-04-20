#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

RUNNER="./run_daily_digest.sh"
DRY_RUN=0
MODE=""
PRESET=""
CMD=()
ENV_UPDATE_KEYS=()
ENV_UPDATE_VALUES=()

if [[ -f "$ROOT_DIR/.env.digest" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.digest"
  set +a
fi

print_help() {
  cat <<'EOF'
arXiv 论文日报向导

用法：
  ./digest_wizard.sh                 # 打开交互式菜单
  ./digest_wizard.sh --default        # 使用 .env.digest 默认配置运行
  ./digest_wizard.sh --preset 预设名  # 使用某个预设运行
  ./digest_wizard.sh --dry-run ...    # 只预览命令，不真正执行

可用预设：
  cv        CV 默认抓取
  ai        AI 论文 + AI 相关重点方向关键词
  both      CV + AI 同时抓取
  tracking  CV 跟踪 / 测试时适应 / 域适应方向
  quick     小规模快速测试

说明：
  交互式运行时，执行前会额外询问是否启用 Focus Transfer 扩展。
  该扩展默认启用；启用后会在日报生成完成后继续调用当前默认配置的迁移分析 API 做 focus / non-focus 迁移分析。
EOF
}

quote_display_arg() {
  local value="${1-}"
  if [[ -z "$value" ]]; then
    printf "''"
    return 0
  fi
  if [[ "$value" =~ ^[A-Za-z0-9_./:=,+-]+$ ]]; then
    printf '%s' "$value"
    return 0
  fi
  value="${value//\'/\'\\\'\'}"
  printf "'%s'" "$value"
}

quote_command() {
  local first="1"
  local part
  for part in "$@"; do
    if [[ "$first" != "1" ]]; then
      printf ' '
    fi
    quote_display_arg "$part"
    first="0"
  done
  printf '\n'
}

show_command() {
  local -a cmd=("$@")
  echo
  echo "[将执行的命令]"
  quote_command "${cmd[@]}"
  echo
}

run_command() {
  local -a cmd=("$@")
  show_command "${cmd[@]}"
  execute_command "${cmd[@]}"
}

execute_command() {
  local -a cmd=("$@")
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[仅预览] 当前不会真正执行抓取。"
    return 0
  fi
  "${cmd[@]}"
}

read_default() {
  local prompt="$1"
  local default="$2"
  local value=""
  read -r -p "$prompt [$default]: " value
  if [[ -z "$value" ]]; then
    value="$default"
  fi
  printf '%s' "$value"
}

read_optional() {
  local prompt="$1"
  local value=""
  read -r -p "$prompt: " value
  printf '%s' "$value"
}

yes_no() {
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

append_cmd_arg() {
  local key="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    CMD+=("$key" "$value")
  fi
}

append_cmd_arg_force() {
  local key="$1"
  local value="${2-}"
  CMD+=("$key" "$value")
}

find_env_update_index() {
  local key="$1"
  local i
  for ((i = 0; i < ${#ENV_UPDATE_KEYS[@]}; i++)); do
    if [[ "${ENV_UPDATE_KEYS[$i]}" == "$key" ]]; then
      printf '%s' "$i"
      return 0
    fi
  done
  printf '%s' "-1"
}

set_env_update() {
  local key="$1"
  local value="$2"
  local idx
  idx="$(find_env_update_index "$key")"
  if [[ "$idx" -ge 0 ]]; then
    ENV_UPDATE_VALUES[$idx]="$value"
  else
    ENV_UPDATE_KEYS+=("$key")
    ENV_UPDATE_VALUES+=("$value")
  fi
}

write_env_assignment() {
  local key="$1"
  local value="$2"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s="%s"\n' "$key" "$value"
}

save_env_updates() {
  local target="$ROOT_DIR/.env.digest"
  local tmp
  local line
  local name
  local idx
  local seen="|"

  tmp="$(mktemp "$ROOT_DIR/.env.digest.tmp.XXXXXX")"
  if [[ -f "$target" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)= ]]; then
        name="${BASH_REMATCH[1]}"
        idx="$(find_env_update_index "$name")"
        if [[ "$idx" -ge 0 ]]; then
          write_env_assignment "$name" "${ENV_UPDATE_VALUES[$idx]}" >> "$tmp"
          seen="${seen}${name}|"
        else
          printf '%s\n' "$line" >> "$tmp"
        fi
      else
        printf '%s\n' "$line" >> "$tmp"
      fi
    done < "$target"
  fi

  for ((idx = 0; idx < ${#ENV_UPDATE_KEYS[@]}; idx++)); do
    name="${ENV_UPDATE_KEYS[$idx]}"
    if [[ "$seen" == *"|${name}|"* ]]; then
      continue
    fi
    write_env_assignment "$name" "${ENV_UPDATE_VALUES[$idx]}" >> "$tmp"
  done

  mv "$tmp" "$target"
  chmod 600 "$target"
  echo "[已保存] 自定义配置已写入 $target"
}

prompt_report_suffix() {
  local suffix=""
  echo
  suffix="$(read_optional "报告文件后缀（留空 = 无后缀，例如 tracking_debug）")"
  append_cmd_arg_force "--output-suffix" "$suffix"
}

prompt_ignore_fetched() {
  echo
  if yes_no "是否忽略当前配置下已经抓取过的论文，只输出新的论文？" "y"; then
    append_cmd_arg "--ignore-fetched" "1"
  else
    append_cmd_arg "--ignore-fetched" "0"
  fi
}

prompt_focus_transfer_extension() {
  echo
  if yes_no "是否在日报生成后继续运行 Focus Transfer 应用扩展？" "y"; then
    CMD+=("--with-focus-transfer")
  else
    CMD+=("--without-focus-transfer")
  fi
}

build_preset_command() {
  local name="$1"
  CMD=("$RUNNER")
  case "$name" in
    cv)
      CMD+=("--domain" "cv" "--categories" "" "--focus-terms" "" "--focus-terms-extra" "")
      ;;
    ai)
      CMD+=(
        "--domain" "ai"
        "--categories" ""
        "--focus-terms" "agent,reasoning,alignment,tool use,large language model,multimodal reasoning,planning,reinforcement learning"
        "--focus-terms-extra" ""
      )
      ;;
    both)
      CMD+=("--domain" "both" "--categories" "" "--focus-terms" "" "--focus-terms-extra" "" "--daily-limit-per-cat" "260" "--focus-latest" "100")
      ;;
    tracking)
      CMD+=(
        "--domain" "cv"
        "--categories" ""
        "--focus-terms" "test-time adaptation,zero-shot,multimodal object tracking,rgb-x tracking,rgb-d tracking,rgb-e tracking,rgb-t tracking,distribution shift,domain shift"
        "--focus-terms-extra" ""
        "--focus-latest" "100"
        "--focus-recent-scan" "1600"
      )
      ;;
    quick)
      CMD+=(
        "--domain" "cv"
        "--categories" ""
        "--focus-terms" ""
        "--focus-terms-extra" ""
        "--daily-limit-per-cat" "50"
        "--focus-latest" "20"
        "--focus-recent-scan" "300"
        "--venue-watch-limit" "20"
        "--abs-enrich-limit" "50"
        "--report-abs-enrich-limit" "50"
        "--google-limit" "50"
      )
      ;;
    *)
      echo "[错误] 未知预设：$name" >&2
      return 1
      ;;
  esac
}

run_default() {
  CMD=("$RUNNER")
  prompt_ignore_fetched
  prompt_focus_transfer_extension
  prompt_report_suffix
  run_command "${CMD[@]}"
}

choose_preset() {
  local choice=""
  echo
  echo "预设选项："
  echo "  1) cv       - CV 默认抓取"
  echo "  2) ai       - AI 论文 + AI 相关重点方向关键词"
  echo "  3) both     - CV + AI 同时抓取"
  echo "  4) tracking - CV 跟踪 / 测试时适应 / 域适应方向"
  echo "  5) quick    - 小规模快速测试"
  read -r -p "请选择预设 [1-5]: " choice
  case "$choice" in
    1) PRESET="cv" ;;
    2) PRESET="ai" ;;
    3) PRESET="both" ;;
    4) PRESET="tracking" ;;
    5) PRESET="quick" ;;
    *) echo "[错误] 预设选择无效。" >&2; return 1 ;;
  esac
  build_preset_command "$PRESET"
  prompt_focus_transfer_extension
  prompt_report_suffix
  if yes_no "现在运行这个预设吗？" "y"; then
    run_command "${CMD[@]}"
  else
    show_command "${CMD[@]}"
    echo "[跳过] 未执行。"
  fi
}

custom_run() {
  local date domain daily_limit focus_latest focus_scan venue_watch translate_backend abs_limit report_abs_limit
  local focus_mode focus_terms categories
  CMD=("$RUNNER")
  ENV_UPDATE_KEYS=()
  ENV_UPDATE_VALUES=()

  echo
  echo "自定义本次运行。每一项直接回车表示使用括号中的默认值。"

  date="$(read_optional "日期，格式 YYYY-MM-DD（留空 = 今天）")"
  append_cmd_arg "--date" "$date"

  domain="$(read_default "抓取领域：cv / ai / both" "${DIGEST_DOMAIN:-cv}")"
  append_cmd_arg "--domain" "$domain"
  set_env_update "DIGEST_DOMAIN" "$domain"

  categories="$(read_optional "手动指定 arXiv 分类，例如 cs.CV,cs.AI（留空 = 根据领域自动推导）")"
  append_cmd_arg_force "--categories" "$categories"
  set_env_update "ARXIV_CATEGORIES" "$categories"

  daily_limit="$(read_default "每个分类抓取的日报论文数量" "${DAILY_LIMIT_PER_CAT:-260}")"
  append_cmd_arg "--daily-limit-per-cat" "$daily_limit"
  set_env_update "DAILY_LIMIT_PER_CAT" "$daily_limit"

  focus_latest="$(read_default "重点方向（Focus）最新论文数量" "${FOCUS_LATEST_N:-100}")"
  append_cmd_arg "--focus-latest" "$focus_latest"
  set_env_update "FOCUS_LATEST_N" "$focus_latest"

  focus_scan="$(read_default "重点方向（Focus）扩展扫描范围" "${FOCUS_RECENT_SCAN:-1200}")"
  append_cmd_arg "--focus-recent-scan" "$focus_scan"
  set_env_update "FOCUS_RECENT_SCAN" "$focus_scan"

  venue_watch="$(read_default "中稿/会刊线索数量" "${VENUE_WATCH_LIMIT:-100}")"
  append_cmd_arg "--venue-watch-limit" "$venue_watch"
  set_env_update "VENUE_WATCH_LIMIT" "$venue_watch"

  abs_limit="$(read_default "日报 abs 摘要补抓数量（-1 = 全部，0 = 关闭，N = 前 N 篇）" "${ABS_ENRICH_LIMIT:--1}")"
  append_cmd_arg "--abs-enrich-limit" "$abs_limit"
  set_env_update "ABS_ENRICH_LIMIT" "$abs_limit"

  report_abs_limit="$(read_default "报告生成前的 abs 摘要补救数量（-1 = 全部，0 = 关闭，N = 前 N 篇）" "${REPORT_ABS_ENRICH_LIMIT:--1}")"
  append_cmd_arg "--report-abs-enrich-limit" "$report_abs_limit"
  set_env_update "REPORT_ABS_ENRICH_LIMIT" "$report_abs_limit"

  translate_backend="$(read_default "翻译后端：google / llm / auto" "${TRANSLATE_BACKEND:-google}")"
  append_cmd_arg "--translate-backend" "$translate_backend"
  set_env_update "TRANSLATE_BACKEND" "$translate_backend"

  echo
  echo "重点方向（Focus）关键词模式："
  echo "  1) 使用默认重点方向关键词"
  echo "  2) 在默认关键词后追加"
  echo "  3) 完全替换重点方向关键词"
  read -r -p "请选择 [1-3]: " focus_mode
  case "$focus_mode" in
    2)
      focus_terms="$(read_optional "要追加的重点方向关键词，用英文逗号分隔")"
      append_cmd_arg_force "--focus-terms" ""
      append_cmd_arg_force "--focus-terms-extra" "$focus_terms"
      set_env_update "FOCUS_TERMS_EXTRA" "$focus_terms"
      set_env_update "FOCUS_TERMS_OVERRIDE" ""
      ;;
    3)
      focus_terms="$(read_optional "用于完全替换的重点方向关键词，用英文逗号分隔")"
      append_cmd_arg_force "--focus-terms" "$focus_terms"
      append_cmd_arg_force "--focus-terms-extra" ""
      set_env_update "FOCUS_TERMS_OVERRIDE" "$focus_terms"
      set_env_update "FOCUS_TERMS_EXTRA" ""
      ;;
    *)
      append_cmd_arg_force "--focus-terms" ""
      append_cmd_arg_force "--focus-terms-extra" ""
      set_env_update "FOCUS_TERMS_OVERRIDE" ""
      set_env_update "FOCUS_TERMS_EXTRA" ""
      ;;
  esac

  if yes_no "是否将这组自定义参数保存到 .env.digest 作为新的默认配置？" "n"; then
    save_env_updates
  fi

  prompt_focus_transfer_extension
  prompt_report_suffix

  show_command "${CMD[@]}"
  if yes_no "现在运行这条自定义命令吗？" "y"; then
    execute_command "${CMD[@]}"
  else
    echo "[跳过] 未执行。"
  fi
}

preview_default() {
  CMD=("$RUNNER")
  show_command "${CMD[@]}"
}

show_latest_report() {
  local report=""
  if [[ ! -d "$ROOT_DIR/reports" ]]; then
    echo
    echo "[提示] 还没有找到报告目录：$ROOT_DIR/reports"
    return
  fi
  while IFS= read -r candidate; do
    report="$candidate"
  done < <(find "$ROOT_DIR/reports" -maxdepth 1 -type f -name 'arxiv_digest_????-??-??*.html' | sort)
  echo
  if [[ -n "$report" && -f "$report" ]]; then
    echo "最近日期报告："
    echo "  $report"
    if yes_no "用系统浏览器打开它吗？" "n"; then
      open "$report"
    fi
  else
    echo "[提示] 还没有找到带日期的日报 HTML。"
  fi
}

interactive_menu() {
  while true; do
    echo
    echo "arXiv 论文日报向导"
    echo "1) 使用 .env.digest 默认配置并运行"
    echo "2) 选择一个预设"
    echo "3) 自定义本次运行"
    echo "4) 预览默认命令"
    echo "5) 查看/打开最近日期报告"
    echo "0) 退出"
    read -r -p "请选择 [0-5]: " choice
    case "$choice" in
      1) run_default; break ;;
      2) choose_preset; break ;;
      3) custom_run; break ;;
      4) preview_default ;;
      5) show_latest_report ;;
      0) echo "已退出。"; break ;;
      *) echo "[提示] 无效选择，请重新输入。" ;;
    esac
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --default)
      MODE="default"
      shift
      ;;
    --preset)
      if [[ $# -lt 2 ]]; then
        echo "[错误] --preset 需要指定预设名称。" >&2
        exit 1
      fi
      MODE="preset"
      PRESET="$2"
      shift 2
      ;;
    --preset=*)
      MODE="preset"
      PRESET="${1#--preset=}"
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "[错误] 未知选项：$1" >&2
      print_help
      exit 1
      ;;
  esac
done

case "$MODE" in
  default)
    run_default
    ;;
  preset)
    if [[ -z "$PRESET" ]]; then
      echo "[错误] --preset 需要指定预设名称。" >&2
      exit 1
    fi
    build_preset_command "$PRESET"
    run_command "${CMD[@]}"
    ;;
  "")
    interactive_menu
    ;;
  *)
    echo "[错误] 不支持的运行模式：$MODE" >&2
    exit 1
    ;;
esac
