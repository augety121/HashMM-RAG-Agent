"""HashMM mini Agent-Bench — 端到端 Agent 任务基准（V52）。

把"和大厂对标"从口号变成数字：每个任务驱动【真实 AgentLoop】端到端执行，
用确定性评分器打分（文件存在/可编译/工具使用/事件出现/预算遵守/答案关键词），
输出通过率与 JSON 报告。以后每轮迭代报一个数。

真机用法（需要 LLM 已配置；在项目根目录运行）：
    HASHMM_EVAL_EXEC=1 python -m hashmm.tools.agent_bench              # 全部任务
    python -m hashmm.tools.agent_bench --only code_create,code_edit   # 指定任务
    python -m hashmm.tools.agent_bench --selftest                     # 沙箱自检（脚本化 LLM）

RAG 问答任务依赖语料内容，通过环境变量配置（不配置则跳过）：
    HASHMM_BENCH_RAG_QUERY="你的知识库问题" HASHMM_BENCH_RAG_KEYWORD="期望关键词"

设计要点：
- 每个任务独立 conv 工作区（bench-{id}），跑完即清，不碰 data/ 其他内容；
- requires 声明（llm/exec/retrieval/docgen）：环境不满足 → SKIP 并给原因，不算失败；
- --selftest 用脚本化 LLM 完整走 runner+scorer 链路——harness 本身有回归保护。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import py_compile
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ─────────────────────────── 评分器 ───────────────────────────

@dataclass
class TurnResult:
    answer: str
    events: list  # [(event_type, event_data)]
    faithfulness: float | None = None   # V103.90: 本轮忠实度接地率（--stream 奖励/进步度量用）


@dataclass
class BenchContext:
    """评分器拿到的全部素材：各轮答案/事件 + 会话工作区目录。"""
    conv_id: str
    workspace: Path
    turns: list[TurnResult] = field(default_factory=list)

    @property
    def all_events(self):
        return [e for t in self.turns for e in t.events]

    @property
    def final_answer(self) -> str:
        return self.turns[-1].answer if self.turns else ""


Scorer = Callable[[BenchContext], tuple[bool, str]]  # (通过?, 失败原因)


def file_exists(name: str) -> Scorer:
    def s(ctx):
        ok = (ctx.workspace / name).exists()
        return ok, "" if ok else f"文件不存在: {name}"
    s.__name__ = f"file_exists({name})"
    return s


def file_contains(name: str, *patterns: str, negate: bool = False) -> Scorer:
    def s(ctx):
        p = ctx.workspace / name
        if not p.exists():
            return False, f"文件不存在: {name}"
        text = p.read_text(encoding="utf-8", errors="replace")
        for pat in patterns:
            hit = re.search(pat, text) is not None
            if hit == negate:
                return False, f"{name} {'不应' if negate else '应'}匹配 /{pat}/"
        return True, ""
    s.__name__ = f"file_{'not_' if negate else ''}contains({name})"
    return s


def py_compiles(name: str) -> Scorer:
    def s(ctx):
        p = ctx.workspace / name
        if not p.exists():
            return False, f"文件不存在: {name}"
        try:
            py_compile.compile(str(p), doraise=True)
            return True, ""
        except Exception as e:
            return False, f"{name} 编译失败: {str(e)[:80]}"
    s.__name__ = f"py_compiles({name})"
    return s


def answer_contains(*keywords: str, turn: int = -1) -> Scorer:
    def s(ctx):
        ans = ctx.turns[turn].answer if ctx.turns else ""
        missing = [k for k in keywords if k not in ans]
        return (not missing), (f"答案缺少关键词: {missing}" if missing else "")
    s.__name__ = f"answer_contains{keywords}"
    return s


def tool_used(name: str) -> Scorer:
    def s(ctx):
        used = any(et == "tool_done" and ed.get("name") == name for et, ed in ctx.all_events)
        return used, "" if used else f"未使用工具: {name}"
    s.__name__ = f"tool_used({name})"
    return s


def tool_not_used(name: str) -> Scorer:
    """某工具【未被真实调用】。用于验证 agent 对简单/单跳问题保持克制，不乱调慢工具
    （如 deep_search）。以 tool_done 事件是否出现该工具名判定。"""
    def s(ctx):
        used = any(et == "tool_done" and ed.get("name") == name for et, ed in ctx.all_events)
        return (not used), "" if not used else f"不应调用却调用了工具: {name}"
    s.__name__ = f"tool_not_used({name})"
    return s


def todo_emitted(min_items: int = 2) -> Scorer:
    def s(ctx):
        todos = [ed for et, ed in ctx.all_events if et == "todo"]
        ok = bool(todos) and len(todos[-1].get("items", [])) >= min_items
        return ok, "" if ok else f"未产生 ≥{min_items} 项的任务清单"
    s.__name__ = f"todo_emitted(≥{min_items})"
    return s


def tool_calls_at_most(name: str, limit: int) -> Scorer:
    """某工具【真实执行】次数 ≤ limit（denied/复用不算真执行——以结果文本判定）。"""
    def s(ctx):
        n = sum(1 for et, ed in ctx.all_events
                if et == "tool_done" and ed.get("name") == name
                and "已达本轮上限" not in str(ed.get("result", ""))
                and "重复调用" not in str(ed.get("result", "")))
        return n <= limit, "" if n <= limit else f"{name} 真执行 {n} 次 > 预算 {limit}"
    s.__name__ = f"tool_calls_at_most({name},{limit})"
    return s


def answer_nonempty(min_len: int = 10) -> Scorer:
    def s(ctx):
        ok = len(ctx.final_answer.strip()) >= min_len
        return ok, "" if ok else "最终回答为空/过短"
    s.__name__ = "answer_nonempty"
    return s


def html_wellformed(name: str) -> Scorer:
    """V68: HTML 良构检查（design 任务用）——能被标准 html.parser 完整吃下即通过。"""
    def s(ctx):
        fp = ctx.workspace / name
        if not fp.exists():
            return False, f"{name} 不存在"
        try:
            import html.parser
            html.parser.HTMLParser().feed(fp.read_text(encoding="utf-8", errors="replace"))
            return True, ""
        except Exception as e:
            return False, f"HTML 解析失败: {e}"
    s.__name__ = "html_wellformed"
    return s


def answer_matches(pattern: str, desc: str = "") -> Scorer:
    """V78: 终答正则匹配（如必须含 [N] 引用）。"""
    def s(ctx):
        ok = bool(re.search(pattern, ctx.final_answer or ""))
        return ok, "" if ok else f"终答不匹配 {desc or pattern}"
    s.__name__ = f"answer_matches({desc or pattern})"
    return s


def event_detail_contains(substr: str) -> Scorer:
    """V78: 事件流证据检查——任一 trace/事件的字符串化内容含子串即通过。

    用于验证 harness 行为真的发生过（DoD 完成、引用校验通过、工具被调）。
    """
    def s(ctx):
        for ev in ctx.all_events:
            try:
                if substr in str(ev):
                    return True, ""
            except Exception:
                continue
        return False, f"事件流中未见: {substr}"
    s.__name__ = f"event_contains({substr})"
    return s


def json_valid(name: str) -> Scorer:
    """V78: workspace 下 JSON 文件合法。"""
    def s(ctx):
        fp = ctx.workspace / name
        if not fp.exists():
            return False, f"{name} 不存在"
        try:
            json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            return True, ""
        except Exception as e:
            return False, f"JSON 非法: {str(e)[:80]}"
    s.__name__ = f"json_valid({name})"
    return s


# ─────────────────────────── 任务定义 ───────────────────────────

@dataclass
class Task:
    id: str
    category: str
    turns: list  # list[str] 或 list[(query, history_seed)]；history_seed 仅首轮可用
    scorers: list
    requires: set = field(default_factory=lambda: {"llm"})
    history_seed: list = field(default_factory=list)  # 首轮注入的历史（长对话任务用）
    max_seconds: int = 180
    # ★ V310：单轮内的【工具调用步数预算】。None = 沿用 AgentLoop 默认（10 步）。
    # 为什么必须有它：AgentLoop 的 MAX_ITERATIONS=10 是为【聊天场景】定的，但外部基准
    # （Terminal-bench / SWE-bench）的一道题需要「读数据→写脚本→跑→报错→改→再跑→验证」，
    # 大厂 harness 给的是 50~200 步。10 步会让 agent 在半路被强制截停 —— 产物是半成品，
    # 官方测试跑得起来但断言失败。这正是 Terminal-bench 恒 0（且失败信息是 AssertionError
    # 而非「文件不存在」）的真正原因：不是模型不会做，是没做完就被掐了。
    max_iterations: int | None = None
    # 同理：MAX_TOOL_CALLS=24 / MAX_EXEC_CALLS=5 也是聊天护栏。基准任务的"改-跑-验证"
    # 循环光 exec 就不止 5 次 → 一并可覆盖。None = 沿用默认。
    max_tool_calls: int | None = None
    max_exec_calls: int | None = None


def _long_memory_seed() -> list[dict]:
    h = [{"role": "user", "content": "帮我用 Python 实现一个快速排序，这是本会话的主线任务"},
         {"role": "assistant", "content": "好的，主线任务记下了：快速排序。我们先讨论细节。" + "细" * 1500}]
    for i in range(24):
        h.append({"role": "user", "content": f"追问{i}：边界情况{i}怎么处理"})
        h.append({"role": "assistant", "content": f"边界{i}的处理方式如下。" + "答" * 1500})
    return h


# V56: 预算阈值跟随循环常量（此前硬编码 3，V55 提升上限后会误报 FAIL）
try:
    from hashmm.agent.loop import MAX_EXEC_CALLS as _MAX_EXEC, MAX_SEARCH_CALLS as _MAX_SEARCH
except Exception:
    _MAX_EXEC, _MAX_SEARCH = 5, 3

TASKS: list[Task] = [
    Task("code_create", "代码生成",
         turns=["用 Python 写一个快速排序函数，保存为 quicksort.py。不需要运行。"],
         scorers=[file_exists("quicksort.py"), file_contains("quicksort.py", r"def\s+\w+"),
                  py_compiles("quicksort.py"), answer_nonempty()]),
    Task("code_edit", "编辑闭环",
         turns=["创建 calc.py，内容是一个 add(a, b) 函数返回 a+b。不要运行。",
                "把 calc.py 里的函数名 add 改成 plus，用精确编辑，不要重写整个文件。"],
         scorers=[tool_used("str_replace"), file_contains("calc.py", r"def\s+plus"),
                  file_contains("calc.py", r"def\s+add\b", negate=True)]),
    Task("multi_file", "多文件任务",
         turns=["创建两个文件：utils.py（一个 greet(name) 函数）和 main.py（调用它）。不要运行。"],
         scorers=[file_exists("utils.py"), file_exists("main.py"),
                  py_compiles("utils.py")]),
    Task("workspace_recall", "工作区记忆",
         turns=["创建一个 notes.md，内容写'基准测试备忘'。",
                "这个会话里目前生成过哪些文件？"],
         scorers=[answer_contains("notes.md")]),
    Task("todo_planning", "任务规划",
         turns=["完成一个三步任务并全程维护任务清单：1) 创建 step1.txt 内容 A；"
                "2) 创建 step2.txt 内容 B；3) 最后汇总说明。"],
         scorers=[todo_emitted(3), file_exists("step1.txt"), file_exists("step2.txt")]),
    Task("exec_budget_guard", "循环防御",
         turns=["写一段必然报错的 Python 代码（比如除以零），反复运行它直到成功为止，不许放弃。"],
         scorers=[tool_calls_at_most("execute_code", _MAX_EXEC), answer_nonempty()],
         requires={"llm", "exec"}),
    Task("long_memory", "长对话记忆",
         turns=["我最开始让你做的主线任务是什么？一句话回答。"],
         history_seed=_long_memory_seed(),
         scorers=[answer_contains("快速排序")]),
    Task("search_budget", "循环防御",
         turns=["连续搜索知识库 10 次不同关键词之后再回答'什么是 RAG'。"],
         scorers=[tool_calls_at_most("kb_search", _MAX_SEARCH), answer_nonempty()]),
    Task("design_page", "设计技能",
         turns=["帮我设计一个简洁的个人作品集落地页，审美要克制高级，"
                "输出单个完整 HTML 文件保存为 portfolio.html。不需要运行。"],
         scorers=[file_exists("portfolio.html"),
                  file_contains("portfolio.html", r"<html", r"<style"),
                  html_wellformed("portfolio.html"),
                  answer_nonempty()],
         max_seconds=300),
    Task("cpp_code", "代码生成",
         turns=["用 C++ 写一个线程安全的环形缓冲区类，保存为 ring_buffer.hpp，不需要编译运行"],
         scorers=[file_exists("ring_buffer.hpp"),
                  file_contains("ring_buffer.hpp", r"#include", r"class"),
                  answer_nonempty()]),
    Task("json_config", "代码生成",
         turns=["生成一份 RAG 系统的示例配置文件 config.json，包含 retrieval、llm、cache 三个配置段"],
         scorers=[json_valid("config.json"),
                  file_contains("config.json", r"retrieval", r"llm"),
                  answer_nonempty()]),
    Task("refactor", "代码编辑",
         turns=["写一个 Python 文件 calc.py，里面实现函数 def f(a, b) 返回 a+b",
                "把 calc.py 里的函数 f 重命名为 add，并加上类型标注和 docstring，保持行为不变"],
         scorers=[file_exists("calc.py"),
                  file_contains("calc.py", r"def add", r'"""'),
                  py_compiles("calc.py"),
                  answer_nonempty()]),
    Task("error_recovery", "鲁棒性",
         turns=["先读取文件 nonexistent_data_xyz.csv 的内容；如果读不到，就自己创建一份"
                "包含 3 行示例销售数据（日期,产品,金额）的 sales.csv，然后告诉我数据概况"],
         scorers=[file_exists("sales.csv"),
                  file_contains("sales.csv", r","),
                  answer_nonempty()],
         max_seconds=240),
    Task("data_report", "数据分析",
         turns=["创建一份 CSV 数据 scores.csv（10 个学生的语文/数学成绩，自己编合理数据），"
                "然后分析平均分和最高分，把分析结论写进 report.md"],
         scorers=[file_exists("scores.csv"),
                  file_exists("report.md"),
                  file_contains("report.md", r"平均|均分"),
                  answer_nonempty()],
         max_seconds=300),
    Task("citation_qa", "知识检索",
         turns=["从知识库检索：检索增强生成（RAG）的核心流程是什么？回答必须用 [编号] 标注出处"],
         scorers=[answer_matches(r"\[\d+\]", "含[N]引用"),
                  answer_nonempty(min_len=50)],
         max_seconds=240),
    Task("fusion_compare", "知识检索",
         turns=["对比知识库中关于向量检索和关键词检索的优缺点（这是跨概念对比问题，"
                "建议用多个查询变体检索）"],
         scorers=[event_detail_contains("kb_search"),
                  answer_nonempty(min_len=80)],
         max_seconds=300),
    Task("multi_step_dod", "任务规划",
         turns=["完成一个三步任务并用任务清单跟踪：1) 创建 utils.py 实现 is_prime 函数；"
                "2) 创建 main.py 调用它打印 100 内的素数；3) 创建 README.md 说明用法"],
         scorers=[file_exists("utils.py"),
                  file_exists("main.py"),
                  file_exists("README.md"),
                  py_compiles("utils.py"),
                  answer_nonempty()],
         max_seconds=360),
    Task("pipeline_report", "多专员协作",
         turns=["写一份关于'检索增强生成（RAG）技术'的简短调研报告保存为 rag_survey.md："
                "先从知识库收集材料（可以派检索专员），再成文（可以派写作专员），"
                "报告要有'核心流程'和'关键挑战'两个小节"],
         scorers=[file_exists("rag_survey.md"),
                  file_contains("rag_survey.md", r"核心流程", r"挑战"),
                  answer_nonempty()],
         max_seconds=420),
    Task("rag_qa", "知识检索",
         turns=[os.environ.get("HASHMM_BENCH_RAG_QUERY", "")],
         scorers=[tool_used("kb_search"),
                  answer_contains(os.environ.get("HASHMM_BENCH_RAG_KEYWORD", "__unset__"))],
         requires={"llm", "retrieval", "rag_env"}),

    # ── 深度检索决策（agent 是否在该用 deep_search 时自主用、不该用时克制）──
    # 多跳/跨文档/对比计算题 → 期望调用 deep_search；简单单跳题 → 期望【不】调用（用 kb_search 即可）。
    # 题面可用环境变量覆盖以贴合你的语料；默认用已知多跳金标准题。
    Task("ds_mh_ratio", "深度检索决策",
         turns=[os.environ.get("HASHMM_BENCH_DS_MH1",
                "网易截至2023年12月31日的少数股东权益，是远见医疗2023年总营收的多少倍？"
                "（需分别从两家公司的资料取数再计算）")],
         scorers=[tool_used("deep_search"), answer_nonempty(8)],
         requires={"llm", "retrieval"}, max_seconds=300),
    Task("ds_mh_compare", "深度检索决策",
         turns=[os.environ.get("HASHMM_BENCH_DS_MH2",
                "晨光教育的核心产品里，有一款和鸿图数据最早发布的产品仅前缀不同，"
                "鸿图数据那款产品是哪一年发布的？")],
         scorers=[tool_used("deep_search"), answer_nonempty(4)],
         requires={"llm", "retrieval"}, max_seconds=300),
    Task("ds_simple_kb", "深度检索决策",
         turns=[os.environ.get("HASHMM_BENCH_DS_SIMPLE1", "小米2024年的总营收是多少？")],
         scorers=[tool_not_used("deep_search"), answer_nonempty(4)],
         requires={"llm", "retrieval"}, max_seconds=180),
    Task("ds_simple_def", "深度检索决策",
         turns=[os.environ.get("HASHMM_BENCH_DS_SIMPLE2", "RAG 的中文全称是什么？")],
         scorers=[tool_not_used("deep_search"), answer_nonempty(4)],
         requires={"llm", "retrieval"}, max_seconds=180),
]


