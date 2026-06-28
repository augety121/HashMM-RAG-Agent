"""KG API routes — knowledge graph management and visualization."""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from hashmm.api.auth import require_admin
from hashmm.kg.storage import KGStorage
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.api.kg")

router = APIRouter(prefix="/api/kg", tags=["knowledge-graph"])

_kg_storage = KGStorage()


@router.get("/stats")
async def kg_stats():
    """Get knowledge graph statistics (public, no auth required)."""
    return _kg_storage.get_stats()


@router.get("/health")
async def kg_health(request: Request):
    """v17 Phase 93: KG quality/health report (admin) — structural metrics plus
    noise/fragment detection. Read-only; never raises (returns {} on failure)."""
    require_admin(request)
    try:
        from hashmm.kg.kg_metrics import kg_health_report
        kg, _ = _kg_storage.load()
        return kg_health_report(kg)
    except Exception as e:
        log_suppressed(logger, e)
        return {"ok": False, "error": str(e)}


@router.post("/resolve")
async def kg_resolve(request: Request):
    """v17 Phase 94: run entity resolution on the SAVED graph in place (admin) —
    merges 繁/简 + alias variants (and, opt-in, drops anaphoric/generic noise),
    no 82-min rebuild. Body/query: drop_noise=true to also drop noise nodes."""
    require_admin(request)
    try:
        drop = str(request.query_params.get("drop_noise", "")).lower() in {"1", "true", "yes", "on"}
        from hashmm.kg.entity_resolution import apply_to_saved
        return apply_to_saved(drop_noise=drop if drop else None)
    except Exception as e:
        log_suppressed(logger, e)
        return {"saved": False, "error": str(e)}


@router.get("/graph")
async def kg_graph(max_nodes: int = 200):
    """Get graph data for visualization (public)."""
    try:
        kg, _ = _kg_storage.load()
        if kg.num_entities == 0:
            return {"nodes": [], "edges": [], "stats": kg.stats()}
        vis = kg.to_vis_data(max_nodes=max_nodes)
        vis["stats"] = kg.stats()
        return vis
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}


@router.post("/build-incremental")
async def build_kg_incremental(request: Request):
    """v17 Phase 108: incrementally extend the KG — extract triples only from chunks
    not already in the graph and merge them in. Avoids the full ~82-min rebuild when
    a few documents are added. Uses the local Qwen (free) when configured."""
    require_admin(request)
    try:
        from hashmm.kg.kg_incremental import build_incremental_live
        kg_llm = None
        try:
            from hashmm.kg.kg_llm_provider import get_kg_llm_fn
            from hashmm.api import app_state
            kg_llm = get_kg_llm_fn(getattr(app_state, "llm_fn", None))
        except Exception as _e:
            logger.debug(f"KG-LLM provider unavailable: {_e}")
            kg_llm = None
        rebuild_comm = str(request.query_params.get("rebuild_comm", "")).lower() in {"1", "true", "yes"}
        # V103.3: 增量建图（抽取三元组+合并，可能较慢）放线程，不冻结事件循环。响应结构不变。
        import asyncio
        res = await asyncio.to_thread(build_incremental_live, llm_fn=kg_llm, rebuild_comm=rebuild_comm)
        return res
    except Exception as e:
        logger.error(f"KG incremental build failed: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/build-from-chunks")
