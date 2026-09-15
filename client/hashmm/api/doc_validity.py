"""KB 文档时效性（validity）—— 有效期窗口、失效区、归档处理。

企业知识库里文档是有时效性的：到期即"失效"，进入失效区，未来可归档。
本模块在 SQLite 维护每篇文档的时效记录（表 kb_doc_validity）+ 完整变更历史
（kb_doc_validity_history）并写 audit_logs，满足审计与历史追溯要求。
失效/归档的文档会被检索（kb_search）排除，并在"失效区"中展示供复核/归档。

日期统一用 unix 秒（float）存储；None 表示不设上/下限。
文档以 filename 为稳定主键（与 retrieval_pipeline.list_documents 的键一致）。
"""
from __future__ import annotations

import time
import threading

from hashmm.api import database as db
from hashmm.utils import get_logger

logger = get_logger("hashmm.api.doc_validity")

ACTIVE = "active"       # 生效中
EXPIRED = "expired"     # 已失效（过期）
ARCHIVED = "archived"   # 已归档
PENDING = "pending"     # 尚未到生效日期


def effective_status(rec: dict | None, now: float | None = None) -> str:
    """根据记录计算"当前"状态（不修改数据库）。"""
    if not rec:
        return ACTIVE
    if rec.get("status") == ARCHIVED:
        return ARCHIVED
    now = now or time.time()
    eff = rec.get("effective_date")
    exp = rec.get("expiry_date")
    if exp is not None and now > exp:
        return EXPIRED
    if eff is not None and now < eff:
        return PENDING
    return ACTIVE


def _days_left(exp, now: float | None = None):
    if exp is None:
        return None
    now = now or time.time()
    return round((exp - now) / 86400.0, 1)


def set_validity(filename: str, doc_id: str = "", effective_date=None,
                 expiry_date=None, note: str = "", actor: str = "") -> dict:
    """设置/更新某文档的有效期窗口；重算状态并写历史 + 审计。"""
    prev = db.get_doc_validity(filename)
    from_status = effective_status(prev) if prev else ""
    status = effective_status({
        "effective_date": effective_date, "expiry_date": expiry_date, "status": ACTIVE,
    })
    db.upsert_doc_validity(filename, doc_id=doc_id, effective_date=effective_date,
                           expiry_date=expiry_date, status=status, note=note, actor=actor)
    db.add_validity_history(filename, "set_validity", from_status, status,
                            effective_date, expiry_date, note, actor)
    db.audit(actor, actor, "kb_set_validity",
             f"{filename}: eff={_fmt(effective_date)} exp={_fmt(expiry_date)} -> {status}")
    _invalidate_cache()
    return {
        "filename": filename, "status": status,
        "effective_date": effective_date, "expiry_date": expiry_date,
    }


def register_on_ingest(filename: str, doc_id: str = "", effective_date=None,
                       expiry_date=None, actor: str = "system") -> dict:
    """文档入库时登记时效。默认 active/无限期；未显式给新日期时不覆盖已有设置。"""
    existing = db.get_doc_validity(filename)
    if existing and effective_date is None and expiry_date is None:
        return existing
    return set_validity(filename, doc_id=doc_id, effective_date=effective_date,
                        expiry_date=expiry_date, note="ingest", actor=actor)


def archive(filename: str, actor: str = "", note: str = "") -> bool:
    """归档：移出生效/失效，进入归档（仍可检索排除、可复核）。"""
    rec = db.get_doc_validity(filename)
    from_status = effective_status(rec) if rec else ACTIVE
    if not rec:
        db.upsert_doc_validity(filename, status=ARCHIVED, note=note, actor=actor)
    else:
        db.set_doc_status(filename, ARCHIVED, actor)
    db.add_validity_history(filename, "archive", from_status, ARCHIVED, None, None, note, actor)
    db.audit(actor, actor, "kb_archive_doc", filename)
    _invalidate_cache()
    return True