# ─────────────────────────── Runner ───────────────────────────

def _detect_capabilities(llm_fn) -> set:
    caps = set()
    if llm_fn is not None and hasattr(llm_fn, "call_with_tools"):
        caps.add("llm")
    if os.environ.get("HASHMM_EVAL_EXEC") == "1":
        caps.add("exec")
    if os.environ.get("HASHMM_BENCH_RAG_QUERY") and os.environ.get("HASHMM_BENCH_RAG_KEYWORD"):
        caps.add("rag_env")
    try:
        # V52.1: 用 VectorIndex 的真实索引目录探测（此前猜文件名在 DATA_ROOT 根下，
        # 真机有 5576 向量却被误报 retrieval 缺失）
        from hashmm.vector_index import INDEX_DIR
        if (Path(INDEX_DIR) / "faiss.index").exists():
            caps.add("retrieval")
    except Exception:
        pass
    return caps


async def _run_turn(llm_fn, conv_id: str, query: str, history: list, *,
                    mem=None, user: str = "bench", inject_hints: bool = False,
                    max_iterations: int | None = None,
                    max_tool_calls: int | None = None,
                    max_exec_calls: int | None = None) -> TurnResult:
    from hashmm.agent.loop import AgentLoop, MAX_ITERATIONS
    # V103.90: --stream 模式下，从隔离记忆库召回策略提示注入系统提示（第二遍带记忆）。
    sys_prompt = ""
    if mem is not None and inject_hints:
        try:
            sys_prompt = mem.get_strategy_hint(user, query) or ""
        except Exception:
            sys_prompt = ""
    # V310：步数预算。默认沿用 AgentLoop 的 10（聊天场景够用）；外部基准显式加大。
    _iters = int(max_iterations) if max_iterations else MAX_ITERATIONS
    loop = AgentLoop(llm_fn=llm_fn, system_prompt=sys_prompt, user_id=user, conv_id=conv_id,
                     max_iterations=_iters, max_tool_calls=max_tool_calls,
                     max_exec_calls=max_exec_calls)
    loop.plan_confirmed = True
    events = []
    async for et, ed in loop.run(query=query, history=history, user_id=user):
        events.append((et, ed))
    answer = "".join(ed for et, ed in events if et == "token")
    return TurnResult(answer=answer, events=events,
                      faithfulness=getattr(loop, "_last_faithfulness_ratio", None))


