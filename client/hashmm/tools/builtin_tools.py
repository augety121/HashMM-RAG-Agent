"""Built-in real tools (V15 Phase 10).

Phase 3 gave admins a way to CONFIGURE arbitrary APIs. This module ships a few
REAL tools that work out of the box — no configuration, no API key — so the
agent can actually do useful real-world things immediately:

  - get_weather   : real current weather via wttr.in (free, no key)
  - get_datetime  : current date/time (for "今天几号"/"现在几点"/relative dates)
  - calculator    : safe arithmetic evaluation

These register the same way web_search does (into the tool registry), so they're
always available to the Agent Loop. They complement (don't replace) the
admin-configured custom tools and MCP servers.

Design notes:
  - weather uses wttr.in which needs no API key — perfect for "订票时主动查天气".
  - calculator uses a SAFE AST evaluator (no eval()), so the LLM can do exact math
    instead of hallucinating arithmetic.
"""
from __future__ import annotations

import ast as _ast
import datetime as _dt
import operator as _op
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.tools.builtin")


# ════════════════════════════════════════════════════════════════════════
# Weather — real, no API key (wttr.in)
# ════════════════════════════════════════════════════════════════════════

def _exec_weather(args: dict, ctx: dict | None = None) -> str:
    city = (args.get("city") or args.get("location") or "").strip()
    if not city:
        return "（请提供城市名）"
    try:
        import httpx
        from urllib.parse import quote
        # wttr.in: format=j1 returns JSON; no key needed.
        url = f"https://wttr.in/{quote(city)}?format=j1&lang=zh"
        with httpx.Client(timeout=12, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "curl/8.0"})
        if resp.status_code != 200:
            return f"（天气查询失败：{city} 返回 {resp.status_code}）"
        data = resp.json()
        cur = (data.get("current_condition") or [{}])[0]
        area = (data.get("nearest_area") or [{}])[0]
        name = ""
        try:
            name = area.get("areaName", [{}])[0].get("value", city)
        except Exception:
            name = city
        temp = cur.get("temp_C", "?")
        feels = cur.get("FeelsLikeC", "?")
        desc = ""
        try:
            desc = (cur.get("lang_zh") or cur.get("weatherDesc") or [{}])[0].get("value", "")
        except Exception as _e:
            log_suppressed(logger, _e)
        humidity = cur.get("humidity", "?")
        wind = cur.get("windspeedKmph", "?")
        # tomorrow forecast (for trip planning)
        forecast = ""
        try:
            days = data.get("weather", [])
            if len(days) >= 2:
                tmr = days[1]
                forecast = (f" 明天：{tmr.get('mintempC','?')}~{tmr.get('maxtempC','?')}°C")
        except Exception as _e:
            log_suppressed(logger, _e)
        return (f"{name} 当前天气：{desc} {temp}°C（体感 {feels}°C），"
                f"湿度 {humidity}%，风速 {wind}km/h。{forecast}")
    except ImportError:
        return "（天气查询需要 httpx：pip install httpx）"
    except Exception as e:
        return f"（天气查询失败：{type(e).__name__}: {str(e)[:120]}）"


# ════════════════════════════════════════════════════════════════════════
# Datetime — for "今天几号" / relative date reasoning
# ════════════════════════════════════════════════════════════════════════

def _exec_datetime(args: dict, ctx: dict | None = None) -> str:
    now = _dt.datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return (f"当前日期时间：{now.strftime('%Y-%m-%d %H:%M:%S')} "
            f"{weekdays[now.weekday()]}")


# ════════════════════════════════════════════════════════════════════════
# Calculator — safe arithmetic (no eval)
# ════════════════════════════════════════════════════════════════════════

_ALLOWED_OPS = {
    _ast.Add: _op.add, _ast.Sub: _op.sub, _ast.Mult: _op.mul,
    _ast.Div: _op.truediv, _ast.Pow: _op.pow, _ast.Mod: _op.mod,
    _ast.USub: _op.neg, _ast.UAdd: _op.pos, _ast.FloorDiv: _op.floordiv,
}


