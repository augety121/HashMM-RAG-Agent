#!/usr/bin/env bash
# HashMM 外部基准安装脚本。
# ★ 铁律：**只增不改**。全部装进 $HASHMM_BENCH_HOME 下的独立 venv/目录，
#   绝不碰你后端主 Python 环境、绝不改任何已装包的版本、不改系统 PATH。
#
# 用法：
#   bash install.sh nodocker   # ★ 推荐：一次装好【所有不需要 Docker 就能出真分】的基准
#   bash install.sh bfcl|tau2|gaia|kotlin|webvoyager|swebench|terminal|agentbench
set -e
# 路径优先级：环境变量 > /root/autodl-tmp(AutoDL 持久盘) > $HOME
if [ -n "$HASHMM_BENCH_HOME" ]; then BENCH_HOME="$HASHMM_BENCH_HOME"
elif [ -d /root/autodl-tmp ]; then BENCH_HOME="/root/autodl-tmp/hashmm-benchmarks"
else BENCH_HOME="$HOME/hashmm-benchmarks"; fi
mkdir -p "$BENCH_HOME"
# V306 修真 bug：此前只认 $1，`install.sh gaia kotlin` 会静默忽略 kotlin（这就是你 Kotlin 没装上的原因）
TARGETS=("$@"); [ ${#TARGETS[@]} -eq 0 ] && TARGETS=("nodocker")
echo "== HashMM 基准安装 → $BENCH_HOME =="
echo "== 目标：${TARGETS[*]} =="

# V325：统一 venv 助手——检测 venv 是否可用（防路径搬迁导致坏 shebang），坏就 --clear 重建。
# 之后一律用 "$V/bin/python -m pip"（不依赖 pip 脚本 shebang）。返回 0=就绪，1=创建失败。
_ensure_venv() {  # $1 = venv 绝对路径
  local V="$1"
  if [ ! -x "$V/bin/python" ] || ! "$V/bin/python" -c "import sys" 2>/dev/null; then
    echo "-- venv 不存在或已损坏（多为 BENCH_HOME 路径变过），重建：$V"
    rm -rf "$V"
    python3 -m venv "$V" 2>/dev/null || {
      echo "!! venv 创建失败——请先装：apt-get install -y python3-venv"; return 1; }
  fi
  return 0
}

_clone() {  # _clone <url> <dir> [sparse-path]，带重试 + 镜像兜底（对抗 github TLS 偶发断）
  local url="$1" dir="$2" sp="$3"
  [ -d "$BENCH_HOME/$dir/.git" ] && { echo "-- $dir 已存在，跳过 clone"; return 0; }
  rm -rf "$BENCH_HOME/$dir"
  # git buffer 调大，减少大仓 TLS 中断
  git config --global http.postBuffer 524288000 2>/dev/null || true
  git config --global http.lowSpeedLimit 1000 2>/dev/null || true
  git config --global http.lowSpeedTime 60 2>/dev/null || true
  local path="${url#https://github.com/}"
  # 依次尝试：原始 github → ghproxy 镜像 → gitclone 镜像
  local mirrors=("$url" "https://ghproxy.net/https://github.com/$path" "https://gitclone.com/github.com/$path")
  for m in "${mirrors[@]}"; do
    for attempt in 1 2; do
      echo "  clone 尝试：$m (第 $attempt 次)"
      if [ -n "$sp" ]; then
        if git clone --depth 1 --filter=blob:none --sparse -q "$m" "$BENCH_HOME/$dir" 2>/dev/null; then
          (cd "$BENCH_HOME/$dir" && git sparse-checkout set "$sp") && return 0
        fi
      else
        git clone --depth 1 -q "$m" "$BENCH_HOME/$dir" 2>/dev/null && return 0
      fi
      rm -rf "$BENCH_HOME/$dir"; sleep 2
    done
  done
  echo "  !! $dir clone 失败（github 与镜像都试过了）。可稍后重试，或手动 clone 到 $BENCH_HOME/$dir"
  return 1
}

_fetcher() {  # 定位多源数据集下载器（install.sh 在 hashmm/evaluation/benchmarks/ 下 → 仓库根/scripts/）
  local d; d="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." 2>/dev/null && pwd)"
  [ -n "$d" ] && [ -f "$d/scripts/fetch_datasets.py" ] && echo "$d/scripts/fetch_datasets.py"
}

_swebench_pro_schema_ok() {  # 旧缓存可能有 731 行，但曾被错误裁掉官方 Docker 判分字段
  local p="${1:-$BENCH_HOME/swebench_pro.jsonl}"
  [ -s "$p" ] || return 1
  python3 - "$p" <<'PY'
import json, sys
required = {
    "instance_id", "problem_statement", "fail_to_pass", "pass_to_pass",
    "before_repo_set_cmd", "selected_test_files_to_run",
}
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        lines = (line for line in f if line.strip())
        row = json.loads(next(lines))
        count = 1 + sum(1 for _ in lines)
    raise SystemExit(0 if count >= 500 and required.issubset(row) else 1)
except Exception:
    raise SystemExit(1)
PY
}

install_humaneval() {
  echo ""; echo "== ★★ HumanEval+MBPP（Python 代码）· 零依赖 · 免 Docker =="
  _clone https://github.com/openai/human-eval.git human-eval
  _clone https://github.com/google-research/google-research.git google-research mbpp
  [ -n "$(find "$BENCH_HOME/human-eval" -name 'HumanEval.jsonl*' -print -quit 2>/dev/null)" ] \
    && echo "-- HumanEval 数据就绪" || echo "!! HumanEval 数据缺失"
  [ -n "$(find "$BENCH_HOME/google-research" -name 'mbpp.jsonl' -print -quit 2>/dev/null)" ] \
    && echo "-- MBPP 数据就绪" || echo "!! MBPP 数据缺失"
}

install_bfcl() {
  echo ""; echo "== ★ BFCL（工具调用）· 免 Docker =="
  _clone https://github.com/ShishirPatil/gorilla.git gorilla berkeley-function-call-leaderboard
  [ -n "$(find "$BENCH_HOME/gorilla" -path '*data*' -name '*simple*.json' -print -quit 2>/dev/null)" ] \
    && echo "-- BFCL 数据就绪" || echo "!! 没找到 BFCL 数据"
}

install_tau2() {
  echo ""; echo "== ★ τ²-bench（多步交互）· 纯 Python · 免 Docker =="
  _clone https://github.com/sierra-research/tau-bench.git tau-bench
  V="$BENCH_HOME/tau2-venv"
  _ensure_venv "$V" || return
  PY="$V/bin/python"
  "$PY" -m pip install -q --upgrade pip 2>/dev/null || true
  "$PY" -m pip install -q -e "$BENCH_HOME/tau-bench" \
    && echo "-- τ²-bench 已装进独立 venv（retail 115 题 / airline 50 题）" \
    || echo "!! τ²-bench venv 安装失败"
  # ★ V310：官方 harness 运行期还需要 fastapi/litellm/pydantic —— `pip install -e .` 不一定
  # 把它们带全。缺了会导致【所有题空轨迹 reward=0】（harness 逐题吞异常照写 0 分），
  # 表面上像"模型一题不会"，实际压根没跑起来。这里显式补齐。
  "$PY" -m pip install -q fastapi litellm pydantic 2>/dev/null \
    && echo "-- τ²-bench 运行期依赖已补齐（fastapi/litellm/pydantic）" \
    || echo "!! τ²-bench 运行期依赖补齐失败（首次跑分时会自动重试补装）"
  "$PY" -c "import fastapi, litellm" 2>/dev/null \
    && echo "-- τ²-bench 依赖自检通过" \
    || echo "!! τ²-bench 依赖自检未过 —— 跑分会跳过并提示手工安装命令"
}

install_webarena() {
  echo ""; echo "== WebArena 官方 812 题 + Playwright/Chromium 评测端 =="
  _clone https://github.com/web-arena-x/webarena.git webarena
  # 官方仓库只提交一个 test.raw.json（内含 812 条），逐题 0.json..811.json 需要配置
  # 网站 URL 后再生成。旧检查把“1 个 json 文件”误报成任务已安装，运行时却加载不到任何题。
  n=$(python -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p,encoding="utf-8")); print(len(d) if isinstance(d,list) else 0)' \
      "$BENCH_HOME/webarena/config_files/test.raw.json" 2>/dev/null || echo 0)
  if [ "$n" != "812" ]; then
    echo "!! WebArena 官方任务损坏：期望 812 条，实际 $n"
    return 1
  fi
  echo "-- WebArena 官方任务就绪：$n 条"

  # 浏览器二进制放进 BENCH_HOME，跟数据集一起进入 Actions cache；Python 包仍由 pip cache 秒装。
  export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$BENCH_HOME/ms-playwright}"
  python -m pip install -q "playwright>=1.40,<2" \
    || { echo "!! Playwright Python 包安装失败"; return 1; }
  if ! python -c 'from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); x=Path(p.chromium.executable_path); p.stop(); assert x.is_file(), x' 2>/dev/null; then
    echo "-- 首次下载 Chromium（缓存后下次不重复下载）"
    if command -v apt-get >/dev/null 2>&1; then
      python -m playwright install --with-deps chromium \
        || { echo "!! Chromium/系统依赖安装失败"; return 1; }
    else
      python -m playwright install chromium \
        || { echo "!! Chromium 安装失败"; return 1; }
    fi
  fi
  python -c 'from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); x=Path(p.chromium.executable_path); p.stop(); assert x.is_file(), x; print("-- Chromium 就绪：", x)' \
    || { echo "!! Chromium 安装后自检失败"; return 1; }
  cat <<'GUIDE'
