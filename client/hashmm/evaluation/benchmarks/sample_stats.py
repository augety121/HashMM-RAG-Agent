"""样本量与置信区间（V310）——回答「我这个分数能不能和大厂的比？」

═══ 为什么必须有这个模块 ═══
大厂发布会引用的数字是【官方全集】跑出来的：SWE-bench Verified 500 题一题不落、
τ²-bench retail 115 题全跑、Terminal-bench 核心集全跑。而本地评测出于 token 成本，
默认只跑几题——3 题、10 题。

问题在于：**3 题的分数几乎不携带信息**。真实能力 40% 的 agent，跑 3 题的观测值
95% 置信区间是 [6%, 79%]（宽 73 分）；跑 10 题仍有 ±26 分。也就是说：
  · 你看到 0/3 → 真实能力可能是 0%，也可能是 30%，样本根本分不出来；
  · 你看到 2/3（67%）→ 也不能说你超过了大厂的 50%。

不把这件事显式告诉用户，"对标"就是自欺。所以每个基准的报告都必须带上：
  ① 你跑了 N 题 / 官方全集 M 题（覆盖率）；
  ② 这个 N 对应的 95% 置信区间有多宽；
  ③ 一句人话的可比性判定（能不能拿去和 leaderboard 比）。

纯标准库（只用 math），零依赖，可单测。
"""
from __future__ import annotations

import math

# ── 官方全集题量（大厂发布会引用的口径）──
# 数字来源：各基准官方仓库/论文的 test split 规模。用于计算"覆盖率"，让用户一眼看到差距。
OFFICIAL_SIZES: dict[str, dict] = {
    "tau2": {
        "full": 115, "name": "τ²-bench (retail test)",
        "note": "官方 retail 115 / airline 50 / telecom 114；大厂报告全集跑",
    },
    "swebench": {
        "full": 500, "name": "SWE-bench Verified",
        "note": "官方 500 题全集，大厂一题不落地跑（Docker 逐实例隔离）",
    },
    "swebench_pro": {
        "full": 731, "name": "SWE-bench Pro (public)",
        "note": "公开 split 731 题（另有商业私有集，Scale SEAL 私榜口径）；抗污染，2026 更被信任",
    },
    "osworld": {
        "full": 369, "name": "OSWorld",
        "note": "真实桌面 computer-use 369 题（官方 VM/Docker 桌面环境）",
    },
    "terminal": {
        "full": 80, "name": "Terminal-Bench 1.0",
        "note": "官方 terminal-bench-core==0.1.1 共 80 题；与 2.0/2.1 是不同榜单",
    },
    "webarena": {
        "full": 812, "name": "WebArena",
        "note": "812 题，需 Docker 自建 5 个网站",
    },
    "gaia": {
        "full": 165, "name": "GAIA (validation)",
        "note": "validation 165 题（level 1-3）",
    },
    "webvoyager": {
        "full": 643, "name": "WebVoyager",
        "note": "643 题，跨 15 个真实网站",
    },
    "humaneval": {
        "full": 164, "name": "HumanEval",
        "note": "164 题（+MBPP 500 题）",
    },
    "bfcl": {
        "full": 2000, "name": "BFCL",
        "note": "多类目合计约 2000+ 条",
    },
}

# 样本量门槛：低于此值的分数不建议与 leaderboard 横向比较。
MIN_COMPARABLE_N = 50
# 达到此值可视为"统计上足够稳"（±10 分以内）。
GOOD_N = 100


