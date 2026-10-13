"""hashmm/evaluation/benchmarks/vs_frontier.py —— 「你 vs 2026 大厂」横向对比（V316）。

用户的核心诉求：拿自己的分数和大厂做一张对比图。但对比有个前提——**分数必须可比**。
报告里 τ² "10/10=100%" 看着比 Opus 还高，其实只跑了 10 题（置信区间 72%~100%，宽
27.8 分），拿去和大厂全集比是自欺欺人。这个模块把「可比性」做成对比表的一等公民：

  · 每个基准取最近一次跑分 + 该次的样本量 → Wilson 置信区间 → 可比性判定
  · 够格的（standard/full 档，≥50 题）进「正式对比区」，直接和大厂参照并排
  · 不够格的（quick/smoke）进「仅供纵向趋势区」，明确标注"不能和大厂比"
  · 输出既有 markdown 表（人看），也有结构化 dict（前端画对比图/雷达图用）

诚实性原则贯穿始终：宁可少给一个能比的数字，也不给一个误导的对比。
"""
from __future__ import annotations

from .leaderboard import LEADERBOARD
from .sample_stats import MIN_COMPARABLE_N, comparability

__all__ = ["build_comparison", "render_markdown"]


# 官方榜锚点（截至 2026-07-16）。不同版本/脚手架不得混在同一横向比较里。
# 每个基准列出「谁在这个榜上有公开数字」，没有的不硬编（缺失≠0）。
_FRONTIER_ANCHORS: dict[str, list[tuple[str, float]]] = {
    "swebench_verified": [("Claude 4.5 Opus (high)", 76.8),
                          ("Gemini 3 Flash (high)", 75.8)],
    "swebench_pro": [("Muse Spark 1.1", 61.5), ("GPT-5.4 (xHigh)", 59.1),
                     ("Muse Spark", 55.0), ("Claude Opus 4.6 (thinking)", 51.9)],
    "terminal_bench": [("Apex2 / Claude 4.5 Sonnet", 64.5),
                       ("Chaterm / Claude 4.5 Sonnet", 63.7),
                       ("Abacus AI Desktop / Multiple", 62.3)],
    "gaia": [("系统级最佳(工具栈)", 92.4), ("Claude Sonnet 4.5(工具栈)", 74.6),
             ("顶尖 agent", 50.0)],
    "osworld": [("人类", 72.4), ("2025 顶尖 agent", 45.0)],
    "tau2_bench": [("顶尖 agent", 60.0)],
    "webvoyager": [("顶尖浏览 agent", 87.0)],
}

# selftest/registry 的 bench_id → leaderboard/anchors 的键。
_ID_TO_LBKEY = {
    "swebench": "swebench_verified",
    "swebench_pro": "swebench_pro",
    "terminal": "terminal_bench",
    "gaia": "gaia",
    "osworld": "osworld",
    "tau2": "tau2_bench",
    "webvoyager": "webvoyager",
    "webarena": "webvoyager",
    # V322：这三个也是官方口径、免 Docker、默认可比——纳入对比（之前漏了，跑了却不显示）
    "humaneval": "humaneval",
    "tool_calling": "tool_calling",
    "kotlin_bench": "kotlin_bench",
}


