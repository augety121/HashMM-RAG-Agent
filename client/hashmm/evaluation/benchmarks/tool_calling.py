"""Built-in tool-call benchmark using HashMM's strict offline judge.

This set is a regression fixture, not an official BFCL score.  It checks tool
selection, required arguments, argument values, invented arguments and the
negative no-tool cases without executing side effects.
"""
from __future__ import annotations

from hashmm.evaluation.tool_call_eval import (
    FAILURE_LABELS, aggregate_tool_evaluations, evaluate_tool_calls,
)


TOOL_SCHEMAS = {
    "run_shell": {"required": ["command"], "properties": {"command": {}}, "additionalProperties": False},
    "create_file": {"required": ["filename", "content"],
                    "properties": {"filename": {}, "content": {}}, "additionalProperties": False},
    "read_file": {"required": ["filename"], "properties": {"filename": {}}, "additionalProperties": False},
    "get_datetime": {"required": [], "properties": {}, "additionalProperties": False},
    "calculator": {"required": ["expression"], "properties": {"expression": {}}, "additionalProperties": False},
    "get_weather": {"required": ["city"], "properties": {"city": {}}, "additionalProperties": False},
    "web_search": {"required": ["query"], "properties": {"query": {}}, "additionalProperties": False},
}

CASES = [
    {"q": "看一下当前目录有哪些文件", "calls": [{"name": "run_shell", "args": {"command": {"one_of": ["ls", "dir", "Get-ChildItem"]}}}]},
    {"q": "把『你好』写进 note.txt", "calls": [{"name": "create_file", "args": {"filename": "note.txt", "content": {"contains": "你好"}}}]},
    {"q": "读取 note.txt 的内容", "calls": [{"name": "read_file", "args": {"filename": "note.txt"}}]},
    {"q": "现在几点了", "calls": [{"name": "get_datetime", "args": {}}]},
    {"q": "计算 (3+4)*5 等于多少", "calls": [{"name": "calculator", "args": {"expression": {"contains": "3+4"}}}]},
    {"q": "北京今天天气如何", "calls": [{"name": "get_weather", "args": {"city": {"contains": "北京"}}}]},
    {"q": "搜索一下最新的 AI 新闻", "calls": [{"name": "web_search", "args": {"query": {"contains": "AI"}}}]},
    {"q": "你们几点关门？", "calls": []},
    {"q": "随便聊聊今天心情", "calls": []},
    {"q": "谢谢你的帮助", "calls": []},
]

TOOLS_DESC = (
    "可用工具及严格参数：run_shell(command)；create_file(filename, content)；"
    "read_file(filename)；get_datetime()；calculator(expression)；"
    "get_weather(city)；web_search(query)。不得添加未声明参数。"
)

_DIMENSION_LABELS = {
    "selection": "工具选择正确",
    "required_args": "必填参数完整",
    "argument_values": "参数值正确",
    "extra_args": "无臆造参数",
    "order": "调用顺序正确",
    "restraint": "反例克制(不该调没调)",
}


def run(adapter, cases=None) -> dict:
    cases = cases or CASES
    results = []
    case_details = []
    fails = []
    for case in cases:
        try:
            decision = adapter.tool_decision(case["q"], TOOLS_DESC)
            tool = decision.get("tool")
            actual = [] if tool in (None, "", "null", "none") else [
                {"name": tool, "args": decision.get("args", {})}
            ]
            result = evaluate_tool_calls(case.get("calls") or [], actual,
                                         tool_schemas=TOOL_SCHEMAS, order_mode="exact")
        except Exception as exc:  # noqa: BLE001
            result = evaluate_tool_calls(case.get("calls") or [], [],
                                         tool_schemas=TOOL_SCHEMAS, order_mode="exact")
            result["passed"] = False
            result["score"] = 0.0
            result["failures"].append({"code": "invalid_call", "label": "调用执行异常",
                                       "detail": f"{type(exc).__name__}: {str(exc)[:100]}"})
        results.append(result)
        case_details.append({
            "query": case["q"], "passed": result["passed"], "score": result["score"],
            "expected_tools": result["expected_tools"], "actual_tools": result["actual_tools"],
            "failures": result["failures"],
        })
        if not result["passed"]:
            why = "、".join(str(f.get("label") or f.get("code")) for f in result["failures"][:3])
            fails.append(f"「{case['q'][:18]}」：{why or '未通过严格判分'}")

    summary = aggregate_tool_evaluations(results)
    dimensions = summary["dimensions"]
    breakdown = {
        _DIMENSION_LABELS.get(name, name): f"{value['passed']}/{value['total']}"
        for name, value in dimensions.items()
    }
    if summary["failure_counts"]:
        breakdown["失败类型"] = "，".join(
            f"{FAILURE_LABELS.get(code, code)}={count}"
            for code, count in sorted(summary["failure_counts"].items())
        )
    return {
        "kind": "builtin",
        "passed": summary["passed"], "total": summary["total"],
        "score_pct": round(100.0 * summary["passed"] / max(1, summary["total"]), 1),
        "detail": (f"内置严格回归集（{summary['total']} 例，非官方 BFCL 口径）："
                   "离线核对工具名、必填参数、参数值、多余参数、顺序与不调用反例；不执行副作用"),
        "fails": fails[:10],
        "cases": case_details,
        "breakdown": breakdown,
    }
