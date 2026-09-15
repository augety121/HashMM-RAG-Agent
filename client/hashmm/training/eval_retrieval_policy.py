"""hashmm/training/eval_retrieval_policy.py — 训好的检索策略「量化 + 可诊断」端到端评测（带规则式基线 A/B）。

为什么需要它（这是整套方案缺的一块地基）：
  · infer_lora 只看「单题、模型是否吐出 <think>/<search>/<answer> 标签」——是定性自检；
  · run_trained_retrieval 只跑「单题、打印逐跳 trace + 真实证据」——也是定性 demo；
  · 都回答不了「这个训好的模型接上你真实知识库后，到底好不好？比不训（规则式判停）强多少？」
本脚本把它补上：在你的金标准集上**批量**跑「训好的策略 → 真实 BGE-M3/FAISS 检索」，
算出一组可度量指标，并用**同一套检索后端、把策略换成规则式判停**跑一遍当基线，给出 A/B 差值。

V103.67 升级（针对 V103.66 首跑出现「两边全打平/全 0」的退化结果，做了三处修正，目的：让评测能「看见真相」）：
  1) 「发起检索率」原先硬判 trace 首跳 action=="search"，但回路 trace 的动作标签不一定叫这个名，
     会假性归 0。改为两个更稳的口径：① retrieval_happened（来源数>0 或 hops>=1，物理上是否检索了）；
     ② policy_issued_search（扫描所有跳，是否出现含 "search" 的动作）。并在报告里**打印实际观察到的
     动作标签**，免得再靠猜。
  2) 「证据含答案率」原先用整句答案子串包含，真实 QA 金标准里整句几乎不可能原样命中 chunk，必然趋 0。
     新增连续指标 answer_token_recall（答案的「内容 token」——中文字符二元组 + 英文词——在证据里的召回比例），
     不会卡在 0；并保留严格子串命中 answer_in_evidence 作次要参考；金标准若带支持段（supporting/contexts 等）
     再算 gold_context_token_recall（衡量检索是否把金标准的证据段捞回来了，是更硬的检索质量信号）。
  3) 新增 --dump N：对前 N 题打印「问题 / 金标准答案 / 回路 trace 动作 / 命中来源字段与片段 / 模型在
     『正确模板 + 真实证据』下的原始输出与解析决策」。这是定位退化的最快手段：能当场看出模型到底吐了什么、
     检索到了什么、来源 dict 的真实字段名是什么。

诚实声明（务必先读，避免误解指标含义）：
  · 本脚本量化的是**检索侧**质量——也就是「训好的策略」真正负责的那部分：要不要检索、检索到的
    证据里有没有答案/支持点。它**不**评「最终自然语言答案对不对」——因为 AgenticRetriever 返回的是
    检索结果（trace + 真实 sources + 置信度），最终答案是 streaming/agent 那层拿证据再生成的。
    「最终答案正确率」需要接答案生成器，属于 evaluation/ 框架（llm_judge），不在本脚本范围。
  · answer_in_evidence（严格子串）/ *_token_recall（token 重合）都是**无需额外 LLM、可复跑的 grounding 代理**，
    不是语义判分；它们衡量「检索是否把含答案/含支持段的证据捞回来了」。要更准的语义判分接 evaluation/llm_judge。
  · source_recall@k 只在金标准条目带来源文件名（source/filename/doc/file 字段）时才计算；没有则显示 n/a，属正常。

用法（在你的 AutoDL 服务器，项目根目录；务必带索引目录）：
    HASH_INDEX_DIR=/root/autodl-tmp/data/vector_index \\
    CUDA_VISIBLE_DEVICES=0 python -m hashmm.training.eval_retrieval_policy \\
        --model /root/autodl-tmp/models/Qwen2.5-7B-Instruct \\
        --lora  /root/autodl-tmp/models/qwen2.5-7b-hashmm-all-lora \\
        --golden /root/autodl-tmp/data/golden/golden_cases.json \\
        --limit 30 --top_k 5 --max_hops 3 --dump 3 \\
        --out /root/autodl-tmp/data/eval/eval_report.json

说明：
  · --limit N：只评前 N 条（评测要逐题生成，较慢；先用 30~50 条看趋势，定稿再全量）。
  · --dump N：先打印前 N 题的逐题诊断（强烈建议先看，再看汇总表）。
  · 默认同时跑「训好的策略」和「规则式基线」两遍并给差值；只想看策略可加 --no_baseline。
  · 无 GPU 时策略不可用，但**基线仍能跑**（规则式判停只用检索后端）；dump 的「模型原始输出」段会跳过。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from hashmm.training.searchr1_policy import decision_from_model_text

logger = logging.getLogger("hashmm.training.eval_retrieval_policy")


# ----------------------------- 纯函数（可单测，不依赖 GPU/索引） -----------------------------

# 回路 sources 里正文可能用的字段名（不同检索层 key 不一，全试一遍，避免取空导致指标假 0）。
_TEXT_KEYS = ("text", "content", "snippet", "chunk", "chunk_text",
              "passage", "page_content", "body", "document", "raw")
# 金标准里「支持段/证据段」可能用的字段名。
_CONTEXT_KEYS = ("supporting", "support", "evidence", "contexts", "context",
                 "passages", "gold_context", "gold_contexts")

# 内容 token 化用：跳过的中文高频虚词（单字）与英文停用词，降低「靠虚词刷高召回」的噪声。
_CJK = "\u4e00-\u9fff\u3400-\u4dbf"
_STOP_CHARS = set("的了是在和与及或之其为以于对从到把被让向且而也都就还又很有这那个我你他她它们")
_STOP_WORDS = {"the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for",
               "on", "at", "with", "as", "by", "that", "this", "be", "it"}


def _normalize(s: Any) -> str:
    """轻量归一化：转字符串、去首尾空白、英文小写、压缩内部空白。CJK 不受影响。"""
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\u3000", " ").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _content_tokens(s: Any) -> set:
    """把文本拆成「内容 token」集合：中文按字符二元组（bigram）、英文/数字按词。

    中文用二元组而非单字，是为了避免「单字重合率虚高」（任何两段中文都会共享大量常用字）；
    bigram 能粗略捕捉「词/短语级」重合，是 ROUGE 式中文近似的常用做法。纯函数、不抛错、无外部依赖。
    """
    if not s:
        return set()
    s = str(s).lower()
    toks: set = set()
    for run in re.findall(f"[{_CJK}]+", s):
        if len(run) == 1:
            if run not in _STOP_CHARS:
                toks.add(run)
        else:
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if not (bg[0] in _STOP_CHARS and bg[1] in _STOP_CHARS):
                    toks.add(bg)
    for w in re.findall(r"[a-z0-9]+", s):
        if (len(w) >= 2 or w.isdigit()) and w not in _STOP_WORDS:
            toks.add(w)
    return toks


def _token_recall(evidence_raw: str, target_raw: str) -> Optional[float]:
    """target 的内容 token 在 evidence 里被覆盖的比例（0~1）。target 无内容 token→None（不计）。"""
    tt = _content_tokens(target_raw)
    if not tt:
        return None
    ev = _content_tokens(evidence_raw)
    if not ev:
        return 0.0
    return round(len(tt & ev) / len(tt), 4)


def _first_answer(reference_answer: Any) -> str:
    """与 build_sft_data 同源：reference_answer 可能是字符串或列表，取第一个非空。"""
    if isinstance(reference_answer, (list, tuple)):
        for a in reference_answer:
            if str(a).strip():
                return str(a).strip()
        return ""
    return str(reference_answer or "").strip()


# ============ 答对判定（判官）：给一个能对外说的"准确率"，不被语序/单位/详略惩罚 ============
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _extract_numbers(s: Any) -> set:
    """抽取文本里的数字（去千分位逗号、规范化），返回数字串集合。用于数值型答案判对。纯函数。"""
    out: set = set()
    for n in _NUM_RE.findall(str(s or "")):
        n2 = n.replace(",", "").rstrip(".")
        if not n2 or n2 == ".":
            continue
        try:
            out.add(str(float(n2)) if "." in n2 else str(int(n2)))
        except ValueError:
            out.add(n2)
    return out


def _answer_correct_heuristic(model_answer: Any, gold: Any) -> Optional[bool]:
    """纯启发式判对（无外部依赖、可单测）：
    1) 金标准含较大数字（整数部分≥4 位，多为金额/股数）→ 模型答案须含其中最大的那个数字才算对；
       问数字却没给出该数字 → 直接判错。
    2) 否则按规范化子串双向包含（较短串≥4 字且被较长串包含）→ 对。
    3) 再兜底：内容 token 召回≥0.6 → 对。
    """
    if not model_answer or not gold:
        return None
    gn = _extract_numbers(gold)
    big = [n for n in gn if len(n.split(".")[0]) >= 4]
    if big:
        mn = _extract_numbers(model_answer)
        largest = max(big, key=lambda x: (len(x.split(".")[0]), x))
        return bool(largest in mn or set(big).issubset(mn))
    gc = _normalize(gold).replace(" ", "")
    mc = _normalize(model_answer).replace(" ", "")
    if gc and mc:
        shorter, longer = (gc, mc) if len(gc) <= len(mc) else (mc, gc)
        if len(shorter) >= 4 and shorter in longer:
            return True
    tr = _token_recall(model_answer, gold)
    return bool(tr is not None and tr >= 0.6)


_JUDGE_LLM_PROMPT = (
    "你是严格的阅卷老师。判断「模型答案」是否答对了「问题」，以「标准答案」为准。\n"
    "只要模型答案包含标准答案的关键事实/数字即算对；措辞、语序、单位写法、详略不同不扣分；"
    "关键数字/事实错误或答非所问即算错。\n"
    "只输出一个字：对 或 错。\n\n"
    "问题：{q}\n标准答案：{gold}\n模型答案：{ans}\n判定："
)


def _make_judge(mode: str):
    """返回 judge(question, model_answer, gold)->Optional[bool]。
    mode: none=不判；heuristic=纯启发式（默认，零成本）；llm=用项目配置的 LLM 逐题判（失败自动回退启发式）。"""
    if mode == "none":
        return lambda q, ans, gold: None
    if mode == "heuristic":
        return lambda q, ans, gold: _answer_correct_heuristic(ans, gold)

    state = {"llm": None, "ready": False}  # llm 模式：照搬 gen_golden_from_corpus 的 LLM 获取

    def _ensure():
        if state["ready"]:
            return
        state["ready"] = True
        try:
            from hashmm.api.core.services import ServiceRegistry
            if not getattr(ServiceRegistry, "_initialized", False):
                ServiceRegistry.init_fast()
            try:
                if not getattr(ServiceRegistry, "_heavy_initialized", False):
                    ServiceRegistry.init_heavy()
            except Exception:
                pass
            state["llm"] = lambda p: ServiceRegistry.call_llm(p)
        except Exception as e:
            logger.warning("[judge] LLM 不可用，回退启发式：%s", e)
            state["llm"] = None

    def judge(q, ans, gold):
        if not ans or not gold:
            return None
        _ensure()
        if state["llm"] is None:
            return _answer_correct_heuristic(ans, gold)
        try:
            t = str(state["llm"](_JUDGE_LLM_PROMPT.format(q=q, gold=gold, ans=ans)) or "").strip()
            if t and t[0] == "对":
                return True
            if t and t[0] == "错":
                return False
            return _answer_correct_heuristic(ans, gold)  # 没给干净对/错 → 回退
        except Exception as e:
            logger.debug("[judge] LLM 调用失败，回退启发式：%s", e)
            return _answer_correct_heuristic(ans, gold)

    return judge


_JUDGE_FN = None  # 由 main() 按 --judge 设置；eval_one 引用


def _question_of(case: dict) -> str:
    return (case.get("query") or case.get("question") or "").strip()


def _answer_of(case: dict) -> str:
    return _first_answer(case.get("reference_answer", case.get("answer")))


def _keypoints_of(case: dict) -> list[str]:
    """金标准里若带关键点（casegen 的「关键点」），取出来做覆盖率。兼容多种字段名。"""
    for key in ("key_points", "keypoints", "points", "key_facts", "aspects"):
        v = case.get(key)
        if isinstance(v, (list, tuple)) and v:
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str) and v.strip():
            parts = re.split(r"[；;、\n]+", v)
            return [p.strip() for p in parts if p.strip()]
    return []


def _contexts_of(case: dict) -> str:
    """金标准里若带支持段/证据段正文，拼成一段用于 gold_context_token_recall。没有返回空串。"""
    for key in _CONTEXT_KEYS:
        v = case.get(key)
        if isinstance(v, (list, tuple)) and v:
            return " ".join(str(x) for x in v if str(x).strip())
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _source_of(case: dict) -> str:
    """金标准里若带来源文件名，取出来算 source_recall@k；没有就返回空串（该项不计）。"""
    for key in ("source", "filename", "doc", "file", "source_file", "doc_id"):
        v = case.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (list, tuple)) and v and str(v[0]).strip():
            return str(v[0]).strip()
    return ""


def _contains(haystack_norm: str, needle_norm: str) -> bool:
    """归一化后的子串包含判断（严格 grounding 代理）。空 needle 视为无法判定→False。"""
    if not needle_norm:
        return False
    return needle_norm in haystack_norm


def _src_text(s: Any) -> str:
    """从单条 source 里按多字段名取正文（取不到返回空串）。"""
    if not isinstance(s, dict):
        return str(s)
    for k in _TEXT_KEYS:
        v = s.get(k)
        if v:
            return str(v)
    return ""


def _evidence_raw(result: dict) -> str:
    """把回路返回的真实 sources 正文拼成一段（原文，未归一化），供 token 召回用。"""
    parts = [_src_text(s) for s in (result.get("sources") or [])]
    return " ".join(p for p in parts if p)


def _evidence_text(result: dict) -> str:
    """归一化后的证据文本，供严格子串包含用。"""
    return _normalize(_evidence_raw(result))


def _trace_actions(result: dict) -> list[str]:
    """取出 trace 每跳的 action 标签（用于诊断真实标签名 + 判断是否发起过 search）。"""
    return [str((h or {}).get("action", "")) for h in (result.get("trace") or []) if isinstance(h, dict)]


def eval_one(result: dict, case: dict, top_k: int) -> dict:
    """对单条结果算指标。result 来自 AgenticRetriever.retrieve(q)。纯函数、不抛错。"""
    sources = result.get("sources") or []
    n_hops = result.get("n_hops", len(result.get("trace") or []))
    evid_norm = _evidence_text(result)
    evid_raw = _evidence_raw(result)
    actions = _trace_actions(result)

    answer = _answer_of(case)
    answer_norm = _normalize(answer)
    kps = [_normalize(k) for k in _keypoints_of(case)]
    contexts = _contexts_of(case)
    gold_source = _normalize(_source_of(case))
    src_names = [_normalize(s.get("filename", s.get("source", ""))) for s in sources[:top_k]
                 if isinstance(s, dict)]

    # 严格子串命中（次要参考）
    answer_in_evidence: Optional[bool] = _contains(evid_norm, answer_norm) if answer_norm else None
    # 连续 token 召回（主指标，不会卡 0）
    answer_token_recall: Optional[float] = _token_recall(evid_raw, answer) if answer else None
    gold_context_token_recall: Optional[float] = _token_recall(evid_raw, contexts) if contexts else None
    # 模型最终答案（仅 searchfirst 驱动有）对金标准答案的 token 召回——端到端正确率的字符串近似（非语义）
    model_answer = result.get("answer")
    model_answer_token_recall: Optional[float] = (
        _token_recall(model_answer, answer) if (model_answer and answer) else None)
    answer_correct: Optional[bool] = (
        _JUDGE_FN(_question_of(case), model_answer, answer)
        if (_JUDGE_FN is not None and model_answer and answer) else None)

    # 大厂级 RAG 指标（RAGAS 风格，启发式）：忠实度 + 上下文相关性
    faithfulness: Optional[float] = _faithfulness(model_answer, evid_raw) if model_answer else None
    context_relevance: Optional[float] = _context_relevance(_question_of(case), evid_raw) if evid_raw else None

    if kps:
        hit = sum(1 for k in kps if _contains(evid_norm, k))
        keypoint_coverage: Optional[float] = round(hit / len(kps), 4)
    else:
        keypoint_coverage = None
    source_recall: Optional[bool] = (gold_source in src_names) if gold_source else None

    return {
        "question": _question_of(case),
        "retrieval_happened": (len(sources) > 0) or (n_hops or 0) >= 1,
        "policy_issued_search": any("search" in a.lower() for a in actions),
        "trace_actions": actions,
        "n_hops": n_hops,
        "stopped_reason": result.get("stopped_reason"),
        "n_sources": len(sources),
        "confidence": result.get("confidence"),
        "answer_in_evidence": answer_in_evidence,
        "answer_token_recall": answer_token_recall,
        "gold_context_token_recall": gold_context_token_recall,
        "model_answer_token_recall": model_answer_token_recall,
        "answer_correct": answer_correct,
        "faithfulness": faithfulness,
        "context_relevance": context_relevance,
        "keypoint_coverage": keypoint_coverage,
        "source_recall@k": source_recall,
    }


def _rate(rows: list[dict], key: str) -> Optional[float]:
    """对某个布尔指标算命中率，只统计非 None 的条目；全 None 返回 None。"""
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return round(sum(1 for v in vals if v) / len(vals), 4)


def _mean(rows: list[dict], key: str) -> Optional[float]:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def aggregate(rows: list[dict]) -> dict:
    """把每条指标汇总成一组聚合分数 + 停止原因分布 + 观察到的动作标签。"""
    reasons: dict[str, int] = {}
    actions_seen: dict[str, int] = {}
    for r in rows:
        k = str(r.get("stopped_reason"))
        reasons[k] = reasons.get(k, 0) + 1
        for a in (r.get("trace_actions") or []):
            actions_seen[a] = actions_seen.get(a, 0) + 1
    return {
        "n_cases": len(rows),
        "retrieval_happened_rate": _rate(rows, "retrieval_happened"),
        "policy_issued_search_rate": _rate(rows, "policy_issued_search"),
        "answer_accuracy": _rate(rows, "answer_correct"),
        "answer_in_evidence_rate": _rate(rows, "answer_in_evidence"),
        "mean_answer_token_recall": _mean(rows, "answer_token_recall"),
        "mean_gold_context_token_recall": _mean(rows, "gold_context_token_recall"),
        "mean_model_answer_token_recall": _mean(rows, "model_answer_token_recall"),
        "mean_faithfulness": _mean(rows, "faithfulness"),
        "mean_context_relevance": _mean(rows, "context_relevance"),
        "mean_keypoint_coverage": _mean(rows, "keypoint_coverage"),
        "source_recall@k": _rate(rows, "source_recall@k"),
        "mean_hops": _mean(rows, "n_hops"),
        "mean_n_sources": _mean(rows, "n_sources"),
        "stopped_reason_dist": reasons,
        "trace_actions_seen": actions_seen,
    }


def _faithfulness(model_answer, evidence) -> Optional[float]:
    """忠实度(RAGAS 风格)：模型答案有多少被检索证据支撑——越高越不像脑补。范围 0~1。
    启发式用 token 召回（答案被证据覆盖的比例）；要更准可在外部用 LLM 逐句判，但本指标
    足以做趋势对比与回归监控（大厂做法：每次改动盯它别掉）。"""
    if not model_answer or not evidence:
        return None
    return round(_token_recall(evidence, model_answer) or 0.0, 4)


def _context_relevance(question, evidence) -> Optional[float]:
    """上下文相关性(RAGAS 风格)：检索证据覆盖了多少问题里的关键词——越高说明检索越相关。0~1。"""
    if not question or not evidence:
        return None
    return round(_token_recall(evidence, question) or 0.0, 4)


def _fmt_pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _fmt_num(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def format_report(trained: Optional[dict], baseline: Optional[dict],
                  trained_label: str = "训好的策略", baseline_label: str = "规则式基线") -> str:
    """打印对照表：训好的策略 vs 规则式基线，关键率给差值。"""
    def col(agg, key, pct=True):
        if agg is None:
            return "—"
        if key == "n_cases":
            return str(int(agg.get(key) or 0))
        return _fmt_pct(agg.get(key)) if pct else _fmt_num(agg.get(key))

    def delta(key):
        if not trained or not baseline:
            return ""
        a, b = trained.get(key), baseline.get(key)
        if a is None or b is None:
            return ""
        d = (a - b) * 100
        return f"  ({'+' if d >= 0 else ''}{d:.1f}pt)"

    rows_def = [
        ("样本数 n_cases", "n_cases", False),
        ("检索发生率(物理)", "retrieval_happened_rate", True),
        ("策略发起search率", "policy_issued_search_rate", True),
        ("✦答对率(判官,主)", "answer_accuracy", True),
        ("答案token召回(主,均)", "mean_answer_token_recall", True),
        ("支持段token召回(均)", "mean_gold_context_token_recall", True),
        ("模型答案token召回(均)", "mean_model_answer_token_recall", True),
        ("✦忠实度(防脑补,均)", "mean_faithfulness", True),
        ("上下文相关性(均)", "mean_context_relevance", True),
        ("证据含答案率(严格)", "answer_in_evidence_rate", True),
        ("关键点覆盖率(均)", "mean_keypoint_coverage", True),
        ("来源召回@k", "source_recall@k", True),
        ("平均跳数", "mean_hops", False),
        ("平均来源条数", "mean_n_sources", False),
    ]
    lines = []
    lines.append("=" * 72)
    lines.append("检索策略端到端评测（检索侧；非最终答案正确率，含义见脚本顶部声明）")
    lines.append("-" * 72)
    lines.append(f"{'指标':<24}{trained_label:>16}{baseline_label:>16}")
    lines.append("-" * 72)
    for label, key, pct in rows_def:
        t = col(trained, key, pct)
        b = col(baseline, key, pct)
        lines.append(f"{label:<24}{t:>16}{b:>16}{delta(key) if pct else ''}")
    lines.append("-" * 72)
    if trained:
        lines.append(f"训练策略·停止原因分布：{trained.get('stopped_reason_dist')}")
        lines.append(f"训练策略·观察到的 trace 动作标签：{trained.get('trace_actions_seen')}")
    if baseline:
        lines.append(f"规则基线·停止原因分布：{baseline.get('stopped_reason_dist')}")
        lines.append(f"规则基线·观察到的 trace 动作标签：{baseline.get('trace_actions_seen')}")
    lines.append("=" * 72)
    return "\n".join(lines)


# ----------------------------- 真机执行部分（需索引/可选 GPU） -----------------------------

def _load_golden(path: str) -> list[dict]:
    """读金标准 JSON：兼容 {"cases":[...]} 或裸列表；只保留有 query+reference_answer 的。"""
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases", data) if isinstance(data, dict) else data
    out = []
    for c in (cases or []):
        if not isinstance(c, dict):
            continue
        if _question_of(c) and _answer_of(c):
            out.append(c)
    return out


def _make_kb_search_fn(top_k: int) -> Callable[[str], list]:
    """与 run_trained_retrieval 完全一致的检索后端包装（已验证可用，不臆造接口）。"""
    from hashmm.retriever_bridge import kb_search_bridge

    def search_fn(subquery: str) -> list:
        try:
            out = kb_search_bridge({"query": subquery, "top_k": top_k}) or {}
            results = out.get("results", []) or []
            norm = []
            for r in results:
                norm.append({
                    "text": r.get("content", r.get("text", "")),
                    "filename": r.get("filename", r.get("source", "")),
                    "page": r.get("page", -1),
                    "score": r.get("score", 0.0),
                })
            return norm
        except Exception as e:
            logger.warning("kb_search failed: %s", e)
            return []
    return search_fn


def _run_set(label: str, cases: list[dict], search_fn, llm_fn, max_hops: int, top_k: int) -> list[dict]:
    """对整个金标准集跑一遍回路，逐题算指标。单题异常不影响整体。"""
    from hashmm.retrieval.agentic import AgenticRetriever
    rows = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        q = _question_of(case)
        try:
            retriever = AgenticRetriever(search_fn, llm_fn=llm_fn, max_hops=max_hops)
            result = retriever.retrieve(q)
        except Exception as e:
            logger.warning("[%s] 第 %d 题检索异常，记为未触发：%s", label, i, e)
            result = {"trace": [], "sources": [], "n_hops": 0, "stopped_reason": "error", "confidence": None}
        rows.append(eval_one(result, case, top_k))
        if i % 10 == 0:
            print(f"  [{label}] 进度 {i}/{len(cases)}  用时 {time.time() - t0:.0f}s")
    print(f"  [{label}] 完成 {len(cases)} 题，用时 {time.time() - t0:.0f}s")
    return rows


def _run_set_searchfirst(label: str, cases: list[dict], search_fn, policy, max_hops: int, top_k: int) -> list[dict]:
    """用「原生 Search-R1 驱动」跑整集：模型自己发 <search> → 打真实检索 → 注入真证据 → 续写。

    这是 tag 训练模型的正确驱动方式（对比 _run_set 的 AgenticRetriever JSON 裁判桥）。单题异常不影响整体。
    """
    rows = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        q = _question_of(case)
        try:
            result = policy.run_search_loop(q, search_fn, max_hops=max_hops, top_k=top_k)
            if result is None:
                result = {"trace": [], "sources": [], "n_hops": 0, "stopped_reason": "policy_unavailable",
                          "confidence": None, "answer": None}
        except Exception as e:
            logger.warning("[%s] 第 %d 题驱动异常：%s", label, i, e)
            result = {"trace": [], "sources": [], "n_hops": 0, "stopped_reason": "error",
                      "confidence": None, "answer": None}
        rows.append(eval_one(result, case, top_k))
        if i % 10 == 0:
            print(f"  [{label}] 进度 {i}/{len(cases)}  用时 {time.time() - t0:.0f}s")
    print(f"  [{label}] 完成 {len(cases)} 题，用时 {time.time() - t0:.0f}s")
    return rows


def _get_answer_llm():
    """取 deepseek 合成函数（与 --judge llm 同源）。失败返回 None（hybrid 退化为只用 7B 答案）。"""
    try:
        from hashmm.api.core.services import ServiceRegistry
        if not getattr(ServiceRegistry, "_initialized", False):
            ServiceRegistry.init_fast()
        if not getattr(ServiceRegistry, "_heavy_initialized", False):
            ServiceRegistry.init_heavy()
        return lambda p: ServiceRegistry.call_llm(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("[eval] 取 answer LLM（deepseek）失败：%s", e)
        return None


def _run_set_hybrid(label, cases, search_fn, policy, max_hops, top_k, answer_llm):
    """选项A（hybrid）：一次 run_search_loop 取(多跳)证据，产出两套答案以直接对照——
       · rows_7b：7B 自己写的答案（= searchfirst 那套，7B 检索强但算术弱）
       · rows_hy：同一批证据交给 deepseek 写的答案（强模型负责推理/算术）
    返回 (rows_hy, rows_7b)。一次检索两份评分，开销=一次 7B 循环 + 每题一次 deepseek。"""
    rows_7b, rows_hy = [], []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        q = _question_of(case)
        try:
            result = policy.run_search_loop(q, search_fn, max_hops=max_hops, top_k=top_k)
            if result is None:
                result = {"trace": [], "sources": [], "n_hops": 0, "stopped_reason": "policy_unavailable",
                          "confidence": None, "answer": None}
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] 第 %d 题驱动异常：%s", label, i, e)
            result = {"trace": [], "sources": [], "n_hops": 0, "stopped_reason": "error",
                      "confidence": None, "answer": None}
        rows_7b.append(eval_one(result, case, top_k))
        # 用 deepseek 基于同一批检索证据作答（只替换答案，trace/sources 保持一致以便对照）
        result_hy = dict(result)
        sources = result.get("sources") or []
        if answer_llm is not None and sources:
            evidence = "\n\n".join(f"[{j}] {s.get('text', '')}" for j, s in enumerate(sources, 1))
            prompt = (
                "你是严谨的企业知识库问答助手。只依据下面检索到的资料回答问题；"
                "需要计算时一步步算清楚，给出简洁准确、含关键数字的最终答案；资料不足就直说，不要编造。\n\n"
                f"问题：{q}\n\n检索到的资料：\n{evidence}\n\n最终答案："
            )
            try:
                result_hy["answer"] = answer_llm(prompt)
            except Exception as e:  # noqa: BLE001
                logger.warning("[%s] 第 %d 题 deepseek 作答异常：%s", label, i, e)
        rows_hy.append(eval_one(result_hy, case, top_k))
        if i % 10 == 0:
            print(f"  [{label}] 进度 {i}/{len(cases)}  用时 {time.time() - t0:.0f}s")
    print(f"  [{label}] 完成 {len(cases)} 题，用时 {time.time() - t0:.0f}s")
    return rows_hy, rows_7b


def _run_set_selfrag(label, cases, search_fn, policy, max_hops, top_k):
    """Self-RAG 评测：复用已加载 policy + search_fn 跑 self_rag_answer，产出两列对照——
       · rows_init：选项A 自评前的初答（= hybrid 那套）
       · rows_sr  ：Self-RAG（自评 + 自适应再检索 + 忠实度门控）后的最终答案
    返回 (rows_sr, rows_init)。一次检索两份评分，专证"自评闸"把忠实度/答对率提了多少。"""
    from hashmm.retrieval import self_rag as sr
    rows_sr, rows_init = [], []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        q = _question_of(case)
        try:
            r = sr.self_rag_answer(q, top_k=top_k, max_hops=max_hops,
                                   policy=policy, search_fn=search_fn)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] 第 %d 题 self_rag 异常：%s", label, i, e)
            r = None
        if r is None:
            r = {"answer": None, "initial_answer": None, "sources": [], "trace": [],
                 "rounds": 0, "stopped_reason": "selfrag_unavailable"}
        srcs = r.get("sources") or []
        nh = (r.get("rounds") or 0) + 1
        res_sr = {"answer": r.get("answer"), "sources": srcs, "trace": r.get("trace") or [],
                  "n_hops": nh, "stopped_reason": "answer", "confidence": r.get("confidence")}
        res_init = {"answer": r.get("initial_answer"), "sources": srcs, "trace": [],
                    "n_hops": nh, "stopped_reason": "answer", "confidence": None}
        rows_sr.append(eval_one(res_sr, case, top_k))
        rows_init.append(eval_one(res_init, case, top_k))
        if i % 5 == 0:
            print(f"  [{label}] 进度 {i}/{len(cases)}  用时 {time.time() - t0:.0f}s")
    print(f"  [{label}] 完成 {len(cases)} 题，用时 {time.time() - t0:.0f}s")
    return rows_sr, rows_init


def _trunc(s: str, n: int) -> str:
    s = str(s).replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def dump_cases(cases: list[dict], search_fn, policy, top_k: int, max_hops: int, n_dump: int,
               driver: str = "searchfirst") -> None:
    """逐题诊断：把回路 trace / 命中来源字段与片段 / 模型行为打出来，定位退化最快的手段。

    driver=searchfirst（默认）：直接展示**原生 Search-R1 驱动**的真实轨迹——模型自己发起的子查询、
      知识库真实返回的来源、以及模型基于真证据给出的最终答案及其对金标准的 token 召回。这正是评测打分的同款路径。
    driver=agentic：展示旧的 AgenticRetriever JSON 裁判桥结果，并额外用「训练同源模板 + 真实证据」直喂模型，
      验证模型本体是否健康（若直喂能正常吐 <search>/<answer> 而回路里却一跳就停，问题就锁定在回路↔策略接线）。
    """
    from hashmm.retrieval.agentic import AgenticRetriever
    print("\n" + "=" * 72)
    print(f"逐题诊断 dump（前 {n_dump} 题，驱动={driver}）—— 看模型到底吐了什么、检索到了什么")
    print("=" * 72)
    use_sf = (policy is not None and driver in ("searchfirst", "hybrid", "selfrag"))
    llm_fn = policy.as_llm_fn() if (policy is not None and driver == "agentic") else None
    for i, case in enumerate(cases[:n_dump], 1):
        q = _question_of(case)
        gold = _answer_of(case)
        try:
            if use_sf:
                r = policy.run_search_loop(q, search_fn, max_hops=max_hops, top_k=top_k) \
                    or {"trace": [], "sources": [], "n_hops": 0, "stopped_reason": "policy_unavailable", "answer": None}
            else:
                r = AgenticRetriever(search_fn, llm_fn=llm_fn, max_hops=max_hops).retrieve(q)
        except Exception as e:
            r = {"trace": [], "sources": [], "n_hops": 0, "stopped_reason": f"error:{e}", "answer": None}
        srcs = r.get("sources") or []
        print(f"\n[{i}] 问题：{q}")
        print(f"    金标准答案：{_trunc(gold, 140)}")
        if _contexts_of(case):
            print(f"    金标准支持段：{_trunc(_contexts_of(case), 100)}")
        print(f"    回路结果：stopped={r.get('stopped_reason')}  hops={r.get('n_hops')}  "
              f"sources={len(srcs)}  trace_actions={_trace_actions(r)}")
        if r.get("subqueries"):
            print(f"    模型发起的子查询：{['《'+str(s)+'》' for s in r.get('subqueries', [])]}")
        if srcs:
            first = srcs[0]
            keys = list(first.keys()) if isinstance(first, dict) else type(first).__name__
            print(f"    sources[0] 字段：{keys}")
            for j, s in enumerate(srcs[:top_k], 1):
                fn = s.get("filename", s.get("source", "?")) if isinstance(s, dict) else "?"
                print(f"      src{j}: {fn} | {_trunc(_src_text(s), 90)}")
            print(f"    答案token召回={_token_recall(_evidence_raw(r), gold)}  "
                  f"严格含答案={_contains(_evidence_text(r), _normalize(gold))}")
        else:
            print("    （回路未返回 sources，检查 search_fn / 索引 / 字段名）")
        if use_sf:
            # 原生驱动已给出模型基于真实证据的最终答案——直接展示并算它对金标准的 token 召回
            ma = r.get("answer")
            if ma is not None:
                print(f"    模型最终答案(基于真实检索)：{_trunc(ma, 200)!r}")
                print(f"    模型答案token召回(对金标准)={_token_recall(ma, gold)}")
            else:
                print("    模型未产出 <answer>（看 stopped 原因；可能 max_hops 或 no_action）")
        elif policy is not None:
            # agentic 模式：绕开回路接线，用训练同源模板 + 真实证据直喂模型，看模型本体行为
            ev_raw = _evidence_raw(r) or _evidence_raw({"sources": search_fn(q)})
            try:
                raw = policy._generate(q, _trunc(ev_raw, 3000), len(srcs))
                print(f"    模型原始输出(直喂 问题+真实证据)：{_trunc(raw, 320)!r}")
                print(f"    → 解析决策：{decision_from_model_text(raw)}")
            except Exception as e:
                print(f"    模型直喂生成失败：{e}")
        else:
            print("    （无 GPU/策略不可用，跳过模型输出）")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser(description="训好的检索策略量化+可诊断端到端评测（带规则式基线 A/B）")
    ap.add_argument("--model", default="/root/autodl-tmp/models/Qwen2.5-7B-Instruct")
    ap.add_argument("--lora", default="/root/autodl-tmp/models/qwen2.5-7b-hashmm-all-lora")
    ap.add_argument("--golden", required=True, help="金标准 JSON（含 query+reference_answer）")
    ap.add_argument("--limit", type=int, default=50, help="只评前 N 条（0=全部）；评测较慢，建议先小后全")
    ap.add_argument("--top_k", type=int, default=5, help="每次检索取几条")
    ap.add_argument("--max_hops", type=int, default=3, help="最多检索几轮")
    ap.add_argument("--max_new", type=int, default=128, help="策略每步最大生成 token")
    ap.add_argument("--dump", type=int, default=3, help="先打印前 N 题的逐题诊断（0=不打印）")
    ap.add_argument("--no_baseline", action="store_true", help="只评训好的策略，不跑规则式基线")
    ap.add_argument("--driver", choices=["searchfirst", "agentic", "hybrid", "selfrag"], default="searchfirst",
                    help="训练策略的驱动方式：searchfirst=原生 Search-R1 循环（模型自己发 <search>，"
                         "tag 训练模型的正确驱动，默认）；agentic=旧的 AgenticRetriever JSON 裁判桥（适合通用 LLM 裁判）；"
                         "hybrid=选项A：7B 负责(多跳)检索，deepseek 基于同一批证据作答，与 7B 单独作答对照（专证多跳算术增益）")
    ap.add_argument("--judge", choices=["none", "heuristic", "llm"], default="heuristic",
                    help="答对判定：heuristic=纯数值/规范化匹配（默认，零成本、不被语序单位惩罚）；"
                         "llm=用你配置的 LLM 逐题判对错（最稳，每题一次调用，失败自动回退启发式）；none=不判")
    ap.add_argument("--out", default="/root/autodl-tmp/data/eval/eval_report.json", help="报告输出路径")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    global _JUDGE_FN
    _JUDGE_FN = _make_judge(args.judge)
    print(f"[eval] 答对判定方式：{args.judge}"
          + ("（纯数值/规范化匹配，零成本）" if args.judge == "heuristic"
             else "（LLM 逐题判，每题一次调用）" if args.judge == "llm" else "（不判）"))

    # 1) 金标准
    print(f"[eval] 读取金标准：{args.golden}")
    cases = _load_golden(args.golden)
    if not cases:
        print("[eval] 没读到可用金标准条目（需要 query + reference_answer）。"
              "先用 gen_golden_from_corpus / casegen 生成并人工校验。")
        return
    if args.limit and args.limit > 0:
        cases = cases[:args.limit]
    print(f"[eval] 参与评测条目：{len(cases)}")

    # 2) 真实检索后端
    print("[eval] 初始化真实检索后端（BGE-M3 + FAISS + BM25）...")
    from hashmm.retriever_bridge import init_retriever
    try:
        init_retriever()
    except Exception as e:
        print(f"[eval] 检索后端初始化失败：{e}\n  请确认带了 HASH_INDEX_DIR 且索引存在。")
        return
    search_fn = _make_kb_search_fn(args.top_k)
    probe = search_fn(_question_of(cases[0]))
    print(f"[eval] 检索后端自检：首题检索到 {len(probe)} 条"
          + (f"，第一条来自 {probe[0].get('filename', '?')}" if probe else "（空，检查索引/问题）"))

    # 3) 训好的策略（无 GPU 时为 None，基线仍可跑）
    print("[eval] 加载训好的 LoRA 检索策略 ...")
    from hashmm.training.searchr1_policy import SearchR1Policy
    policy = SearchR1Policy(model_dir=args.model, lora_dir=args.lora, max_new_tokens=args.max_new)
    llm_fn = policy.as_llm_fn()

    # 3.5) 逐题诊断（强烈建议先看）
    if args.dump and args.dump > 0:
        dump_cases(cases, search_fn, policy if llm_fn is not None else None,
                   args.top_k, args.max_hops, args.dump, driver=args.driver)

    trained_label, baseline_label = "训好的策略", "规则式基线"
    trained_rows = None
    hybrid_7b_rows = None
    selfrag_init_rows = None
    if llm_fn is not None:
        if args.driver == "searchfirst":
            print("[eval] 跑【训好的策略 · 原生 Search-R1 驱动】（模型自己发 <search> → 真实检索 → 注入真证据 → 续写）...")
            trained_rows = _run_set_searchfirst("trained", cases, search_fn, policy, args.max_hops, args.top_k)
        elif args.driver == "hybrid":
            print("[eval] 跑【hybrid · 选项A】（7B 多跳检索 → deepseek 基于同一批证据作答；同时记 7B 单独作答以对照）...")
            answer_llm = _get_answer_llm()
            if answer_llm is None:
                print("[eval] ⚠ deepseek（answer LLM）不可用，hybrid 退化为只有 7B 答案，对照列将与左列相同。")
            trained_rows, hybrid_7b_rows = _run_set_hybrid(
                "hybrid", cases, search_fn, policy, args.max_hops, args.top_k, answer_llm)
            trained_label, baseline_label = "7B检索+deepseek", "7B单独作答"
        elif args.driver == "selfrag":
            print("[eval] 跑【Self-RAG · 自评+自适应再检索+忠实度门控】（对照选项A 自评前初答）...")
            trained_rows, selfrag_init_rows = _run_set_selfrag(
                "selfrag", cases, search_fn, policy, args.max_hops, args.top_k)
            trained_label, baseline_label = "Self-RAG(自评+门控)", "选项A(无自评)"
        else:
            print("[eval] 跑【训好的策略 · AgenticRetriever JSON 裁判桥】（旧路径，适合通用 LLM 裁判）...")
            trained_rows = _run_set("trained", cases, search_fn, llm_fn, args.max_hops, args.top_k)
    else:
        print("[eval] 策略模型不可用（无 GPU 或加载失败）。将只跑规则式基线。")
        if args.no_baseline:
            print("[eval] 你加了 --no_baseline 且策略不可用，无可评内容，退出。")
            return

    baseline_rows = None
    if args.driver == "hybrid":
        baseline_rows = hybrid_7b_rows   # hybrid 下对照列 = 同一批证据交给 7B 自己作答
    elif args.driver == "selfrag":
        baseline_rows = selfrag_init_rows   # selfrag 下对照列 = 选项A 自评前初答
    elif not args.no_baseline:
        print("[eval] 跑【规则式基线】（同一检索后端，llm_fn=None，回路用规则判停）...")
        baseline_rows = _run_set("baseline", cases, search_fn, None, args.max_hops, args.top_k)

    # 4) 汇总 + 报告
    trained_agg = aggregate(trained_rows) if trained_rows else None
    baseline_agg = aggregate(baseline_rows) if baseline_rows else None
    print("\n" + format_report(trained_agg, baseline_agg, trained_label, baseline_label))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "config": {"model": args.model, "lora": args.lora, "golden": args.golden,
                   "n_cases": len(cases), "top_k": args.top_k, "max_hops": args.max_hops,
                   "driver": args.driver},
        "trained": {"aggregate": trained_agg, "rows": trained_rows} if trained_rows else None,
        "baseline": {"aggregate": baseline_agg, "rows": baseline_rows} if baseline_rows else None,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[eval] 明细报告已写入：{out}")
    print("[eval] 说明：--driver searchfirst 是这个 tag 训练模型的正确驱动（模型自己发 <search>、用真实证据作答）；"
          "基线=同一后端单次 seed 检索（无模型），二者对比才真正衡量「模型驱动检索」的增益。")
    print("[eval] 提示：先看逐题 dump 锁定行为；务必用 held-out 题（别拿训练用过的题评）；"
          "把这条命令接进 CI（对固定金标准跑、比上次分数），就是 103.39 里 P0「能说不的验证者」。")


if __name__ == "__main__":
    main()
