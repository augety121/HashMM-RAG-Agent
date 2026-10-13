#!/usr/bin/env bash
# scripts/e2e_local_backend.sh — 本地后端端到端验证（V89 固化）。
#
# 完整复刻用户桌面"本地模式"的全链路，任何 requirements 缺包/启动炸点
# 在交付前暴露（V88/V89 的两次真机事故——GBK 编码、python-multipart 暗依赖——
# 这条链都能在打包前抓住）。需要网络；全程约 2-4 分钟。
#
# 用法： bash scripts/e2e_local_backend.sh
# 通过标准：最后打印 "E2E ALL GREEN"

set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d /tmp/hashmm-e2e.XXXXXX)"
PORT=17891
trap 'kill "${SRV_PID:-0}" 2>/dev/null || true; rm -rf "$WORK"' EXIT

echo "[e2e] 1/6 净化 requirements（与 backendmgr 同一实现）"
node -e "
const { BackendManager } = require('$REPO/desktop/backendmgr');
const r = new BackendManager({ log: () => {} }).sanitizeRequirements('$REPO/requirements.txt', '$WORK');
if (r.error) { console.error(r.error); process.exit(1); }
console.log('      ' + r.count + ' packages, hash=' + r.hash);
"

echo "[e2e] 2/6 创建 venv"
python3 -m venv "$WORK/venv"

echo "[e2e] 3/6 pip 全量安装（真网）"
"$WORK/venv/bin/python" -m pip install -r "$WORK/requirements.runtime.txt" \
  --disable-pip-version-check --prefer-binary -q

echo "[e2e] 4/6 异地 cwd 启动后端（与 backendmgr 同款 -c 引导：V90 起不依赖 PYTHONPATH）"
mkdir -p "$WORK/home" && cd "$WORK/home"
BOOT="import os,sys; src=os.environ.get('HASHMM_SRC',''); (src and src not in sys.path) and sys.path.insert(0,src); getattr(sys.stdout,'reconfigure',lambda **k:None)(encoding='utf-8'); getattr(sys.stderr,'reconfigure',lambda **k:None)(encoding='utf-8'); import uvicorn; uvicorn.run('hashmm.api.server:app', host='127.0.0.1', port=int(os.environ.get('HASHMM_PORT','17680')), log_level='info')"
HASHMM_SRC="$REPO" HASHMM_PORT="$PORT" PYTHONUTF8=1 \
  HASHMM_JWT_SECRET="e2e-$(head -c24 /dev/urandom | base64 | tr -d '/+=')" \
  "$WORK/venv/bin/python" -c "$BOOT" >"$WORK/boot.log" 2>&1 &
SRV_PID=$!

ok=""
for i in $(seq 1 45); do
  sleep 2
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/health" 2>/dev/null; then ok=1; break; fi
  kill -0 "$SRV_PID" 2>/dev/null || { echo "[e2e] 进程退出，日志尾部："; tail -25 "$WORK/boot.log"; exit 1; }
done
[ -n "$ok" ] || { echo "[e2e] health 超时"; tail -25 "$WORK/boot.log"; exit 1; }
echo "      /api/health 200"

echo "[e2e] 5/6 登录 admin/admin123"
TOKEN=$(curl -fsS -X POST "http://127.0.0.1:$PORT/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token') or d.get('access_token') or '')")
[ -n "$TOKEN" ] || { echo "[e2e] 登录未返回 token"; exit 1; }
echo "      token(${#TOKEN} chars)"

echo "[e2e] 6/6 带 token 调 /api/conversations"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/api/conversations" \
  -H "Authorization: Bearer $TOKEN")
[ "$CODE" = "200" ] || { echo "[e2e] conversations 返回 $CODE"; exit 1; }
echo "      200"
echo "E2E ALL GREEN"
