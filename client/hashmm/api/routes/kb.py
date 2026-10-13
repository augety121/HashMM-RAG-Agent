"""Knowledge Base routes — document management and advanced search.

Routes:
- GET  /api/kb/documents     — list all indexed documents
- POST /api/kb/search        — advanced search with filters
- GET  /api/kb/stats         — knowledge base statistics (still in server.py)
- POST /api/kb/upload        — upload documents (still in server.py)
"""
from __future__ import annotations
import json
import asyncio
from fastapi import APIRouter, HTTPException, Request  # V308 修 F821：HTTPException 被 raise 却未 import
from hashmm.utils import get_logger
from hashmm.api import doc_validity as dv
from hashmm.api import database as db
from hashmm.api.auth import require_admin, require_auth
from hashmm.access_control import (
    ACLConfigurationError,
    allowed_source_ids,
    filter_principal_documents,
    load_default_acl,
)

logger = get_logger("hashmm.api.routes.kb")

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


def _document_scope(user: dict, items):
    try:
        acl = load_default_acl()
    except ACLConfigurationError as exc:
        raise HTTPException(status_code=503, detail="知识库访问策略不可用") from exc
    return filter_principal_documents(
        items,
        principal=str(user.get("uid") or ""),
        is_admin=user.get("role") == "admin",
        acl=acl,
    ), acl


def _graph_source_scope(user: dict, pipeline, acl) -> set[str] | None:
    corpus = list(getattr(getattr(pipeline, "bm25_index", None), "_corpus", []) or [])
    return allowed_source_ids(
        corpus,
        principal=str(user.get("uid") or ""),
        is_admin=user.get("role") == "admin",
        acl=acl,
    )


def _filter_graph_rows(rows, visible_source_ids: set[str] | None) -> list:
    if visible_source_ids is None:
        return list(rows or [])
    visible = set(visible_source_ids)
    scoped = []
    for row in rows or []:
        data = row if isinstance(row, dict) else getattr(row, "__dict__", {})
        sources = {str(item) for item in (data.get("source_ids") or [])}
        if sources.intersection(visible):
            scoped.append(row)
    return scoped


@router.get("/documents")
async def list_kb_documents(request: Request):
    """List all indexed documents with chunk counts + validity status."""
    user = require_auth(request)
    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if pipe:
            documents, _ = _document_scope(user, pipe.list_documents())
            return {"documents": dv.annotate(documents)}
        return {"documents": []}
    except HTTPException:
        raise
    except Exception as e:
        return {"documents": [], "error": str(e)}


@router.post("/search")
async def kb_search_api(request: Request):
    """Advanced knowledge base search with filters.

    Body: {"query": "...", "top_k": 5, "filename": "...", "page_range": [1, 50]}
    """
    user = require_auth(request)
    body = await request.json()
    query = body.get("query", "")
    top_k = body.get("top_k", 5)
    filters = {}
    if "filename" in body:
        filters["filename"] = body["filename"]
    if "page_range" in body:
        filters["page_range"] = tuple(body["page_range"])
    if "doc_id" in body:
        filters["doc_id"] = body["doc_id"]

    if not query.strip():
        return {"results": [], "error": "Empty query"}

    try:
        from hashmm.retriever_bridge import get_pipeline
        pipe = get_pipeline()
        if not pipe:
            return {"results": [], "error": "No retrieval pipeline"}

        _, acl = _document_scope(user, [])
        if user.get("role") != "admin" and acl is None:
            filters["owner_id"] = str(user.get("uid") or "")

        # V103.1: 检索丢线程跑，避免大库搜索时短暂卡住事件循环（与文件解析同样处理）
        response = await asyncio.to_thread(
            pipe.search, query, top_k=top_k, filters=filters if filters else None)
        scoped_results, _ = _document_scope(user, response.results)
        results = []
        for r in dv.filter_results(scoped_results):  # 时效性：排除失效/归档文档
            results.append({
                "text": r.text[:500],
                "filename": r.filename,
                "page": r.page,
                "section": r.section,
                "score": round(r.score, 4),
            })
        return {
            "results": results,
            "total_candidates": response.total_candidates,
            "elapsed_ms": response.elapsed_ms,
        }
    except Exception as e:
        return {"results": [], "error": str(e)}


