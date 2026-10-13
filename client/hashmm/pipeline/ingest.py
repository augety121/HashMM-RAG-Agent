"""Ingest Pipeline — unified document ingestion with KG extraction.

Flow: parse → chunk → vectorize → hash → KG extract → index → save

Supports incremental ingestion (add single doc without rebuilding).
"""
from __future__ import annotations
from hashmm.llm_timeout import client_timeout
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable
from hashmm.pipeline.chunker import TextChunker, Chunk
from hashmm.kg.extractor import KGExtractor
from hashmm.kg.graph import KnowledgeGraph
from hashmm.kg.community import CommunityManager
from hashmm.kg.storage import KGStorage
from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.pipeline.ingest")


@dataclass
class IngestResult:
    """Result of ingesting a document."""
    doc_id: str
    filename: str
    num_chunks: int = 0
    num_entities: int = 0
    num_relations: int = 0
    elapsed_ms: int = 0
    errors: list[str] = field(default_factory=list)
    status: str = "success"  # "success" | "partial" | "failed" | "duplicate" | "quarantined"

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "filename": self.filename,
            "num_chunks": self.num_chunks, "num_entities": self.num_entities,
            "num_relations": self.num_relations, "elapsed_ms": self.elapsed_ms,
            "errors": self.errors, "status": self.status,
        }


@dataclass
class IngestProgress:
    """Tracks progress of an ingestion job."""
    stage: str = "idle"        # idle, parsing, chunking, vectorizing, extracting_kg, indexing, done
    progress: float = 0.0     # 0.0 - 1.0
    message: str = ""
    entities_found: int = 0
    chunks_processed: int = 0
    total_chunks: int = 0


