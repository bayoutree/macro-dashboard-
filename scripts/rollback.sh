#!/usr/bin/env bash
# ============================================================
# rollback.sh — 一键回滚到指定 commit 或上一个 commit
# 起源：2026-09-18 事故回滚耗时 1.5 小时（19:42 授权 → 21:11 完成）
#
# 用法：
#   bash scripts/rollback.sh              # 回滚到 HEAD~1（默认撤销最近一次提交）
#   bash scripts/rollback.sh <commit>     # 回滚到指定 commit（如 6acccd67）
#   bash scripts/rollback.sh --dry <c>    # 预演：只显示将执行的操作，不推送
#
# 策略：采用 git revert（安全、保留历史）而非 reset --hard。
#       若确需硬重置到某 commit（如子 agent 连续多个坏 commit），
#       显式加 --hard：bash scripts/rollback.sh --hard <commit>
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="revert"
DRY=0
TARGET=""

for arg in "$@"; do
  case "$arg" in
    --dry)  DRY=1 ;;
    --hard) MODE="hard" ;;
    *)      TARGET="$arg" ;;
  esac
done

run() {
  echo "  > $*"
  [ "$DRY" -eq 0 ] && eval "$*"
}

echo "================ 一键回滚 ================"
echo "当前分支: $(git rev-parse --abbrev-ref HEAD)"
echo "当前 HEAD: $(git rev-parse --short HEAD)"

# 同步远程，避免基于过期状态回滚
git fetch origin >/dev/null 2>&1 || echo "⚠️  git fetch 失败（网络问题），继续基于本地状态"

if [ "$MODE" = "hard" ]; then
  COMMIT="${TARGET:-HEAD~1}"
  echo "模式：硬重置到 $COMMIT（将丢弃之后的所有提交）"
  run "git reset --hard $COMMIT"
  run "git push origin main --force-with-lease"
else
  # revert 模式
  if [ -n "$TARGET" ]; then
    # 回滚到指定 commit：revert 从 TARGET 之后到 HEAD 的所有提交
    RANGE="${TARGET}..HEAD"
    COUNT=$(git rev-list --count "$RANGE" 2>/dev/null || echo "?")
    echo "模式：revert $TARGET..HEAD（共 $COUNT 个提交）"
    echo "将撤销的提交："
    git --no-pager log --oneline "$RANGE" 2>/dev/null | sed 's/^/    /' || true
    # 从新到旧逐个 revert
    for c in $(git rev-list "$RANGE" 2>/dev/null); do
      run "git revert --no-edit $c"
    done
  else
    echo "模式：revert HEAD（撤销最近一次提交）"
    run "git revert --no-edit HEAD"
  fi
  run "git push origin main"
fi

echo ""
echo "✅ 回滚操作已执行（dry-run=$DRY）。"
echo "提醒：回滚后请同步更新 index.html 中的 JS 缓存版本号，"
echo "      否则用户浏览器可能仍加载旧 JS（2026-09-18 曾因此误判回滚失败）。"
echo "=========================================="