@router.post("/query/data",
             summary="检索调试 — 只返回检索结果，不调 LLM",
             description="返回结构化的 entities/relations/chunks 数据，用于调试检索质量",
             tags=["Query"])
async def query_data(request: Request):
    """Return structured retrieval data without LLM generation.
    
    Useful for debugging retrieval quality, evaluating KG coverage,
    and building custom frontends.
    """
    user = require_auth(request)
    body = await request.json()
    query = body.get("query", "").strip()
    mode = body.get("mode", "mix")
    top_k = body.get("top_k", 5)

    if not query or len(query) < 2:
        return {"status": "error", "message": "Query too short"}

    result = {
        "status": "success", "query": query, "mode": mode,
        "entities": [], "relations": [], "chunks": [],
        "metadata": {},
    }

    from hashmm.retriever_bridge import get_pipeline
    pipeline = get_pipeline()
    _, acl = _document_scope(user, [])
    graph_source_ids = _graph_source_scope(user, pipeline, acl) if pipeline else (
        None if user.get("role") == "admin" else set()
    )

    # KG retrieval
    kg_ents, kg_rels = [], []
    if mode in ("kg", "mix"):
        try:
            from hashmm.kg.kg_retriever import get_kg_retriever
            from hashmm.kg.keyword_extractor import extract_keywords
            kw = extract_keywords(query)
            kg_ret = get_kg_retriever()
            if kg_ret.is_available:
                kg_result = await asyncio.to_thread(
                    kg_ret.search,
                    query, mode=mode,
                    hl_keywords=kw.get("hl") or None,
                    ll_keywords=kw.get("ll") or None,
                    top_k_entities=top_k, top_k_relations=top_k,
                )
                kg_ents = _filter_graph_rows(kg_result.entities, graph_source_ids)
                kg_rels = _filter_graph_rows(kg_result.relations, graph_source_ids)
                result["entities"] = kg_ents
                result["relations"] = kg_rels
                result["metadata"]["kg_elapsed_ms"] = kg_result.elapsed_ms
                result["metadata"]["keywords"] = kw
                result["metadata"]["kg_chunk_ids"] = kg_result.chunk_ids[:20]
        except Exception as e:
            result["metadata"]["kg_error"] = str(e)[:200]

    # Vector + BM25 retrieval
    if mode in ("naive", "mix"):
        try:
            if pipeline:
                import time
                t0 = time.time()
                filters = None
                if user.get("role") != "admin" and acl is None:
                    filters = {"owner_id": str(user.get("uid") or "")}
                response = await asyncio.to_thread(
                    pipeline.search, query, top_k=top_k, filters=filters,
                )
                scoped_results, _ = _document_scope(user, response.results)
                elapsed = round((time.time() - t0) * 1000)
                result["chunks"] = [
                    {
                        "text": r.text[:500], "filename": r.filename,
                        "page": r.page, "section": r.section,
                        "score": round(r.score, 4), "doc_id": r.doc_id,
                    }
                    for r in dv.filter_results(scoped_results)  # 时效性过滤
                ]
                result["metadata"]["retrieval_elapsed_ms"] = elapsed
                result["metadata"]["total_candidates"] = response.total_candidates
        except Exception as e:
            result["metadata"]["retrieval_error"] = str(e)[:200]

    return result


# ═══════════════════════════════════════════════════════════════════
# 文档时效性 / 失效区 / 归档（管理端，写审计 + 历史追溯）
# ═══════════════════════════════════════════════════════════════════

@router.get("/validity/list",
            summary="按状态列出文档时效（active/expired/archived/全部）—— 失效区视图")
async def kb_validity_list(request: Request, status: str = ""):
    """失效区/归档区视图：status=expired 看失效，archived 看归档，留空看全部。"""
    require_admin(request)
    return {"status": status or "all", "items": db.list_doc_validity(status)}


@router.get("/validity/detail",
            summary="单篇文档的时效详情 + 变更历史（审计追溯）")
async def kb_validity_detail(request: Request, filename: str):
    require_admin(request)
    if not filename:
        return {"error": "filename required"}
    return dv.detail(filename)


