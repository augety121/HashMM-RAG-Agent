"""hashmm/evaluation/deep_suites_quality.py — 质量 · 鲁棒 · 稳定性硬测试（V297）。

诉求原话："往深入的测试、压力测试以及稳定性测试；chat 输出准确性、工具调用的准确性、
RAG 检索的准确性、chat 的幻觉如何解决、质量测评。"

  1. chat_accuracy        —— Chat 输出准确性（需 LLM）：给定材料的事实问答，按关键 token 精判。
  2. tool_call_accuracy   —— 工具调用准确性（需 LLM）：给工具清单+问题，判"选对工具+参数对+
                              不该调时不乱调"（JSON 严格解析）。
  3. hallucination_guard  —— 幻觉治理（需 LLM）：材料没有答案必须弃答；问 B 材料只有 A 必须弃答；
                              前提错误必须纠正而不是顺着编。
  4. rag_citation         —— RAG 引用忠实（需 LLM）：答案必须标注来源 [docN]，且被引文档确实含
                              该结论的关键词（引了不含的 = 张冠李戴，判负）。
  5. stability_soak       —— 稳定性浸泡（离线、真跑）：多线程 DB 浸泡（8 线程×40 轮建会话/写读）、
                              派活队列搅拌（100 轮 create/poll/complete 交错 3 runner）、画布 200 次
                              重复渲染确定性。统计错误数=0、p95 延迟、吞吐，全程分段日志。

LLM 套件未配模型时如实 skip；每条用例多次运行取稳、逐步骤留"执行日志"；永不抛错。
"""
from __future__ import annotations

import json
import re
import statistics
import time
import uuid

from hashmm.utils import get_logger, log_suppressed
from hashmm.evaluation.deep_eval import RunOutcome, SuiteReport, run_case_ntimes
from hashmm.evaluation.deep_suites_persist import _fresh_db, _log
from hashmm.evaluation.tool_call_eval import evaluate_tool_calls

logger = get_logger("hashmm.evaluation.deep_suites_quality")


def _clip(s, n: int = 400) -> str:
    t = str(s if s is not None else "")
    return t if len(t) <= n else t[:n] + f" …（共{len(t)}字）"


def _ask(llm_fn, sys_p: str, user_p: str, max_tok: int = 400) -> str:
    try:
        qc = getattr(llm_fn, "quick_call", None)
        if callable(qc):
            return str(qc(sys_p, user_p, max_tok=max_tok) or "").strip()
        return str(llm_fn(sys_p + "\n\n" + user_p) or "").strip()
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return ""


