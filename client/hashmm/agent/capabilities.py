"""User-facing runtime capability projection.

The UI must not infer readiness from the existence of a card or a feature flag.
This module projects the tools and services that the Chat harness can actually
reach into one stable, privacy-safe contract shared by desktop and App.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _router_paths(*routers: Any) -> set[str]:
    """Return mounted paths declared by the supplied routers.

    This is deliberately based on the actual FastAPI route objects instead of
    feature flags, so a service is not advertised as ready when its API was
    renamed or stopped being registered.
    """
    return {
        str(getattr(route, "path", ""))
        for router in routers
        for route in getattr(router, "routes", [])
        if getattr(route, "path", "")
    }


_INTERNAL_TOOLS = frozenset({
    # These are implemented by AgentLoop/harness rather than the public
    # executor map. They are still real execution paths, not UI-only cards.
    "update_todo", "spawn_worker", "memory_recall", "remember_preference",
})

_USER_VISIBLE_CAPABILITIES = frozenset({
    "rag",
    "web_research",
    "image_search",
    "browser_use",
    "computer_use",
    "artifacts",
    "multi_agent",
    "memory",
    "canvas",
    "long_tasks",
    "automations",
    "publisher_studio",
})

_ADMIN_VISIBLE_CAPABILITIES = frozenset({
    "governed_skill_evolution",
    "mcp",
    "hooks",
    "execution_sandbox",
    "graph_rag",
})


def _attach_truth_contract(capability: dict[str, Any]) -> None:
    """Attach the product-facing truth contract to one capability in place.

    ``state`` is kept for backwards-compatible clients.  New clients use
    ``availability`` and ``visibility`` so a configured card, an admin
    diagnostic and a production-ready user capability are never conflated.
    """
    cap_id = str(capability.get("id") or "")
    state = str(capability.get("state") or "unavailable")
    missing_tools = list(capability.get("missing_tools") or [])
    wired = bool(capability.get("wired"))
    enabled = bool(capability.get("enabled"))
    availability = {
        "ready": "available",
        "degraded": "degraded",
        "setup_required": "unavailable",
        "disabled": "unavailable",
        "unavailable": "unavailable",
    }.get(state, "unavailable")
    visibility = (
        "user"
        if cap_id in _USER_VISIBLE_CAPABILITIES
        else "admin"
        if cap_id in _ADMIN_VISIBLE_CAPABILITIES
        else "diagnostic"
    )
    capability.update({
        "availability": availability,
        "visibility": visibility,
        "diagnostic_only": visibility == "diagnostic",
        "production_ready": bool(
            availability == "available"
            and enabled
            and wired
            and not missing_tools
        ),
        "evidence": {
            "kind": "runtime_contract",
            "route_or_tool_wiring_checked": True,
            "tests": list(capability.get("tests") or []),
        },
    })


def _declared_tool_names() -> set[str]:
    from hashmm.agent.loop import AgentLoop

    return {
        str(schema.get("function", {}).get("name") or "")
        for schema in AgentLoop._get_default_tools()
        if isinstance(schema, dict)
    } - {""}


def _tool_inventory() -> dict[str, Any]:
    """Return the Chat tool contract against the actual executor registry.

    A schema is not an executable capability.  Keeping this check next to the
    runtime projection prevents the desktop/App from advertising a tool which
    the Chat loop cannot dispatch.  Harness-owned internal tools are included
    explicitly; every other effective tool must have a callable executor.
    """
    declared = _declared_tool_names()
    executors: dict[str, Any] = {}
    try:
        from hashmm.agent.loop import AgentLoop
        executors = AgentLoop._get_tool_executors(AgentLoop.__new__(AgentLoop))
    except Exception:
        executors = {}
    bound = {
        str(name) for name, fn in executors.items()
        if str(name) and callable(fn)
    }
    effective = declared & (bound | _INTERNAL_TOOLS)
    missing = declared - bound - _INTERNAL_TOOLS
    return {
        "declared": sorted(declared),
        "effective": sorted(effective),
        "missing_executors": sorted(missing),
        "executor_only": sorted(bound - declared),
    }


def _tool_names() -> set[str]:
    """Return only tools that the governed Chat loop can really execute."""
    return set(_tool_inventory()["effective"])


def _module_index() -> dict[str, dict[str, Any]]:
    from hashmm.agent.modules import status

    return {str(item.get("key") or ""): item for item in status()}


def _module_capability(
    module: dict[str, Any],
    *,
    cap_id: str,
    title: str,
    description: str,
    active_tools: set[str],
    required: set[str],
    surfaces: dict[str, str],
    entrypoints: list[str],
    tests: list[str],
    requires_desktop: bool = False,
) -> dict[str, Any]:
    declared = set(module.get("tools") or [])
    exposed = sorted(declared & active_tools)
    missing = sorted(required - active_tools)
    enabled = bool(module.get("enabled"))
    wired = bool(module.get("wired"))
    healthy = bool(module.get("healthy"))
    if not enabled:
        state, reason = "disabled", "已在能力设置中关闭"
    elif not wired or missing:
        state = "unavailable"
        reason = str(module.get("health_msg") or "尚未完整接入 Chat")
    elif not healthy:
        state = "degraded"
        reason = str(module.get("health_msg") or "运行依赖尚未就绪")
    else:
        state = "ready"
        reason = str(module.get("health_msg") or "已接入 Chat")
    return {
        "id": cap_id,
        "title": title,
        "description": description,
        "state": state,
        "reason": reason,
        "enabled": enabled,
        "wired": wired and not missing,
        "requires_desktop": requires_desktop,
        "active_tools": exposed,
        "missing_tools": missing,
        "surfaces": surfaces,
        "entrypoints": entrypoints,
        "tests": tests,
    }


def build_runtime_capabilities(
    user_id: str = "",
    *,
    mounted_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic capability snapshot without credentials/secrets.

    ``mounted_paths`` is supplied by the live FastAPI application.  Keeping it
    optional preserves offline diagnostics, while the authenticated runtime
    endpoint can prove that a service is actually mounted instead of merely
    importing a router object that never became reachable.
    """
    active_tools = _tool_names()
    tool_inventory = _tool_inventory()
    modules = _module_index()
    capabilities: list[dict[str, Any]] = [
        _module_capability(
            modules["rag"], cap_id="rag", title="知识与证据",
            description="从知识库和知识图谱检索，并把来源带回回答。",
            active_tools=active_tools, required={"kb_search", "kg_query"},
            surfaces={"chat": "direct", "desktop": "direct", "app": "direct"},
            entrypoints=["Chat 自动检索", "资料库", "知识图谱"],
            tests=["tests/test_v367_graph_engineering.py", "tests/test_v341_grounding_ledger.py"],
        ),
        _module_capability(
            modules["web"], cap_id="web_research", title="联网调研",
            description="搜索公开网络并读取网页或视频文字材料。",
            active_tools=active_tools, required={"web_search", "fetch_url"},
            surfaces={"chat": "direct", "desktop": "direct", "app": "direct"},
            entrypoints=["Chat 自动模式", "深度检索"],
            tests=["tests/test_v350_tool_call_eval.py", "tests/test_backend_security.py"],
        ),
        _module_capability(
            modules["image"], cap_id="image_search", title="我的图片检索",
            description="只在当前账号上传的图片中按文件名、标签和图片说明检索。",
            active_tools=active_tools, required={"image_search"},
            surfaces={"chat": "direct", "desktop": "image_library", "app": "chat"},
            entrypoints=["Chat 自动检索", "图片库"],
            tests=["tests/test_v368_runtime_capabilities.py"],
        ),
        _module_capability(
            modules["browser"], cap_id="browser_use", title="浏览器操作",
            description="打开动态网页、读取页面、点击和填写表单，并保留截图证据。",
            active_tools=active_tools,
            required={"browser_open", "browser_read", "browser_act", "browser_screenshot"},
            surfaces={"chat": "direct", "desktop": "right_panel", "app": "desktop_relay"},
            entrypoints=["Chat 浏览器模式", "右侧浏览器", "App 桌面接力"],
            tests=["tests/test_browser_kernel.py", "desktop/tests-node/test_browser_trajectory.js"],
        ),
        _module_capability(
            modules["computer"], cap_id="computer_use", title="文件与电脑操作",
            description="读取和修改工作区文件、执行受控命令；桌面交互由在线电脑执行。",
            active_tools=active_tools,
            required={"file_tree", "read_file_range", "str_replace", "execute_code"},
            surfaces={"chat": "direct", "desktop": "native", "app": "desktop_relay"},
            entrypoints=["Chat 电脑操作", "当前工作", "App 桌面接力"],
            tests=["tests/test_computer_task_dispatch.py", "desktop/tests-node/test_computeruse.js"],
            requires_desktop=True,
        ),
        _module_capability(
            modules["computer"], cap_id="artifacts", title="文档与成果",
            description="在同一任务里生成并继续修改 Word、PDF、表格、PPT 和代码文件。",
            active_tools=active_tools,
            required={"create_document", "create_pdf", "create_xlsx", "create_pptx_from_plan"},
            surfaces={"chat": "direct", "desktop": "right_panel", "app": "viewer"},
            entrypoints=["Chat 画布", "文档工坊", "文件预览"],
            tests=["tests/test_code_file_download.py", "tests/test_v362_docstudio_incremental_cache.py"],
        ),
        _module_capability(
            modules["dispatch"], cap_id="multi_agent", title="多智能体协作",
            description="主 Agent 按需派出隔离专员，或启动可观察、可停止的团队任务。",
            active_tools=active_tools, required={"spawn_worker"},
            surfaces={"chat": "direct", "desktop": "team_workspace", "app": "team_workspace"},
            entrypoints=["Chat 多智能体", "智能体工坊", "执行记录"],
            tests=["tests/test_v343_team_lifecycle.py", "tests/test_v72_loop_engineering.py"],
        ),
        _module_capability(
            modules["memory"], cap_id="memory", title="长期记忆",
            description="按需召回用户偏好和历史经验，写入动作经过明确的记忆工具。",
            active_tools=active_tools, required={"memory_recall", "remember_preference"},
            surfaces={"chat": "direct", "desktop": "manager", "app": "manager"},
            entrypoints=["Chat 自动召回", "偏好与记忆"],
            tests=["tests/test_v96_user_memory.py", "tests/test_v51_long_conversation.py"],
        ),
    ]

    # Canvas/long-task services are first-class APIs rather than model tools.
    # Their readiness is derived from registered route objects, not a static
    # card. This catches accidental route removal during application assembly.
    from hashmm.api.routes.canvas_ask import router as canvas_ask_router
    from hashmm.api.routes.canvas_locks import router as canvas_locks_router
    from hashmm.api.routes.canvas_share import router as canvas_share_router
    from hashmm.api.routes.canvas_templates import router as canvas_templates_router
    from hashmm.api.routes.canvas_evidence import router as canvas_evidence_router
    from hashmm.api.routes.conversations import router as conversations_router
    from hashmm.api.routes.loops import router as loops_router
    from hashmm.api.routes.team_ops import router as team_ops_router
    from hashmm.api.routes.evolution import router as evolution_router
    from hashmm.api.routes.user_work import router as user_work_router

    declared_canvas_paths = _router_paths(
        canvas_ask_router, canvas_locks_router, canvas_share_router,
        canvas_templates_router, canvas_evidence_router, conversations_router,
    )
    canvas_paths = mounted_paths if mounted_paths is not None else declared_canvas_paths
    canvas_required = {
        "/api/canvas/ask", "/api/canvas/lock", "/api/canvas/templates",
        "/api/canvas/publish",
        "/api/conversations/{conv_id}/evidence-refs",
        "/api/conversations/{conv_id}/files/{filename}/evidence-links",
        "/api/conversations/{conv_id}/files/{filename}/canvas-blocks",
    }
    canvas_ready = canvas_required <= canvas_paths
    canvas_wired = canvas_ready and "canvas_block_patch" in active_tools
    declared_loop_paths = _router_paths(loops_router)
    loop_paths = mounted_paths if mounted_paths is not None else declared_loop_paths
    loops_ready = {
        "/api/loops", "/api/loops/goal", "/api/loops/{loop_id}/pause",
        "/api/loops/{loop_id}/resume", "/api/loops/{loop_id}/stop",
    } <= loop_paths
    declared_team_paths = _router_paths(team_ops_router)
    team_paths = mounted_paths if mounted_paths is not None else declared_team_paths
    team_required = {
        "/api/team/start", "/api/team/status/{team_id}",
        "/api/team/{team_id}/tree",
        "/api/team/{team_id}/agents/{session_id}/message",
        "/api/team/{team_id}/agents/{session_id}/stop",
    }
    team_ready = team_required <= team_paths and "spawn_worker" in active_tools
    declared_evolution_paths = _router_paths(evolution_router)
    evolution_paths = mounted_paths if mounted_paths is not None else declared_evolution_paths
    evolution_required = {
        "/api/evolution/skills/{skill_id}/evolve",
        "/api/evolution/skills/{skill_id}/evolution",
        "/api/evolution/skills/{skill_id}/evolution/{run_id}/evaluate",
        "/api/evolution/skills/{skill_id}/evolution/{run_id}/approve",
        "/api/evolution/skills/{skill_id}/evolution/{run_id}/reject",
        "/api/evolution/skills/{skill_id}/evolution/{run_id}/rollback",
    }
    evolution_ready = evolution_required <= evolution_paths
    declared_automation_paths = _router_paths(user_work_router)
    automation_paths = (
        mounted_paths if mounted_paths is not None else declared_automation_paths
    )
    automation_required = {
        "/api/user-work/routines",
        "/api/user-work/routines/{task_id}",
        "/api/user-work/routines/{task_id}/run",
        "/api/user-work/routines/{task_id}/toggle",
    }
    automation_routes_ready = automation_required <= automation_paths
    try:
        from hashmm import scheduler
        automation_actions = sorted(
            set(scheduler.list_actions())
            & {"daily_brief", "corpus_digest", "kg_health", "ai_news_radar"}
        )
        automation_background_ready = scheduler.scheduler_enabled()
    except Exception:
        automation_actions = []
        automation_background_ready = False
    automation_wired = automation_routes_ready and bool(automation_actions)
    try:
        from hashmm.agent.skill_packs import builtin_src_dir
        publisher_skill_ready = (
            builtin_src_dir() / "wechat-ai-briefing" / "SKILL.md"
        ).is_file()
    except Exception:
        publisher_skill_ready = False
    publisher_required_tools = {"web_search", "fetch_url", "create_file"}
    publisher_active_tools = sorted(publisher_required_tools & active_tools)
    publisher_missing_tools = sorted(publisher_required_tools - active_tools)
    publisher_wired = publisher_skill_ready and not publisher_missing_tools
    for capability in capabilities:
        if capability.get("id") != "multi_agent":
            continue
        capability["state"] = "ready" if team_ready else "unavailable"
        capability["wired"] = team_ready
        capability["reason"] = (
            "持久 Agent 树、任务 DAG、邮箱、单 Agent 控制与独立验证已装配"
            if team_ready else "多 Agent 生命周期 API 或子 Agent 工具未完整挂载"
        )
        capability["missing_tools"] = (
            ([] if "spawn_worker" in active_tools else ["spawn_worker"])
            + sorted(team_required - team_paths)
        )
        capability["tests"] = [
            "tests/test_v392_agent_mesh.py",
            "tests/test_v382_team_agent_sessions.py",
        ]
    capabilities.extend([
        {
            "id": "publisher_studio", "title": "AI 公众号简报工作室",
            "description": "从热点候选、原始来源核验到公众号 Markdown/HTML 交付，事实与编辑判断分开呈现。",
            "state": (
                "ready" if publisher_wired
                else "degraded" if publisher_skill_ready
                else "unavailable"
            ),
            "reason": (
                "工作方法、联网取证与文档交付执行器均已装配"
                if publisher_wired
                else (
                    "工作方法已安装，但缺少实际执行器："
                    + "、".join(publisher_missing_tools)
                    if publisher_skill_ready
                    else "内置公众号工作方法未随服务端安装"
                )
            ),
            "enabled": True,
            "wired": publisher_wired,
            "requires_desktop": False,
            "active_tools": publisher_active_tools,
            "missing_tools": (
                publisher_missing_tools
                + ([] if publisher_skill_ready else ["wechat-ai-briefing/SKILL.md"])
            ),
            "surfaces": {"chat": "skill", "desktop": "plugin_center", "app": "chat"},
            "entrypoints": ["插件 · 我的工作方法", "Chat 自动匹配", "自动任务 · AI 热点候选雷达"],
            "tests": ["tests/test_v699_publisher_automation.py"],
        },
        {
            "id": "canvas", "title": "工作画布",
            "description": "把对话与网页证据变成可回源、可验证的成果，并保留版本、编辑锁和发布边界。",
            "state": "ready" if canvas_wired else "unavailable",
            "reason": "画布证据、修订与发布链路已装配" if canvas_wired else "画布 API 或块级修订工具未完整挂载",
            "enabled": True, "wired": canvas_wired, "requires_desktop": False,
            "active_tools": ["canvas_block_patch"] if "canvas_block_patch" in active_tools else [],
            "missing_tools": (
                ([] if "canvas_block_patch" in active_tools else ["canvas_block_patch"])
                + ([] if canvas_ready else sorted(canvas_required - canvas_paths))
            ),
            "surfaces": {"chat": "artifact", "desktop": "right_panel", "app": "native"},
            "entrypoints": ["Chat 画布", "右侧文件/画布", "App 画布"],
            "tests": ["tests/test_v391_browser_canvas_twin.py", "frontend-next/__tests__/canvasVersions.test.ts"],
        },
        {
            "id": "long_tasks", "title": "持续任务",
            "description": "任务可后台运行、暂停、恢复、追加要求，并把结果回写到原对话。",
            "state": "ready" if loops_ready and "update_todo" in active_tools else "unavailable",
            "reason": "持久循环、运行轨迹和上下文压缩已装配并挂载" if loops_ready else "持续任务 API 未完整挂载到运行服务",
            "enabled": True, "wired": loops_ready and "update_todo" in active_tools,
            "requires_desktop": False, "active_tools": ["update_todo"] if "update_todo" in active_tools else [],
            "missing_tools": [] if loops_ready else ["durable_loop_routes"],
            "surfaces": {"chat": "direct", "desktop": "run_timeline", "app": "run_timeline"},
            "entrypoints": ["Chat 长任务", "任务与进度", "App 执行记录"],
            "tests": ["tests/test_v335_durable_agent_loops.py", "tests/test_v51_long_conversation.py"],
        },
        {
            "id": "automations", "title": "自动任务",
            "description": "按你的时区重复执行只读工作，保存运行、核验和投递记录，并可回到原对话。",
            "state": (
                "ready" if automation_wired and automation_background_ready
                else "degraded" if automation_wired
                else "unavailable"
            ),
            "reason": (
                f"后台调度已就绪，可执行 {len(automation_actions)} 种已安装工作"
                if automation_wired and automation_background_ready
                else "自动任务可创建和手动运行；服务端尚未开启后台调度"
                if automation_wired
                else "自动任务 API 或已安装动作不完整"
            ),
            "enabled": True,
            "wired": automation_wired,
            "requires_desktop": False,
            "active_tools": [],
            "missing_tools": (
                sorted(automation_required - automation_paths)
                + ([] if automation_actions else ["scheduler_actions"])
                + ([] if automation_background_ready else ["background_scheduler"])
            ),
            "surfaces": {
                "chat": "result_delivery",
                "desktop": "automation_workspace",
                "app": "today",
            },
            "entrypoints": ["自动任务", "插件 · 持续工作", "App 今天"],
            "tests": [
                "tests/test_v669_user_automation.py",
                "frontend-next/__tests__/v669AutomationCapability.test.ts",
            ],
        },
        {
            "id": "governed_skill_evolution", "title": "受治理技能改进",
            "description": "把成功经验生成为隔离候选，经同源历史成对回放、固定安全对抗、成本与延迟门禁、人工确认和基线哈希校验后才进入 Chat，并支持精确回滚。",
            "state": "ready" if evolution_ready else "unavailable",
            "reason": "候选隔离、历史回放、安全对抗、发布门、人工采用、并发保护和回滚链路已装配" if evolution_ready else "技能演进评测或决策 API 未完整挂载",
            "enabled": True, "wired": evolution_ready, "requires_desktop": False,
            "active_tools": [], "missing_tools": sorted(evolution_required - evolution_paths),
            "surfaces": {"chat": "approved_skill", "desktop": "evolution_review", "app": "work_feed"},
            "entrypoints": ["自我进化 · 学习型技能", "统一工作记录"],
            "tests": [
                "tests/test_v393_governed_skill_evolution.py",
                "tests/test_v394_skill_replay_gate.py",
            ],
        },
    ])

    # MCP readiness is configuration-dependent. No endpoint/header/secret is exposed.
    mcp_tools: list[str] = []
    try:
        from hashmm.tools.mcp_client import get_enabled_schemas
        mcp_tools = sorted(
            str(item.get("function", {}).get("name") or "")
            for item in get_enabled_schemas()
            if str(item.get("function", {}).get("name") or "")
        )
    except Exception:
        mcp_tools = []
    capabilities.append({
        "id": "mcp", "title": "MCP 扩展",
        "description": "把外部工具按结构化 schema 接入同一审批、Hooks 和审计边界。",
        "state": "ready" if mcp_tools else "setup_required",
        "reason": f"已接入 {len(mcp_tools)} 个 MCP 工具" if mcp_tools else "尚未配置可用的 MCP 工具",
        "enabled": True, "wired": bool(mcp_tools), "requires_desktop": False,
        "active_tools": mcp_tools, "missing_tools": [],
        "surfaces": {"chat": "direct", "desktop": "manager", "app": "status"},
        "entrypoints": ["管理后台 · 可用能力", "Chat 自动工具选择"],
        "tests": ["tests/test_mcp_client_v2.py", "desktop/tests-node/test_mcp.js"],
    })

    hook_count = 0
    try:
        from hashmm.api.routes.hooks import _load
        hook_count = sum(1 for value in _load().values() if value.get("owner") == user_id)
    except Exception:
        hook_count = 0
    capabilities.append({
        "id": "hooks", "title": "自动触发",
        "description": "用受密钥保护的触发地址启动真实派活，并记录命中与执行结果。",
        "state": "ready" if hook_count else "setup_required",
        "reason": f"当前账号有 {hook_count} 个触发器" if hook_count else "尚未为当前账号创建触发器",
        "enabled": True, "wired": True, "requires_desktop": False,
        "active_tools": [], "missing_tools": [],
        "surfaces": {"chat": "event", "desktop": "manager", "app": "manager"},
        "entrypoints": ["高级能力 · 自动触发"],
        "tests": ["tests/test_hooks.py", "tests/test_declarative_hook_boundary.py"],
    })

    try:
        from hashmm.agent.sandbox import sandbox_status
        sandbox = sandbox_status()
    except Exception as exc:
        sandbox = {
            "backend": "unavailable", "available": False, "isolated": False,
            "detail": f"沙箱状态检测失败: {type(exc).__name__}",
        }
    if sandbox.get("isolated"):
        sandbox_state = "ready"
    elif sandbox.get("available"):
        sandbox_state = "degraded"
    else:
        sandbox_state = "unavailable"
    sandbox_tools = [
        name for name in ("execute_code", "run_shell") if name in active_tools
    ]
    sandbox_wired = bool(sandbox.get("isolated") and sandbox_tools)
    capabilities.append({
        "id": "execution_sandbox", "title": "安全执行环境",
        "description": "代码与 Shell 只在受限文件系统、进程、资源和默认断网边界内运行。",
        "state": sandbox_state,
        "reason": str(sandbox.get("detail") or ""),
        "enabled": True,
        "wired": sandbox_wired,
        "healthy": bool(sandbox.get("isolated")),
        "requires_desktop": False,
        "active_tools": sandbox_tools,
        "missing_tools": ([] if sandbox_tools else ["execute_code", "run_shell"]),
        "runtime": {
            "backend": str(sandbox.get("backend") or "unavailable"),
            "isolated": bool(sandbox.get("isolated")),
            "network_default": str(sandbox.get("network_default") or "deny"),
        },
        "surfaces": {"chat": "tool_boundary", "desktop": "status", "app": "status"},
        "entrypoints": ["Chat 代码执行", "Chat Shell", "运行与治理"],
        "tests": ["tests/test_v377_sandbox_broker.py"],
    })

    graph_ready = False
    graph_tool_ready = "kg_query" in active_tools
    graph_reason = "知识图谱暂无可扩展证据"
    try:
        from hashmm.kg.storage import KGStorage
        from hashmm.retrieval.graph_engineering import enabled
        stats = KGStorage().get_stats()
        graph_ready = bool(enabled() and int(stats.get("entities", 0) or 0) > 0)
        graph_reason = (
            f"已基于 {int(stats.get('entities', 0) or 0)} 个实体做有界证据扩展"
            if graph_ready else "等待知识图谱数据；不会凭空生成关系"
        )
    except Exception:
        pass
    graph_contract_ready = bool(graph_ready and graph_tool_ready)
    if graph_ready and not graph_tool_ready:
        graph_reason = "Knowledge graph data exists, but kg_query is not mounted in Chat"
    capabilities.append({
        "id": "graph_rag", "title": "图关系检索",
        "description": "只沿已持久化的实体关系扩展原始片段，并重新执行访问控制。",
        "state": "ready" if graph_contract_ready else (
            "unavailable" if graph_ready and not graph_tool_ready else "setup_required"
        ), "reason": graph_reason,
        "enabled": True, "wired": graph_contract_ready, "requires_desktop": False,
        "active_tools": ["kg_query"] if graph_tool_ready else [],
        "missing_tools": [] if graph_tool_ready else ["kg_query"],
        "surfaces": {"chat": "retrieval", "desktop": "knowledge_graph", "app": "knowledge_graph"},
        "entrypoints": ["Chat RAG", "知识图谱"],
        "tests": ["tests/test_v367_graph_engineering.py"],
    })

    for capability in capabilities:
        _attach_truth_contract(capability)
    from hashmm.agent.capability_manifest import attach_manifests
    capability_graph = attach_manifests(capabilities)

    ready = sum(1 for item in capabilities if item["production_ready"])
    snapshot: dict[str, Any] = {
        "contract": "hashmm.runtime-capabilities.v1",
        "truth_contract": "hashmm.capability-truth.v1",
        "manifest_contract": capability_graph["schema"],
        "capability_graph": capability_graph,
        "chat_tool_count": len(active_tools),
        # Keep both numbers so clients can explain a partial installation
        # instead of presenting a schema count as if it were executable.
        "chat_declared_tool_count": len(tool_inventory["declared"]),
        "chat_effective_tool_count": len(tool_inventory["effective"]),
        "chat_missing_executors": tool_inventory["missing_executors"],
        "chat_tool_inventory": tool_inventory,
        "ready_count": ready,
        "available_count": sum(
            1 for item in capabilities if item["availability"] == "available"
        ),
        "degraded_count": sum(
            1 for item in capabilities if item["availability"] == "degraded"
        ),
        "unavailable_count": sum(
            1 for item in capabilities if item["availability"] == "unavailable"
        ),
        "total_count": len(capabilities),
        "capabilities": capabilities,
    }
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot["revision"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return snapshot
