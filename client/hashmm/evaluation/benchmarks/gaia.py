"""GAIA 基准（Meta 推出的通用 agent 助手基准）—— V306。

★ 不需要 Docker。需要**联网搜索**（你后端的 web_search 工具 + Serper key）+ 多步推理 + 工具使用。

数据：官方 validation 集 165 题（GitHub 镜像，install.sh gaia 拉取）。其中 **127 题无附件**，
纯靠搜索+推理即可作答 —— 这就是本模块默认跑的子集（有附件的题需要下载文件，默认跳过；
设 HASHMM_GAIA_WITH_FILES=1 且附件已下载时才纳入）。

判分：官方 **quasi-exact-match**（归一化后精确匹配）—— 数字去千分位/单位、字符串小写去标点、
列表按元素逐个比。这是 GAIA 官方 scorer 的口径，不是 LLM 裁判，**不会给自己放水**。

题目难度分 Level 1/2/3。顶尖 agent 在 validation 上大约 30–50%（GPT-4+插件 ~15%，人类 ~92%）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import re
import string
from pathlib import Path


from . import experience as _EXP
from ._paths import bench_home  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def _data_file() -> Path | None:
    """定位 GAIA validation metadata.jsonl（不同镜像层级可能不同，全局搜）。"""
    root = bench_home() / "GAIA"
    if not root.is_dir():
        return None
    hits = list(root.rglob("validation/metadata.jsonl")) or list(root.rglob("metadata.jsonl"))
    return hits[0] if hits else None


def detect() -> dict:
    f = _data_file()
    return {"installed": f is not None, "path": str(f) if f else "",
            "hint": "" if f else "未装 GAIA 数据：运行 benchmarks/install.sh gaia（GitHub 镜像，无需 Docker/HF）"}


def load_tasks(limit: int = 20, with_files: bool = False) -> list[dict]:
    f = _data_file()
    if not f:
        return []
    tasks = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not t.get("Question") or not t.get("Final answer"):
            continue
        if t.get("file_name") and not with_files:
            continue          # 有附件的题需要文件，默认跳过（诚实：不硬跑会失败的题）
        tasks.append(t)
    return tasks[:limit] if limit else tasks


# ── 官方 quasi-exact-match 判分 ──────────────────────────────────────────────
def _norm_number(s: str) -> str | None:
    t = s.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        f = float(t)
        return str(int(f)) if f.is_integer() else str(round(f, 6))
    except Exception:  # noqa: BLE001
        return None


def _norm_str(s: str) -> str:
    s = str(s).strip().lower()
    s = s.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", s).strip()


def score_answer(pred: str, gold: str) -> bool:
    """GAIA 官方口径：数字→归一化比较；列表→逐元素比；字符串→去标点小写比。"""
    pred, gold = str(pred or "").strip(), str(gold or "").strip()
    if not pred:
        return False
    # 列表（逗号或分号分隔）
    if ("," in gold or ";" in gold) and len(re.split(r"[;,]", gold)) > 1:
        sep = ";" if ";" in gold else ","
        gs = [x.strip() for x in gold.split(sep) if x.strip()]
        ps = [x.strip() for x in re.split(r"[;,]", pred) if x.strip()]
        if len(gs) != len(ps):
            return False
        return all(score_answer(p, g) for p, g in zip(ps, gs))
    # 数字
    gn, pn = _norm_number(gold), _norm_number(pred)
    if gn is not None:
        return pn == gn
    # 字符串
    return _norm_str(pred) == _norm_str(gold)


_PROMPT = (
    "你是一个能联网搜索、能执行代码的通用 AI 助手。请回答下面的问题。\n"
    "可以使用工具：web_search（联网搜索）、fetch_url（抓网页）、execute_code（跑 Python）、calculator。\n"
    "多步推理：先搜索获取事实，必要时抓页面细节、算数用代码，最后给出答案。\n\n"
    "【回答格式（必须严格遵守）】最后一行必须是：\n"
    "FINAL ANSWER: <答案>\n"
    "答案要尽量简短：一个数字、或几个词、或用逗号分隔的列表。数字不要带千分位逗号和单位；"
    "字符串不要加冠词、不要写缩写、数字用阿拉伯数字。\n\n"
    "问题：{q}"
)



# ★ V312 强制补答：现场报告里 3/20 题的"答案"是预算耗尽的道歉话术（"抱歉，我在处理
# 这个任务时没能获取到足够的信息…"），还有一题答 "Insufficient data"。GAIA 是
# quasi-exact-match——道歉必错，给出最可能的猜测至少有机会。识别放弃话术后在
# 【同一会话】里追加一轮，压出基于已收集信息的最优答案。
_GIVEUP_MARKERS = ("抱歉，我在处理这个任务", "没能获取到足够的信息", "无法回答",
                   "无法确定", "insufficient data", "i'm sorry", "i am sorry",
                   "cannot determine", "not enough information", "unable to find")

_FORCE_ANSWER = (
    "不要道歉、不要说信息不足。基于你目前已经搜索到/看到的全部信息，"
    "给出你【最可能】的最终答案。就算需要推测，也给出最合理的那个。\n"
    "严格按格式输出最后一行：\nFINAL ANSWER: <答案>\n"
    "只给答案本身（数字/短语/逗号分隔列表），不带解释、不带单位、不带千分位。"
)


def _is_giveup(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    return any(m in t for m in _GIVEUP_MARKERS)


def _extract_final(text: str) -> str:
    m = re.search(r"FINAL ANSWER\s*[:：]\s*(.+)", str(text), re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("。.").strip()
    # 兜底：取最后一行非空
    lines = [x.strip() for x in str(text).splitlines() if x.strip()]
    return lines[-1] if lines else ""


def run(adapter, *, limit: int = 20) -> dict:
    """跑 GAIA。用真实 AgentLoop（带 web_search 等工具），官方 quasi-exact-match 判分。"""
    det = detect()
    if not det["installed"]:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": det["hint"]}

    from .sample_stats import resolve_limit
    limit = resolve_limit("gaia", limit)   # 默认 standard(50)，仍尊重 HASHMM_GAIA_LIMIT
    with_files = os.environ.get("HASHMM_GAIA_WITH_FILES") == "1"
    tasks = load_tasks(limit=limit, with_files=with_files)
    if not tasks:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": "GAIA 数据已存在但没解析出可跑任务"}

    from hashmm.tools import agent_bench as AB
    passed = 0
    by_level: dict = {}
    fails: list[str] = []
    cases: list[dict] = []          # 逐题明细 → 详细报告 + 你据此优化
    no_search = 0                   # 有多少题 agent 压根没联网搜（GAIA 必须搜）
    empty_ans = 0                   # 有多少题没给出答案
    forced = 0                      # 有多少题触发了"放弃话术→强制补答"（V312）
    nudged = 0                      # 有多少题首答没调工具被强制补搜（V313）

    # V324 并发：每题独立（一整套多轮 agent 逻辑），并行跑。worker 返回聚合所需字段。
    # ★ 并发安全：experience 的 bench_memory() 是共享单例，并发读写会竞争，且对并行任务
    #   本就无益（同时跑、看不到彼此经验）。故【仅串行(并发=1)时】注入经验；并行时不注入。
    #   无论哪种模式，主线程仍会 record_outcome 为将来的跑分积累经验。
    from .parallel import bench_concurrency
    _conc = bench_concurrency()
    _hint_kw = _EXP.hint_kwargs("gaia") if _conc <= 1 else {}

    def _one(iv: tuple) -> dict:
        i, t = iv
        gold = str(t.get("Final answer", ""))
        lvl = str(t.get("Level", "?"))
        query = _PROMPT.format(q=t["Question"])
        answer, tools, err = "", [], ""
        _nudged = _forced = 0
        conv_id = f"gaia-{i}-{int(time.time())}-{threading.get_ident()}"
        try:
            task = AB.Task(id=f"gaia_{i}", category="gaia", turns=[query], requires=set(),
                           scorers=[AB.answer_nonempty(min_len=1)], max_seconds=300)
            r = AB.run_task(task, adapter.llm_fn, conv_id=conv_id, **_hint_kw)
            raw = r.get("answer") or ""
            answer = _extract_final(raw)
            tools = r.get("tools_used") or []
            if not any("search" in str(x).lower() or "fetch" in str(x).lower() for x in tools):
                _nudged = 1
                taskN = AB.Task(id=f"gaia_{i}_search", category="gaia",
                                turns=["你还没有联网查证。现在必须先用 web_search 搜索关键事实"
                                       "（必要时 fetch_url 看原文），再按 FINAL ANSWER 格式给出答案。"],
                                requires=set(), scorers=[AB.answer_nonempty(min_len=1)], max_seconds=180)
                rN = AB.run_task(taskN, adapter.llm_fn, conv_id=conv_id)
                rawN = rN.get("answer") or ""
                ansN = _extract_final(rawN)
                if ansN:
                    answer, raw = ansN, rawN
                tools = tools + (rN.get("tools_used") or [])
            if _is_giveup(answer) or _is_giveup(raw):
                _forced = 1
                task2 = AB.Task(id=f"gaia_{i}_force", category="gaia",
                                turns=[_FORCE_ANSWER], requires=set(),
                                scorers=[AB.answer_nonempty(min_len=1)], max_seconds=120)
                r2 = AB.run_task(task2, adapter.llm_fn, conv_id=conv_id)
                raw2 = r2.get("answer") or ""
                ans2 = _extract_final(raw2)
                if ans2 and not _is_giveup(ans2):
                    answer = ans2
                tools = tools + (r2.get("tools_used") or [])
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        ok = score_answer(answer, gold)
        return {"i": i, "t": t, "gold": gold, "lvl": lvl, "answer": answer,
                "tools": tools, "err": err, "ok": ok, "nudged": _nudged, "forced": _forced}

    from .parallel import run_parallel
    results = run_parallel(list(enumerate(tasks)), _one)
    for r in results:
        if r.get("_error"):        # worker 整体异常（并发助手兜底）
            continue
        t, gold, lvl = r["t"], r["gold"], r["lvl"]
        answer, tools, err, ok = r["answer"], r["tools"], r["err"], r["ok"]
        by_level.setdefault(lvl, [0, 0])
        by_level[lvl][1] += 1
        nudged += r["nudged"]
        forced += r["forced"]
        _EXP.record_outcome("gaia", str(t.get("Question", ""))[:300], bool(ok),
                            detail=(f"答对：{answer[:80]}" if ok
                                    else f"答「{answer[:40]}」应为「{gold[:40]}」"))
        if ok:
            passed += 1
            by_level[lvl][0] += 1
        if not answer:
            empty_ans += 1
        if not any("search" in str(x).lower() or "fetch" in str(x).lower() for x in tools):
            no_search += 1
        cases.append({
            "id": t.get("task_id", "")[:12], "level": lvl, "ok": ok,
            "q": str(t.get("Question", ""))[:120],
            "pred": answer[:60], "gold": gold[:60],
            "tools": ",".join(str(x) for x in tools[:6]) or "（没调任何工具）",
            "err": err[:60],
        })
        if not ok and len(fails) < 5:
            fails.append(f"L{lvl}: 答「{answer[:20] or '(空)'}」应为「{gold[:20]}」")

    total = len(tasks)
    diag = {}
    if no_search:
        diag["⚠️没联网搜索的题"] = f"{no_search}/{total}（GAIA 必须搜索，没搜必错——检查 Serper key 是否对后端进程生效）"
    if empty_ans:
        diag["⚠️没给出答案的题"] = f"{empty_ans}/{total}（没按 FINAL ANSWER 格式输出）"
    if nudged:
        diag["已强制补搜的题"] = f"{nudged}/{total}（首答没调任何工具——GAIA 没搜必错，已同会话追加强制搜索轮）"
    if forced:
        diag["已强制补答的题"] = (f"{forced}/{total}（首答是道歉/放弃话术——道歉在 exact-match 下必错，"
                                 f"已同会话追加一轮压出最可能答案）")
    return {
        "kind": "official", "skip": False,
        "passed": passed, "total": total,
        "score_pct": round(100.0 * passed / max(1, total), 1),
        "detail": f"官方数据集（GAIA validation 前 {total} 题{'（含附件题）' if with_files else '（无附件子集）'}，官方 quasi-exact-match 判分）",
        "breakdown": {**{f"Level {k}": f"{v[0]}/{v[1]}" for k, v in sorted(by_level.items())},
                      **diag, "判分口径": "官方 quasi-exact-match（非 LLM 裁判）"},
        "fails": fails[:5],
        "cases": cases,
    }
