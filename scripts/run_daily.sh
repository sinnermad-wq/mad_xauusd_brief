#!/usr/bin/env bash
# run_daily.sh — 手動觸發每日黃金簡報，或由排程器（cron / systemd timer / Task Scheduler）喚醒。
#
# 使用：
#   bash scripts/run_daily.sh                  # 預設：full pipeline（fetch + MD + Telegram）
#   bash scripts/run_daily.sh --dry-run        # 不發 Telegram + 預覽
#   bash scripts/run_daily.sh --send-only      # 不重抓資料，從 cache 重發
#   bash scripts/run_daily.sh --no-send        # 只生成 MD，不送 Telegram
#
# 環境變數：
#   PYTHON      — Python interpreter（預設：uv run python）
#   TZ          — 時區名（預設：Asia/Hong_Kong）
#   REPORT_HOUR — 排程紀錄用，**不會擋**你手動跑
#   REPORT_MINUTE — 同上

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

# --- .env loading --- (python-dotenv 入 main.py 已 load，shell 內只 log 偵錯用)
if [[ ! -f ".env" ]]; then
    echo "[WARN] ${PROJECT_ROOT}/.env not found; main.py 會 raise KeyError" >&2
fi

# --- Python 解析器 ---
if command -v uv >/dev/null 2>&1; then
    PYTHON_BIN="${PYTHON:-uv run python}"
elif [[ -x ".venv/bin/python" ]]; then
    PYTHON_BIN="${PYTHON:-.venv/bin/python}"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="${PYTHON:-python3}"
else
    PYTHON_BIN="${PYTHON:-python}"
fi

# --- 跑 ---
echo "[$(date -Iseconds)] run_daily starting (args: $*)"
echo "  Python:  ${PYTHON_BIN}"
echo "  Project: ${PROJECT_ROOT}"

# shellcheck disable=SC2086
${PYTHON_BIN} -m daily_xauusd_brief.main "$@"
EXIT_CODE=$?

echo "[$(date -Iseconds)] run_daily finished with exit code ${EXIT_CODE}"
exit ${EXIT_CODE}
