"""一键生成【详细】外部基准对标报告（markdown）—— V306。

不是几行概览，而是一份能拿去**指导优化**的报告：
  · 环境体检（Docker/数据/搜索后端就绪情况，缺什么一目了然）
  · 每个基准：分数 + 与顶尖 agent 逐项对比表 + 分项拆解 + **逐题明细** + **失败归因**
  · **针对性优化建议**（根据失败模式自动生成：选错工具？没联网搜？编译错？）
  · 历史趋势（每次迭代的分数变化）
诚实性：smoke 不出分、内置集不对标、官方集才对标（已在数据层保证）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .leaderboard import LEADERBOARD, compare
from .registry import BENCHMARKS
from .trend import get_trend, purge_polluted, summary
from ._paths import bench_home as _bench_home

_KIND_CN = {
    "official": "✅ 官方数据集（可对标）",
    "builtin": "⚠️ 内置最小集（**不可**与官方 leaderboard 比较）",
    "smoke": "管线自检（不产生分数）",
}


def _bar(pct: float, width: int = 30) -> str:
    n = max(0, min(width, int(round(pct / 100.0 * width))))
    return "█" * n + "░" * (width - n)



def _env_section() -> list[str]:
    """环境体检：缺什么一眼看清。"""
    import shutil
    import subprocess
    h = _bench_home()
    L = ["## 0. 环境体检", ""]
    docker = False
    if shutil.which("docker"):
        try:
            docker = subprocess.run(["docker", "info"], capture_output=True, timeout=6).returncode == 0
        except Exception:  # noqa: BLE001
            docker = False
    L.append(f"- Docker：{'✅ 可用（SWE-bench/Terminal-bench 可跑官方模式）' if docker else '❌ 不可用（AutoDL 容器实例通常不给特权 Docker）→ 这两个基准走本机模式'}")
    L.append(f"- 基准目录：`{h}`")
    L.append(f"- 运行模式：`HASHMM_BENCH_MODE={os.environ.get('HASHMM_BENCH_MODE', 'smoke')}`"
             f"{'' if os.environ.get('HASHMM_BENCH_MODE') == 'full' else ' ⚠️ 不是 full，跑不出真分！'}")
    key = os.environ.get("HASHMM_SERPER_API_KEY", "")
    L.append(f"- 搜索后端（GAIA/WebVoyager 必须）：{'✅ Serper 已配置' if key else '❌ 未配置 HASHMM_SERPER_API_KEY → GAIA 必然 0 分'}")
    L.append("")
    L.append("| 基准数据 | 状态 |")
    L.append("|---|---|")
    checks = [
        ("BFCL（工具调用）", list(h.glob("gorilla/**/*simple*.json"))),
        ("τ²-bench", [h / "tau-bench" / "run.py"] if (h / "tau-bench" / "run.py").exists() else []),
        ("GAIA", list(h.glob("GAIA/**/metadata.jsonl"))),
        ("Kotlin 数据", list(h.glob("mxeval/**/HumanEval_kotlin*.jsonl"))),
        ("kotlinc 编译器", [h / "kotlinc" / "bin" / "kotlinc"] if (h / "kotlinc" / "bin" / "kotlinc").exists() else []),
        ("WebVoyager", list(h.glob("WebVoyager/**/*.jsonl"))),
        ("SWE-bench 数据集", [h / "swebench_verified.jsonl"] if (h / "swebench_verified.jsonl").exists() else []),
        ("Terminal-bench 任务", list(h.glob("terminal-bench/original-tasks/*/task.yaml"))[:1]),
        ("AgentBench", list(h.glob("AgentBench/**/os_interaction/**/*.json"))[:1]),
    ]
    for name, hits in checks:
        L.append(f"| {name} | {'✅ 已装' if hits else '❌ 未装 → `bash install.sh` 对应目标'} |")
    L.append("")
    return L


def _suggestions(bid: str, s: dict) -> list[str]:
    """★ 根据失败模式自动给出**针对性优化建议** —— 这才是拿来改项目的。"""
    L: list[str] = []
    bd = s.get("breakdown") or {}
    cases = s.get("cases") or []
    score = s.get("latest", 0)

    if bid == "tool_calling":
        wrong_fn = int(bd.get("选错函数", 0) or 0)
        wrong_arg = int(bd.get("参数不对", 0) or 0)
        if wrong_fn > wrong_arg and wrong_fn:
            L.append(f"- **主要瓶颈：选错函数（{wrong_fn} 例）** → 优化工具描述："
                     "每个工具的 description 写清「什么时候用/什么时候不用」，加 2-3 个 few-shot 示例；"
                     "相似工具之间显式写出区别（如 `search` vs `fetch_url`）。")
        if wrong_arg:
            L.append(f"- **参数填错（{wrong_arg} 例）** → 在工具 schema 的每个参数上补 `description` 和"
                     "示例值；必填参数用 `required` 显式标注；枚举类参数给出全部合法取值。")
        if score < 80:
            L.append("- 距离顶尖模型（88%）还有差距 → 考虑在系统提示里加"
                     "「先复述用户意图 → 再选工具」的两步式约束，能显著降低选错率。")

    elif bid == "gaia":
        nosearch = next((v for k, v in bd.items() if "没联网搜索" in k), "")
        empty = next((v for k, v in bd.items() if "没给出答案" in k), "")
        if nosearch:
            L.append(f"- **🔴 致命：{nosearch}** → GAIA 全靠联网搜索，没搜必错。检查："
                     "① `HASHMM_SERPER_API_KEY` 是否对**后端进程**生效（不是当前 shell）；"
                     "② 系统提示里是否鼓励 agent 主动调 web_search；"
                     "③ agent 的 max_iterations 是否够它搜索+抓页+推理（GAIA 常需 5-10 步）。")
        if empty:
            L.append(f"- **{empty}** → 强化输出格式约束：在系统提示末尾重复一遍"
                     "「最后一行必须是 FINAL ANSWER: xxx」，并在解析失败时让 agent 重试一次。")
        if cases:
            l1 = [c for c in cases if str(c.get("level")) == "1"]
            if l1 and sum(1 for c in l1 if c.get("ok")) == 0:
                L.append("- **连 Level 1（最简单）都全错** → 说明不是推理能力问题，是**工具链没跑通**。"
                         "先手动在对话里问一个需要搜索的问题，确认 web_search 真的返回结果。")
        if score and score < 15:
            L.append("- 当前低于「GPT-4+插件」参照（15%）→ 优先补搜索能力，而不是换更强的模型。")

    elif bid == "kotlin_bench":
        comp = int(bd.get("编译失败", 0) or 0)
        test = int(bd.get("测试失败", 0) or 0)
        if comp > test and comp:
            L.append(f"- **主要瓶颈：编译失败（{comp} 例）** → 模型生成的 Kotlin 语法不过关。"
                     "在提示里明确「只输出可编译的 Kotlin，不要 markdown 围栏、不要 import 不存在的包」；"
                     "可加一轮「编译错误反馈 → 让模型自修」的循环（agentic 自修能显著提分）。")
        if test:
            L.append(f"- **逻辑错误（测试失败 {test} 例）** → 模型能写出语法正确但逻辑错的代码。"
                     "考虑让 agent 先写测试用例自测、或加 self-reflection 一轮。")

    elif bid == "tau2_bench":
        L.append("- τ²-bench 考的是**多轮对话中遵守业务策略**。低分常见原因："
                 "① agent 没读 wiki（业务规则）就动手；② 该问用户的没问、擅自替用户决定；"
                 "③ 工具调用顺序错。→ 在系统提示里强制「先复述规则 → 再确认信息 → 最后执行」。")

    elif bid == "terminal_bench":
        L.append("- 终端任务失败多因 agent **不验证自己的操作结果**。→ 在提示里要求"
                 "「每步操作后用 shell 校验结果，再进行下一步」；提高 max_iterations。")

    if not L:
        L.append("- 暂无自动建议（分数尚可或样本不足）。可增大跑测题量获得更稳定的结论。")
    return L


def _cases_table(cases: list, bid: str) -> list[str]:
    """逐题明细表 —— 你能直接看到每道题答了什么、该答什么、为什么错。"""
    if not cases:
        return []
    L = ["<details><summary>📋 逐题明细（点击展开）</summary>", ""]
    if bid == "gaia":
        L += ["| # | Lv | 结果 | 问题 | 你的答案 | 正确答案 | 用了哪些工具 |", "|---|---|---|---|---|---|---|"]
        for i, c in enumerate(cases[:40], 1):
            L.append(f"| {i} | {c.get('level','')} | {'✅' if c.get('ok') else '❌'} | "
                     f"{str(c.get('q',''))[:50]} | {c.get('pred') or '(空)'} | {c.get('gold','')} | {c.get('tools','')} |")
    elif bid == "tool_calling":
        L += ["| # | 类别 | 结果 | 请求 | 你调的 | 应调的 | 错因 |", "|---|---|---|---|---|---|---|"]
        for i, c in enumerate(cases[:40], 1):
            L.append(f"| {i} | {c.get('cat','')} | {'✅' if c.get('ok') else '❌'} | "
                     f"{str(c.get('q',''))[:40]} | {c.get('pred','')} | {c.get('gold','')} | {c.get('why','')} |")
    elif bid == "kotlin_bench":
        L += ["| # | 题目 | 函数 | 结果 | 失败原因 |", "|---|---|---|---|---|"]
        for i, c in enumerate(cases[:40], 1):
            L.append(f"| {i} | {c.get('id','')} | {c.get('entry','')} | "
                     f"{'✅' if c.get('ok') else '❌'} | {c.get('why','')} |")
    else:
        L += ["| # | 结果 | 详情 |", "|---|---|---|"]
        for i, c in enumerate(cases[:40], 1):
            L.append(f"| {i} | {'✅' if c.get('ok') else '❌'} | {str(c)[:100]} |")
    L += ["", "</details>", ""]
    return L


def build_markdown() -> str:
    L: list[str] = []
    purged = purge_polluted()
    sm = summary()
    hist = get_trend(limit=500)

    L.append("# HashMM 外部基准对标 · 详细报告")
    L.append("")
    L.append(f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    if purged:
        L.append(f"> 🧹 已自动清理 {purged} 条历史污染数据（早期 smoke 误入库的假分数）。")
    L.append("")

    L += _env_section()

    # ── 大厂横向对比（V316：带可比性门禁，只有达标分数才与大厂并排）──
    try:
        from .trend import latest_runs
        from .vs_frontier import build_comparison, render_markdown, render_parity_markdown
        cmp = build_comparison(latest_runs())
        L.append("")
        L.append(render_markdown(cmp))
        L.append("")
        L.append(render_parity_markdown())
        L.append("")
    except Exception:  # noqa: BLE001
        pass

    # ── 概览 ──
    L.append("## 1. 总览")
    L.append("")
    L.append("| 基准 | 类型 | 最新分 | 最高分 | 跑测 | 较上次 | 对标位置 |")
    L.append("|---|---|---|---|---|---|---|")
    for b in BENCHMARKS:
        s = sm.get(b["id"])
        if not s:
            L.append(f"| {b['name']} | — | 未跑出分数 | — | 0 | — | — |")
            continue
        kind = _KIND_CN.get(s.get("kind", ""), s.get("kind", ""))
        delta = "—" if s.get("delta") is None else f"{s['trend']} {s['delta']:+}%"
        pos = ""
        if s.get("comparable"):
            pos = compare(b["lb_key"], float(s["latest"]))["ranking"]
        L.append(f"| {b['name']} | {kind} | **{s['latest']}%** | {s['best']}% | {s['runs']} | {delta} | {pos} |")
    L.append("")

    # ── 逐基准详情 ──
    L.append("## 2. 逐基准详情")
    L.append("")
    for b in BENCHMARKS:
        s = sm.get(b["id"])
        lb = LEADERBOARD.get(b["lb_key"], {})
        L.append(f"### {b['name']}")
        L.append("")
        if lb:
            L.append(f"- **口径**：{lb.get('metric', '')}")
            L.append(f"- **说明**：{lb.get('note', '')}")
        L.append(f"- **前置**：{b.get('requires', '')}")
        if not s:
            L.append("- **状态**：❌ 尚未跑出真实分数（未安装数据集 / 前置不满足 / 只跑了 smoke）")
            L.append("")
            continue

        L.append(f"- **类型**：{_KIND_CN.get(s.get('kind',''), '')}")
        L.append("")
        L.append("```")
        L.append(f"你的分数  {s['latest']:>5.1f}%  {_bar(s['latest'])}")
        if s.get("comparable") and lb.get("refs"):
            for name, val in sorted(lb["refs"], key=lambda x: -x[1]):
                L.append(f"{name[:10]:<10} {val:>5.1f}%  {_bar(val)}")
        L.append("```")
        L.append("")
        if s.get("comparable") and lb.get("refs"):
            c = compare(b["lb_key"], float(s["latest"]))
            L.append(f"**对标位置：{c['ranking']}**")
            if c.get("beats"):
                L.append(f"已超过：{', '.join(c['beats'])}")
            L.append("")

        bd = s.get("breakdown") or {}
        if bd:
            L.append("**分项拆解**")
            L.append("")
            L.append("| 项 | 值 |")
            L.append("|---|---|")
            for k, v in bd.items():
                L.append(f"| {k} | {v} |")
            L.append("")

        fails = s.get("fails") or []
        if fails:
            L.append("**失败样例**")
            L.append("")
            for f in fails:
                L.append(f"- {f}")
            L.append("")

        L += _cases_table(s.get("cases") or [], b["id"])

        # ★ 针对性优化建议
        L.append("**🔧 优化建议（据失败模式自动生成）**")
        L.append("")
        L += _suggestions(b["id"], s)
        L.append("")

        h = [x for x in hist if x["bench_id"] == b["id"]]
        if len(h) > 1:
            L.append("**历史趋势**")
            L.append("")
            L.append("| 时间 | 分数 | 通过/总数 |")
            L.append("|---|---|---|")
            for x in h[-12:]:
                ts = time.strftime("%m-%d %H:%M", time.localtime(x["ts"]))
                L.append(f"| {ts} | {x['score_pct']}% | {x['passed']}/{x['total']} |")
            L.append("")

    # ── 收尾 ──
    L.append("---")
    L.append("")
    L.append("## 3. 分数可比性说明（重要）")
    L.append("")
    L.append("- **official**：跑官方数据集，分数可与 leaderboard 做量级对比。")
    L.append("- **builtin**：内置最小集，只用于回归，**不可**与官方分数比较。")
    L.append("- **smoke**：只验证管线通不通，**不产生分数**、不入库。")
    L.append("- 无 Docker 时 SWE-bench / Terminal-bench 走**本机模式**："
             "判分脚本仍是官方的，但环境不是官方 Docker 镜像 → 标注为非官方口径。")
    L.append("- 参照分数取自各基准官方榜单/供应商公开报告，随版本变化，以官方最新为准。")
    return "\n".join(L)
