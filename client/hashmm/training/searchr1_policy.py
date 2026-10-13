"""hashmm/training/searchr1_policy.py — 把训练好的 LoRA 检索策略模型接进 P0 多跳回路。

训练（SFT）让模型学会了用 <think>/<search>/<answer> 的格式思考与检索，但模型自己生成的
<information> 是「脑补」的，不是真证据。要让它有用，必须把模型发出的 <search> 真正打到你的
检索后端（BGE-M3 + FAISS），由知识库返回真实证据，再让模型据此继续。

本模块提供一个适配器：把训练好的模型包装成 AgenticRetriever 期望的
``llm_fn(prompt) -> str``（输出 {"action":"search","query":...} 或 {"action":"finish"}）。
于是「模型决定何时/检索什么」+「你的知识库返回真实证据」合成完整回路 —— 这才是 P2 的落地形态。

设计原则（与项目一致）：重库延迟导入、模型只加载一次、任何异常都安全降级（返回 finish），
不可用时回路自动退回原有的规则式判停，绝不影响线上。

用法（接进 streaming 的 _agentic_retrieval，或单测）：
    from hashmm.training.searchr1_policy import SearchR1Policy
    policy = SearchR1Policy(model_dir="/root/autodl-tmp/models/Qwen2.5-7B-Instruct",
                            lora_dir="/root/autodl-tmp/models/qwen2.5-7b-hashmm-lora")
    llm_fn = policy.as_llm_fn()                 # 传给 AgenticRetriever(search_fn, llm_fn=llm_fn)
"""
from __future__ import annotations

import logging
import re
from typing import Callable

logger = logging.getLogger("hashmm.training.searchr1_policy")

# 模型被训练成输出这套标签；适配器从中提取「下一步检索什么 / 是否作答」。
_SEARCH_RE = re.compile(r"<search>\s*(.*?)\s*</search>", re.DOTALL)
_ANSWER_RE = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.DOTALL)

# 给模型的输入模板：把回路已掌握的「原问题 + 已检索证据」喂进去，让模型决定下一步。
# 与训练时的指令模板同源，保证训练/推理一致。
_POLICY_PROMPT = (
    "Answer the given question. You must conduct reasoning inside <think> and </think> "
    "first. If you lack knowledge, call search by <search> query </search>. Results will be "
    "given between <information> and </information>. When you have enough information, give the "
    "final answer inside <answer> and </answer>.\n"
    "Question: {query}\n"
    "Already retrieved evidence ({n} items):\n{evidence}\n"
)


def decision_from_model_text(text: str) -> dict:
    """把模型输出的 <search>/<answer> 文本翻译成回路要的决策 dict。

    规则：先看有没有 <search>（且非空）→ 继续检索该子查询；否则若有 <answer> → finish；
    都没有 → finish（安全默认）。纯函数、不抛错。
    """
    if not text:
        return {"action": "finish"}
    try:
        m = _SEARCH_RE.search(text)
        if m:
            q = (m.group(1) or "").strip()
            # 去掉模型可能带出的多余引号/标点
            q = q.strip(" \t\r\n\"'：:")
            if q:
                return {"action": "search", "query": q}
        if _ANSWER_RE.search(text):
            return {"action": "finish"}
    except Exception as e:
        logger.debug("decision_from_model_text failed: %s", e)
    return {"action": "finish"}


# 与训练数据完全同一套 Search-R1 指令模板（从 build_sft_data 复用，保证训练/推理逐字一致，绝不另写一份免漂移）。
try:
    from hashmm.training.build_sft_data import _USER_TEMPLATE
except Exception:  # build_sft_data 偶发不可用时退回内置同款（与训练模板逐字相同）
    _USER_TEMPLATE = (
        "Answer the given question. You must conduct reasoning inside <think> and </think> "
        "first every time you get new information. After reasoning, if you find you lack some "
        "knowledge, you can call a search engine by <search> query </search>, and it will return "
        "the top searched results between <information> and </information>. You can search as many "
        "times as you want. If you find no further external knowledge needed, you can directly "
        "provide the answer inside <answer> and </answer> without detailed illustrations. For "
        "example, <answer> Beijing </answer>. Question: {question}\n"
    )