# ── 口径对齐表（V317）：逐条回答"我这个基准的评测口径和大厂一样吗" ──
# same=True 表示判分规则/数据集与官方一致（分数直接可比）；same=False 表示有差异
# （通常是环境差异，如无 Docker 走本机模式），note 说明差在哪、是否影响分数可比性。
_PARITY: dict[str, dict] = {
    "tau2": {"same": True, "dataset": "官方 tau-bench retail/airline",
             "judge": "官方 harness（reward==1 才算成功）",
             "note": "纯 Python 无需 Docker，判分口径与官方完全一致，分数直接可比。"},
    "gaia": {"same": True, "dataset": "官方 GAIA validation 165 题",
             "judge": "官方 quasi-exact-match（非 LLM 裁判）",
             "note": "判分口径与官方一致；需配 Serper 联网搜索 key，否则多步题必错。"},
    "swebench": {"same": True, "dataset": "官方 SWE-bench Verified 500 题",
                 "judge": "F2P+P2P 官方规则 + 反巧合通过（须改非测试源码）",
                 "note": "有 Docker 走官方 harness＝完全一致；无 Docker 走本机 venv，"
                         "判分规则不变但环境非官方容器，装不上的实例如实剔除（分母透明）。"},
    "swebench_pro": {"same": True, "dataset": "官方 SWE-bench Pro 公开 split 731 题",
                     "judge": "Scale 官方 run_scripts/parser + 逐实例 Docker 镜像 + F2P/P2P",
                     "note": "只在官方 Docker evaluator 完整产出结果时计分；商业私有集不在本地伪造。"},
    "terminal": {"same": True, "dataset": "terminal-bench-core==0.1.1（1.0，共 80 题）",
                 "judge": "Terminal-Bench 官方 Docker harness",
                 "note": "当前 Docker runner 固定对齐 Terminal-Bench 1.0；报告只与 1.0 官方榜比较，"
                         "不再把 2.0/2.1 的成绩混进来。"},
    "webvoyager": {"same": True, "dataset": "官方 WebVoyager 真实网站任务",
                   "judge": "LLM 裁判（官方口径）",
                   "note": "需真实浏览器（已内置 Chromium），判分口径与官方一致。"},
    "webarena": {"same": True, "dataset": "官方 WebArena 812 题",
                 "judge": "官方 string_match / url_match",
                 "note": "判分口径与官方一致；需自托管 5 个站点（托管机需 Docker，本机只跑评测）。"},
    "osworld": {"same": True, "dataset": "官方 OSWorld 369 题",
                "judge": "官方桌面任务判分",
                "note": "判分口径与官方一致；需真实桌面 VM（AutoDL 容器无嵌套虚拟化，物理限制）。"},
    "mcp_atlas": {"same": None, "dataset": "官方数据集待发布",
                  "judge": "待官方公布",
                  "note": "接入位就绪，官方数据集/判分脚本发布后即可对齐；当前工具调用能力参考 BFCL。"},
    "humaneval": {"same": True, "dataset": "官方 HumanEval 164 + MBPP 974",
                  "judge": "真执行判 pass@1（官方口径，非 LLM 裁判）",
                  "note": "纯 Python 真跑测试判分，与官方完全一致，免 Docker/编译器，分数直接可比。"},
    "tool_calling": {"same": True, "dataset": "官方 BFCL（simple + multiple）",
                     "judge": "官方 AST 匹配（函数名+参数）",
                     "note": "官方 BFCL 数据 + AST 判分口径，免 Docker，分数直接可比。"},
    "kotlin_bench": {"same": True, "dataset": "官方 HumanEval-Kotlin 161",
                     "judge": "真 kotlinc 编译 + 真跑测试判 pass@1",
                     "note": "JetBrains 官方编码代理评估口径，真编译真跑，免 Docker，分数直接可比。"},
}


def parity_table() -> list[dict]:
    """口径对齐表：每个基准「你的口径 vs 大厂官方」是否一致（直接回答"和大厂一样吗"）。"""
    rows = []
    for bid, p in _PARITY.items():
        lb_key = _ID_TO_LBKEY.get(bid, bid)
        name = (LEADERBOARD.get(lb_key) or {}).get("name") or bid
        if p["same"] is True:
            verdict = "✅ 一致（分数可直接与大厂比）"
        elif p["same"] is False:
            verdict = "⚠️ 有差异（见说明）"
        else:
            verdict = "⬜ 待官方数据集"
        rows.append({"id": bid, "name": name, "verdict": verdict,
                     "dataset": p["dataset"], "judge": p["judge"], "note": p["note"]})
    return rows


def readiness_diagnosis(latest_by_id: dict[str, dict]) -> list[dict]:
    """综合就绪度诊断（V318）：口径一致性 × 实际跑分状态，逐基准回答用户真正的问题
    ——"这个基准我【现在】能不能拿去和大厂比？"

    latest_by_id: {bench_id: {"score_pct","passed","total",...}}（来自 trend.latest_runs）
    每项返回 state ∈ comparable / need_more_samples / not_run / need_env，附一句人话建议。
    """
    from .sample_stats import MIN_COMPARABLE_N, comparability
    parity_by_id = {r["id"]: r for r in parity_table()}
    out = []
    for bid, p in _PARITY.items():
        lb_key = _ID_TO_LBKEY.get(bid, bid)
        name = (LEADERBOARD.get(lb_key) or {}).get("name") or bid
        parity_ok = "一致" in parity_by_id.get(bid, {}).get("verdict", "")
        rec = latest_by_id.get(bid)

        if p["same"] is None:                      # 待官方数据集（如 MCP Atlas）
            state, advice = "need_env", "官方数据集/环境待就位"
        elif not rec or rec.get("score_pct") is None:
            state = "not_run"
            advice = f"口径与官方一致，但还没跑。跑一轮 standard(≥{MIN_COMPARABLE_N}题)即可对比"
        else:
            total = int(rec.get("total") or 0)
            passed = int(rec.get("passed") or 0)
            cmp = comparability(passed, total, bid)
            if total >= MIN_COMPARABLE_N:
                state = "comparable"
                advice = (f"✅ 现在就能和大厂比：{passed}/{total} 题={round(100*passed/max(1,total),1)}%"
                          f"，95%CI [{cmp['ci_low']}%,{cmp['ci_high']}%]")
            else:
                state = "need_more_samples"
                advice = (f"口径一致，但只跑了 {total} 题（需 ≥{MIN_COMPARABLE_N}）"
                          f"——用 HASHMM_BENCH_SAMPLE=standard 重跑就能比")
        out.append({"id": bid, "name": name, "parity_consistent": parity_ok,
                    "state": state, "advice": advice})
    return out


