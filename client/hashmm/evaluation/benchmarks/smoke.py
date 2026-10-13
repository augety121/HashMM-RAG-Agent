"""内置 smoke 任务——**只验证"接线通不通"，不产生任何可对标的分数**。

V306 修正（重要）：此前 smoke 把"1 个玩具任务通过"算成 100%，还拿去和 Claude Code 的公开分比，
得出"已达到/超过全部参照"——这是**误导性的假分数**。现在 smoke 只回答一个是非题：
"HashMM 的 agent 能不能被这个基准的适配器驱动、结果能不能被评分" → 通过/失败，**不给分数、不对标**。
真实分数只能来自 official 模式（真实基准数据集）。
"""
from __future__ import annotations


def _pipeline_result(ok: bool, what: str, extra: str = "") -> dict:
    return {"kind": "smoke", "pipeline_ok": bool(ok), "score_pct": None,
            "passed": 1 if ok else 0, "total": 1,
            "detail": f"适配管线自检{'通过' if ok else '失败'}（{what}）——"
                      f"**这不是基准分数**，只证明 agent 能被该基准驱动。{extra}"}


def swebench_smoke(adapter) -> dict:
    from hashmm.tools import agent_bench as AB
    scorers = [AB.file_exists("solution.py"), AB.file_contains("solution.py", "def add"),
               AB.py_compiles("solution.py")]
    r = adapter.run_task("创建 solution.py，实现 add(a,b) 返回两数之和", scorers,
                         task_id="swe_smoke", max_seconds=60)
    return _pipeline_result(r.get("status") == "PASS", "写代码→落盘→可编译")


def terminal_smoke(adapter) -> dict:
    from hashmm.tools import agent_bench as AB
    r = adapter.run_task("创建 out.txt 并写入文本 done", [AB.file_exists("out.txt")],
                         task_id="term_smoke", max_seconds=60)
    return _pipeline_result(r.get("status") == "PASS", "工具执行→文件产出")


def tau2_smoke(adapter) -> dict:
    cases = [("现在气温 5 度，我要不要穿厚外套？", "要"), ("现在气温 25 度，我要不要穿厚外套？", "不")]
    ok = 0
    for q, expect in cases:
        try:
            a = adapter.answer(q + "（只答'要'或'不要'加一句理由）")
        except Exception:  # noqa: BLE001
            a = ""
        hit = ("不要" in a or a.strip().startswith("不")) if expect == "不" else \
              ("要" in a and not a.strip().startswith("不"))
        ok += 1 if hit else 0
    return _pipeline_result(ok == len(cases), f"条件策略 {ok}/{len(cases)}")


def kotlin_smoke(adapter) -> dict:
    from hashmm.tools import agent_bench as AB
    r = adapter.run_task("创建 Solution.kt，写一个 Kotlin 函数 sum(a: Int, b: Int): Int 返回两数之和",
                         [AB.file_exists("Solution.kt"), AB.file_contains("Solution.kt", "fun ")],
                         task_id="kotlin_smoke", max_seconds=60)
    return _pipeline_result(r.get("status") == "PASS", "生成 Kotlin 函数")
