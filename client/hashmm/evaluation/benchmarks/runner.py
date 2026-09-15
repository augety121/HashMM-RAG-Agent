"""统一基准运行入口。

V306 关键修正 —— **分数诚实性**：
  · kind="official"：真实基准数据集跑出来的分 → **才**与 leaderboard 对标；
  · kind="builtin" ：内置最小集（十几个自造用例）→ 给分，但**不与官方 leaderboard 比较**
                    （10 个玩具题拿 100% 不代表你超过了 Claude Code）；
  · kind="smoke"   ：只验证适配管线通不通 → **不给分数、不对标**。
之前把 smoke 的"1/1 通过"当成 100% 拿去对标，得出"已达到/超过全部参照"——那是假象，已修。
"""
from __future__ import annotations

from .adapter import HashMMAgentAdapter
from .leaderboard import compare
from .registry import get_benchmark


def _dispatch(bench_id: str, adapter, mode: str, limit: int) -> dict:
    if bench_id == "lohosearch":
        from . import loho_search
        return loho_search.run(adapter, mode=mode, limit=limit)
    # ── 无需 Docker、能出真分的 ──
    if bench_id == "humaneval":
        from . import humaneval_bench
        if humaneval_bench.detect()["installed"]:
            return humaneval_bench.run(adapter)
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": humaneval_bench.detect()["hint"]}
    if bench_id == "tool_calling":
        from . import bfcl
        if bfcl.detect()["installed"]:
            return bfcl.run(adapter)                    # 官方 BFCL 数据
        from . import tool_calling
        return tool_calling.run(adapter)                # 退回内置最小集（不可对标）
    if bench_id == "tau2_bench":
        from . import tau2_full
        if tau2_full.detect()["installed"]:
            return tau2_full.run(adapter, limit=limit)  # 官方 tau-bench harness（纯 Python）
        from . import smoke
        r = smoke.tau2_smoke(adapter)
        r["detail"] += f"（{tau2_full.detect()['hint']}）"
        return r
    if bench_id == "gaia":
        from . import gaia
        if gaia.detect()["installed"]:
            return gaia.run(adapter)                    # 官方 GAIA + quasi-exact-match
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": gaia.detect()["hint"]}
    if bench_id == "kotlin_bench":
        from . import kotlin_bench as KB
        if KB.detect()["installed"]:
            return KB.run(adapter)                      # HumanEval-Kotlin 真编译真跑
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": KB.detect()["hint"]}
    if bench_id == "webvoyager":
        from . import webvoyager
        return webvoyager.run(adapter)
    # ── 需要 Docker / 显式开关的 ──
    if bench_id == "swebench_verified":
        from . import external
        return external.run_swebench(adapter, mode=mode, limit=limit)
    if bench_id == "terminal_bench":
        from . import external
        return external.run_terminal_bench(adapter, mode=mode, limit=limit)
    if bench_id == "agentbench_os":
        from . import agentbench_os
        return agentbench_os.run(adapter)
    if bench_id == "swebench_pro":
        from . import swebench_pro
        return swebench_pro.run(adapter, limit=limit)
    if bench_id == "osworld":
        from . import osworld
        return osworld.run(adapter, limit=limit)
    if bench_id == "mcp_atlas":
        from . import mcp_atlas
        return mcp_atlas.run(adapter, limit=limit)
    if bench_id == "webarena":
        from . import webarena
        return webarena.run(adapter, limit=limit)
    return {"kind": "smoke", "skip": True, "detail": "无运行器"}