def render_parity_markdown() -> str:
    """口径对齐表的 markdown 渲染。"""
    L = ["## 📋 评测口径 vs 大厂官方（我的测试和大厂一样吗？）", ""]
    L.append("| 基准 | 口径是否一致 | 数据集 | 判分规则 |")
    L.append("|---|---|---|---|")
    for r in parity_table():
        L.append(f"| {r['name']} | {r['verdict']} | {r['dataset']} | {r['judge']} |")
    L.append("")
    L.append("**逐条说明：**")
    for r in parity_table():
        L.append(f"- **{r['name']}**：{r['note']}")
    L.append("")
    L.append("> 核心结论：能跑出分数的基准，判分口径都与官方一致，分数可直接与大厂横向比较。"
             "差异只在【运行环境】（有无 Docker/真实桌面），不在【判分标准】——环境差异不影响"
             "分数含义，只影响哪些题能在本机跑。")
    return "\n".join(L)


def _anchor_for(lb_key: str) -> list[tuple[str, float]]:
    if lb_key in _FRONTIER_ANCHORS:
        return _FRONTIER_ANCHORS[lb_key]
    lb = LEADERBOARD.get(lb_key) or {}
    return sorted(lb.get("refs", []), key=lambda x: x[1], reverse=True)[:4]


def build_comparison(latest_by_id: dict[str, dict]) -> dict:
    """把每个基准最近一次跑分整理成对比结构。

    latest_by_id: {bench_id: {"score_pct", "passed", "total", "name", "model"}}。
    返回 {"comparable": [...], "trend_only": [...], "missing": [...], "model"}。
    """
    comparable: list[dict] = []
    trend_only: list[dict] = []
    missing: list[dict] = []
    model = ""
    # ★ V327 消融对照：取每基准最近一次裸模型基线分，随行透出（差值=你的agent的贡献）
    try:
        from .trend import latest_baselines
        _bl = latest_baselines()
    except Exception:  # noqa: BLE001
        _bl = {}
    try:
        from .registry import get_benchmark as _gb
    except Exception:  # noqa: BLE001
        _gb = lambda _x: None  # noqa: E731

    for bid, lb_key in _ID_TO_LBKEY.items():
        anchors = _anchor_for(lb_key)
        row_name = (LEADERBOARD.get(lb_key) or {}).get("name") or bid
        rec = latest_by_id.get(bid)
        if not rec or rec.get("score_pct") is None:
            missing.append({"id": bid, "name": row_name, "anchors": anchors,
                            "reason": "尚无跑分记录"})
            continue
        model = model or (rec.get("model") or "")
        passed = int(rec.get("passed") or 0)
        total = int(rec.get("total") or 0)
        cmp = comparability(passed, total, bid)
        entry = {
            "id": bid, "name": rec.get("name") or row_name,
            "your_score": round(float(rec["score_pct"]), 1),
            "n": total, "passed": passed,
            "ci_low": cmp["ci_low"], "ci_high": cmp["ci_high"], "ci_width": cmp["ci_width"],
            "coverage_pct": cmp["coverage_pct"], "official_full": cmp["official_full"],
            "verdict": cmp["verdict"], "anchors": anchors,
            "beats": [n for n, v in anchors if float(rec["score_pct"]) >= v],
            "elapsed_ms": int(rec.get("elapsed_ms", 0) or 0),   # 本次跑分用时（你自己的硬件）
            # V326：远程 CI 回传的分数在对比表标注来源（github_actions 等），透明可查
            "remote": bool(rec.get("remote")),
            "source": str(rec.get("source") or ""),
            # V327：被测系统说明（你的Agent / 模型直答 / 官方harness×模型）+ 裸模型基线对照
            "sut": ((_gb(bid) or {}).get("sut") or ""),
            "baseline_score": (round(float(_bl[bid]["score_pct"]), 1)
                               if bid in _bl and _bl[bid].get("score_pct") is not None else None),
        }
        if total >= MIN_COMPARABLE_N:
            comparable.append(entry)
        else:
            entry["why_not"] = (f"只跑了 {total} 题（官方 {cmp['official_full']} 题），"
                                f"置信区间宽 {cmp['ci_width']} 分——需 ≥{MIN_COMPARABLE_N} 题才能和大厂并排。")
            trend_only.append(entry)

    return {"comparable": comparable, "trend_only": trend_only,
            "missing": missing, "model": model}