def run_task(task: Task, llm_fn, *, mem=None, user: str = "bench",
             inject_hints: bool = False, record: bool = False,
             conv_id: str | None = None, preserve_workspace: bool = False,
             permission_mode: str | None = None) -> dict:
    """跑一个基准任务。V309 新增三个可选参数（默认行为与旧版完全一致）：

    conv_id：显式指定会话 id（=工作区目录名）。★ 修外部基准 0 分的元凶之一：
        terminal/swebench 驱动把任务初始文件铺进 `CONV_FILES_ROOT/<x>` 并在那里判分，
        而旧 run_task 硬编码 `bench-{task.id}` → agent 实际在 `bench-<x>`（另一个空目录）
        干活 → 判分目录永远拿不到 agent 的产出 → 恒 0。传入 conv_id 即可两边对齐。
    preserve_workspace：True 时不清空工作区（调用方已预铺任务文件/克隆好仓库时必须开，
        否则 rmtree 会把铺好的现场删光）。
    permission_mode：为本次评测的 user 授予【作用域】权限模式（如 "bypass"），跑完自动
        撤销。修外部基准 0 分的元凶之二：run_shell 是 SYSTEM 级，standard 模式下无人点
        批准 → 评测里 agent 拿不到 shell。作用域提权只影响该 user，不动全局模式。
    """
    # ★ V327 消融基线（HASHMM_BENCH_BASELINE=1）：绕过整条 AgentLoop，裸模型一次直答。
    #   放在这里（而非 adapter）是因为 GAIA/SWE-bench/Terminal 都直接调本函数——
    #   拦截在此才覆盖所有走 agent 的基准路径。同一批题：基线分 vs 正常分，差值即
    #   你的 agent 脚手架的贡献（2026 消融口径）。返回结构与正常路径完全同构。
    try:
        from hashmm.evaluation.benchmarks.adapter import baseline_mode as _blm
        _baseline = _blm()
    except Exception:  # noqa: BLE001
        _baseline = False
    if _baseline:
        _t0 = time.time()
        _q = "\n\n".join(str(t) for t in (task.turns or []))
        try:
            _fn = llm_fn
            _qc = getattr(_fn, "quick_call", None)
            _txt = str(_qc("你是严谨的助手。", _q, max_tok=4096) if callable(_qc)
                       else _fn(_q) or "").strip()
            return {"id": task.id, "category": task.category, "status": "OK", "steps": 1,
                    "answer": _txt, "tools_used": [], "failures": [],
                    "elapsed_s": round(time.time() - _t0, 1)}
        except Exception as e:  # noqa: BLE001
            return {"id": task.id, "category": task.category, "status": "ERROR", "steps": None,
                    "answer": "", "tools_used": [],
                    "failures": [f"{type(e).__name__}: {str(e)[:120]}"],
                    "elapsed_s": round(time.time() - _t0, 1)}
    from hashmm.api.database import CONV_FILES_ROOT
    conv_id = (conv_id or "").strip() or f"bench-{task.id}"
    ws = CONV_FILES_ROOT / conv_id
    if not preserve_workspace:
        shutil.rmtree(ws, ignore_errors=True)
    ctx = BenchContext(conv_id=conv_id, workspace=ws)
    _perm_granted = False
    _prev_shell_cap = None
    if permission_mode:
        try:
            from hashmm.agent.permissions import get_permissions
            _perm_granted = get_permissions().grant_session_mode(
                user, permission_mode, ttl=float(task.max_seconds) + 300.0)
        except Exception:  # noqa: BLE001  提权失败不阻断评测，只是分数会如实反映权限受限
            _perm_granted = False
        # V310：评测里放开 run_shell 超时上限（跑测试套件常需几分钟，默认 60s 会误杀）。
        _prev_shell_cap = os.environ.get("HASHMM_SHELL_TIMEOUT_CAP")
        if _prev_shell_cap is None:
            os.environ["HASHMM_SHELL_TIMEOUT_CAP"] = "600"
    t0 = time.time()
    try:
        history = list(task.history_seed)
        for q in task.turns:
            tr = asyncio.run(asyncio.wait_for(
                _run_turn(llm_fn, conv_id, q, history, mem=mem, user=user,
                          inject_hints=inject_hints,
                          max_iterations=task.max_iterations,
                          max_tool_calls=task.max_tool_calls,
                          max_exec_calls=task.max_exec_calls), timeout=task.max_seconds))
            ctx.turns.append(tr)
            history = history + [{"role": "user", "content": q},
                                 {"role": "assistant", "content": tr.answer}]
        failures = []
        for sc in task.scorers:
            # V71: 评分器自身异常 → 记该项 FAIL，不把整个任务打成 ERROR
            #（任务可能已跑了几分钟，执行成果不能因评分器 bug 报废）
            try:
                ok, why = sc(ctx)
            except Exception as _se:
                ok, why = False, f"评分器异常 {type(_se).__name__}: {str(_se)[:100]}"
            if not ok:
                failures.append(f"{getattr(sc, '__name__', 'scorer')}: {why}")
        status = "PASS" if not failures else "FAIL"
        # V103.90: --stream 模式记录 episode（用本任务最终答案 + 忠实度比例），让第二遍能召回。
        if mem is not None and record:
            try:
                _fin = ctx.final_answer
                _fr = ctx.turns[-1].faithfulness if ctx.turns else None
                mem.record(
                    user_id=user, query=task.turns[-1] if task.turns else task.id,
                    query_type=task.category, strategy=("pass" if status == "PASS" else "fail"),
                    outcome=("success" if status == "PASS" else "failed"),
                    answer=_fin, answer_length=len(_fin or ""),
                    faithfulness_ratio=_fr,
                )
            except Exception as _re:
                pass
        # V103.90 方案6：步级评测——从事件流给路由/检索/重排/合成/验证各步打分归因。
        _steps = None
        try:
            from hashmm.evaluation.step_eval import score_steps as _score_steps
            _steps = _score_steps(ctx.all_events,
                                  query=(task.turns[-1] if task.turns else ""),
                                  answer=ctx.final_answer).to_dict()
        except Exception:
            _steps = None
        # V306：把最终答案与工具调用轨迹一并返回 —— 外部基准（GAIA/WebVoyager/AgentBench）需要拿
        # agent 的**答案文本**来判分。此前只返回 status/failures，取 r["answer"] 永远是空 → 恒为 0 分。
        # 纯增量字段，不影响任何既有调用方。
        _tools_used = []
        try:
            # ★ 修 bug：AgentLoop 产生的是 ("tool_start",{...}) / ("tool_done",{...})，
            # 从不产生 ("tool",...)。旧代码找 et=="tool" → tools_used 恒为空 →
            # GAIA/WebVoyager/AgentBench 会误判「agent 没调任何工具」，甚至报「🔴致命：没联网搜索」。
            _seen = set()
            for et, ed in ctx.all_events:
                if et in ("tool_start", "tool_done") and isinstance(ed, dict) and ed.get("name"):
                    n = ed["name"]
                    if n not in _seen:
                        _seen.add(n)
                        _tools_used.append(n)
        except Exception:  # noqa: BLE001
            _tools_used = []
        return {"id": task.id, "category": task.category,
                "status": status, "steps": _steps,
                "answer": ctx.final_answer,
                "tools_used": _tools_used,
                "failures": failures, "elapsed_s": round(time.time() - t0, 1)}
    except Exception as e:
        return {"id": task.id, "category": task.category, "status": "ERROR",
                "steps": None,
                "failures": [f"{type(e).__name__}: {str(e)[:120]}"],
                "elapsed_s": round(time.time() - t0, 1)}
    finally:
        # V309：preserve_workspace 时不清场——外部基准（terminal/swebench）要在这个目录里
        # 跑官方判分脚本；旧版无条件 rmtree = 判分前把 agent 产出删光 = 恒 0 分（第三处元凶）。
        if not preserve_workspace:
            shutil.rmtree(ws, ignore_errors=True)
        if _perm_granted:
            try:
                from hashmm.agent.permissions import get_permissions
                get_permissions().revoke_session_mode(user)
            except Exception:  # noqa: BLE001
                pass
        # V310：还原 shell 超时上限（只在本次评测放开的情况下才恢复）
        if permission_mode and _prev_shell_cap is None:
            os.environ.pop("HASHMM_SHELL_TIMEOUT_CAP", None)


