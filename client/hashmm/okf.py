"""Open Knowledge Format (OKF) v0.2 compatible knowledge packs.

The implementation deliberately separates *inspection* from *ingestion*:

1. uploaded ZIP bytes are validated and staged for the authenticated owner;
2. the user reviews warnings, provenance and trust signals;
3. only an explicit apply call writes concepts and indexes their Markdown.

OKF computation/attestation fields are preserved as data.  HashMM never
executes commands, code or URLs found in a knowledge pack.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from hashmm.api import database as db

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_UNPACKED_BYTES = 60 * 1024 * 1024
MAX_FILES = 500
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_CONCEPTS = 350
PREVIEW_TTL_SECONDS = 30 * 60

_RESERVED = {"index.md", "log.md"}
_PREVIEW_ROOT = db.DATA_ROOT / "okf" / "previews"
_INDEX_LOCK = threading.RLock()
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_MD_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _ensure_tables() -> None:
    with db._conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS okf_packs (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                manifest_json TEXT NOT NULL DEFAULT '{}',
                concept_count INTEGER NOT NULL DEFAULT 0,
                warning_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_okf_packs_owner
                ON okf_packs(owner_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS okf_concepts (
                id TEXT PRIMARY KEY,
                pack_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                path TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                trust TEXT NOT NULL DEFAULT 'unverified',
                status TEXT NOT NULL DEFAULT '',
                stale_after TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                content TEXT NOT NULL DEFAULT '',
                content_sha256 TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(pack_id, path),
                FOREIGN KEY(pack_id) REFERENCES okf_packs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_okf_concepts_owner
                ON okf_concepts(owner_id, pack_id);
            CREATE TABLE IF NOT EXISTS okf_links (
                pack_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                target_path TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'links',
                PRIMARY KEY(pack_id, source_path, target_path, relation),
                FOREIGN KEY(pack_id) REFERENCES okf_packs(id) ON DELETE CASCADE
            );
            """
        )


def _owner_dir(owner_id: str) -> Path:
    digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]
    path = _PREVIEW_ROOT / digest
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_member(info: zipfile.ZipInfo) -> str:
    raw = str(info.filename or "").replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("知识包包含非法绝对路径")
    parts = PurePosixPath(raw).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("知识包包含路径穿越")
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        raise ValueError("知识包不能包含符号链接")
    if info.flag_bits & 0x1:
        raise ValueError("知识包不能包含加密文件")
    return "/".join(parts)


def _frontmatter(text: str, path: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        loaded = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} 的 YAML 元数据无法解析") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} 的 YAML 元数据必须是对象")
    clean = {str(key): value for key, value in loaded.items()}
    return clean, text[match.end():].lstrip()


def _trust(metadata: dict[str, Any]) -> str:
    verified = metadata.get("verified")
    if isinstance(verified, dict):
        actor = str(
            verified.get("by") or verified.get("reviewer")
            or verified.get("actor") or ""
        ).lower()
        if actor and not actor.startswith(("machine", "model", "agent", "ci")):
            return "human-reviewed"
        return "machine-confirmed"
    if isinstance(verified, str) and verified.strip():
        lowered = verified.lower()
        if any(token in lowered for token in ("human", "reviewer", "owner", "人工")):
            return "human-reviewed"
        return "machine-confirmed"
    if verified is True or metadata.get("generated") or metadata.get("attestation"):
        return "machine-confirmed"
    return "unverified"