-- WebArena 网站群需要独立持久环境；GitHub runner 负责 Agent/Chromium/evaluator。
   Actions 的 webarena_host 填官方 AMI 公网域名/IP（不带端口），程序会自动派生七个 URL、
   健康检查并生成官方账户登录 cookie。官方 AMI：us-east-2 / ami-08a862bf98e3bd7aa。
GUIDE
}

install_gaia() {
  echo ""; echo "== ★ GAIA（多步推理+联网搜索）· 免 Docker =="
  _clone https://github.com/aymeric-roucher/GAIA.git GAIA
  f=$(find "$BENCH_HOME/GAIA" -name metadata.jsonl -print -quit 2>/dev/null)
  [ -n "$f" ] && echo "-- GAIA 数据就绪：$f（165 题，其中 127 题无附件）" || echo "!! 没找到 GAIA 数据"
  echo "-- 提醒：GAIA 必须联网搜索。请确认后端已配 Serper："
  echo "     export HASHMM_SERPER_API_KEY=<你的key>"
  echo "     export HASHMM_SEARCH_BACKEND=serper"
}

install_kotlin() {
  echo ""; echo "== ★ Kotlin 编码（真编译真跑）· 免 Docker =="
  # 数据：HumanEval-Kotlin 161 题
  _clone https://github.com/amazon-science/mxeval.git mxeval data
  # JDK：优先用系统已有的 java（AutoDL/conda 常自带）；没有再下载独立 JDK
  if ls "$BENCH_HOME"/jdk*/bin/java >/dev/null 2>&1; then
    echo "-- 已有独立 JDK，跳过"
  elif command -v java >/dev/null 2>&1; then
    echo "-- 检测到系统 java（$(java -version 2>&1 | sed -n '1p')），直接用，不下载"
  else
    echo "-- 下载独立 JDK 21（多镜像，带超时；只放在 $BENCH_HOME）"
    JDK_URLS=(
      "https://mirrors.tuna.tsinghua.edu.cn/Adoptium/21/jdk/x64/linux/OpenJDK21U-jdk_x64_linux_hotspot_21.0.5_11.tar.gz"
      "https://mirror.bjtu.edu.cn/adoptium/21/jdk/x64/linux/OpenJDK21U-jdk_x64_linux_hotspot_21.0.5_11.tar.gz"
      "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.5%2B11/OpenJDK21U-jdk_x64_linux_hotspot_21.0.5_11.tar.gz"
    )
    ok=0
    for u in "${JDK_URLS[@]}"; do
      echo "   试 $u"
      if curl -fL --connect-timeout 15 --max-time 300 -o "$BENCH_HOME/jdk.tar.gz" "$u" 2>/dev/null; then
        tar -xzf "$BENCH_HOME/jdk.tar.gz" -C "$BENCH_HOME" && rm -f "$BENCH_HOME/jdk.tar.gz" && ok=1 && break
      fi
      rm -f "$BENCH_HOME/jdk.tar.gz"
    done
    [ "$ok" = "1" ] && echo "-- JDK 就绪" || echo "!! JDK 三个镜像都失败——可 apt/conda 装 java，或跳过 Kotlin 基准"
  fi
  # kotlinc（多镜像 + 超时）
  if [ ! -x "$BENCH_HOME/kotlinc/bin/kotlinc" ]; then
    echo "-- 下载 kotlinc（带超时）"
    KC_URLS=(
      "https://github.com/JetBrains/kotlin/releases/download/v2.0.21/kotlin-compiler-2.0.21.zip"
      "https://ghproxy.net/https://github.com/JetBrains/kotlin/releases/download/v2.0.21/kotlin-compiler-2.0.21.zip"
    )
    ok=0
    for u in "${KC_URLS[@]}"; do
      echo "   试 $u"
      if curl -fL --connect-timeout 15 --max-time 300 -o "$BENCH_HOME/kc.zip" "$u" 2>/dev/null; then
        unzip -q -o "$BENCH_HOME/kc.zip" -d "$BENCH_HOME" && rm -f "$BENCH_HOME/kc.zip" \
          && chmod +x "$BENCH_HOME/kotlinc/bin/"* && ok=1 && break
      fi
      rm -f "$BENCH_HOME/kc.zip"
    done
    [ "$ok" = "1" ] && echo "-- kotlinc 就绪" || echo "!! kotlinc 下载失败（可稍后重试）"
  fi
}

