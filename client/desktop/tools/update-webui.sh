#!/usr/bin/env bash
# HashMM 桌面端 webui 热替换（V271，macOS/Linux 版）。
# 用法：./update-webui.sh "/Applications/HashMM.app/Contents"   # 或 Linux 安装目录
set -euo pipefail
SRC="$(cd "$(dirname "$0")/../../frontend-next/out" 2>/dev/null && pwd || true)"
[ -f "${SRC:-}/index.html" ] || { echo "[X] 未找到构建产物，请先: cd frontend-next && npm ci && npm run build"; exit 1; }
DEST="${1:-}"
[ -n "$DEST" ] && [ -d "$DEST/resources" ] || { echo "[X] 请传入含 resources 的安装目录，例如 /opt/HashMM 或 HashMM.app/Contents"; exit 1; }
echo "[i] $SRC → $DEST/resources/webui"
rm -rf "$DEST/resources/webui.bak"
[ -d "$DEST/resources/webui" ] && mv "$DEST/resources/webui" "$DEST/resources/webui.bak"
cp -R "$SRC" "$DEST/resources/webui"
echo "[√] 完成，重启桌面端生效（旧版备份为 webui.bak）"
