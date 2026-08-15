#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "📊 宏观经济指标看板"
echo "━━━━━━━━━━━━━━━━━━"
echo "🌐 浏览器打开: http://localhost:8080"
echo "⏹  Ctrl+C 停止"
echo ""
python3 -m http.server 8080
