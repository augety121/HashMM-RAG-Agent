#!/usr/bin/env bash
# ============================================================
# HashMM 后端一键启动脚本（最新版 · 截至 V203）
#   chmod +x start-hashmm.sh  &&  ./start-hashmm.sh
#
# 说明：电脑操作 / 智能助理 / 浏览器 Agent / 多步序列 / 记忆 等新功能
#       都在「客户端 + App」侧实现；V203 新增：HASHMM_PRESET=max 一键满血、
#       技能包系统（零配置自动启用，数据在 data/skill_packs/）。务必先把下面的 service_role 占位换成你自己 ROTATE 后的新密钥。
# ============================================================
set -e

# ── 模型 / 数据路径 ──
export HASHMM_SRC=/root/autodl-tmp
export HASH_INDEX_DIR=/root/autodl-tmp/data/vector_index
export HASHMM_BASE_MODEL=/root/autodl-tmp/models/Qwen2.5-7B-Instruct
export HASHMM_SEARCHR1_LORA=/root/autodl-tmp/models/qwen2.5-7b-hashmm-final-v2

# ── 自带 JWT 密钥（本地账号用；换成你自己的随机串）──
# Security-sensitive values are loaded from the project .env or inherited
# environment. Never put JWT secrets in a launcher script.

# 登录令牌有效期（秒）。桌面端目前没有前端自动续期，默认已改成 30 天，避免正常使用中被踢去重新登录。
# 想更短寿命（更安全）就把这个数字调小；改密码/在设置里「强制登出」仍会立刻让旧令牌失效。
export HASHMM_ACCESS_TTL=2592000                    # 30 天

# ── Supabase（App 与客户端共用账号 + 跨端同步）──
export HASHMM_SUPABASE_URL=https://your-project.supabase.co
export HASHMM_SUPABASE_PUBLISHABLE_KEY=YOUR_SUPABASE_PUBLISHABLE_KEY
export HASHMM_SUPABASE_ADMIN_EMAILS=admin@example.invalid

# ★★ 同步到 Supabase 必需！没有它 App 看不到对话/记忆/动态。
#    Supabase 控制台 → Project Settings → API → 复制 service_role secret，粘到下面。
#    ⚠️ 安全提醒：你之前把这串密钥贴进脚本/截图了，等于全库可写，务必去控制台 ROTATE 重置一个新的再用，
#       且不要再提交进库或截图外发。这里留空位让你贴新的：
# HASHMM_SUPABASE_SERVICE_KEY must come from .env or the process environment.

# ★★ 公网地址（App 工作台「自动同步、无需手填」靠这个）。
#    uvicorn 跑在 6006，AutoDL 把它映射到外部 20014，所以填外部地址：
export HASHMM_PUBLIC_URL=http://111.115.7.14:20014

# ★★★ 权限审计 / 运行轨迹（管理后台这俩面板要数据就开着）★★★
export HASHMM_AUDIT_TOOLS=1
export HASHMM_AGENT_TRACE=1
#   （可选）人工审批默认关——开了会拦高危工具、影响正常使用，一般别开：
#   export HASHMM_TOOL_APPROVAL=1

# ◆◆◆ 新增：本地语音转文字（STT）——手机没系统语音服务时，App 录音上传到这里转文字 ◆◆◆
#   原理：用 faster-whisper 跑在你这台 4090 上，免费、不花 API 钱、语音不出你的服务器。
#   ① 安装依赖（已装会秒过；首次需联网装一次）：
export HF_ENDPOINT=https://hf-mirror.com          # 国内拉 Whisper 模型走镜像，避免超时/被墙
pip install -q faster-whisper 2>/dev/null || echo "[STT] faster-whisper 安装失败，可手动: pip install faster-whisper"
#   ② 开关与参数：
export HASHMM_STT_ENABLED=1                        # 0=关闭语音转文字
export HASHMM_STT_MODEL=small                      # tiny/base/small/medium/large-v3（中文建议 small 起步；要更准用 medium）
export HASHMM_STT_DEVICE=cuda                      # 显存紧张可改 cpu
export HASHMM_STT_COMPUTE=float16                  # cuda 用 float16；cpu 会自动退 int8
#   说明：首次调用 /api/stt 时才会下载并加载模型（启动不受影响）。
#   若模型下载不动：可先在能联网处 `huggingface-cli download Systran/faster-whisper-small`，
#   或把 HASHMM_STT_MODEL 指向已下载好的本地目录。