class IngestPipeline:
    """Unified pipeline for document ingestion with KG extraction.

    This pipeline:
    1. Parses documents (PDF/DOCX/TXT/code)
    2. Chunks text using configurable strategy
    3. Vectorizes chunks with BGE-M3 (if encoder available)
    4. Extracts KG entities/relations with LLM
    5. Updates the knowledge graph
    6. Appends to metadata for existing FAISS index

    Args:
        kg: KnowledgeGraph instance (shared with retriever)
        kg_extractor: KGExtractor with LLM configured
        community_mgr: CommunityManager for graph communities
        encoder_fn: Optional function to encode text → embeddings
        chunk_size: Target chunk size in characters
        chunk_strategy: "fixed" | "recursive" | "paragraph"
        kg_dir: Directory for KG persistence
        metadata_path: Path to metadata.jsonl (appended incrementally)
    """

    def __init__(
        self,
        kg: KnowledgeGraph | None = None,
        kg_extractor: KGExtractor | None = None,
        community_mgr: CommunityManager | None = None,
        encoder_fn: Callable | None = None,
        chunk_size: int = 800,
        chunk_strategy: str = "recursive",
        kg_dir: str = "data/kg",
        metadata_path: str = "data/chunks.jsonl",
    ):
        self.chunker = TextChunker(chunk_size=chunk_size, strategy=chunk_strategy)
        self.kg = kg or KnowledgeGraph()
        self.extractor = kg_extractor or KGExtractor()
        self.community_mgr = community_mgr or CommunityManager()
        # Optional LLM for semantic-triple KG extraction (opt-in; default None →
        # local co-occurrence extractor as before). Set via set_kg_llm() or, for
        # backward compatibility, via self.extractor.set_llm(...).
        self._kg_llm_fn: Callable | None = None
        self.encoder_fn = encoder_fn
        self.storage = KGStorage(kg_dir)
        self.metadata_path = Path(metadata_path)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)

        self.progress = IngestProgress()

    def set_kg_llm(self, llm_fn: Callable | None):
        """Register an LLM for semantic-triple KG extraction.

        Also forwarded to the current extractor's set_llm so existing call sites
        (``pipeline.extractor.set_llm(...)``) keep working. Whether the LLM path
        is actually used is decided at extraction time by ``HASHMM_KG_LLM_EXTRACT``
        (default off → unchanged local extraction).
        """
        self._kg_llm_fn = llm_fn
        try:
            self.extractor.set_llm(llm_fn)
        except Exception as _e:
            log_suppressed(logger, _e)

    def _select_extractor(self):
        """Pick the extraction backend for this run.

        Opt-in only: returns the LLM semantic-triple extractor iff
        ``HASHMM_KG_LLM_EXTRACT`` is truthy AND a usable llm_fn is available.
        Otherwise returns the existing (local co-occurrence) extractor, so
        default behaviour is byte-for-byte unchanged.
        """
        import os
        flag = os.environ.get("HASHMM_KG_LLM_EXTRACT", "").strip().lower()
        if flag not in ("1", "true", "yes", "on"):
            return self.extractor
        llm_fn = self._kg_llm_fn or getattr(self.extractor, "llm_fn", None)
        if not callable(llm_fn):
            logger.warning(
                "HASHMM_KG_LLM_EXTRACT is set but no llm_fn is available; "
                "falling back to local KG extraction"
            )
            return self.extractor
        from hashmm.kg.llm_extractor import LLMTripleExtractor
        if isinstance(self.extractor, LLMTripleExtractor):
            return self.extractor
        logger.info("KG extraction: LLM semantic-triple mode (opt-in) active for this run")
        return LLMTripleExtractor(llm_fn=llm_fn)

    def ingest_file(self, filepath: str | Path,
                    extract_kg: bool = True,
                    on_progress: Callable[[IngestProgress], None] | None = None,
                    doc_id: str | None = None,
                    skip_quality: bool = False,
                    ) -> IngestResult:
        """Ingest a single document file.

        Args:
            filepath: Path to the document
            extract_kg: Whether to extract knowledge graph (slower but richer)
            on_progress: Callback for progress updates
            doc_id: Optional fixed doc_id (for reparse — reuse existing ID)

        Returns:
            IngestResult with statistics
        """
        filepath = Path(filepath)
        t0 = time.time()
        result = IngestResult(doc_id="", filename=filepath.name)

        # V204 图2-①：文件指纹幂等控制——同一文件（SHA256）重复上传直接命中既有
        # doc_id，跳过 解析/切块/向量化/KG 全流程（reparse 场景显式传 doc_id，跳过查重）。
        _sha = ""
        try:
            from hashmm.pipeline import fingerprint as _fp
            _sha = _fp.sha256_file(filepath)
            if _sha and not doc_id:
                _dup = _fp.lookup(_sha)
                if _dup:
                    result.doc_id = _dup["doc_id"]
                    result.status = "duplicate"
                    result.errors = []
                    result.elapsed_ms = int((time.time() - t0) * 1000)
                    logger.info(f"指纹命中，跳过重复入库: {filepath.name} -> doc {_dup['doc_id']}")
                    return result
        except Exception as _fpe:
            log_suppressed(logger, _fpe, "fingerprint.guard")

        def _update(stage: str, progress: float, msg: str = ""):
            self.progress.stage = stage
            self.progress.progress = progress
            self.progress.message = msg
            if on_progress:
                on_progress(self.progress)

        try:
            # Stage 1: Parse (3-level fallback per format)
            _update("parsing", 0.1, f"解析 {filepath.name}...")
            from hashmm.pipeline.parser import DocumentParser
            parser = DocumentParser(output_dir="data/docs")
            doc = parser.parse(filepath)
            # v11: Override doc_id for reparse (keep the same ID)
            if doc_id:
                doc.doc_id = doc_id
            result.doc_id = doc.doc_id
            self._current_filename = doc.filename  # For _encode_and_index

            if not doc.full_text.strip():
                result.status = "failed"
                result.errors.append("文档解析为空")
                if doc.quality.issues:
                    result.errors.extend(doc.quality.issues)
                return result

            # V205 P1-6：质量闸（防坏）——低分文档进隔离区待人工复核，不污染索引。
            # skip_quality=True（隔离区"放行"重入）或阈值=0 时跳过；隔离模块失败按放行处理。
            if not skip_quality:
                try:
                    from hashmm.pipeline import quarantine as _quar
                    _thr = _quar.threshold()
                    _score = float(getattr(doc.quality, "overall_score", 1.0) or 1.0)
                    if _thr > 0 and _score < _thr:
                        _rec = _quar.stage(filepath, _score, list(doc.quality.issues or []))
                        if _rec:
                            result.status = "quarantined"
                            result.errors.append(
                                f"解析质量分 {_score:.2f} 低于阈值 {_thr}，已进隔离区待复核（qid={_rec['qid']}）")
                            result.errors.extend(list(doc.quality.issues or [])[:5])
                            _update("done", 1.0, f"已隔离: {filepath.name}（质量分 {_score:.2f}）")
                            return result
                except Exception as _qe:
                    log_suppressed(logger, _qe, "quality.gate")

            # Stage 2: Preprocess + OCR correction
            _update("preprocessing", 0.15, f"文本预处理 + OCR 纠错...")
            from hashmm.pipeline.text_preprocessor import TextPreprocessor
            from hashmm.pipeline.ocr_corrector import OCRCorrector
            preprocessor = TextPreprocessor()
            ocr = OCRCorrector()

            for b in doc.blocks:
                if not b.is_noise and b.content.strip():
                    b.content = preprocessor.process(b.content)
            ocr.correct_blocks(doc.blocks)

            # Stage 3: Chunk from ContentBlocks (preserve page/section metadata)
            _update("chunking", 0.25, f"分块中... ({len(doc.blocks)} blocks)")
            clean_blocks = [b for b in doc.blocks if not b.is_noise and b.content.strip()]

            # Build page mapping for chunker
            pages_info = []
            offset = 0
            block_page_map = {}
            for b in clean_blocks:
                block_page_map[offset] = (b.page, b.section)
                pages_info.append((offset, offset + len(b.content), b.page))
                offset += len(b.content) + 2  # +2 for \n\n separator

            chunks = self.chunker.chunk(doc.full_text, doc.doc_id, pages_info)
            result.num_chunks = len(chunks)
            self.progress.total_chunks = len(chunks)

            # Enrich chunks with section info from blocks
            for chunk in chunks:
                # Find which block this chunk came from by char position
                best_page = -1
                best_section = ""
                for block_offset, (page, section) in block_page_map.items():
                    if chunk.start_char >= block_offset:
                        best_page = page
                        best_section = section
                if chunk.page <= 0 and best_page > 0:
                    chunk.page = best_page
                if not getattr(chunk, 'section', '') and best_section:
                    chunk.section = best_section

            if not chunks:
                result.status = "failed"
                result.errors.append("切片结果为空")
                return result

            # Stage 3: Save parsed document + metadata
            _update("indexing", 0.4, f"保存 {len(chunks)} 个切片...")
            self._save_parsed_doc(doc)
            self._append_metadata(chunks, doc)

            # Stage 3.5: Encode chunks → FAISS (if encoder available)
            _update("indexing", 0.45, "向量编码中...")
            self._encode_and_index(chunks, doc_text=getattr(doc, "full_text", ""))

            # Stage 4: Extract KG. Local co-occurrence by default; LLM
            # semantic-triple extraction when opted in (HASHMM_KG_LLM_EXTRACT=1).
            if extract_kg:
                extractor = self._select_extractor()
                from hashmm.kg.llm_extractor import LLMTripleExtractor
                _mode = "LLM 语义三元组" if isinstance(extractor, LLMTripleExtractor) else "本地模式"
                _update("extracting_kg", 0.5, f"提取知识图谱（{_mode}）...")
                chunk_dicts = [c.to_dict() for c in chunks]

                def kg_progress(completed: int, total: int):
                    self.progress.chunks_processed = completed
                    self.progress.entities_found = self.kg.num_entities
                    pct = 0.5 + 0.4 * (completed / max(total, 1))
                    _update("extracting_kg", pct,
                            f"KG 提取: {completed}/{total}, "
                            f"{self.kg.num_entities} 实体")

                entities, relations = extractor.extract_from_chunks(
                    chunk_dicts, on_progress=kg_progress
                )
                self.kg.add_entities(entities)
                self.kg.add_relations(relations)

                # V104 P2：表格 → 结构化三元组进 KG（默认关，HASHMM_TABLE_KG=1）。确定性、
                # 纯 Python、永不抛错；把"行键×列头→值"接进图谱，让表格可被结构化检索（EvoGraph-R1
                # 多模态超图的落地）。文本三元组照常由上面的 extractor 产出，二者互不影响。
                import os as _os
                if _os.environ.get("HASHMM_TABLE_KG", "0") == "1":
                    try:
                        from hashmm.kg.table_extractor import extract_table_kg
                        t_ents, t_rels, _ti = [], [], 0
                        for _b in doc.blocks:
                            if getattr(_b, "type", "") == "table" and getattr(_b, "table_data", None):
                                _e, _r = extract_table_kg(
                                    _b.table_data, section=getattr(_b, "section", ""),
                                    source_id=doc.doc_id, table_index=_ti,
                                )
                                t_ents.extend(_e); t_rels.extend(_r); _ti += 1
                        if t_ents or t_rels:
                            self.kg.add_entities(t_ents)
                            self.kg.add_relations(t_rels)
                            logger.info(f"表格 KG: +{len(t_ents)} 实体 +{len(t_rels)} 关系（{_ti} 张表）")
                    except Exception as _te:
                        log_suppressed(logger, _te)

                result.num_entities = len(entities)
                result.num_relations = len(relations)

                # Save KG
                _update("indexing", 0.90, "精炼知识图谱...")
                # Phase 3: Refine entities
                from hashmm.kg.refiner import KGRefiner
                refiner = KGRefiner(min_frequency=1)
                chunk_dicts_for_refine = [c.to_dict() for c in chunks]
                refine_stats = refiner.refine(self.kg, chunk_dicts_for_refine)
                result.num_entities = self.kg.num_entities
                logger.info(f"KG refined: {refine_stats}")

                _update("indexing", 0.95, "保存知识图谱...")
                self.storage.save(self.kg, self.community_mgr)

            # Stage 5: Done
            result.elapsed_ms = round((time.time() - t0) * 1000)
            # V204 图2-①：入库成功登记指纹（供下次幂等命中）
            try:
                if _sha and result.status == "success":
                    from hashmm.pipeline import fingerprint as _fp2
                    _fp2.register(_sha, filepath.name, filepath.stat().st_size, result.doc_id)
            except Exception as _rge:
                log_suppressed(logger, _rge, "fingerprint.register")
            _update("done", 1.0, f"完成: {result.num_chunks} 切片, "
                    f"{result.num_entities} 实体, {result.num_relations} 关系")

            logger.info(f"Ingested {filepath.name}: {result.num_chunks} chunks, "
                        f"{result.num_entities} entities, {result.num_relations} relations "
                        f"({result.elapsed_ms}ms)")

        except Exception as e:
            result.status = "failed"
            result.errors.append(str(e))
            logger.error(f"Ingest failed for {filepath}: {e}")
            _update("done", 1.0, f"失败: {e}")

        return result

    def ingest_directory(self, dirpath: str | Path,
                         extract_kg: bool = True,
                         extensions: list[str] | None = None,
                         on_progress: Callable[[IngestProgress], None] | None = None,
                         ) -> list[IngestResult]:
        """Ingest all documents in a directory.

        Args:
            dirpath: Directory path
            extract_kg: Whether to extract KG
            extensions: File extensions to process (default: common doc types)
            on_progress: Progress callback

        Returns:
            List of IngestResult for each file
        """
        dirpath = Path(dirpath)
        if not dirpath.exists():
            raise FileNotFoundError(f"Directory not found: {dirpath}")

        exts = extensions or [".pdf", ".docx", ".doc", ".txt", ".md",
                               ".py", ".json", ".csv", ".tex"]
        files = [f for f in dirpath.rglob("*") if f.suffix.lower() in exts and f.is_file()]
        files.sort()

        results = []
        for i, filepath in enumerate(files):
            logger.info(f"Ingesting [{i + 1}/{len(files)}]: {filepath.name}")
            r = self.ingest_file(filepath, extract_kg=extract_kg, on_progress=on_progress)
            results.append(r)

        # Rebuild communities after batch ingest
        if extract_kg and self.kg.num_entities >= 10:
            # Phase 3: Refine KG entities
            logger.info("Refining KG entities...")
            from hashmm.kg.refiner import KGRefiner
            refiner = KGRefiner(min_frequency=1)
            all_chunks = []
            for r in results:
                if r.doc_id:
                    doc_chunks = self._load_doc_chunks(r.doc_id)
                    all_chunks.extend(doc_chunks)
            refine_stats = refiner.refine(self.kg, all_chunks)
            logger.info(f"KG refined: {refine_stats}")

            # Detect communities
            logger.info("Detecting communities...")
            communities = self.community_mgr.detect_communities(self.kg)
            if communities and self.community_mgr.llm_fn:
                self.community_mgr.summarize_communities(self.kg, communities)
            self.storage.save(self.kg, self.community_mgr)

        return results

    def rebuild_communities(self):
        """Rebuild community detection and summaries. Returns community count."""
        if self.kg.num_entities < 5:
            logger.warning("Too few entities for community detection")
            return 0

        communities = self.community_mgr.detect_communities(self.kg)
        # v17 Phase 98 (LazyGraphRAG): defer the expensive LLM summarization unless
        # explicitly disabled via HASHMM_KG_LAZY_SUMMARIES. Detection (cheap) still
        # runs; summaries are produced on-demand / via a later rebuild.
        try:
            from hashmm.kg.community_retrieval import lazy_summaries_enabled
            _lazy = lazy_summaries_enabled()
        except Exception:
            _lazy = False
        if communities and self.community_mgr.llm_fn and not _lazy:
            self.community_mgr.summarize_communities(self.kg, communities)
        elif _lazy:
            logger.info("LazyGraphRAG: community summarization deferred "
                        "(HASHMM_KG_LAZY_SUMMARIES); using member-name context at query time")
        self.storage.save(self.kg, self.community_mgr)
        return len(communities)

    def remove_document(self, doc_id: str):
        try:
            from hashmm.pipeline import fingerprint as _fp3
            _fp3.forget_doc(doc_id)
        except Exception as _fde:
            log_suppressed(logger, _fde, "fingerprint.forget")
        """Remove a document and its KG contributions."""
        # Remove from KG
        self.kg.remove_by_source(doc_id)

        # Remove from metadata
        if self.metadata_path.exists():
            remaining = []
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                for line in f:
                    entry = json.loads(line.strip())
                    if entry.get("doc_id") != doc_id:
                        remaining.append(line)
            with open(self.metadata_path, "w", encoding="utf-8") as f:
                f.writelines(remaining)

        # Save updated KG
        self.storage.save(self.kg, self.community_mgr)
        logger.info(f"Removed document: {doc_id}")

    def hot_add_to_faiss(self, chunks: list[Chunk], encoder_fn=None):
        """D3: Add chunks to FAISS index without full rebuild.

        Requires the FAISS index + encoder to be loaded in server state.
        """
        if not encoder_fn and not self.encoder_fn:
            logger.warning("No encoder available for FAISS hot-add")
            return False

        enc = encoder_fn or self.encoder_fn
        try:
            from hashmm.api import app_state
            state = app_state.state
            fi = state.get("faiss_index")
            meta = state.get("metadata", [])
            hash_net = state.get("hash_net")

            if not fi or not hash_net:
                logger.warning("FAISS index or hash_net not loaded, skipping hot-add")
                return False

            import numpy as np
            texts = [c.text for c in chunks]
            embeddings = enc(texts)  # shape: (N, 1024)
            if hasattr(embeddings, 'cpu'):
                embeddings = embeddings.cpu().numpy()

            # Encode to binary hash
            pack_bits = state.get("pack_bits")
            if pack_bits and hash_net:
                import torch
                with torch.no_grad():
                    codes = hash_net(torch.tensor(embeddings, dtype=torch.float32))
                    binary = pack_bits(codes)
                fi.add(binary)

                # Append metadata
                for c in chunks:
                    meta.append(c.to_dict())

                # Save updated index
                import faiss
                cfg = state.get("cfg")
                if cfg:
                    faiss.write_index_binary(fi, str(cfg.hash_index_path))
                    # Update metadata.jsonl
                    meta_path = cfg.hash_index_dir + "/metadata.jsonl"
                    with open(meta_path, "a", encoding="utf-8") as f:
                        for c in chunks:
                            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")

                logger.info(f"Hot-added {len(chunks)} chunks to FAISS ({fi.ntotal} total)")
                return True
        except Exception as e:
            logger.error(f"FAISS hot-add failed: {e}")
        return False

    def _append_metadata(self, chunks: list[Chunk], doc):
        """Append chunk metadata to chunks.jsonl (incremental)."""
        with open(self.metadata_path, "a", encoding="utf-8") as f:
            for chunk in chunks:
                entry = {
                    "doc_id": doc.doc_id,
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text[:500],
                    "modality": chunk.modality or getattr(doc, 'file_type', 'text'),
                    "page": chunk.page,
                    "source": doc.filename,
                    "parser": getattr(doc, 'parser_used', ''),
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _save_parsed_doc(self, doc):
        """Save full parsed document to data/docs/{doc_id}/."""
        doc_dir = Path("data/docs") / doc.doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)

        # Save parsed.json (document structure + quality report)
        parsed_path = doc_dir / "parsed.json"
        with open(parsed_path, "w", encoding="utf-8") as f:
            json.dump(doc.to_dict(), f, ensure_ascii=False, indent=2)

        # Save clean full text
        with open(doc_dir / "full_text.txt", "w", encoding="utf-8") as f:
            f.write(doc.full_text)

        # Save sections
        if doc.sections:
            with open(doc_dir / "sections.json", "w", encoding="utf-8") as f:
                json.dump([{"title": s.title, "level": s.level, "start": s.start_block}
                           for s in doc.sections], f, ensure_ascii=False, indent=2)

        # Save tables as CSV + Markdown
        table_idx = 0
        for block in doc.blocks:
            if block.type == "table" and block.table_data:
                (doc_dir / "tables").mkdir(exist_ok=True)
                import csv as _csv
                with open(doc_dir / "tables" / f"table_{table_idx}.csv", "w",
                          encoding="utf-8", newline="") as f:
                    _csv.writer(f).writerows(block.table_data)
                with open(doc_dir / "tables" / f"table_{table_idx}.md", "w",
                          encoding="utf-8") as f:
                    f.write(block.content)
                table_idx += 1

        # Save image contexts
        for block in doc.blocks:
            if block.type == "image" and block.image_path:
                ctx_path = Path(block.image_path).with_suffix(".context.txt")
                ctx_path.write_text(block.content, encoding="utf-8")

        # Save quality report
        with open(doc_dir / "quality_report.json", "w", encoding="utf-8") as f:
            json.dump(doc.quality.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info(f"Saved: {doc_dir} ({len(doc.full_text)} chars, "
                    f"{doc.table_count} tables, {doc.image_count} images, "
                    f"quality={doc.quality.overall_score:.2f})")

    def load_kg(self) -> tuple[KnowledgeGraph, CommunityManager]:
        """Load existing KG from disk."""
        if self.storage.exists():
            self.kg, self.community_mgr = self.storage.load()
        return self.kg, self.community_mgr

    def _load_doc_chunks(self, doc_id: str) -> list[dict]:
        """Load chunks for a specific document from metadata."""
        chunks = []
        if self.metadata_path.exists():
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("doc_id") == doc_id:
                            chunks.append(entry)
                    except Exception as _e:
                        log_suppressed(logger, _e)
        return chunks

    def _encode_and_index(self, chunks: list, doc_text: str = ""):
        """Encode chunks with BGE-M3 and add to vector + BM25 indexes.

        v17 Phase 70: the text that gets EMBEDDED / BM25-indexed is the
        contextual-retrieval text (raw text when the feature is off → no change);
        the metadata still stores the ORIGINAL chunk text for display/return.
        """
        if not chunks:
            return

        texts = [c.text for c in chunks]
        doc_id = chunks[0].doc_id if hasattr(chunks[0], 'doc_id') else "unknown"

        # Contextual Retrieval: index text = situating context + chunk (mode-driven).
        # 'off' returns texts unchanged → identical to pre-Phase-70 behavior.
        try:
            from hashmm.retrieval import contextual as _ctx
            index_texts = _ctx.contextualize_chunks(chunks, doc_text=doc_text, llm_fn=self._kg_llm_fn)
            if not index_texts or len(index_texts) != len(texts):
                index_texts = texts
        except Exception as e:
            logger.warning(f"Contextual retrieval skipped (fallback to raw text): {e}")
            index_texts = texts

        # Build metadata for each chunk (display text stays the ORIGINAL chunk).
        meta_list = []
        for c in chunks:
            meta_list.append({
                "doc_id": getattr(c, 'doc_id', doc_id),
                "chunk_id": getattr(c, 'chunk_id', ''),
                "text": c.text[:500],
                "filename": getattr(self, '_current_filename', ''),
                "page": getattr(c, 'page', -1),
                "section": getattr(c, 'section', ''),
                "start_char": getattr(c, 'start_char', 0),
            })

        # 1. Vector index (BGE-M3 → FAISS)
        try:
            from hashmm.encoder_pool import EncoderPool
            embeddings = EncoderPool.encode_texts(index_texts, show_progress=len(index_texts) > 100)

            from hashmm.vector_index import VectorIndex
            vi = VectorIndex()
            vi.add(embeddings, meta_list)
            vi.save()
            logger.info(f"Indexed {len(index_texts)} chunks to FAISS (total: {vi.total_vectors})")
        except Exception as e:
            logger.warning(f"Vector indexing failed: {e}")

        # 2. BM25 keyword index (contextual BM25 — same index text)
        try:
            from hashmm.retrieval_pipeline import BM25Index
            bm25 = BM25Index()
            bm25.load()  # Load existing
            bm25.add(index_texts, meta_list)
            bm25.save()
            logger.info(f"Indexed {len(index_texts)} chunks to BM25 (total: {bm25.size})")
        except Exception as e:
            logger.warning(f"BM25 indexing failed: {e}")


# ── CLI entry point ──

def main():
    """CLI: python -m hashmm.pipeline.ingest --input data/staging/paper.pdf"""
    import argparse
    parser = argparse.ArgumentParser(description="HashMM-RAG Document Ingestion")
    parser.add_argument("--input", "-i", required=True, help="File or directory to ingest")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--strategy", default="recursive", choices=["fixed", "recursive", "paragraph"])
    parser.add_argument("--no-kg", action="store_true", help="Skip KG extraction")
    parser.add_argument("--kg-dir", default="data/kg")
    args = parser.parse_args()

    pipeline = IngestPipeline(
        chunk_size=args.chunk_size,
        chunk_strategy=args.strategy,
        kg_dir=args.kg_dir,
    )

    # LLM is OPTIONAL — only used for community summaries (5-10 API calls)
    # Entity extraction uses LOCAL mode (regex + jieba + co-occurrence) — no LLM needed
    llm_ready = False
    try:
        from hashmm.api import database as _db
        _db.init_db()
        models = _db.list_models()
        active = next((m for m in models if m.get("is_default")), models[0] if models else None)
        if active and active.get("base_url") and active.get("api_key"):
            from openai import OpenAI
            db_base = active["base_url"]
            if db_base.rstrip("/").endswith("/v1"):
                db_base = db_base.rstrip("/")[:-3]
            client = OpenAI(api_key=active["api_key"], base_url=db_base, timeout=client_timeout())
            model_name = active.get("model_name", "deepseek-chat")
            pipeline.community_mgr.set_llm(lambda p: client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": p}],
                max_tokens=500, temperature=0.1,
            ).choices[0].message.content or "")
            llm_ready = True
            logger.info(f"LLM for summaries: {model_name} @ {db_base}")
    except Exception as e:
        logger.info(f"DB LLM not available: {e}")

    if not llm_ready:
        import os as _os
        api_key = _os.environ.get("LLM_API_KEY") or _os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = _os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
        model = _os.environ.get("LLM_MODEL", "deepseek-chat")
        if base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/")[:-3]
        if api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url, timeout=client_timeout())
                pipeline.community_mgr.set_llm(lambda p: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": p}],
                    max_tokens=500, temperature=0.1,
                ).choices[0].message.content or "")
                llm_ready = True
                logger.info(f"LLM for summaries: {model} @ {base_url}")
            except Exception as e:
                logger.info(f"Env LLM not available: {e}")

    if not llm_ready:
        logger.info("No LLM configured — entity extraction still works (local mode), community summaries skipped")

    input_path = Path(args.input)
    if input_path.is_dir():
        results = pipeline.ingest_directory(input_path, extract_kg=not args.no_kg)
    else:
        results = [pipeline.ingest_file(input_path, extract_kg=not args.no_kg)]

    print(f"\n{'='*50}")
    print(f"Ingestion complete: {len(results)} documents")
    for r in results:
        status = "成功" if r.status == "success" else "部分" if r.status == "partial" else "失败"
        print(f"  {status} {r.filename}: {r.num_chunks} chunks, "
              f"{r.num_entities} entities, {r.num_relations} rels ({r.elapsed_ms}ms)")
        for err in r.errors:
            print(f"      ⚠ {err}")


if __name__ == "__main__":
    main()
