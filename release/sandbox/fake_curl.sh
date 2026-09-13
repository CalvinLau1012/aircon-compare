#!/usr/bin/env bash
# Sandbox 用假 curl：FAKE_FAIL_CURL=1 時模擬健康檢查失敗；否則要求 FAKE_CURL_DIR/index.html 存在。
set -euo pipefail

LOG="${FAKE_CURL_LOG:-/dev/null}"
echo "curl $*" >>"$LOG"

if [ "${FAKE_FAIL_CURL:-0}" = "1" ]; then exit 22; fi
if [ -n "${FAKE_CURL_DIR:-}" ] && [ -f "${FAKE_CURL_DIR}/index.html" ]; then exit 0; fi
exit 22
