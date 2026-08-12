"""hashmm/evaluation/multiagent_eval.py — 多 Agent 协作评测（V284，严格按资料 3.3.4.4）。

资料原文：多 Agent 评测不是"问裁判这个团队好不好"，而是三层可执行测法：
  · **方法一·交接轨迹评测**：定义标准协作链（如 Chief→RAG→Memory），跑真实 trace，检查
    Agent 选择正确率 / 交接字段完整率 / 交接顺序 / 端到端成功。
  · **方法二·消融评测**：固定 evalset，跑 Full Team vs 去掉某 Agent vs Single，比成功率差值/成本，
    看每个 Agent 有没有真实贡献、是不是过度设计。
  · **方法三·过程监控**：抓重复调用/无效交接/冲突/打转等协作病征。

落地到本项目 staff（Chief 中心化调度 + RAG/Memory/Test/Canvas 专员 + 黑板）：真跑 Chief.dispatch，
从黑板 trace 抽取协作轨迹判分；消融用"派活数 vs 黑板留痕数"近似看专员是否真被调用产出。
纯逻辑 + 可注入（chief_fn）；**永不抛错**。
"""
from __future__ import annotations

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.evaluation.multiagent_eval")


# 多 Agent 协作任务：期望交接链 + 期望产出病征检查
MULTIAGENT_TASKS = [
    {"name": "记忆维护派活", "task": "帮我做一次记忆体检和整理",
     "expect_agent_keywords": ["记忆", "memory"], "expect_no_repeat": True},
    {"name": "检索派活", "task": "在知识库里检索一条关于向量检索的内容",
     "expect_agent_keywords": ["rag", "检索", "retriev"], "expect_no_repeat": True},
    {"name": "混合任务派活", "task": "先检索资料再做一次记忆整理",
     "expect_agent_keywords": ["rag", "检索", "记忆", "memory"], "expect_no_repeat": True},
]


def _extract_trace(dispatch_result: dict, blackboard_snapshot: list[dict]) -> dict:
    """从 dispatch 返回 + 黑板留痕抽取协作轨迹指标。**永不抛错**。"""
    info = {"agents_involved": [], "handoffs": 0, "repeats": 0, "produced": 0}
    try:
        # ★ V313 修评测口径：交接 = Chief 的派活记录（entry 里带 "agent"=专员名）。
        # 旧口径把 entry.get("topic") 也算成"参与者"——协调贴（topic=chief/arch）和
        # 专员的内部进度贴被计成重复交接，造出"平均重复调用=3.0"的假打转（产品端
        # 本就对同一专员去重派活）。现在：只统计派活记录；同一专员真被派两次才算 repeat。
        seen = []
        for entry in blackboard_snapshot or []:
            agent = str(entry.get("agent") or "").strip().lower()
            if agent and agent not in ("chief", "arch"):
                info["agents_involved"].append(agent)
                if agent in seen:
                    info["repeats"] += 1
                seen.append(agent)
            if entry.get("detail") or (entry.get("ok") is not None):
                info["produced"] += 1
        info["handoffs"] = len(info["agents_involved"])
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
    return info


