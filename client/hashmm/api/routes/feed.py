"""动态流聚合（V218）—— App「动态」页的后端。

背景：App 动态页此前只有 token 用量可拉——不是 App 没做，是后端没有一个
"动态"端点可喂。本端点把散在各处的动态素材一次聚齐：

  GET /api/feed  →  {
    generated_at,            # 服务器时间戳（秒）
    release,                 # 后端代码版本（V218+），App 可据此提示"后端待升级"
    usage,                   # token/成本类摘要（从 app_state.metrics 白名单抽取，可能为空）
    recent_conversations,    # 最近会话（id/title/updated，≤8）
    recent_files,            # 最近产物文件（跨这些会话合并按 mtime 倒序，≤10，含 download_url）
    runners,                 # 派活 runner 心跳（在线/最近心跳）
    scheduled,               # 定时任务及其最近一次结果（≤6，仅管理员可见明细）
    episodes,                # 经验回放最近条目（≤5，自我进化的"系统学到了什么"）
  }

设计纪律（与本仓库其余路由一致）：
- require_auth 起步；scheduled 明细额外要求 admin（复用 /api/admin/scheduled 的权限口径）。
- 每一块独立 try/except 降级为空——单块故障绝不拖垮整个动态页。
- 只调用仓库里已存在、已被其他路由使用过的函数（database.list_conversations /
  list_conversation_files、agent.dispatch.runners_status、hashmm.scheduler.list_tasks、
  evolution episodes 的同款 SQL）——零新表、零新依赖。
"""
from __future__ import annotations

import hashlib
import json
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from hashmm.api import app_state
from hashmm.api import database as db
from hashmm.api.auth import require_auth
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.feed")

router = APIRouter(prefix="/api", tags=["feed"])

_USAGE_KEY_HINTS = ("token", "cost", "today", "total_requests", "llm")


def _feed_etag(payload: dict) -> str:
    """Return a stable validator for the meaningful feed state.

    ``generated_at`` is deliberately excluded: otherwise every request would
    produce a new validator even when no user-visible data changed.
    """
    validator = {k: v for k, v in payload.items() if k != "generated_at"}
    raw = json.dumps(
        validator, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return '"' + hashlib.sha256(raw).hexdigest() + '"'


def _usage_snapshot() -> dict:
    """从全局 metrics 里白名单抽取用量类字段（形状随部署而异，全部软取）。"""
    try:
        raw = app_state.metrics.to_dict() if app_state.metrics else {}
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items()
                if any(h in str(k).lower() for h in _USAGE_KEY_HINTS)
                and isinstance(v, (int, float, str))}
    except Exception as e:
        log_suppressed(logger, e)
        return {}


def _recent_user_runs(convs: list[dict], limit: int = 20) -> list[dict]:
    """Build a run timeline from persisted assistant run manifests.

    A run belongs to the Chat that produced it. Reading manifests through the
    already owner-scoped conversation list keeps App and desktop on one source
    of truth and avoids exposing the server-wide JSONL trace to an ordinary user.
    """
    rows: list[dict] = []
    for conv in convs:
        conv_id = str(conv.get("id") or "")
        if not conv_id:
            continue
        try:
            messages = db.get_messages(conv_id, limit=200) or []
        except Exception as e:
            log_suppressed(logger, e)
            continue
        last_user = ""
        for message in messages:
            if message.get("role") == "user":
                last_user = str(message.get("content") or "").strip()
                continue
            if message.get("role") != "assistant":
                continue
            manifest = message.get("run_manifest") or {}
            if not isinstance(manifest, dict) or not manifest.get("run_id"):
                continue
            termination = manifest.get("termination") or {}
            latency = manifest.get("stage_latency_ms") or {}
            tokens = manifest.get("tokens") or {}
            verification = manifest.get("verification") or {}
            stop_reason = str(termination.get("reason") or "complete")
            status = "failed" if verification.get("status") == "failed" else "complete"
            rows.append({
                "conv_id": conv_id,
                "conv_title": conv.get("title") or "对话",
                "message_id": message.get("id") or "",
                "run_id": manifest.get("run_id") or "",
                "title": (last_user or conv.get("title") or "Agent 运行")[:120],
                "status": status,
                "ts": message.get("created_at") or 0,
                "task_type": manifest.get("task_type") or "",
                "execution_mode": manifest.get("execution_mode") or "",
                "model": manifest.get("model") or "",
                "elapsed_ms": latency.get("total") or 0,
                "stop_reason": stop_reason,
                "iterations": termination.get("iterations") or 0,
                "tokens": (tokens.get("input") or 0) + (tokens.get("output") or 0),
                "failed_checks": verification.get("failed_checks") or [],
            })
    rows.sort(key=lambda row: float(row.get("ts") or 0), reverse=True)
    return rows[:max(1, min(limit, 50))]


