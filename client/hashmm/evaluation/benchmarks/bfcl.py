"""BFCL（Berkeley Function-Calling Leaderboard）真实数据评测 —— V306。

**为什么这个基准最有价值**：它是本套件里唯一**不需要 Docker、不需要 HuggingFace** 就能跑出
**真实可对标分数**的基准 —— 官方数据直接在 GitHub 的 `ShishirPatil/gorilla` 仓库里，
`install.sh` 会 clone 下来（你的 AutoDL 能连 GitHub）。

判分口径（诚实标注）：官方数据 + **简化 AST 判分**（函数名匹配 + 必填参数齐全 + 参数值命中官方
possible_answer 的可接受集合）。覆盖 `simple` / `multiple` 两类（单次函数调用），这是 BFCL 里
可离线 AST 判分的主体。与官方评测脚本可能有细微出入（官方还有 live/parallel/multi-turn 等类别与
更细的类型规则），故分数标为"官方数据·简化AST"，可与 leaderboard 做**量级对比**，不等同官方复现值。

缺数据时明确 SKIP，绝不假造分数。纯标准库。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


from ._paths import bench_home  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def _find_data_files() -> dict:
    """在 gorilla 仓库里定位 BFCL 数据（不同版本目录层级不同，全局搜索最稳）。"""
    root = bench_home() / "gorilla"
    if not root.is_dir():
        return {}
    out: dict = {}
    for cat in ("simple", "multiple"):
        # 数据文件：**/data/**/*<cat>*.json（排除 possible_answer 目录）
        data_f = None
        ans_f = None
        try:
            for p in root.rglob("*.json"):
                s = str(p)
                if "possible_answer" in s:
                    if cat in p.name.lower() and ans_f is None and "live" not in p.name.lower():
                        ans_f = p
                elif "/data/" in s.replace("\\", "/") and cat in p.name.lower() \
                        and "live" not in p.name.lower() and "multi_turn" not in s:
                    if data_f is None:
                        data_f = p
        except Exception:  # noqa: BLE001
            pass
        if data_f and ans_f:
            out[cat] = (data_f, ans_f)
    return out


def _load_jsonl(p: Path) -> list[dict]:
    """BFCL 文件多为 JSONL；也兼容 JSON 数组。"""
    try:
        text = p.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return []
    if not text:
        return []
    if text.lstrip().startswith("["):
        try:
            d = json.loads(text)
            return d if isinstance(d, list) else []
        except Exception:  # noqa: BLE001
            return []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return rows


def detect() -> dict:
    files = _find_data_files()
    return {"installed": bool(files), "categories": sorted(files.keys()),
            "hint": "" if files else "未装 BFCL 官方数据：运行 benchmarks/install.sh bfcl（从 GitHub clone gorilla，无需 Docker/HF）"}


def _question_text(item: dict) -> str:
    q = item.get("question")
    # 官方格式：question: [[{role,content}, ...]]
    try:
        if isinstance(q, list) and q and isinstance(q[0], list):
            return " ".join(str(m.get("content", "")) for m in q[0] if isinstance(m, dict))
        if isinstance(q, list) and q and isinstance(q[0], dict):
            return " ".join(str(m.get("content", "")) for m in q if isinstance(m, dict))
    except Exception:  # noqa: BLE001
        pass
    return str(q or "")


def _tools_desc(item: dict) -> str:
    fns = item.get("function") or []
    if isinstance(fns, dict):
        fns = [fns]
    lines = []
    for f in fns:
        if not isinstance(f, dict):
            continue
        name = f.get("name", "")
        desc = str(f.get("description", ""))[:200]
        params = (f.get("parameters") or {}).get("properties") or {}
        required = (f.get("parameters") or {}).get("required") or []
        ps = ", ".join(f"{k}{'*' if k in required else ''}" for k in params)
        lines.append(f"- {name}({ps}): {desc}")
    return "\n".join(lines)


def _norm(v) -> str:
    """把值归一成可比较的字符串（数字/布尔/字符串统一；去引号与空白）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    s = str(v).strip().strip('"').strip("'").strip()
    # 数字字符串归一
    try:
        f = float(s)
        return str(int(f)) if f.is_integer() else str(f)
    except Exception:  # noqa: BLE001
        return s.lower()


def _match_ground_truth(pred_name: str, pred_args: dict, gt_list: list) -> bool:
    """简化 AST 判分：函数名一致 + 每个 GT 参数的值命中其可接受集合。"""
    for gt in gt_list or []:
        if not isinstance(gt, dict):
            continue
        for fname, params in gt.items():
            if _norm(fname) != _norm(pred_name):
                continue
            if not isinstance(params, dict):
                return True
            ok = True
            for pname, accepted in params.items():
                acc = accepted if isinstance(accepted, list) else [accepted]
                acc_norm = [_norm(a) for a in acc]
                # 可接受集合含空 → 该参数可省略
                optional = any(a in ("", "none", "null") for a in acc_norm)
                if pname not in pred_args:
                    if optional:
                        continue
                    ok = False
                    break
                if _norm(pred_args[pname]) not in acc_norm:
                    ok = False
                    break
            if ok:
                return True
    return False


