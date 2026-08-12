"""基准趋势入库与查询（V306）——每次跑分落库，看迭代后分数变化。

用标准库 sqlite3，库文件放 HASHMM_DATA_DIR（与其它 HashMM 数据同处），不引入新依赖。
永不抛错（趋势记录失败不影响跑分主流程）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


def _db_path() -> Path:
    base = os.environ.get("HASHMM_DATA_DIR") or str(Path.home() / ".hashmm")
    d = Path(base)
    d.mkdir(parents=True, exist_ok=True)
    return d / "bench_trend.sqlite"


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(str(_db_path()), timeout=5)
    c.execute("""CREATE TABLE IF NOT EXISTS bench_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, bench_id TEXT, name TEXT, mode TEXT,
        score_pct REAL, passed INTEGER, total INTEGER,
        detail TEXT, meta TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bench_ts ON bench_runs(bench_id, ts)")
    return c


def record_run(result: dict, meta: dict | None = None) -> bool:
    """把一次 run_benchmark 的结果落库。返回是否成功。"""
    try:
        # 只记录【有真实分数】的跑测：跳过的、smoke（score_pct=None）都不入库——
        # 否则趋势图上会出现"管线自检 100%"这种假数据点。
        if not result or result.get("skip") or result.get("score_pct") is None:
            return False
        with _db() as c:
            c.execute(
                "INSERT INTO bench_runs(ts, bench_id, name, mode, score_pct, passed, total, detail, meta) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (time.time(), result.get("id", ""), result.get("name", ""),
                 result.get("mode", ""), float(result.get("score_pct", 0.0)),
                 int(result.get("passed", 0)), int(result.get("total", 0)),
                 str(result.get("detail", ""))[:500],
                 json.dumps({**(meta or {}), "kind": result.get("kind", ""),
                             "comparable": bool(result.get("comparable")),
                             "breakdown": result.get("breakdown") or {},
                             "fails": (result.get("fails") or [])[:8],
                             "cases": (result.get("cases") or [])[:60]},
                            ensure_ascii=False)[:20000]))
        return True
    except Exception:
        return False


