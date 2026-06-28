"""HashMM-RAG v1.0 — Multi-model + Auth + Permissions + Knowledge Base Isolation.

New in v1.0 (over v0.9):
  1. User authentication (register/login/JWT tokens)
  2. Role-based access control (admin/user/viewer)
  3. Multi-model management (add/switch/test models via UI)
  4. Knowledge base isolation
  5. Audit logging for all operations
  6. Model config: .env as default, DB + UI can override
"""
from __future__ import annotations
import asyncio, json, time, uuid, os, re, math, threading
from collections import defaultdict, Counter
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import numpy as np

from hashmm.api import database as db
from hashmm.api.auth import (
    create_token, get_current_user, require_auth, require_admin,
    require_conv_access, require_conv_access_or_create,
)
from hashmm.api.retrieval import (
    _build_keyword_index, detect_followup, detect_continuation,
    skill_hash_search, skill_rerank, skill_keyword_search,
    hook_dedup, hook_relevance, hook_rewrite, compress_chunks,
    multi_hop_search, detect_truncation, decompose_query, validate_answer,
    SYS, THINK_INST, BASE_INST, SCORE_THRESHOLD, RERANK_TOP,
)
from hashmm.api.intent_engine import (
    classify_intent, extract_topics, load_skills, match_skills, get_skill_prompt,
)
from hashmm.api.tool_functions import (
    agent_plan, tool_code_generate, tool_file_create,
    tool_create_pptx, tool_create_zip, tool_execute_code,
)
from hashmm.utils import get_logger, log_suppressed
logger = get_logger("hashmm.server")
from hashmm.api import model_manager
from hashmm.api.middleware import setup_logging, TraceMiddleware, RateLimitMiddleware, BodySizeLimitMiddleware, SecurityHeadersMiddleware, log_request
from hashmm.api.intent import classify_rules, classify_fallback, parse_llm_intent, intent_to_task_type, ProjectContext, INTENT_SYSTEM
from hashmm.api.tool_registry import (
    get_tool_definitions, execute_tool as _exec_tool, execute_tool, _EXECUTORS,
    register_executor, FILES_DIR, UPLOAD_DIR,
)

# ── Agent Loop config ──
USE_AGENT_LOOP = True          # Set False to fall back to v8.0 pipeline
MAX_AGENT_ITERATIONS = 12      # Max tool-call rounds per request
MAX_TOOL_RESULT_CHARS = 10000  # Truncate long tool results (was 6000)

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════
SCORE_THRESHOLD = 0.72
RERANK_TOP = 5
CACHE_THRESHOLD = 0.92
CACHE_MAX = 200
PERSIST_DIR = Path("data/agent_state")
PERSIST_INTERVAL = 60

# ═══════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════
# STATE — lazy references populated by ServiceRegistry during lifespan.
# Class definitions are in hashmm/api/core/state.py.
# ═══════════════════════════════════════════════════════════════════
from hashmm.api.core.state import Metrics, SemanticCache, PersistentMemory

# These are set to ServiceRegistry's instances in lifespan.
# NOT instantiated here — avoids double initialization.
metrics: Metrics | None = None
sem_cache: SemanticCache | None = None
memory: PersistentMemory | None = None


# ═══════════════════════════════════════════════════════════════════
# Thin wrappers — delegate to ServiceRegistry, keep backward compat
# ═══════════════════════════════════════════════════════════════════

_state: dict = {}
_llm_fn = None
_active_model_info = None


def _load():
    """Ensure all services are initialized (delegates to ServiceRegistry)."""
    global _llm_fn, _active_model_info, _state, memory, metrics, sem_cache
    from hashmm.api.core.services import ServiceRegistry
    ServiceRegistry.ensure_loaded()
    _state = ServiceRegistry.state
    _llm_fn = ServiceRegistry.llm_fn
    _active_model_info = ServiceRegistry.llm_info
    if not memory:
        memory = ServiceRegistry.memory
    if not metrics:
        metrics = ServiceRegistry.metrics
    if not sem_cache:
        sem_cache = ServiceRegistry.sem_cache


def _reload_llm():
    """Reload LLM from model manager."""
    global _llm_fn, _active_model_info
    from hashmm.api.core.services import ServiceRegistry
    ServiceRegistry.reload_llm()
    _llm_fn = ServiceRegistry.llm_fn
    _active_model_info = ServiceRegistry.llm_info


def _llm(prompt: str) -> str:
    """Call LLM with circuit breaker protection."""
    from hashmm.api.core.services import ServiceRegistry
    return ServiceRegistry.call_llm(prompt)

from hashmm.api.core.safety import check_safety, sanitize_output
from hashmm.api.core.llm_router import LLMRouter, get_llm_params

_llm_router = LLMRouter()


def generate(query, results, history, sys_key, profile_ctx="", file_ctx=""):
    """Generate answer using structured messages API.

    Separates system prompt, retrieval context, history, and user query
    into proper message roles for better LLM understanding.
    """
    if not _llm_fn:
        model = db.get_default_model()
        if model:
            raise RuntimeError(f"LLM 已配置（{model.get('model_name')}）但连接失败，请在管理后台检查 API Key 和 Base URL")
        raise RuntimeError("LLM 未配置，请在管理后台添加模型")

    # Build structured messages
    system_content = SYS.get(sys_key, SYS["kb"])
    if profile_ctx:
        system_content += "\n" + profile_ctx

    messages = [{"role": "system", "content": system_content}]

    # 长对话：保留最近 RECENT_TURNS 轮原文；更早的历史不再直接丢弃，而是压缩成「前文回顾」
    # 注入为一条 system 消息——这样几十轮的长对话也能保持线索连贯，而不是过 10 轮就失忆。
    RECENT_TURNS = 10
    if len(history) > RECENT_TURNS:
        older = history[:-RECENT_TURNS]
        recap_lines = []
        for h in older:
            role = "用户" if h["role"] == "user" else "助手"
            txt = (h.get("content") or "").strip().replace("\n", " ")
            if txt:
                recap_lines.append(f"{role}：{txt[:120]}")
        # 控制总长度：从最近的更早历史往回保留 ~1800 字（离当前越近越相关）
        kept, total = [], 0
        for line in reversed(recap_lines):
            if total + len(line) > 1800:
                break
            kept.append(line)
            total += len(line)
        recap = "\n".join(reversed(kept))
        if recap:
            messages.append({
                "role": "system",
                "content": "【对话前文回顾】（较早对话的要点，用于保持上下文连贯，不必逐条引用）：\n" + recap,
            })

    # History as proper message turns（最近 RECENT_TURNS 轮原文，截断放宽到 1500 字/条，多轮跟得住前文）
    for h in history[-RECENT_TURNS:]:
        role = "user" if h["role"] == "user" else "assistant"
        messages.append({"role": role, "content": h["content"][:1500]})

    # Retrieval context as separate system message (isolated from user input)
    if results:
        ctx_parts = []
        for i, r in enumerate(results[:5], 1):
            text = r.get("text", "").strip()
            if text:
                source = r.get("source", r.get("filename", ""))
                page = r.get("page", -1)
                page_str = f" p.{page}" if page and page > 0 else ""
                ctx_parts.append(f"[{i}] {source}{page_str}\n{text}")

        if ctx_parts:
            retrieval_injection = (
                "以下是从知识库中检索到的相关内容。请基于这些内容回答用户问题，"
                "引用时使用 [1][2] 等标记。如果检索结果不足以完整回答，"
                "结合自身知识补充，但标明哪些来自知识库。\n\n"
                + "\n\n".join(ctx_parts)
            )
            messages.append({"role": "system", "content": retrieval_injection})

    # User query (with optional file context)
    user_content = query
    if file_ctx:
        user_content = f"用户上传文件内容：\n{file_ctx[:5000]}\n\n{query}"
    messages.append({"role": "user", "content": user_content})

    metrics.total_llm_calls += 1
    try:
        if hasattr(_llm_fn, 'chat'):
            return _llm_fn.chat(messages)
        else:
            # Fallback: concatenate to single prompt for legacy LLM functions
            flat = "\n\n".join(m["content"] for m in messages)
            return _llm_fn(flat)
    except Exception as e:
        err = str(e) or repr(e)
        raise RuntimeError(f"LLM 调用失败（{err[:150]}）") from e

