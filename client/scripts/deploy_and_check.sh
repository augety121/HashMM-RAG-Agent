#!/usr/bin/env bash
# 一键部署 + 自检。在项目根运行： bash scripts/deploy_and_check.sh
# 作用：确认新代码在 → 前端 build → 重启 uvicorn → 自检下载路由。
set -e
cd "$(dirname "$0")/.."

echo "===== 1. 确认后端是 V41+ 新代码 ====="
A=$(grep -c "_strip_big_code_blocks" hashmm/agent/loop.py || echo 0)
B=$(grep -c "def download_conv_file" hashmm/api/routes/conversations.py || echo 0)
echo "  _strip_big_code_blocks: $A (应≥1)"
echo "  download_conv_file 路由: $B (应≥1)"
if [ "$A" -lt 1 ] || [ "$B" -lt 1 ]; then
  echo "  ✗ 后端代码不是最新！请先把 zip 解压覆盖到项目根，再重跑本脚本。"
  exit 1
fi
echo "  ✓ 后端代码是最新"

echo "===== 2. 前端 rebuild ====="
if [ -d frontend-next ]; then
  cd frontend-next
  npm run build
  cd ..
  echo "  ✓ 前端已 build"
else
  echo "  ⚠ 未找到 frontend-next，跳过"
fi

echo "===== 3. 杀掉旧 uvicorn 进程 ====="
pkill -f "uvicorn hashmm.api.server:app" 2>/dev/null && echo "  ✓ 已杀旧进程" || echo "  (没有正在运行的旧进程)"
sleep 2

echo "===== 4. 重新启动（后台） ====="
echo "  请手动运行你的启动命令，例如："
echo "  HASHMM_EVAL_EXEC=1 CUDA_VISIBLE_DEVICES=0 python -m uvicorn hashmm.api.server:app --host 0.0.0.0 --port 6006"
echo ""
echo "启动后，新对话里写代码 → 点下载。若仍404，日志会有 [下载诊断] 行（区分鉴权/文件问题），发我。"
