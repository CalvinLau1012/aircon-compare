#!/bin/bash
set -e

echo "[entrypoint] 啟動 cron..."
cron

echo "[entrypoint] 啟動 nginx..."
exec nginx -g 'daemon off;'