def evaluate_answer(query, answer):
    if not _llm_fn: return 5
    try:
        r = _llm(f"Rate 1-5 (number only):\nQ: {query}\nA: {answer[:300]}\nScore:")
        return int(re.search(r'[1-5]', r.strip()).group())
    except Exception: return 4


# ═══════════════════════════════════════════════════════════════════
# AGENT PIPELINE
# ═══════════════════════════════════════════════════════════════════
def agent_run(query: str, session_id: str, file_context: str = "") -> dict:
    """v5.0 Agent Pipeline: query → intent → retrieval → generate → check."""
    _load()
    history = memory.get_history(session_id)
    trace = []; sources = []; t0 = time.time(); quality = 0; skills_used = []

    # Auto-fill query when only files are provided
    if not query.strip() and file_context:
        query = "请分析上传的文件内容"

    safe, reason = check_safety(query)
    if not safe:
        trace.append({"node": "safety", "detail": f"⚠️ {reason}"})
        return {"answer": f"⚠️ {reason}", "sources": [], "trace": trace,
                "elapsed_ms": 0, "intent": "blocked", "rewritten_query": None}

    query = " ".join(query.split())[:8000]

    expanded = detect_followup(query, history)
    if expanded:
        trace.append({"node": "understand", "detail": f"展开：{expanded[:60]}"})
        query = expanded

    topics = extract_topics(query)
    intent = classify_intent(query)
    memory.update_profile(session_id, query, intent, topics)
    profile_ctx = memory.get_profile_context(session_id)
    trace.append({"node": "classify", "detail": f"{intent} · {','.join(topics[:3]) or '—'}"})

    rewritten = query

    # Semantic Cache Check
    q_emb = None
    try:
        if _state.get("text_enc"):
            import torch
            with torch.no_grad():
                q_emb = _state["text_enc"]([rewritten]).cpu().numpy()
            cached = sem_cache.lookup(q_emb)
            if cached and intent not in ("chitchat",):
                trace.append({"node": "cache", "detail": f"命中缓存（原问：{cached['query'][:40]}）"})
                elapsed = (time.time() - t0) * 1000
                trace.append({"node": "done", "detail": f"{elapsed:.0f}ms (cached)"})
                metrics.record(elapsed, intent, ["cache"], cache_hit=True)
                return {"answer": cached["answer"], "sources": cached["sources"],
                        "trace": trace, "elapsed_ms": round(elapsed,2),
                        "intent": intent, "rewritten_query": rewritten if rewritten != query else None}
    except Exception as _e:
        log_suppressed(logger, _e)

    # v5.0: Unified retrieval using ChatRetrieval (replaces old hash search)
    try:
        fc = file_context[:5000] if file_context else ""
        if file_context:
            answer = generate(query, [], history, "file", profile_ctx, file_ctx=fc)
            skills_used.append("file_qa")
            trace.append({"node": "file_qa", "detail": f"文件上下文 {len(fc)} 字"})
        elif intent == "chitchat":
            answer = generate(query, [], history, "chat", profile_ctx)
            skills_used.append("llm_direct")
        else:
            # v5.0: Always try retrieval for non-chitchat queries
            from hashmm.chat_retrieval import get_chat_retrieval
            _chat_rag = get_chat_retrieval()

            if _chat_rag.should_search(query):
                # Run retrieval pipeline
                enhanced_msgs, rag_sources, rag_strategy = _chat_rag.enhance(
                    query, [{"role": h["role"], "content": h["content"]} for h in history[-6:]]
                )
                if rag_sources:
                    # Convert to agent_run source format
                    sources = []
                    for s in rag_sources:
                        sources.append({
                            "rank": s["id"],
                            "chunk_id": "", "doc_id": "",
                            "modality": "text",
                            "text": s.get("text", "")[:600],
                            "score": s.get("score", 0),
                            "method": "vector+bm25",
                            "source": s.get("filename", ""),
                            "filename": s.get("filename", ""),
                            "page": s.get("page", -1),
                        })
                    skills_used.append("retrieval")
                    trace.append({"node": "skill:retrieval",
                                  "detail": f"检索 {len(sources)} 条"})

            # Generate answer
            if sources:
                sys_key = "compare" if intent == "compare" else "kb"
                answer = generate(query, sources, history, sys_key, profile_ctx)
                trace.append({"node": "generate", "detail": f"基于 {len(sources)} 条来源"})

                # v5.0: Answer quality check
                try:
                    from hashmm.answer_checker import AnswerChecker
                    checker = AnswerChecker()
                    chunks = [s["text"] for s in sources if s.get("text")]
                    report = checker.check(answer, chunks, rag_sources if rag_sources else None)
                    quality = 5 if report.confidence == "high" else 3 if report.confidence == "medium" else 2
                    if report.ungrounded_numbers:
                        trace.append({"node": "check", "detail":
                                      f"⚠️ 未验证数字: {', '.join(report.ungrounded_numbers[:3])}"})
                    else:
                        trace.append({"node": "check", "detail": f"质量: {report.confidence}"})
                except Exception:
                    quality = evaluate_answer(query, answer)
                    trace.append({"node": "evaluate", "detail": f"质量: {quality}/5"})
            else:
                answer = generate(query, [], history, "open", profile_ctx)
                skills_used.append("llm_direct")
                trace.append({"node": "generate", "detail": "LLM 直接回答（无检索结果）"})

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        err_msg = str(e) or repr(e) or type(e).__name__
        tb_lines = [l.strip() for l in tb.strip().split('\n') if l.strip() and not l.strip().startswith('Traceback')]
        location = tb_lines[-2] if len(tb_lines) >= 2 else ""
        full_err = f"{err_msg}\n{location}" if location else err_msg
        logger.error(f"[Agent Error]\n{tb}")
        answer = f"处理出错：{err_msg}"
        trace.append({"node": "error", "detail": full_err[:300]})

    answer = sanitize_output(answer)
    elapsed = (time.time() - t0) * 1000
    trace.append({"node": "done", "detail": f"{elapsed:.0f}ms"})

    if q_emb is not None and intent not in ("chitchat",) and len(answer) > 50:
        sem_cache.store(q_emb, query, answer, sources)

    memory.log_episode(query=query[:100], intent=intent, skills=skills_used,
                        n_sources=len(sources), elapsed_ms=round(elapsed), quality=quality)
    metrics.record(elapsed, intent, skills_used)

    return {"answer": answer, "sources": sources, "trace": trace,
            "elapsed_ms": round(elapsed, 2), "intent": intent,
            "rewritten_query": rewritten if rewritten != query else None}


def _auto_select_retrieval_mode(query: str) -> tuple[str, str]:
    """Automatically select the best retrieval mode for a query.

    Returns (mode, reason) — the reason is human-readable and surfaced in the
    frontend trace ("explainable retrieval"). Delegates the core naive/kg/mix
    decision to the single, tested router in ``hashmm.retrieval.query_router``
    (Phase 48), adding only the modes the router doesn't model: ``global``
    (community-level overview) and the explicit code-task short-circuit.
    """
    q = query.lower()

    code_words = ["代码", "实现", "写一个", "code", "implement", "function"]
    if any(w in q for w in code_words):
        return "naive", "代码任务 → 词法召回"

    global_words = ["趋势", "整体", "概况", "行业", "总体", "全面", "总结所有"]
    if any(w in q for w in global_words):
        return "global", "概览/趋势 → 社区级全局检索"

    try:
        from hashmm.retrieval.query_router import route_query
        decision = route_query(query, default="mix")
        logger.info(f"[Router] {query[:40]} → {decision.mode} ({decision.reason})")
        return decision.mode, decision.reason
    except Exception:
        return "mix", ""


