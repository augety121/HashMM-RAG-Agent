"""顶尖 agent 在各基准上的公开分数（用于对标）。

数据来自各基准官方 leaderboard / 供应商技术报告的公开数字，随版本会变，仅作【量级参照】——
不是精确复现值。用户跑出自己的分后，中枢会显示"你 vs 这些参照"。请以官方最新公布为准。
"""

# 每个基准：一组"名字→分数(%)"的公开参照 + 计量单位说明 + 来源备注
LEADERBOARD = {
    "humaneval": {
        "metric": "pass@1 (%)",
        "note": "Python 代码生成（HumanEval+MBPP）。真执行判分。顶尖模型 90%+，强模型 ~80%。",
        "refs": [
            ("顶尖模型", 92.0),
            ("强模型", 80.0),
            ("中等模型", 60.0),
        ],
    },
    "swebench_verified": {
        "metric": "Pass@1 (%)",
        "source_url": "https://www.swebench.com/",
        "note": "SWE-bench Verified 官方 500 题；以下是官方榜同一 mini-SWE-agent 2.0.0 "
                "脚手架的模型结果（截至 2026-07-16）。不同脚手架结果不可直接归因给模型。",
        "refs": [
            ("Claude 4.5 Opus (high reasoning)", 76.8),
            ("Gemini 3 Flash (high reasoning)", 75.8),
            ("DeepSeek V3.2 (high reasoning)", 70.0),
            ("Gemini 3 Pro", 69.6),
        ],
    },
    "swebench_pro": {
        "metric": "Pass@1 (%)",
        "source_url": "https://labs.scale.com/api/pdf/leaderboard/swe_bench_pro_public",
        "note": "Scale SWE-bench Pro public 731 题；以下是官方 public leaderboard 的 Resolve Rate"
                "（截至 2026-07-16；必须使用 Pro 官方逐实例 Docker 评测口径）。",
        "refs": [
            ("Muse Spark 1.1", 61.5),
            ("GPT-5.4 (xHigh)", 59.1),
            ("Muse Spark", 55.0),
            ("Claude Opus 4.6 (thinking)", 51.9),
        ],
    },
    "terminal_bench": {
        "metric": "任务成功率 (%)",
        "source_url": "https://www.tbench.ai/leaderboard/terminal-bench/1.0",
        "note": "本 runner 固定使用官方 terminal-bench@1.0 的 terminal-bench-core==0.1.1；"
                "以下只引用同一 1.0 官方榜（截至 2026-07-16），不与 2.0/2.1 混比。",
        "refs": [
            ("Apex2 / Claude 4.5 Sonnet", 64.5),
            ("Chaterm / Claude 4.5 Sonnet", 63.7),
            ("Abacus AI Desktop / Multiple", 62.3),
            ("Ante / Claude Sonnet 4.5", 60.3),
        ],
    },
    "osworld": {
        "metric": "任务成功率 (%)",
        "note": "真实桌面 computer-use（369 题，官方 VM/Docker 桌面环境）。2026 生产决策六大基准之一。",
        "refs": [
            ("人类", 72.4),
            ("2025 顶尖 agent", 45.0),
            ("早期多模态基线", 12.2),
        ],
    },
    "tool_calling": {
        "metric": "调用准确率 (%)",
        "note": "函数调用：工具选择 + 参数填充 + 不该调时不调(幻觉检查)。对标 BFCL 口径。",
        "refs": [
            ("顶尖函数调用模型", 88.0),
            ("强模型", 80.0),
            ("一般模型", 65.0),
        ],
    },
    "tau2_bench": {
        "metric": "任务通过率 (%)",
        "note": "复杂多步交互 + 策略发现(如客服工单按策略处理)。对标 τ²-bench 口径。",
        "refs": [
            ("顶尖 agent", 60.0),
            ("强基线", 45.0),
            ("一般基线", 30.0),
        ],
    },
    "gaia": {
        "metric": "准确率 (%)",
        "note": "多步推理+联网搜索+工具使用。官方 validation。人类 ~92%，顶尖 agent 30-50%，早期 GPT-4+插件 ~15%。",
        "refs": [
            ("顶尖 agent(近期)", 50.0),
            ("强 agent", 33.0),
            ("GPT-4+插件(早期)", 15.0),
        ],
    },
    "webvoyager": {
        "metric": "任务成功率 (%)",
        "note": "真实网站任务。官方用 GPT-4V 看截图判分；我们用文本工具+LLM裁判，口径不同，不建议横向比。",
        "refs": [
            ("顶尖多模态 agent", 59.0),
            ("强基线", 40.0),
        ],
    },
    "agentbench_os": {
        "metric": "成功率 (%)",
        "note": "操作系统/shell 任务。官方在 Docker 里跑；本机执行口径略有差异。",
        "refs": [
            ("顶尖模型", 60.0),
            ("强模型", 40.0),
            ("一般模型", 20.0),
        ],
    },
    "kotlin_bench": {
        "metric": "通过率 (%)",
        "note": "Kotlin 生态真实软件工程任务(JetBrains 官方编码代理评估口径)。",
        "refs": [
            ("顶尖编码 agent", 65.0),
            ("强基线", 50.0),
        ],
    },
}


def compare(bench_id: str, score_pct: float) -> dict:
    """给定你的分数(%)，返回与参照的对比：高于/接近/低于哪些参照。"""
    lb = LEADERBOARD.get(bench_id)
    if not lb:
        return {"metric": "", "note": "", "refs": [], "your_score": score_pct, "ranking": ""}
    refs = sorted(lb["refs"], key=lambda x: x[1], reverse=True)
    beaten = [n for n, v in refs if score_pct >= v]
    ahead_of = beaten[-1] if beaten else None
    # 找到你处在哪两个参照之间
    above = [(n, v) for n, v in refs if v > score_pct]
    below = [(n, v) for n, v in refs if v <= score_pct]
    if not above:
        ranking = f"已达到/超过全部参照(最高 {refs[0][1]}%)"
    elif not below:
        ranking = f"低于全部参照(最低 {refs[-1][1]}%)，有较大提升空间"
    else:
        ranking = f"介于「{above[-1][0]} {above[-1][1]}%」与「{below[0][0]} {below[0][1]}%」之间"
    return {
        "metric": lb["metric"], "note": lb["note"], "refs": refs,
        "your_score": round(score_pct, 1), "ranking": ranking,
        "beats": [n for n, v in refs if score_pct >= v],
    }