install_webvoyager() {
  echo ""; echo "== WebVoyager（真实网页任务）=="
  _clone https://github.com/MinorJerry/WebVoyager.git WebVoyager
  echo "-- 需要 Serper key（同 GAIA）。判分用 LLM 裁判，与官方 GPT-4V 口径不同。"
}

install_agentbench() {
  echo ""; echo "== AgentBench（OS 任务）=="
  _clone https://github.com/THUDM/AgentBench.git AgentBench data/os_interaction
  cat <<'MSG'
!! 注意：AgentBench-OS 会在【你的服务器上真实执行 shell】（官方是用 Docker 隔离的）。
!! 因此**默认关闭**。确认可接受后，给后端进程加：
!!     export HASHMM_AGENTBENCH_ALLOW_LOCAL=1
!! 已内置危险命令过滤（rm -rf / / apt / useradd / systemctl / mkfs 等任务一律跳过）。
MSG
}

install_swebench_pro() {
  echo ""; echo "== SWE-bench Pro（抗污染·2026 编码指标）=="
  V="$BENCH_HOME/swebench-venv"
  _ensure_venv "$V" || return 1
  # ★ V329：同 swebench 的吞错拆除——pip 失败必须红（此前 || true 一路绿）。
  "$V/bin/python" -m pip install -q --upgrade pip datasets pyarrow huggingface_hub pandas tqdm docker \
    || { echo "!! SWE-bench Pro 下载/官方 Docker evaluator 依赖安装失败"; return 1; }
  _clone https://github.com/scaleapi/SWE-bench_Pro-os.git swebench-pro-eval || return 1
  # V329 生成的缓存只保留了 Verified 的大写字段。源字段已丢失，无法本地修补；仅这一次重下，
  # 新 JSONL 与 evaluator 随 BENCH_HOME 一起进入 Actions cache，后续命中便不再下载。
  if [ -s "$BENCH_HOME/swebench_pro.jsonl" ] \
     && ! _swebench_pro_schema_ok "$BENCH_HOME/swebench_pro.jsonl"; then
    echo "-- 检测到旧版 SWE-bench Pro 缓存缺官方小写/脚本字段；删除该坏文件并重新下载一次"
    rm -f "$BENCH_HOME/swebench_pro.jsonl"
  fi
  if [ -s "$BENCH_HOME/swebench_pro.jsonl" ]; then
    echo "-- 数据集已存在（$(wc -l < "$BENCH_HOME/swebench_pro.jsonl") 条），跳过下载（缓存命中）"
  else
    FETCH="$(_fetcher)"
    if [ -n "$FETCH" ]; then
      "$V/bin/python" "$FETCH" swebench_pro --out "$BENCH_HOME" \
        || { echo "!! swebench_pro 数据集【全部下载源】失败（上面有逐源尝试报告；配 HF_TOKEN 可解限流）"; return 1; }
    else
      export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
      echo "-- （未找到 fetch_datasets.py，退回单源下载 HF_ENDPOINT=$HF_ENDPOINT）"
      HASHMM_BENCH_HOME="$BENCH_HOME" "$V/bin/python" - <<'PYIN' || { echo "!! swebench_pro 数据集下载失败（看上面的具体异常）"; return 1; }
import json, os, sys
out = os.path.join(os.environ["HASHMM_BENCH_HOME"], "swebench_pro.jsonl")
K = ("instance_id","repo","base_commit","problem_statement","patch","test_patch",
     "requirements","interface","repo_language","fail_to_pass","pass_to_pass",
     "before_repo_set_cmd","selected_test_files_to_run","dockerhub_tag",
     "issue_specificity","issue_categories")
last = None
for name in ("ScaleAI/SWE-bench_Pro", "scaleapi/SWE-bench_Pro"):
    try:
        from datasets import load_dataset
        ds = load_dataset(name, split="test")
        with open(out, "w", encoding="utf-8") as f:
            for r in ds:
                f.write(json.dumps({k: r.get(k) for k in K if k in r}, ensure_ascii=False) + "\n")
        print(f"OK 写出 {out}（{len(ds)} 题，源 {name}）"); break
    except Exception as e:
        last = e
else:
    print(f"!! 拉取失败：{last!r}。可手工把公开 split 转成 jsonl 放到 {out}"); sys.exit(1)
PYIN
    fi
  fi
  [ -s "$BENCH_HOME/swebench_pro.jsonl" ] || { echo "!! 产物自检失败：swebench_pro.jsonl 不存在或为空"; return 1; }
  _swebench_pro_schema_ok "$BENCH_HOME/swebench_pro.jsonl" \
    || { echo "!! 产物自检失败：swebench_pro.jsonl 缺官方 evaluator 必需字段"; return 1; }
  [ -f "$BENCH_HOME/swebench-pro-eval/swe_bench_pro_eval.py" ] \
    && [ -d "$BENCH_HOME/swebench-pro-eval/run_scripts" ] \
    && [ -d "$BENCH_HOME/swebench-pro-eval/dockerfiles/base_dockerfile" ] \
    && [ -d "$BENCH_HOME/swebench-pro-eval/dockerfiles/instance_dockerfile" ] \
    && [ -f "$BENCH_HOME/swebench-pro-eval/helper_code/image_uri.py" ] \
    || { echo "!! 产物自检失败：SWE-bench Pro 官方 evaluator/run_scripts/dockerfiles 不完整"; return 1; }
}