def _sanitize_for_json(obj):
    """Recursively convert numpy types to Python native types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if hasattr(obj, 'item'):  # numpy scalar (int64, float32, etc.)
        return obj.item()
    if isinstance(obj, float) and (obj != obj):  # NaN
        return 0
    return obj

# ═══════════════════════════════════════════════════════════════════
# FASTAPI
# ═══════════════════════════════════════════════════════════════════
# v29: Request schemas extracted to schemas.py
from hashmm.api.schemas import (
    ChatRequest, LoginRequest, RegisterRequest, ModelCreateRequest,
    ModelUpdateRequest, KBRequest, UserUpdateRequest, PasswordChangeRequest,
)


@asynccontextmanager
async def lifespan(app):
    db.init_db()

    # v12: Two-phase startup — Phase 1 is fast (<0.5s), Phase 2 runs in background
    from hashmm.api.core.services import ServiceRegistry
    ServiceRegistry.init_fast()  # DB + config only — server immediately available

    # Populate module-level references
    global memory, metrics, sem_cache
    memory = ServiceRegistry.memory
    metrics = ServiceRegistry.metrics
    sem_cache = ServiceRegistry.sem_cache

    # Wire up legacy app_state
    from hashmm.api import app_state as _as
    _as._load_fn = _load
    _as._reload_llm_fn = _reload_llm
    _as.load_skills_fn = load_skills

    # Phase 2: Heavy init in background thread (models, indexes, LLM)
    async def _background_init():
        await asyncio.to_thread(ServiceRegistry.init_heavy)
        # Re-sync after heavy init
        from hashmm.api import app_state as _as2
        _as2._load_fn = _load
        _as2._reload_llm_fn = _reload_llm
        # v15 Phase 5: security hardening checks
        try:
            from hashmm.api.security import run_startup_security_check
            run_startup_security_check()
        except RuntimeError:
            raise
        except Exception as _sec_e:
            logger.debug(f"security check skipped: {_sec_e}")
        # V91: 离线开箱预置（模板/技能仅在空库种入；HASHMM_NO_SEED=1 关闭）
        try:
            from hashmm.api.seeds import seed_builtin_content
            _seeded = seed_builtin_content(log=logger.info)
            if _seeded["templates"] or _seeded["skills"]:
                logger.info(f"[Server] Seeded builtin content: {_seeded}")
        except Exception as _seed_e:
            logger.debug(f"seed skipped: {_seed_e}")
        logger.info("[Server] All services ready.")

        # V108 零配置：把后端公网地址写入 Supabase app_config，供 App 自动读取。
        # 读 HASHMM_PUBLIC_URL（用户启动时设一次），App 登录后零配置连上。
        try:
            import os as _os
            from hashmm.api import supabase_sync as _ss
            _purl = _os.environ.get("HASHMM_PUBLIC_URL", "").strip()
            if _purl and _ss.enabled():
                _ss.push_backend_url(_purl)
                logger.info(f"[Server] 已上报后端公网地址到 Supabase（App 零配置）：{_purl}")
        except Exception as _pu_e:
            logger.debug(f"push backend_url skipped: {_pu_e}")

        # Proactive services: start the scheduler loop (no-op unless
        # HASHMM_SCHEDULER=1). Started after services are ready so actions can use
        # the corpus/KG.
        try:
            from hashmm import scheduler
            if scheduler.start_scheduler():
                logger.info("[Server] Scheduler loop started (proactive services).")
        except Exception as _sch_e:
            logger.debug(f"scheduler not started: {_sch_e}")

        # V105: 微信 iLink 渠道 worker（no-op，除非 HASHMM_WECHAT_ENABLE=1 且已扫码登录有会话）。
        # 服务就绪后启动，使后端重启后已登录的微信渠道自动恢复长轮询。
        try:
            from hashmm.api.routes.channels import start_wechat_worker
            if start_wechat_worker():
                logger.info("[Server] 微信 iLink worker 已恢复（长轮询）。")
        except Exception as _wx_e:
            logger.debug(f"wechat worker not started: {_wx_e}")

    asyncio.create_task(_background_init())
    logger.info("[Server] Phase 1 ready — accepting requests. Models loading in background...")

    yield
    ServiceRegistry.shutdown()

app = FastAPI(title="HashMM-RAG", version="13.0.0", lifespan=lifespan)
# v15: Structured logging
setup_logging("INFO")

# v15: Request tracing + rate limiting
app.add_middleware(TraceMiddleware)
app.add_middleware(SecurityHeadersMiddleware)  # v17 Phase 66: baseline security headers
app.add_middleware(RateLimitMiddleware, max_per_minute=60, max_per_hour=600)
# Body-size guard: added last so it runs FIRST (rejects over-large payloads
# before they are buffered/parsed). Tunable via HASHMM_MAX_BODY_MB.
app.add_middleware(BodySizeLimitMiddleware)

# v27: Secure CORS — configurable via environment
import os as _os
_CORS_ORIGINS = _os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:6006").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

# v29: Include extracted route modules
from hashmm.api.routes import all_routers as _routers
for _r in _routers:
    app.include_router(_r)

# v12: Global exception handler — user-friendly messages, no stack traces
from fastapi.responses import JSONResponse as _JSONResponse

_ERROR_MESSAGES = {
    400: "请求参数有误",
    401: "未登录或令牌已过期",
    403: "权限不足",
    404: "资源不存在",
    429: "请求过于频繁，请稍后再试",
    500: "服务内部错误，请稍后重试",
    503: "服务暂时不可用",
}

@app.exception_handler(Exception)
async def _global_error_handler(request, exc):
    status = getattr(exc, "status_code", 500)
    request_id = getattr(request.state, "request_id", "")
    # Log full error internally (with request_id for correlation)
    if status >= 500:
        logger.error(f"[{request_id}] [{request.method} {request.url.path}] "
                     f"{type(exc).__name__}: {exc}", exc_info=True)
    # Return friendly message to user (never expose internals)
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and len(detail) < 200 and "Traceback" not in detail:
        msg = detail  # HTTPException with clean detail
    else:
        msg = _ERROR_MESSAGES.get(status, "服务异常，请稍后重试")
    return _JSONResponse(
        status_code=status,
        content={"error": True, "code": status, "message": msg, "request_id": request_id},
    )

# ── Static files ──
# Try frontend-next/out first (Next.js build), then frontend/ (vanilla HTML)
_fe = None
for p in [Path("frontend-next/out"), Path("frontend"),
          Path(__file__).parent.parent.parent / "frontend-next" / "out",
          Path(__file__).parent.parent.parent / "frontend"]:
    if (p / "index.html").exists():
        _fe = p; break

if _fe and ((_fe / "_next").exists() or len(list(_fe.glob("*.js"))) > 2):
    # Next.js static export — mount all static assets
    app.mount("/static", StaticFiles(directory=str(_fe)), name="static")

@app.get("/")
async def root():
    if _fe: return FileResponse(_fe / "index.html")
    return {"msg": "HashMM-RAG v1.0.0 — No frontend found"}


# v29: Auth routes moved to routes/auth.py

# 客户端错误遥测（V103.42 · 方案 P3 遥测打通）：前端 ErrorBoundary 崩溃时会 POST 到这里。
# 之前没有这个端点，上报全部 404 进黑洞——现在落到日志 + 可选 jsonl，便于发现前端面板崩溃。
@app.post("/api/client-errors", include_in_schema=False)
async def client_errors(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    err = str(body.get("error", ""))[:300]
    comp = str(body.get("component", ""))[:300]
    url = str(body.get("url", ""))[:200]
    logger.warning(f"[client-error] {err} | url={url} | component={comp}")
    try:
        import json as _json
        from pathlib import Path as _Path
        _d = _Path("logs")
        _d.mkdir(exist_ok=True)
        with open(_d / "client_errors.jsonl", "a", encoding="utf-8") as _f:
            _f.write(_json.dumps({
                "error": err, "stack": str(body.get("stack", ""))[:800],
                "component": comp, "url": url, "ts": body.get("ts"),
            }, ensure_ascii=False) + "\n")
    except Exception as _e:
        log_suppressed(logger, _e)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════
# CHAT (auth optional — degrade gracefully for unauthenticated)
# ═══════════════════════════════════════════════════════════════════
@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    username = user["sub"] if user else "anonymous"

    sid = req.session_id or str(uuid.uuid4())
    memory.get_or_create_session(sid, req.message, user_id)
    memory.add_message(sid, "user", req.message)

    # 桌面文件投送：App 发的聊天走的就是这个 /api/chat（不是 conv_stream）。
    # 识别"把电脑/客户端上的某文件发我"→确保该对话在后端存在（App 的对话可能只在 Supabase，
    # 先 INSERT OR IGNORE 建好，后续上传/下载/回写消息才走得通）→写投送请求→立即回执，不跑 RAG。
    if _is_desktop_file_request(req.message):
        try:
            db.create_conversation(sid, user_id, title=(req.message[:20] or "文件"))
            db.create_message(sid, "user", req.message)
            from hashmm.api import supabase_sync
            supabase_sync.push_file_request(user_id, sid, req.message)
            ack = "好的，正在从你电脑里找这个文件并发送过来，稍等几秒…（需要你电脑上的 HashMM 客户端开着、并登录同一账号）"
        except Exception as e:
            ack = f"想从你电脑取这个文件，但投送通道暂时不可用（{e}）。请确认电脑客户端在线后再试。"
        memory.add_message(sid, "assistant", ack)
        try:
            db.create_message(sid, "assistant", ack)
        except Exception:
            pass
        db.audit(user_id, username, "chat", f"file-request: {req.message[:80]}")
        return _sanitize_for_json({
            "answer": ack, "session_id": sid, "sources": [], "trace": [],
            "elapsed_ms": 0, "intent": "desktop_file", "rewritten_query": None})

    result = agent_run(req.message, sid, file_context=req.file_context or "")
    memory.add_message(sid, "assistant", result["answer"])

    db.audit(user_id, username, "chat", f"Q: {req.message[:80]}")

    return _sanitize_for_json({
        "answer": result["answer"], "session_id": sid, "sources": result["sources"],
        "trace": result["trace"], "elapsed_ms": result["elapsed_ms"],
        "intent": result["intent"], "rewritten_query": result.get("rewritten_query")})


@app.post("/api/chat/agentic")
async def chat_agentic(req: ChatRequest, request: Request):
    """v17 Phase 105: agentic-RAG endpoint — query routing → KG-augmented retrieval
    → CRAG self-correction → (optional subagents / evaluator-optimizer) → answer,
    with an observable trace. Additive: never touches /api/chat. On any failure it
    falls back to the normal pipeline so callers always get an answer."""
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    username = user["sub"] if user else "anonymous"
    sid = req.session_id or str(uuid.uuid4())
    memory.get_or_create_session(sid, req.message, user_id)
    memory.add_message(sid, "user", req.message)
    t0 = time.time()

    def _to_dicts(results):
        out = []
        for i, r in enumerate((results or [])[:8], 1):
            out.append({
                "id": i,
                "text": (r.get("text", "") if isinstance(r, dict) else getattr(r, "text", "")) or "",
                "source": (r.get("filename", "") if isinstance(r, dict) else getattr(r, "filename", "")) or "",
                "filename": (r.get("filename", "") if isinstance(r, dict) else getattr(r, "filename", "")) or "",
                "page": (r.get("page", -1) if isinstance(r, dict) else getattr(r, "page", -1)),
                "score": (r.get("score", 0) if isinstance(r, dict) else getattr(r, "score", 0)),
            })
        return out

    try:
        from hashmm.agent.agentic_chat import run_agentic_chat
        from hashmm.chat_retrieval import get_chat_retrieval
        pipe = getattr(get_chat_retrieval(), "_pipeline", None)
        history = memory.get_history(sid)

        def search_fn(q):
            if pipe is None:
                return []
            try:
                return pipe.search(q, top_k=8).results
            except Exception as e:
                log_suppressed(logger, e)
                return []

        def generate_fn(q, results, feedback=None):
            qq = q if not feedback else f"{q}\n\n（请修正以下问题后重答：{feedback}）"
            return generate(qq, _to_dicts(results), history, "kb", "")

        def synth_fn(q, reports):
            merged = []
            for rep in (reports or []):
                merged += rep.get("results", [])
            return generate(q, _to_dicts(merged), history, "kb", "")

        # v17 Phase 106: optionally route the cheap loop calls (decompose / CRAG
        # rewrite / subagent distillation) to the local Qwen for free; final
        # generation/synthesis stays on the paid chat model. Default OFF → loops
        # use _llm_fn and worker_fn stays None (unchanged behaviour).
        from hashmm.agent.local_routing import resolve_loop_routing
        _routing = resolve_loop_routing(_llm_fn)
        out = run_agentic_chat(req.message, search_fn, generate_fn,
                               llm_fn=_routing["llm_fn"], worker_fn=_routing["worker_fn"],
                               synth_fn=synth_fn)
        answer = sanitize_output(out.get("answer", "") or "")
        memory.add_message(sid, "assistant", answer)
        db.audit(user_id, username, "chat_agentic", f"Q: {req.message[:80]}")
        return _sanitize_for_json({
            "answer": answer, "session_id": sid,
            "sources": _to_dicts(out.get("sources", [])),
            "trace": out.get("trace", []), "intent": out.get("intent"),
            "effort": out.get("effort"), "corrected": out.get("corrected"),
            "elapsed_ms": round((time.time() - t0) * 1000), "mode": "agentic"})
    except Exception as e:
        log_suppressed(logger, e)
        # graceful fallback to the standard pipeline
        result = agent_run(req.message, sid, file_context=req.file_context or "")
        memory.add_message(sid, "assistant", result["answer"])
        return _sanitize_for_json({
            "answer": result["answer"], "session_id": sid, "sources": result["sources"],
            "trace": result.get("trace", []), "intent": result.get("intent"),
            "elapsed_ms": result.get("elapsed_ms"), "mode": "agentic-fallback"})


@app.post("/api/chat/stream", include_in_schema=False)
async def chat_stream(req: ChatRequest, request: Request):
    """App 端聊天走的就是这个流式接口（POST，session_id=对话id）。

    以前这里是个废弃的 308 跳转：App 的流式请求要么失败、要么被重定向到 conv_id="new"，
    导致 ① 文件投送意图识别不到（落在错误的对话）② App 发的消息没落到统一会话表，
    于是「App 与桌面历史不一致」。现在改为真正处理：
      确保该对话在后端存在（App 的对话可能只在 Supabase）→ 落库用户消息（自动同步 Supabase）
      → 文件投送意图则写投送请求+回执；否则跑 agent → 以 SSE 返回答案 + 落库助手消息。
    因为落库会同步 Supabase、桌面端读 SQLite 也就能看到，App 与桌面历史从此走同一份数据。
    """
    from fastapi.responses import StreamingResponse
    from hashmm.api.streaming import _sse
    from starlette.concurrency import run_in_threadpool

    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    username = user["sub"] if user else "anonymous"
    sid = req.session_id or str(uuid.uuid4())

    # 确保对话存在于后端 SQLite（idempotent），并落库用户消息（自动同步 Supabase）
    try:
        db.create_conversation(sid, user_id, title=(req.message[:20] or "对话"))
    except Exception:
        pass
    try:
        db.create_message(sid, "user", req.message)
    except Exception:
        pass

    # 手机取照片意图（"发我手机里最近一张照片"）→ 路由给手机 App
    if _is_phone_photo_request(req.message):
        try:
            from hashmm.api import supabase_sync as _sbs
            _sbs.push_file_request(user_id, sid, req.message, target="phone")
        except Exception:
            pass
        ackp = "好的，正在请求你的手机发送最近的一张照片——请保持手机上的 HashMM 在前台、允许相册权限，确认后照片会出现在这里。"
        try:
            db.create_message(sid, "assistant", ackp)
        except Exception:
            pass
        db.audit(user_id, username, "chat", f"phone-photo-request: {req.message[:80]}")

        async def _ack_stream_phone2():
            yield _sse("token", {"content": ackp})
            yield _sse("done", {"sources": [], "trace": [], "steps": [], "elapsed_ms": 0, "session_id": sid})
        return StreamingResponse(_ack_stream_phone2(), media_type="text/event-stream")

    # 桌面文件投送意图（"把电脑/客户端上的某文件发我"）
    if _is_desktop_file_request(req.message):
        try:
            from hashmm.api import supabase_sync as _sbs
            _sbs.push_file_request(user_id, sid, req.message)
        except Exception:
            pass
        ack = "好的，正在从你电脑里找这个文件并发送过来，稍等几秒…（需要电脑上的 HashMM 客户端开着、并登录同一账号）"
        try:
            db.create_message(sid, "assistant", ack)
        except Exception:
            pass
        db.audit(user_id, username, "chat", f"file-request: {req.message[:80]}")

        async def _ack_stream():
            yield _sse("token", {"content": ack})
            yield _sse("done", {"sources": [], "trace": [], "steps": [], "elapsed_ms": 0, "session_id": sid})
        return StreamingResponse(_ack_stream(), media_type="text/event-stream")

    # 普通问答：跑 agent（放线程池避免阻塞事件循环），整段答案作为一个 token 发回
    memory.get_or_create_session(sid, req.message, user_id)
    memory.add_message(sid, "user", req.message)
    result = await run_in_threadpool(agent_run, req.message, sid, req.file_context or "")
    answer = result.get("answer", "")
    memory.add_message(sid, "assistant", answer)
    try:
        db.create_message(sid, "assistant", answer, sources=result.get("sources") or None)
    except Exception:
        pass
    db.audit(user_id, username, "chat", f"Q: {req.message[:80]}")

    async def _answer_stream():
        if answer:
            yield _sse("token", {"content": answer})
        yield _sse("done", {
            "sources": result.get("sources", []), "trace": result.get("trace", []),
            "steps": [], "elapsed_ms": result.get("elapsed_ms", 0), "session_id": sid,
        })
    return StreamingResponse(_answer_stream(), media_type="text/event-stream")


@app.get("/api/corpus/stats")
async def stats():
    # Try to load index, but don't crash if it fails
    try:
        _load()
    except Exception as e:
        # Index not available — return partial stats from DB
        default_model = db.get_default_model()
        model_name = default_model.get("model_name", "—") if default_model else "—"
        if default_model and not _llm_fn:
            _reload_llm()
        return {"total_chunks": 0, "hash_bits": 0, "index_size_kb": 0,
                "modalities": {}, "llm_ready": _llm_fn is not None,
                "cache_size": 0, "episodes": 0,
                "active_model": model_name,
                "index_error": str(e)[:100]}

    s = _state
    mods = {}
    for e in s["metadata"]:
        m = e.get("modality", "?"); mods[m] = mods.get(m, 0) + 1
    p = s["cfg"].hash_index_path
    # v5.0: Use new retrieval pipeline stats instead of old hash index
    total_chunks = 0
    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if pipe and pipe.vector_index:
            total_chunks = pipe.vector_index.total_vectors
    except Exception as _e:
        log_suppressed(logger, _e)

    # Fallback: old hash index (if still loaded)
    if total_chunks == 0 and "faiss_index" in s:
        total_chunks = s["faiss_index"].ntotal

    hash_bits = s.get("bits", 0)
    p = s["cfg"].hash_index_path
    default_model = db.get_default_model()
    model_name = default_model.get("model_name", "—") if default_model else "—"
    llm_ok = _llm_fn is not None
    if default_model and not llm_ok:
        _reload_llm()
        llm_ok = _llm_fn is not None

    # v14: database + cache observability
    try:
        from hashmm.api import db_backend
        db_info = {"backend": db_backend.backend_name(),
                   "pool_size": db._POOL_SIZE}
    except Exception:
        db_info = {"backend": "sqlite", "pool_size": 0}
    try:
        from hashmm.agent.cache import get_cache
        cache_info = get_cache().stats()
    except Exception:
        cache_info = {}

    return {"total_chunks": total_chunks, "hash_bits": hash_bits,
            "index_size_kb": round(p.stat().st_size / 1024, 1) if p.exists() else 0,
            "modalities": mods, "llm_ready": llm_ok,
            "cache_size": len(sem_cache.entries), "episodes": len(memory.episodes),
            "active_model": model_name,
            "db": db_info, "cache": cache_info}

# ═══════════════════════════════════════════════════════════════════
# SESSIONS
# ═══════════════════════════════════════════════════════════════════
# v7.0: Session routes → routes/conversations.py
# v7.0: Upload/files/workspace routes → routes/files.py
# v7.0: File extractors (_extract_pdf, etc.) → routes/files.py


class TitleRequest(BaseModel):
    query: str
    answer: str = ""

# ═══════════════════════════════════════════════════════════════════
# CONVERSATION SEARCH (search across all sessions)

# ═══════════════════════════════════════════════════════════════════
# v10.0: NEW SSE ENDPOINT using Handlers (parallel to old /api/chat/stream)
# ═══════════════════════════════════════════════════════════════════

class ConvChatRequest(BaseModel):
    message: str
    file_context: str | None = None
    custom_prompt: str | None = None
    doc_filter: list[str] | None = None  # v7.0: filter retrieval to specific documents
    retrieval_mode: str = "mix"  # v11: "naive" | "kg" | "mix" | "global"
    answer_style: str | None = None  # v10.0: "factual" | "analytical" | "creative"
    # V86: 问答栏附件（已通过 /api/upload 落盘的文件名，截屏按钮/图片附件走这里）。
    # 图片类附件在配置了 HASHMM_VISION_* 时会做「定向图像理解」前置步骤。
    attachments: list[str] | None = None

_INTENT_SYSTEM = (
    "你是意图分类器。判断用户这条消息属于哪一类，只输出一行 JSON、不要解释。\n"
    "类别：\n"
    "- fetch_desktop_file：用户想把【自己电脑/桌面/客户端】上的某个【已有文件】发给自己（取文件，不是提问、不是让你新做一个文件）。\n"
    "- fetch_phone_photo：用户想把【自己手机/相册】里的照片发过来。\n"
    "- normal：其它一切（提问、分析、闲聊、让你新建/生成文件 都算 normal）。\n"
    'JSON 格式：{"intent":"fetch_desktop_file|fetch_phone_photo|normal","filename":"<提到的文件名或关键词，没有则空>"}'
)


def _classify_intent_llm(q: str) -> dict | None:
    """用模型判断意图（取桌面文件 / 取手机照片 / 普通），返回 {"intent","filename"} 或 None。
    这是把脆弱的关键字开关换成"模型判断"的核心：模型本就能理解无穷种说法，无需穷举关键词；
    且能区分"把我电脑里的报告发我"(取文件) 与"帮我做个报告"(新建)——正则很难分清。"""
    if not _llm_fn or not q:
        return None
    try:
        raw = _llm_fn(f"{_INTENT_SYSTEM}\n\n用户消息：{q.strip()}\n\nJSON：")
        import json as _json
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            return None
        d = _json.loads(m.group(0))
        intent = str(d.get("intent", "")).strip()
        if intent not in ("fetch_desktop_file", "fetch_phone_photo", "normal"):
            return None
        return {"intent": intent, "filename": str(d.get("filename", "")).strip()}
    except Exception:
        return None


def _intent_prefilter(q: str) -> bool:
    """便宜的粗筛：完全没有任何"取/发/文件/照片/电脑/手机"信号、或明显是长篇提问的消息，
    直接判定不是取文件——避免给每条普通消息都调一次模型。只做粗筛，真正判断交给模型。
    注意：这不是关键字决策，只是省钱的门，列表不必穷举（剩下的由模型理解）。"""
    if not q:
        return False
    s = q.strip().lower()
    if len(s) > 80:                 # 取文件指令一般很短；长句基本是提问/任务
        return False
    sig = ("文件", "文档", "表格", "图片", "照片", "相片", "截图", "压缩包", "安装包", "报告",
           "ppt", "word", "excel", "pdf", "发", "传", "给我", "拿", "取", "电脑", "桌面",
           "客户端", "本机", "手机", "相册", ".doc", ".xls", ".ppt", ".pdf", ".png", ".jpg", ".zip", ".csv")
    return any(k in s for k in sig)


def _is_desktop_file_request(q: str) -> bool:
    """是否"把我电脑/桌面/客户端上的某文件发给我"。模型优先判断，模型不可用时退回关键字（零回归）。"""
    if not _intent_prefilter(q):
        return False
    d = _classify_intent_llm(q)
    if d is not None:
        return d.get("intent") == "fetch_desktop_file"
    return _is_desktop_file_request_regex(q)


def _is_phone_photo_request(q: str) -> bool:
    """是否"把我手机相册里的照片发过来"。模型优先判断，模型不可用时退回关键字（零回归）。"""
    if not _intent_prefilter(q):
        return False
    d = _classify_intent_llm(q)
    if d is not None:
        return d.get("intent") == "fetch_phone_photo"
    return _is_phone_photo_request_regex(q)


def _is_desktop_file_request_regex(q: str) -> bool:
    """关键字兜底（模型不可用时用）。判断用户消息是不是"把我电脑/客户端/桌面上的某文件发给我"。"""
    if not q:
        return False
    s = q.strip()
    if len(s) > 60:           # 文件投送指令一般很短；长句多半是提问
        return False
    # 明显的提问/分析意图 → 不是文件投送
    if any(k in s for k in ("什么", "怎么", "为什么", "如何", "讲讲", "讲解", "分析", "总结",
                            "解释", "介绍", "区别", "对比", "概述", "?", "？")):
        return False
    loc = any(k in s for k in ("客户端", "电脑", "桌面", "本机", "电脑上", "桌面上", "电脑里", "电脑里面"))
    send = any(k in s for k in (
        "给我", "发我", "发给我", "给我发", "发给", "发到", "发送", "传给我", "传我",
        "传过来", "发过来", "我要", "拿给我", "取给我", "丢给我", "甩给我",
    ))
    has_file = any(k in s for k in (
        "文件", "文档", "表格", "图片", "照片", "压缩包", "安装包",
        "ppt", "PPT", "word", "Word", "excel", "Excel", "pdf", "PDF",
        ".doc", ".xls", ".ppt", ".pdf", ".png", ".jpg", ".jpeg", ".zip", ".txt", ".csv", ".md",
    ))
    # 触发：①位置词(客户端/电脑/桌面)+发送词（如"电脑上的报告发我"）；
    #       ②含"文件/文档…"+(发送词或位置词)（如"给我111文件""客户端上面的文件"）
    return (loc and send) or (has_file and (send or loc))


def _is_phone_photo_request_regex(q: str) -> bool:
    """关键字兜底（模型不可用时用）。判断是不是"把我手机相册里的照片发过来"。"""
    if not q:
        return False
    s = q.strip()
    if len(s) > 60:
        return False
    if any(k in s for k in ("什么", "怎么", "为什么", "如何", "讲讲", "讲解", "分析", "总结",
                            "解释", "介绍", "区别", "对比", "概述", "?", "？")):
        return False
    on_phone = any(k in s for k in ("手机", "相册", "手机上", "手机里", "手机相册", "手机里面"))
    if not on_phone:
        return False
    photo = any(k in s for k in ("照片", "图片", "相片", "截图")) or ("最近" in s and "张" in s)
    return on_phone and photo


@app.post("/api/conversations/{conv_id}/stream")
async def conv_stream(conv_id: str, req: ConvChatRequest, request: Request):
    """v10.0 SSE streaming — async generator, non-blocking for concurrent users."""
    from fastapi.responses import StreamingResponse
    from hashmm.api.streaming import generate_sse_async

    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    username = user["sub"] if user else "anonymous"

    query = req.message.strip()
    file_context = req.file_context or ""
    custom_prompt = req.custom_prompt or ""
    doc_filter = req.doc_filter or []
    retrieval_mode = req.retrieval_mode if req.retrieval_mode in ("naive", "kg", "mix", "global", "auto") else "auto"
    answer_style = req.answer_style if req.answer_style in ("factual", "analytical", "creative") else None

    # v12: Auto retrieval mode — select based on query characteristics
    _route_reason = ""
    if retrieval_mode == "auto":
        retrieval_mode, _route_reason = _auto_select_retrieval_mode(query)

    if not query and file_context:
        query = "请分析上传的文件内容"

    # v17 Phase 65: object-level authorization for the main write endpoint.
    # Existing conversation → caller must be the owner (or an admin); a new
    # conv_id → created owned by the caller. Closes the cross-user write hole
    # where anyone knowing a conv_id could inject messages / run the agent in
    # someone else's conversation.
    require_conv_access_or_create(request, conv_id, default_title=query)

    # V86: 问答栏附件（截屏/图片）——元数据随用户消息持久化，刷新后图片卡仍在。
    # download_url 一律由服务端按文件名重建（不信任客户端 URL）。
    from urllib.parse import quote as _urlquote
    from hashmm.agent import vision as _vision
    _att_names: list[str] = []
    for _n in (req.attachments or [])[:8]:
        if isinstance(_n, str):
            _safe = Path(_n).name
            if _safe and _safe == _n and _safe not in _att_names:
                _att_names.append(_safe)
    _att_meta = [{"filename": n, "download_url": f"/api/files/{_urlquote(n)}"} for n in _att_names]
    db.create_message(conv_id, "user", query, files=_att_meta or None)

    # ── 手机取照片：用户在电脑端说"发我手机里最近的一张照片" → 路由给手机 App 执行 ──
    if _is_phone_photo_request(query):
        try:
            from hashmm.api import supabase_sync as _sbs
            _sbs.push_file_request(user_id, conv_id, query, target="phone")
        except Exception:
            pass
        _ackp = "好的，正在请求你的手机发送最近的一张照片——请打开手机上的 HashMM 并停留在前台、允许相册权限，确认后照片会出现在这里。"
        try:
            db.create_message(conv_id, "assistant", _ackp)
        except Exception:
            pass
        from fastapi.responses import StreamingResponse as _SRp
        from hashmm.api.streaming import _sse as _sse_evtp

        async def _ack_stream_phone():
            yield _sse_evtp("token", {"content": _ackp})
            yield _sse_evtp("done", {"sources": [], "trace": [], "steps": [], "elapsed_ms": 0, "session_id": conv_id})

        return _SRp(_ack_stream_phone(), media_type="text/event-stream")

    # ── 桌面文件投送：用户在 App/网页聊天里说"把电脑/桌面的某文件发我" ──
    # 写一条 file_request 到 Supabase，桌面客户端常驻轮询会找到该文件并上传到本对话
    # （走云端 Supabase/AutoDL，不是局域网）。本轮不跑 RAG，直接回执。
    if _is_desktop_file_request(query):
        try:
            from hashmm.api import supabase_sync as _sbs
            _sbs.push_file_request(user_id, conv_id, query)
        except Exception:
            pass
        _ack = "好的，正在从你的电脑里查找并发送这个文件——请确保桌面客户端正在运行。找到后会直接出现在本对话里，你可以在这儿查看/下载。"
        try:
            db.create_message(conv_id, "assistant", _ack)
        except Exception:
            pass
        from fastapi.responses import StreamingResponse as _SR
        from hashmm.api.streaming import _sse as _sse_evt2

        async def _ack_stream():
            yield _sse_evt2("token", {"content": _ack})
            yield _sse_evt2("done", {"sources": [], "trace": [], "steps": [], "elapsed_ms": 0, "session_id": conv_id})

        return _SR(_ack_stream(), media_type="text/event-stream")

    # V86: 图像理解前置步骤——带图提问时先发「vision」trace，定向分析结果注入上下文，
    # 主链 generate_sse_async 签名与内部逻辑零改动（分析失败/未配置则按原样纯文字走）。
    _vision_images = _vision.sanitize_image_names(_att_names)

    async def _stream_with_vision():
        from hashmm.api.streaming import _sse as _sse_evt
        from starlette.concurrency import run_in_threadpool
        fc = file_context
        if _vision_images:
            try:
                block, detail = await run_in_threadpool(_vision.analyze_for_chat, _vision_images, query)
            except Exception as _e:  # analyze_for_chat 自身永不抛错，这里是最后保险
                block, detail = "", f"图像理解异常（{_e}），本轮按文字回答"
            if detail:
                yield _sse_evt("trace", {"steps": [{"node": "vision", "detail": detail}]})
            if block:
                fc = (block + "\n\n" + fc) if fc else block
        inner = generate_sse_async(
            conv_id=conv_id,
            query=query,
            user_id=user_id,
            username=username,
            file_context=fc,
            custom_prompt=custom_prompt,
            doc_filter=doc_filter,
            retrieval_mode=retrieval_mode,
            route_reason=_route_reason,
            answer_style=answer_style,
            load_fn=_load,
            llm_fn=_llm_fn,
            classify_rules_fn=classify_rules,
            classify_fallback_fn=classify_fallback,
            parse_llm_intent_fn=parse_llm_intent,
            intent_to_task_type_fn=intent_to_task_type,
            intent_system=INTENT_SYSTEM,
            sys_prompts=SYS,
            request=request,
        )
        async for chunk in inner:
            yield chunk

    return StreamingResponse(_stream_with_vision(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})



# v12: Health check
# v12: Admin dashboard data
# ═══════════════════════════════════════════════════════════════════
# v13: Workspace APIs (CC-level project management)

# ═══════════════════════════════════════════════════════════════
# v21: Template APIs

# ═══════════════════════════════════════════════════════════════
# v24: Conversation search + message edit + regenerate
# ═══════════════════════════════════════════════════════════════

@app.get("/api/search")
async def search_conversations(q: str = "", request: Request = None):
    """Full-text search across all conversations."""
    if not q or len(q) < 2:
        return {"results": []}
    user = get_current_user(request) if request else None
    user_id = user["uid"] if user else "anonymous"
    with db._conn() as c:
        rows = c.execute("""
            SELECT m.conv_id, m.content, m.role, m.created_at,
                   cv.title
            FROM messages m 
            JOIN conversations cv ON m.conv_id = cv.id
            WHERE m.content LIKE ? AND cv.user_id = ?
            ORDER BY m.created_at DESC LIMIT 20
        """, (f"%{q}%", user_id)).fetchall()
    results = []
    for r in rows:
        content = r["content"]
        # Find matching snippet
        idx = content.lower().find(q.lower())
        start = max(0, idx - 50)
        end = min(len(content), idx + len(q) + 50)
        snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
        results.append({
            "conv_id": r["conv_id"],
            "title": r["title"],
            "role": r["role"],
            "snippet": snippet,
            "created_at": r["created_at"],
            "tags": db.get_tags(r["conv_id"]) if hasattr(db, "get_tags") else [],
        })
    return {"results": results}


# ═══════════════════════════════════════════════════════════════
# 深度检索（Self-RAG）—— 加法端点，绝不触碰 /api/chat 与主聊天 SSE。
# 客户端"深度检索"开关调这个：模型驱动多跳检索 → deepseek 作答 → 自我批判 →
# 不足则自适应再检索 → 仍不足则忠实度门控。Self-RAG 不可用（无 GPU 等）时优雅降级。
class DeepSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    max_hops: int = 3
    max_rounds: int = 2


@app.post("/api/deepsearch")
async def deepsearch(req: DeepSearchRequest, request: Request):
    user = get_current_user(request)
    user_id = user["uid"] if user else "anonymous"
    username = user["sub"] if user else "anonymous"
    q = (req.query or "").strip()
    if not q:
        return _sanitize_for_json({"answer": "", "sources": [], "grounded": False,
                                   "confidence": None, "error": "empty_query"})
    r = None
    try:
        from hashmm.retrieval import self_rag as _sr
        # self_rag_answer 是同步且较慢（多跳 + 自评 + 可能再检索），丢线程池跑，别堵事件循环。
        r = await asyncio.to_thread(_sr.self_rag_answer, q,
                                    top_k=req.top_k, max_hops=req.max_hops,
                                    max_rounds=req.max_rounds)
    except Exception as e:  # noqa: BLE001
        logger.warning("[deepsearch] self_rag 异常：%s", e)
    db.audit(user_id, username, "deepsearch", f"Q: {q[:80]}")
    if r is None:
        return _sanitize_for_json({
            "answer": "深度检索暂不可用（检索策略未就绪，例如本机无 GPU 或模型未加载）。",
            "sources": [], "grounded": False, "confidence": None,
            "rounds": 0, "trace": [], "answer_by": None, "degraded": True})
    return _sanitize_for_json({
        "answer": r.get("answer"),
        "initial_answer": r.get("initial_answer"),
        "sources": r.get("sources") or [],
        "grounded": r.get("grounded"),
        "confidence": r.get("confidence"),
        "retrieval_top_score": r.get("retrieval_top_score"),
        "rounds": r.get("rounds"),
        "critique": r.get("critique"),
        "trace": r.get("trace") or [],
        "answer_by": r.get("answer_by"),
    })


# ═══════════════════════════════════════════════════════════════
# v25: Project System APIs

@app.post("/api/conversations/{conv_id}/execute/stream")
async def stream_execute_code(conv_id: str, request: Request):
    """Execute code with streaming output (SSE)."""
    # v17 Phase 65: must own this conversation (or be admin) — otherwise anyone
    # knowing a conv_id could run code in another user's workspace.
    require_conv_access(request, conv_id)
    body = await request.json()
    code_text = body.get("code", "")
    timeout = min(body.get("timeout", 30), 300)

    import asyncio, sys, tempfile, os

    async def generate():
        # Write code to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code_text)
            tmp_path = f.name

        cwd = str(db.conv_files_dir(conv_id))
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            async def read_stream(stream, label):
                async for line in stream:
                    text = line.decode("utf-8", errors="replace")
                    yield f"data: {json.dumps({'type': label, 'text': text}, ensure_ascii=False)}\n\n"

            # Read stdout
            if proc.stdout:
                async for chunk in read_stream(proc.stdout, "stdout"):
                    yield chunk

            # Wait for completion
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                yield f"data: {json.dumps({'type': 'error', 'text': f'超时 ({timeout}s)'})}\n\n"

            # Read stderr
            if proc.stderr:
                stderr = await proc.stderr.read()
                if stderr:
                    yield f"data: {json.dumps({'type': 'stderr', 'text': stderr.decode('utf-8', errors='replace')[:2000]})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'exit_code': proc.returncode})}\n\n"
        finally:
            os.unlink(tmp_path)

    from starlette.responses import StreamingResponse
    return StreamingResponse(generate(), media_type="text/event-stream")



# ═══════════════════════════════════════════════════════════════
# v25: Usage tracking + health API
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# v26: Knowledge Base PDF Upload
# ═══════════════════════════════════════════════════════════════

@app.post("/api/kb/upload")
async def upload_to_kb(
    file: UploadFile = File(...),
    effective_date: str = Form(None),
    expiry_date: str = Form(None),
):
    """Upload PDF to knowledge base: parse → chunk → vectorize → add to FAISS.

    可选 effective_date / expiry_date（'YYYY-MM-DD' 或 epoch 秒）登记文档有效期；
    到期后文档自动进入失效区并从检索中排除。
    """
    import tempfile, os
    
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "仅支持 PDF 文件")
    
    # Save uploaded file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        # Parse PDF
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader
            except ImportError:
                os.unlink(tmp_path)
                raise HTTPException(500, "PDF 解析库未安装 (pip install pypdf)")
        
        reader = PdfReader(tmp_path)
        full_text = ""
        for page in reader.pages:
            text = page.extract_text() or ""
            full_text += text + "\n"
        
        if len(full_text.strip()) < 50:
            os.unlink(tmp_path)
            raise HTTPException(400, "PDF 文本提取失败（可能是扫描件）")
        
        # Chunk (512 chars, 128 overlap)
        CHUNK_SIZE = 512
        OVERLAP = 128
        chunks = []
        for i in range(0, len(full_text), CHUNK_SIZE - OVERLAP):
            chunk = full_text[i:i + CHUNK_SIZE].strip()
            if len(chunk) > 50:
                chunks.append(chunk)
        
        # Add to knowledge base (if FAISS agent is available)
        added = 0
        source_name = file.filename or "uploaded.pdf"
        
        try:
            # Try to add to the existing FAISS index
            # v5.0: Use EncoderPool (same as retrieval pipeline)
            from hashmm.encoder_pool import EncoderPool
            import numpy as np, faiss
            
            encoder = EncoderPool.get_encoder()
            embeddings = encoder(chunks)
            
            # Load existing index or create new
            index_path = "data/kb_index.faiss"
            metadata_path = "data/kb_metadata.jsonl"
            
            if os.path.exists(index_path):
                index = faiss.read_index(index_path)
            else:
                dim = embeddings.shape[1]
                index = faiss.IndexFlatIP(dim)
            
            # Normalize and add
            faiss.normalize_L2(embeddings)
            index.add(embeddings)
            faiss.write_index(index, index_path)
            
            # Save metadata
            with open(metadata_path, "a") as f:
                for j, chunk in enumerate(chunks):
                    meta = {"source": source_name, "chunk_id": j, "text": chunk[:200], "page": j // 3 + 1}
                    f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            
            added = len(chunks)
        except Exception as e:
            # Fallback: save chunks as text file for manual processing
            chunks_path = f"data/kb_chunks_{source_name}.txt"
            with open(chunks_path, "w") as f:
                for chunk in chunks:
                    f.write(chunk + "\n---\n")
            added = len(chunks)
            return {
                "ok": True, "filename": source_name, "chunks": added,
                "pages": len(reader.pages), "chars": len(full_text),
                "warning": f"已分块保存但未加入向量索引: {str(e)[:100]}"
            }
        
        # 时效性：登记文档有效期（无日期 = active/无限期），失败不影响上传
        try:
            from hashmm.api import doc_validity as _dv
            _dv.register_on_ingest(
                source_name,
                effective_date=_dv.parse_date(effective_date),
                expiry_date=_dv.parse_date(expiry_date),
                actor="upload",
            )
        except Exception:
            pass

        return {
            "ok": True, "filename": source_name, "chunks": added,
            "pages": len(reader.pages), "chars": len(full_text),
        }
    finally:
        os.unlink(tmp_path)

@app.get("/api/kb/stats")
async def kb_stats():
    """Knowledge base statistics."""
    import os
    stats = {"chunks": 0, "documents": 0, "index_size": 0}
    
    metadata_path = "data/kb_metadata.jsonl"
    if os.path.exists(metadata_path):
        with open(metadata_path) as f:
            lines = f.readlines()
            stats["chunks"] = len(lines)
            sources = set()
            for line in lines:
                try:
                    meta = json.loads(line)
                    sources.add(meta.get("source", ""))
                except Exception: pass
            stats["documents"] = len(sources)
    
    index_path = "data/kb_index.faiss"
    if os.path.exists(index_path):
        stats["index_size"] = os.path.getsize(index_path)
    
    return stats

# v5.1: Document Management & Advanced Search → moved to hashmm/api/routes/kb.py

# ═══════════════════════════════════════════════════════════════
# v26: Conversation Tags

@app.get("/api/search/semantic")
async def semantic_search(q: str = "", request: Request = None):
    """Semantic search across conversations using BGE-M3 embeddings."""
    if not q or len(q) < 2:
        return {"results": []}
    
    try:
        # v5.0: Use EncoderPool
        from hashmm.encoder_pool import EncoderPool
        import numpy as np
        
        encoder = EncoderPool.get_encoder()
        query_emb = encoder([q])
        
        # Get recent messages to search through
        user = get_current_user(request) if request else None
        uid = user["uid"] if user else "anonymous"
        
        with db._conn() as c:
            rows = c.execute("""
                SELECT m.conv_id, m.content, m.role, cv.title
                FROM messages m JOIN conversations cv ON m.conv_id = cv.id
                WHERE cv.user_id = ? AND length(m.content) > 20
                ORDER BY m.created_at DESC LIMIT 500
            """, (uid,)).fetchall()
        
        if not rows:
            return {"results": []}
        
        # Encode all messages
        texts = [r["content"][:200] for r in rows]
        msg_embs = encoder.encode(texts)
        
        # Cosine similarity
        from numpy.linalg import norm
        query_norm = query_emb / (norm(query_emb, axis=1, keepdims=True) + 1e-8)
        msg_norm = msg_embs / (norm(msg_embs, axis=1, keepdims=True) + 1e-8)
        scores = (query_norm @ msg_norm.T)[0]
        
        # Top results
        top_indices = scores.argsort()[-10:][::-1]
        results = []
        seen_convs = set()
        for idx in top_indices:
            if scores[idx] < 0.3:
                break
            r = rows[idx]
            if r["conv_id"] in seen_convs:
                continue
            seen_convs.add(r["conv_id"])
            results.append({
                "conv_id": r["conv_id"],
                "title": r["title"],
                "snippet": r["content"][:150],
                "score": round(float(scores[idx]), 3),
            })
        
        return {"results": results[:5]}
    except Exception as e:
        # Fallback to keyword search
        return await search_conversations(q, request)



# ═══════════════════════════════════════════════════════════════
# v28: Admin Dashboard API
# ═══════════════════════════════════════════════════════════════

@app.get("/api/admin/dashboard")
async def admin_dashboard(request: Request):
    """Dashboard data for AdminDashboard component."""
    with db._conn() as c:
        # Message counts
        total_msgs = c.execute("SELECT COUNT(*) as n FROM messages").fetchone()["n"]
        today_msgs = c.execute("SELECT COUNT(*) as n FROM messages WHERE created_at > strftime('%s','now','start of day')").fetchone()["n"]
        total_convs = c.execute("SELECT COUNT(*) as n FROM conversations").fetchone()["n"]
        
        # Token usage today
        today_tokens = c.execute("""
            SELECT COALESCE(SUM(tokens_in),0) as ti, COALESCE(SUM(tokens_out),0) as to_ 
            FROM messages WHERE role='assistant' AND created_at > strftime('%s','now','start of day')
        """).fetchone()
        
        # Task distribution (from conversation titles, rough estimate)
        task_dist = c.execute("""
            SELECT 
                CASE 
                    WHEN title LIKE '%PPT%' OR title LIKE '%ppt%' THEN 'PPT 生成'
                    WHEN title LIKE '%代码%' OR title LIKE '%实现%' OR title LIKE '%写%' THEN '代码开发'
                    WHEN title LIKE '%报告%' OR title LIKE '%文档%' THEN '文档制作'
                    ELSE '知识问答'
                END as detail,
                COUNT(*) as count
            FROM conversations
            WHERE created_at > strftime('%s','now','-7 days')
            GROUP BY detail ORDER BY count DESC
        """).fetchall()
    
    return {
        "messages": {"total": total_msgs, "today": today_msgs},
        "conversations": {"total": total_convs},
        "tokens_today": {
            "input": today_tokens["ti"],
            "output": today_tokens["to_"],
            "cost_cny": round((today_tokens["ti"] * 0.001 + today_tokens["to_"] * 0.002) / 1000, 2)
        },
        "task_distribution": [dict(r) for r in task_dist],
    }



# ═══════════════════════════════════════════════════════════════
# v27: File create from CodeBlock + Image upload

# ═══════════════════════════════════════════════════════════════
# v6.0: Comprehensive Metrics API
# ═══════════════════════════════════════════════════════════════

@app.get("/api/admin/metrics")
async def admin_metrics(request: Request):
    """Comprehensive system metrics for monitoring dashboard."""
    require_admin(request)

    # Runtime metrics
    runtime = metrics.to_dict()

    # Retrieval stats
    retrieval_stats = {}
    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if pipe:
            retrieval_stats = {
                "total_vectors": pipe.vector_index.total_vectors,
                "bm25_docs": pipe.bm25_index.size,
                "reranker_available": pipe._reranker_available or False,
                "documents": pipe.list_documents(),
            }
    except Exception as _e:
        log_suppressed(logger, _e)

    # KG stats
    kg_stats = {}
    try:
        from hashmm.kg.storage import KGStorage
        kg_stats = KGStorage().get_stats()
    except Exception as _e:
        log_suppressed(logger, _e)

    # Database stats
    db_stats = {}
    try:
        with db._conn() as c:
            db_stats = {
                "total_conversations": c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
                "total_messages": c.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                "total_users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "user_memories": c.execute("SELECT COUNT(*) FROM user_memory").fetchone()[0],
            }
    except Exception as _e:
        log_suppressed(logger, _e)

    # v7.0: Embedding cache stats
    embedding_cache = {}
    try:
        from hashmm.encoder_pool import EncoderPool
        embedding_cache = EncoderPool.cache_stats()
    except Exception as _e:
        log_suppressed(logger, _e)

    return {
        "runtime": runtime,
        "retrieval": retrieval_stats,
        "knowledge_graph": kg_stats,
        "database": db_stats,
        "embedding_cache": embedding_cache,
    }


# CATCH-ALL (MUST be last route — serves Next.js SPA for non-API paths)
# ═══════════════════════════════════════════════════════════════════
@app.get("/{path:path}")
async def catch_all(path: str):
    # API 前缀不应落到前端 catch-all：若某 API 路由未匹配（如未启用/拼写错），
    # 返回标准 404 而不是前端 HTML —— 避免 HTML 掩盖 API 问题、便于诊断。
    if path.startswith(("api/", "v1/", "mcp")) or path in ("v1", "mcp"):
        raise HTTPException(404, f"no such API route: /{path}")
    if _fe:
        f = _fe / path
        if f.is_file(): return FileResponse(f)
        fhtml = _fe / f"{path}.html"
        if fhtml.is_file(): return FileResponse(fhtml)
        return FileResponse(_fe / "index.html")
    raise HTTPException(404)