def harness_fingerprint(llm_fn=None) -> dict:
    """脚手架指纹（V315）。

    2026 的关键转变（Artificial Analysis Coding Agent Index）：**模型和脚手架配对测试**——
    同一套 GAIA 任务，裸模型 44.8% vs 带完整工具栈 74.6% vs 系统级最高 92.36%，
    30~50 分的差距来自脚手架。所以分数必须连同"用什么脚手架跑的"一起报，否则不可复现、
    也无法归因。这份指纹进每次跑分结果与趋势库。
    """
    import os
    info = {}
    try:
        from hashmm import RELEASE
        info["脚手架版本"] = RELEASE
    except Exception:  # noqa: BLE001
        info["脚手架版本"] = "unknown"
    try:
        from hashmm.api.tool_registry import TOOL_ANNOTATIONS
        info["工具数"] = len(TOOL_ANNOTATIONS)
    except Exception:  # noqa: BLE001
        pass
    try:
        from hashmm.agent.adaptive_rag import enabled as _rag_on
        from hashmm.agent.iterative_retrieval import iterative_enabled as _it_on
        info["自适应RAG"] = "开" if _rag_on() else "关"
        info["迭代检索"] = "开" if _it_on() else "关"
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import experience as _EXP
        info["经验闭环"] = "开" if _EXP.enabled() else "关"
    except Exception:  # noqa: BLE001
        pass
    try:
        from hashmm.agent.modules import all_modules
        info["能力模块"] = ",".join(m.key for m in all_modules() if m.enabled())
    except Exception:  # noqa: BLE001
        pass
    configured_model = (
        getattr(llm_fn, "model_name", "")
        or os.environ.get("HASHMM_OPENAI_MODEL", "")
        or os.environ.get("HASHMM_MODEL", "")
    )
    if configured_model:
        info["模型"] = str(configured_model)
    else:
        try:
            from hashmm.api.model_manager import get_active_llm_fn
            _fn, meta = get_active_llm_fn()
            info["模型"] = str((meta or {}).get("model_name") or "unknown")
        except Exception:  # noqa: BLE001
            info["模型"] = "unknown"
    return info

def run_benchmark(bench_id: str, mode: str = "smoke", llm_fn=None, limit: int = 3) -> dict:
    b = get_benchmark(bench_id)
    if not b:
        return {"id": bench_id, "error": "未知基准", "skip": True,
                "score_pct": None, "comparable": False}
    adapter = HashMMAgentAdapter(llm_fn)
    if not adapter.ready:
        return {"id": bench_id, "name": b["name"], "skip": True, "kind": "smoke",
                "score_pct": None, "comparable": False,
                "detail": "未配置可用 LLM，无法跑基准"}
    try:
        import time as _t
        _t0 = _t.time()
        r = _dispatch(bench_id, adapter, mode, limit)
        r["elapsed_ms"] = round((_t.time() - _t0) * 1000)
    except Exception as e:  # noqa: BLE001
        return {"id": bench_id, "name": b["name"], "skip": True, "kind": "smoke",
                "score_pct": None, "comparable": False,
                "detail": f"运行异常:{type(e).__name__}:{e}"}

    kind = r.get("kind", "smoke")
    score = r.get("score_pct")
    from .adapter import baseline_mode
    _bl = baseline_mode()
    default_comparable = kind == "official" and score is not None and not r.get("skip")
    result_comparable = bool(r["comparable"]) if "comparable" in r else default_comparable
    out = {
        "id": bench_id, "name": (b["name"] + "（🧪裸模型基线）") if _bl else b["name"],
        "kind": kind, "mode": mode,
        "passed": r.get("passed", 0), "total": r.get("total", 0),
        "score_pct": score,                      # smoke 为 None
        "pipeline_ok": r.get("pipeline_ok"),
        "detail": ("[🧪基线：裸模型直答，未用你的agent——仅作消融对照] " if _bl else "") + r.get("detail", ""),
        "skip": bool(r.get("skip", False)),
        # ★ 只有官方数据集的分数才可与顶尖 agent 对标；基线分永不对标（它不是你的系统的分）
        "comparable": result_comparable and not _bl,
        "elapsed_ms": r.get("elapsed_ms", 0),    # V322：本次跑分用时（你自己的硬件，非对标指标）
        # ★ V327 透明化：本基准测的是什么系统（你的Agent / 模型直答 / 官方harness×模型）
        "sut": b.get("sut", ""),
        "baseline": _bl,
    }
    if out["comparable"]:
        out["comparison"] = compare(b["lb_key"], float(score))
    else:
        # 仍把参照带上供展示，但明确不做"你超过了谁"的判定
        out["comparison"] = None
    for k in ("breakdown", "fails", "cases", "metrics", "snapshot"):     # cases = 逐题明细（详细报告要用）
        if k in r:
            out[k] = r[k]
    # ★ V315：分数必须连同脚手架指纹一起报（模型+harness 配对，2026 对标口径）
    out["harness"] = harness_fingerprint(llm_fn)
    if out.get("breakdown") is not None:
        out["breakdown"] = {**out["breakdown"],
                            "脚手架": " · ".join(f"{k}={v}" for k, v in out["harness"].items())}
    try:
        from . import trend
        trend.record_run(out, meta={"mode": mode, "kind": kind, "baseline": _bl,
                                    "elapsed_ms": out.get("elapsed_ms", 0)})
    except Exception:  # noqa: BLE001
        pass
    return out