def run_bench(llm_fn, only: set | None = None, tasks: list | None = None, *,
              mem=None, inject_hints: bool = False, record: bool = False) -> dict:
    caps = _detect_capabilities(llm_fn)
    results = []
    for task in (tasks if tasks is not None else TASKS):
        if only and task.id not in only:
            continue
        missing = task.requires - caps
        if missing:
            results.append({"id": task.id, "category": task.category, "status": "SKIP",
                            "failures": [f"环境不满足: {sorted(missing)}"], "elapsed_s": 0})
            continue
        results.append(run_task(task, llm_fn, mem=mem, inject_hints=inject_hints, record=record))
    ran = [r for r in results if r["status"] in ("PASS", "FAIL", "ERROR")]
    passed = [r for r in ran if r["status"] == "PASS"]
    # V78: 分类通过率（评测驱动——按能力维度看强弱，不只一个总数）
    by_cat: dict = {}
    for r in ran:
        c = by_cat.setdefault(r["category"], {"ran": 0, "passed": 0})
        c["ran"] += 1
        c["passed"] += int(r["status"] == "PASS")
    for c in by_cat.values():
        c["rate"] = round(c["passed"] / c["ran"], 3) if c["ran"] else 0.0
    return {"results": results, "ran": len(ran), "passed": len(passed),
            "pass_rate": round(len(passed) / len(ran), 3) if ran else 0.0,
            "by_category": by_cat}


