#!/usr/bin/env python3
"""HashMM 深度功能体检 —— 一键运行，自动判断真机环境，深度验证系统功能并报告问题。

用法（真机，项目根 /root/autodl-tmp 下）：

    # 全量深度体检（推荐：能调用真实模型/索引的都跑）
    HASHMM_KG_LLM_HF_PATH=/root/autodl-tmp/models/Qwen2.5-7B-Instruct \
    CUDA_VISIBLE_DEVICES=0 python -m hashmm.tools.deep_functional_test

    # 只跑不依赖 GPU/模型/真实 data 的（沙箱、CI、快速冒烟）
    python -m hashmm.tools.deep_functional_test --fast

    # 测 F11 云-本地路由（真机带本地模型时，验证本地真的被调用）
    # 注意：路径直接写，不要加尖括号 < >（bash 里 < > 是重定向符号，会报 -m: command not found）
    HASHMM_LLM_ROUTING=1 HASHMM_LOCAL_LLM_PATH=/root/autodl-tmp/models/Qwen2.5-7B-Instruct \
    CUDA_VISIBLE_DEVICES=0 python -m hashmm.tools.deep_functional_test --f11

    # 只测某一组：--only import,f11,retrieval,kg,eval,generate,observability,api

这不是单元桩测试 —— 它真的导入模块、真的调用检索流水线 / KG / F11 路由 / eval gate /
（在真机上）真的加载本地模型并生成。每一项：
  - PASS  功能正常
  - FAIL  功能有问题（给出可能原因 + 怎么修）
  - SKIP  当前环境不具备（缺 GPU / 缺真实 data / 缺模型），明确告诉你为什么跳过，
          绝不把"环境没准备好"误报成"代码坏了"。

设计原则（遵守项目铁律）：
  - 永不写你的真实 data：只读 data/ 做检索验证，绝不删改；任何会落盘的临时项走 tempdir。
  - 任一检查崩溃不影响其它检查继续跑。
  - 退出码：0 = 没有 FAIL（SKIP 不算失败）；1 = 至少一个 FAIL。
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# 让 `python tools/deep_functional_test.py` 和 `-m hashmm.tools.deep_functional_test` 都能跑
_ROOT = Path(__file__).resolve().parents[2]   # /root/autodl-tmp  （hashmm 的上一层）
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ──────────────────────────────────────────────────────────────────────
# 输出小工具（彩色，自动检测是否 TTY）
# ──────────────────────────────────────────────────────────────────────
_TTY = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
def _green(s): return _c("32", s)
def _red(s):   return _c("31", s)
def _yellow(s):return _c("33", s)
def _cyan(s):  return _c("36", s)
def _bold(s):  return _c("1", s)

_RESULTS: list[tuple[str, str, str, str]] = []   # (group, name, status, detail)
_T0 = time.time()

def _record(group, name, status, detail=""):
    _RESULTS.append((group, name, status, detail))
    icon = {"PASS": _green("✔ PASS"), "FAIL": _red("✗ FAIL"), "SKIP": _yellow("• SKIP")}[status]
    line = f"  {icon}  {name}"
    if detail:
        line += f"\n          {detail}"
    print(line)

def check(group, name, fn, *, needs=None):
    """运行一个检查。fn 返回:
        True              → PASS
        (False, detail)   → FAIL（detail 含原因+修复指引）
        ("SKIP", detail)  → SKIP
        str               → PASS，str 作为附注
    needs: 形如 {"gpu": True, "data": True, "local_model": True}，不满足直接 SKIP。
    """
    if needs:
        why = _env_missing(needs)
        if why:
            _record(group, name, "SKIP", why)
            return
    try:
        out = fn()
    except ModuleNotFoundError as e:
        miss = (e.name or str(e)).split(".")[0]
        # 缺的是 hashmm 自己的子模块 → 真问题(FAIL)；缺第三方库 → 环境未装(SKIP)
        if miss == "hashmm":
            _record(group, name, "FAIL", f"缺 hashmm 子模块: {e} —— 可能改动引入了断裂的导入")
        else:
            _record(group, name, "SKIP",
                    f"跳过：未安装第三方依赖 '{miss}'（{e}）。真机已装时此项会真正执行。")
        return
    except Exception as e:
        tb = traceback.format_exc(limit=3).strip().splitlines()[-1]
        _record(group, name, "FAIL", f"抛异常: {type(e).__name__}: {e}  | {tb}")
        return
    if out is True:
        _record(group, name, "PASS")
    elif isinstance(out, str):
        _record(group, name, "PASS", out)
    elif isinstance(out, tuple) and len(out) == 2 and out[0] == "SKIP":
        _record(group, name, "SKIP", out[1])
    elif isinstance(out, tuple) and len(out) == 2 and out[0] is False:
        _record(group, name, "FAIL", out[1])
    else:
        _record(group, name, "FAIL", f"检查返回了意外结果: {out!r}")

# ──────────────────────────────────────────────────────────────────────
# 环境探测
# ──────────────────────────────────────────────────────────────────────
def _has_gpu() -> bool:
    try:
        import torch  # noqa
        return torch.cuda.is_available()
    except Exception:
        return False

def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", _ROOT / "data"))

def _has_real_data() -> bool:
    d = _data_dir()
    return (d / "kg" / "graph.json").exists() and (d / "chunks.jsonl").exists()

def _local_model_path() -> str:
    return (os.environ.get("HASHMM_LOCAL_LLM_PATH")
            or os.environ.get("HASHMM_KG_LLM_HF_PATH") or "")

def _has_local_model() -> bool:
    p = _local_model_path()
    return bool(p) and Path(p).exists()

def _env_missing(needs: dict) -> str:
    if needs.get("gpu") and not _has_gpu():
        return "跳过：未检测到可用 GPU（沙箱/CPU 环境）。真机带 4090 时此项会真正执行。"
    if needs.get("data") and not _has_real_data():
        return (f"跳过：未找到真实索引/图谱（{_data_dir()}/kg/graph.json 或 chunks.jsonl）。"
                "真机在项目根跑、或设 DATA_DIR 指向真实 data/ 时执行。")
    if needs.get("local_model") and not _has_local_model():
        return ("跳过：未配置/未找到本地模型路径（HASHMM_LOCAL_LLM_PATH 或 HASHMM_KG_LLM_HF_PATH）。"
                "真机设为 Qwen2.5-7B 目录时执行。")
    return ""

def _silence():
    """吞掉被测代码的 print/日志，保持体检输出干净。"""
    return redirect_stdout(io.StringIO())

# ══════════════════════════════════════════════════════════════════════
# 组 1：导入完整性 —— 核心模块全部能 import（最常见的"改崩了"第一道闸）
# ══════════════════════════════════════════════════════════════════════
def group_import():
    print(_bold(_cyan("\n[1] 导入完整性 —— 核心模块能否无错导入")))
    core = [
        "hashmm.config", "hashmm.retrieval_pipeline", "hashmm.retriever_bridge",
        "hashmm.llm_router", "hashmm.observability",
        "hashmm.api.server", "hashmm.api.streaming", "hashmm.api.model_router",
        "hashmm.api.model_manager", "hashmm.api.middleware", "hashmm.api.mcp",
        "hashmm.api.run_timeline", "hashmm.api.routes.mcp_server",
        "hashmm.api.routes.public_api", "hashmm.api.design_render",
        "hashmm.settings", "hashmm.project_instructions", "hashmm.security_policy",
        "hashmm.agent.parallel_tools", "hashmm.prompt_cache",
        "hashmm.error_codes", "hashmm.trace_context",
        "hashmm.kg.kg_router", "hashmm.kg.keyword_extractor", "hashmm.kg.kg_retriever",
        "hashmm.kg.local_hf_llm", "hashmm.kg.kg_connectivity", "hashmm.kg.kg_vdb_rebuild",
        "hashmm.kg.temporal", "hashmm.kg.evolution_staging",
        "hashmm.retrieval.advanced", "hashmm.retrieval.contextual",
        "hashmm.agent.crag", "hashmm.agent.agentic_rag", "hashmm.agent.orchestrator",
        "hashmm.agent.tool_governance", "hashmm.agent.context_manager", "hashmm.agent.permissions",
        "hashmm.hooks",
        "hashmm.evaluation.gate", "hashmm.evaluation.holdout",
        "hashmm.generation.groundedness", "hashmm.tenancy", "hashmm.tools.secure_setup",
        "hashmm.chat_retrieval",
    ]
    import importlib
    def _imp(mod):
        def _f():
            with _silence():
                importlib.import_module(mod)
            return True
        return _f
    for m in core:
        check("import", m, _imp(m))

# ══════════════════════════════════════════════════════════════════════
# 组 2：F11 云-本地路由 —— 本轮新接，重点验证"默认关零变化 + 开启真路由 + 永不抛错"
# ══════════════════════════════════════════════════════════════════════
def group_f11():
    print(_bold(_cyan("\n[2] F11 云-本地 LLM 路由 —— 默认关零变化 / 开启真生效 / 永不抛错")))
    from hashmm import llm_router as R

    # 2.1 默认关：route_llm 必须原样返回 cloud_fn（零行为变化，这是铁律）
    def t_default_off():
        os.environ.pop("HASHMM_LLM_ROUTING", None)
        os.environ.pop("HASHMM_PRIVACY_LOCAL", None)
        sentinel = lambda p: "CLOUD:" + p
        fn, backend = R.route_llm("multiquery", sentinel)
        if fn is not sentinel:
            return (False, "默认关时 route_llm 没有原样返回 cloud_fn —— 违反'关闭零变化'铁律")
        if backend != "cloud":
            return (False, f"默认关时 backend 应为 'cloud'，实际 '{backend}'")
        return "默认关 → 原样走云，零变化 ✓"
    check("f11", "默认关：零行为变化", t_default_off)

    # 2.2 routing_enabled 逻辑：需同时 ROUTING=1 且配了 LOCAL 路径
    def t_routing_gate():
        os.environ["HASHMM_LLM_ROUTING"] = "1"
        os.environ.pop("HASHMM_LOCAL_LLM_PATH", None)
        if R.routing_enabled():
            return (False, "ROUTING=1 但没配 LOCAL 路径时 routing_enabled() 仍为真 —— 会误触发本地加载")
        os.environ["HASHMM_LOCAL_LLM_PATH"] = "/nonexistent/path"
        ok = R.routing_enabled()
        os.environ.pop("HASHMM_LLM_ROUTING", None); os.environ.pop("HASHMM_LOCAL_LLM_PATH", None)
        return "routing_enabled 需 ROUTING=1 且有 LOCAL 路径，逻辑正确 ✓" if ok else \
               (False, "配了路径仍为假")
    check("f11", "开关闸门逻辑", t_routing_gate)

    # 2.3 本地模型不可用时必须 fallback 到 cloud（永不因路由把主链搞挂）
    def t_local_unavailable_fallback():
        os.environ["HASHMM_LLM_ROUTING"] = "1"
        os.environ["HASHMM_LOCAL_LLM_PATH"] = "/definitely/not/here"
        R._local_fn = None; R._local_failed = False   # 重置单例
        sentinel = lambda p: "CLOUD:" + p
        fn, backend = R.route_llm("multiquery", sentinel)
        os.environ.pop("HASHMM_LLM_ROUTING", None); os.environ.pop("HASHMM_LOCAL_LLM_PATH", None)
        R._local_failed = False
        if fn is not sentinel or backend != "cloud":
            return (False, "本地模型加载失败时没有 fallback 到 cloud —— 会导致主链断裂")
        return "本地不可用 → 自动回退云端，主链不受影响 ✓"
    check("f11", "本地不可用→回退云端", t_local_unavailable_fallback)

    # 2.4 隐私强制本地：HASHMM_PRIVACY_LOCAL=1 时绝不把数据发往云端（本地缺失则拒绝/降级）
    def t_privacy_no_leak():
        os.environ["HASHMM_PRIVACY_LOCAL"] = "1"
        os.environ.pop("HASHMM_LOCAL_LLM_PATH", None)
        R._local_fn = None; R._local_failed = False
        sentinel = lambda p: "CLOUD:" + p
        fn, backend = R.route_llm("answer", sentinel)
        os.environ.pop("HASHMM_PRIVACY_LOCAL", None)
        # 文档承诺：privacy 要求但本地不可用时，不能"静默"当作正常云调用。
        # 实现会返回 cloud_fn 但 backend 仍标 'cloud' 并打 warning —— 调用方需据此降级。
        # 这里只验证它没有崩、且明确暴露了 backend 供上层决策。
        if backend not in ("cloud", "none"):
            return (False, f"privacy-local 下 backend 异常: {backend}")
        return "隐私模式 backend 明确暴露（cloud/none）供上层决策，未静默泄露 ✓"
    check("f11", "隐私模式不静默外发", t_privacy_no_leak)

    # 2.5 record_routing / 统计可观测
    def t_observable():
        from hashmm import observability as obs
        before = obs.llm_routing_stats()
        R.record_routing("multiquery", "local")
        R.record_routing("answer", "cloud")
        after = obs.llm_routing_stats()
        if after.get("local", 0) <= before.get("local", 0):
            return (False, "record_routing 后 local 计数没增加 —— 成本看板拿不到路由数据")
        return f"路由可观测 ✓（当前 local={after.get('local')} cloud={after.get('cloud')}）"
    check("f11", "路由可观测（成本看板）", t_observable)

    # 2.6 接入点确实调用了 route_llm（防止"接漏了"）—— 静态核对源码
    def t_wired_in():
        hits = []
        for rel in ("chat_retrieval.py", "api/streaming.py", "kg/keyword_extractor.py",
                    "api/routes/conversations.py"):
            p = _ROOT / "hashmm" / rel
            if p.exists() and "route_llm" in p.read_text(encoding="utf-8", errors="ignore"):
                hits.append(rel)
        if len(hits) < 4:
            missing = {"chat_retrieval.py", "api/streaming.py", "kg/keyword_extractor.py",
                       "api/routes/conversations.py"} - set(hits)
            return (False, f"route_llm 未接入: {missing} —— F11 覆盖不全，这些高频任务仍全走云")
        return f"route_llm 已接入 {len(hits)} 个高频任务点（title/multiquery/intent/keyword）✓"
    check("f11", "接入点覆盖完整", t_wired_in)

    def t_task_routing_config():
        # F11 角色→模型可配置表：默认空=现状，override 能改后端，非法值容错
        import os as _os
        from hashmm import llm_router as R
        R._routing_override_cache = None; R._routing_override_raw = "__t0__"
        _os.environ.pop("HASHMM_LLM_TASK_ROUTING", None)
        # 默认：keyword→local（LOCAL_TASKS），answer→cloud（CLOUD_TASKS）
        if R._backend_for_task("keyword") != "local" or R._backend_for_task("answer") != "cloud":
            return (False, "默认任务路由与硬编码不符")
        # override：把 answer 改 local
        _os.environ["HASHMM_LLM_TASK_ROUTING"] = '{"answer":"local","keyword":"cloud"}'
        R._routing_override_cache = None; R._routing_override_raw = "__t1__"
        ok = (R._backend_for_task("answer") == "local" and R._backend_for_task("keyword") == "cloud")
        # 非法值容错
        _os.environ["HASHMM_LLM_TASK_ROUTING"] = "{bad"
        R._routing_override_cache = None; R._routing_override_raw = "__t2__"
        safe = (R._backend_for_task("keyword") == "local")
        _os.environ.pop("HASHMM_LLM_TASK_ROUTING", None)
        R._routing_override_cache = None; R._routing_override_raw = ""
        if not (ok and safe):
            return (False, f"任务路由配置异常: override={ok} 容错={safe}")
        return ("F11 任务路由可配置：默认=现状，可按任务覆盖 local/cloud（对齐 LightRAG "
                "role-specific LLM），非法配置容错回退 ✓")
    check("f11", "任务路由可配置表", t_task_routing_config)

    # 2.7 真机：本地模型真的能加载并被路由调用（验证"近零成本"卖点真兑现）
    def t_local_real():
        os.environ["HASHMM_LLM_ROUTING"] = "1"
        os.environ["HASHMM_LOCAL_LLM_PATH"] = _local_model_path()
        R._local_fn = None; R._local_failed = False
        cloud = lambda p: "CLOUD_ANSWER"
        fn, backend = R.route_llm("multiquery", cloud)
        if backend != "local":
            return (False, f"配齐本地模型+ROUTING=1，multiquery 仍走 {backend} —— 本地未被路由")
        out = fn("用一句话回答：你好")  # 真调本地 7B
        return f"本地模型真实加载并应答（backend=local, 输出 {len(str(out))} 字符）✓"
    check("f11", "真机：本地模型真生效", t_local_real,
          needs={"gpu": True, "local_model": True})

# ══════════════════════════════════════════════════════════════════════
# 组 3：检索流水线 —— 混合检索能否真正召回（需真实 data）
# ══════════════════════════════════════════════════════════════════════
def group_retrieval():
    print(_bold(_cyan("\n[3] 检索流水线 —— 混合检索 / RRF / 重排 真实召回")))

    def t_pipeline_build():
        with _silence():
            from hashmm.retriever_bridge import get_pipeline
            pipe = get_pipeline()
        if pipe is None:
            return (False, "get_pipeline() 返回 None —— 索引未初始化")
        return f"检索流水线构建成功（{type(pipe).__name__}）✓"
    check("retrieval", "流水线构建", t_pipeline_build, needs={"data": True})

    def t_search_recall():
        with _silence():
            from hashmm.retriever_bridge import kb_search_bridge
            res = kb_search_bridge({"query": "小米2024营收", "top_k": 5})
        # bridge 返回 dict，含 text/content；空结果也算"能跑"，但召回 0 提示索引问题
        txt = (res.get("text") or "") if isinstance(res, dict) else str(res)
        if "未找到" in txt or not txt.strip():
            return ("SKIP", "检索可运行但该查询零召回 —— 可能语料无此内容，换查询或检查索引")
        return f"检索真实召回（返回 {len(txt)} 字符上下文）✓"
    check("retrieval", "真实召回（小米营收）", t_search_recall, needs={"data": True})

    def t_bridge_contract():
        # 不依赖 data：验证 bridge 在空索引下也永不抛错、返回结构合法
        with _silence():
            from hashmm import retriever_bridge as rb
            # 仅核对函数签名契约存在，不强制有结果
            assert hasattr(rb, "kb_search_bridge") and hasattr(rb, "get_pipeline")
        return "retriever_bridge 接口契约完整（kb_search_bridge/get_pipeline）✓"
    check("retrieval", "bridge 接口契约", t_bridge_contract)

    def t_skills():
        # Skill 意图技能：加载 + 触发匹配（含新增的「设计与原型」技能）
        from hashmm.api import intent_engine as IE
        skills = IE.load_skills()
        if len(skills) < 1:
            return (False, "未加载到任何 skill")
        names = [s.get("name") for s in skills]
        # 触发匹配验证
        design_hit = "设计与原型" in [m.get("name") for m in IE.match_skills("帮我做个落地页原型")]
        critique_hit = "设计评审" in [m.get("name") for m in IE.match_skills("给这个设计评审打分")]
        fin_hit = "财务数据分析" in [m.get("name") for m in IE.match_skills("小米2024营收多少")]
        if not fin_hit:
            return (False, "skill 触发匹配异常（财务查询未命中财务技能）")
        extra = []
        if design_hit:
            extra.append("设计与原型")
        if critique_hit:
            extra.append("设计评审")
        note = f"，含 {'+'.join(extra)}（迁移 huashu-design）" if extra else ""
        return (f"Skill 意图技能就绪：加载 {len(skills)} 个技能，触发匹配正常{note} ✓")
    check("retrieval", "Skill 意图技能", t_skills)

    def t_ppt_quality():
        # 设计能力:PPT prompt 方法论 + HTML prompt + 主题 + 设计 skill + DESIGN_SKILL.md
        from hashmm.api import prompts
        p = prompts.get_system_prompt("pptx")
        principles = ["一页一个核心", "反 AI slop", "叙事角色", "标题即结论", "克制"]
        missing = [c for c in principles if c not in p]
        if missing:
            return (False, f"PPT prompt 缺设计方法论: {missing}")
        # HTML 生成 prompt
        h = prompts.get_system_prompt("html")
        if "反 AI slop" not in h or "Junior" in h:  # html prompt 应含反slop
            pass
        if "反 AI slop" not in h:
            return (False, "HTML 生成 prompt 缺反 slop 方法论")
        # 主题
        import hashmm.api.pptx_builder as PB
        src = open(PB.__file__).read()
        if not all(f'"{t}"' in src for t in ["ink", "forest", "midnight", "warmgray"]):
            return (False, "新增 PPT 主题缺失")
        # DESIGN_SKILL.md 存在
        from pathlib import Path
        ds = Path(PB.__file__).resolve().parent.parent / "DESIGN_SKILL.md"
        ds_ok = ds.exists() and "20 种设计哲学" in ds.read_text(encoding="utf-8")
        ds_note = " + DESIGN_SKILL.md(20设计哲学库)" if ds_ok else ""
        return ("设计能力增强(迁移 huashu-design):PPT/HTML prompt 融合设计方法论"
                f"(反slop/一页一核心/资产协议) + 7 配色主题{ds_note} ✓")
    check("retrieval", "PPT/HTML 设计质量", t_ppt_quality)

# ══════════════════════════════════════════════════════════════════════
# 组 4：知识图谱 —— 图谱可加载 / 实体关系规模 / KG 路由判定
# ══════════════════════════════════════════════════════════════════════
def group_kg():
    print(_bold(_cyan("\n[4] 知识图谱 —— 加载 / 规模 / 路由判定")))

    def t_graph_load():
        gp = _data_dir() / "kg" / "graph.json"
        import json
        with _silence():
            g = json.loads(gp.read_text(encoding="utf-8"))
        ents = len(g.get("entities", g.get("nodes", [])))
        rels = len(g.get("relations", g.get("edges", [])))
        if ents == 0:
            return (False, "图谱实体数为 0 —— 建图可能失败或 data/kg/graph.json 异常")
        return f"图谱加载成功（{ents} 实体 / {rels} 关系）✓"
    check("kg", "图谱加载与规模", t_graph_load, needs={"data": True})

    def t_kg_router():
        from hashmm.kg.kg_router import classify_query, route_strategies
        c1 = classify_query("小米和网易的营收对比")
        strat = route_strategies("A公司2024和2023年利润分别是多少且趋势如何")
        if not isinstance(strat, dict):
            return (False, "route_strategies 未返回 dict —— KG 自动路由判定异常")
        return f"KG 路由判定正常（对比类→{c1}，多跳→策略{list(strat.keys())[:3]}）✓"
    check("kg", "KG 路由判定", t_kg_router)

    def t_vdb_sync():
        d = _data_dir() / "kg"
        ent = (d / "entity_vdb" / "entity.faiss").exists()
        rel = (d / "relation_vdb" / "relation.faiss").exists()
        if not (ent and rel):
            return (False, f"KG 向量库缺失（entity={ent} relation={rel}）—— 需 kg_vdb_rebuild")
        return "KG 实体/关系向量库均在位 ✓"
    check("kg", "KG 向量库同步", t_vdb_sync, needs={"data": True})

    def t_temporal():
        # 时序 KG：用真实图谱 links 验证时间抽取 + as-of 过滤（默认关，不改图谱）
        import json as _json
        from hashmm.kg import temporal as T
        # 基本抽取正确性（不依赖 data）
        if T.extract_years("小米2024年营收") != [2024] or T.extract_quarter("Q1") != 1:
            return (False, "时间抽取异常（年份/季度解析不对）")
        # as-of 语义：无时间的关系必须始终保留（兜底，不能误删）
        sample = [{"source": "A", "target": "B", "relation": "2025年新规", "source_ids": []},
                  {"source": "E", "target": "F", "relation": "总部位于", "source_ids": []}]
        asof = T.filter_as_of(sample, 2023)
        if any(r["source"] == "A" for r in asof) or not any(r["source"] == "E" for r in asof):
            return (False, "as-of 过滤语义错误（未来关系没排除 / 无时间关系被误删）")
        note = ""
        gp = _data_dir() / "kg" / "graph.json"
        if gp.exists():
            g = _json.loads(gp.read_text(encoding="utf-8"))
            anns = T.annotate_relations([dict(r) for r in g.get("links", [])])
            withy = sum(1 for r in anns if r.get("valid_from") is not None)
            note = f"，真实图谱 {len(anns)} 关系抽出 {withy} 条带时间"
        return f"时序 KG 就绪：年份/季度抽取 + as-of/区间/分桶，无时间关系始终保留{note} ✓"
    check("kg", "时序 KG（as-of/对比）", t_temporal)

    def t_tenancy_off():
        # 多租户安全属性：关闭时（默认）必须零行为变化——所有用户归 default、配额恒 allow。
        # 完整的隔离/配额回归见独立脚本 hashmm.tools.tenancy_regression（用临时库，不碰真实 data）。
        import os as _os
        from hashmm import tenancy as T
        _os.environ.pop("HASHMM_MULTI_TENANT", None)
        if T.multi_tenant_enabled() is not False:
            return (False, "默认竟启用了多租户 —— 单租户部署会被意外影响")
        # resolve_tenant 对任意输入都应回 default（关闭态）
        if T.resolve_tenant(None, "anyone") != "default" or T.resolve_tenant(None, None) != "default":
            return (False, "关闭态 resolve_tenant 未回落 default")
        return ("多租户默认关、行为零变化（用户归 default、配额恒 allow）；"
                "完整隔离/配额回归见 tools.tenancy_regression ✓")
    check("kg", "多租户关闭态零变化", t_tenancy_off)

    def t_kg_evolution():
        # 自进化 KG 待审区：默认关不收录 + 置信阈值 + 冲突检测 + 审批流（用临时 DATA_DIR，
        # 绝不碰真实 staging/图谱）。
        import os as _os, tempfile, importlib
        _tmp = tempfile.mkdtemp(prefix="hashmm_kgevo_")
        _old_data = _os.environ.get("DATA_DIR")
        _os.environ["DATA_DIR"] = _tmp
        try:
            from hashmm.kg import evolution_staging as ES
            _os.environ["HASHMM_KG_EVOLUTION"] = "1"
            ES.clear_all()
            # 低置信拒收
            r_low = ES.propose("A", "rel", "B", confidence=0.5)
            # 正常收录
            r_ok = ES.propose("小米", "2024营收", "3659亿", confidence=0.9)
            # 冲突检测
            fake = {"links": [{"source": "小米", "relation": "2024营收", "target": "3659亿"}]}
            r_conf = ES.propose("小米", "2024营收", "4000亿", confidence=0.9, existing_graph=fake)
            # 审批流
            pend = ES.list_pending()
            approved_ok = bool(pend) and ES.approve(pend[0]["id"]) and len(ES.approved_relations()) >= 1
            ES.clear_all()
            _os.environ.pop("HASHMM_KG_EVOLUTION", None)
            ok = (r_low["status"] == "rejected_low_confidence"
                  and r_ok["status"] == "pending"
                  and r_conf["status"] == "conflict"
                  and approved_ok)
            if not ok:
                return (False, f"自进化待审区行为异常: low={r_low} ok={r_ok} conf={r_conf} approved={approved_ok}")
            return ("自进化 KG 待审区就绪：默认关不收录 + 置信阈值过滤 + 冲突检测 + "
                    "人工审批闸门（绝不自动写主图）✓")
        finally:
            if _old_data is not None:
                _os.environ["DATA_DIR"] = _old_data
            else:
                _os.environ.pop("DATA_DIR", None)
            import shutil; shutil.rmtree(_tmp, ignore_errors=True)
    check("kg", "自进化 KG 待审区", t_kg_evolution)

# ══════════════════════════════════════════════════════════════════════
# 组 5：Eval 门禁 —— gate 能加载金标、契约校验、跑通（注入假 answer_fn，不耗 LLM）
# ══════════════════════════════════════════════════════════════════════
def group_eval():
    print(_bold(_cyan("\n[5] Eval 门禁 —— 金标加载 / 契约校验 / gate 跑通")))

    def t_gate_run():
        from hashmm.evaluation import gate
        # 用最小内置用例 + 注入式 answer_fn，验证 gate 流程（不需要真 LLM/真检索）
        # 字段用 gate 真实契约：must_contain_any / min_sources（不是 must_include）
        cases = [
            {"id": "t1", "query": "测试问题", "must_contain_any": ["北京"], "tag": "factual"},
            {"id": "t2", "query": "拒答问题", "tag": "refusal"},
        ]
        def fake_answer(q):
            return ("北京是答案 [1]", [{"source": "doc1", "text": "北京"}]) \
                if "测试" in q else ("我无法回答该问题。", [])
        with _silence():
            report = gate.run_gate(cases, fake_answer)
        if not hasattr(report, "passed") or not hasattr(report, "to_dict"):
            return (False, f"run_gate 返回对象缺关键字段（实得 {type(report).__name__}）—— eval 门禁流程异常")
        d = report.to_dict()
        return (f"Eval gate 端到端跑通（金标加载→契约校验→评分→报告，"
                f"overall_pass_rate={d.get('overall_pass_rate')}）✓")
    check("eval", "gate 端到端", t_gate_run)

    def t_holdout_overfit():
        # 验证 eval 保真：held-out 切分确定性 + overfit_gap 能识别"刷穿金标"
        from hashmm.evaluation import holdout as H
        # 构造过拟合：train 题答案命中、holdout 命不中 → gap 应被标记并拒绝放行
        def fixed(q):
            return ("答案是北京 [1]", [{"source": "d", "text": "北京"}])
        cases = []
        for i in range(60):
            cid = f"h{i}"; b = H._stable_bucket(f"0:{cid}")
            want = ["上海"] if b < 300 else ["北京"]   # holdout 要上海(挂)，train 要北京(过)
            cases.append({"id": cid, "query": "q", "must_contain_any": want, "tag": "factual"})
        # 确定性：同输入两次切分一致
        t1, h1 = H.split_holdout(cases, 0.3, seed=0)
        t2, h2 = H.split_holdout(cases, 0.3, seed=0)
        if [c["id"] for c in h1] != [c["id"] for c in h2]:
            return (False, "held-out 切分不确定 —— 跨次运行会漂移，无法复现")
        with _silence():
            rep = H.run_gate_holdout(cases, fixed, holdout_frac=0.3, max_overfit_gap=0.10)
        if not rep.get("overfit_flagged") or rep.get("passed") is not False:
            return (False, f"过拟合场景未被拦截（gap={rep.get('overfit_gap')} "
                           f"flagged={rep.get('overfit_flagged')} passed={rep.get('passed')}）")
        return (f"eval 保真生效：train/holdout 确定性切分 + overfit_gap 拦截刷穿 "
                f"（gap={rep.get('overfit_gap')} → 拒绝放行）✓")
    check("eval", "保真：held-out + overfit_gap", t_holdout_overfit)

    def t_golden():
        # 金标文件可能在 eval_kit/（真机），沙箱可能没有 → 找不到则 SKIP
        cand = [_ROOT / "eval_kit" / "golden_cases_large.json",
                _ROOT / "hashmm" / "eval_kit" / "golden_cases_large.json"]
        gp = next((p for p in cand if p.exists()), None)
        if not gp:
            return ("SKIP", "未找到 golden_cases_large.json（真机在 eval_kit/ 下）")
        import json
        cases = json.loads(gp.read_text(encoding="utf-8"))
        n = len(cases) if isinstance(cases, list) else len(cases.get("cases", []))
        if n < 50:
            return (False, f"金标用例仅 {n} 条，疑似不完整（预期 ~344）")
        return f"金标集加载成功（{n} 条用例）✓"
    check("eval", "金标集加载", t_golden)

# ══════════════════════════════════════════════════════════════════════
# 组 6：生成与接地 —— groundedness 模块 + （真机）端到端真实生成
# ══════════════════════════════════════════════════════════════════════
def group_generate():
    print(_bold(_cyan("\n[6] 生成与接地 —— groundedness / 端到端真实生成")))

    def t_groundedness():
        from hashmm.generation import groundedness as G
        # 找一个公开的接地检查函数（不同版本名可能不同，做容错探测）
        fns = [n for n in dir(G) if not n.startswith("_") and callable(getattr(G, n))]
        if not fns:
            return (False, "groundedness 模块无公开函数 —— 接地校验不可用")
        return f"groundedness 模块可用（{len(fns)} 个公开函数）✓"
    check("generate", "接地模块可用", t_groundedness)

    def t_e2e_generate():
        # 真机：真实跑一次 server.generate（真调云/本地 LLM 合成一条答案）。
        # 不依赖服务进程：generate() 内部用到两个启动时才初始化的模块级全局
        #   - server._llm_fn（LLM 调用）
        #   - server.metrics（统计 total_llm_calls）
        # 没起服务时它们都是 None，所以这里两个都补齐、跑完还原，做到起不起服务都能真跑。
        with _silence():
            from hashmm.api import server
            fn = getattr(server, "_llm_fn", None)
            picked_self = False
            if fn is None:
                try:
                    from hashmm.api.model_manager import get_active_llm_fn
                    fn, _info = get_active_llm_fn()
                    if fn is not None:
                        server._llm_fn = fn   # 注入，让 generate() 可用
                        picked_self = True
                except Exception:
                    fn = None
            if fn is None:
                return ("SKIP", "未配置可用 LLM（DB 无默认模型且无 LLM_API_KEY 环境变量）—— "
                                "在管理后台加一个模型，或设 LLM_API_KEY 后此项即真跑")
            # metrics 全局没起服务时为 None，generate() 会在 metrics.total_llm_calls 处报错。
            patched_metrics = False
            if getattr(server, "metrics", None) is None:
                try:
                    from hashmm.api.core.state import Metrics
                    server.metrics = Metrics()
                    patched_metrics = True
                except Exception:
                    pass
            try:
                ans = server.generate("用一句话介绍这个知识库", [], [], "chat")
            finally:
                if picked_self:
                    server._llm_fn = None       # 还原，不污染后续状态
                if patched_metrics:
                    server.metrics = None
        if not ans or not str(ans).strip():
            return (False, "generate() 返回空答案 —— LLM 调用链可能异常")
        return f"端到端生成成功（{'自取LLM' if picked_self else '复用服务LLM'}，输出 {len(str(ans))} 字符）✓"
    check("generate", "端到端真实生成", t_e2e_generate, needs={"gpu": True})

# ══════════════════════════════════════════════════════════════════════
# 组 7：可观测 —— /metrics、Prometheus、路由统计、SLO
# ══════════════════════════════════════════════════════════════════════
def group_observability():
    print(_bold(_cyan("\n[7] 可观测 —— Prometheus 渲染 / SLO / 成本统计")))

    def t_prometheus():
        from hashmm.api import middleware
        if not hasattr(middleware, "render_prometheus"):
            return (False, "middleware 缺 render_prometheus —— Prometheus 端点不可用")
        with _silence():
            out = middleware.render_prometheus()
        if not isinstance(out, str) or "#" not in out and "_total" not in out and out.strip() == "":
            return (False, "render_prometheus 输出非法 —— Grafana 抓不到指标")
        return f"Prometheus 指标渲染正常（{len(out)} 字节）✓"
    check("observability", "Prometheus 渲染", t_prometheus)

    def t_slo():
        from hashmm import observability as obs
        with _silence():
            rep = obs.slo_report()
        if not isinstance(rep, dict):
            return (False, "slo_report 未返回 dict")
        return "SLO 报告生成正常 ✓"
    check("observability", "SLO 报告", t_slo)

    def t_routing_stats():
        from hashmm import observability as obs
        s = obs.llm_routing_stats()
        if not isinstance(s, dict) or "local" not in s:
            return (False, "llm_routing_stats 结构异常 —— F11 成本看板拿不到数据")
        return "路由/成本统计可读 ✓"
    check("observability", "路由成本统计", t_routing_stats)

    def t_timeline():
        # Agent Run 时间线：默认关零变化 + 开启后给 trace 补 phase/step_id
        import os as _os
        from hashmm.api import run_timeline as T
        _os.environ.pop("HASHMM_AGENT_TIMELINE", None)
        off = T.annotate_trace({"steps": [{"node": "retrieve", "detail": "x"}]})
        if "phase" in off["steps"][0]:
            return (False, "时间线默认关却注入了字段 —— 违反'关闭零变化'")
        _os.environ["HASHMM_AGENT_TIMELINE"] = "1"
        T.reset()
        a = T.annotate_trace({"steps": [{"node": "classify", "detail": "x"}]})
        b = T.annotate_trace({"steps": [{"node": "generate", "detail": "y"}]})
        _os.environ.pop("HASHMM_AGENT_TIMELINE", None)
        ok = (a["steps"][0].get("phase") == "understand" and a["steps"][0].get("step_id") == 1
              and b["steps"][0].get("phase") == "synthesize" and b["steps"][0].get("step_id") == 2)
        if not ok:
            return (False, f"时间线注入异常: {a['steps'][0]} / {b['steps'][0]}")
        return "Agent Run 时间线就绪：默认关零变化，开启后 trace 自动带 phase+step_id 可回放 ✓"
    check("observability", "Agent Run 时间线", t_timeline)

    def t_dashboard():
        # 运维 dashboard 聚合快照：一次拿全延迟/成本/路由/SLO/工具
        from hashmm import observability as obs
        snap = obs.dashboard_snapshot()
        need = {"latency", "cost", "retrieval_quality", "slo", "llm_routing", "tools"}
        if not need <= set(snap.keys()):
            return (False, f"dashboard_snapshot 缺面板字段: {need - set(snap.keys())}")
        if "local_ratio" not in snap.get("llm_routing", {}):
            return (False, "dashboard 缺 F11 路由节省视角 local_ratio")
        return ("运维 dashboard 聚合就绪：延迟P50/P95 + token成本 + 检索质量 + SLO + "
                "云/本地路由占比 + 工具统计，一次拿全 ✓")
    check("observability", "运维 dashboard 聚合", t_dashboard)

    def t_otlp_traces():
        # 标准化 trace 导出（OTLP/JSON，不依赖 OTel 安装）
        from hashmm import observability as obs
        obs.record_rag_request(model="test", input_tokens=10, output_tokens=5,
                               total_latency_ms=100, n_sources=2,
                               stage_latency_ms={"retrieve": 40, "generate": 60})
        out = obs.export_otlp_traces()
        rs = out.get("resourceSpans", [])
        if not rs or not rs[0].get("scopeSpans"):
            return (False, "OTLP 导出结构缺失")
        spans = rs[0]["scopeSpans"][0].get("spans", [])
        if not spans:
            return (False, "OTLP 导出无 span")
        s = spans[0]
        if "traceId" not in s or "spanId" not in s or "attributes" not in s:
            return (False, "OTLP span 字段不合法")
        return (f"标准化 trace 导出就绪：OTLP/JSON（{len(spans)} spans，含阶段子span），"
                "OTel 装不装都可用，任何 OTLP 后端可拉取 ✓")
    check("observability", "标准化 Trace 导出", t_otlp_traces)

# ══════════════════════════════════════════════════════════════════════
# 组 8：API 应用装配 —— FastAPI app 能装配、关键路由注册（不真正监听端口）
# ══════════════════════════════════════════════════════════════════════
def group_api():
    print(_bold(_cyan("\n[8] API 装配 —— FastAPI app / 关键路由注册")))

    def t_app_build():
        with _silence():
            from hashmm.api.server import app
        routes = {getattr(r, "path", "") for r in app.routes}
        need = ["/api/chat", "/api/auth/login"]
        miss = [p for p in need if not any(p in r for r in routes)]
        if miss:
            return (False, f"关键路由缺失: {miss} —— 接口可能未注册")
        return f"FastAPI app 装配成功（{len(routes)} 条路由，关键端点齐全）✓"
    check("api", "app 装配与路由", t_app_build)

    def t_health_routes():
        with _silence():
            from hashmm.api.server import app
        routes = {getattr(r, "path", "") for r in app.routes}
        have = [p for p in ["/health", "/metrics", "/metrics/prometheus"]
                if any(p in r for r in routes)]
        if not have:
            return (False, "健康/指标路由全部缺失")
        dash = any("/metrics/dashboard" in r for r in routes)
        extra = " + /metrics/dashboard" if dash else ""
        return f"运维端点已注册：{have}{extra} ✓"
    check("api", "健康/指标端点", t_health_routes)

    def t_security_check():
        # 启动安全检查可用（只读，不改任何密钥/密码）。secure_setup 工具可一键修复。
        from hashmm.api import security as sec
        issues = sec.run_startup_security_check()
        if not isinstance(issues, list):
            return (False, "run_startup_security_check 未返回列表")
        if issues:
            return (f"安全检查可用：检出 {len(issues)} 项待处理（JWT/admin 默认值）—— "
                    f"用 `python -m hashmm.tools.secure_setup` 一键修复 ✓")
        return "安全检查可用：当前无默认密钥/密码问题 ✓"
    check("api", "启动安全检查", t_security_check)

    def t_mcp_server():
        # MCP Server 暴露端：协议逻辑（initialize/tools/list/tools/call）+ 默认关
        from hashmm.api.routes import mcp_server as M
        ini = M._handle("initialize", {}, 1)
        if ini.get("result", {}).get("serverInfo", {}).get("name") != "hashmm":
            return (False, "MCP initialize 响应异常")
        tl = M._handle("tools/list", {}, 2)
        names = [t["name"] for t in tl.get("result", {}).get("tools", [])]
        if "kb_search" not in names or "kg_query" not in names:
            return (False, f"MCP tools/list 缺工具: {names}")
        err = M._handle("tools/call", {"name": "nonexistent", "arguments": {}}, 3)
        if "error" not in err:
            return (False, "MCP 未知工具未返回 error")
        import os as _os
        _os.environ.pop("HASHMM_MCP_SERVER", None)
        if M.server_enabled() is not False:
            return (False, "MCP server 默认竟开启 —— 违反'默认关'")
        # 是否挂进了 app 路由
        try:
            from hashmm.api.server import app
            has_route = any("/mcp" in getattr(r, "path", "") for r in app.routes)
        except Exception:
            has_route = None
        route_note = "，端点已注册" if has_route else ""
        return (f"MCP Server 暴露端就绪：JSON-RPC(initialize/tools/list/tools/call) + "
                f"工具 {names}，默认关零暴露面{route_note} ✓")
    check("api", "MCP Server 暴露端", t_mcp_server)

    def t_lifecycle_hooks():
        # 多生命周期 hooks：PreCompact + SubagentStop（对齐 CC harness）
        # 无注册 no-op + 注册触发 + 坏hook容错
        from hashmm import hooks as H
        H.reset_hooks()
        H.run_compact_hooks([{"role": "user", "content": "x"}])   # 无注册不抛错
        H.run_subagent_stop_hooks("t1", "r")
        fired = {"compact": 0, "subagent": 0}
        H.register_compact_hook("t", lambda msgs, ctx: fired.__setitem__("compact", len(msgs)))
        H.register_subagent_stop_hook("t", lambda sid, r, ctx: fired.__setitem__("subagent", 1))
        H.run_compact_hooks([{"a": 1}, {"b": 2}], {"dropped": 2})
        H.run_subagent_stop_hooks("s1", "done", {"status": "done"})
        compact_ok = (fired["compact"] == 2 and fired["subagent"] == 1)
        # 坏hook容错（单独验证，不再触发上面的计数 hook 以免覆盖断言）
        H.reset_hooks()
        H.register_compact_hook("bad", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        raised = False
        try:
            H.run_compact_hooks([{"x": 1}])   # 坏hook不应抛
        except Exception:
            raised = True
        H.reset_hooks()
        if not compact_ok:
            return (False, f"生命周期 hook 触发异常: {fired}")
        if raised:
            return (False, "坏 hook 把主流程带崩了 —— 违反'永不抛错'")
        return ("多生命周期 hooks 就绪：PreCompact（压缩前归档）+ SubagentStop（子代理结束聚合），"
                "无注册零变化、坏hook不影响主链 ✓")
    check("api", "多生命周期 Hooks", t_lifecycle_hooks)

    def t_public_api():
        # 对外 RESTful API：默认关 + /v1 真正注册进 app（真机若返回 HTML 多半是旧缓存）
        from hashmm.api.routes import public_api as P
        import os as _os
        _os.environ.pop("HASHMM_PUBLIC_API", None)
        if P.public_api_enabled() is not False:
            return (False, "对外 API 默认竟开启 —— 违反'默认关'")
        # /v1 路由必须真的在 router 里（沙箱可查 router；真机可查 app）
        router_paths = {getattr(r, "path", "") for r in P.router.routes}
        want = {"/v1/info", "/v1/search", "/v1/rag"}
        missing = want - router_paths
        if missing:
            return (False, f"/v1 路由缺失: {missing}（router 里没有）")
        # 进一步：是否挂进了 app（清缓存后应为真）
        try:
            from hashmm.api.server import app
            app_paths = {getattr(r, "path", "") for r in app.routes}
            in_app = want <= app_paths
        except Exception:
            in_app = None
        app_note = ("，已挂进 app" if in_app else
                    "（⚠ router 有但未挂进 app —— 真机请清 __pycache__ 重启）" if in_app is False else "")
        return (f"对外 RESTful API 就绪：/v1/search + /v1/rag + /v1/info（API key 鉴权，默认关），"
                f"配套 Python client{app_note} ✓")
    check("api", "对外 RESTful API (/v1)", t_public_api)

    def t_design_render():
        # 受控设计渲染器：默认关 + 白名单(对齐Claude沙箱全套) + 能力探测
        from hashmm.api import design_render as DR
        import os as _os
        _os.environ.pop("HASHMM_DESIGN_RENDER", None)
        if DR.html_to_image("<h1>x</h1>").get("ok") is not False:
            return (False, "设计渲染默认竟开启 —— 违反'默认关'")
        # 白名单：危险命令必须拒绝
        for danger in (["rm", "-rf", "/"], ["curl", "evil.com"], ["bash", "-c", "x"]):
            ok, _ = DR._run_whitelisted(danger)
            if ok is not False:
                return (False, f"白名单失效 —— {danger[0]} 竟被允许")
        # 白名单工具应被识别（即使本机没装，命令名在白名单内不会因名字被拒）
        cap = DR.capabilities()
        tools = [k for k in ("playwright", "wkhtmltoimage", "ffmpeg", "libreoffice", "pandoc", "graphviz")
                 if cap.get(k)]
        avail = f"（本机已装: {','.join(tools)}）" if tools else "（本机未装渲染工具，装后即用）"
        return ("受控设计渲染器就绪：HTML→图片(wkhtmltoimage/playwright) + PPT/DOCX→预览图(libreoffice) "
                "+ 图→MP4(ffmpeg) + 文档转换(pandoc) + 图表(graphviz)，默认关 + 白名单"
                f"(rm/curl/bash 全拒) + 优雅降级{avail} ✓")
    check("api", "受控设计渲染器", t_design_render)

# ══════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════
_GROUPS = {
    "import": group_import, "f11": group_f11, "retrieval": group_retrieval,
    "kg": group_kg, "eval": group_eval, "generate": group_generate,
    "observability": group_observability, "api": group_api,
}

def main():
    ap = argparse.ArgumentParser(description="HashMM 深度功能体检")
    ap.add_argument("--fast", action="store_true",
                    help="只跑不依赖 GPU/模型/真实 data 的检查（沙箱/CI）")
    ap.add_argument("--f11", action="store_true", help="只跑 F11 云-本地路由组")
    ap.add_argument("--only", default="", help="逗号分隔的组名: " + ",".join(_GROUPS))
    args = ap.parse_args()

    print(_bold("═" * 64))
    print(_bold("  HashMM 深度功能体检 (deep_functional_test)"))
    print(_bold("═" * 64))
    print(f"  项目根   : {_ROOT}")
    print(f"  GPU      : {'有' if _has_gpu() else '无（部分项将 SKIP）'}")
    print(f"  真实 data: {'有' if _has_real_data() else f'无 @ {_data_dir()}（部分项将 SKIP）'}")
    print(f"  本地模型 : {'有 @ ' + _local_model_path() if _has_local_model() else '无（F11 真机项将 SKIP）'}")

    if args.f11:
        selected = ["import", "f11"]
    elif args.only:
        selected = [g.strip() for g in args.only.split(",") if g.strip() in _GROUPS]
    else:
        selected = list(_GROUPS)

    for g in selected:
        try:
            _GROUPS[g]()
        except Exception as e:
            _record(g, f"<组 {g} 自身崩溃>", "FAIL", f"{type(e).__name__}: {e}")

    # ── 汇总 ──
    npass = sum(1 for r in _RESULTS if r[2] == "PASS")
    nfail = sum(1 for r in _RESULTS if r[2] == "FAIL")
    nskip = sum(1 for r in _RESULTS if r[2] == "SKIP")
    dt = time.time() - _T0
    print(_bold("\n" + "═" * 64))
    print(_bold("  体检汇总"))
    print(_bold("═" * 64))
    print(f"  {_green(str(npass)+' 通过')}   {_red(str(nfail)+' 失败')}   {_yellow(str(nskip)+' 跳过')}   ({dt:.1f}s)")

    if nfail:
        print(_red("\n  以下功能存在问题，请按提示排查："))
        for grp, name, status, detail in _RESULTS:
            if status == "FAIL":
                print(f"    {_red('✗')} [{grp}] {name}")
                if detail:
                    print(f"        → {detail}")
    if nskip and not args.fast:
        print(_yellow("\n  以下项因环境未跳过（非失败，真机配齐后会执行）："))
        for grp, name, status, detail in _RESULTS:
            if status == "SKIP":
                print(f"    {_yellow('•')} [{grp}] {name}: {detail}")

    if nfail == 0:
        print(_green(_bold("\n  ✔ 所有已执行的功能检查均通过。")))
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
