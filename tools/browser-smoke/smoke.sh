#!/usr/bin/env bash
# ============================================================================
# 周期看板改进 · 三层门禁 · 云端自测通道
#   第0层：node --check js/*.js  +  headless 冒烟（Console 零真实错误）
#   第2层：浏览器层 DOM 可见文本 + SVG/Canvas 图形断言
#   硬闸门：Console 真实错误数 = 0 且 退出码 = 0
#   第1层（数据层）由 scripts/ssot_check.py + 独立重拉脚本承担。
# 用法: bash smoke.sh [repo_dir] [base_url]
# ============================================================================
set -u
REPO="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
PORT="${SMOKE_PORT:-8899}"
WORK="$(cd "$(dirname "$0")" && pwd)"
OUT="$WORK/output"; mkdir -p "$OUT"

echo "== 门禁目标仓库: $REPO"
echo "== 输出目录:     $OUT"

echo "[1/3] 第0层·语法生死线: node --check"
FAIL=0
shopt -s nullglob
for f in "$REPO"/js/*.js "$REPO"/macro-econ-dashboard/js/*.js; do
  if ! node --check "$f" >/dev/null 2>/tmp/syntax_err; then
    echo "  FAIL $(basename "$f")"; sed 's/^/      /' /tmp/syntax_err; FAIL=1
  else
    echo "  OK   $(basename "$f")"
  fi
done
[ $FAIL -ne 0 ] && { echo ">> 语法门禁未过，终止（FAIL 即打回）。"; exit 1; }
echo "  全部 JS 语法 OK"

echo "[2/3] 启动本地静态服务 :$PORT"
pkill -f "http.server $PORT" 2>/dev/null; sleep 1
( cd "$REPO" && setsid nohup python3 -m http.server "$PORT" --bind 127.0.0.1 >/tmp/smoke_http.log 2>&1 </dev/null & )
sleep 2
if ! curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/index.html"; then
  echo ">> 静态服务启动失败"; exit 1
fi

BASE_URL="${2:-http://127.0.0.1:$PORT}"
echo "[3/3] 第2层·headless 渲染 + Console/DOM/图形断言  (BASE=$BASE_URL)"
REPO="$REPO" BASE_URL="$BASE_URL" OUT="$OUT" python3 "$WORK/regression.py"
RC=$?
pkill -f "http.server $PORT" 2>/dev/null
echo "== 硬闸门：退出码=$RC （0=通过）"
exit $RC