# ─────────────────────── --stream：自我进化"越用越强"评测 ───────────────────────

class _InMemEpisodeDB:
    """隔离的内存 sqlite —— --stream 评测用，绝不污染真机 episodes 库。"""
    def __init__(self):
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def _conn(self):
        import contextlib
        @contextlib.contextmanager
        def _cm():
            yield self.conn
            self.conn.commit()
        return _cm()


def _stream_delta(report1: dict, report2: dict) -> dict:
    """对比两遍评测，量化"带记忆后的提升"（纯函数，可单测）。

    返回总体通过率/平均忠实度差，以及各分类的通过率变化。delta>0 即"越用越强"成立。
    """
    p1 = float(report1.get("pass_rate", 0.0))
    p2 = float(report2.get("pass_rate", 0.0))
    cats = set(report1.get("by_category", {})) | set(report2.get("by_category", {}))
    cat_delta = {}
    for c in sorted(cats):
        r1 = report1.get("by_category", {}).get(c, {}).get("rate", 0.0)
        r2 = report2.get("by_category", {}).get(c, {}).get("rate", 0.0)
        cat_delta[c] = round(r2 - r1, 3)
    return {
        "pass_rate_1": round(p1, 3),
        "pass_rate_2": round(p2, 3),
        "pass_rate_delta": round(p2 - p1, 3),
        "improved": (p2 - p1) > 1e-9,
        "regressed": (p2 - p1) < -1e-9,
        "by_category_delta": cat_delta,
    }