def run(adapter, limit_per_cat: int = 40) -> dict:
    """跑真实 BFCL（simple + multiple）。缺数据→SKIP（绝不假造分数）。"""
    files = _find_data_files()
    if not files:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": detect()["hint"]}

    # V322：走预设系统，默认可比（standard=50 总题数）。tool_calling 是按类别取，
    # 把"总目标"除以类别数换算成每类题数——与其它基准"默认 50 可比"口径一致。
    from .sample_stats import resolve_limit
    n_cats = max(1, len(files))
    total_target = resolve_limit("tool_calling", limit_per_cat * n_cats)
    limit_per_cat = max(1, total_target // n_cats)

    passed = total = 0
    fails: list[str] = []
    per_cat: dict = {}
    cases: list[dict] = []

    # V324 并发：把所有类别的题铺平，并行发 LLM，再按类聚合（每题独立）。
    work: list[dict] = []
    for cat, (data_f, ans_f) in sorted(files.items()):
        items = _load_jsonl(data_f)[:limit_per_cat]
        answers = {a.get("id"): a.get("ground_truth") for a in _load_jsonl(ans_f)}
        per_cat.setdefault(cat, [0, 0])
        for it in items:
            iid = it.get("id")
            gt = answers.get(iid)
            if gt is None:
                continue
            per_cat[cat][1] += 1
            work.append({"cat": cat, "it": it, "iid": iid, "gt": gt})

    def _one(w: dict) -> dict:
        it, iid, gt = w["it"], w["iid"], w["gt"]
        prompt = (
            f"可用函数：\n{_tools_desc(it)}\n\n"
            f"用户请求：{_question_text(it)}\n\n"
            '只输出一个 JSON：{"name": "函数名", "arguments": {"参数名": 值}}。不要解释、不要代码块。'
        )
        try:
            raw = adapter.answer(prompt)
        except Exception as e:  # noqa: BLE001
            return {**w, "err": f"调用失败 {e}", "ok": False}
        m = re.search(r"\{[\s\S]*\}", str(raw))
        try:
            d = json.loads(m.group(0)) if m else {}
        except Exception:  # noqa: BLE001
            d = {}
        name = d.get("name") or d.get("tool") or d.get("function") or ""
        args = d.get("arguments") or d.get("args") or d.get("parameters") or {}
        if not isinstance(args, dict):
            args = {}
        ok = _match_ground_truth(str(name), args, gt)
        return {**w, "ok": ok, "name": str(name), "args": args}

    from .parallel import run_parallel
    results = run_parallel(work, _one)
    for r in results:
        cat, iid, gt = r["cat"], r["iid"], r["gt"]
        total += 1
        if r.get("err"):
            if len(fails) < 5:
                fails.append(f"{iid}: {r['err']}")
            continue
        ok = bool(r.get("ok"))
        name, args = r.get("name", ""), r.get("args", {})
        if ok:
            passed += 1
            per_cat[cat][0] += 1
        elif len(fails) < 5:
            fails.append(f"{iid}: 预测 {name[:20]}{list(args)[:3]}")
        gt_name = ""
        try:
            gt_name = list(gt[0].keys())[0] if gt and isinstance(gt[0], dict) else ""
        except Exception:  # noqa: BLE001
            pass
        cases.append({
            "id": str(iid)[:16], "cat": cat, "ok": ok,
            "q": _question_text(r["it"])[:100],
            "pred": f"{name}({','.join(list(args)[:4])})"[:60],
            "gold": str(gt_name)[:40],
            "why": ("" if ok else ("选错函数" if name != gt_name else "参数不对")),
        })
    per_cat = {c: f"{o}/{n}" for c, (o, n) in per_cat.items()}

    if total == 0:
        return {"kind": "official", "skip": True, "score_pct": None,
                "detail": "BFCL 数据已存在但解析不出可评测样本（目录结构可能变了，见 bfcl.py::_find_data_files）"}
    score = round(100.0 * passed / total, 1)
    return {
        "kind": "official", "skip": False,
        "passed": passed, "total": total, "score_pct": score,
        "breakdown": {**per_cat,
                      "选错函数": str(sum(1 for c in cases if c.get("why") == "选错函数")),
                      "参数不对": str(sum(1 for c in cases if c.get("why") == "参数不对")),
                      "判分口径": "官方数据·简化AST(函数名+参数值命中)"},
        "fails": fails[:5],
        "cases": cases,
        "detail": f"BFCL 官方数据（simple+multiple，各取前 {limit_per_cat} 例）",
    }