install_terminal2() {
  echo ""; echo "== Terminal-Bench 2.x（2026 大厂对标口径）=="
  _clone https://github.com/laude-institute/terminal-bench.git terminal-bench-src
  SRC=""
  for c in "$BENCH_HOME/terminal-bench-src/tasks" "$BENCH_HOME/terminal-bench-src/terminal_bench/tasks"; do
    [ -d "$c" ] && SRC="$c" && break
  done
  if [ -n "$SRC" ]; then
    mkdir -p "$BENCH_HOME/terminal-bench-2"
    rm -rf "$BENCH_HOME/terminal-bench-2/tasks"
    cp -r "$SRC" "$BENCH_HOME/terminal-bench-2/tasks"
    N=$(ls "$BENCH_HOME/terminal-bench-2/tasks" | wc -l)
    echo "OK terminal-bench-2/tasks（$N 个任务；跑分时 HASHMM_TERMINAL_SET=auto 会优先用 2.x）"
  else
    echo "未在官方仓库里找到 tasks 目录——目录结构可能已变，把任务集手工放到 $BENCH_HOME/terminal-bench-2/tasks/"
  fi
}

install_osworld() {
  echo ""; echo "== OSWorld（任务集；桌面环境需另配 VM/Docker 宿主）=="
  _clone https://github.com/xlang-ai/OSWorld.git osworld-src
  local copied=""
  if [ -d "$BENCH_HOME/osworld-src" ]; then
    mkdir -p "$BENCH_HOME/osworld"
    for c in evaluation_examples examples; do
      if [ -d "$BENCH_HOME/osworld-src/$c" ]; then
        cp -r "$BENCH_HOME/osworld-src/$c" "$BENCH_HOME/osworld/"
        copied="$c"
        break
      fi
    done
  fi
  [ -n "$copied" ] || { echo "!! OSWorld 仓库里没有找到 evaluation_examples/examples 任务目录"; return 1; }
  echo "OK 任务集就位（$copied）；桌面环境按 osworld.py 的 SETUP_GUIDE 托管后 export HASHMM_OSWORLD_VM=<host:port>"
}