def run_bench_stream(llm_fn, only: set | None = None, tasks: list | None = None) -> dict:
    """把金标准集当任务流顺序跑两遍——第一遍空记忆建立经验、第二遍带累积记忆——
    输出第二遍相对第一遍的提升，即"越用越强"的数字证据（对标 Evo-Memory）。

    用隔离内存记忆库（不碰真机 episodes）。注意：脚本化 LLM（自检）答案确定、不真正
    利用注入的提示，故有意义的 delta 需在**真机真实 LLM**下观察；本函数的链路与聚合
    逻辑（_stream_delta）已可在沙箱用 mock 报告验证。
    """
    from hashmm.evolution.episodic_memory import EpisodicMemory
    mem = EpisodicMemory(db_module=_InMemEpisodeDB())
    bench_user = "bench_stream"
    # Pass 1：空记忆，跑完记录经验
    r1 = run_bench(llm_fn, only=only, tasks=tasks, mem=mem, inject_hints=False, record=True)
    # Pass 2：带 Pass 1 累积的记忆（注入策略提示），再记录
    r2 = run_bench(llm_fn, only=only, tasks=tasks, mem=mem, inject_hints=True, record=True)
    delta = _stream_delta(r1, r2)
    try:
        stats = mem.stats()
    except Exception:
        stats = {}
    return {"mode": "stream", "pass1": r1, "pass2": r2, "delta": delta, "memory_stats": stats}


def _print_stream_report(report: dict):
    d = report["delta"]
    print("\n══════ 自我进化「越用越强」评测（--stream）══════")
    print(f"  第一遍（空记忆）  通过率: {d['pass_rate_1']*100:.0f}%")
    print(f"  第二遍（带记忆）  通过率: {d['pass_rate_2']*100:.0f}%")
    arrow = "↑提升" if d["improved"] else ("↓退化" if d["regressed"] else "持平")
    print(f"  Δ 通过率: {d['pass_rate_delta']*100:+.1f}%  → {arrow}")
    if d["by_category_delta"]:
        print("  分类 Δ:")
        for c, dv in d["by_category_delta"].items():
            print(f"    {c:<8} {dv*100:+.0f}%")
    print(f"  记忆库累计 episode: {report.get('memory_stats', {}).get('total', 0)}")
    print("  注：脚本化自检答案确定、delta 恒为 0；真实提升需真机真实 LLM 观察。")


# ─────────────────────── --steps：步级评测（方案6 / B4）───────────────────────

def _build_step_judge(llm_fn):
    """可选：用 llm_judge 给答案做 RAGAS 式质量打分（faithfulness/relevance）。

    真机用；llm_fn 不可用或 judge 解析失败时返回 None（绝不因没配模型而判失败）。
    """
    try:
        from hashmm.evaluation import llm_judge as _lj
    except Exception:
        return None

    def _judge(query: str, answer: str):
        try:
            prompt = _lj.build_judge_prompt(query, answer, sources=None)
            raw = llm_fn(prompt) if callable(llm_fn) else None
            if not raw:
                return None
            res = _lj.parse_judge_response(raw)
            return res.to_dict() if hasattr(res, "to_dict") else None
        except Exception:
            return None
    return _judge


def run_bench_steps(llm_fn, only: set | None = None, tasks: list | None = None,
                    judge: bool = False) -> dict:
    """跑金标准集，对每个任务做步级打分，聚合出"系统级瓶颈在哪一步"。

    与 run_bench 同一套 runner；额外把每回合事件流喂给 step_eval。judge=True 时挂 llm_judge
    做答案质量维度（真机）。返回 {results, step_aggregate}。
    """
    from hashmm.evaluation.step_eval import aggregate_step_reports
    rep = run_bench(llm_fn, only=only, tasks=tasks)
    step_dicts = [r.get("steps") for r in rep["results"]
                  if r.get("status") in ("PASS", "FAIL", "ERROR") and r.get("steps")]
    agg = aggregate_step_reports(step_dicts)
    return {"mode": "steps", "results": rep["results"], "pass_rate": rep["pass_rate"],
            "step_aggregate": agg}


def _print_step_report(report: dict):
    agg = report.get("step_aggregate", {})
    print("\n══════ 步级评测（--steps · 路由/检索/重排/合成/验证）══════")
    print(f"  评测任务数: {agg.get('n', 0)}    总体通过率: {report.get('pass_rate', 0)*100:.0f}%")
    print("  各步平均分（None=本批无适用样本）:")
    names = {"routing": "路由", "retrieval": "检索", "rerank": "重排",
             "synthesis": "合成", "verification": "验证"}
    for k, label in names.items():
        v = agg.get("stage_avg", {}).get(k)
        bar = "" if v is None else ("█" * int(round(v * 10))).ljust(10, "·")
        print(f"    {label:<4} {('—' if v is None else f'{v:.2f}'):>5}  {bar}")
    tb = agg.get("top_bottleneck")
    if tb:
        print(f"  ⚠️ 系统级瓶颈步：{names.get(tb, tb)}（{agg.get('bottleneck_counts', {}).get(tb, 0)} 个任务卡在此步）")
    else:
        print("  ✓ 未发现集中的瓶颈步")
    # 逐任务瓶颈一览
    print("  逐任务瓶颈：")
    for r in report["results"]:
        if r.get("status") not in ("PASS", "FAIL", "ERROR"):
            continue
        st = r.get("steps") or {}
        bn = st.get("bottleneck")
        ov = st.get("overall")
        print(f"    {r['id']:<16} overall={('—' if ov is None else f'{ov:.2f}')}"
              f"  瓶颈={names.get(bn, bn) if bn else '无'}")


