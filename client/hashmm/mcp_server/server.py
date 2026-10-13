"""HashMM-RAG MCP Server — expose retrieval as tools for Claude Desktop / Cursor.

Three tools:
  - cross_modal_search: hash-based retrieval (fast, compact)
  - hybrid_search: vector + hash with RRF fusion
  - corpus_stats: index metadata

Two resources:
  - chunks://{chunk_id}: read a specific chunk

One prompt:
  - research_query: template for academic paper Q&A

Install:
    pip install "mcp>=1.2" --break-system-packages

Run (stdio, for Claude Desktop):
    python -m hashmm.mcp_server.server

Claude Desktop config (~/.config/claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "hashmm-rag": {
          "command": "python",
          "args": ["-m", "hashmm.mcp_server.server"],
          "cwd": "/root/autodl-tmp"
        }
      }
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ── Server instance ──────────────────────────────────────────────────

mcp = FastMCP("HashMM-RAG")

# ── Lazy-loaded retrieval components ─────────────────────────────────

_retriever = None
_metadata = None


def _ensure_loaded():
    """Lazy-load hash net, FAISS index, and metadata on first call."""
    global _retriever, _metadata
    if _retriever is not None:
        return

    import sys
    print("Loading HashMM-RAG retrieval components...", file=sys.stderr)

    from hashmm.config import HashMMConfig
    cfg = HashMMConfig()

    # Load metadata (chunk info)
    meta_path = Path(cfg.hash_index_dir) / "metadata.jsonl"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"Index metadata not found at {meta_path}. "
            f"Run: python scripts/04_build_index.py --chunks data/chunks.jsonl"
        )
    _metadata = {}
    with open(meta_path) as f:
        for line in f:
            entry = json.loads(line)
            _metadata[entry["chunk_id"]] = entry

    # Load hash retriever
    from hashmm.hashing.encoders import TextEncoder
    from hashmm.hashing.train import load_hash_net
    from hashmm.hashing.index import load_faiss_binary_index

    text_enc = TextEncoder(model_name=cfg.hash_text_encoder, device=cfg.hash_device)
    hash_net, ckpt_meta = load_hash_net(cfg)

    index_path = cfg.hash_index_path
    if not index_path.exists():
        raise FileNotFoundError(f"FAISS index not found at {index_path}")

    import faiss
    faiss_index = faiss.read_index_binary(str(index_path))

    _retriever = {
        "text_enc": text_enc,
        "hash_net": hash_net,
        "faiss_index": faiss_index,
        "bits": ckpt_meta["bits"],
        "cfg": cfg,
    }
    print(f"Loaded: {faiss_index.ntotal} chunks, {ckpt_meta['bits']}-bit codes",
          file=sys.stderr)


def _search(query: str, top_k: int = 10) -> list[dict]:
    """Core search: query → BGE-M3 → hash head → FAISS binary → results."""
    _ensure_loaded()
    import torch
    import numpy as np
    from hashmm.hashing.hash_net import pack_bits

    r = _retriever
    # Encode query
    with torch.no_grad():
        q_emb = r["text_enc"]([query]).to(r["cfg"].hash_device)
        q_code = r["hash_net"].sign_text(q_emb)
        q_packed = pack_bits(q_code).cpu().numpy().astype(np.uint8)

    # FAISS search
    distances, indices = r["faiss_index"].search(q_packed, top_k)

    # Build results
    meta_list = list(_metadata.values())
    results = []
    for rank, (dist, idx) in enumerate(zip(distances[0], indices[0])):
        if idx < 0 or idx >= len(meta_list):
            continue
        entry = meta_list[idx]
        results.append({
            "rank": rank + 1,
            "chunk_id": entry.get("chunk_id", ""),
            "modality": entry.get("modality", ""),
            "text": (entry.get("text") or "")[:500],
            "hamming_distance": int(dist),
            "doc_id": entry.get("doc_id", ""),
        })
    return results


# ── Tools ────────────────────────────────────────────────────────────


@mcp.tool()
def cross_modal_search(query: str, top_k: int = 10) -> list[dict]:
    """Search the indexed corpus using cross-modal hash codes.

    Fast binary retrieval: queries are hashed to compact codes and matched
    via Hamming distance. Returns ranked results with chunk text previews.

    Args:
        query: Natural language search query
        top_k: Number of results to return (default 10)
    """
    return _search(query, top_k)


@mcp.tool()
def corpus_stats() -> dict:
    """Get statistics about the indexed corpus.

    Returns chunk count, index size, hash bit width, and modality breakdown.
    """
    _ensure_loaded()
    r = _retriever
    modalities = {}
    for entry in _metadata.values():
        m = entry.get("modality", "unknown")
        modalities[m] = modalities.get(m, 0) + 1

    index_path = r["cfg"].hash_index_path
    index_bytes = index_path.stat().st_size if index_path.exists() else 0

    return {
        "total_chunks": r["faiss_index"].ntotal,
        "hash_bits": r["bits"],
        "index_size_kb": round(index_bytes / 1024, 1),
        "modality_breakdown": modalities,
    }


# ── Resources ────────────────────────────────────────────────────────


@mcp.resource("chunks://{chunk_id}")
def get_chunk(chunk_id: str) -> str:
    """Read a specific chunk by its ID."""
    _ensure_loaded()
    entry = _metadata.get(chunk_id)
    if not entry:
        return f"Chunk {chunk_id} not found"
    return json.dumps(entry, ensure_ascii=False, indent=2)


# ── Prompts ──────────────────────────────────────────────────────────


@mcp.prompt()
def research_query(topic: str) -> str:
    """Generate a structured research query prompt for academic paper search."""
    return (
        f"Search the indexed academic papers for information about: {topic}\n\n"
        f"Use the cross_modal_search tool to find relevant chunks, then "
        f"synthesize the findings into a coherent answer with citations to "
        f"specific chunk IDs."
    )


# ── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