def _links(metadata: dict[str, Any], body: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    declared = metadata.get("links")
    if isinstance(declared, list):
        for item in declared:
            if isinstance(item, str):
                rows.append({"target": item, "relation": "links"})
            elif isinstance(item, dict):
                target = str(
                    item.get("target") or item.get("path") or item.get("to") or ""
                ).strip()
                if target:
                    rows.append({
                        "target": target,
                        "relation": str(item.get("type") or item.get("relation") or "links")[:80],
                    })
    elif isinstance(declared, dict):
        for relation, targets in declared.items():
            values = targets if isinstance(targets, list) else [targets]
            for target in values:
                if isinstance(target, str) and target.strip():
                    rows.append({"target": target.strip(), "relation": str(relation)[:80]})
    for target in _MD_LINK.findall(body):
        target = target.strip().split("#", 1)[0]
        if target.lower().endswith(".md") and not target.startswith(("http://", "https://")):
            rows.append({"target": target, "relation": "markdown"})
    dedup: dict[tuple[str, str], dict[str, str]] = {}
    for item in rows:
        target = item["target"].replace("\\", "/").lstrip("./")
        if target:
            dedup[(target, item["relation"])] = {
                "target": target,
                "relation": item["relation"],
            }
    return list(dedup.values())[:200]


def _title(metadata: dict[str, Any], body: str, path: str) -> str:
    explicit = str(metadata.get("title") or metadata.get("name") or "").strip()
    if explicit:
        return explicit[:240]
    heading = re.search(r"(?m)^#\s+(.+?)\s*$", body)
    if heading:
        return heading.group(1).strip()[:240]
    return PurePosixPath(path).stem[:240]


def inspect_archive(owner_id: str, filename: str, payload: bytes) -> dict[str, Any]:
    """Validate and stage an OKF ZIP without mutating the knowledge base."""
    owner = str(owner_id or "").strip()
    if not owner:
        raise ValueError("用户身份无效")
    if not payload:
        raise ValueError("知识包为空")
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("知识包超过 25 MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError("文件不是有效的 ZIP 知识包") from exc
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if len(infos) > MAX_FILES:
        raise ValueError("知识包文件过多")
    if sum(max(0, info.file_size) for info in infos) > MAX_UNPACKED_BYTES:
        raise ValueError("知识包解压后超过 60 MB")

    entries: dict[str, bytes] = {}
    for info in infos:
        path = _safe_member(info)
        if info.file_size > MAX_MARKDOWN_BYTES and path.lower().endswith(".md"):
            raise ValueError(f"{path} 超过单文件 2 MB 限制")
        if path.lower().endswith(".md"):
            raw = archive.read(info)
            if len(raw) != info.file_size:
                raise ValueError(f"{path} 读取不完整")
            entries[path] = raw
    if not entries:
        raise ValueError("知识包中没有 Markdown 文件")

    concepts: list[dict[str, Any]] = []
    reserved: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    paths = set(entries)
    for path in sorted(entries):
        try:
            text = entries[path].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{path} 必须使用 UTF-8 编码") from exc
        metadata, body = _frontmatter(text, path)
        base = PurePosixPath(path).name.lower()
        if base in _RESERVED:
            reserved[base] = {"path": path, "metadata": metadata, "content": body}
            continue
        concept_type = str(metadata.get("type") or "").strip()
        if not concept_type:
            raise ValueError(f"{path} 缺少非空 type")
        links = _links(metadata, body)
        for link in links:
            source_parent = PurePosixPath(path).parent
            normalized = str((source_parent / link["target"]).as_posix()).lstrip("./")
            link["resolved_target"] = normalized
            if normalized not in paths:
                warnings.append({
                    "code": "broken_link",
                    "path": path,
                    "message": f"链接目标不存在：{link['target']}",
                })
        if not metadata.get("sources") and not metadata.get("citations"):
            warnings.append({
                "code": "missing_provenance",
                "path": path,
                "message": "未声明 sources 或 citations；仍可导入，但可信度较低。",
            })
        concepts.append({
            "path": path,
            "type": concept_type[:120],
            "title": _title(metadata, body, path),
            "trust": _trust(metadata),
            "status": str(metadata.get("status") or "")[:80],
            "stale_after": str(metadata.get("stale_after") or "")[:80],
            "metadata": metadata,
            "content": body,
            "sha256": hashlib.sha256(entries[path]).hexdigest(),
            "links": links,
        })
    if not concepts:
        raise ValueError("知识包没有可导入的概念文件")
    if len(concepts) > MAX_CONCEPTS:
        raise ValueError("知识包概念数超过 350 个")

    index = reserved.get("index.md") or {}
    index_meta = index.get("metadata") if isinstance(index.get("metadata"), dict) else {}
    pack_name = str(
        (index_meta or {}).get("title") or (index_meta or {}).get("name")
        or Path(filename or "knowledge-pack").stem
    ).strip()[:160]
    pack_description = str((index_meta or {}).get("description") or "").strip()[:1000]
    preview_id = uuid.uuid4().hex
    staged = {
        "preview_id": preview_id,
        "owner_id": owner,
        "source_name": Path(filename or "knowledge-pack.zip").name[:240],
        "name": pack_name or "知识包",
        "description": pack_description,
        "created_at": time.time(),
        "expires_at": time.time() + PREVIEW_TTL_SECONDS,
        "concepts": concepts,
        "reserved": reserved,
        "warnings": warnings,
        "archive_sha256": hashlib.sha256(payload).hexdigest(),
    }
    stage_path = _owner_dir(owner) / f"{preview_id}.json"
    stage_path.write_text(
        json.dumps(staged, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return public_preview(staged)


def public_preview(staged: dict[str, Any]) -> dict[str, Any]:
    concepts = list(staged.get("concepts") or [])
    trusts = {"human-reviewed": 0, "machine-confirmed": 0, "unverified": 0}
    for concept in concepts:
        trust = str(concept.get("trust") or "unverified")
        trusts[trust if trust in trusts else "unverified"] += 1
    return {
        "preview_id": staged["preview_id"],
        "name": staged["name"],
        "description": staged.get("description") or "",
        "source_name": staged.get("source_name") or "",
        "concept_count": len(concepts),
        "warning_count": len(staged.get("warnings") or []),
        "warnings": list(staged.get("warnings") or [])[:100],
        "trust": trusts,
        "types": sorted({str(item.get("type") or "") for item in concepts if item.get("type")}),
        "concepts": [
            {
                "path": item["path"],
                "type": item["type"],
                "title": item["title"],
                "trust": item["trust"],
                "status": item.get("status") or "",
                "stale_after": item.get("stale_after") or "",
                "link_count": len(item.get("links") or []),
            }
            for item in concepts[:MAX_CONCEPTS]
        ],
        "expires_at": staged["expires_at"],
        "notice": "预检不会写入资料库；计算与证明字段仅作为元数据保存，不会执行。",
    }


def _load_preview(owner_id: str, preview_id: str) -> tuple[dict[str, Any], Path]:
    token = str(preview_id or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise LookupError("预检记录不存在或已过期")
    path = _owner_dir(owner_id) / f"{token}.json"
    if not path.is_file():
        raise LookupError("预检记录不存在或已过期")
    try:
        staged = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError("预检记录不可用，请重新上传") from exc
    if staged.get("owner_id") != owner_id or float(staged.get("expires_at") or 0) < time.time():
        path.unlink(missing_ok=True)
        raise LookupError("预检记录不存在或已过期")
    return staged, path


def _chunk(text: str, limit: int = 1200) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > limit:
            pieces = [paragraph[i:i + limit] for i in range(0, len(paragraph), limit)]
        else:
            pieces = [paragraph]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if current and len(candidate) > limit:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


def _encode_texts(texts: list[str]):
    """Late import keeps OKF validation usable on API-only/CPU test hosts."""
    from hashmm.encoder_pool import EncoderPool

    return EncoderPool.encode_texts(texts)


def apply_preview(owner_id: str, preview_id: str) -> dict[str, Any]:
    staged, stage_path = _load_preview(owner_id, preview_id)
    _ensure_tables()
    pack_id = uuid.uuid4().hex
    now = time.time()
    concepts = list(staged["concepts"])
    texts: list[str] = []
    metadata: list[dict[str, Any]] = []
    for concept in concepts:
        for index, part in enumerate(_chunk(str(concept.get("content") or ""))):
            texts.append(part)
            metadata.append({
                "doc_id": f"okf:{pack_id}:{concept['path']}",
                "chunk_id": f"okf:{pack_id}:{concept['path']}#{index}",
                "filename": f"{staged['name']} / {concept['title']}",
                "section": concept["path"],
                "page": -1,
                "text": part,
                "source_type": "okf",
                "owner_id": owner_id,
                "workspace_id": "",
                "okf_pack_id": pack_id,
                "okf_type": concept["type"],
                "okf_trust": concept["trust"],
                "okf_status": concept.get("status") or "",
                "okf_stale_after": concept.get("stale_after") or "",
            })
    if not texts:
        raise ValueError("知识包没有可索引内容")

    # A single process-wide lock keeps dense/sparse metadata parallel.  Unknown
    # index failures are surfaced; we reload persisted indexes instead of
    # continuing with a partially mutated in-memory corpus.
    with _INDEX_LOCK:
        try:
            from hashmm.retriever_bridge import get_pipeline, init_retriever

            pipeline = get_pipeline()
            if pipeline is None:
                raise RuntimeError("检索服务尚未就绪")
            embeddings = _encode_texts(texts)
            pipeline.vector_index.add(embeddings, metadata)
            pipeline.bm25_index.add(texts, metadata)
            pipeline.vector_index.save()
            pipeline.bm25_index.save()
        except Exception:
            try:
                init_retriever()
            except Exception:
                pass
            raise

    with db._conn() as conn:
        conn.execute(
            "INSERT INTO okf_packs"
            "(id,owner_id,name,description,source_name,manifest_json,"
            "concept_count,warning_count,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                pack_id,
                owner_id,
                staged["name"],
                staged.get("description") or "",
                staged.get("source_name") or "",
                json.dumps({
                    "archive_sha256": staged.get("archive_sha256"),
                    "reserved": staged.get("reserved") or {},
                    "format": "OKF",
                    "format_version": "0.2-compatible",
                }, ensure_ascii=False, separators=(",", ":")),
                len(concepts),
                len(staged.get("warnings") or []),
                now,
                now,
            ),
        )
        for concept in concepts:
            concept_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO okf_concepts"
                "(id,pack_id,owner_id,path,type,title,trust,status,stale_after,"
                "metadata_json,content,content_sha256,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    concept_id,
                    pack_id,
                    owner_id,
                    concept["path"],
                    concept["type"],
                    concept["title"],
                    concept["trust"],
                    concept.get("status") or "",
                    concept.get("stale_after") or "",
                    json.dumps(concept.get("metadata") or {}, ensure_ascii=False, separators=(",", ":")),
                    concept.get("content") or "",
                    concept["sha256"],
                    now,
                ),
            )
            for link in concept.get("links") or []:
                conn.execute(
                    "INSERT OR IGNORE INTO okf_links"
                    "(pack_id,owner_id,source_path,target_path,relation) VALUES(?,?,?,?,?)",
                    (
                        pack_id,
                        owner_id,
                        concept["path"],
                        link.get("resolved_target") or link.get("target") or "",
                        link.get("relation") or "links",
                    ),
                )
    stage_path.unlink(missing_ok=True)
    return {
        "id": pack_id,
        "name": staged["name"],
        "concept_count": len(concepts),
        "warning_count": len(staged.get("warnings") or []),
        "indexed_chunks": len(texts),
    }


def list_packs(owner_id: str) -> list[dict[str, Any]]:
    _ensure_tables()
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id,name,description,source_name,concept_count,warning_count,"
            "created_at,updated_at FROM okf_packs WHERE owner_id=? "
            "ORDER BY updated_at DESC",
            (owner_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def export_pack(owner_id: str, pack_id: str) -> tuple[str, bytes]:
    _ensure_tables()
    with db._conn() as conn:
        pack = conn.execute(
            "SELECT id,name,description,source_name,manifest_json FROM okf_packs "
            "WHERE id=? AND owner_id=?",
            (pack_id, owner_id),
        ).fetchone()
        if not pack:
            raise LookupError("知识包不存在")
        concepts = conn.execute(
            "SELECT path,type,title,trust,status,stale_after,metadata_json,content "
            "FROM okf_concepts WHERE pack_id=? AND owner_id=? ORDER BY path",
            (pack_id, owner_id),
        ).fetchall()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        index_meta = {
            "type": "collection",
            "title": pack["name"],
            "description": pack["description"],
            "generated": {
                "by": "HashMM",
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
        archive.writestr(
            "index.md",
            "---\n" + yaml.safe_dump(index_meta, allow_unicode=True, sort_keys=False)
            + "---\n\n# " + str(pack["name"]) + "\n",
        )
        for row in concepts:
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except ValueError:
                meta = {}
            meta["type"] = row["type"]
            if row["title"]:
                meta["title"] = row["title"]
            if row["status"]:
                meta["status"] = row["status"]
            if row["stale_after"]:
                meta["stale_after"] = row["stale_after"]
            content = (
                "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
                + "---\n\n" + str(row["content"] or "")
            )
            archive.writestr(str(row["path"]), content)
    safe_name = re.sub(r'[<>:"/\\|?*]+', "-", str(pack["name"] or "knowledge-pack")).strip()
    return f"{safe_name or 'knowledge-pack'}.okf.zip", output.getvalue()
