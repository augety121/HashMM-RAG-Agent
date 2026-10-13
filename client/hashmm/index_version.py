"""v17 Phase 30 — embedding/index version pinning + compatibility regression.

A silent killer in production RAG: rebuilding or swapping the embedding model
(or its dimension) without rebuilding the FAISS index. Query vectors then come from
a different space than the indexed vectors → retrieval quietly degrades with NO
error. This module pins the embedding fingerprint into the index metadata and
checks it at load time so a mismatch is caught LOUDLY (and you re-index + re-run the
golden/C-MTEB regression).

Pure + offline-testable. Wire: write meta after building the index; check on load.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


def embedding_fingerprint(model_name: str, dim: int, normalize: bool = True) -> str:
    """Stable fingerprint of the embedding config the index depends on."""
    raw = f"{(model_name or '').strip().lower()}|dim={dim}|norm={int(bool(normalize))}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def write_index_meta(index_dir: str, model_name: str, dim: int,
                     n_vectors: int = 0, normalize: bool = True,
                     extra: dict | None = None) -> dict:
    """Persist embedding/index version metadata next to the index."""
    meta = {
        "embedding_model": model_name,
        "embedding_dim": dim,
        "normalize": bool(normalize),
        "fingerprint": embedding_fingerprint(model_name, dim, normalize),
        "n_vectors": n_vectors,
        "built_at": time.time(),
        **(extra or {}),
    }
    p = Path(index_dir)
    p.mkdir(parents=True, exist_ok=True)
    (p / "index_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    return meta


def read_index_meta(index_dir: str) -> dict | None:
    f = Path(index_dir) / "index_meta.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def check_index_compatibility(meta: dict | None, serving_model: str, serving_dim: int,
                              normalize: bool = True) -> tuple[bool, str]:
    """Compare the index's build-time embedding fingerprint to the serving embedding.

    Returns (ok, reason). ok=False means the index must be rebuilt before serving —
    otherwise retrieval is silently wrong.
    """
    if not meta:
        return True, "无 index_meta（旧索引）：建议下次重建时写入版本指纹以便后续校验。"
    serving_fp = embedding_fingerprint(serving_model, serving_dim, normalize)
    if meta.get("fingerprint") == serving_fp:
        return True, "索引与服务端嵌入一致。"
    return False, (
        f"嵌入不匹配：索引按 {meta.get('embedding_model')}(dim={meta.get('embedding_dim')}) 构建，"
        f"当前服务用 {serving_model}(dim={serving_dim})。必须重建索引并重跑 golden/C-MTEB 回归，"
        f"否则检索会静默失真。")