@router.post("/validity/set",
             summary="设置/更新某文档有效期（生效日/到期日）")
async def kb_validity_set(request: Request):
    """Body: {filename, doc_id?, effective_date?, expiry_date?, note?}
    日期支持 'YYYY-MM-DD' 或 epoch 秒；省略表示不限。"""
    admin = require_admin(request)
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    if not filename:
        return {"error": "filename required"}
    eff = dv.parse_date(body.get("effective_date"))
    exp = dv.parse_date(body.get("expiry_date"))
    if eff is not None and exp is not None and exp < eff:
        return {"error": "到期日不能早于生效日"}
    res = dv.set_validity(
        filename, doc_id=body.get("doc_id", ""), effective_date=eff, expiry_date=exp,
        note=body.get("note", ""), actor=admin.get("sub", "admin"),
    )
    return {"ok": True, **res}


@router.post("/validity/archive", summary="归档文档（移入归档区，检索排除）")
async def kb_validity_archive(request: Request):
    admin = require_admin(request)
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    if not filename:
        return {"error": "filename required"}
    dv.archive(filename, actor=admin.get("sub", "admin"), note=body.get("note", ""))
    return {"ok": True, "filename": filename, "status": dv.ARCHIVED}


@router.post("/validity/restore", summary="恢复文档（按有效期重新判定）")
async def kb_validity_restore(request: Request):
    admin = require_admin(request)
    body = await request.json()
    filename = (body.get("filename") or "").strip()
    if not filename:
        return {"error": "filename required"}
    dv.restore(filename, actor=admin.get("sub", "admin"), note=body.get("note", ""))
    return {"ok": True, "filename": filename, "status": dv.detail(filename)["status"]}


@router.post("/validity/sweep", summary="扫描并把已过期文档移入失效区（可定时调用）")
async def kb_validity_sweep(request: Request):
    admin = require_admin(request)
    return {"ok": True, **dv.sweep(actor=admin.get("sub", "system"))}


# ── V205 P1-6：入库质量隔离区（防坏）——低分文档人工复核 ──

@router.get("/quarantine", summary="隔离区列表（低质量文档待复核）")
async def kb_quarantine_list(request: Request):
    require_admin(request)
    from hashmm.pipeline import quarantine as _q
    return {"items": _q.list_items(200), **_q.stats()}


@router.post("/quarantine/approve", summary="放行：跳过质量闸重新入库")
async def kb_quarantine_approve(request: Request):
    admin = require_admin(request)
    body = await request.json()
    qid = str(body.get("qid", "")).strip()
    from hashmm.pipeline import quarantine as _q
    it = _q.get_item(qid)
    if not it:
        raise HTTPException(404, "隔离记录不存在")
    import asyncio
    from pathlib import Path as _P
    from hashmm.pipeline.ingest import IngestPipeline
    staged = _P(it["staged_path"])
    if not staged.is_file():
        _q.remove(qid, delete_file=False)
        raise HTTPException(410, "暂存文件已丢失，记录已清除")
    pipeline = IngestPipeline()
    await asyncio.to_thread(pipeline.load_kg)
    result = await asyncio.to_thread(pipeline.ingest_file, staged, True, None, None, True)
    if result.status in ("success", "partial", "duplicate"):
        _q.remove(qid, delete_file=True)
        try:
            from hashmm.retriever_bridge import init_retriever
            await asyncio.to_thread(init_retriever, True)
        except Exception:
            pass
    db.audit(admin["uid"], admin["sub"], "quarantine_approve", it["filename"])
    return {"ok": result.status in ("success", "partial", "duplicate"), "result": result.to_dict()}


@router.post("/quarantine/reject", summary="拒绝：删除暂存文件与记录")
async def kb_quarantine_reject(request: Request):
    admin = require_admin(request)
    body = await request.json()
    qid = str(body.get("qid", "")).strip()
    from hashmm.pipeline import quarantine as _q
    it = _q.get_item(qid)
    if not it:
        raise HTTPException(404, "隔离记录不存在")
    _q.remove(qid, delete_file=True)
    db.audit(admin["uid"], admin["sub"], "quarantine_reject", it["filename"])
    return {"ok": True}