async def build_kg_from_chunks(request: Request):
    """Build knowledge graph from existing indexed chunks (no LLM, no re-parsing).

    Takes < 5 seconds for 2000 chunks. Uses regex entity extraction
    and co-occurrence relations.
    """
    require_admin(request)
    try:
        from hashmm.kg.lightweight import LightweightKGBuilder
        # v17 Phase 87: if KG LLM extraction is opted in (HASHMM_KG_LLM_EXTRACT=1),
        # use Qwen2.5 (or the chat llm) for clean semantic-triple extraction
        # instead of regex fragments. Default OFF → builder runs unchanged.
        kg_llm = None
        try:
            from hashmm.kg.kg_llm_provider import get_kg_llm_fn, kg_llm_max_chunks
            from hashmm.api import app_state
            kg_llm = get_kg_llm_fn(getattr(app_state, "llm_fn", None))
        except Exception as _e:
            logger.debug(f"KG-LLM provider unavailable, using regex: {_e}")
            kg_llm = None
        builder = LightweightKGBuilder(min_entity_freq=2, llm_fn=kg_llm)
        import asyncio
        if kg_llm is not None:
            logger.info("KG build: using LLM semantic-triple extraction (Qwen2.5/chat)")
            _cap = None
            try:
                _cap = kg_llm_max_chunks()
            except Exception:
                _cap = None
            # V103.3: 全量建图（LLM 语义三元组抽取可达数十分钟）放线程，不冻结事件循环。
            stats = await asyncio.to_thread(builder.build_and_save, max_chunks=_cap)
        else:
            stats = await asyncio.to_thread(builder.build_and_save)

        # v17 Phase 16: detect communities right after build so 社区 isn't 0.
        # The semantic-only graph (dates/metrics excluded as hubs) now forms
        # real clusters.
        try:
            from hashmm.pipeline.ingest import IngestPipeline
            from hashmm.api import app_state

            def _detect_communities():
                pipeline = IngestPipeline()
                pipeline.load_kg()
                # v17 Phase 88: prefer the KG LLM (local Qwen2.5) for community
                # summaries too, so they don't hit the paid chat endpoint.
                _comm_llm = kg_llm or getattr(app_state, "llm_fn", None)
                if _comm_llm:
                    pipeline.community_mgr.set_llm(_comm_llm)
                n_comm = pipeline.rebuild_communities()
                pipeline.storage.save(pipeline.kg, pipeline.community_mgr)
                s = pipeline.kg.stats()
                s["communities_detected"] = n_comm if isinstance(n_comm, int) else s.get("communities", 0)
                return s

            # V103.3: 社区检测+LLM 摘要+持久化也放线程（与建图同样可能很慢）。
            stats = await asyncio.to_thread(_detect_communities)
        except Exception as ce:
            logger.warning(f"community detection after build skipped: {ce}")

        return {"ok": True, "stats": stats}
    except Exception as e:
        logger.error(f"KG build failed: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/entity/{name}")
async def kg_entity(name: str, request: Request, depth: int = 1):
    """Get entity details and neighborhood."""
    require_admin(request)
    try:
        kg, _ = _kg_storage.load()
        entity = kg.get_entity(name)
        if not entity:
            raise HTTPException(404, f"Entity '{name}' not found")
        neighborhood = kg.get_neighbors(name, depth=min(depth, 3))
        relations = kg.get_relations_for_entity(name)
        return {"entity": entity, "neighborhood": neighborhood, "relations": relations}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/search")
async def kg_search(request: Request, q: str = "", limit: int = 20):
    """Search entities by name/description."""
    require_admin(request)
    if not q.strip():
        return {"results": []}
    try:
        kg, _ = _kg_storage.load()
        results = kg.search_entities(q, limit=limit)
        return {"results": results, "total": len(results)}
    except Exception as e:
        return {"results": [], "error": str(e)}


