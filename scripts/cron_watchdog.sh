#!/bin/bash
# cron_watchdog.sh —— GitHub schedule 缺跑自动补（本地 cron, Asia/Shanghai）
# 背景：11-08/11-09 GitHub schedule 延迟 6.7~10.5h 触发，依赖平台自觉不可靠。
# 机制：每晚两轮（21:40 / 23:40 CST）检查当日(UTC)三条链路是否有 success/进行中的 run，
#       缺失即 workflow_dispatch 补跑；23:40 轮仍缺则落 ALERT 文件，次日自查读取上报。
# 密钥：从本地 git remote URL 动态提取，不落盘、不进仓库。
# 用法：cron_watchdog.sh [--dry-run]
set -u
REPO="bayoutree/macro-dashboard-"
API="https://api.github.com/repos/${REPO}/actions/workflows"
LOG_TAG="cron_watchdog"
ALERT_FILE="/root/cron_watchdog_ALERT"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

TOKEN=$(git -C /root/macro-dashboard- remote get-url origin 2>/dev/null | sed -E 's#https://([^:@]+(:[^@]*)?)@.*#\1#' | sed -E 's#^[^:]*:##')
if [ -z "$TOKEN" ]; then echo "[$LOG_TAG] FATAL 无法从 git remote 提取 token"; exit 1; fi

TODAY=$(date -u +%Y-%m-%d)   # 16:00 CST 场 = 当日 08:00 UTC，手动补跑亦在 UTC 当日，按 UTC 日过滤即可
log(){ echo "[$(date '+%F %T %Z')] [$LOG_TAG] $*"; }

check_and_dispatch(){
  local wf="$1" label="$2"
  local cnt
  cnt=$(curl -s --max-time 30 -H "Authorization: Bearer ${TOKEN}" -H "Accept: application/vnd.github+json" \
    "${API}/${wf}/runs?created=%3E%3D${TODAY}&per_page=30" \
    | python3 -c "
import json,sys
try:
    runs=json.load(sys.stdin).get('workflow_runs',[])
except Exception:
    print(-1); raise SystemExit
good=[r for r in runs if r.get('conclusion')=='success' or r.get('status') in ('in_progress','queued','waiting')]
print(len(good))" 2>/dev/null)
  if [ -z "$cnt" ] || [ "$cnt" = "-1" ]; then
    log "ERR  ${label} API 查询失败，跳过（下轮重试）"; return 1
  fi
  if [ "$cnt" -ge 1 ]; then
    log "OK   ${label} 当日已有 ${cnt} 个有效 run（success/进行中）"
    return 0
  fi
  log "MISS ${label} 当日无有效 run"
  if [ "$DRY_RUN" = "1" ]; then log "DRY  ${label} dry-run 模式不 dispatch"; return 2; fi
  local code
  code=$(curl -s --max-time 30 -o /dev/null -w '%{http_code}' -X POST \
    -H "Authorization: Bearer ${TOKEN}" -H "Accept: application/vnd.github+json" \
    "${API}/${wf}/dispatches" -d '{"ref":"main"}')
  if [ "$code" = "204" ]; then
    log "FIX  ${label} 已 dispatch 补跑（HTTP 204）"
  else
    log "ALERT ${label} dispatch 失败 HTTP=${code}"
    echo "$(date '+%F %T') ${label} dispatch HTTP=${code}" >> "$ALERT_FILE"
  fi
}

log "---- 本轮检查开始（${TODAY} UTC, dry_run=${DRY_RUN}）----"
check_and_dispatch "daily_update.yml"         "daily_update(主数据)"
check_and_dispatch "macro-econ-dashboard.yml" "macro-econ(宏观)"
check_and_dispatch "update-data.yml"          "update-data(frontend同步)"
log "---- 本轮检查结束 ----"
