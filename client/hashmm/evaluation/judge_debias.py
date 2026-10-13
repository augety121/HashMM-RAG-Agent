"""hashmm/evaluation/judge_debias.py — 裁判去偏见 + 比较式评估（V284，资料 3.1.3/3.3.1.2/裁判避坑五律）。

资料明确点出 LLM-as-a-judge 的三大认知偏差，且给了工程避坑法。这里落地：
  · **双向逆序对决**（消首位偏差）：A/B 两个回答，正序判一次、逆序再判一次，只有两次都判同一个赢
    才算真赢；结论翻转 = 平局（说明裁判在瞎猜位置）。
  · **冗长归一化**（消冗长偏差）：把"更长就更好"的倾向显式压制——长度差过大时提示裁判按信息密度判。
  · **CoT 先理由后分数**（减打分随机性）：强制 JSON 先 rationale 再 verdict。
  · **偏好≠正确校验**：比较式只产出"更受偏好"，不等于"更正确"——本模块只做偏好排序，正确性另走
    功能正确性/Ragas（资料口诀：能用功能正确性就别用 LLM-judge）。

纯逻辑 + 可注入（judge_llm）；**永不抛错**。用于测评中枢的"裁判自检"套件：拿一对
"明显好 vs 明显差"的答案，验证裁判在正逆序下都能稳定选出好的（裁判本身可信才敢用它打分）。
"""
from __future__ import annotations

import json
import re

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evaluation.judge_debias")


def _json_obj(raw: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", str(raw or ""))
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {}


_PAIR_PROMPT = (
    "你是严格的对比裁判。下面是同一个问题的两个回答 A 和 B。\n"
    "评判哪个更好，标准优先级：① 事实正确、② 切题、③ 信息密度（不因更长而更好，"
    "空洞的长回答要扣分）。\n"
    "必须先写理由再给结论。只输出 JSON：\n"
    '{{"rationale": "对比理由，指出关键差异", "winner": "A" 或 "B" 或 "tie"}}\n\n'
    "【问题】\n{q}\n\n【回答 A】\n{a}\n\n【回答 B】\n{b}\n"
)


def compare_once(question: str, ans_a: str, ans_b: str, judge_llm) -> str:
    """单次对决，返回 'A'/'B'/'tie'。**永不抛错**。"""
    try:
        raw = str(judge_llm(_PAIR_PROMPT.format(q=question, a=ans_a, b=ans_b)) or "")
        w = str(_json_obj(raw).get("winner", "")).strip().upper()
        if w in ("A", "B"):
            return w
        return "tie"
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return "tie"


def compare_debiased(question: str, ans_a: str, ans_b: str, judge_llm) -> tuple[str, str]:
    """双向逆序对决去首位偏差，返回 (winner, 说明)。**永不抛错**。

    正序 (A,B) 判一次 → 逆序 (B,A) 判一次；两次都指向同一原始回答才算它真赢，
    否则 tie（结论随位置翻转 = 裁判在猜，判平局）。
    """
    if not callable(judge_llm):
        return ("tie", "无裁判 LLM")
    try:
        w1 = compare_once(question, ans_a, ans_b, judge_llm)          # A=ans_a, B=ans_b
        w2 = compare_once(question, ans_b, ans_a, judge_llm)          # A=ans_b, B=ans_a
        # 把两次结论都映射回"原始 a / 原始 b"
        first = {"A": "a", "B": "b", "tie": "tie"}[w1]
        second = {"A": "b", "B": "a", "tie": "tie"}[w2]               # 逆序时 A 其实是 ans_b
        if first == second and first != "tie":
            return (first, f"正逆序一致判 {first} 胜（无位置偏差）")
        if first == "tie" and second == "tie":
            return ("tie", "两次均平")
        return ("tie", f"结论随位置翻转（正序判{first}、逆序判{second}）→ 判平局")
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return ("tie", f"对决异常 {type(e).__name__}")


# 裁判自检用例：一对"明显更好 vs 明显更差/空洞冗长"，可信裁判应稳定选出更好的那个。
JUDGE_SELFCHECK = [
    {"name": "正确 vs 错误",
     "q": "RAG 的两个阶段是什么？",
     "good": "RAG 分为检索和生成两个阶段：先检索相关文档，再基于文档生成答案。",
     "bad": "RAG 是一种只用微调不用检索的训练方法，把知识全部写进模型权重。"},
    {"name": "精炼 vs 空洞冗长",
     "q": "向量检索的核心思想？",
     "good": "把文本编码成向量，用向量相似度找语义最接近的内容。",
     "bad": ("向量检索是一个非常非常重要的技术，它在现代人工智能领域扮演着极其关键的角色，"
             "很多很多公司都在用它，它的历史非常悠久，应用场景也非常广泛，总之是一个特别特别"
             "值得深入学习和研究的、意义重大的、影响深远的、不可或缺的核心关键技术。")},
    {"name": "切题 vs 答非所问",
     "q": "BM25 擅长什么？",
     "good": "BM25 擅长精确匹配专有名词、代码符号和数字这类关键词。",
     "bad": "今天天气很好，适合出去散步，顺便可以思考一下人生的意义。"},
]


def run_judge_selfcheck(judge_llm, k: int = 1):
    """裁判自检：对每对用例做双向逆序对决，good 应稳定胜出。返回 SuiteReport。**永不抛错**。"""
    from hashmm.evaluation.deep_eval import SuiteReport, run_case_ntimes, RunOutcome
    rep = SuiteReport("裁判自检(去偏见对决)")
    if not callable(judge_llm):
        for c in JUDGE_SELFCHECK:
            rep.add(run_case_ntimes(c["name"], None, k, skip_reason="未配裁判 LLM"))
        return rep
    flips = 0
    total = 0
    for c in JUDGE_SELFCHECK:
        def run_once(c=c):
            nonlocal flips, total
            total += 1
            winner, msg = compare_debiased(c["q"], c["good"], c["bad"], judge_llm)
            # good 是第一个参数(a)，所以期望 winner == "a"
            ok = winner == "a"
            if "翻转" in msg:
                flips += 1
            mode = "" if ok else ("位置偏差(结论翻转)" if "翻转" in msg else "裁判选错(选了差答案)")
            trace = {
                "问题": c["q"],
                "更好的答案(应胜出)": c["good"],
                "更差的答案(空洞/错误/答非所问)": c["bad"],
                "双向逆序对决结论": msg,
                "问题定位": ["裁判正逆序都选对了好答案，无位置偏差"] if ok
                            else ([f"裁判有位置偏差：{msg}"] if "翻转" in msg else [f"裁判选错：{msg}"]),
            }
            return RunOutcome(ok, 1.0 if ok else 0.0, mode, msg, trace=trace)
        rep.add(run_case_ntimes(c["name"], run_once, k))
    rep.extra_metrics = {"位置翻转率": round(flips / total, 3) if total else 0.0}
    return rep