def run_multiagent(chief_fn, blackboard_read_fn, k: int = 1):
    """多 Agent 协作评测：真跑 Chief 派活，从黑板 trace 判交接正确 + 过程病征。返回 SuiteReport。

    chief_fn(task)->dispatch_result；blackboard_read_fn(topic, n)->list（读黑板留痕）。**永不抛错**。
    """
    from hashmm.evaluation.deep_eval import SuiteReport, run_case_ntimes, RunOutcome
    rep = SuiteReport("多Agent协作(交接链+过程监控)")
    if not callable(chief_fn):
        for t in MULTIAGENT_TASKS:
            rep.add(run_case_ntimes(t["name"], None, k, skip_reason="无 Chief 调度入口"))
        return rep

    total_repeats = 0
    total_tasks = 0
    for t in MULTIAGENT_TASKS:
        def run_once(t=t):
            nonlocal total_repeats, total_tasks
            total_tasks += 1
            import time as _time
            since = _time.time()   # V300：只统计本次派活之后新增的黑板留痕
            try:
                result = chief_fn(t["task"])
            except Exception as e:  # noqa: BLE001
                return RunOutcome(False, 0.0, "调度异常", f"{type(e).__name__}: {e}")
            # 读黑板 trace（各 topic 汇总）——**关键修复**：黑板是持久追加的，若读最近 N 条会把
            # 之前任务/之前运行留下的同类条目也算进来，导致"同一专员出现多次"被误判成"团队打转"。
            # 这里用 since 时间戳只保留本次 dispatch 之后新增的条目，repeat 计数才反映真实的本次协作。
            snapshot = []
            if callable(blackboard_read_fn):
                for topic in ("rag", "memory", "test", "canvas"):
                    try:
                        for e in (blackboard_read_fn(topic, 20) or []):
                            if float(e.get("ts", 0)) >= since:
                                snapshot.append(e)
                    except Exception:  # noqa: BLE001
                        pass
            trace = _extract_trace(result if isinstance(result, dict) else {}, snapshot)
            total_repeats += trace["repeats"]
            # 交接正确：期望的 Agent 关键词至少命中一个 + 有产出 + 无过度重复
            involved_blob = " ".join(trace["agents_involved"]) + " " + str(result)[:200].lower()
            agent_hit = any(kw.lower() in involved_blob for kw in t["expect_agent_keywords"])
            produced_ok = trace["produced"] >= 1 or "通过" in str(result) or "完成" in str(result)
            no_excess_repeat = trace["repeats"] <= 1
            passed = agent_hit and produced_ok and (no_excess_repeat or not t["expect_no_repeat"])
            mode = ""
            if not passed:
                if not agent_hit:
                    mode = "Agent选择错误(没派给对的专员)"
                elif not produced_ok:
                    mode = "无有效产出"
                else:
                    mode = "重复调用(团队打转)"
            score = (0.5 * agent_hit + 0.3 * produced_ok + 0.2 * no_excess_repeat)
            problems = []
            if mode == "Agent选择错误(没派给对的专员)":
                problems.append(f"没派给对的专员：期望命中 {t['expect_agent_keywords']}，实际涉及 {trace['agents_involved']}")
            elif mode == "无有效产出":
                problems.append("专员被调用但黑板无有效产出")
            elif mode == "重复调用(团队打转)":
                problems.append(f"团队打转：同类调用重复了 {trace['repeats']} 次")
            rich_trace = {
                "协作任务": t["task"],
                "期望交接链关键词": t["expect_agent_keywords"],
                "实际涉及Agent": trace["agents_involved"] or "（未识别到专员）",
                "交接/调用次数": trace["handoffs"],
                "黑板产出条数": trace["produced"],
                "重复调用次数": trace["repeats"],
                "调度结果": str(result)[:300],
                "问题定位": problems or ["交接链正确、有产出、未打转"],
            }
            return RunOutcome(passed, score, mode,
                              f"涉及{trace['handoffs']}次调用、产出{trace['produced']}、重复{trace['repeats']}｜{str(result)[:60]}",
                              trace=rich_trace)
        rep.add(run_case_ntimes(t["name"], run_once, k))
    rep.extra_metrics = {"平均重复调用": round(total_repeats / total_tasks, 2) if total_tasks else 0.0}
    return rep


def run_ablation(full_fn, single_fn, tasks: list[str] | None = None, k: int = 1):
    """消融评测（资料方法二）：对同一批任务比 Full Team vs Single Agent 成功率差值。返回 dict。

    full_fn(task)->bool（多 Agent 是否成功）；single_fn(task)->bool（单 Agent 是否成功）。**永不抛错**。
    差值>0 说明多 Agent 架构有正向贡献；≈0 或成本高很多 = 可能过度设计。
    """
    tasks = tasks or [t["task"] for t in MULTIAGENT_TASKS]
    out = {"full_success": 0, "single_success": 0, "n": len(tasks), "delta": 0.0, "verdict": ""}
    if not (callable(full_fn) and callable(single_fn)):
        out["verdict"] = "缺少对照函数，消融跳过"
        return out
    try:
        for task in tasks:
            for _ in range(max(1, k)):
                try:
                    if full_fn(task):
                        out["full_success"] += 1
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if single_fn(task):
                        out["single_success"] += 1
                except Exception:  # noqa: BLE001
                    pass
        runs = out["n"] * max(1, k)
        fr = out["full_success"] / runs if runs else 0
        sr = out["single_success"] / runs if runs else 0
        out["delta"] = round(fr - sr, 3)
        out["verdict"] = ("多 Agent 有正向贡献" if out["delta"] > 0.05 else
                          "多 Agent 与单 Agent 相当（注意是否过度设计）" if abs(out["delta"]) <= 0.05 else
                          "多 Agent 反而更差（协作有损耗）")
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        out["verdict"] = f"消融异常 {type(e).__name__}"
    return out
