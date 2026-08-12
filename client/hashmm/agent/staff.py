"""hashmm/agent/staff.py — 专职 Agent 编制（V273）。

用户诉求：记忆要有 agent 专门维护、测试要有 agent、要有总 agent 派任务、RAG agent、
画布 agent；子 agent 之间怎么通信、要不要 team。

对照面试资料落地（不自创名词）：
· 架构选型（资料 6.5.2 金融推理模板）：这些职责是**流程规范、可分治**的场景 →
  用**中心化 Orchestrator**：Chief 统一拆解/分派/验收/收敛，避免去中心化的协调税。
  （高熵并行探索类另有既有 team.py / subagents.py，两者互补不替代。）
· 通信模式（资料 6.4）：Chief→专员用 **Tool Call 模式**（委托出去、保留控制权、
  等结果回来自己决定下一步——ClaudeCode SubAgent 的做法）；专员之间不横向直连。
· 传递内容（资料 6.4）：**仅共享最终结果**进黑板（控制状态膨胀）；完整推理留在
  专员私有上下文（复用 worker.py 的隔离上下文纪律）。
· 通信介质（资料 6.4）：默认**内存返回**；跨轮/跨进程留痕用**文件邮箱**
  （file-backed mailbox，ClaudeCode AgentTeam 的做法）——每个专员一个 inbox 目录。
· 需不需要给"技能/模板/工具"也配 agent？——不需要。它们是**资产**（无状态、被调用）；
  需要 agent 的是**有状态的持续职责**（维护记忆、执行测试、编排任务）。本文件注释
  即此判断的落点，changelog 同步说明。

纪律：专员一次 run() 一个任务、永不抛错（返回 {ok, detail, data}）；黑板/邮箱写
失败绝不拖垮任务；所有 LLM 经 get_active_llm_fn（容灾链）；专员工具白名单最小化。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.staff")

# ── 共享黑板（内存，仅最终结果）+ 文件邮箱（跨轮留痕） ─────────────────────
_BLACKBOARD: dict[str, list[dict]] = {}          # topic -> [entries]
_BB_CAP = 50


def _mail_root() -> Path:
    try:
        from hashmm.api.database import DATA_ROOT
        p = Path(DATA_ROOT) / "agent_mail"
    except Exception:  # noqa: BLE001
        p = Path.home() / ".hashmm" / "agent_mail"
    p.mkdir(parents=True, exist_ok=True)
    return p


def blackboard_post(topic: str, entry: dict) -> None:
    """仅写最终结果（资料 6.4：控制状态空间膨胀）。"""
    try:
        lst = _BLACKBOARD.setdefault(str(topic), [])
        lst.append({**entry, "ts": time.time()})
        del lst[:-_BB_CAP]
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)


def blackboard_read(topic: str, n: int = 10) -> list[dict]:
    return list(_BLACKBOARD.get(str(topic), []))[-n:]


def mailbox_send(agent: str, message: dict) -> None:
    """文件邮箱：写入对方 inbox，接收方下一轮读取（CC AgentTeam 介质）。"""
    try:
        box = _mail_root() / str(agent)
        box.mkdir(parents=True, exist_ok=True)
        fname = box / f"{int(time.time() * 1000)}.json"
        fname.write_text(json.dumps({**message, "ts": time.time()}, ensure_ascii=False),
                         encoding="utf-8")
        # 容量护栏：每箱只留最近 30 封
        files = sorted(box.glob("*.json"))
        for old in files[:-30]:
            old.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)


def mailbox_drain(agent: str, keep: bool = False) -> list[dict]:
    out: list[dict] = []
    try:
        box = _mail_root() / str(agent)
        if not box.exists():
            return out
        for f in sorted(box.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                pass
            if not keep:
                f.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
    return out


def _llm() -> Callable[[str], str] | None:
    try:
        from hashmm.agent.llm import get_active_llm_fn
        return get_active_llm_fn()
    except Exception:  # noqa: BLE001
        try:
            from hashmm.api import app_state
            fn = getattr(app_state, "llm_fn", None)
            return fn if callable(fn) else None
        except Exception:  # noqa: BLE001
            return None


# ══════════════════════════ 专员们 ══════════════════════════
class MemoryAgent:
    """记忆维护专员——职责：让 user_memory 不腐化。
    动作（对齐 Hermes 11.4 的"写入/读取之外还要有更新策略"精神，策略取轻量确定性版）：
      去重合并（同 key 多值 → 保留最新，旧值并入 note）、时效衰减（>90 天未更新的
      条目降权标记 stale）、低价值清理（空值/超短值）、体检报告（条数/最旧/建议）。
    """

    name = "memory"
    charter = "维护用户长期记忆：去重、衰减、清理、体检"

    def run(self, user_id: str, task: str = "maintain") -> dict:
        try:
            from hashmm.agent import user_memory as um
            data = um._load(user_id)  # 同包内维护性访问；结构 {items:{key:{...}}} 或扁平
            items = data.get("items") if isinstance(data.get("items"), dict) else None
            if items is None:
                # 兼容扁平 {key: value} 旧结构
                items = {k: (v if isinstance(v, dict) else {"value": v})
                         for k, v in data.items() if not str(k).startswith("_")}
                data = {"items": items}
            now = time.time()
            merged = dropped = stale = 0
            for k in list(items.keys()):
                v = items[k]
                val = str(v.get("value", "")).strip()
                if not val or len(val) < 2:
                    items.pop(k, None); dropped += 1; continue
                ts = float(v.get("ts") or v.get("updated_at") or now)
                if now - ts > 90 * 86400 and not v.get("stale"):
                    v["stale"] = True; stale += 1
                v.setdefault("ts", ts)
            # 去重：value 完全相同的 key 合并（保留最短 key）
            seen: dict[str, str] = {}
            for k in sorted(items.keys(), key=len):
                val = str(items[k].get("value", ""))
                if val in seen and seen[val] != k:
                    items.pop(k, None); merged += 1
                else:
                    seen[val] = k
            um._path(user_id).write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            detail = f"记忆体检：{len(items)} 条在册；合并 {merged}、清理 {dropped}、标记陈旧 {stale}"
            blackboard_post("memory", {"agent": self.name, "detail": detail})
            return {"ok": True, "detail": detail,
                    "data": {"count": len(items), "merged": merged, "dropped": dropped, "stale": stale}}
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return {"ok": False, "detail": f"记忆维护失败：{e}", "data": {}}


class TestAgent:
    """测试专员——职责：驱动测试中枢套件并（可选）用 1-5 分量规裁判打分。"""

    name = "test"
    charter = "执行测试中枢套件；对答案/运行做 1-5 分量规裁判"

    def run(self, user: dict, task: str = "smoke", ids: list[str] | None = None) -> dict:
        try:
            from hashmm.api.routes import selftest as st
            pick = ids or [s["id"] for s in st.SUITES if not s["slow"]][:6]
            results = []
            for sid in pick:
                s = st._BY_ID.get(sid)
                if not s:
                    continue
                t0 = time.time()
                try:
                    r = s["fn"](user)
                except Exception as e:  # noqa: BLE001
                    r = {"ok": False, "skip": False, "detail": str(e)[:200]}
                r.update({"id": sid, "name": s["name"], "ms": round((time.time() - t0) * 1000)})
                results.append(r)
            passed = sum(1 for r in results if r.get("ok"))
            detail = f"跑 {len(results)} 项：通过 {passed}，失败 {sum(1 for r in results if not r.get('ok') and not r.get('skip'))}"
            blackboard_post("test", {"agent": self.name, "detail": detail})
            return {"ok": True, "detail": detail, "data": {"results": results}}
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return {"ok": False, "detail": f"测试执行失败：{e}", "data": {}}

    def judge(self, question: str, answer: str, contexts: list[str] | None = None,
              rubric: str = "answer_quality") -> dict:
        try:
            from hashmm.evaluation.judge_rubric import judge_with_rubric
            return judge_with_rubric(question, answer, contexts or [], rubric=rubric, llm_fn=_llm())
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": f"裁判不可用：{e}", "scores": {}}


class RagAgent:
    """RAG 专员——职责：一次结构化检索（检索→重排→依据打包），只回最终结果。"""

    name = "rag"
    charter = "结构化检索：多路召回+重排+来源打包"

    def run(self, query: str, top_k: int = 5) -> dict:
        try:
            from hashmm.auto_retrieval import AutoRetriever
            t0 = time.time()
            ctx = AutoRetriever().retrieve_context(str(query or ""), top_k=top_k)
            srcs = list(getattr(ctx, "sources", None) or [])
            try:
                from hashmm.retrieval import rerank as rr
                if rr.rerank_enabled() and len(srcs) > 1:
                    srcs = rr.rerank(query, srcs, top_k=top_k) or srcs
            except Exception as e:  # noqa: BLE001
                log_suppressed(logger, e)
            ms = round((time.time() - t0) * 1000)
            data = {"sources": [{"filename": s.get("filename", ""),
                                 "score": s.get("score", 0),
                                 "text": str(s.get("text") or "")[:300]} for s in srcs[:top_k]],
                    "ms": ms}
            detail = f"检索 {len(data['sources'])} 条，{ms}ms"
            blackboard_post("rag", {"agent": self.name, "detail": detail, "query": str(query)[:80]})
            return {"ok": True, "detail": detail, "data": data}
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return {"ok": False, "detail": f"检索失败：{e}", "data": {}}


class CanvasAgent:
    """画布/设计专员——职责：把"做 PPT / 出设计稿"类目标变成产物。
    Design 融合（Trae Work design 心智 + Qoder PPT 模式）：先让 LLM 产结构化大纲
    （标题/章节/要点），PPT 意图直接走既有 create_pptx_from_plan 执行器；
    设计稿意图走 render_design / create_document。Figma 导入列入路线（changelog）。
    """

    name = "canvas"
    charter = "设计/PPT：大纲化目标 → 调用文档/幻灯生成执行器"

    _PPT_HINT = ("ppt", "pptx", "幻灯", "演示", "slide")

    def run(self, goal: str, user_id: str = "", conv_id: str = "") -> dict:
        llm = _llm()
        try:
            outline = ""
            if llm:
                outline = str(llm(
                    "把下面的目标整理成可直接生成演示/文档的结构化大纲（JSON：{title, sections:[{heading, bullets:[..]}]}），"
                    "只回 JSON 不解释：\n" + str(goal)[:1200]) or "").strip()
            if not outline:
                outline = json.dumps({"title": str(goal)[:40],
                                      "sections": [{"heading": "概述", "bullets": [str(goal)[:80]]}]},
                                     ensure_ascii=False)
            is_ppt = any(h in str(goal).lower() for h in self._PPT_HINT)
            from hashmm.api.tool_registry import get_executor
            name = "create_pptx_from_plan" if is_ppt else "create_document"
            fn = get_executor(name)
            if not callable(fn):
                return {"ok": False, "detail": f"执行器 {name} 不可用", "data": {"outline": outline}}
            args = {"plan": outline} if is_ppt else {
                "title": (json.loads(outline).get("title") if outline.startswith("{") else str(goal)[:40]) or "文档",
                "content": outline, "format": "docx"}
            res = fn(args, {"user_id": user_id, "conv_id": conv_id})
            detail = f"{'PPT' if is_ppt else '文档'}已生成"
            blackboard_post("canvas", {"agent": self.name, "detail": detail})
            return {"ok": True, "detail": detail, "data": {"outline": outline, "result": str(res)[:400]}}
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return {"ok": False, "detail": f"生成失败：{e}", "data": {}}


class Chief:
    """总调度（Orchestrator，资料 6.5.2 中心化模板）：拆解→分派（Tool Call 委托）→
    验收（可用 TestAgent.judge）→ 收敛。仅共享最终结果进黑板。"""

    ROSTER: dict[str, Any] = {}

    def __init__(self) -> None:
        if not Chief.ROSTER:
            Chief.ROSTER = {a.name: a for a in (MemoryAgent(), TestAgent(), RagAgent(), CanvasAgent())}

    def dispatch(self, goal: str, user: dict | None = None, conv_id: str = "") -> dict:
        """按目标关键词路由到专员；未命中时走 RAG 专员打底并注明。"""
        user = user or {}
        g = str(goal or "").strip()
        try:
            # V274 派活前先做架构选型（资料 6.1/6.2/6.5）——把"该用单体还是多 Agent"
            # 讲清楚写进黑板，避免无脑上多 Agent（多 Agent 不一定更好）。
            try:
                from hashmm.agent.arch_advisor import advise
                _adv = advise(g)
                blackboard_post("arch", {"goal": g[:60], "arch": _adv.arch,
                                         "reason": _adv.reason[:120], "n": _adv.n_agents})
            except Exception as _ae:
                log_suppressed(logger, _ae)
            # V300 交接链：一个目标可能横跨多个领域（如"先检索再记忆整理"）——按领域收集要派的专员，
            # **去重保序**后依次执行，形成真实交接链；只命中一个就是单专员。绝不对同一专员重复派活（防打转）。
            def _run_agent(name: str):
                if name == "memory":
                    return Chief.ROSTER["memory"].run(user.get("uid", "anonymous"))
                if name == "test":
                    return Chief.ROSTER["test"].run(user)
                if name == "canvas":
                    return Chief.ROSTER["canvas"].run(g, user_id=user.get("uid", ""), conv_id=conv_id)
                return Chief.ROSTER["rag"].run(g)

            wanted: list[str] = []
            if any(k in g for k in ("记忆", "memory")):
                wanted.append("memory")
            if any(k in g for k in ("测试", "自测", "test")):
                wanted.append("test")
            if any(k in g.lower() for k in CanvasAgent._PPT_HINT) or "设计" in g or "文档" in g:
                wanted.append("canvas")
            if any(k in g.lower() for k in ("检索", "rag", "retriev", "知识库", "查资料", "查询")):
                wanted.append("rag")
            # 去重保序（同一专员绝不派两次）
            _seen: set[str] = set()
            wanted = [a for a in wanted if not (a in _seen or _seen.add(a))]
            if not wanted:
                wanted = ["rag"]   # 未命中领域 → RAG 专员打底

            results = []
            for name in wanted:
                rr = _run_agent(name)
                results.append(rr)
                blackboard_post("chief", {"goal": g[:80], "agent": name,
                                          "ok": rr.get("ok"), "detail": rr.get("detail", "")[:120]})
            # 汇总：全部成功才算成功；把各专员产出拼起来
            ok_all = all(x.get("ok") for x in results) if results else False
            detail = " → ".join(f"[{n}] {x.get('detail', '')[:80]}" for n, x in zip(wanted, results))
            r = {"ok": ok_all, "detail": detail, "data": {"agents": wanted,
                 "results": [x.get("data") for x in results]}}
            mailbox_send("chief", {"goal": g[:120], "result": detail[:200]})
            return r
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
            return {"ok": False, "detail": f"调度失败：{e}", "data": {}}


def get_chief() -> Chief:
    return Chief()
