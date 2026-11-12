# frontend/data/ 全仓引用扫描与删除可行性证据

- 评估日：2026-11-12
- 依据：CIO 裁决②（frontend/data/ 保留 30 天观察，零引用则删；期间强制一致性门禁 + noindex）
- 结论先行：**权威站点（GitHub Pages 根目录）对 frontend/data/ 零引用**；但 `frontend/` 本身是一个**线上仍可访问的完整子站**（自带 js/css/data），它的页面按相对路径读 `frontend/data/`。因此"只删 data 副本"会做出半残子站，建议改为**整体下线 frontend/ 子站**（30 天观察后执行）。

---

## 1. 扫描口径

```bash
# 读侧：页面与脚本是否请求 frontend/data/
grep -rn "frontend/data" --include="*.html" --include="*.js" . | grep -v node_modules | wc -l   # → 0
# 写侧 / 门禁侧：是否有代码写入、镜像、校验该目录
grep -rn "frontend/data" --include="*.py" --include="*.yml" --include="*.json" . \
  | grep -v node_modules | grep -v "^./tools/healthcheck" | grep -v "^./frontend/data"
```

## 2. 读侧结论：权威站点零引用

- 根站点数据目录由 `js/app.js:8` 固定为 `dataDir: 'data'`，所有 fetch 走 `data/*.json`；`stock_dashboard.html:1135` 直接 `fetch('data/timing_scores.json')`。
- 全仓 html/js 中对字面量 `frontend/data` 的命中数：**0**。
- 唯一"读 frontend/data/"的是 `frontend/` 子站自己：它把 `js/`、`css/`、`data/` 各复制了一份，页面里仍是相对路径 `data/xxx.json`，解析后落在 `frontend/data/`。

即：**主站不依赖该目录，子站靠它活着。**

## 3. 线上可达性证据（2026-11-12 实测）

| URL | 状态 | 大小 |
|---|---|---|
| `https://bayoutree.github.io/macro-dashboard-/frontend/` | 200 | 11,627 B |
| `https://bayoutree.github.io/macro-dashboard-/frontend/index.html` | 200 | 11,627 B |
| `https://bayoutree.github.io/macro-dashboard-/frontend/data/timing_scores.json` | 200 | 1,738,302 B |

→ 子站与数据副本对公网可直接访问、可被搜索引擎收录。裁决②的 noindex 已落地（见 §5）。

## 4. 写侧 / 门禁侧引用（9 个文件，共 12 处）

| # | 文件:行 | 用途 | 删副本前必须处理 |
|---|---|---|---|
| 1 | `scripts/history_guard.py:37` | `DATA_DIRS = ("data", "macro-econ-dashboard/data", "frontend/data")`，日更守卫逐目录扫 | 从元组移除 `frontend/data` |
| 2 | `scripts/freshness_gate.py:914/952/964` | FE-DRIFT 前后端双副本一致性校验（内容 + ssot_source 标记） | 有 `if _fe_dir.is_dir()` 保护，目录不存在自动跳过；建议随删除一并删该段代码，避免"空跑通过"假绿 |
| 3 | `scripts/ssot_check.py:188-192` | `--fix` 单点修复镜像到 frontend/data/ | 删除镜像逻辑 |
| 4 | `scripts/rebuild_timing_history.py:255` | 落盘时同时写 data/ 与 frontend/data/ | 去掉第二写目标 |
| 5 | `scripts/history_guard_allowlist.json:5/14/32` | 白名单登记 3 个 frontend/data/ 文件 | 删对应条目 |
| 6 | `scripts/monthly_consistency_check.py:9/62-72` | L2 静态侧 A 段：data/ ↔ frontend/data/ 哈希一致 | 该段整体退场（CIO 自己的入口，需 CIO 改） |
| 7 | `.github/workflows/update-data.yml:189/214` | 日更后 `cp data/*.json frontend/data/` | 删同步步骤 |
| 8 | `.github/workflows/macro-econ-dashboard.yml:50-56` | 同上（宏观链路同步） | 删同步步骤 |
| 9 | `.github/workflows/daily_update.yml` | 同上（另一条日更链） | 删同步步骤 |

**关键点：日更流水线目前是"写两侧"，一旦只删目录不删写入步骤，日更会因 `cp` 目标不存在而失败或产生脏状态。删除动作必须是"代码先行、目录后删"。**

## 5. noindex 落地（裁决②期间强制项）

`frontend/index.html:6`、`frontend/stock_dashboard.html:6` 已插入：

```html
<meta name="robots" content="noindex,nofollow">
```

验证：`grep -n "robots" frontend/*.html` → 两文件第 6 行命中。

## 6. 建议方案（比"只删 data 副本"更彻底）

**方案 B：整体下线 `frontend/` 子站**（推荐）
- 理由：Vercel 已废弃，子站唯一作用是历史镜像；data 副本只是它的一部分，只删 data 会留下"页面在、数据 404"的半残站，反而制造新的呈现层故障。
- 步骤：① 30 天观察期内（至 2026-12-12）保持现状 + noindex + FE-DRIFT 门禁；② 观察期结束当日确认 Pages 主站无 404、无引用；③ 一次性提交：删 `frontend/` 整个目录 + 清 §4 的 9 处写入/校验代码 + 删 3 条白名单；④ 同日跑 `monthly_audit.py --strict` + `monthly_consistency_check.py` 验证无回归。

**方案 A：只删 `frontend/data/`（不推荐）** —— 需保留子站 html/js，但页面会取不到数据；除非同时把子站页面改为读 `../data/`，工作量大于整体下线。

## 7. 附：本次扫描未发现的风险

- 根站点 6 个核心 JSON（cn_macro / us_macro / cycle_position / asset_valuation / asset_prices / dashboard_summary）+ timing_scores / feargreed_pointer 均从 `data/` 取，与 frontend/data/ 无关。
- `frontend/data/` 内 39 个 JSON 与 `data/` 同名文件在 2026-11-12 巡检中语义一致（FE-DRIFT 0 命中），观察期内不存在"页面展示旧值"的即时风险。
