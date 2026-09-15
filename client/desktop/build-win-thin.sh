#!/usr/bin/env bash
# build-win-thin.sh — 在 Ubuntu/容器里打「纯瘦客户端」Windows 安装包（无内嵌终端）。
#
# 原理：带 node-pty 原生模块的完整版无法在 Linux 上交叉编译成 Windows 二进制；
# 但去掉 node-pty 后整个 app 就是纯 JS/HTML，没有任何原生模块，electron-builder
# 配合 Wine 即可在 Linux 上产出 Windows nsis 安装包。
#
# 本脚本：备份 → 摘掉 node-pty 依赖 + 临时改 main.js 跳过 require → 装依赖 →
#          electron-builder --win → 无论成败都还原现场。
#
# 前置：已装 wine（见 BUILD-WINDOWS.md 路线 B）。
set -euo pipefail
cd "$(dirname "$0")"

echo "==> [1/5] 备份现场"
cp package.json package.json.bak
cp main.js main.js.bak

cleanup() {
  echo "==> 还原现场"
  mv -f package.json.bak package.json 2>/dev/null || true
  mv -f main.js.bak main.js 2>/dev/null || true
}
trap cleanup EXIT

echo "==> [2/5] 摘掉 node-pty 依赖（纯瘦客户端不需要原生模块）"
node -e '
  const fs=require("fs");
  const pj=JSON.parse(fs.readFileSync("package.json","utf8"));
  if (pj.dependencies) delete pj.dependencies["node-pty"];
  fs.writeFileSync("package.json", JSON.stringify(pj,null,2));
  console.log("    node-pty 已从 dependencies 移除");
'
# main.js 里的 require("node-pty") 包在 try/catch 里，缺了会自动降级——无需改源码。
# 但为干净起见，确认降级分支存在：
grep -q 'node-pty 未就绪' main.js && echo "    main.js 已有 node-pty 缺失降级分支 ✓"

echo "==> [3/5] 清依赖装纯 JS 依赖"
rm -rf node_modules package-lock.json
export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://npmmirror.com/mirrors/electron/}"
export ELECTRON_BUILDER_BINARIES_MIRROR="${ELECTRON_BUILDER_BINARIES_MIRROR:-https://npmmirror.com/mirrors/electron-builder-binaries/}"
npm install --no-audit --no-fund

echo "==> [4/5] electron-builder 打 Windows 包（Wine）"
npx electron-builder --win

echo "==> [5/5] 完成。产物："
ls -lh dist/*.exe 2>/dev/null || { echo "未找到 exe，检查上面的 electron-builder 输出"; exit 1; }
echo ""
echo "这是【无内嵌终端】的瘦客户端 exe，拷到 Windows 双击即装。"
echo "要带终端的完整版，请在 Windows 机器上按 BUILD-WINDOWS.md 路线 A 打。"