# ── 四档样本预设（HASHMM_BENCH_SAMPLE=smoke|quick|standard|full）──
# 动机：题量直接决定 token 花费，也直接决定分数能不能和大厂比。与其偷偷用一个小默认值
# 让用户误以为"我跑过 SWE-bench 了"，不如把这个取舍摆到台面上，让他自己选：
#   smoke    —— V311 新增：每基准 1 题、几十秒出结果。只回答一个问题："管道通不通"
#               （依赖齐不齐 / API 通不通 / 判分能不能跑）。0 分基准反复跑 20 分钟才
#               看到错误太痛苦——先 smoke 定位结构性问题，再上量。分数无任何意义。
#   quick    —— 冒烟用。几题就出结果，省 token，但【分数不可比】（报告会明说）。
#   standard —— 50 题级别。区间收窄到 ±10 分上下，趋势可信，可弱对标。
#   full     —— 官方全集。这才是大厂发布会引用的口径，跑一次很贵（SWE 500 题）。
# V311 同时补齐 webarena 键（此前缺失 → standard/full 档下静默回落代码默认值）。
SAMPLE_PRESETS: dict[str, dict[str, int]] = {
    "smoke":    {"tau2": 1,  "swebench": 1,   "swebench_pro": 1,   "terminal": 1,  "webarena": 1,   "webvoyager": 1,  "gaia": 1,   "humaneval": 1,   "kotlin": 1,   "tool_calling": 1,   "osworld": 1},
    "quick":    {"tau2": 5,  "swebench": 3,   "swebench_pro": 3,   "terminal": 5,  "webarena": 5,   "webvoyager": 10, "gaia": 10,  "humaneval": 10,  "kotlin": 10,  "tool_calling": 10,  "osworld": 5},
    "standard": {"tau2": 50, "swebench": 50,   "swebench_pro": 50,  "terminal": 50, "webarena": 50,  "webvoyager": 50, "gaia": 50,  "humaneval": 50,  "kotlin": 50,  "tool_calling": 50,  "osworld": 50},
    "full":     {"tau2": 115, "swebench": 500,   "swebench_pro": 731, "terminal": 80, "webarena": 812, "webvoyager": 100, "gaia": 165, "humaneval": 164, "kotlin": 161, "tool_calling": 200, "osworld": 369},
}


def resolve_limit(bench_key: str, fallback: int) -> int:
    """按预设决定本次跑多少题。**默认就是可比档（standard=50）**，和大厂一个口径。

    优先级（从高到低）：
      0. 线程局部强制档位（set_forced_sample）—— 程序化"为对比而跑"用，覆盖一切。
      1. 单基准环境变量（如 HASHMM_TAU2_LIMIT）—— 用户显式微调（可低于 50，但会不可比）。
      2. HASHMM_BENCH_SAMPLE 预设（smoke/quick/standard/full）—— 用户显式切全局档位。
      3. **默认 = standard(50 题)** —— 不配置任何东西时就跑够题、默认可比（≥50→Wilson CI 收窄
         到可与大厂横向比）。这是 V322 的关键改变：以前默认跑 fallback(3~30 题)导致默认不可比，
         用户得手动升档才能对比；现在反过来——默认可比，想省 token 才显式降档。
    """
    import os
    forced = _forced_sample()                       # ← 优先级 0
    if forced in SAMPLE_PRESETS:
        return int(SAMPLE_PRESETS[forced].get(bench_key, fallback))
    per_bench = os.environ.get(f"HASHMM_{bench_key.upper()}_LIMIT")
    if per_bench and per_bench.strip().isdigit():
        return int(per_bench)
    preset = (os.environ.get("HASHMM_BENCH_SAMPLE") or "").strip().lower()
    if preset in SAMPLE_PRESETS:
        return int(SAMPLE_PRESETS[preset].get(bench_key, fallback))
    # ★ 默认可比：standard(50)。没有该 key 的预设值时才回落到调用方 fallback。
    return int(SAMPLE_PRESETS["standard"].get(bench_key, fallback))


def capping_env_below_comparable(bench_key: str) -> str:
    """若某个 env 把本基准题数压到了可比阈值(50)以下，返回该 env 名（供 UI 提示"删了它就可比"）。

    只报"显式设了 HASHMM_*_LIMIT 或 HASHMM_BENCH_SAMPLE=quick/smoke 导致 <50"的情形；
    默认(无配置)不会触发（默认就是 50）。返回空串＝没有东西在拦。
    """
    import os
    n = resolve_limit(bench_key, MIN_COMPARABLE_N)
    if n >= MIN_COMPARABLE_N:
        return ""
    per = os.environ.get(f"HASHMM_{bench_key.upper()}_LIMIT")
    if per and per.strip().isdigit() and int(per) < MIN_COMPARABLE_N:
        return f"HASHMM_{bench_key.upper()}_LIMIT={per}"
    preset = (os.environ.get("HASHMM_BENCH_SAMPLE") or "").strip().lower()
    if preset in ("smoke", "quick"):
        return f"HASHMM_BENCH_SAMPLE={preset}"
    return "低于可比阈值的样本设置"


# ── 线程局部强制档位：给「为对比而跑」用（背景 job 在自己线程里设定，互不干扰）──
import threading as _threading  # noqa: E402

_tls = _threading.local()


def _forced_sample():
    return getattr(_tls, "force_sample", None)


def set_forced_sample(preset: str | None) -> None:
    """在当前线程强制样本档位（'standard'/'full'/...）。传 None 或非法值＝清除。"""
    _tls.force_sample = preset if preset in SAMPLE_PRESETS else None


def clear_forced_sample() -> None:
    _tls.force_sample = None


