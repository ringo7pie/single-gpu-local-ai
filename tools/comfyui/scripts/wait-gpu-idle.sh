#!/usr/bin/env bash
# GPU が空くまで待つ。nvidia-smi の使用率が 0% で、計算プロセスが 1 つも無い状態が
# 連続 REQUIRED 回（既定 3 回、INTERVAL 秒おき。既定 30 秒）続いたら GPU_IDLE を出して 0 で終わる。
#
#   wait-gpu-idle.sh [--interval 秒] [--required 回] [--allow 文字列]
#
# --allow に渡した文字列を、nvidia-smi の行（pid・プロセス名）か /proc/<pid>/cmdline に含む計算プロセスは
# 「空き」の判定で無視する（例: --allow ai_finance。nvidia-smi のプロセス名は `python` としか出ないことがあるので、
# コマンドラインでも照合する）。何度でも指定できる。
# 別プロジェクト（~/Proj/USDFormat の Isaac Lab など）の学習を落とさないため、GPU を使う試験の前に流す
# （knowledge/10-silver/areas/env.md）。nvidia-smi が無い・失敗するときは 2 で終わる。
set -euo pipefail

INTERVAL=30
REQUIRED=3
ALLOW=()
while [ $# -gt 0 ]
do
  case "$1" in
    --interval) INTERVAL="$2"; shift 2 ;;
    --required) REQUIRED="$2"; shift 2 ;;
    --allow) ALLOW+=("$2"); shift 2 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知の引数: $1" >&2; exit 2 ;;
  esac
done

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia-smi がありません" >&2
  exit 2
fi

idle_count=0
while true
do
  if ! utilization="$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -n 1 | tr -d ' ')"; then
    echo "nvidia-smi が失敗しました" >&2
    exit 2
  fi
  apps="$(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader || true)"
  others=""
  while IFS= read -r line
  do
    [ -n "${line//[[:space:]]/}" ] || continue
    pid="${line%%,*}"
    cmdline=""
    if [ -r "/proc/$pid/cmdline" ]; then
      cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    fi
    allowed_app=0
    for allowed in "${ALLOW[@]+"${ALLOW[@]}"}"
    do
      case "$line $cmdline" in
        *"$allowed"*) allowed_app=1; break ;;
      esac
    done
    if [ "$allowed_app" -eq 0 ]; then
      others+="$line"$'\n'
    fi
  done <<< "$apps"
  if [ "$utilization" = "0" ] && [ -z "$others" ]; then
    idle_count=$((idle_count + 1))
  else
    idle_count=0
  fi
  echo "$(date '+%F %T') util=${utilization}% idle_count=${idle_count}/${REQUIRED} apps=$(printf '%s' "$apps" | tr '\n' ';')"
  if [ "$idle_count" -ge "$REQUIRED" ]; then
    echo "GPU_IDLE"
    exit 0
  fi
  sleep "$INTERVAL"
done
