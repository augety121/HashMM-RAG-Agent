"""LoHoSearch adapter with frozen/live separation and provenance checks.

HashMM does not bundle or fabricate benchmark questions.  A local dataset is
accepted only with a sidecar manifest and a matching SHA-256.  Scores from an
unverified snapshot are always marked non-comparable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any


OFFICIAL_CASES = 544


def _path() -> Path | None:
    raw = os.environ.get("HASHMM_LOHOSEARCH_DATA", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _manifest_path(data_path: Path) -> Path:
    explicit = os.environ.get("HASHMM_LOHOSEARCH_MANIFEST", "").strip()
    return Path(explicit).expanduser().resolve() if explicit else data_path.with_suffix(data_path.suffix + ".manifest.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def detect() -> dict[str, Any]:
    path = _path()
    if path is None:
        return {"installed": False, "verified": False,
                "hint": "Set HASHMM_LOHOSEARCH_DATA to a frozen LoHoSearch JSON/JSONL snapshot."}
    if not path.is_file():
        return {"installed": False, "verified": False, "hint": f"Dataset not found: {path}"}
    manifest_path = _manifest_path(path)
    if not manifest_path.is_file():
        return {"installed": False, "verified": False,
                "hint": f"Provenance manifest is required: {manifest_path}"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"installed": False, "verified": False, "hint": f"Invalid manifest: {type(exc).__name__}"}
    actual = _sha256(path)
    expected = str(manifest.get("sha256") or "").lower()
    if not expected or actual != expected:
        return {"installed": False, "verified": False, "hint": "Dataset SHA-256 does not match its manifest."}
    official = (
        str(manifest.get("dataset_id") or "").lower() == "lohosearch"
        and int(manifest.get("case_count") or 0) == OFFICIAL_CASES
        and bool(str(manifest.get("source_url") or "").strip())
        and bool(str(manifest.get("snapshot_id") or "").strip())
    )
    return {"installed": True, "verified": True, "official_snapshot": official,
            "path": str(path), "manifest": manifest,
            "hint": "Verified frozen snapshot" if official else "Verified local snapshot; not leaderboard-comparable"}


def _load_cases(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        values = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        raise ValueError("LoHoSearch dataset must be a JSON array or {cases:[...]}")
    return [dict(item) for item in values if isinstance(item, dict)]


def _answers(case: dict[str, Any]) -> list[str]:
    raw = case.get("answers") if isinstance(case.get("answers"), list) else [case.get("answer")]
    return [str(item).strip() for item in raw if str(item or "").strip()]


def _normalise(value: Any) -> str:
    text = str(value or "").casefold().strip()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _exact(prediction: str, gold: list[str]) -> bool:
    value = _normalise(prediction)
    return bool(value) and any(value == _normalise(answer) for answer in gold)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(float(ordered[index]), 3)


def run(adapter, *, mode: str = "smoke", limit: int = 0) -> dict[str, Any]:
    state = detect()
    if not state.get("installed"):
        return {"kind": "official", "skip": True, "score_pct": None, "comparable": False,
                "detail": state.get("hint", "LoHoSearch unavailable")}
    path = Path(state["path"])
    cases = _load_cases(path)
    if not cases:
        return {"kind": "official", "skip": True, "score_pct": None, "comparable": False,
                "detail": "LoHoSearch snapshot contains no cases"}
    mode_limit = {"smoke": 20, "ci": 50, "full": OFFICIAL_CASES}.get(mode, 20)
    requested = min(len(cases), max(1, int(limit or mode_limit)))
    selected = cases[:requested]
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    tool_calls: list[int] = []
    for index, case in enumerate(selected):
        question = str(case.get("question") or case.get("query") or "").strip()
        gold = _answers(case)
        if not question or not gold:
            rows.append({"id": str(case.get("id") or index), "status": "invalid_case", "correct": False})
            continue
        started = time.perf_counter()
        result = adapter.run_task(
            question, [], task_id=f"lohosearch-{case.get('id', index)}",
            max_seconds=int(os.environ.get("HASHMM_LOHOSEARCH_CASE_TIMEOUT", "600")),
        )
        elapsed = max(0.0, time.perf_counter() - started)
        prediction = str(result.get("answer") or "")
        tools = list(result.get("tools_used") or [])
        steps = int(result.get("steps") or len(tools) or 0)
        correct = _exact(prediction, gold)
        latencies.append(elapsed)
        tool_calls.append(steps)
        rows.append({
            "id": str(case.get("id") or index), "domain": str(case.get("domain") or ""),
            "search_scope": str(case.get("search_scope") or ""),
            "logic_complexity": str(case.get("logic_complexity") or ""),
            "correct": correct, "tool_calls": steps, "elapsed_s": round(elapsed, 3),
            "status": str(result.get("status") or "unknown"),
            "prediction_hash": hashlib.sha256(prediction.encode("utf-8")).hexdigest(),
        })
    valid = [row for row in rows if row.get("status") != "invalid_case"]
    passed = sum(bool(row.get("correct")) for row in valid)
    score = round(100.0 * passed / len(valid), 4) if valid else None
    official = bool(state.get("official_snapshot")) and len(cases) == OFFICIAL_CASES
    comparable = official and mode == "full" and requested == OFFICIAL_CASES
    breakdown: dict[str, dict[str, int]] = {}
    for field in ("domain", "search_scope", "logic_complexity"):
        for row in valid:
            key = f"{field}:{row.get(field) or 'unknown'}"
            item = breakdown.setdefault(key, {"passed": 0, "total": 0})
            item["total"] += 1
            item["passed"] += int(bool(row.get("correct")))
    return {
        "kind": "official" if official else "frozen_external",
        "skip": False, "comparable": comparable, "score_pct": score,
        "passed": passed, "total": len(valid), "cases": rows,
        "breakdown": breakdown,
        "metrics": {
            "accuracy": score, "pass_at_1": score,
            "mean_tool_calls": round(statistics.fmean(tool_calls), 3) if tool_calls else 0.0,
            "p50_latency_s": _percentile(latencies, 0.50),
            "p95_latency_s": _percentile(latencies, 0.95),
            "unsupported_claim_rate": None, "evidence_precision": None,
            "note": "Evidence metrics require benchmark trajectories with evidence annotations.",
        },
        "detail": (
            f"LoHoSearch {mode}: {passed}/{len(valid)} exact-match; "
            + ("official full snapshot" if comparable else "not leaderboard-comparable")
        ),
        "snapshot": {"sha256": state["manifest"].get("sha256"),
                     "snapshot_id": state["manifest"].get("snapshot_id"),
                     "source_url": state["manifest"].get("source_url")},
    }


__all__ = ["OFFICIAL_CASES", "detect", "run"]
