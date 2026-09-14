#!/usr/bin/env bash
# Sandbox 用假 docker：模擬 ps/inspect/image/tag/stop/run/compose/build，並記錄所有呼叫。
# - 用 FAKE_DOCKER_STATE 追蹤 tag → image ID（令 release 部署後 latest 真正改變，回滾要 retag）
# - 由 FAKE_* 環境變數控制行為；只供測試，切勿用於正式站。
set -euo pipefail

LOG="${FAKE_DOCKER_LOG:-/dev/null}"
STATE="${FAKE_DOCKER_STATE:-/tmp/aircon-fake-docker-state}"
echo "docker $*" >>"$LOG"

DEFAULT_OLD="sha256:$(printf 'a%.0s' {1..64})"
DEFAULT_RELEASE="sha256:$(printf 'c%.0s' {1..64})"
DEFAULT_MISMATCH="sha256:$(printf 'b%.0s' {1..64})"
[ -f "$STATE" ] || : >"$STATE"

id_of() { # $1=ref（先查 state，再按 ref 類型回預設）
  local ref="$1" id=""
  case "$ref" in
    *:pre-*)
      if [ "${FAKE_TAG_ID_MISMATCH:-0}" = "1" ]; then
        echo "${FAKE_IMAGE_ID_MISMATCH:-$DEFAULT_MISMATCH}"
        return 0
      fi
      ;;
  esac
  id="$(sed -n "s|^${ref}=||p" "$STATE" 2>/dev/null | tail -n1)"
  if [ -n "$id" ]; then echo "$id"; return 0; fi
  case "$ref" in
    *:latest)   echo "${FAKE_IMAGE_ID_OLD:-$DEFAULT_OLD}" ;;
    *:pre-*)    echo "${FAKE_IMAGE_ID_PRE:-${FAKE_IMAGE_ID_OLD:-$DEFAULT_OLD}}" ;;
    *:release-*) echo "${FAKE_IMAGE_ID_RELEASE:-$DEFAULT_RELEASE}" ;;
    *)          echo "${FAKE_IMAGE_ID:-$DEFAULT_OLD}" ;;
  esac
}

cmd="${1:-}"
case "$cmd" in
  ps)
    if [ "${FAKE_PS_NONE:-0}" = "1" ]; then exit 0; fi
    if [ "${FAKE_PS_AMBIGUOUS:-0}" = "1" ]; then echo "aaaa1111"; echo "bbbb2222"; exit 0; fi
    echo "fakecid0001"
    ;;
  inspect)
    fmt="${3:-}"
    case "$fmt" in
      *".Name"*) echo "${FAKE_VOL_NAME:-fakevol}" ;;
      *".Source"*) echo "${FAKE_INSPECT_SRC:-/var/lib/docker/volumes/fake/_data}" ;;
      *) echo "${FAKE_INSPECT_SRC:-/var/lib/docker/volumes/fake/_data}" ;;
    esac
    ;;
  image)
    sub="${2:-}"
    if [ "$sub" = "inspect" ]; then
      if [ "${FAKE_FAIL_IMAGE:-0}" = "1" ]; then exit 1; fi
      fmt="${3:-}"
      ref="${5:-}"
      if [ "$fmt" = "-f" ]; then
        case "$ref" in
          *:latest)
            if [ "${FAKE_FAIL_IMAGE_INSPECT_LATEST:-0}" = "1" ]; then exit 1; fi
            ;;
          *:pre-*)
            if [ "${FAKE_FAIL_IMAGE_INSPECT_PRE:-0}" = "1" ]; then exit 1; fi
            ;;
        esac
        id_of "$ref"
      fi
      exit 0
    fi
    ;;
  build)
    if [ "${FAKE_FAIL_BUILD:-0}" = "1" ]; then exit 1; fi
    exit 0
    ;;
  tag)
    if [ "${FAKE_FAIL_TAG:-0}" = "1" ]; then exit 1; fi
    src="${2:-}"; dst="${3:-}"
    if [ -n "$src" ] && [ -n "$dst" ]; then
      id="$(id_of "$src")"
      grep -v "^${dst}=" "$STATE" >"$STATE.tmp" 2>/dev/null || true
      mv -f "$STATE.tmp" "$STATE" 2>/dev/null || true
      echo "${dst}=${id}" >>"$STATE"
    fi
    exit 0
    ;;
  stop)
    exit 0
    ;;
  compose)
    if [ "${FAKE_FAIL_COMPOSE:-0}" = "1" ]; then exit 1; fi
    exit 0
    ;;
  run)
    src=""
    data=""
    prev=""
    for a in "$@"; do
      if [ "$prev" = "-v" ]; then
        case "$a" in
          *:/app) src="${a%:/app}" ;;
          *:/data:ro|*:/data) data="${a%%:/data*}" ;;
        esac
      fi
      prev="$a"
    done
    if [ -z "$src" ]; then
      echo "fake-docker: run 冇 -v <dir>:/app" >&2
      exit 1
    fi
    fixture="${FAKE_APP_FIXTURE:-}"
    if [ -z "$fixture" ] || [ ! -d "$fixture" ]; then
      echo "fake-docker: 冇有效 FAKE_APP_FIXTURE" >&2
      exit 1
    fi
    if [ "${FAKE_FAIL_RUN:-0}" = "1" ]; then exit 1; fi
    # 先模擬 sync_data_from_mount（read-only /data → runtime 資料），再套用 fixture（= build 輸出）
    if [ -n "$data" ] && [ -d "$data" ]; then
      find "$data" -maxdepth 1 -type f \
        \( -name '*.json' -o -name '*.csv' -o -name '*-bak*' \) \
        ! -name 'deploy_payload.json' -exec cp -a {} "$src/" \;
    fi
    cp -a "$fixture/." "$src/"
    exit 0
    ;;
esac
exit 0