@router.get("/feed", summary="动态流聚合（App 动态页一次拉齐）")
async def feed(request: Request):
    user = require_auth(request)
    uid = user.get("uid", "")
    is_admin = (user.get("role") == "admin")

    out: dict = {
        "generated_at": int(time.time()),
        "usage": _usage_snapshot(),
        "recent_conversations": [],
        "recent_files": [],
        "runners": [],
        "scheduled": [],
        "episodes": [],
        "runs": [],
    }
    try:
        from hashmm import RELEASE
        out["release"] = RELEASE
    except Exception:
        out["release"] = ""

    # ── 最近会话 + 跨会话最近产物 ──
    try:
        convs = db.list_conversations(uid, limit=20) or []
        out["recent_conversations"] = [
            {"id": c.get("id"), "title": c.get("title") or "对话",
             "updated_at": c.get("updated_at") or c.get("created_at")}
            for c in convs[:8]
        ]
        files: list[dict] = []
        for c in convs[:8]:
            cid = c.get("id") or ""
            if not cid:
                continue
            try:
                for f in (db.list_conversation_files(cid) or []):
                    files.append({
                        "conv_id": cid,
                        "conv_title": c.get("title") or "对话",
                        "filename": f.get("filename"),
                        "size": f.get("size", 0),
                        "mtime": f.get("mtime") or f.get("created_at") or 0,
                        "download_url": f"/api/conversations/{cid}/download/{f.get('filename')}",
                    })
            except Exception as e:
                log_suppressed(logger, e)
        files.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
        out["recent_files"] = files[:10]
    except Exception as e:
        log_suppressed(logger, e)

    # ── runner 心跳（与 /api/dispatch/runners 同源） ──
    try:
        from hashmm.agent import dispatch as dq
        out["runners"] = dq.runners_status(
            owner=str(user.get("uid") or ""),
            created_by=str(user.get("sub") or ""),
        )
    except Exception as e:
        log_suppressed(logger, e)

    # ── 定时任务最近结果（明细仅管理员；普通用户给数量概览，不泄露任务内容） ──
    try:
        from hashmm import scheduler
        tasks = scheduler.list_tasks() or []
        if is_admin:
            def _lr(t: dict):
                return t.get("last_run") or t.get("last_run_at") or 0
            tasks = sorted(tasks, key=_lr, reverse=True)[:6]
            out["scheduled"] = [
                {"id": t.get("id"), "name": t.get("name") or t.get("action") or "任务",
                 "enabled": bool(t.get("enabled", True)),
                 "last_run": t.get("last_run") or t.get("last_run_at"),
                 "last_result": (str(t.get("last_result") or t.get("last_status") or ""))[:200]}
                for t in tasks
            ]
        else:
            out["scheduled"] = [{"count": len(tasks)}]
    except Exception as e:
        log_suppressed(logger, e)

    # ── 运行轨迹：当前用户 Chat 的真实 run manifest（普通用户也能看自己的） ──
    try:
        out["runs"] = _recent_user_runs(convs, limit=20)
    except Exception as e:
        log_suppressed(logger, e)

    # ── 经验回放（与 /api/evolution/episodes 同款 SQL，取最近 5 条摘要） ──
    try:
        from hashmm.evolution.episodic_memory import get_episodic_memory
        mem = get_episodic_memory()
        mem._ensure_db()
        with mem._db._conn() as c:
            rows = c.execute(
                "SELECT * FROM episodes WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (uid, 5),
            ).fetchall()
        eps = []
        for r in rows:
            d = dict(r)
            eps.append({
                "id": d.get("id"),
                "created_at": d.get("created_at"),
                "summary": (str(d.get("summary") or d.get("task") or ""))[:160],
            })
        out["episodes"] = eps
    except Exception as e:
        log_suppressed(logger, e)
        out["episodes"] = []

    etag = _feed_etag(out)
    cache_headers = {"ETag": etag, "Cache-Control": "private, max-age=15, must-revalidate"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)
    return JSONResponse(out, headers=cache_headers)


@router.get("/runs/mine", summary="当前账号 Chat 的持久化运行轨迹")
async def my_runs(request: Request, limit: int = 50):
    """Return durable run manifests owned by the authenticated user.

    Unlike the legacy admin JSONL trace, these records survive UI navigation,
    belong to a specific Chat, and are safe for both App and desktop users.
    """
    user = require_auth(request)
    uid = str(user.get("uid") or "")
    convs = db.list_conversations(uid, limit=50, archived=-1) or []
    runs = _recent_user_runs(convs, limit=limit)
    return {
        "source": "message_run_manifest",
        "enabled": True,
        "runs": runs,
        "count": len(runs),
    }