install_swebench_preload() {
  # ★ V316 零网络方案：把本机友好的仓库预 clone 到镜像目录，SWE-bench Verified/Pro
  # 跑分时直接 --local 秒开、完全不依赖网络。适合 AutoDL 直连 github 不稳的场景。
  # 用法：在【有网】机器上跑 `bash install.sh swebench_preload`，把生成的
  # $BENCH_HOME/swebench-repos-preload 整个拷到 AutoDL 的同路径即可。
  echo ""; echo "== 预 clone SWE-bench 本机友好仓库（零网络方案）=="
  DST="$BENCH_HOME/swebench-repos-preload"; mkdir -p "$DST"
  REPOS="psf/requests pallets/flask pallets/click marshmallow-code/marshmallow pytest-dev/pytest pylint-dev/pylint sphinx-doc/sphinx PyCQA/flake8 sympy/sympy pvlib/pvlib-python"
  for R in $REPOS; do
    NAME=$(echo "$R" | tr '/' '_' | sed 's/_/__/')
    OWNER_REPO=$(echo "$R" | sed 's|/|__|')
    TARGET="$DST/$OWNER_REPO"
    if [ -d "$TARGET" ]; then echo "  ✓ $OWNER_REPO 已存在"; continue; fi
    echo "  -- clone $R ..."
    for PRE in "${HASHMM_GIT_MIRRORS%/}/" "https://ghfast.top/" "https://gh-proxy.com/" ""; do
      if git clone --bare "${PRE}https://github.com/$R.git" "$TARGET" 2>/dev/null; then
        echo "     ✓ $OWNER_REPO（源：${PRE:-github直连}）"; break
      fi
      rm -rf "$TARGET"
    done
    [ -d "$TARGET" ] || echo "     ✗ $OWNER_REPO 全部源失败，跳过"
  done
  echo "-- 预置完成：$DST（把这个目录拷到 AutoDL 同路径，SWE 跑分即零网络）"
}

