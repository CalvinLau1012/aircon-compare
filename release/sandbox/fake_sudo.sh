#!/usr/bin/env bash
# Sandbox 用假 sudo：只記錄呼叫並以指定碼退出，唔會執行任何 root 動作。
set -euo pipefail
LOG="${FAKE_SUDO_LOG:-/dev/null}"
echo "sudo $*" >>"$LOG"
exit "${FAKE_SUDO_EXIT:-99}"
