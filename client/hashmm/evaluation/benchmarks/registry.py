"""基准注册表 —— 测试中枢据此渲染「外部基准对标」分组的勾选项。

`no_docker=True` 表示：**在你这台没有 Docker 的 AutoDL 上也能跑出真实分数**。
"""

BENCHMARKS = [
    {"id": "lohosearch", "name": "LoHoSearch（长程知识图谱搜索）", "lb_key": "lohosearch",
     "kind": "search", "no_docker": True,
     "requires": "HASHMM_LOHOSEARCH_DATA + SHA-256 provenance manifest",
     "sut": "HashMM AgentLoop + governed search tools; exact-match scoring",
     "desc": "544题完整集仅在来源、快照和SHA-256均验证后可标为官方全量；20题 smoke 和50题 CI 只做纵向回归，不冒充排行榜分数。"},
    # ── 无需 Docker，能出真实可对标分数 ──────────────────────────────
    {"id": "humaneval", "name": "HumanEval+MBPP（Python 代码）", "lb_key": "humaneval",
     "kind": "code", "no_docker": True, "requires": "install.sh humaneval（纯 Python，零依赖）",
     "sut": "模型直答（业界 HumanEval 标准口径就是单轮生成——测底座模型的代码能力）",
     "desc": "★★ 最省事：纯 Python 真执行判 pass@1，**不需要 Docker 也不需要编译器**。HumanEval 164 题 + MBPP 974 题。"},
    {"id": "tool_calling", "name": "工具调用基准（BFCL）", "lb_key": "tool_calling",
     "kind": "tool", "no_docker": True, "requires": "install.sh bfcl",
     "sut": "模型单次工具决策（BFCL 官方口径——测底座模型的函数调用能力）",
     "desc": "★ 官方 BFCL 数据 + AST 判分。不需要 Docker。"},
    {"id": "tau2_bench", "name": "τ²-bench（多步交互+策略）", "lb_key": "tau2_bench",
     "kind": "interaction", "no_docker": True, "requires": "install.sh tau2",
     "sut": "官方 harness × 你的模型（τ² 官方口径：官方模拟环境驱动模型多步交互）",
     "desc": "★ 官方 tau-bench 是**纯 Python，不需要 Docker**。用官方 harness + 官方 reward 判分（retail 115 题 / airline 50 题）。"},
    {"id": "gaia", "name": "GAIA（多步推理+联网搜索）", "lb_key": "gaia",
     "kind": "general", "no_docker": True, "requires": "install.sh gaia + Serper key",
     "sut": "★你的Agent（真实 AgentLoop + web_search/fetch_url/execute_code 工具，多步）",
     "desc": "★ 官方 165 题，需要联网搜索+工具使用。官方 quasi-exact-match 判分（非 LLM 裁判）。"},
    {"id": "kotlin_bench", "name": "Kotlin 编码（HumanEval-Kotlin）", "lb_key": "kotlin_bench",
     "kind": "code", "no_docker": True, "requires": "install.sh kotlin（会下载隔离 JDK+kotlinc）",
     "sut": "模型直答（同 HumanEval 口径——测底座模型的 Kotlin 代码能力）",
     "desc": "★ 官方 161 题，**真 kotlinc 编译 + 真跑测试** 判 pass@1。不需要 Docker。"},
    {"id": "webvoyager", "name": "WebVoyager（真实网页任务）", "lb_key": "webvoyager",
     "kind": "web", "no_docker": True, "requires": "install.sh webvoyager + Serper key",
     "sut": "★你的Agent（文本工具浏览网页），判分为 LLM 裁判（非官方口径）",
     "desc": "官方任务 + 文本工具（搜索/抓页）。判分用 LLM 裁判，**与官方 GPT-4V 口径不同**，只作纵向参考。"},

    # ── 需要 Docker，或需要显式开关 ─────────────────────────────────
    {"id": "swebench_verified", "name": "SWE-bench Verified（代码修复）", "lb_key": "swebench_verified",
     "kind": "code", "no_docker": False, "requires": "install.sh swebench",
     "sut": "★你的Agent（真实 AgentLoop + read_file/str_replace/run_shell 改仓库出补丁）",
     "desc": "有 Docker → 官方 harness（官方口径）；没 Docker → **本机 venv 模式**（非官方口径，装不上依赖的实例会如实剔除）。"},
    {"id": "agentbench_os", "name": "AgentBench（操作系统任务）", "lb_key": "agentbench_os",
     "kind": "os", "no_docker": False, "requires": "install.sh agentbench + 显式开关",
     "sut": "★你的Agent（AgentLoop 执行 OS 任务）",
     "desc": "官方 OS 任务。会在**你的服务器上真实执行 shell**（官方用 Docker 隔离），"
             "故**默认关闭**，需设 HASHMM_AGENTBENCH_ALLOW_LOCAL=1；已内置危险命令过滤。"},
    {"id": "terminal_bench", "name": "Terminal-bench（终端任务）", "lb_key": "terminal_bench",
     "kind": "terminal", "no_docker": True, "requires": "install.sh terminal（本机模式不需要 Docker）",
     "sut": "★你的Agent（真实 AgentLoop 在工作区执行终端任务，官方 pytest 判分）",
     "desc": "有 Docker → 固定跑 terminal-bench-core==0.1.1（1.0）官方 harness；没 Docker "
             "可跑本机诊断模式，但环境不是官方容器，结果会明确标记为不可与排行榜横向比较。"},
    {"id": "swebench_pro", "name": "SWE-bench Pro（抗污染·2026 编码指标）", "lb_key": "swebench_pro",
     "kind": "code", "no_docker": False, "requires": "install.sh swebench_pro",
     "sut": "★你的Agent（与 Verified 同一套 AgentLoop 机制）",
     "desc": "Scale 官方 public split 731 题。生成补丁后使用官方 run_scripts、parser 和逐实例 "
             "Docker 镜像按 F2P/P2P 判分；Docker/evaluator 不完整时明确跳过，不使用本机 venv 冒充。"},
    {"id": "osworld", "name": "OSWorld（真实桌面 computer use）", "lb_key": "osworld",
     "kind": "os", "no_docker": False, "requires": "官方 VM/Docker 桌面环境（见运行时指引）",
     "sut": "★你的Agent（接入位；桌面 provider 接线后驱动）",
     "desc": "2026 生产决策六大基准之一：真实 Ubuntu 桌面 369 题。评测程序可跑在本机，"
             "桌面环境需 VM/Docker 宿主——未配置时给出确切接入指引（接入位已留好）。"},
    {"id": "mcp_atlas", "name": "MCP Atlas（MCP 工具调用）", "lb_key": "tool_calling",
     "kind": "tool", "no_docker": True, "requires": "官方数据集发布后 install（接入位已留）",
     "sut": "★你的Agent（MCP 工具面；等官方数据集）",
     "desc": "MCP 协议工具调用基准（2026 新晋）。本机已具备 MCP 工具面；数据集/判分脚本"
             "以官方发布为准，当前为接入位+能力预检，不产生可对标分数。"},
    {"id": "webarena", "name": "WebArena（网页沙箱）", "lb_key": "webvoyager",
     "kind": "web", "no_docker": True, "requires": "install.sh webarena + 配站点URL（托管站的机器才需 Docker，本机只要 Playwright）",
     "sut": "★你的Agent（浏览器内核真操作，官方判分口径）",
     "desc": "官方 812 题 + 官方判分口径（exact/must_include/url_match）。**你的 AutoDL 不需要 Docker** —— "
             "只有托管那 5 个站的机器需要（可用你自己有 Docker 的机器/云主机/官方 AWS AMI）。"
             "配好站点 URL（HASHMM_WEBARENA_SHOPPING 等）后用浏览器内核真跑真判分；没配则给出确切托管指引。"},
]

_BY_ID = {b["id"]: b for b in BENCHMARKS}


def get_benchmark(bench_id: str) -> dict | None:
    return _BY_ID.get(bench_id)
