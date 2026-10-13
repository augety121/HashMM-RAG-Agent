"""图片资源库（V205 P1-5，图2-④口径）：文件系统存二进制 + SQLite 存元数据索引。

入库：上传图片时登记（sha256 幂等；caption 来自视觉模型分析文本，没有也能入库）。
检索：`search_text(q)` 对 caption/tags/filename 做分词重叠打分（轻量、零模型依赖）；
      语义向量检索留给 encoders 就绪后增强（接口已留 embedding 字段）。
消费：REST（/api/images/*）给 UI；kb 检索桥在命中时附带图片提示给 Agent。
铁律：任何失败静默降级，绝不影响上传/检索主链路。HASHMM_IMAGE_STORE=0 可关。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.retrieval.image_store")

_LOCK = threading.Lock()
_IMG_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}


def _enabled() -> bool:
    return (os.environ.get("HASHMM_IMAGE_STORE", "1") or "1") != "0"


def _root() -> Path:
    d = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    p = Path(d).expanduser().resolve()
    (p / "images").mkdir(parents=True, exist_ok=True)
    return p


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(str(_root() / "image_store.db"), timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    cols = {str(row[1]) for row in c.execute("PRAGMA table_info(images)").fetchall()}
    if not cols:
        c.execute("""CREATE TABLE images(
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, sha256 TEXT NOT NULL, filename TEXT,
            caption TEXT, tags TEXT, size INTEGER, path TEXT, created REAL,
            UNIQUE(owner, sha256))""")
    elif "owner" not in cols:
        # Existing rows have no provable owner. Keep them for an administrator-led
        # migration but assign the empty legacy owner so they are invisible to users.
        # DDL is transactional: an interrupted migration leaves the old table intact.
        with c:
            c.execute("DROP TABLE IF EXISTS images_v368")
            c.execute("""CREATE TABLE images_v368(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, sha256 TEXT NOT NULL, filename TEXT,
                caption TEXT, tags TEXT, size INTEGER, path TEXT, created REAL,
                UNIQUE(owner, sha256))""")
            c.execute("""INSERT INTO images_v368(
                id, owner, sha256, filename, caption, tags, size, path, created)
                SELECT id, '', sha256, filename, caption, tags, size, path, created FROM images""")
            c.execute("DROP TABLE images")
            c.execute("ALTER TABLE images_v368 RENAME TO images")
    c.execute("CREATE INDEX IF NOT EXISTS idx_img_sha ON images(sha256)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_img_owner_created ON images(owner, created DESC)")
    c.commit()
    return c


def _tok(s: str) -> set[str]:
    s = (s or "").lower()
    out = set(re.findall(r"[a-z0-9_]{2,}", s))
    for i in range(len(s) - 1):
        if "\u4e00" <= s[i] <= "\u9fff" and "\u4e00" <= s[i + 1] <= "\u9fff":
            out.add(s[i:i + 2])
    return out


def add_image(content: bytes, filename: str, caption: str = "", tags: list[str] | None = None,
              owner: str = "") -> dict | None:
    """登记一张图（sha256 幂等：重复返回既有记录）。返回记录 dict 或 None。"""
    owner = str(owner or "").strip()
    if not _enabled() or not content or not owner:
        return None
    ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
    if ext not in _IMG_EXTS:
        return None
    try:
        sha = hashlib.sha256(content).hexdigest()
        with _LOCK, _db() as c:
            row = c.execute(
                "SELECT id, filename, caption, path FROM images WHERE owner=? AND sha256=?",
                (owner, sha),
            ).fetchone()
            if row:
                # 幂等命中：caption 变充实则补写（首次 analyze=0、后续有解读的场景）
                if caption and len(caption) > len(row[2] or ""):
                    c.execute("UPDATE images SET caption=? WHERE id=?", (caption[:4000], row[0]))
                return {"id": row[0], "filename": row[1], "duplicate": True}
            iid = uuid.uuid4().hex[:16]
            safe = re.sub(r"[^\w.\-]", "_", filename)[:60]
            path = _root() / "images" / f"{iid}__{safe}"
            path.write_bytes(content)
            c.execute("INSERT INTO images(id, owner, sha256, filename, caption, tags, size, path, created) "
                      "VALUES(?,?,?,?,?,?,?,?,?)",
                      (iid, owner, sha, filename, (caption or "")[:4000],
                       json.dumps(tags or [], ensure_ascii=False), len(content), str(path), time.time()))
            return {"id": iid, "filename": filename, "duplicate": False}
    except Exception as e:
        log_suppressed(logger, e, "image_store.add")
        return None


def search_text(query: str, top_k: int = 5, owner: str = "") -> list[dict]:
    """文本搜图：caption/tags/filename 分词重叠打分。失败返回空。"""
    owner = str(owner or "").strip()
    if not _enabled() or not owner:
        return []
    q = _tok(query)
    if not q:
        return []
    try:
        with _db() as c:
            rows = c.execute("SELECT id, filename, caption, tags, created FROM images "
                             "WHERE owner=? ORDER BY created DESC LIMIT 2000", (owner,)).fetchall()
        scored = []
        for iid, fname, cap, tags, created in rows:
            hay = _tok(f"{fname} {cap} {tags}")
            inter = len(q & hay)
            if inter:
                scored.append((inter / (len(q) ** 0.5), {
                    "id": iid, "filename": fname,
                    "caption": (cap or "")[:200], "created": created}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [dict(m, score=round(s, 3)) for s, m in scored[:top_k]]
    except Exception as e:
        log_suppressed(logger, e, "image_store.search")
        return []


def get_image(image_id: str, owner: str | None) -> dict | None:
    try:
        with _db() as c:
            if owner is None:
                r = c.execute("SELECT id, filename, caption, tags, size, path, created FROM images WHERE id=?",
                              (image_id,)).fetchone()
            else:
                owner = str(owner or "").strip()
                if not owner:
                    return None
                r = c.execute("SELECT id, filename, caption, tags, size, path, created FROM images "
                              "WHERE id=? AND owner=?", (image_id, owner)).fetchone()
        if not r:
            return None
        return {"id": r[0], "filename": r[1], "caption": r[2], "tags": r[3],
                "size": r[4], "path": r[5], "created": r[6]}
    except Exception:
        return None


def list_images(limit: int = 50, owner: str = "") -> list[dict]:
    owner = str(owner or "").strip()
    if not owner:
        return []
    try:
        with _db() as c:
            rows = c.execute("SELECT id, filename, caption, size, created FROM images "
                             "WHERE owner=? ORDER BY created DESC LIMIT ?", (owner, int(limit))).fetchall()
        return [{"id": r[0], "filename": r[1], "caption": (r[2] or "")[:120],
                 "size": r[3], "created": r[4]} for r in rows]
    except Exception:
        return []


def remove_image(image_id: str, owner: str | None) -> bool:
    try:
        with _LOCK, _db() as c:
            if owner is None:
                r = c.execute("SELECT path FROM images WHERE id=?", (image_id,)).fetchone()
            else:
                owner = str(owner or "").strip()
                if not owner:
                    return False
                r = c.execute("SELECT path FROM images WHERE id=? AND owner=?", (image_id, owner)).fetchone()
            if not r:
                return False
            if owner is None:
                c.execute("DELETE FROM images WHERE id=?", (image_id,))
            else:
                c.execute("DELETE FROM images WHERE id=? AND owner=?", (image_id, owner))
        try:
            Path(r[0]).unlink(missing_ok=True)
        except Exception:
            pass
        return True
    except Exception as e:
        log_suppressed(logger, e, "image_store.remove")
        return False


def stats(owner: str | None = None) -> dict:
    try:
        with _db() as c:
            if owner is None:
                n, sz = c.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM images").fetchone()
            else:
                owner = str(owner or "").strip()
                if not owner:
                    return {"count": 0, "bytes": 0}
                n, sz = c.execute(
                    "SELECT COUNT(*), COALESCE(SUM(size),0) FROM images WHERE owner=?", (owner,),
                ).fetchone()
        return {"count": int(n), "bytes": int(sz)}
    except Exception:
        return {"count": 0, "bytes": 0}