# ── 「为对比而跑」：本机当前真能产出可比分数的基准 ────────────────────
# 只列【官方口径、免 Docker、判分不打折】的基准（跑够题即可与大厂横向比）。
# webvoyager/webarena 用 LLM 裁判（非官方 GPT-4V 口径）→ 只作纵向参考，不入此表；
# swebench/terminal/osworld 需 Docker/VM 或网络，视机器而定，也不入此表（在自测里单列）。
_COMPARABLE_CAPABLE = ["tau2_bench", "gaia", "humaneval", "tool_calling", "kotlin_bench"]


def _detect_for(bench_id: str) -> dict:
    """调用某基准的 detect()（统一入口，缺失/异常都安全降级）。"""
    try:
        if bench_id == "tau2_bench":
            from . import tau2_full as M
        elif bench_id == "gaia":
            from . import gaia as M
        elif bench_id == "humaneval":
            from . import humaneval_bench as M
        elif bench_id == "tool_calling":
            from . import bfcl as M
        elif bench_id == "kotlin_bench":
            from . import kotlin_bench as M
        else:
            return {"installed": False, "hint": "非可对比基准"}
        return M.detect()
    except Exception as e:  # noqa: BLE001
        return {"installed": False, "hint": f"探测失败:{type(e).__name__}"}


def comparable_candidates() -> list[dict]:
    """本机可对比基准的就绪清单：逐项回答「跑够题就能和大厂比吗？现在能跑吗？还缺什么」。

    返回 [{id,name,runnable,hint,standard_n,full_n}]。runnable=True 表示现在就能跑出
    可比分数（安装+前置都就绪），跑够 standard(50) 题即进正式对比区。
    """
    from .sample_stats import MIN_COMPARABLE_N, SAMPLE_PRESETS
    out = []
    for bid in _COMPARABLE_CAPABLE:
        b = get_benchmark(bid) or {}
        det = _detect_for(bid)
        key = {"tau2_bench": "tau2", "gaia": "gaia", "humaneval": "humaneval",
               "tool_calling": "tool_calling", "kotlin_bench": "kotlin"}.get(bid, bid)
        # V323 自查修正：预设表已含 tool_calling/kotlin 真实键，不再用代理键
        std_n = SAMPLE_PRESETS["standard"].get(key, MIN_COMPARABLE_N)
        full_n = SAMPLE_PRESETS["full"].get(key, 0)
        out.append({
            "id": bid, "name": b.get("name") or bid,
            "runnable": bool(det.get("installed")),
            "hint": det.get("hint", "") or ("就绪，可跑" if det.get("installed") else "未就绪"),
            "standard_n": std_n, "full_n": full_n,
            "min_comparable": MIN_COMPARABLE_N,
        })
    return out


