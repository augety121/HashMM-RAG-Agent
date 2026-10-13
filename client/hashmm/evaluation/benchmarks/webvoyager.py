"""WebVoyager / WebArena —— 网页交互基准，V306。

**先把话说清楚（免得你又白装一遍）：**

· **WebArena 跑不了**：它要求你用 Docker **自建 5 个网站**（购物/论坛/GitLab/地图/CMS）作为可复现的
  沙箱环境。没有 Docker 就没有环境，这不是判分问题，是**环境根本起不来**。本模块对 WebArena 只做
  一件事：如实告诉你需要 Docker，绝不假装能跑。

· **WebVoyager 可以跑**（部分）：它的任务是在**真实网站**上完成信息检索/查询（Allrecipes、Amazon、
  arXiv、GitHub、Google Search、Booking…）。你的后端有 `web_search`（Serper）+ `fetch_url`，
  能覆盖其中**信息检索型**的任务。
  **诚实标注**：官方评测用 **GPT-4V 看截图**判分（agent 需要真的点击/滚动页面）；我们这里用
  **文本工具 + LLM 裁判**判分，口径与官方**不同**，分数标为
  "官方任务·文本工具+LLM裁判(非官方GPT-4V口径)"，只能作为**你自己迭代的纵向参考**，
  **不建议**直接和 leaderboard 上的 WebVoyager 分数横向比。

任务数据：官方仓库 `MinorJerry/WebVoyager` 的 `data/WebVoyager_data.jsonl`（GitHub 直连）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


from ._paths import bench_home  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def _data_file() -> Path | None:
    root = bench_home() / "WebVoyager"
    if not root.is_dir():
        return None
    hits = list(root.rglob("*WebVoyager*data*.jsonl")) or list(root.rglob("data/*.jsonl"))
    return hits[0] if hits else None


def detect() -> dict:
    f = _data_file()
    return {"installed": f is not None,
            "hint": "" if f else "未装 WebVoyager 数据：运行 benchmarks/install.sh webvoyager（GitHub 直连，无需 Docker）"}


def webarena_status() -> dict:
    """WebArena 的诚实状态：需要 Docker 自建网站，你的机器跑不了。"""
    return {
        "runnable": False,
        "reason": "WebArena 需要用 Docker 自建 5 个网站（购物/论坛/GitLab/地图/CMS）作为可复现沙箱。"
                  "你的 AutoDL 容器没有 Docker → 环境起不来，无法评测。"
                  "替代：跑 WebVoyager（真实网站，不需要 Docker）。",
    }


# 只跑"信息检索型"网站的任务（文本工具够用）；购物下单/预订这类强交互的先不跑（会假过/假败）
_TEXT_FRIENDLY = ("google search", "arxiv", "github", "wolfram", "bbc", "espn",
                  "coursera", "cambridge dictionary", "huggingface")


def load_tasks(limit: int = 20) -> list[dict]:
    f = _data_file()
    if not f:
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        q = t.get("ques") or t.get("question") or t.get("query") or ""
        site = str(t.get("web_name") or t.get("web") or "").lower()
        if not q:
            continue
        if not any(s in site for s in _TEXT_FRIENDLY):
            continue           # 强交互站点（下单/预订）文本工具跑不了 → 不跑，不假装
        out.append({"id": t.get("id", ""), "site": site, "q": q,
                    "url": t.get("web") or t.get("url") or ""})
        if limit and len(out) >= limit:
            break
    return out


_JUDGE = (
    "你是严格的评测裁判。判断下面这个网页任务的回答是否**真正完成了任务**。\n"
    "标准：答案必须是具体的、可核验的信息；含糊其辞、'我无法访问'、'请自行查看'一律算失败。\n"
    "只输出一个词：SUCCESS 或 FAILURE。\n\n任务：{q}\n\n回答：{a}"
)


def _browser_mode_available() -> bool:
    """浏览器内核是否具备 Chromium 引擎（决定 WebVoyager 能否走真浏览器口径）。"""
    if os.environ.get("HASHMM_WEBVOYAGER_BROWSER", "").strip() == "0":
        return False   # 显式关闭
    try:
        from hashmm.tools.browser_kernel import get_kernel
        return get_kernel().playwright_available()
    except Exception:  # noqa: BLE001
        return False


def run(adapter, *, limit: int = 15) -> dict:
    det = detect()
    if not det["installed"]:
        return {"kind": "official", "skip": True, "score_pct": None, "detail": det["hint"]}

    from .sample_stats import resolve_limit
    limit = resolve_limit("webvoyager", limit)
    tasks = load_tasks(limit)
    if not tasks:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": "WebVoyager 数据已装，但没有文本工具能覆盖的任务"}

    from hashmm.tools import agent_bench as AB
    # ★ V309：有 Chromium 引擎时走【真浏览器】口径——agent 用 browser_open/act/read 真在
    # 页面上打开/点击/读取，比"web_search+fetch_url 读文本"更接近官方 GPT-4V 看截图的口径。
    use_browser = _browser_mode_available()

    def _one(pair):
        """跑一题并 LLM 裁判；只返回纯数据，聚合在主线程做（线程安全约定见 parallel.py）。"""
        i, t = pair
        if use_browser:
            q = (f"用浏览器工具完成下面这个网页任务：先 browser_open 打开网址，用 browser_read 读页面、"
                 f"browser_act 点击/填表/翻页与页面交互，直到找到答案。给出**具体、可核验**的答案"
                 f"（不要说'请自行查看'）。\n\n网站：{t['url']}\n任务：{t['q']}")
        else:
            q = (f"用 web_search 搜索、fetch_url 抓取页面来完成下面这个网页任务，"
                 f"给出**具体、可核验**的答案（不要说'请自行查看'）。\n\n"
                 f"网站：{t['url']}\n任务：{t['q']}")
        try:
            task = AB.Task(id=f"wv_{i}", category="webvoyager", turns=[q], requires=set(),
                           scorers=[AB.answer_nonempty(min_len=1)], max_seconds=300)
            # 浏览器模式给 agent NETWORK 级作用域权限（browser_* 是 NETWORK 级；评测无人点批准）
            r = AB.run_task(task, adapter.llm_fn,
                            permission_mode="bypass" if use_browser else None)
            ans = str(r.get("answer") or "")
        except Exception:  # noqa: BLE001
            ans = ""
        verdict = ""
        if ans.strip():
            try:
                verdict = adapter.answer(_JUDGE.format(q=t["q"], a=ans[:1500]))
            except Exception:  # noqa: BLE001
                verdict = ""
        ok = bool(re.search(r"SUCCESS", str(verdict), re.IGNORECASE))
        return {"ok": ok, "site": t["site"], "q": t["q"]}

    # ★ V332 逐题并发：文本工具模式每题只有 HTTP 调用（LLM/搜索/抓页），线程安全，
    #   与 gaia/humaneval/bfcl/kotlin 同用 run_parallel（HASHMM_BENCH_CONCURRENCY，默认 4）。
    #   真浏览器模式共享同一个 Chromium 内核（get_kernel() 单例），页面状态互串 → 强制串行。
    from .parallel import run_parallel
    results = run_parallel(list(enumerate(tasks)), _one,
                           concurrency=None if not use_browser else 1)

    passed = 0
    fails: list[str] = []
    by_site: dict = {}
    for t, r in zip(tasks, results):
        by_site.setdefault(t["site"], [0, 0])
        by_site[t["site"]][1] += 1
        if isinstance(r, dict) and r.get("ok"):
            passed += 1
            by_site[t["site"]][0] += 1
        elif len(fails) < 5:
            err = (r or {}).get("_error") if isinstance(r, dict) else None
            fails.append(f"{t['site']}: {t['q'][:36]}" + (f"（{err}）" if err else ""))

    total = len(tasks)
    _judge_note = ("真浏览器操作+LLM裁判（比纯文本口径更接近官方，但裁判仍是文本 LLM 非 GPT-4V）"
                   if use_browser else
                   "LLM 裁判（官方用 GPT-4V 看截图，口径不同，不建议横向比 leaderboard）")
    return {
        "kind": "official", "skip": False,
        "passed": passed, "total": total,
        "score_pct": round(100.0 * passed / max(1, total), 1),
        "detail": (f"官方任务（WebVoyager {total} 题，"
                   + ("**真浏览器(Chromium)+LLM裁判**）" if use_browser
                      else "**文本工具+LLM裁判·非官方GPT-4V口径**）")),
        "breakdown": {**{k: f"{v[0]}/{v[1]}" for k, v in by_site.items()},
                      "引擎": "chromium(真浏览器)" if use_browser else "文本工具",
                      "判分口径": _judge_note},
        "fails": fails[:5],
    }