install_swebench() {
  echo ""; echo "== SWE-bench Verified =="
  V="$BENCH_HOME/swebench-venv"           # V325：此前漏设 $V，靠全局残留——单独跑必坏，已修
  _ensure_venv "$V" || return 1
  "$V/bin/python" -m pip install -q --upgrade pip 2>/dev/null || true
  # ★ V328 修「CI 数据没下载下来但全绿」（用户真实踩坑，41s'完成'实为早败被吞）：
  #   ① datasets 显式安装——此前赌 SWE-bench harness 的传递依赖：harness 一装失败，
  #      下载段就 No module named 'datasets'，又被 || true 吞成绿色。
  "$V/bin/python" -m pip install -q datasets pyarrow huggingface_hub || { echo "!! datasets/pyarrow/huggingface_hub 安装失败（数据集下载的硬前提）"; return 1; }
  _clone https://github.com/princeton-nlp/SWE-bench.git SWE-bench || return 1
  #   ② harness 安装失败不再静默（去掉 2>/dev/null）：数据集下载不依赖它，降级为可见警告。
  "$V/bin/python" -m pip install -q -e "$BENCH_HOME/SWE-bench" \
    || echo "!! SWE-bench harness 安装失败（官方评测/本机venv模式会受影响；数据集下载不受影响，继续）"
  # ★ V329：下载升级为【多源回退】fetch_datasets.py（官方→镜像→urllib直连 × HF_TOKEN × 校验 × 原子写），
  #   任一条路通即成；已有非空文件直接跳过（Actions 缓存命中就走这，"一次成功、终身不再下载"）。
  if [ -s "$BENCH_HOME/swebench_verified.jsonl" ]; then
    echo "-- 数据集已存在（$(wc -l < "$BENCH_HOME/swebench_verified.jsonl") 条），跳过下载（缓存命中）"
  else
    FETCH="$(_fetcher)"
    if [ -n "$FETCH" ]; then
      "$V/bin/python" "$FETCH" swebench --out "$BENCH_HOME" \
        || { echo "!! swebench 数据集【全部下载源】失败（上面有逐源尝试报告；配 HF_TOKEN 可解限流）"; return 1; }
    else
      # 兜底：单独拷走 install.sh 没带 scripts/ 时，退回 V328 严格单源 heredoc。
      export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
      echo "-- （未找到 fetch_datasets.py，退回单源下载 HF_ENDPOINT=$HF_ENDPOINT）"
      HASHMM_BENCH_HOME="$BENCH_HOME" "$V/bin/python" - <<'PYIN' || { echo "!! swebench 数据集下载失败（看上面的具体异常）"; return 1; }
import json, os, sys
out = os.path.join(os.environ["HASHMM_BENCH_HOME"], "swebench_verified.jsonl")
K = ("instance_id","repo","base_commit","problem_statement","patch","test_patch","FAIL_TO_PASS","PASS_TO_PASS")
try:
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    with open(out, "w", encoding="utf-8") as f:
        for r in ds:
            f.write(json.dumps({k: r[k] for k in K if k in r}, ensure_ascii=False) + "\n")
    print(f"-- 数据集已写入 {out}（{len(ds)} 条）")
except Exception as e:
    print("!! 拉取失败：", repr(e)); sys.exit(1)
PYIN
    fi
  fi
  #   ④ 产物自检：文件必须存在且非空——终结「绿色但没数据」。
  [ -s "$BENCH_HOME/swebench_verified.jsonl" ] || { echo "!! 产物自检失败：swebench_verified.jsonl 不存在或为空"; return 1; }
  if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "!! 没有 Docker → 会走【本机 venv 模式】（非官方口径；装不上依赖的实例会如实从分母剔除）"
  fi
}

install_terminal() {
  echo ""; echo "== Terminal-bench（必须有 Docker）=="
  if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "!! 你的机器没有 Docker → Terminal-bench 无法评测（每个任务都在容器里跑）。跳过安装。"
    return 0
  fi
  # terminal-bench 0.2.18+ 的 pyproject.toml 明确要求 Python >=3.12。
  # 先做确定性检查，避免让 pip 跑到一半才给出不易定位的 requires-python 错误。
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo "!! terminal-bench 需要 Python >=3.12；当前是 $(python3 --version 2>&1)。请切换 Python 后重试。"
    return 1
  fi
  _clone https://github.com/laude-institute/terminal-bench.git terminal-bench || return 1
  V="$BENCH_HOME/tb-venv"
  # Actions 缓存或 BENCH_HOME 可能残留旧版 Python 创建的可运行 venv；它虽然没损坏，
  # 但仍不满足 terminal-bench 的版本下限，必须主动重建，不能只靠 _ensure_venv 的损坏检查。
  if [ -x "$V/bin/python" ] \
     && ! "$V/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo "-- tb-venv 的 Python 低于 3.12，删除并用当前 Python 重建：$V"
    rm -rf "$V"
  fi
  _ensure_venv "$V" || return 1
    # 固定 1.x 官方 harness 版本，和下游 terminal-bench-core==0.1.1 配套。
    # 旧版从 GitHub head editable 安装：缓存重建日期不同就可能静默换代码/结果结构，无法复现。
    if ! "$V/bin/python" -c '
import importlib.metadata as m, json
d=m.distribution("terminal-bench")
assert d.version == "0.2.18"
raw=d.read_text("direct_url.json")
assert not raw or not json.loads(raw).get("dir_info",{}).get("editable",False)
' 2>/dev/null; then
      "$V/bin/python" -m pip uninstall -y -q terminal-bench terminal_bench >/dev/null 2>&1 || true
      "$V/bin/python" -m pip install -q "terminal-bench==0.2.18" \
        || { echo "!! terminal-bench==0.2.18 安装失败（tb-venv 是运行硬前提）"; return 1; }
    fi
  # 产物自检：用 pip 元数据说话（别再「绿色但空壳」）。
  "$V/bin/python" -m pip show terminal-bench >/dev/null 2>&1 \
    || "$V/bin/python" -m pip show terminal_bench >/dev/null 2>&1 \
    || { echo "!! 产物自检失败：tb-venv 里没有 terminal-bench 包"; return 1; }
  "$V/bin/python" -c "from terminal_bench.cli.tb.main import app" 2>/dev/null \
    || { echo "!! 产物自检失败：Terminal-bench CLI 入口不可导入"; return 1; }
}