def wilson_interval(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 区间（比 normal approximation 在小样本/极端值下靠谱得多）。

    返回 (下界%, 上界%)。total=0 时返回 (0, 100)（完全无信息）。
    为什么用 Wilson 而不是 p ± 1.96*sqrt(p(1-p)/n)：后者在 p=0 时区间宽度为 0
    （"0/3 → 0%±0%"），这是彻头彻尾的误导。Wilson 在 0/3 时给出 [0%, 56%]，
    如实反映"样本太小，什么都说明不了"。
    """
    if total <= 0:
        return (0.0, 100.0)
    p = passed / total
    denom = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    lo = max(0.0, (center - margin) / denom)
    hi = min(1.0, (center + margin) / denom)
    return (round(100 * lo, 1), round(100 * hi, 1))


def comparability(passed: int, total: int, bench_key: str = "") -> dict:
    """给一个跑测结果生成「能不能和大厂比」的结构化判定。

    返回 dict：
      n / official_full / coverage_pct / ci_low / ci_high / ci_width / verdict / advice
    """
    spec = OFFICIAL_SIZES.get(bench_key, {})
    full = int(spec.get("full") or 0)
    lo, hi = wilson_interval(passed, total)
    width = round(hi - lo, 1)
    cov = round(100.0 * total / full, 1) if full else None

    if total <= 0:
        verdict = "无样本"
        advice = "这一项没跑出任何题，分数栏应显示「未跑」而不是 0%。"
    elif total < 10:
        verdict = "❌ 不可比（样本过小）"
        advice = (f"只跑了 {total} 题，95% 置信区间宽达 {width} 分——这个分数几乎不携带信息，"
                  f"和 leaderboard 上的数字【没有可比性】。想得到有意义的结论至少跑 "
                  f"{MIN_COMPARABLE_N} 题。")
    elif total < MIN_COMPARABLE_N:
        verdict = "⚠️ 仅供纵向参考"
        advice = (f"{total} 题的置信区间仍有 ±{width / 2:.0f} 分。可以用来看【自己的迭代趋势】"
                  f"（同样题量下分数是否在涨），但不建议和大厂的全集数字横向比。")
    elif total < GOOD_N:
        verdict = "⚠️ 弱可比"
        advice = f"{total} 题，区间 ±{width / 2:.0f} 分。趋势可信；与 leaderboard 比要注明题量。"
    else:
        verdict = "✅ 可比"
        advice = (f"{total} 题，区间 ±{width / 2:.0f} 分，统计上足够稳。"
                  + (f"但仍是官方 {full} 题的 {cov}%，横向比时请注明。" if full and total < full else ""))

    return {
        "n": total,
        "passed": passed,
        "official_full": full or None,
        "coverage_pct": cov,
        "ci_low": lo,
        "ci_high": hi,
        "ci_width": width,
        "verdict": verdict,
        "advice": advice,
        "official_note": spec.get("note", ""),
    }


def format_breakdown(passed: int, total: int, bench_key: str = "") -> dict:
    """生成可直接塞进基准 breakdown 的键值对（中文，给报告和看板用）。"""
    c = comparability(passed, total, bench_key)
    out = {
        "样本量": (f"{c['n']} 题"
                   + (f" / 官方全集 {c['official_full']} 题（覆盖 {c['coverage_pct']}%）"
                      if c["official_full"] else "")),
        "95%置信区间": f"[{c['ci_low']}%, {c['ci_high']}%]（宽 {c['ci_width']} 分）",
        "可比性": c["verdict"],
    }
    if c["advice"]:
        out["样本量提示"] = c["advice"]
    # V316：满分/极端分 + 小样本最容易造成"看着比大厂还高"的误解——前置一条醒目提示。
    if total > 0 and total < MIN_COMPARABLE_N:
        pct = round(100.0 * passed / total, 1)
        if pct >= 90.0:
            out["⚠️分数看着高但不可比"] = (
                f"{passed}/{total} 题={pct}% 只是小样本结果，真实水平在 "
                f"[{c['ci_low']}%, {c['ci_high']}%] 这么宽的区间里——**不能**据此说"
                f"“超过了大厂”。想和大厂对比请 HASHMM_BENCH_SAMPLE=standard 跑 ≥50 题。")
        elif pct <= 10.0 and total <= 5:
            out["⚠️分数看着低但可能是样本太小"] = (
                f"{passed}/{total} 题只够验证管道通不通，别当成真实水平。")
    if total <= 2:
        # V311：smoke/极小样本明确降级——1 题 0 分不是"0%"，只是管道验证结果。
        out["可比性"] = "smoke 级（≤2 题）：仅验证管道连通性，分数无统计意义"
    return out