def get_trend(bench_id: str | None = None, limit: int = 100) -> list[dict]:
    """取某基准（或全部）的历史跑分，按时间升序（便于画趋势线）。"""
    try:
        with _db() as c:
            if bench_id:
                rows = c.execute(
                    "SELECT ts,bench_id,name,mode,score_pct,passed,total,detail,meta FROM bench_runs "
                    "WHERE bench_id=? ORDER BY ts DESC LIMIT ?", (bench_id, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts,bench_id,name,mode,score_pct,passed,total,detail,meta FROM bench_runs "
                    "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    except Exception:
        return []
    def _has_kind(meta):
        try:
            return bool((json.loads(meta or "{}") or {}).get("kind"))
        except Exception:  # noqa: BLE001
            return False
    out = [{"ts": r[0], "bench_id": r[1], "name": r[2], "mode": r[3],
            "score_pct": r[4], "passed": r[5], "total": r[6], "detail": r[7]}
           for r in rows if _has_kind(r[8])]   # 丢弃无 kind 的老污染记录
    out.reverse()   # 升序
    return out


def purge_polluted() -> int:
    """删除污染数据：没有 kind 的老记录 + 任何 kind=smoke 的记录（smoke 本不该入库）。
    返回删除条数。给 /bench/purge 端点用，让用户一键清掉历史假 100%。"""
    try:
        with _db() as c:
            rows = c.execute("SELECT id, meta FROM bench_runs").fetchall()
            bad = []
            for rid, meta in rows:
                try:
                    k = (json.loads(meta or "{}") or {}).get("kind", "")
                except Exception:  # noqa: BLE001
                    k = ""
                if not k or k == "smoke":
                    bad.append(rid)
            for rid in bad:
                c.execute("DELETE FROM bench_runs WHERE id=?", (rid,))
            return len(bad)
    except Exception:  # noqa: BLE001
        return 0


def latest_runs() -> dict:
    """每个基准最近一次【有真实分数】的跑分，含样本量与模型（供 vs_frontier 对比）。

    返回 {bench_id: {"score_pct","passed","total","name","model"}}。
    只取带 kind 的记录（丢弃老污染），只取与最新同 kind 的那次。
    """
    out: dict = {}
    try:
        with _db() as c:
            ids = [r[0] for r in c.execute("SELECT DISTINCT bench_id FROM bench_runs").fetchall()]
            for bid in ids:
                rows = c.execute(
                    "SELECT score_pct,passed,total,name,meta FROM bench_runs "
                    "WHERE bench_id=? ORDER BY ts DESC LIMIT 20", (bid,)).fetchall()

                def _kind(mj):
                    try:
                        return (json.loads(mj or "{}") or {}).get("kind", "")
                    except Exception:  # noqa: BLE001
                        return ""

                def _is_baseline(mj):
                    try:
                        return bool((json.loads(mj or "{}") or {}).get("baseline"))
                    except Exception:  # noqa: BLE001
                        return False

                # ★ V327：正式榜跳过基线记录——裸模型基线是对照参照，不代表你的系统，
                #   若不过滤，"先跑agent分再跑基线"会把 agent 正式分顶掉。
                rows = [r for r in rows if _kind(r[4]) and not _is_baseline(r[4])]
                if not rows:
                    continue
                r0 = rows[0]
                try:
                    m0 = json.loads(r0[4] or "{}") or {}
                except Exception:  # noqa: BLE001
                    m0 = {}
                # 模型名藏在 breakdown 的"脚手架"行（V315 harness_fingerprint）里
                model = ""
                fp = (m0.get("breakdown") or {}).get("脚手架", "")
                for part in str(fp).split("·"):
                    if "模型=" in part:
                        model = part.split("模型=", 1)[-1].strip()
                out[bid] = {"score_pct": r0[0], "passed": int(r0[1] or 0),
                            "total": int(r0[2] or 0), "name": r0[3] or bid, "model": model,
                            "elapsed_ms": int(m0.get("elapsed_ms", 0) or 0),
                            # V326：透出来源——bench/ingest 回传的分数带 remote=True + source
                            # （如 github_actions），对比表/图据此标注"远程CI"，兑现透明可查。
                            "remote": bool(m0.get("remote")),
                            "source": str(m0.get("source") or "")}
    except Exception:  # noqa: BLE001
        pass
    return out


def latest_baselines() -> dict:
    """每个基准最近一次【裸模型基线】分（HASHMM_BENCH_BASELINE=1 跑出的对照）。

    返回 {bench_id: {"score_pct","passed","total"}}——供对比表在你的 agent 分旁边
    显示"裸模型 X% → 你的agent Y%（+Z）"的消融增益（2026 口径）。
    """
    out: dict = {}
    try:
        with _db() as c:
            ids = [r[0] for r in c.execute("SELECT DISTINCT bench_id FROM bench_runs").fetchall()]
            for bid in ids:
                rows = c.execute(
                    "SELECT score_pct,passed,total,meta FROM bench_runs "
                    "WHERE bench_id=? ORDER BY ts DESC LIMIT 20", (bid,)).fetchall()
                for r in rows:
                    try:
                        m = json.loads(r[3] or "{}") or {}
                    except Exception:  # noqa: BLE001
                        m = {}
                    if m.get("baseline") and r[0] is not None:
                        out[bid] = {"score_pct": r[0], "passed": int(r[1] or 0),
                                    "total": int(r[2] or 0)}
                        break
    except Exception:  # noqa: BLE001
        pass
    return out


def summary() -> dict:
    """每个基准的：最近分、最高分、跑测次数、相对上次的变化（趋势）。"""
    try:
        with _db() as c:
            ids = [r[0] for r in c.execute("SELECT DISTINCT bench_id FROM bench_runs").fetchall()]
            res = {}
            for bid in ids:
                rows = c.execute(
                    "SELECT ts,score_pct,meta FROM bench_runs WHERE bench_id=? ORDER BY ts DESC LIMIT 50",
                    (bid,)).fetchall()
                if not rows:
                    continue
                # V306 修趋势污染：老版本把 smoke 的"100%"也入了库、且没存 kind。
                # 现在：① 丢掉 kind 为空的老污染记录；② 只在同 kind 内比较。
                def _kind(mj):
                    try:
                        return (json.loads(mj or "{}") or {}).get("kind", "")
                    except Exception:  # noqa: BLE001
                        return ""
                rows = [r for r in rows if _kind(r[2])]        # 丢弃无 kind 的老记录
                if not rows:
                    continue
                k_latest = _kind(rows[0][2])
                rows = [r for r in rows if _kind(r[2]) == k_latest]
                scores = [r[1] for r in rows]
                try:
                    m0 = json.loads(rows[0][2] or "{}")
                except Exception:  # noqa: BLE001
                    m0 = {}
                latest = scores[0]
                prev = scores[1] if len(scores) > 1 else None
                delta = round(latest - prev, 1) if prev is not None else None
                res[bid] = {
                    "latest": latest, "best": max(scores), "runs": len(scores),
                    "prev": prev, "delta": delta,
                    "kind": m0.get("kind", ""), "comparable": bool(m0.get("comparable")),
                    "breakdown": m0.get("breakdown") or {},
                    "fails": m0.get("fails") or [],
                    "cases": m0.get("cases") or [],
                    "trend": ("↑" if (delta or 0) > 0 else "↓" if (delta or 0) < 0 else "→"),
                }
            return res
    except Exception:
        return {}
