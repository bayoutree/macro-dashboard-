#!/usr/bin/env bash
# ============================================================
# pre_push_check.sh — 推送前强制门禁
# 起源：2026-09-18 生产事故（cycle_v3.js 圆括号不平衡，
#       整个文件解析失败，线上所有 ECharts 不渲染，回滚 1.5h）
#
# 用法：bash scripts/pre_push_check.sh
# 退出码非 0 = 门禁未通过，禁止 git push
# ============================================================
set -u

cd "$(dirname "$0")/.." || exit 2

FAIL=0
pass() { echo "  ✅ $1"; }
fail() { echo "  ❌ $1"; FAIL=1; }

echo "================ 推送前门禁检查 ================"

# ---------- 1. JS 语法检查（10 秒即可拦住解析级事故）----------
echo "[1/3] JavaScript 语法检查 (node --check)"
if ! command -v node >/dev/null 2>&1; then
  echo "  ⚠️  node 不可用，跳过 JS 检查（请确保本地有 node 再推送前端改动）"
else
  for js in js/*.js; do
    [ -f "$js" ] || continue
    if node --check "$js" 2>/tmp/jscheck_err; then
      pass "$js"
    else
      fail "$js 语法错误："
      cat /tmp/jscheck_err
    fi
  done
fi

# ---------- 2. JSON 数据文件可解析 ----------
echo "[2/3] JSON 数据文件有效性检查"
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PYCHECK'
import json, glob, sys
bad = 0
for f in glob.glob("data/*.json"):
    try:
        with open(f, encoding="utf-8") as fh:
            json.load(fh)
        print(f"  ✅ {f}")
    except Exception as e:
        print(f"  ❌ {f}: {e}")
        bad += 1
sys.exit(1 if bad else 0)
PYCHECK
  [ $? -eq 0 ] && pass "全部 JSON 可解析" || fail "存在无法解析的 JSON"
else
  echo "  ⚠️  python3 不可用，跳过 JSON 检查"
fi

# ---------- 3. timing_scores.json history 存活检查 ----------
# 防止 pipeline 清空根因复发（2026-09-16、09-18 复发过两次）
echo "[3/3] timing_scores.json 已验证 history 存活检查"
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PYHIST'
import json, sys
VERIFIED = {
    ("liquidity", "social_financing_trend"),
    ("liquidity", "m1_m2_scissors"),
    ("valuation", "hs300_pb_percentile"),
    ("valuation", "buffett_ratio"),
    ("valuation", "break_net_rate"),
    ("equity_bond", "dividend_bond_spread_hs300"),
    ("equity_bond", "dividend_bond_spread_red"),
    ("equity_bond", "hs300_erp"),
}
try:
    with open("data/timing_scores.json", encoding="utf-8") as f:
        d = json.load(f)
except FileNotFoundError:
    print("  ℹ️  timing_scores.json 不存在，跳过")
    sys.exit(0)
missing = []
for dk, dim in d.get("dimensions", {}).items():
    for ik, ind in dim.get("indicators", {}).items():
        if (dk, ik) in VERIFIED:
            h = ind.get("history") if isinstance(ind, dict) else None
            if not isinstance(h, list) or len(h) < 2:
                missing.append(f"{dk}.{ik}")
if missing:
    print(f"  ⚠️  以下已验证指标 history 缺失（前端将灰灯/空图）: {', '.join(missing)}")
    print("     若为有意删除可忽略；若刚跑过 update_data.py，请先跑 sync_timing_scores_history.py")
else:
    print("  ✅ 8 个已验证指标 history 均存活")
PYHIST
fi

echo "================================================"
if [ "$FAIL" -ne 0 ]; then
  echo "🚫 门禁未通过，禁止推送。修复上述错误后重试。"
  exit 1
fi
echo "🟢 门禁通过，可以推送。"