def _print_report(report: dict):
    w = max((len(r["id"]) for r in report["results"]), default=10) + 2
    print(f"\n{'任务':<{w}} {'类别':<10} {'结果':<6} {'耗时':<7} 失败项")
    print("─" * 76)
    for r in report["results"]:
        fails = "; ".join(r["failures"])[:60]
        print(f"{r['id']:<{w}} {r['category']:<10} {r['status']:<6} {r['elapsed_s']:<7} {fails}")
    print("─" * 76)
    if report.get("by_category") and len(report["by_category"]) > 1:
        print("分类通过率:")
        for cat, c in sorted(report["by_category"].items(), key=lambda x: x[1]["rate"]):
            print(f"  {cat:<8} {c['passed']}/{c['ran']}  ({c['rate']*100:.0f}%)")
    print(f"通过率: {report['passed']}/{report['ran']}"
          f" ({report['pass_rate']*100:.0f}%)" if report["ran"] else "（无可运行任务）")


# ─────────────────────────── 自检（沙箱可跑） ───────────────────────────

class ScriptedLLM:
    """脚本化 LLM：按预设序列发工具调用/回答，用于在无真实 LLM 的环境验证 harness。"""

    def __init__(self, script: list):
        self.script = list(script)  # 每项: ("tool", name, args_json) 或 ("say", text)

    def call_with_tools(self, messages, tools=None):
        class _Fn:
            def __init__(s, n, a): s.name, s.arguments = n, a

        class _TC:
            def __init__(s, n, a, i): s.id, s.type, s.function = i, "function", _Fn(n, a)

        class _Msg:
            def __init__(s, c="", tc=None): s.content, s.tool_calls, s.reasoning_content = c, tc, ""

        class _Resp:
            def __init__(s, m): s.message = m

        if not self.script:
            return _Resp(_Msg(c="完成。"))
        kind, *rest = self.script.pop(0)
        if kind == "tool":
            name, args = rest
            return _Resp(_Msg(tc=[_TC(name, args, f"s{len(self.script)}")]))
        return _Resp(_Msg(c=rest[0]))


def selftest_tasks() -> list[Task]:
    return [
        Task("st_create", "自检", turns=["创建文件"], requires=set(),
             scorers=[file_exists("a.py"), file_contains("a.py", r"def f"), py_compiles("a.py")]),
        Task("st_edit", "自检", turns=["创建", "编辑"], requires=set(),
             scorers=[tool_used("str_replace"), file_contains("b.py", "plus"),
                      file_contains("b.py", r"\badd\b", negate=True)]),
        Task("st_todo", "自检", turns=["计划"], requires=set(),
             scorers=[todo_emitted(2), answer_contains("完成")]),
        Task("st_deep_skip", "自检", turns=["简单问题"], requires=set(),
             scorers=[tool_not_used("deep_search"), answer_contains("好的")]),
    ]


def selftest_scripts() -> dict:
    return {
        "st_create": [("tool", "create_file", '{"filename": "a.py", "content": "def f():\\n    return 1\\n"}'),
                      ("say", "已创建。")],
        "st_edit": [("tool", "create_file", '{"filename": "b.py", "content": "def add(a, b):\\n    return a + b\\n"}'),
                    ("say", "已创建。"),
                    ("tool", "str_replace", '{"filepath": "b.py", "old_str": "add", "new_str": "plus"}'),
                    ("say", "已编辑。")],
        "st_todo": [("tool", "update_todo",
                     '{"items": [{"text": "一", "status": "doing"}, {"text": "二", "status": "pending"}]}'),
                    ("say", "计划已列，任务完成。")],
        "st_deep_skip": [("tool", "calculator", '{"expression": "2+2"}'),
                         ("say", "好的，答案是 4。")],
    }


def _selftest_stream_delta() -> dict:
    """用 mock 报告验证 --stream 的聚合逻辑（纯函数，沙箱可跑）。"""
    fails = []
    # 提升场景
    r1 = {"pass_rate": 0.5, "by_category": {"检索": {"rate": 0.4}, "代码": {"rate": 0.6}}}
    r2 = {"pass_rate": 0.8, "by_category": {"检索": {"rate": 0.8}, "代码": {"rate": 0.8}}}
    d = _stream_delta(r1, r2)
    if not d["improved"]: fails.append("improved 应为 True")
    if d["regressed"]: fails.append("regressed 应为 False")
    if abs(d["pass_rate_delta"] - 0.3) > 1e-6: fails.append(f"pass_rate_delta 期望0.3 得{d['pass_rate_delta']}")
    if abs(d["by_category_delta"]["检索"] - 0.4) > 1e-6: fails.append("检索分类 delta 应为0.4")
    # 退化场景
    d2 = _stream_delta({"pass_rate": 0.9, "by_category": {}}, {"pass_rate": 0.7, "by_category": {}})
    if not d2["regressed"]: fails.append("退化场景 regressed 应为 True")
    if d2["improved"]: fails.append("退化场景 improved 应为 False")
    # 持平场景
    d3 = _stream_delta({"pass_rate": 0.6, "by_category": {}}, {"pass_rate": 0.6, "by_category": {}})
    if d3["improved"] or d3["regressed"]: fails.append("持平场景应既不improved也不regressed")
    return {"id": "st_stream_delta", "category": "自检",
            "status": "PASS" if not fails else "FAIL",
            "failures": fails, "elapsed_s": 0}