def restore(filename: str, actor: str = "", note: str = "") -> bool:
    """恢复：按其有效期重新判定为 生效/失效/待生效。"""
    rec = db.get_doc_validity(filename)
    from_status = effective_status(rec) if rec else ARCHIVED
    new_status = effective_status({
        "effective_date": rec.get("effective_date") if rec else None,
        "expiry_date": rec.get("expiry_date") if rec else None,
        "status": ACTIVE,
    }) if rec else ACTIVE
    db.set_doc_status(filename, new_status, actor)
    db.add_validity_history(filename, "restore", from_status, new_status, None, None, note, actor)
    db.audit(actor, actor, "kb_restore_doc", f"{filename} -> {new_status}")
    _invalidate_cache()
    return True


def sweep(actor: str = "system") -> dict:
    """把已过有效期、状态仍为 active 的文档标记为 expired（移入失效区），可审计。"""
    now = time.time()
    moved = []
    for rec in db.list_doc_validity():
        if rec.get("status") == ACTIVE:
            exp = rec.get("expiry_date")
            if exp is not None and now > exp:
                db.set_doc_status(rec["filename"], EXPIRED, actor)
                db.add_validity_history(rec["filename"], "auto_expire", ACTIVE, EXPIRED,
                                        rec.get("effective_date"), exp, "sweep", actor)
                moved.append(rec["filename"])
    if moved:
        db.audit(actor, actor, "kb_sweep_expired", f"{len(moved)} docs: " + ", ".join(moved[:10]))
        _invalidate_cache()
    return {"expired": moved, "count": len(moved)}


def annotate(docs: list[dict]) -> list[dict]:
    """给 list_documents() 的结果补上时效字段。"""
    out = []
    now = time.time()
    for d in docs:
        fn = d.get("filename", "")
        rec = db.get_doc_validity(fn)
        d = dict(d)
        d["validity_status"] = effective_status(rec, now) if rec else ACTIVE
        d["effective_date"] = rec.get("effective_date") if rec else None
        d["expiry_date"] = rec.get("expiry_date") if rec else None
        d["days_left"] = _days_left(rec.get("expiry_date"), now) if rec else None
        out.append(d)
    return out


def detail(filename: str) -> dict:
    """单篇时效详情 + 变更历史（审计追溯）。"""
    rec = db.get_doc_validity(filename)
    return {
        "filename": filename,
        "record": rec,
        "status": effective_status(rec),
        "days_left": _days_left(rec.get("expiry_date")) if rec else None,
        "history": db.get_validity_history(filename, limit=200),
    }


# ── 检索过滤（带缓存，避免每次检索都查库）──
_cache = {"set": None, "ts": 0.0}
_cache_lock = threading.Lock()
_CACHE_TTL = 30.0


def _invalidate_cache():
    with _cache_lock:
        _cache["set"] = None
        _cache["ts"] = 0.0


def hidden_filenames() -> set:
    """当前应从检索中排除的文件名集合（失效/归档）。缓存约 30s。"""
    now = time.time()
    with _cache_lock:
        if _cache["set"] is not None and (now - _cache["ts"]) < _CACHE_TTL:
            return _cache["set"]
    s: set = set()
    try:
        for rec in db.list_doc_validity():
            if effective_status(rec, now) in (EXPIRED, ARCHIVED):
                fn = rec.get("filename", "")
                if fn:
                    s.add(fn)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"validity hidden set failed: {e}")
        return set()
    with _cache_lock:
        _cache["set"] = s
        _cache["ts"] = now
    return s


def filter_results(results):
    """从检索结果（含 .filename 属性的对象）中剔除失效/归档文档。绝不抛异常。"""
    try:
        hidden = hidden_filenames()
        if not hidden:
            return results
        return [r for r in results if getattr(r, "filename", None) not in hidden]
    except Exception:  # noqa: BLE001
        return results


def filter_dicts(results, key: str = "filename"):
    """同 filter_results，但用于 list[dict]。"""
    try:
        hidden = hidden_filenames()
        if not hidden:
            return results
        return [r for r in results if r.get(key) not in hidden]
    except Exception:  # noqa: BLE001
        return results


def _fmt(ts):
    if ts is None:
        return "∞"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except Exception:  # noqa: BLE001
        return str(ts)


def parse_date(s):
    """把 'YYYY-MM-DD' 或 epoch（数字/字符串）解析为 epoch float；空/非法返回 None。"""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    try:
        return float(s)  # epoch 字符串
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return time.mktime(time.strptime(s, fmt))
        except ValueError:
            continue
    return None
