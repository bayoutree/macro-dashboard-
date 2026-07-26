#!/usr/bin/env python3
"""
一键运行所有数据获取脚本
按顺序执行，单个失败不阻塞其他
"""
import sys
import os
import time
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import logger, DATA_DIR, SCRIPTS_DIR

SCRIPTS = [
    ("fetch_us_macro.py", "美国宏观数据 (FRED)"),
    ("fetch_cn_macro.py", "中国宏观数据 (AKShare)"),
    ("fetch_asset_data.py", "全球资产价格 (yfinance)"),
    ("calc_valuations.py", "资产估值计算"),
    ("generate_summary.py", "看板综合结论"),
]


def run_script(script_name, description):
    """执行单个脚本，返回 (成功, 耗时, 错误信息)"""
    script_path = SCRIPTS_DIR / script_name
    logger.info(f"\n{'='*60}")
    logger.info(f"▶ 执行: {description} ({script_name})")
    logger.info(f"{'='*60}")

    start = time.time()
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=False,
            timeout=300,
            env={**os.environ}
        )
        elapsed = time.time() - start
        if result.returncode == 0:
            logger.info(f"✅ {script_name} 成功 ({elapsed:.1f}s)")
            return True, elapsed, None
        else:
            logger.error(f"❌ {script_name} 失败 (exit code {result.returncode}, {elapsed:.1f}s)")
            return False, elapsed, f"exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        logger.error(f"❌ {script_name} 超时 (>300s)")
        return False, elapsed, "timeout"
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"❌ {script_name} 异常: {e}")
        return False, elapsed, str(e)


def main():
    start_all = time.time()
    logger.info("🚀 宏观经济指标看板 - 数据更新开始")
    logger.info(f"   时间: {datetime.now().isoformat()}")
    logger.info(f"   数据目录: {DATA_DIR}")

    results = []
    for script, desc in SCRIPTS:
        success, elapsed, error = run_script(script, desc)
        results.append({
            "script": script,
            "description": desc,
            "success": success,
            "elapsed": elapsed,
            "error": error
        })

    # 打印摘要
    total_time = time.time() - start_all
    success_count = sum(1 for r in results if r["success"])

    logger.info(f"\n{'='*60}")
    logger.info("📊 执行摘要")
    logger.info(f"{'='*60}")
    for r in results:
        status = "✅" if r["success"] else "❌"
        logger.info(f"  {status} {r['description']:30s} {r['elapsed']:6.1f}s  {r.get('error', '') or ''}")

    logger.info(f"\n总计: {success_count}/{len(results)} 成功, 耗时 {total_time:.1f}s")

    if success_count == len(results):
        logger.info("🎉 所有数据更新成功!")
    else:
        logger.warning(f"⚠️ {len(results) - success_count} 个脚本执行失败")

    # 检查输出文件
    expected_files = [
        "us_macro.json", "cn_macro.json", "asset_prices.json",
        "asset_valuation.json", "dashboard_summary.json"
    ]
    logger.info(f"\n数据文件检查:")
    for f in expected_files:
        path = DATA_DIR / f
        if path.exists():
            size = path.stat().st_size / 1024
            logger.info(f"  ✓ {f:30s} {size:.1f} KB")
        else:
            logger.info(f"  ✗ {f:30s} 不存在")


if __name__ == "__main__":
    main()