for T in "${TARGETS[@]}"; do
  case "$T" in
    nodocker) install_humaneval; install_bfcl; install_tau2; install_gaia; install_kotlin; install_webvoyager ;;
    humaneval|mbpp) install_humaneval ;;
    bfcl) install_bfcl ;;      tau2) install_tau2 ;;        gaia) install_gaia ;;
    kotlin) install_kotlin ;;  webvoyager) install_webvoyager ;;
    swebench) install_swebench ;; terminal) install_terminal ;; agentbench) install_agentbench ;;
    swebench_pro) install_swebench_pro ;; terminal2) install_terminal2 ;; osworld) install_osworld ;;
    swebench_preload) install_swebench_preload ;;
    webarena) install_webarena ;;
    # 全部数据集：含需 Docker 才能【运行】的那几个——但【数据集下载】在你服务器上就能做，
    # 下好后配合免费 CI（见 benchmarks/README_CI.md）跑 Docker 部分、结果回传后端。
    all) install_humaneval; install_bfcl; install_tau2; install_gaia; install_kotlin; install_webvoyager
         install_swebench; install_swebench_pro; install_agentbench
         install_terminal; install_terminal2; install_webarena; install_osworld ;;
    *) echo "!! 未知目标：$T"
       echo "   可选：nodocker（免Docker能跑的4个）| all（全部数据集）| 或单个："
       echo "   bfcl tau2 gaia kotlin webvoyager humaneval swebench swebench_pro terminal terminal2 webarena osworld agentbench" ;;
  esac
done

# ── 装完只自检【本次请求的目标】。旧版无条件检查全部 13 项，把未请求的项目也打印成
# 「没装上」，导致 CI 明明已装好 SWE/Terminal/WebArena/OSWorld 却看起来全失败。
_first_file() {  # _first_file <root> <find predicates...>；不用 head，避免 pipefail/SIGPIPE
  local root="$1"; shift
  [ -d "$root" ] || return 0
  find "$root" "$@" -print -quit 2>/dev/null
}

_line_count() {
  [ -f "$1" ] && wc -l < "$1" || echo 0
}

_check_target() {
  case "$1" in
    humaneval)
      [ -n "$(_first_file "$BENCH_HOME/human-eval" -name 'HumanEval.jsonl*')" ] \
        && [ -n "$(_first_file "$BENCH_HOME/google-research" -name 'mbpp.jsonl')" ] ;;
    bfcl) [ -n "$(_first_file "$BENCH_HOME/gorilla" -path '*data*' -name '*simple*.json')" ] ;;
    tau2)
      [ -d "$BENCH_HOME/tau-bench" ] \
        && "$BENCH_HOME/tau2-venv/bin/python" -c "import fastapi, litellm" 2>/dev/null ;;
    gaia) [ -n "$(_first_file "$BENCH_HOME/GAIA" -name metadata.jsonl)" ] ;;
    kotlin)
      [ -n "$(_first_file "$BENCH_HOME/mxeval" -name 'HumanEval_kotlin*.jsonl')" ] \
        && [ -x "$BENCH_HOME/kotlinc/bin/kotlinc" ] \
        && ( ls "$BENCH_HOME"/jdk*/bin/java >/dev/null 2>&1 || command -v java >/dev/null 2>&1 ) ;;
    webvoyager) [ -n "$(_first_file "$BENCH_HOME/WebVoyager" -name '*.jsonl')" ] ;;
    swebench)
      [ -s "$BENCH_HOME/swebench_verified.jsonl" ] && [ -d "$BENCH_HOME/SWE-bench" ] \
        && "$BENCH_HOME/swebench-venv/bin/python" -c "import swebench" 2>/dev/null ;;
    swebench_pro)
      _swebench_pro_schema_ok "$BENCH_HOME/swebench_pro.jsonl" \
        && [ -f "$BENCH_HOME/swebench-pro-eval/swe_bench_pro_eval.py" ] \
        && [ -d "$BENCH_HOME/swebench-pro-eval/run_scripts" ] \
        && [ -d "$BENCH_HOME/swebench-pro-eval/dockerfiles/base_dockerfile" ] \
        && [ -d "$BENCH_HOME/swebench-pro-eval/dockerfiles/instance_dockerfile" ] \
        && [ -f "$BENCH_HOME/swebench-pro-eval/helper_code/image_uri.py" ] \
        && "$BENCH_HOME/swebench-venv/bin/python" -c "import pandas, docker" 2>/dev/null ;;
    terminal)
      [ -d "$BENCH_HOME/terminal-bench" ] \
        && "$BENCH_HOME/tb-venv/bin/python" \
          -c "from terminal_bench.cli.tb.main import app" 2>/dev/null ;;
    terminal2)
      [ -n "$(_first_file "$BENCH_HOME/terminal-bench-2/tasks" \( -name task.yaml -o -name task.toml \))" ] ;;
    webarena)
      [ "$(python -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); print(len(d) if isinstance(d,list) else 0)' "$BENCH_HOME/webarena/config_files/test.raw.json" 2>/dev/null || echo 0)" = "812" ] \
        && PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$BENCH_HOME/ms-playwright}" \
           python -c 'from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); x=Path(p.chromium.executable_path); p.stop(); assert x.is_file()' 2>/dev/null ;;
    osworld)
      [ -d "$BENCH_HOME/osworld-src" ] \
        && { [ -d "$BENCH_HOME/osworld/evaluation_examples" ] || [ -d "$BENCH_HOME/osworld/examples" ]; } ;;
    agentbench)
      [ -n "$(_first_file "$BENCH_HOME/AgentBench" -path '*os_interaction*' -name '*.json')" ] ;;
    swebench_preload) [ -n "$(_first_file "$BENCH_HOME/swebench-repos-preload" -mindepth 1 -maxdepth 1)" ] ;;
    *) return 1 ;;
  esac
}