# ---- 原生 Search-R1 驱动循环用的纯函数（可单测、无 GPU/重库依赖）----

def _extract_tag(text: str, tag: str) -> str | None:
    """取出 <tag>...</tag> 内首个内容；没有返回 None。纯函数。"""
    if not text:
        return None
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None


def _truncate_after_first(text: str, markers: tuple) -> str:
    """在最早出现的任一 marker 之后截断（含 marker）；都没有则原样返回。

    用于 Search-R1 推理在 </search> 处停下——丢掉模型自己脑补的 <information>，好注入真证据。
    """
    if not text:
        return text
    best = None
    for mk in markers:
        i = text.find(mk)
        if i != -1:
            end = i + len(mk)
            if best is None or end < best:
                best = end
    return text[:best] if best is not None else text


def _searchr1_source_key(s) -> str:
    """跨跳去重的稳定 key：优先 id，否则 filename|page|正文前缀。"""
    if not isinstance(s, dict):
        return repr(s)
    if s.get("id") not in (None, ""):
        return f"id:{s['id']}"
    return f"{s.get('filename', '')}|{s.get('page', '')}|{(s.get('text', '') or '')[:60]}"


def _format_information(results, top_k: int) -> str:
    """把真实检索结果拼成注入用的 <information> 正文（取 top_k、多字段名兼容、限长 1500 字）。"""
    parts = []
    for s in (results or [])[:max(1, top_k)]:
        if isinstance(s, dict):
            t = s.get("text") or s.get("content") or s.get("snippet") or ""
        else:
            t = str(s)
        if t:
            parts.append(str(t).strip().replace("\n", " "))
    return (" ".join(parts))[:1500] if parts else "（知识库未返回结果）"