def _safe_eval(node):
    if isinstance(node, _ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("only numbers allowed")
    if isinstance(node, _ast.BinOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if not op:
            raise ValueError("operator not allowed")
        return op(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, _ast.UnaryOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if not op:
            raise ValueError("operator not allowed")
        return op(_safe_eval(node.operand))
    raise ValueError("expression not allowed")


def _exec_calculator(args: dict, ctx: dict | None = None) -> str:
    expr = (args.get("expression") or "").strip()
    if not expr:
        return "（请提供算式）"
    try:
        tree = _ast.parse(expr, mode="eval")
        result = _safe_eval(tree.body)
        return f"{expr} = {result}"
    except Exception:
        return f"（无法计算：{expr[:80]}。只支持 + - * / ** % // 和数字）"


# ════════════════════════════════════════════════════════════════════════
# Schemas + registration
# ════════════════════════════════════════════════════════════════════════

def _scoped_retrieval_search(ctx: dict | None, top_k: int):
    """Build the Self-RAG search adapter when Chat selected documents.

    Returning ``None`` keeps the existing owner-ACL retrieval path for turns
    without an explicit selection. With a selection, every hop is routed
    through the same filename filter as kb_search.
    """
    scope = (ctx or {}).get("doc_filter") or []
    if isinstance(scope, str):
        scope = [scope]
    scope = [
        str(item).strip()[:260]
        for item in scope
        if str(item).strip()
    ][:40]
    if not scope:
        return None

    def _search(question: str):
        from hashmm.retriever_bridge import kb_search_bridge
        payload = kb_search_bridge(
            {"query": question, "top_k": top_k},
            {**(ctx or {}), "doc_filter": scope},
        )
        return [
            {
                **item,
                "text": item.get("text") or item.get("content") or "",
            }
            for item in (payload.get("results") or [])
        ]

    return _search


def run_deep_search(args: dict, ctx: dict | None = None) -> dict:
    """Run Self-RAG and retain its structured evidence for Chat persistence."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "Error: deep_search 需要 query 参数。"}
    try:
        from hashmm.retrieval import self_rag as _sr
        top_k = int(args.get("top_k") or 5)
        r = _sr.self_rag_answer(
            query,
            top_k=top_k,
            max_hops=int(args.get("max_hops") or 3),
            search_fn=_scoped_retrieval_search(ctx, top_k),
            principal=str((ctx or {}).get("user_id") or "") or None,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"深度检索出错：{str(e)[:200]}。可改用 kb_search。"}
    if r is None:
        return {"ok": False, "error": (
            "深度检索当前不可用（检索策略未就绪，例如未配置 HASHMM_SEARCHR1_LORA 或本机无 GPU）。"
            "请改用 kb_search。")}
    return {**r, "ok": True}


def format_deep_search_result(r: dict) -> str:
    """Format a structured Self-RAG result for the model-facing tool contract."""
    if not r.get("ok"):
        return str(r.get("error") or "深度检索当前不可用，请改用 kb_search。")
    out = []
    out.append(f"【深度检索答案】{r.get('answer') or '（无答案）'}")
    note = "已通过自评" if r.get("grounded") else "证据可能不足"
    conf = r.get("confidence")
    if conf is not None:
        note += f"，置信度约 {round(conf * 100)}%"
    if r.get("rounds"):
        note += f"，自适应再检索 {r.get('rounds')} 轮"
    out.append(f"【可信度】{note}")
    srcs = r.get("sources") or []
    if srcs:
        out.append("【来源】")
        for i, s in enumerate(srcs[:6], 1):
            fn = s.get("filename") or s.get("file") or "?"
            txt = (s.get("text") or "")[:200].replace("\n", " ")
            out.append(f"[{i}] ({fn}) {txt}")
    return "\n".join(out)


def _exec_deep_search(args: dict, ctx: dict | None = None) -> str:
    """深度检索（Self-RAG）：模型驱动多跳检索 + deepseek 作答 + 自我批判 + 证据不足时
    自适应再检索 + 忠实度门控。适合需跨多文档/多跳推理/对比计算才能回答的复杂知识问题；
    普通单跳事实查询用 kb_search 更快。返回答案 + 可信度 + 带 [N] 编号的来源，供 agent 引用。
    策略未就绪（无 GPU / 未配 HASHMM_SEARCHR1_LORA）时返回友好提示，绝不抛错。"""
    return format_deep_search_result(run_deep_search(args, ctx))


def _exec_deep_research(args: dict, ctx: dict | None = None) -> str:
    """深度研究模式（方案7，对标 DeerFlow / R2R Deep Research）：把问题拆成多个子主题、各自
    深度检索取证据、综合成一份**跨多文档、每段带 [N] 全局引用**的长报告（引用经全局去重重编号，
    最后审计接地率）。适合"写一份带引用的研究/调研报告""系统梳理某主题"这类需要长报告的请求；
    只要一个结论用 deep_search 即可。策略未就绪（无 GPU / 未配检索）时优雅降级，绝不抛错。"""
    query = (args.get("query") or "").strip()
    if not query:
        return "Error: deep_research 需要 query 参数。"
    try:
        from hashmm.agent import deep_research as _dr
        from hashmm.retrieval import self_rag as _sr
    except Exception as e:  # noqa: BLE001
        return f"深度研究不可用：{str(e)[:160]}。可改用 deep_search。"

    _cache: dict = {}
    principal = str((ctx or {}).get("user_id") or "") or None
    _top_k = int(args.get("top_k") or 5)
    _scoped_search = _scoped_retrieval_search(ctx, _top_k)

    def _search(subtopic: str):
        try:
            r = _sr.self_rag_answer(subtopic, top_k=_top_k,
                                    max_hops=int(args.get("max_hops") or 2),
                                    search_fn=_scoped_search,
                                    principal=principal) or {}
        except Exception:
            r = {}
        _cache[subtopic] = r
        return r.get("sources") or []

    def _summarize(subtopic: str, sources: list) -> str:
        r = _cache.get(subtopic) or {}
        ans = (r.get("answer") or "").strip()
        # self_rag 答案若已带 [k] 引用直接用（merge 会全局重编号）；否则用带引用的兜底摘要
        if ans and "[" in ans:
            return ans
        if ans and sources:
            return ans + "".join(f"[{i + 1}]" for i in range(min(2, len(sources))))
        return _dr._default_summary(subtopic, sources)

    try:
        # 取共享 llm_fn（与被评 RAG 同一模型）→ 激活 SAGE 评审+重试；拿不到则单轮降级（向后兼容）
        _llm_fn = None
        try:
            from hashmm.api import app_state as _as
            _llm_fn = getattr(_as, "llm_fn", None)
        except Exception:
            _llm_fn = None
        report = _dr.run_deep_research(
            query, search_fn=_search, summarize_fn=_summarize, llm_fn=_llm_fn,
            max_subtopics=int(args.get("max_subtopics") or 4), audit=True,
            max_rounds=int(args.get("max_rounds") or 2))
    except Exception as e:  # noqa: BLE001
        return f"深度研究执行出错：{str(e)[:160]}。可改用 deep_search。"

    if not report.sources:
        return ("深度研究未能检索到可引用的证据（可能检索策略未就绪：未配置 HASHMM_SEARCHR1_LORA "
                "或本机无 GPU）。请改用 deep_search 或 kb_search。")
    tail = ""
    if report.grounding is not None:
        tail = f"\n\n（全报告接地率约 {round(report.grounding * 100)}%，{report.n_sources} 条来源，{report.n_sections} 个子主题）"
    return report.markdown + tail


def _exec_json_format(args: dict, ctx: dict | None = None) -> str:
    """校验并美化 JSON；非法时返回错误位置（纯函数，无网络）。"""
    import json as _json
    text = (args.get("json") or args.get("text") or "").strip()
    if not text:
        return "（请提供 JSON 文本）"
    try:
        obj = _json.loads(text)
    except Exception as e:
        return f"JSON 非法：{e}"
    try:
        indent = int(args.get("indent", 2))
    except Exception:
        indent = 2
    try:
        return _json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=bool(args.get("sort_keys")))
    except Exception as e:
        return f"序列化失败：{e}"


def _exec_text_stats(args: dict, ctx: dict | None = None) -> str:
    """统计字符/行/词/中文字/约 token 数（纯函数）。"""
    import re as _re
    text = args.get("text") or ""
    if not isinstance(text, str) or not text:
        return "（请提供文本）"
    chars = len(text)
    chars_no_space = len("".join(text.split()))
    lines = text.count("\n") + 1
    en_words = len(_re.findall(r"[A-Za-z0-9_]+", text))
    cjk = len(_re.findall(r"[\u4e00-\u9fff]", text))
    approx_tokens = chars // 3 + 1
    return (f"字符数：{chars}（不含空白 {chars_no_space}）\n行数：{lines}\n"
            f"英文单词：{en_words}\n中文字数：{cjk}\n约 token 数：{approx_tokens}")


def _exec_hash_text(args: dict, ctx: dict | None = None) -> str:
    """计算文本哈希（纯函数）。"""
    import hashlib as _hl
    text = args.get("text") or ""
    if not isinstance(text, str) or not text:
        return "（请提供文本）"
    algo = (args.get("algorithm") or "sha256").lower()
    data = text.encode("utf-8")
    table = {"md5": _hl.md5, "sha1": _hl.sha1, "sha256": _hl.sha256, "sha512": _hl.sha512}
    if algo == "all":
        return "\n".join(f"{k}: {fn(data).hexdigest()}" for k, fn in table.items())
    fn = table.get(algo)
    if not fn:
        return f"不支持的算法：{algo}（可选 md5/sha1/sha256/sha512/all）"
    return f"{algo}: {fn(data).hexdigest()}"


BUILTIN_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的实时天气和明日预报。用户问天气、或规划出行/订票时主动查目的地天气。",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名，如 北京 / Shanghai / Tokyo"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "获取当前日期和时间。当用户问“今天几号”“现在几点”“这周几”或需要计算相对日期时使用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "精确计算数学算式（加减乘除、幂、取余）。需要准确算术时用，不要心算。",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "算式，如 1234*5.6/7"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deep_search",
            "description": "深度检索（多跳 / 综合 / 对比 / 计算）。当问题满足以下【任一】情况时，"
                           "必须用本工具，不要用 kb_search："
                           "① 需要跨两个及以上文档或实体综合信息；"
                           "② 多跳推理（要先查到 A 才能据此查 B）；"
                           "③ 对比两个及以上对象（如「对比 X 和 Y」「谁更…」）；"
                           "④ 先取数再计算（如「A 是 B 的多少倍 / 几倍 / 差多少」）。"
                           "它做模型驱动多跳检索 + 自我校验 + 证据不足时自动补检，这类问题的准确率远高于 kb_search。"
                           "只有【单一事实】的简单查询（某公司某年的某个指标、某术语的定义）才用 kb_search。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要深度检索的复杂问题（可含多跳、对比、计算）"},
                    "max_hops": {"type": "integer", "description": "最大检索跳数，默认 3"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deep_research",
            "description": "深度研究模式：把一个较大的主题拆成多个子主题并行深度检索，综合成一份"
                           "**跨多文档、每段带 [N] 全局引用的长报告**（引用自动去重+全局编号，并审计接地率）。"
                           "仅当用户明确要「写一份带引用的研究/调研报告」「系统梳理/综述某主题」「多维度对比分析」"
                           "这类需要结构化长报告的场景才用；如果只需要一个结论/答案，用 deep_search 即可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要研究的主题/问题（会被拆成多个子主题分别检索）"},
                    "max_subtopics": {"type": "integer", "description": "最多拆几个子主题，默认 4"},
                    "max_hops": {"type": "integer", "description": "每个子检索的最大跳数，默认 2"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "json_format",
            "description": "校验并美化（pretty-print）JSON 文本，或检测 JSON 是否合法。需要格式化/校验 JSON 时用，不要手算。",
            "parameters": {
                "type": "object",
                "properties": {
                    "json": {"type": "string", "description": "要校验/美化的 JSON 文本"},
                    "indent": {"type": "integer", "description": "缩进空格数，默认 2"},
                    "sort_keys": {"type": "boolean", "description": "是否按键排序，默认否"},
                },
                "required": ["json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "text_stats",
            "description": "统计文本的字符数/行数/英文单词数/中文字数/约 token 数。需要数字数或估算长度时用。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要统计的文本"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hash_text",
            "description": "计算文本的哈希值（md5/sha1/sha256/sha512）。需要校验和/指纹时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要计算哈希的文本"},
                    "algorithm": {"type": "string", "description": "md5/sha1/sha256/sha512/all，默认 sha256"},
                },
                "required": ["text"],
            },
        },
    },
]

BUILTIN_EXECUTORS = {
    "get_weather": _exec_weather,
    "get_datetime": _exec_datetime,
    "calculator": _exec_calculator,
    "deep_search": _exec_deep_search,
    "deep_research": _exec_deep_research,
    "json_format": _exec_json_format,
    "text_stats": _exec_text_stats,
    "hash_text": _exec_hash_text,
}


def register_builtin_tools():
    """Register built-in tools into the global tool registry (like web_search)."""
    try:
        from hashmm.api.tool_registry import register_executor
        for name, fn in BUILTIN_EXECUTORS.items():
            register_executor(name, fn)
        logger.info(f"[BuiltinTools] registered: {', '.join(BUILTIN_EXECUTORS)}")
    except Exception as e:
        logger.debug(f"builtin tool registration skipped: {e}")