_target_label() {
  case "$1" in
    swebench) echo "SWE-bench Verified（$(_line_count "$BENCH_HOME/swebench_verified.jsonl") 条 + harness）" ;;
    swebench_pro) echo "SWE-bench Pro（$(_line_count "$BENCH_HOME/swebench_pro.jsonl") 条）" ;;
    terminal) echo "Terminal-bench（仓库 + Python harness）" ;;
    webarena) echo "WebArena（$(find "$BENCH_HOME/webarena/config_files" -name '*.json' 2>/dev/null | wc -l) 个配置文件）" ;;
    osworld) echo "OSWorld（仓库 + 任务目录）" ;;
    *) echo "$1" ;;
  esac
}

CHECK_TARGETS=()
for T in "${TARGETS[@]}"; do
  case "$T" in
    nodocker) CHECK_TARGETS+=(humaneval bfcl tau2 gaia kotlin webvoyager) ;;
    all) CHECK_TARGETS+=(humaneval bfcl tau2 gaia kotlin webvoyager swebench swebench_pro agentbench terminal terminal2 webarena osworld) ;;
    humaneval|mbpp) CHECK_TARGETS+=(humaneval) ;;
    *) CHECK_TARGETS+=("$T") ;;
  esac
done

echo ""; echo "════════════ 本次请求的安装结果自检 ════════════"
CHECK_FAILED=0
for T in "${CHECK_TARGETS[@]}"; do
  if _check_target "$T"; then
    echo "  ✓ $(_target_label "$T")"
  else
    echo "  ✗ $(_target_label "$T")  ← 本次请求未安装完整"
    CHECK_FAILED=1
  fi
done
[ "$CHECK_FAILED" -eq 0 ] || { echo "!! 至少一个本次请求的基准未通过产物自检"; exit 1; }

cat <<EOF

════════════════════ 完成 ════════════════════
把下面这些加进【启动后端的脚本】(start-hashmm.sh)，然后**重启后端**：

  export HASHMM_BENCH_HOME=$BENCH_HOME
  export HASHMM_BENCH_MODE=full
  export HASHMM_SERPER_API_KEY=<你的 Serper key>     # GAIA/WebVoyager 要联网搜索
  export HASHMM_SEARCH_BACKEND=serper
  # 样本量：默认就跑 standard(50 题)——和大厂一个口径，**默认可比，无需任何配置**。
  #   想省 token 先做快速自检（分数不可比，报告会明说）： export HASHMM_BENCH_SAMPLE=quick
  #   想完全对齐大厂发布会口径（官方全集，最贵）：       export HASHMM_BENCH_SAMPLE=full
  # 注意：别再设 HASHMM_TAU2_LIMIT / HASHMM_GAIA_LIMIT 等低于 50 的值——那会把分数压到
  #       不可比档（这是旧版为省 token 的默认，现已取消）。

然后：测试中枢 →「外部基准对标」→ 勾★开头的 4 个（免 Docker，能出真分）→ 运行。
EOF