# ◆ V203 功能档位：一个开关解锁全部高级能力（等于把 ~30 个 HASHMM_* 高级旗标按推荐值打开）。
#   basic=历史行为 / recommended=推荐 / max=满血（更准但更吃算力）。4090 单卡建议直接 max。
#   注意：它只补默认，你在本脚本里显式 export 的任何旗标都不会被覆盖。
export HASHMM_PRESET=max

# ◆ V203 模型路由（客户端「模型路由」面板对应的两个总开关）：
#   开启后可把便宜杂活分流给本地小模型省 API 钱；面板里的逐任务配置保存在后端。
#   没有本地小模型就保持注释——所有任务照旧走云端，不影响任何功能。
# export HASHMM_LLM_ROUTING=1
# export HASHMM_LOCAL_LLM_PATH=/root/autodl-tmp/models/Qwen2.5-7B-Instruct

# ◆ （可选）检索增强开关——HASHMM_PRESET=max 已按推荐值打开这一层；
#   想单独强开/强关某项，取消注释显式指定即可（显式值优先于档位）：
# export HASHMM_NAVIGATE_EXPAND=1   # 章节图导航扩展（答案带相邻上下文）
# export HASHMM_MQE=1               # 多查询扩展（一个问题拆多条去检索）
# export HASHMM_RERANK=1            # 交叉编码重排（默认已用 bge-reranker，这个是额外层）

# ── GPU ──
export CUDA_VISIBLE_DEVICES=0

# ── V219 启动预检（防"跑成旧代码"——本轮 dispatch 404 排障的根治）──
#    ① 锁定到脚本所在目录（从任何地方运行都一致）；
#    ② 把本目录压到 PYTHONPATH 最前（对抗以前 pip install 过的旧副本遮蔽）；
#    ③ 校验 import 到的 hashmm 版本与路径——不对就拒绝启动并告诉你怎么修，
#       绝不再静默跑旧代码。通过后启动日志还会打印「[Server] 代码版本 …」二次自证。
cd "$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
python - <<'PYCHK'
import os, sys
try:
    import hashmm
except Exception as e:
    print(f"[start] ✗ 预检失败：当前目录导入不了 hashmm（{e}）。请确认完整源码树已解压到本目录。")
    sys.exit(1)
rel = getattr(hashmm, "RELEASE", "")
src = os.path.realpath(os.path.dirname(os.path.abspath(hashmm.__file__)))
here = os.path.realpath(os.path.join(os.getcwd(), "hashmm"))
print(f"[start] 代码版本 {rel or '（无 RELEASE 常量 → 旧代码）'} · 代码路径 {src}")
if src != here:
    print("[start] ✗ 加载到的 hashmm 不在当前目录——多半是以前 pip install 过的旧副本在遮蔽。")
    print(f"[start]   期望: {here}")
    print(f"[start]   实际: {src}")
    print("[start]   修复: pip uninstall -y hashmm   然后重跑本脚本。")
    sys.exit(1)
if not rel:
    print("[start] ✗ 本目录的 hashmm 不是 V218+ 源码树（缺 RELEASE 常量）。")
    print("[start]   请把最新完整源码树解压覆盖到本目录（当心解压成嵌套子文件夹）。")
    sys.exit(1)
PYCHK

# ── 启动（内部端口 6006，外部 20014）──
python -c "import uvicorn; uvicorn.run('hashmm.api.server:app', host='0.0.0.0', port=6006, log_level='info')"