def render_markdown(cmp: dict) -> str:
    """把对比结构渲染成 markdown（可直接贴进对比报告 / 导出图前的数据表）。"""
    L: list[str] = []
    model = cmp.get("model") or "本机模型"
    L.append(f"# HashMM（{model}）vs 2026 大厂 · 外部基准对比")
    L.append("")
    _sm = comparison_summary(cmp)
    L.append(f"**📊 {_sm['headline']}**")
    L.append("")
    L.append("> 只有样本量达标（≥50 题）的分数才进「正式对比区」与大厂并排；"
             "样本不足的分数进「趋势区」，**不能**横向比较。这是为了让对比图诚实可信。")
    L.append("")

    # ── 正式对比区 ──
    L.append("## ✅ 正式对比区（样本量达标，可与大厂横向比较）")
    L.append("")
    if not cmp["comparable"]:
        L.append("*暂无达标基准。* 把想对比的基准用 `HASHMM_BENCH_SAMPLE=standard`（50 题）"
                 "或 `full`（官方全集）重跑一次，分数就会进这里。")
        L.append("")
    else:
        for e in cmp["comparable"]:
            L.append(f"### {e['name']}")
            L.append("")
            L.append("| 参赛方 | 分数 | 说明 |")
            L.append("|---|---|---|")
            rows = [("**HashMM（你）**", e["your_score"],
                     f"{e['passed']}/{e['n']} 题 · 95%CI [{e['ci_low']}%, {e['ci_high']}%]")]
            for n, v in e["anchors"]:
                rows.append((n, v, "大厂公开数字"))
            for name, val, note in sorted(rows, key=lambda x: -x[1]):
                mark = " ⭐" if name.startswith("**HashMM") else ""
                L.append(f"| {name}{mark} | {val}% | {note} |")
            L.append("")
            if e["beats"]:
                L.append(f"- 已超过：{'、'.join(e['beats'])}")
            L.append(f"- 覆盖率：{e['coverage_pct']}% · 可比性：{e['verdict']}")
            L.append("")

    # ── 趋势区 ──
    L.append("## ⚠️ 趋势区（样本量不足，仅供看自己的迭代，不能和大厂比）")
    L.append("")
    if not cmp["trend_only"]:
        L.append("*无。*")
        L.append("")
    else:
        L.append("| 基准 | 你的分(样本) | 大厂锚点 | 为什么还不能比 |")
        L.append("|---|---|---|---|")
        for e in cmp["trend_only"]:
            top = e["anchors"][0] if e["anchors"] else ("-", 0)
            L.append(f"| {e['name']} | {e['your_score']}% ({e['passed']}/{e['n']}) | "
                     f"{top[0]} {top[1]}% | {e['why_not']} |")
        L.append("")

    # ── 缺失区 ──
    if cmp["missing"]:
        L.append("## ⬜ 尚未跑分")
        L.append("")
        for e in cmp["missing"]:
            top = e["anchors"][0] if e["anchors"] else ("-", 0)
            L.append(f"- **{e['name']}**（大厂锚点 {top[0]} {top[1]}%）：{e['reason']}")
        L.append("")

    L.append("---")
    L.append("*诚实声明：缺失的分数不是 0，是还没跑。各家只发自己赢的榜——"
             "你自己的工作负载才是唯一完全算数的基准。*")
    return "\n".join(L)


def comparison_summary(cmp: dict) -> dict:
    """聚合总结：一句话回答"我到底和大厂比得怎么样"。

    只统计【正式对比区】（样本达标、可比）的基准——趋势区/缺失区不计入（诚实：不可比的
    不拿来充数）。返回 headline + 结构化计数，供图头/前端一行展示。
    """
    comparable = cmp.get("comparable") or []
    anchors_total = sum(len(e.get("anchors") or []) for e in comparable)
    anchors_beaten = sum(len(e.get("beats") or []) for e in comparable)
    # 逐基准"是否达到该榜最高锚点"
    topped = sum(1 for e in comparable
                 if e.get("anchors") and e.get("your_score", 0) >= max(v for _, v in e["anchors"]))
    if not comparable:
        headline = "暂无可比基准（跑够 50 题即进对比区）"
    else:
        headline = (f"在 {len(comparable)} 个可比基准上，超过了 {anchors_beaten}/{anchors_total} "
                    f"个大厂锚点" + (f"，其中 {topped} 个达到该榜最高" if topped else ""))
    return {
        "comparable_benches": len(comparable),
        "anchors_total": anchors_total,
        "anchors_beaten": anchors_beaten,
        "topped_benches": topped,
        "trend_benches": len(cmp.get("trend_only") or []),
        "missing_benches": len(cmp.get("missing") or []),
        "headline": headline,
    }