class SearchR1Policy:
    """加载一次 LoRA 策略模型，提供 llm_fn。模型加载失败 → as_llm_fn() 返回 None，回路自动降级。"""

    def __init__(self, model_dir: str, lora_dir: str, *, max_new_tokens: int = 128):
        self.model_dir = model_dir
        self.lora_dir = lora_dir
        self.max_new_tokens = max_new_tokens
        self._tok = None
        self._model = None
        self._ok = False
        self._load()

    def _load(self) -> None:
        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
            from peft import PeftModel
            if not torch.cuda.is_available():
                logger.warning("[SearchR1Policy] 无 GPU，策略模型不加载，回路将走规则式判停。")
                return
            self._tok = AutoTokenizer.from_pretrained(self.model_dir, trust_remote_code=True)
            bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                     bnb_4bit_use_double_quant=True,
                                     bnb_4bit_compute_dtype=torch.bfloat16)
            base = AutoModelForCausalLM.from_pretrained(
                self.model_dir, quantization_config=bnb, device_map={"": 0}, trust_remote_code=True)
            self._model = PeftModel.from_pretrained(base, self.lora_dir)
            self._model.eval()
            self._ok = True
            logger.info("[SearchR1Policy] 策略模型已加载：%s + LoRA %s", self.model_dir, self.lora_dir)
        except Exception as e:
            logger.warning("[SearchR1Policy] 加载失败，回路降级为规则式判停：%s", e)
            self._ok = False

    def _generate(self, query: str, evidence: str, n: int) -> str:
        import torch
        msgs = [{"role": "user", "content": _POLICY_PROMPT.format(query=query, n=n, evidence=evidence)}]
        prompt = self._tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = self._tok(prompt, return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=self.max_new_tokens,
                                       do_sample=False, pad_token_id=self._tok.eos_token_id)
        return self._tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)

    def _gen(self, prompt_text: str, stop_markers: tuple) -> str:
        """单次贪心生成，命中任一 stop_marker 即在其后截断返回。

        Search-R1 原生推理要在 </search> 处停下，把真实检索注入再续写——这就是那个停点。
        """
        import torch
        inputs = self._tok(prompt_text, return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=self.max_new_tokens,
                                       do_sample=False, pad_token_id=self._tok.eos_token_id)
        text = self._tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        return _truncate_after_first(text, stop_markers)

    def run_search_loop(self, question: str, search_fn, *, max_hops: int = 3,
                        top_k: int = 5, max_sources: int = 20) -> dict | None:
        """原生 Search-R1 推理循环（这才是这个 <tag> 训练模型的正确驱动方式）。

        模型出 <search>子查询</search> → 在 </search> 处停 → 用 search_fn 打**真实检索** →
        把真实结果作为 <information>…</information> 注入 → 模型续写，直到 <answer> 或到 max_hops。
        与 as_llm_fn+AgenticRetriever 的区别：那条把「判断够不够」的元指令塞进 Question 槽，模型读不懂、
        一跳就停；这里给模型的是它训练时见过的干净 Question 模板，模型会真的发起 <search>。

        返回结构与 AgenticRetriever.retrieve 兼容（sources/n_hops/subqueries/stopped_reason/confidence/
        trace），额外带 answer（模型基于真实证据给出的最终答案）。模型不可用返回 None；绝不抛错。

        注意（训练数据决定的局限）：当前 SFT 轨迹只示范了单跳（think→search→information→think→answer），
        所以模型偏好单跳；要稳健多跳需在训练数据里加入多跳轨迹。但即便单跳，这里也已是「模型驱动 +
        真实证据」，远胜旧接线的「零模型检索」。
        """
        if not self._ok:
            return None
        try:
            base = self._tok.apply_chat_template(
                [{"role": "user", "content": _USER_TEMPLATE.format(question=question)}],
                tokenize=False, add_generation_prompt=True)
        except Exception as e:
            logger.warning("[run_search_loop] 构造 prompt 失败：%s", e)
            return None
        seen: dict = {}
        subqueries: list = []
        trace: list = []
        assistant, answer, stopped = "", None, "max_hops"
        for hop in range(max(1, max_hops)):
            try:
                chunk = self._gen(base + assistant, ("</search>", "</answer>"))
            except Exception as e:
                logger.warning("[run_search_loop] 第 %d 跳生成失败：%s", hop, e)
                stopped = "gen_error"
                break
            assistant += chunk
            ans = _extract_tag(chunk, "answer")
            if ans is not None:
                answer = ans
                trace.append({"hop": hop, "action": "answer", "subquery": "",
                              "n_sources_after": len(seen), "confidence": None})
                stopped = "answer"
                break
            sq = _extract_tag(chunk, "search")
            if sq:
                if sq in subqueries:
                    stopped = "repeat"
                    break
                subqueries.append(sq)
                try:
                    results = list(search_fn(sq) or [])
                except Exception as e:
                    logger.warning("[run_search_loop] search_fn 失败：%s", e)
                    results = []
                for s in results:
                    k = _searchr1_source_key(s)
                    if k not in seen:
                        seen[k] = s
                assistant += f"\n<information>{_format_information(results, top_k)}</information>\n"
                trace.append({"hop": hop, "action": "search", "subquery": sq,
                              "n_sources_after": len(seen), "confidence": None})
                continue
            stopped = "no_action"
            break
        return {"sources": list(seen.values())[:max_sources], "n_hops": len(subqueries) or 1,
                "subqueries": subqueries, "stopped_reason": stopped, "answer": answer,
                "confidence": None, "trace": trace}

    def as_llm_fn(self) -> Callable[[str], str] | None:
        """返回 AgenticRetriever 用的 llm_fn(prompt)->str（JSON 裁判协议）。模型不可用→None。

        ⚠ 推荐改用 run_search_loop：本方法把 AgenticRetriever 的「判断够不够」元指令塞进训练模板的
        Question 槽，本 <tag> 训练模型读不懂、实测一跳就停（评测里 policy_issued_search≈0）。它更适合
        一个**通用 instruct LLM**当裁判；要发挥这个 Search-R1 模型的价值，请用 run_search_loop。
        """
        if not self._ok:
            return None
        import json as _json

        def llm_fn(prompt: str) -> str:
            try:
                # prompt 本身已含「原问题 + 证据」，直接交给模型决策（n/evidence 已在其中）。
                text = self._generate(prompt, evidence="(见上文)", n=0)
                return _json.dumps(decision_from_model_text(text), ensure_ascii=False)
            except Exception as e:
                logger.debug("[SearchR1Policy] 生成失败，返回 finish：%s", e)
                return _json.dumps({"action": "finish"})
        return llm_fn