@router.get("/path")
async def kg_path(request: Request, source: str = "", target: str = ""):
    """Find shortest path between two entities."""
    require_admin(request)
    if not source or not target:
        raise HTTPException(400, "source and target required")
    try:
        kg, _ = _kg_storage.load()
        path = kg.get_path(source, target)
        if path is None:
            return {"path": None, "message": f"No path found between '{source}' and '{target}'"}
        return {"path": path, "hops": len(path)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/communities")
async def kg_communities(request: Request):
    """Get all community summaries."""
    require_admin(request)
    try:
        _, comm_mgr = _kg_storage.load()
        return {
            "communities": [info.to_dict() for info in comm_mgr.communities.values()],
            "total": len(comm_mgr.communities),
        }
    except Exception as e:
        return {"communities": [], "error": str(e)}


@router.post("/rebuild-communities")
async def rebuild_communities(request: Request):
    """Rebuild community detection and summaries.

    v16: first re-normalize entities (merge 小米/小米集团/... variants) so the
    graph is actually connected, THEN detect communities. Fixes 社区=0.
    """
    require_admin(request)
    try:
        from hashmm.pipeline.ingest import IngestPipeline
        from hashmm.api import app_state
        import asyncio

        def _do_rebuild():
            pipeline = IngestPipeline()
            pipeline.load_kg()

            if pipeline.kg.num_entities < 5:
                return {"ok": False, "message": "实体数量不足（需要至少 5 个）"}

            # v16: merge fragmented entity variants first (raises density/connectivity)
            merge_stats = {}
            try:
                merge_stats = pipeline.kg.renormalize()
            except Exception as _e:
                merge_stats = {"error": str(_e)[:120]}

            # Set LLM for community summarization
            if app_state.llm_fn:
                pipeline.community_mgr.set_llm(app_state.llm_fn)

            pipeline.rebuild_communities()
            # Persist the re-normalized graph + communities
            try:
                pipeline.storage.save(pipeline.kg, pipeline.community_mgr)
            except Exception as _e:
                log_suppressed(logger, _e)
            stats = pipeline.kg.stats()
            return {"ok": True, "stats": stats, "merge": merge_stats}

        # V103.3: 重活（load_kg / renormalize / 社区检测+LLM 摘要 / save，可达数分钟）放线程，
        # 不再冻结事件循环——重建期间后端对其它请求仍可响应。响应结构不变，前端无需改。
        return await asyncio.to_thread(_do_rebuild)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/export")
async def kg_export(request: Request, format: str = "json"):
    """Export knowledge graph in various formats."""
    require_admin(request)
    try:
        kg, comm_mgr = _kg_storage.load()

        if format == "json":
            import networkx as nx
            data = nx.node_link_data(kg.graph, edges="links")
            data["communities"] = comm_mgr.to_dict()
            return data

        elif format == "graphml":
            import networkx as nx
            import io
            buf = io.BytesIO()
            nx.write_graphml(kg.graph, buf)
            from fastapi.responses import Response
            return Response(
                content=buf.getvalue(),
                media_type="application/xml",
                headers={"Content-Disposition": "attachment; filename=kg.graphml"},
            )

        else:
            raise HTTPException(400, f"Unsupported format: {format}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/build-vdb",
             summary="构建实体/关系向量数据库",
             description="从已有 KG 构建 EntityVDB + RelationVDB，启用 Graph-RAG 检索")
async def build_kg_vdb(request: Request):
    """Build entity and relation vector databases from the current KG."""
    from hashmm.api.auth import require_admin
    require_admin(request)

    try:
        from hashmm.kg.kg_retriever import get_kg_retriever
        retriever = get_kg_retriever()
        if not retriever._kg or retriever._kg.num_entities == 0:
            raise HTTPException(400, "知识图谱为空，请先构建 KG（POST /api/kg/build-from-chunks）")

        # V103.3: 构建实体/关系向量库（可能较慢）放线程，不冻结事件循环。响应结构不变。
        import asyncio
        result = await asyncio.to_thread(retriever.rebuild_vdbs)
        if not result.get("ok"):
            raise HTTPException(500, result.get("error", "构建失败"))

        return {
            "ok": True,
            "entities_indexed": result["entities"],
            "relations_indexed": result["relations"],
            "message": f"VDB 构建完成：{result['entities']} 实体 + {result['relations']} 关系",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/retriever-stats",
            summary="Graph-RAG 检索器状态")
async def kg_retriever_stats():
    """Get KGRetriever status and statistics."""
    try:
        from hashmm.kg.kg_retriever import get_kg_retriever
        retriever = get_kg_retriever()
        return retriever.stats()
    except Exception as e:
        return {"available": False, "error": str(e)}
