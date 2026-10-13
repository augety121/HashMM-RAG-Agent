"""Deterministic tool-call evaluation shared by HashMM's eval surfaces.

This is deliberately an offline judge.  It inspects the emitted call contract
without executing side effects and never asks a model to grade itself.  It
separates selection, required arguments, argument values, invented arguments,
ordering and the negative "do not call a tool" cases described by EDD/BFCL.
"""
from __future__ import annotations

from collections import Counter
import json
import re
from typing import Any, Iterable


SCHEMA = "hashmm.tool-call-eval.v1"
FAILURE_LABELS = {
    "invalid_call": "调用结构无效",
    "unknown_tool": "调用了未提供的工具",
    "no_tool_violation": "本不该调用工具",
    "missing_tool": "缺少应调用的工具",
    "unexpected_tool": "出现多余工具调用",
    "wrong_tool": "工具选择错误",
    "wrong_order": "工具调用顺序错误",
    "missing_required_arg": "缺少必填参数",
    "extra_arg": "臆造了未声明参数",
    "wrong_arg_value": "参数值错误",
}


def _normalise_call(raw: Any) -> tuple[dict, list[dict]]:
    failures: list[dict] = []
    if not isinstance(raw, dict):
        return {"name": "", "args": {}}, [{"code": "invalid_call"}]
    name = str(raw.get("name") or raw.get("tool") or "").strip()
    args = raw.get("args", raw.get("arguments", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            failures.append({"code": "invalid_call", "tool": name,
                             "detail": "arguments 不是合法 JSON 对象"})
            args = {}
    if not isinstance(args, dict):
        failures.append({"code": "invalid_call", "tool": name,
                         "detail": "arguments 必须是对象"})
        args = {}
    return {"name": name, "args": args}, failures


def _value_matches(actual: Any, rule: Any) -> bool:
    if isinstance(rule, dict) and any(k in rule for k in ("equals", "contains", "one_of", "regex")):
        if "equals" in rule:
            return str(actual).strip() == str(rule["equals"]).strip()
        if "contains" in rule:
            return str(rule["contains"]).casefold() in str(actual).casefold()
        if "one_of" in rule:
            return any(str(actual).strip() == str(v).strip() for v in (rule.get("one_of") or []))
        try:
            return re.search(str(rule.get("regex") or ""), str(actual)) is not None
        except re.error:
            return False
    return actual == rule or str(actual).strip() == str(rule).strip()


def _schema_parts(schema: dict | None) -> tuple[set[str], set[str], bool]:
    schema = schema if isinstance(schema, dict) else {}
    required = {str(k) for k in (schema.get("required") or [])}
    props = schema.get("properties") or {}
    allowed = {str(k) for k in (props.keys() if isinstance(props, dict) else props)}
    allow_extra = bool(schema.get("additionalProperties", False))
    return required, allowed, allow_extra


def _match_indices(expected: list[dict], actual: list[dict], order_mode: str) -> list[int | None]:
    if order_mode == "exact":
        return [i if i < len(actual) else None for i in range(len(expected))]
    used: set[int] = set()
    cursor = 0
    out: list[int | None] = []
    for exp in expected:
        found = None
        start = cursor if order_mode == "in_order" else 0
        for i in range(start, len(actual)):
            if i not in used and actual[i]["name"] == exp["name"]:
                found = i
                used.add(i)
                if order_mode == "in_order":
                    cursor = i + 1
                break
        out.append(found)
    return out


def evaluate_tool_calls(expected_calls: Iterable[dict] | None,
                        actual_calls: Iterable[dict] | None, *,
                        tool_schemas: dict[str, dict] | None = None,
                        order_mode: str = "exact",
                        allow_extra_calls: bool = False) -> dict:
    """Evaluate one expected/actual tool trajectory without executing it.

    ``order_mode`` is ``exact``, ``in_order`` or ``any``.  Expected argument
    values can be literals or rules such as ``{"contains": "北京"}``.
    """
    if order_mode not in {"exact", "in_order", "any"}:
        raise ValueError("order_mode must be exact, in_order or any")
    schemas = tool_schemas or {}
    failures: list[dict] = []
    expected: list[dict] = []
    actual: list[dict] = []
    for raw in expected_calls or []:
        call, bad = _normalise_call(raw)
        expected.append(call)
        failures.extend({**f, "side": "expected"} for f in bad)
    for index, raw in enumerate(actual_calls or []):
        call, bad = _normalise_call(raw)
        actual.append(call)
        failures.extend({**f, "side": "actual", "call_index": index} for f in bad)

    expected_names = [c["name"] for c in expected]
    actual_names = [c["name"] for c in actual if c["name"]]
    applicable = {"selection", "required_args", "argument_values", "extra_args", "order"}

    if not expected:
        applicable = {"restraint"}
        for i, call in enumerate(actual):
            if call["name"]:
                failures.append({"code": "no_tool_violation", "tool": call["name"], "call_index": i})
    else:
        for i, call in enumerate(actual):
            if call["name"] and schemas and call["name"] not in schemas:
                failures.append({"code": "unknown_tool", "tool": call["name"], "call_index": i})

        if Counter(expected_names) != Counter(actual_names):
            for name, count in (Counter(expected_names) - Counter(actual_names)).items():
                failures.extend({"code": "missing_tool", "tool": name} for _ in range(count))
            if not allow_extra_calls:
                for name, count in (Counter(actual_names) - Counter(expected_names)).items():
                    failures.extend({"code": "unexpected_tool", "tool": name} for _ in range(count))
        if order_mode == "exact" and expected_names != actual_names and Counter(expected_names) == Counter(actual_names):
            failures.append({"code": "wrong_order", "expected": expected_names, "actual": actual_names})
        elif order_mode == "in_order":
            pos = 0
            for name in actual_names:
                if pos < len(expected_names) and name == expected_names[pos]:
                    pos += 1
            if pos != len(expected_names):
                failures.append({"code": "wrong_order", "expected": expected_names, "actual": actual_names})

        matches = _match_indices(expected, actual, order_mode)
        for exp_index, actual_index in enumerate(matches):
            if actual_index is None:
                continue
            exp, act = expected[exp_index], actual[actual_index]
            if exp["name"] != act["name"]:
                failures.append({"code": "wrong_tool", "expected": exp["name"],
                                 "actual": act["name"], "call_index": actual_index})
                continue
            required, allowed, allow_extra_args = _schema_parts(schemas.get(exp["name"]))
            for key in sorted(required - set(act["args"])):
                failures.append({"code": "missing_required_arg", "tool": exp["name"],
                                 "argument": key, "call_index": actual_index})
            if allowed and not allow_extra_args:
                for key in sorted(set(act["args"]) - allowed):
                    failures.append({"code": "extra_arg", "tool": exp["name"],
                                     "argument": key, "call_index": actual_index})
            for key, rule in exp["args"].items():
                if key not in act["args"]:
                    if key not in required:
                        failures.append({"code": "missing_required_arg", "tool": exp["name"],
                                         "argument": key, "call_index": actual_index})
                elif not _value_matches(act["args"][key], rule):
                    failures.append({"code": "wrong_arg_value", "tool": exp["name"],
                                     "argument": key, "call_index": actual_index})

    code_to_dim = {
        "invalid_call": "selection", "unknown_tool": "selection",
        "no_tool_violation": "restraint", "missing_tool": "selection",
        "unexpected_tool": "selection", "wrong_tool": "selection",
        "wrong_order": "order", "missing_required_arg": "required_args",
        "extra_arg": "extra_args", "wrong_arg_value": "argument_values",
    }
    failed_dims = set()
    for failure in failures:
        dimension = code_to_dim.get(failure.get("code"), "selection")
        if not expected and dimension == "selection":
            dimension = "restraint"
        failed_dims.add(dimension)
    dimensions = {name: name not in failed_dims for name in sorted(applicable)}
    score = sum(1 for ok in dimensions.values() if ok) / max(1, len(dimensions))
    counts = Counter(str(f.get("code") or "invalid_call") for f in failures)
    return {
        "schema": SCHEMA,
        "passed": not failures,
        "score": round(score, 4),
        "dimensions": dimensions,
        "failure_counts": dict(counts),
        "failures": [{**f, "label": FAILURE_LABELS.get(str(f.get("code")), "判分失败")}
                     for f in failures],
        "expected_tools": expected_names,
        "actual_tools": actual_names,
        "order_mode": order_mode,
        "executed": False,
    }


def aggregate_tool_evaluations(results: Iterable[dict]) -> dict:
    rows = list(results)
    dimensions: dict[str, list[int]] = {}
    failures: Counter[str] = Counter()
    for row in rows:
        for name, ok in (row.get("dimensions") or {}).items():
            bucket = dimensions.setdefault(str(name), [0, 0])
            bucket[1] += 1
            bucket[0] += 1 if ok else 0
        failures.update(row.get("failure_counts") or {})
    return {
        "passed": sum(1 for row in rows if row.get("passed")),
        "total": len(rows),
        "dimensions": {k: {"passed": v[0], "total": v[1]} for k, v in dimensions.items()},
        "failure_counts": dict(failures),
    }