# ════════════════════════════════════════════════════════════════════════
# 1. Chat 输出准确性
# ════════════════════════════════════════════════════════════════════════
def run_chat_accuracy(llm_fn=None, k: int = 2) -> SuiteReport:
    rep = SuiteReport("质量·Chat输出准确性")
    cases = [
        {"name": "数值提取", "mat": "本季度营收 1.83 亿元，同比增长 27%；净利润 2100 万元。",
         "q": "本季度营收多少？", "must": ["1.83"], "ban": ["2100", "27%"]},
        {"name": "多实体不串", "mat": "张三负责检索模块，李四负责前端，王五负责部署。",
         "q": "前端是谁负责？", "must": ["李四"], "ban": ["张三", "王五"]},
        {"name": "是否题带依据", "mat": "系统要求 Python ≥3.10。当前环境 Python 3.8。",
         "q": "当前环境满足系统要求吗？只答'满足'或'不满足'并给一句依据。", "must": ["不满足"], "ban": []},
        {"name": "简单推算", "mat": "会议 14:00 开始，时长 90 分钟。",
         "q": "会议几点结束？", "must": ["15:30"], "ban": []},
    ]
    if not callable(llm_fn):
        for c in cases:
            rep.add(run_case_ntimes(c["name"], None, k, skip_reason="未配 LLM"))
        return rep
    sys_p = "你是严谨的问答助手。只依据【材料】作答，答案精确、直接，不要展开。"
    for c in cases:
        def run_once(c=c):
            ans = _ask(llm_fn, sys_p, f"【材料】{c['mat']}\n【问题】{c['q']}")
            got = all(m in ans for m in c["must"])
            polluted = any(b in ans and b not in c["q"] for b in c["ban"])
            ok = got and not polluted
            trace = {"问题": c["q"], "材料": c["mat"], "模型答案": _clip(ans, 200),
                     "必含": c["must"], "不得串入": c["ban"] or "（无）",
                     "判定": "答案准确 ✓" if ok else ("串入无关实体/数字 ✗" if polluted else f"漏关键答案{c['must']} ✗")}
            return RunOutcome(ok, 1.0 if ok else 0.0,
                              "" if ok else ("实体串扰" if polluted else "答案不准"),
                              _clip(ans, 100), trace)
        rep.add(run_case_ntimes(c["name"], run_once, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 2. 工具调用准确性
# ════════════════════════════════════════════════════════════════════════
_TOOLS_SPEC = (
    '可用工具（只能选其一或不选）：\n'
    '1. {"tool":"weather","args":{"city":"<城市名>"}} —— 查实时天气\n'
    '2. {"tool":"calc","args":{"expr":"<四则表达式>"}} —— 数学计算\n'
    '3. {"tool":"kb_search","args":{"query":"<检索词>"}} —— 查内部知识库\n'
    '规则：实时天气必须调用 weather；精确四则运算必须调用 calc（即使心算很简单也一样）；'
    '仅内部资料可回答的问题必须调用 kb_search。需要工具时**只输出一行 JSON**'
    '（不要解释、不要代码块围栏）；只有闲聊或无需外部/精确能力时才输出 '
    '{"tool":"none","answer":"<直接答案>"}。'
)

_QUALITY_TOOL_SCHEMAS = {
    "weather": {"required": ["city"], "properties": {"city": {}}, "additionalProperties": False},
    "calc": {"required": ["expr"], "properties": {"expr": {}}, "additionalProperties": False},
    "kb_search": {"required": ["query"], "properties": {"query": {}}, "additionalProperties": False},
}


def _parse_tool_json(ans: str) -> dict | None:
    m = re.search(r"\{.*\}", ans, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def run_tool_call_accuracy(llm_fn=None, k: int = 2) -> SuiteReport:
    rep = SuiteReport("质量·工具调用准确性")
    cases = [
        {"name": "选对工具+参数(天气)", "q": "上海现在天气怎么样？",
         "calls": [{"name": "weather", "args": {"city": {"contains": "上海"}}}]},
        {"name": "选对工具+参数(计算)", "q": "帮我算 37*24 等于多少",
         "calls": [{"name": "calc", "args": {"expr": {"contains": "37"}}}]},
        {"name": "选对工具+参数(知识库)", "q": "查一下我们知识库里跨模态哈希的资料",
         "calls": [{"name": "kb_search", "args": {"query": {"contains": "哈希"}}}]},
        {"name": "不该调时不乱调", "q": "你好呀，用一句话介绍你自己",
         "calls": []},
    ]
    if not callable(llm_fn):
        for c in cases:
            rep.add(run_case_ntimes(c["name"], None, k, skip_reason="未配 LLM"))
        return rep
    for c in cases:
        def run_once(c=c):
            ans = _ask(llm_fn, _TOOLS_SPEC, "用户请求：" + c["q"], max_tok=200)
            obj = _parse_tool_json(ans)
            steps = [f"模型原始输出：{_clip(ans, 220)}",
                     f"JSON 解析：{'成功' if obj else '失败（没按契约只输出 JSON）'}"]
            if not obj:
                trace = {"问题": c["q"], "执行日志": steps, "判定": "未按契约输出可解析 JSON ✗"}
                return RunOutcome(False, 0.0, "输出不可解析", _clip(ans, 80), trace)
            got_tool = obj.get("tool")
            actual = [] if got_tool in (None, "", "null", "none") else [
                {"name": got_tool, "args": obj.get("args", {})}
            ]
            judged = evaluate_tool_calls(c["calls"], actual, tool_schemas=_QUALITY_TOOL_SCHEMAS)
            steps.append(f"期望工具：{judged['expected_tools'] or '不调用'}；实际工具：{judged['actual_tools'] or '未调用'}")
            steps.extend(f"判分：{f.get('label', f.get('code'))}" for f in judged["failures"][:5])
            ok = judged["passed"]
            trace = {"问题": c["q"], "执行日志": steps,
                     "判定": "严格工具契约通过（未执行副作用）" if ok else "严格工具契约未通过"}
            first_failure = judged["failures"][0].get("label", "工具调用错误") if judged["failures"] else ""
            return RunOutcome(ok, judged["score"], "" if ok else first_failure,
                              f"tool={obj.get('tool')}", trace)
        rep.add(run_case_ntimes(c["name"], run_once, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 3. 幻觉治理（弃答 / 纠错，而不是顺着编）
# ════════════════════════════════════════════════════════════════════════
_ABSTAIN_WORDS = ["未提及", "没有提到", "无法确定", "不知道", "材料中没有", "未给出", "无法回答", "没有相关信息", "未包含"]


def run_hallucination_guard(llm_fn=None, k: int = 2) -> SuiteReport:
    rep = SuiteReport("质量·幻觉治理")
    cases = [
        {"name": "材料无答案必须弃答",
         "mat": "本项目使用 SQLite 存储会话，前端为 Next.js。",
         "q": "本项目的服务器部署在哪个城市？",
         "type": "abstain", "ban": ["北京", "上海", "深圳", "杭州", "广州"]},
        {"name": "只有A问B必须弃答",
         "mat": "2023 年营收为 5000 万元。",
         "q": "2024 年营收是多少？",
         "type": "abstain", "ban": ["5000"]},
        {"name": "错误前提必须纠正",
         "mat": "会议纪要：项目 Alpha 由王工负责。",
         "q": "既然项目 Alpha 由李工负责，请评价李工的负责情况。",
         "type": "correct", "must_any": ["王工", "并非", "不是李工", "前提", "有误", "纠正"]},
    ]
    if not callable(llm_fn):
        for c in cases:
            rep.add(run_case_ntimes(c["name"], None, k, skip_reason="未配 LLM"))
        return rep
    sys_p = ("你是严谨的问答助手。只依据【材料】作答；材料没有的信息明确说'材料未提及/无法确定'，"
             "绝不编造；用户前提与材料矛盾时先指出并纠正。")
    for c in cases:
        def run_once(c=c):
            ans = _ask(llm_fn, sys_p, f"【材料】{c['mat']}\n【问题】{c['q']}")
            steps = [f"材料：{c['mat']}", f"问题：{c['q']}", f"模型答案：{_clip(ans, 260)}"]
            if c["type"] == "abstain":
                abstained = any(w in ans for w in _ABSTAIN_WORDS)
                fabricated = any(b in ans for b in c.get("ban", []))
                ok = abstained and not fabricated
                steps.append(f"弃答判定：{'✓ 明确弃答' if abstained else '✗ 未弃答'}；"
                             f"编造检查：{'✗ 出现了材料没有的具体值' if fabricated else '✓ 无编造'}")
                verdict = "拒绝编造、明确弃答 ✓" if ok else ("编造了不存在的信息 ✗" if fabricated else "既没答对也没明确弃答 ✗")
                fm = "" if ok else ("编造幻觉" if fabricated else "未明确弃答")
            else:
                ok = any(w in ans for w in c["must_any"])
                steps.append(f"纠错判定：{'✓ 指出并纠正了错误前提' if ok else '✗ 顺着错误前提往下编'}")
                verdict = "纠正错误前提 ✓" if ok else "顺着错误前提编（前提幻觉）✗"
                fm = "" if ok else "前提幻觉"
            trace = {"执行日志": steps, "判定": verdict}
            return RunOutcome(ok, 1.0 if ok else 0.0, fm, _clip(ans, 90), trace)
        rep.add(run_case_ntimes(c["name"], run_once, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 4. RAG 引用忠实（引了必须真含——张冠李戴判负）
# ════════════════════════════════════════════════════════════════════════
def run_rag_citation(llm_fn=None, k: int = 2) -> SuiteReport:
    rep = SuiteReport("质量·RAG引用忠实")
    cases = [
        {"name": "标注来源且引对",
         "docs": {"doc1": "缓存策略：热点数据 TTL 为 300 秒。", "doc2": "限流策略：默认每分钟 120 次。"},
         "q": "热点数据的缓存 TTL 是多少？答案末尾用 [docN] 标注依据来源。",
         "must": ["300"], "right_doc": "doc1", "key": "300"},
        {"name": "多文档各引各的",
         "docs": {"doc1": "A 服务超时阈值 5 秒。", "doc2": "B 服务超时阈值 8 秒。"},
         "q": "B 服务的超时阈值是多少？答案末尾用 [docN] 标注依据来源。",
         "must": ["8"], "right_doc": "doc2", "key": "8"},
    ]
    if not callable(llm_fn):
        for c in cases:
            rep.add(run_case_ntimes(c["name"], None, k, skip_reason="未配 LLM"))
        return rep
    for c in cases:
        def run_once(c=c):
            mat = "\n".join(f"[{k_}] {v}" for k_, v in c["docs"].items())
            ans = _ask(llm_fn, "只依据材料作答，并按要求在末尾标注 [docN] 来源。",
                       f"【材料】\n{mat}\n【问题】{c['q']}")
            steps = [f"材料：{mat}", f"模型答案：{_clip(ans, 220)}"]
            got = all(m in ans for m in c["must"])
            cited = re.findall(r"\[(doc\d+)\]", ans)
            cite_right = c["right_doc"] in cited
            # 张冠李戴检查：引用的每个 doc 必须真的含答案关键词（引了不含的判负）
            miscited = [d for d in cited if c["key"] not in c["docs"].get(d, "")]
            steps.append(f"答案含关键值：{'✓' if got else '✗'}；引用了：{cited or '（没标注）'}；"
                         f"正确来源 {c['right_doc']} 被引：{'✓' if cite_right else '✗'}；"
                         f"张冠李戴（引了不含答案的 doc）：{miscited or '无'}")
            ok = got and cite_right and not miscited
            trace = {"执行日志": steps,
                     "判定": "引用忠实（标了、标对、没乱标）✓" if ok else
                             ("张冠李戴 ✗" if miscited else ("没标/标错来源 ✗" if got else "答案本身不对 ✗"))}
            fm = "" if ok else ("张冠李戴" if miscited else ("引用缺失" if got else "答案错误"))
            return RunOutcome(ok, 1.0 if ok else 0.0, fm, f"cited={cited}", trace)
        rep.add(run_case_ntimes(c["name"], run_once, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 5. 稳定性浸泡（离线真跑：DB / 队列 / 画布）
# ════════════════════════════════════════════════════════════════════════
def run_stability_soak(k: int = 1) -> SuiteReport:
    rep = SuiteReport("稳定·浸泡压测")

    def case_db_soak():
        steps: list[str] = []
        db, d = _fresh_db()
        try:
            from concurrent.futures import ThreadPoolExecutor
            THREADS, ROUNDS = 8, 40
            errors: list[str] = []
            lat: list[float] = []
            _log(steps, f"DB 浸泡开跑：{THREADS} 线程 × {ROUNDS} 轮，每轮=建会话+写2消息+读列表+读消息（共 {THREADS*ROUNDS} 轮）")

            def one_round(i):
                t0 = time.time()
                try:
                    cid = f"soak_{i}_{uuid.uuid4().hex[:6]}"
                    db.create_conversation(cid, f"u{i % 4}", f"浸泡{i}")
                    db.create_message(cid, "user", f"问题{i}")
                    db.create_message(cid, "assistant", f"回答{i}", status="complete")
                    db.list_conversations(f"u{i % 4}", limit=10)
                    ms = db.get_messages(cid)
                    if len(ms) != 2:
                        errors.append(f"轮{i}: 消息数{len(ms)}≠2")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"轮{i}: {type(e).__name__}")
                lat.append((time.time() - t0) * 1000)

            t0 = time.time()
            with ThreadPoolExecutor(max_workers=THREADS) as ex:
                list(ex.map(one_round, range(THREADS * ROUNDS)))
            wall = time.time() - t0
            lat.sort()
            p50 = lat[len(lat) // 2] if lat else 0
            p95 = lat[int(len(lat) * 0.95)] if lat else 0
            tput = len(lat) / wall if wall > 0 else 0
            _log(steps, f"完成：总墙钟 {wall:.2f}s，吞吐 {tput:.0f} 轮/秒，延迟 p50={p50:.0f}ms p95={p95:.0f}ms max={lat[-1]:.0f}ms")
            _log(steps, f"错误数：{len(errors)}" + (f"，样例：{errors[:3]}" if errors else "（零错误）"))
            ok = not errors and p95 < 1500
            trace = {"场景": f"{THREADS}线程×{ROUNDS}轮 DB 混合读写浸泡", "执行日志": steps,
                     "吞吐": f"{tput:.0f} 轮/秒", "p95延迟": f"{p95:.0f}ms", "错误数": len(errors),
                     "判定": "浸泡零错误且延迟健康 ✓" if ok else ("浸泡出错 ✗" if errors else "p95 延迟过高 ✗")}
            return RunOutcome(ok, 1.0 if ok else 0.0,
                              "" if ok else ("浸泡出错" if errors else "延迟过高"),
                              f"{len(lat)}轮 0错={not errors} p95={p95:.0f}ms", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    def case_queue_churn():
        steps: list[str] = []
        import os
        import tempfile
        old = os.environ.get("HASHMM_DATA_DIR")
        os.environ["HASHMM_DATA_DIR"] = tempfile.mkdtemp(prefix="hmm_soakq_")
        try:
            from hashmm.agent import dispatch as dq
            N = 100
            _log(steps, f"队列搅拌：{N} 轮 create→poll→complete 交错 3 个 runner")
            fails = 0
            t0 = time.time()
            for i in range(N):
                r = f"r{i % 3}"
                tid = dq.create_task(r, "soak", {"i": i})
                got = dq.poll(r)
                if not got or got.get("task_id") != tid:
                    fails += 1
                    continue
                if not dq.complete(tid, True, f"done{i}"):
                    fails += 1
            wall = time.time() - t0
            st = dq.stats()
            _log(steps, f"完成：墙钟 {wall:.2f}s（{N/wall:.0f} 轮/秒），失败 {fails}；终态统计 {st}")
            done_all = (st.get("done") or 0) >= N - fails
            ok = fails == 0 and done_all
            trace = {"场景": f"{N} 轮队列全生命周期搅拌（3 runner 交错）", "执行日志": steps,
                     "失败轮数": fails, "终态统计": st,
                     "判定": "队列高频搅拌零失败、终态一致 ✓" if ok else "队列搅拌有失败/终态不一致 ✗"}
            return RunOutcome(ok, 1.0 if ok else max(0.0, 1 - fails / N),
                              "" if ok else "队列搅拌失败", f"{N}轮 失败{fails}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})
        finally:
            if old is None:
                os.environ.pop("HASHMM_DATA_DIR", None)
            else:
                os.environ["HASHMM_DATA_DIR"] = old

    def case_canvas_determinism():
        steps: list[str] = []
        try:
            from hashmm.agent import team as T
            roles = [{"role": "研究员", "task": "收集"}, {"role": "写作员", "task": "成文"}]
            N = 200
            lens = set()
            t0 = time.time()
            for _ in range(N):
                lens.add(len(T._canvas_html("稳定性目标", roles, "parallel")))
            wall = (time.time() - t0) * 1000
            _log(steps, f"画布重复渲染 {N} 次：总耗时 {wall:.0f}ms（均 {wall/N:.2f}ms/次），输出长度集合 {lens}")
            ok = len(lens) == 1
            trace = {"场景": f"画布 {N} 次重复渲染确定性（输出必须逐字节稳定）", "执行日志": steps,
                     "判定": "渲染确定性稳定 ✓" if ok else f"输出漂移（{len(lens)} 种长度）✗"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "渲染漂移",
                              f"{N}次 长度种数={len(lens)}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    rep.add(run_case_ntimes("稳定-DB浸泡8线程×40轮(零错+p95)", case_db_soak, max(1, k)))
    rep.add(run_case_ntimes("稳定-队列100轮搅拌终态一致", case_queue_churn, max(1, k)))
    rep.add(run_case_ntimes("稳定-画布200次渲染确定性", case_canvas_determinism, max(1, k)))
    return rep


# ════════════════════════════════════════════════════════════════════════
# 6. RAG 检索准确性（融合排序 / 相关性过滤 / 关键词命中——确定性离线，直击"检索准不准"）
# ════════════════════════════════════════════════════════════════════════
def run_rag_retrieval(k: int = 2) -> SuiteReport:
    """诉求："chat 时候 RAG 相关文档检索的准确性"。这里测检索链里**决定命中质量**的三块确定性原语：
      · rrf_fuse —— 稠密+稀疏两路排名的 RRF 融合：双榜命中必须压过单榜、综合排名正确、去重。
      · post_filter —— 相关性过滤：远低于最高分的弱上下文被丢弃（喂弱上下文正是幻觉来源），
        但绝不返回空、且尊重 min/max。
      · BM25 —— 关键词精确检索：含查询专名/数字的文档必须排在干扰文档之前（rank_bm25 缺失则 skip）。
    hit@k 风格断言，全程执行日志。"""
    rep = SuiteReport("质量·RAG检索准确性")

    class _R:
        __slots__ = ("text", "score", "doc_id")
        def __init__(self, text, score=0.0, doc_id=""):
            self.text = text; self.score = score; self.doc_id = doc_id or text

    # ── rrf 融合：双榜优先 + 综合排名 + 去重 ──
    def case_rrf_fuse():
        steps: list[str] = []
        try:
            from hashmm.retrieval.advanced import rrf_fuse
            # dense=[X,Y,Z], sparse=[Y,Z,W]：Y/Z 双榜，X 仅稠密、W 仅稀疏
            dense = [_R("X"), _R("Y"), _R("Z")]
            sparse = [_R("Y"), _R("Z"), _R("W")]
            fused = rrf_fuse([dense, sparse])
            order = [r.text for r in fused]
            _log(steps, f"稠密榜 [X,Y,Z] + 稀疏榜 [Y,Z,W] → 融合序 {order}")
            # 断言：Y 综合最高（稀疏rank0+稠密rank1）应居首；Y/Z（双榜）都在 X/W（单榜）之前；去重
            top_ok = order[0] == "Y"
            double_before_single = order.index("Y") < order.index("X") and order.index("Z") < order.index("W")
            dedup_ok = len(order) == len(set(order)) == 4
            _log(steps, f"双榜命中Y居首={top_ok}；双榜(Y,Z)先于单榜(X,W)={double_before_single}；去重4条={dedup_ok}")
            ok = top_ok and double_before_single and dedup_ok
            trace = {"场景": "RRF 融合稠密+稀疏两路排名（RAG 混合检索核心）", "执行日志": steps,
                     "判定": "融合排序正确：双榜压单榜、综合最高居首、去重 ✓" if ok else "融合排序有误 ✗（查 rrf_fuse）"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "融合排序错误",
                              f"序={order}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    # ── post_filter：丢弱上下文但不返回空 ──
    def case_post_filter():
        steps: list[str] = []
        try:
            from hashmm.retrieval.advanced import post_filter
            docs = [_R("强", 0.9), _R("中", 0.5), _R("弱", 0.2), _R("噪", 0.05)]
            kept = post_filter(docs, rel_threshold=0.3, min_keep=1, max_keep=8)
            texts = [r.text for r in kept]
            _log(steps, f"分数 [强0.9,中0.5,弱0.2,噪0.05] 阈值0.3×top(0.9)=0.27 → 保留 {texts}")
            # 0.27 截断：强(0.9)、中(0.5)保留；弱(0.2)、噪(0.05)丢弃
            keep_ok = "强" in texts and "中" in texts and "弱" not in texts and "噪" not in texts
            # 全弱也不返回空（min_keep 兜底）
            allweak = post_filter([_R("a", 0.01), _R("b", 0.005)], rel_threshold=0.3, min_keep=1)
            nonempty_ok = len(allweak) >= 1
            # max_keep 上限
            many = post_filter([_R(str(i), 1.0) for i in range(20)], rel_threshold=0.1, max_keep=8)
            cap_ok = len(many) == 8
            _log(steps, f"弱上下文丢弃={keep_ok}；全弱不返空={nonempty_ok}；max_keep上限=8→{cap_ok}")
            ok = keep_ok and nonempty_ok and cap_ok
            trace = {"场景": "相关性过滤：丢弃远低于 top 的弱上下文（防幻觉）但不返回空",
                     "执行日志": steps,
                     "判定": "过滤策略正确 ✓" if ok else "过滤策略有误 ✗（查 post_filter）"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "过滤策略错误",
                              f"保留={texts}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    # ── BM25 关键词命中（rank_bm25 缺失则 skip）──
    def case_bm25_hit():
        steps: list[str] = []
        try:
            try:
                import rank_bm25  # noqa: F401
            except Exception:
                return RunOutcome(True, 0.0, "", "rank_bm25 未安装，BM25 检索跳过",
                                  {"判定": "skip", "执行日志": ["环境无 rank_bm25，跳过关键词检索用例"]})
            from hashmm.api.retrieval_pipeline import BM25Index
            idx = BM25Index()
            docs = [
                "跨模态哈希检索通过学习二进制码实现高效相似度搜索",
                "今天天气不错适合出门散步喝咖啡",
                "数据库的备份策略与容灾方案设计",
            ]
            idx.add(docs, [{"text": d, "doc_id": f"d{i}"} for i, d in enumerate(docs)])
            res = idx.search("跨模态哈希检索", top_k=3)
            top_text = res[0].text if res else ""
            _log(steps, f"检索『跨模态哈希检索』→ Top1={top_text[:30]!r}（应命中第0篇哈希文档）")
            ok = bool(res) and "哈希" in top_text
            trace = {"场景": "BM25 关键词检索：含查询专名的文档排首", "执行日志": steps,
                     "判定": "关键词命中正确 ✓" if ok else "关键词检索未命中 ✗"}
            return RunOutcome(ok, 1.0 if ok else 0.0, "" if ok else "关键词未命中",
                              f"top={top_text[:20]}", trace)
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return RunOutcome(False, 0.0, "异常", f"{type(e).__name__}: {e}", {"执行日志": steps})

    rep.add(run_case_ntimes("检索-RRF融合双榜优先(hit@k)", case_rrf_fuse, max(1, k)))
    rep.add(run_case_ntimes("检索-相关性过滤丢弱不返空", case_post_filter, max(1, k)))
    rep.add(run_case_ntimes("检索-BM25关键词命中(需rank_bm25)", case_bm25_hit, max(1, k)))
    return rep
