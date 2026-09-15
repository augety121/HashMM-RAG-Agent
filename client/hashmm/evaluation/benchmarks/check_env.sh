#!/usr/bin/env bash
# HashMM 基准环境自检 —— 在你的 AutoDL 服务器上跑，一眼看清每个基准能不能跑。
# 用法：bash check_env.sh
echo "════════════════ HashMM 基准环境自检 ════════════════"
# 路径优先级：环境变量 > /root/autodl-tmp(AutoDL 持久盘) > $HOME
if [ -n "$HASHMM_BENCH_HOME" ]; then BENCH_HOME="$HASHMM_BENCH_HOME"
elif [ -d /root/autodl-tmp ]; then BENCH_HOME="/root/autodl-tmp/hashmm-benchmarks"
else BENCH_HOME="$HOME/hashmm-benchmarks"; fi

echo ""; echo "【1】Docker 能力检测"
if command -v docker >/dev/null 2>&1; then
  echo "  ✓ docker 命令存在：$(command -v docker)"
  if docker info >/dev/null 2>&1; then
    echo "  ✓ docker daemon 可用 —— SWE-bench/Terminal-bench 官方模式可跑！"
    DOCKER_OK=1
  else
    echo "  ✗ docker 命令在，但 daemon 连不上（docker info 失败）"
    echo "    → 你的容器里装了 docker 客户端，但没有可用的 docker daemon。"
    echo "    → AutoDL 容器要用 Docker，需要：容器启动时挂载宿主 /var/run/docker.sock，"
    echo "       或该实例支持 dind(docker-in-docker)。多数 AutoDL 算力实例默认不支持。"
    DOCKER_OK=0
  fi
else
  echo "  ✗ 没有 docker 命令 —— 你的容器里根本没装 docker。"
  echo "    → 这不是 HashMM 的限制。SWE-bench/Terminal-bench 官方评测【每个任务都在独立 Docker"
  echo "       容器里跑】，是基准本身的设计。容器套容器(dind)需要特权模式，AutoDL 算力实例通常不给。"
  echo "    → 但别急：下面这些基准【不需要 Docker】，你照样能跑出真实分数。"
  DOCKER_OK=0
fi

echo ""; echo "【2】免 Docker 基准 —— 数据/工具就绪情况"
chk() { [ -e "$1" ] && echo "  ✓ $2" || echo "  ✗ $2（未就绪：$3）"; }
chk "$(find "$BENCH_HOME/gorilla" -path '*data*' -name '*simple*.json' 2>/dev/null | head -1)" \
    "BFCL 工具调用（官方数据）" "bash install.sh bfcl"
chk "$BENCH_HOME/tau-bench/run.py" "τ²-bench（官方 harness，纯 Python）" "bash install.sh tau2"
chk "$(find "$BENCH_HOME/GAIA" -name metadata.jsonl 2>/dev/null | head -1)" \
    "GAIA（多步推理，需 Serper）" "bash install.sh gaia"
chk "$(find "$BENCH_HOME/mxeval" -name 'HumanEval_kotlin*.jsonl' 2>/dev/null | head -1)" \
    "Kotlin 数据集" "bash install.sh kotlin"
if ls "$BENCH_HOME"/jdk*/bin/java >/dev/null 2>&1 || command -v java >/dev/null 2>&1; then
  echo "  ✓ JDK（Kotlin 编译需要）"
else echo "  ✗ JDK（未就绪：bash install.sh kotlin 会自动下载到隔离目录）"; fi
[ -x "$BENCH_HOME/kotlinc/bin/kotlinc" ] && echo "  ✓ kotlinc" || echo "  ✗ kotlinc（未就绪：bash install.sh kotlin）"
chk "$(find "$BENCH_HOME/WebVoyager" -name '*.jsonl' 2>/dev/null | head -1)" \
    "WebVoyager（需 Serper）" "bash install.sh webvoyager"

echo ""; echo "【3】网络连通性（装数据要用）"
for host in github.com google.serper.dev hf-mirror.com; do
  if timeout 8 bash -c "echo > /dev/tcp/$host/443" 2>/dev/null; then echo "  ✓ $host:443 可达"
  else echo "  ✗ $host:443 不通"; fi
done

echo ""; echo "【4】搜索后端（GAIA/WebVoyager 必须）"
if [ -n "$HASHMM_SERPER_API_KEY" ]; then echo "  ✓ HASHMM_SERPER_API_KEY 已设置（长度 ${#HASHMM_SERPER_API_KEY}）"
else echo "  ✗ HASHMM_SERPER_API_KEY 未设置 —— GAIA/WebVoyager 无法联网搜索"; fi

echo ""; echo "【5】浏览器内核（V309 browser_open/act/read/screenshot；对标 Claude Code 内置浏览器）"
python3 - <<'PYEOF'
try:
    import playwright  # noqa
    print("  ✓ playwright 已安装")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_page()
            pg.set_content("<h1 id='t'>ok</h1>")
            assert pg.inner_text("#t") == "ok"
            b.close()
        print("  ✓ Chromium 可启动并渲染 —— 浏览器内核走【chromium 引擎】(JS/点击/填表/截图全可用)")
    except Exception as e:
        print(f"  ✗ Chromium 起不来：{type(e).__name__}: {str(e)[:120]}")
        print("    → 跑一次：playwright install chromium   （下载浏览器二进制）")
        print("    → 缺系统库时：playwright install-deps  （需要 apt/root）")
        print("    → 装不上也没关系：浏览器内核会自动降级【lite 引擎】(静态抓取+链接跳转仍可用)")
except ImportError:
    print("  ✗ 未安装 playwright —— 浏览器内核当前走【lite 引擎】(纯 stdlib，能开页读文点链接)")
    print("    → 想要完整能力(JS渲染/填表/截图)：pip install playwright && playwright install chromium")
PYEOF

echo ""; echo "════════════════ 结论 ════════════════"
if [ "$DOCKER_OK" = "1" ]; then
  echo "你的机器【支持 Docker】→ 9 个基准全部可跑（含 SWE-bench/Terminal-bench 官方模式）。"
else
  echo "你的机器【不支持 Docker】→ 这 4 个 ★ 基准照样能跑出真实可对标分数："
  echo "   BFCL、τ²-bench、GAIA、Kotlin"
  echo "SWE-bench 会自动走【本机 venv 模式】(非官方口径)；Terminal-bench/WebArena 需要 Docker，会如实跳过。"
  echo ""
  echo "想在 AutoDL 上用 Docker？两条路："
  echo "  A) 开 AutoDL 时选支持 dind 的镜像/实例（不是所有实例都给特权模式）"
  echo "  B) 换一台自己的服务器/云主机（有 root + Docker）跑 SWE-bench 官方 harness"
fi
