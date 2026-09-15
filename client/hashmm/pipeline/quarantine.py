"""入库质量隔离区（V205 P1-6）——fingerprint 管"防重"，这里管"防坏"。

解析质量分（QualityAssessor.overall_score）低于阈值的文档不直接进索引，
而是把**原始文件**暂存到 <DATA_DIR>/quarantine/ 并登记（分数/问题清单），
等人工在 UI 复核：放行（跳过质量闸重新入库）或拒绝（删除暂存）。
阈值 env `HASHMM_QUALITY_MIN`（默认 0.25，只拦真正的坏文档；设 0 关闭闸门）。
铁律：隔离区自身任何失败都放行走原流程（fail-open），绝不因质检模块把入库搞挂。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.pipeline.quarantine")

_LOCK = threading.Lock()


def threshold() -> float:
    try:
        return float(os.environ.get("HASHMM_QUALITY_MIN", "0.25") or "0.25")
    except Exception:
        return 0.25


def _root() -> Path:
    d = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    p = Path(d).expanduser().resolve()
    (p / "quarantine").mkdir(parents=True, exist_ok=True)
    return p


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(str(_root() / "quarantine.db"), timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS quarantine(
        qid TEXT PRIMARY KEY, filename TEXT, score REAL,
        issues TEXT, staged_path TEXT, created REAL)""")
    return c


def stage(src: Path, score: float, issues: list[str]) -> dict | None:
    """把低分文档移入隔离区。返回登记记录；失败返回 None（调用方按放行处理）。"""
    try:
        src = Path(src)
        qid = f"q{int(time.time() * 1000) % 10 ** 10}"
        safe = re.sub(r"[^\w.\-]", "_", src.name)[:80]
        dest = _root() / "quarantine" / f"{qid}__{safe}"
        shutil.copy2(str(src), str(dest))
        rec = {"qid": qid, "filename": src.name, "score": round(float(score), 3),
               "issues": issues[:10], "staged_path": str(dest), "created": time.time()}
        with _LOCK, _db() as c:
            c.execute("INSERT INTO quarantine(qid, filename, score, issues, staged_path, created) "
                      "VALUES(?,?,?,?,?,?)",
                      (qid, rec["filename"], rec["score"],
                       json.dumps(rec["issues"], ensure_ascii=False), rec["staged_path"], rec["created"]))
        logger.info(f"文档进入隔离区: {src.name} score={rec['score']} qid={qid}")
        return rec
    except Exception as e:
        log_suppressed(logger, e, "quarantine.stage")
        return None


def list_items(limit: int = 100) -> list[dict]:
    try:
        with _db() as c:
            rows = c.execute("SELECT qid, filename, score, issues, staged_path, created "
                             "FROM quarantine ORDER BY created DESC LIMIT ?", (int(limit),)).fetchall()
        out = []
        for r in rows:
            try:
                issues = json.loads(r[3] or "[]")
            except Exception:
                issues = []
            out.append({"qid": r[0], "filename": r[1], "score": r[2],
                        "issues": issues, "staged_path": r[4], "created": r[5]})
        return out
    except Exception:
        return []


def get_item(qid: str) -> dict | None:
    for it in list_items(500):
        if it["qid"] == qid:
            return it
    return None


def remove(qid: str, delete_file: bool = True) -> bool:
    try:
        it = get_item(qid)
        with _LOCK, _db() as c:
            c.execute("DELETE FROM quarantine WHERE qid=?", (qid,))
        if delete_file and it:
            try:
                Path(it["staged_path"]).unlink(missing_ok=True)
            except Exception:
                pass
        return True
    except Exception as e:
        log_suppressed(logger, e, "quarantine.remove")
        return False


def stats() -> dict:
    try:
        with _db() as c:
            n = int(c.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0])
        return {"count": n, "threshold": threshold()}
    except Exception:
        return {"count": 0, "threshold": threshold()}