def run_for_comparison(bench_ids: list[str] | None = None, *, sample: str = "standard",
                       llm_fn=None, progress=None, parallel: int | None = None,
                       baseline: bool | None = None) -> list[dict]:
    """跑一批基准以产出可对比分数：强制 sample 档位（默认 standard=50），无视 env 残留。

    只跑 runnable 的；结果自动入 bench_runs（→ 对比图/报告）。progress(done,total,id) 可选回调。
    返回每项的 run_benchmark 结果（含 skip 原因），顺序与 bench_ids 一致。

    ★ V332 基准级并发：桌面端此前逐个串行（5 个基准 × 50 题≈数小时）。现在与 CI runner
      同款 ThreadPool 并发（parallel 参数 > HASHMM_BENCH_PARALLEL > 默认 2，上限 4）。
      sample 档位是 thread-local（sample_stats._tls），**必须在工作线程内 set/clear**，
      否则并发线程读不到强制档位、静默回落 env——这与 remote_bench_runner._run_one 同一坑。
      注意总 LLM 压力 ≈ 基准级并发 × 逐题并发(HASHMM_BENCH_CONCURRENCY，默认 4)。
    """
    import os as _os
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from contextlib import nullcontext
    from .adapter import baseline_scope
    from .sample_stats import set_forced_sample, clear_forced_sample
    ids = [i for i in (bench_ids or _COMPARABLE_CAPABLE) if i in _COMPARABLE_CAPABLE]
    runnable = {c["id"]: c for c in comparable_candidates() if c["runnable"]}
    try:
        workers = int(parallel if parallel is not None
                      else _os.environ.get("HASHMM_BENCH_PARALLEL", "2"))
    except Exception:  # noqa: BLE001
        workers = 2
    workers = max(1, min(workers, 4))   # 上限 4：humaneval/kotlin 会在本机真跑代码/编译

    results_by_idx: dict[int, dict] = {}
    jobs: list[tuple[int, str]] = []
    for i, bid in enumerate(ids):
        if bid not in runnable:
            c = next((x for x in comparable_candidates() if x["id"] == bid), {})
            results_by_idx[i] = {"id": bid, "name": c.get("name") or bid, "skip": True,
                                 "detail": f"未就绪，跳过：{c.get('hint', '')}", "kind": "official"}
        else:
            jobs.append((i, bid))

    def _notify(done: int, bid: str) -> None:
        if callable(progress):
            try:
                progress(done, len(ids), bid)
            except Exception:  # noqa: BLE001
                pass

    def _one(pair: tuple[int, str]) -> tuple[int, dict]:
        i, bid = pair
        set_forced_sample(sample)               # ★ thread-local：工作线程内强制档位
        try:
            scope = baseline_scope(baseline) if baseline is not None else nullcontext()
            with scope:
                return i, run_benchmark(bid, mode="full", llm_fn=llm_fn)
        finally:
            clear_forced_sample()

    done = len(results_by_idx)
    _notify(done, jobs[0][1] if jobs else "")
    if workers <= 1 or len(jobs) <= 1:
        for pair in jobs:
            i, r = _one(pair)
            results_by_idx[i] = r
            done += 1
            _notify(done, pair[1])
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs)),
                                thread_name_prefix="hashmm-cmp") as pool:
            futs = {pool.submit(_one, pair): pair for pair in jobs}
            for fut in as_completed(futs):
                i, bid = futs[fut]
                try:
                    _, r = fut.result()
                except Exception as e:  # noqa: BLE001
                    r = {"id": bid, "name": bid, "skip": True, "kind": "official",
                         "detail": f"并发工作线程异常：{type(e).__name__}: {e}"}
                results_by_idx[i] = r
                done += 1
                _notify(done, bid)
    return [results_by_idx[i] for i in range(len(ids))]
