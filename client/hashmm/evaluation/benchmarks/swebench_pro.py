"""SWE-bench Pro public split：生成补丁并调用 Scale 官方 Docker evaluator。

Pro 不是 SWE-bench Verified 的同构 Python/pytest 数据集。它包含多语言仓库，官方判分
依赖逐实例 Docker 镜像、run_scripts 和 parser.py；缺任一项时必须明确跳过，不能退回
Verified 的本机 venv runner 后仍声称是官方成绩。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from ._paths import bench_home
from .sample_stats import MIN_COMPARABLE_N, resolve_limit
from .swebench_full import _sh, produce_patch_for_instance

_REQUIRED_FIELDS = {
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "fail_to_pass",
    "pass_to_pass",
    "before_repo_set_cmd",
    "selected_test_files_to_run",
}


def dataset_path() -> Path:
    return bench_home() / "swebench_pro.jsonl"


def evaluator_root() -> Path:
    return bench_home() / "swebench-pro-eval"


def evaluator_python() -> Path:
    return bench_home() / "swebench-venv" / "bin" / "python"


def _load_instances(path: Path, limit: int) -> tuple[list[dict], str]:
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                missing = sorted(_REQUIRED_FIELDS.difference(row))
                if missing:
                    return [], f"数据第 {line_no} 行缺官方字段：{', '.join(missing)}"
                rows.append(row)
                if limit and len(rows) >= limit:
                    break
    except Exception as e:  # noqa: BLE001
        return [], f"读取数据失败：{type(e).__name__}: {e}"
    if not rows:
        return [], "数据集为空"
    return rows, ""


def detect() -> dict:
    missing: list[str] = []
    if not dataset_path().is_file():
        missing.append("swebench_pro.jsonl")
    root = evaluator_root()
    if not (root / "swe_bench_pro_eval.py").is_file():
        missing.append("官方 swe_bench_pro_eval.py")
    if not (root / "run_scripts").is_dir():
        missing.append("官方 run_scripts")
    if not (root / "dockerfiles" / "base_dockerfile").is_dir():
        missing.append("官方 base_dockerfile")
    if not (root / "dockerfiles" / "instance_dockerfile").is_dir():
        missing.append("官方 instance_dockerfile")
    if not (root / "helper_code" / "image_uri.py").is_file():
        missing.append("官方 image_uri helper")
    if not evaluator_python().is_file():
        missing.append("swebench-venv")
    return {
        "installed": not missing,
        "hint": "" if not missing else (
            "SWE-bench Pro(public) 未完整安装（缺 " + "、".join(missing)
            + "）：运行 hashmm/evaluation/benchmarks/install.sh swebench_pro"
        ),
    }


def _skip(detail: str) -> dict:
    return {
        "kind": "official",
        "passed": 0,
        "total": 0,
        "score_pct": None,
        "mode": "full",
        "skip": True,
        "comparable": False,
        "detail": detail,
    }


def run(adapter, *, limit: int = 3) -> dict:
    installed = detect()
    if not installed["installed"]:
        return _skip(installed["hint"])
    if _sh(["docker", "info"], timeout=10)[0] != 0:
        return _skip("Docker 不可用；SWE-bench Pro 官方 evaluator 必须运行逐实例 Docker 镜像")

    limit = resolve_limit("swebench_pro", limit)
    instances, data_error = _load_instances(dataset_path(), limit)
    if data_error:
        return _skip(data_error + "；请重新运行 install.sh swebench_pro 修复旧缓存")

    predictions: list[dict] = []
    notes: list[str] = []

    def _produce(inst: dict) -> tuple[str, str, str]:
        iid = str(inst["instance_id"])
        conv_id = f"swebench-pro-{iid}".replace("/", "_")[:100]
        patch, note = produce_patch_for_instance(adapter, inst, conv_id)
        return iid, patch, note

    patch_workers = max(1, min(
        len(instances), int(os.environ.get("HASHMM_SWE_PATCH_WORKERS", "1")),
    ))
    if patch_workers > 1:
        with ThreadPoolExecutor(max_workers=patch_workers,
                                thread_name_prefix="swebench-pro-patch") as pool:
            produced = list(pool.map(_produce, instances))
    else:
        produced = [_produce(inst) for inst in instances]
    for iid, patch, note in produced:
        predictions.append({"instance_id": iid, "patch": patch, "prefix": "hashmm"})
        notes.append(f"{iid}:{note}")

    root = evaluator_root()
    with tempfile.TemporaryDirectory(prefix="swebench_pro_") as td:
        temp = Path(td)
        patch_path = temp / "patches.json"
        output_dir = temp / "official-output"
        output_dir.mkdir()
        patch_path.write_text(json.dumps(predictions, ensure_ascii=False), encoding="utf-8")
        artifact_root = (os.environ.get("HASHMM_BENCH_ARTIFACT_DIR") or "").strip()
        artifact_dir = (Path(artifact_root) / "diagnostics" / "swebench_pro"
                        if artifact_root else None)
        if artifact_dir is not None:
            try:
                artifact_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(patch_path, artifact_dir / "predictions.json")
            except Exception:  # noqa: BLE001
                pass

        workers = max(1, min(
            len(predictions),
            int(os.environ.get("HASHMM_SWEBENCH_PRO_WORKERS", "2")),
        ))
        timeout = int(os.environ.get("HASHMM_SWEBENCH_PRO_EVAL_TIMEOUT", "7200"))
        cmd = [
            str(evaluator_python()),
            str(root / "swe_bench_pro_eval.py"),
            "--raw_sample_path", str(dataset_path()),
            "--patch_path", str(patch_path),
            "--output_dir", str(output_dir),
            "--scripts_dir", str(root / "run_scripts"),
            "--num_workers", str(workers),
            "--dockerhub_username", "jefzda",
            "--use_local_docker",
        ]
        rc, stdout, stderr = _sh(cmd, cwd=str(root), timeout=timeout)
        result_path = output_dir / "eval_results.json"
        if artifact_dir is not None:
            try:
                (artifact_dir / "evaluator.log").write_text(
                    (stdout or "") + ("\n===== STDERR =====\n" + stderr if stderr else ""),
                    encoding="utf-8",
                )
                if result_path.is_file():
                    shutil.copy2(result_path, artifact_dir / "eval_results.json")
            except Exception:  # noqa: BLE001
                pass
        if not result_path.is_file():
            evidence = (stderr or stdout or "无 evaluator 输出").strip().replace("\n", " ")[:300]
            return _skip(f"SWE-bench Pro 官方 evaluator 未产出结果（rc={rc}）：{evidence}")
        try:
            raw_results = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            return _skip(f"SWE-bench Pro 官方结果无法解析：{type(e).__name__}: {e}")
        if not isinstance(raw_results, dict):
            return _skip("SWE-bench Pro 官方结果结构错误：eval_results.json 不是对象")

    ids = [p["instance_id"] for p in predictions]
    passed_ids = [iid for iid in ids if raw_results.get(iid) is True]
    passed = len(passed_ids)
    total = len(ids)  # 缺结果或 evaluator 异常的已提交实例仍计入分母
    nonempty = sum(bool(p["patch"].strip()) for p in predictions)
    score = round(100.0 * passed / total, 1)
    detail = (
        f"官方 Scale Docker evaluator：resolved {passed}/{total}，Pass@1 {score}%；"
        f"非空补丁 {nonempty}/{total}"
    )
    if rc != 0:
        detail += f"；evaluator rc={rc}（已按实际产出的 eval_results.json 计分）"
    failed_notes = [n for n in notes if not n.endswith(":ok")]
    for iid in ids:
        if raw_results.get(iid) is not True and len(failed_notes) < 5:
            failed_notes.append(f"{iid}:官方 Docker 测试未通过或未产出")
    cases = [{
        "instance_id": str(pred["instance_id"]),
        "resolved": raw_results.get(str(pred["instance_id"])) is True,
        "official_result_present": str(pred["instance_id"]) in raw_results,
        "patch_nonempty": bool(pred["patch"].strip()),
        "patch_bytes": len(pred["patch"].encode("utf-8")),
        "patch_sha256": hashlib.sha256(pred["patch"].encode("utf-8")).hexdigest(),
        "generation_note": notes[index].split(":", 1)[-1],
    } for index, pred in enumerate(predictions)]
    return {
        "kind": "official",
        "passed": passed,
        "total": total,
        "score_pct": score,
        "mode": "full",
        "skip": False,
        "pipeline_ok": len(raw_results) == total,
        "comparable": total >= MIN_COMPARABLE_N,
        "detail": detail,
        "resolved_ids": passed_ids[:50],
        "breakdown": {
            "本次提交": str(total),
            "非空补丁": str(nonempty),
            "官方 evaluator 结果": str(len(raw_results)),
            "补丁生成并发": str(patch_workers),
            "Docker评测并发": str(workers),
            "predictions留档": "diagnostics/swebench_pro/predictions.json",
            "评测方式": "Scale run_scripts + 逐实例 Docker 镜像 + F2P/P2P",
        },
        "fails": failed_notes[:5],
        "cases": cases,
    }