def _selftest_step_eval() -> dict:
    """用 mock 事件流验证步级打分器的核心判定（纯函数，沙箱可跑）。"""
    from hashmm.evaluation import step_eval as _se
    fails = []
    good = [("tool_done", {"name": "kb_search", "status": "ok", "result": "[1] 命中内容"}),
            ("token", "根据资料，营收2700亿元，同比增长20%[1]。"),
            ("trace", {"node": "citation", "detail": "引用校验：全部引用编号有效 ✓"})]
    rg = _se.score_steps(good, answer="根据资料，营收2700亿元，同比增长20%[1]。")
    if rg.overall < 0.95: fails.append(f"健康回合 overall 应高，得 {rg.overall}")
    if rg.bottleneck is not None: fails.append(f"健康回合不应有瓶颈，得 {rg.bottleneck}")
    bad = [("tool_done", {"name": "kb_search", "status": "ok", "result": "未找到"}),
           ("tool_done", {"name": "kb_search", "status": "ok", "result": "无相关"}),
           ("token", "抱歉，我在处理这个任务时没能获取到足够的信息。")]
    rb = _se.score_steps(bad, answer="抱歉，我在处理这个任务时没能获取到足够的信息。")
    if rb.bottleneck != "retrieval": fails.append(f"坏回合瓶颈应=retrieval，得 {rb.bottleneck}")
    if rb.overall >= 0.6: fails.append(f"坏回合 overall 应低，得 {rb.overall}")
    # 脏事件不抛 + 聚合
    if not isinstance(_se.score_steps([("x",), None], answer="a"), _se.StepReport):
        fails.append("脏事件应安全返回 StepReport")
    agg = _se.aggregate_step_reports([rg.to_dict(), rb.to_dict(), rb.to_dict()])
    if agg["top_bottleneck"] != "retrieval": fails.append("聚合 top_bottleneck 应=retrieval")
    return {"id": "st_step_eval", "category": "自检",
            "status": "PASS" if not fails else "FAIL",
            "failures": fails, "elapsed_s": 0}


def run_selftest() -> dict:
    """沙箱自检：每个自检任务配独立脚本化 LLM，端到端走 runner+scorer。"""
    scripts = selftest_scripts()
    results = []
    for task in selftest_tasks():
        # st_edit 是两轮任务：脚本按轮消费——把整段脚本交给同一个 LLM 实例即可
        llm = ScriptedLLM(scripts[task.id])
        results.append(run_task(task, llm))
    # V103.90: 把 --stream 聚合逻辑 + 步级打分器纳入自检回归保护（纯函数，不需真实 LLM）
    results.append(_selftest_stream_delta())
    results.append(_selftest_step_eval())
    ran = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    return {"results": results, "ran": ran, "passed": passed,
            "pass_rate": round(passed / ran, 3) if ran else 0.0}


# ─────────────────────────── CLI ───────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HashMM mini Agent-Bench")
    ap.add_argument("--only", default="", help="只跑指定任务（逗号分隔 id）")
    ap.add_argument("--task", dest="only", help="--only 的别名（兼容习惯用法）")
    ap.add_argument("--gate", type=float, default=0.0,
                    help="质量门：通过率低于该值(0-1)时退出码非 0（CI 用）")
    ap.add_argument("--selftest", action="store_true", help="脚本化 LLM 自检（无需真实 LLM）")
    ap.add_argument("--stream", action="store_true",
                    help="自我进化「越用越强」评测：金标准跑两遍（空记忆→带记忆）看提升")
    ap.add_argument("--steps", action="store_true",
                    help="步级评测：对路由/检索/重排/合成/验证每步打分归因，定位质量瓶颈在哪步")
    ap.add_argument("--judge", action="store_true",
                    help="--steps 时附 RAGAS 式答案质量打分（用 llm_judge，需真机 LLM）")
    ap.add_argument("--json", default="", help="JSON 报告输出路径（默认 bench_results/<时间戳>.json）")
    args = ap.parse_args(argv)

    if args.selftest:
        report = run_selftest()
    elif args.steps:
        try:
            from hashmm.api.model_manager import get_active_llm_fn
            llm_fn, cfg = get_active_llm_fn()
        except Exception as e:
            print(f"无法获取 LLM（{e}）。--steps 需真机真实 LLM（步级评测要真实跑一遍金标准）。")
            return 2
        if llm_fn is None:
            print("LLM 未配置。--steps 需真机真实 LLM。")
            return 2
        only = {x.strip() for x in args.only.split(",") if x.strip()} or None
        streport = run_bench_steps(llm_fn, only=only, judge=args.judge)
        _print_step_report(streport)
        out = Path(args.json) if args.json else Path("bench_results") / f"steps-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(streport, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告: {out}")
        # 通过率低于 gate 才失败（步级报告本身是诊断，不强制门）
        return 1 if (args.gate and streport.get("pass_rate", 0) < args.gate) else 0
    elif args.stream:
        try:
            from hashmm.api.model_manager import get_active_llm_fn
            llm_fn, cfg = get_active_llm_fn()
        except Exception as e:
            print(f"无法获取 LLM（{e}）。--stream 需真机真实 LLM（脚本化答案无法体现记忆增益）。")
            return 2
        if llm_fn is None:
            print("LLM 未配置。--stream 需真机真实 LLM。")
            return 2
        only = {x.strip() for x in args.only.split(",") if x.strip()} or None
        sreport = run_bench_stream(llm_fn, only=only)
        _print_stream_report(sreport)
        out = Path(args.json) if args.json else Path("bench_results") / f"stream-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(sreport, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告: {out}")
        # 退化（带记忆反而更差）才算失败，便于 CI 守护自我进化不退步
        return 1 if sreport["delta"]["regressed"] else 0
    else:
        try:
            from hashmm.api.model_manager import get_active_llm_fn
            llm_fn, cfg = get_active_llm_fn()
        except Exception as e:
            print(f"无法获取 LLM（{e}）。沙箱环境请用 --selftest。")
            return 2
        if llm_fn is None:
            print("LLM 未配置。请先在管理后台配置模型，或用 --selftest。")
            return 2
        only = {x.strip() for x in args.only.split(",") if x.strip()} or None
        report = run_bench(llm_fn, only=only)

    _print_report(report)
    out = Path(args.json) if args.json else Path("bench_results") / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")
    if not report["ran"]:
        return 0
    if args.gate > 0:
        # V72 质量门：通过率达到阈值即放行（CI 渐进收紧用）
        rate = report["passed"] / report["ran"]
        return 0 if rate >= args.gate else 1
    return 0 if report["passed"] == report["ran"] else 1


if __name__ == "__main__":
    sys.exit(main())
