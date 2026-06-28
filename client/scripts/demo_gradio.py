#!/usr/bin/env python3
"""Interactive HashMM-RAG demo with Gradio.

Launch:
    pip install gradio --break-system-packages
    python scripts/demo_gradio.py

Then open http://localhost:7860 in your browser (or the AutoDL forwarded URL).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import gradio as gr


# ── Lazy-loaded components ───────────────────────────────────────────

_components = None


def _load():
    global _components
    if _components is not None:
        return _components

    import numpy as np
    import torch
    from hashmm.config import HashMMConfig
    from hashmm.hashing.encoders import TextEncoder
    from hashmm.hashing.train import load_hash_net
    from hashmm.hashing.hash_net import pack_bits

    cfg = HashMMConfig()

    # Load metadata
    meta_path = Path(cfg.hash_index_dir) / "metadata.jsonl"
    metadata = []
    with open(meta_path) as f:
        for line in f:
            metadata.append(json.loads(line))

    # Load encoder + hash net
    text_enc = TextEncoder(model_name=cfg.hash_text_encoder, device=cfg.hash_device)
    hash_net, ckpt_meta = load_hash_net(cfg)

    # Load FAISS index
    import faiss
    faiss_index = faiss.read_index_binary(str(cfg.hash_index_path))

    # Also load float embeddings for vector baseline comparison
    float_embs = None
    float_path = Path(cfg.hash_index_dir) / "float_embeddings.npy"
    if float_path.exists():
        float_embs = np.load(float_path)

    _components = {
        "text_enc": text_enc,
        "hash_net": hash_net,
        "faiss_index": faiss_index,
        "metadata": metadata,
        "bits": ckpt_meta["bits"],
        "cfg": cfg,
        "float_embs": float_embs,
    }
    return _components


def search(query: str, top_k: int, method: str) -> str:
    """Run a search query and return formatted results."""
    import torch
    import numpy as np
    from hashmm.hashing.hash_net import pack_bits

    c = _load()
    t0 = time.time()

    with torch.no_grad():
        q_emb = c["text_enc"]([query]).to(c["cfg"].hash_device)

    if method == "Hash (binary)":
        q_code = c["hash_net"].sign_text(q_emb)
        q_packed = pack_bits(q_code).cpu().numpy().astype(np.uint8)
        distances, indices = c["faiss_index"].search(q_packed, top_k)
        elapsed = time.time() - t0

        results = []
        for rank, (dist, idx) in enumerate(zip(distances[0], indices[0])):
            if idx < 0 or idx >= len(c["metadata"]):
                continue
            entry = c["metadata"][idx]
            text_preview = (entry.get("text") or "")[:200]
            results.append(
                f"**#{rank+1}** [Hamming={dist}] `{entry.get('modality', '?')}` "
                f"— {entry.get('chunk_id', '?')}\n"
                f"> {text_preview}{'...' if len(entry.get('text', '')) > 200 else ''}\n"
            )

        header = (
            f"**Hash retrieval** ({c['bits']}-bit) — "
            f"{len(results)} results in {elapsed*1000:.1f} ms\n"
            f"Index size: {c['faiss_index'].ntotal} chunks, "
            f"{c['faiss_index'].ntotal * c['bits'] // 8 / 1024:.1f} KB\n\n"
        )
        return header + "\n".join(results) if results else header + "No results found."

    else:
        # Vector search (if float embeddings available)
        if c["float_embs"] is None:
            return "Float embeddings not available. Run 04_build_index.py first."

        q_np = q_emb.cpu().numpy().astype(np.float32)
        q_np /= (np.linalg.norm(q_np) + 1e-9)
        scores = (c["float_embs"] @ q_np.T).flatten()
        top_idx = np.argsort(-scores)[:top_k]
        elapsed = time.time() - t0

        results = []
        for rank, idx in enumerate(top_idx):
            entry = c["metadata"][idx]
            text_preview = (entry.get("text") or "")[:200]
            results.append(
                f"**#{rank+1}** [cosine={scores[idx]:.4f}] `{entry.get('modality', '?')}` "
                f"— {entry.get('chunk_id', '?')}\n"
                f"> {text_preview}{'...' if len(entry.get('text', '')) > 200 else ''}\n"
            )

        header = (
            f"**Vector retrieval** (1024-d float) — "
            f"{len(results)} results in {elapsed*1000:.1f} ms\n"
            f"Index size: {c['float_embs'].shape[0]} chunks, "
            f"{c['float_embs'].nbytes / (1024*1024):.1f} MB\n\n"
        )
        return header + "\n".join(results) if results else header + "No results found."


def get_stats() -> str:
    c = _load()
    modalities = {}
    for entry in c["metadata"]:
        m = entry.get("modality", "unknown")
        modalities[m] = modalities.get(m, 0) + 1

    lines = [
        f"**Corpus**: {len(c['metadata'])} chunks",
        f"**Hash bits**: {c['bits']}",
        f"**FAISS index**: {c['faiss_index'].ntotal} entries, "
        f"{c['faiss_index'].ntotal * c['bits'] // 8 / 1024:.1f} KB",
        f"**Modalities**: {json.dumps(modalities)}",
    ]
    return "\n".join(lines)


# ── Gradio UI ────────────────────────────────────────────────────────

with gr.Blocks(title="HashMM-RAG Demo") as demo:
    gr.Markdown(
        "# HashMM-RAG — Cross-Modal Hash-Augmented Multimodal RAG\n"
        "Search academic papers using 256-bit binary hash codes with "
        "124× index compression vs dense vectors."
    )

    with gr.Row():
        with gr.Column(scale=3):
            query_input = gr.Textbox(
                label="Search Query",
                placeholder="e.g., cross-modal hashing for retrieval",
                lines=2,
            )
            with gr.Row():
                top_k = gr.Slider(1, 50, value=10, step=1, label="Top-K")
                method = gr.Radio(
                    ["Hash (binary)", "Vector (float)"],
                    value="Hash (binary)",
                    label="Retrieval Method",
                )
            search_btn = gr.Button("Search", variant="primary")

        with gr.Column(scale=1):
            stats_output = gr.Markdown(label="Corpus Stats")
            stats_btn = gr.Button("Refresh Stats")

    results_output = gr.Markdown(label="Results")

    search_btn.click(search, [query_input, top_k, method], results_output)
    query_input.submit(search, [query_input, top_k, method], results_output)
    stats_btn.click(get_stats, [], stats_output)
    demo.load(get_stats, [], stats_output)


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
