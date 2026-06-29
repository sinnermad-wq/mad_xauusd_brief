#!/usr/bin/env bash
# setup_cron_linux.sh — 在 Linux/macOS 上註冊 / 取消每日 XAUUSD 簡報 cron job。
#
# Usage:
#   bash scripts/setup_cron_linux.sh              # 安裝 cron（08:30 HKT）
#   bash scripts/setup_cron_linux.sh --uninstall  # 由 cron 移除
#   bash scripts/setup_cron_linux.sh --status     # 查看狀態
#   bash scripts/setup_cron_linux.sh --dry-run    # 顯示將會寫入的 crontab line，唔 actually install
#
# Cron schedule default: 30 8 * * * (08:30 server timezone; assume server is in HKT)
# 若你需要不同 timezone，請編輯 CRON_SCHEDULE 環境變數 or 直接 edit SCRIPT。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CRON_TAG="# daily-xauusd-brief-managed"
CRON_SCHEDULE="${CRON_SCHEDULE:-30 8 * * *}"
LOG_FILE="${LOG_FILE:-${PROJECT_ROOT}/logs/cron.log}"
RUNNER="${PROJECT_ROOT}/scripts/run_daily.sh"

usage() {
    sed -n '2,12p' "$0"
}

ACTION="${ACTION:-install}"
if [[ $# -gt 0 ]]; then
    ACTION="$1"
fi

dump_line() {
    echo "${CRON_SCHEDULE} cd ${PROJECT_ROOT} && bash ${RUNNER} >> ${LOG_FILE} 2>&1 ${CRON_TAG}"
}

status() {
    echo "## Managed cron entries (filter tag ${CRON_TAG}):"
    crontab -l 2>/dev/null | grep -F "${CRON_TAG}" || echo "  (none)"
}

install() {
    if [[ ! -x "${RUNNER}" ]]; then
        chmod +x "${RUNNER}"
        echo "[INFO] made ${RUNNER} executable"
    fi
    mkdir -p "$(dirname "${LOG_FILE}")"

    local new_line
    new_line="$(dump_line)"

    local tmp
    tmp="$(mktemp)"
    if crontab -l 2>/dev/null >"${tmp}"; then
        # remove old managed lines
        grep -v -F "${CRON_TAG}" "${tmp}" >"${tmp}.new" || true
        mv "${tmp}.new" "${tmp}"
    else
        : >"${tmp}"
    fi
    echo "${new_line}" >> "${tmp}"
    crontab "${tmp}"
    rm -f "${tmp}"

    echo "[OK] installed cron job:"
    dump_line
    echo
    echo "View:    bash scripts/setup_cron_linux.sh --status"
    echo "Remove:  bash scripts/setup_cron_linux.sh --uninstall"
}

uninstall() {
    local tmp
    tmp="$(mktemp)"
    if crontab -l 2>/dev/null >"${tmp}"; then
        grep -v -F "${CRON_TAG}" "${tmp}" >"${tmp}.new" || true
        mv "${tmp}.new" "${tmp}"
        crontab "${tmp}"
    fi
    rm -f "${tmp}"
    echo "[OK] removed managed cron entries (if any)"
}

dry_run() {
    echo "Dry run — would install the line below into crontab:"
    dump_line
}

case "${ACTION}" in
    install|--install|"")
        install
        ;;
    uninstall|--uninstall)
        uninstall
        ;;
    status|--status)
        status
        ;;
    --dry-run)
        dry_run
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        echo "Unknown action: ${ACTION}" >&2
        usage
        exit 2
        ;;
esac
