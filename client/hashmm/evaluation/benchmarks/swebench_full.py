"""SWE-bench Verified full-run 真机对接（V306）。

设计红线（尊重"不改你的环境"）：
  · 本模块只用 Python 标准库（subprocess/json/os/tempfile/pathlib），**不 import 任何第三方包**；
  · SWE-bench 本体装在**独立 venv**（install.sh 建的 ~/hashmm-benchmarks/swebench-venv），
    本模块通过 subprocess 调它，**绝不污染后端主 Python 环境、绝不改任何包版本**；
  · Docker 由 SWE-bench 官方 harness 自己管（每个实例在自己的容器里跑测试），不碰宿主环境。

流程：clone 仓库@base_commit 到会话工作区 → 驱动 HashMM agent 改代码 → git diff 取补丁 →
写成 SWE-bench predictions 格式 → 调官方 harness 在 Docker 里跑 FAIL_TO_PASS/PASS_TO_PASS →
解析 report → Pass@1。

真机对接前（未装 swebench/无 Docker），上层 external.run_swebench 会回退 smoke 或明确 SKIP，
本模块的 run() 也会在缺条件时返回明确状态，**绝不假造分数**。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

MODEL_NAME = "hashmm-agent"

_SELF_CHECK = (
    "现在自查：① 你改的代码是否真的解决了 issue 描述的问题？"
    "② 运行与改动相关的测试，确认没有引入回归。"
    "③ 如果测试或检查暴露问题，继续修改；全部确认后回复『已完成』。"
)


def _sh(cmd: list[str], cwd: str | None = None, timeout: int = 600) -> tuple[int, str, str]:
    """跑一条命令，返回 (rc, stdout, stderr)。永不抛（超时/异常都归一为非零 rc）。"""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


from ._paths import bench_home  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def swebench_python() -> str | None:
    """独立 venv 的 python 路径（install.sh 建）。不存在返回 None。"""
    cand = bench_home() / "swebench-venv" / "bin" / "python"
    return str(cand) if cand.exists() else None


def load_dataset(path: str | None = None, limit: int = 0) -> list[dict]:
    """加载 SWE-bench Verified 实例（本地 JSON/JSONL，install.sh 下载）。缺文件返回 []。"""
    p = Path(path) if path else (bench_home() / "swebench_verified.jsonl")
    if not p.exists():
        # 兼容 .json 数组
        alt = p.with_suffix(".json")
        if alt.exists():
            p = alt
        else:
            return []
    insts: list[dict] = []
    try:
        text = p.read_text(encoding="utf-8")
        if p.suffix == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if line:
                    insts.append(json.loads(line))
        else:
            data = json.loads(text)
            insts = data if isinstance(data, list) else data.get("instances", [])
    except Exception:
        return []
    return insts[:limit] if limit else insts


def prepare_repo(instance: dict, workspace: Path) -> tuple[bool, str]:
    """git clone 指定仓库并 checkout base_commit 到 workspace。返回 (ok, 说明)。"""
    repo = instance.get("repo", "")            # e.g. "django/django"
    base = instance.get("base_commit", "")
    if not repo or not base:
        return False, "实例缺 repo/base_commit"
    url = f"https://github.com/{repo}.git"
    workspace.mkdir(parents=True, exist_ok=True)
    # 浅历史不够 checkout 任意 commit，这里全量 clone（真机可加缓存/镜像加速）
    rc, _, err = _sh(["git", "clone", url, str(workspace)], timeout=1800)
    if rc != 0:
        return False, f"clone 失败：{err[:200]}"
    rc, _, err = _sh(["git", "checkout", "-f", base], cwd=str(workspace), timeout=120)
    if rc != 0:
        return False, f"checkout {base[:10]} 失败：{err[:200]}"
    # 清掉可能的改动，保证干净基线
    _sh(["git", "reset", "--hard", base], cwd=str(workspace), timeout=60)
    _sh(["git", "clean", "-fdx"], cwd=str(workspace), timeout=60)
    return True, "ok"


def extract_patch(workspace: Path) -> str:
    """取 agent 对工作区的改动为统一 diff（相对 base_commit）。"""
    rc, out, _ = _sh(["git", "add", "-A"], cwd=str(workspace), timeout=60)
    rc, out, err = _sh(["git", "diff", "--cached", "--no-color"], cwd=str(workspace), timeout=120)
    return out if rc == 0 else ""


def build_prediction(instance_id: str, patch: str, model: str = MODEL_NAME) -> dict:
    """SWE-bench predictions 条目格式。"""
    return {"instance_id": instance_id, "model_patch": patch, "model_name_or_path": model}


def write_predictions(preds: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for p in preds:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")


def run_official_eval(predictions_path: Path, run_id: str, *,
                      dataset: str = "princeton-nlp/SWE-bench_Verified",
                      swe_py: str | None = None, max_workers: int = 4,
                      timeout: int = 5400) -> tuple[int, str, str]:
    """调官方 harness 在 Docker 里评测。返回 (rc, stdout, stderr)。"""
    py = swe_py or swebench_python()
    if not py:
        return 127, "", "swebench venv 未安装（先跑 install.sh）"
    cmd = [py, "-m", "swebench.harness.run_evaluation",
           "--dataset_name", dataset,
           "--predictions_path", str(predictions_path),
           "--max_workers", str(max_workers),
           "--run_id", run_id]
    return _sh(cmd, timeout=timeout)


def parse_report(report_path: Path) -> dict:
    """解析官方 harness 产出的 report JSON → {resolved, total, pass_rate, resolved_ids}。"""
    if not report_path.exists():
        return {"resolved": 0, "total": 0, "pass_rate": 0.0, "resolved_ids": [], "error": "无 report"}
    try:
        d = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"resolved": 0, "total": 0, "pass_rate": 0.0, "resolved_ids": [], "error": str(e)}
    def _count(value) -> int:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    # 官方 report 的 total_instances 是【整个 Verified 数据集 500 题】，而本次 quick
    # 只提交 3 个 prediction。旧版优先使用 total_instances，因而把 quick 错报成 0/500。
    # Pass@1 的分母必须优先使用 submitted_instances（包括空补丁/错误，不能剔除）。
    resolved_ids = d.get("resolved_ids") or []
    if not isinstance(resolved_ids, list):
        resolved_ids = []
    resolved_n = len(resolved_ids) or _count(
        d.get("resolved_instances", d.get("resolved", 0))
    )
    total = (_count(d.get("submitted_instances"))
             or _count(d.get("completed_instances"))
             or _count(d.get("total"))
             or _count(d.get("total_instances")))
    if not total:
        total = resolved_n
    rate = round(100.0 * resolved_n / max(1, total), 1)
    return {"resolved": resolved_n, "total": total, "pass_rate": rate,
            "resolved_ids": resolved_ids,
            "completed": _count(d.get("completed_instances")),
            "empty_patch": _count(d.get("empty_patch_instances")),
            "errors": _count(d.get("error_instances"))}


def _find_report(run_id: str, predictions_path: Path) -> Path | None:
    """官方 harness 的 report 落点随版本不同，尽力定位。"""
    candidates = [
        Path.cwd() / f"{MODEL_NAME}.{run_id}.json",
        predictions_path.parent / f"{MODEL_NAME}.{run_id}.json",
        Path.cwd() / f"results.{run_id}.json",
    ]
    # 也扫当前目录下带 run_id 的 json
    for pat in (Path.cwd(), predictions_path.parent):
        try:
            for f in pat.glob(f"*{run_id}*.json"):
                candidates.append(f)
        except Exception:
            pass
    for c in candidates:
        if c.exists():
            return c
    return None


def produce_patch_for_instance(adapter, instance: dict, conv_id: str) -> tuple[str, str]:
    """在会话工作区里 clone + 让 agent 改代码 + 取 diff。返回 (patch, note)。"""
    from hashmm.api.database import CONV_FILES_ROOT
    if not callable(getattr(adapter.llm_fn, "call_with_tools", None)):
        return "", "模型适配器缺 call_with_tools；AgentLoop 无法使用文件/终端工具"
    workspace_root = Path(CONV_FILES_ROOT).resolve()
    workspace = (workspace_root / conv_id).resolve()
    if workspace_root not in workspace.parents:
        return "", "非法评测会话目录"
    # preserve_workspace 会保留现场供提取 diff；同一实例重跑前必须清掉旧 clone，避免 clone
    # 因目录非空失败，也避免旧补丁污染本次 Pass@1。
    shutil.rmtree(workspace, ignore_errors=True)
    ok, msg = prepare_repo(instance, workspace)
    if not ok:
        return "", msg
    # Pro 还提供人工整理的 requirements/interface；它们是题面的一部分，不是答案，必须交给 agent。
    # Verified 没有这些字段时行为不变。上限只用于防御异常数据，不截断官方公开集的正常范围。
    sections = [f"=== ISSUE ===\n{str(instance.get('problem_statement') or '')[:20000]}"]
    requirements = str(instance.get("requirements") or "").strip()
    interface = str(instance.get("interface") or "").strip()
    if requirements:
        sections.append(f"=== REQUIREMENTS ===\n{requirements[:12000]}")
    if interface:
        sections.append(f"=== INTERFACE ===\n{interface[:16000]}")
    problem = "\n\n".join(sections)
    repo = instance.get("repo", "")
    query = (
        f"你在一个已 clone 到当前工作区的 Git 仓库（{repo}）里工作。请阅读下面的 issue，"
        f"定位相关源文件并修改代码以解决它；改完不需要解释。用 read_file/str_replace/create_file/"
        f"run_shell 等工具直接改工作区里的文件。\n\n{problem}"
    )
    result: dict = {}
    try:
        # SWE-bench 需要多轮探索、修改和测试；预算与本机 runner 保持一致。
        from hashmm.tools import agent_bench as AB
        task = AB.Task(
            id=conv_id,
            category="swebench",
            turns=[query, _SELF_CHECK],
            requires=set(),
            scorers=[AB.answer_nonempty(min_len=1)],
            max_seconds=int(os.environ.get("HASHMM_SWEBENCH_MAX_SECONDS", "2400")),
            max_iterations=int(os.environ.get("HASHMM_SWEBENCH_MAX_ITERS", "80")),
            max_tool_calls=int(os.environ.get("HASHMM_SWEBENCH_MAX_TOOLCALLS", "200")),
            max_exec_calls=int(os.environ.get("HASHMM_SWEBENCH_MAX_EXECS", "50")),
        )
        # conv_id 必须和预先 clone 的目录一致；否则 agent 会在另一个空目录工作。
        # preserve_workspace 防止 run_task 清空刚 clone 的仓库；bypass 只在本次评测会话生效。
        result = AB.run_task(
            task,
            adapter.llm_fn,
            user=f"bench:{conv_id}",
            conv_id=conv_id,
            preserve_workspace=True,
            permission_mode="bypass",
        )
    except Exception as e:  # noqa: BLE001
        return "", f"agent 执行异常：{type(e).__name__}:{e}"
    patch = extract_patch(workspace)
    if patch.strip():
        return patch, "ok"
    status = str(result.get("status") or "unknown")
    tools = result.get("tools_used") or []
    failures = "; ".join(str(x) for x in (result.get("failures") or []))[:200]
    suffix = f"；{failures}" if failures else ""
    return "", f"空补丁（agent status={status}，tools={len(tools)}，未改动目标仓库{suffix}）"


def run(adapter, *, limit: int = 5, dataset_path: str | None = None) -> dict:
    """SWE-bench Verified full-run 编排。缺条件时返回明确状态，绝不假造分数。"""
    if not _sh(["docker", "info"], timeout=8)[0] == 0:
        return {"kind": "official", "passed": 0, "total": 0, "score_pct": None, "mode": "full", "skip": True,
                "detail": "Docker 不可用，SWE-bench full-run 无法进行（在 AutoDL 开启 Docker 后重试）"}
    swe_py = swebench_python()
    if not swe_py:
        return {"kind": "official", "passed": 0, "total": 0, "score_pct": None, "mode": "full", "skip": True,
                "detail": "swebench 独立 venv 未安装（先在后端跑 benchmarks/install.sh swebench）"}
    # ★ V326：让官方 harness 路径（有 Docker，如 CI）也遵循样本档位（set_forced_sample / HASHMM_BENCH_SAMPLE），
    #   和无 Docker 的 swebench_local 一致。此前只用硬编码 limit，导致 CI 里 --sample 被忽略、恒跑 3 题。
    from .sample_stats import MIN_COMPARABLE_N, resolve_limit
    limit = resolve_limit("swebench", limit)
    instances = load_dataset(dataset_path, limit=limit)
    if not instances:
        return {"kind": "official", "passed": 0, "total": 0, "score_pct": None, "mode": "full", "skip": True,
                "detail": "未找到 SWE-bench Verified 数据集（install.sh 会下载到 $HASHMM_BENCH_HOME）"}

    run_id = f"hashmm_{int(time.time())}"
    preds: list[dict] = []
    notes: list[str] = []

    def _produce(inst: dict) -> tuple[str, str, str]:
        iid = inst.get("instance_id", "")
        conv_id = f"swebench-{iid}".replace("/", "_")[:80]
        patch, note = produce_patch_for_instance(adapter, inst, conv_id)
        return iid, patch, note

    patch_workers = max(1, min(
        len(instances), int(os.environ.get("HASHMM_SWE_PATCH_WORKERS", "1")),
    ))
    if patch_workers > 1:
        with ThreadPoolExecutor(max_workers=patch_workers,
                                thread_name_prefix="swebench-patch") as pool:
            produced = list(pool.map(_produce, instances))
    else:
        produced = [_produce(inst) for inst in instances]
    for iid, patch, note in produced:
        preds.append(build_prediction(iid, patch))
        notes.append(f"{iid}:{note}")

    tmp = Path(tempfile.mkdtemp(prefix="swebench_"))
    pred_path = tmp / "predictions.jsonl"
    write_predictions(preds, pred_path)
    artifact_root = (os.environ.get("HASHMM_BENCH_ARTIFACT_DIR") or "").strip()
    artifact_dir = (Path(artifact_root) / "diagnostics" / "swebench_verified"
                    if artifact_root else None)
    if artifact_dir is not None:
        try:
            write_predictions(preds, artifact_dir / "predictions.jsonl")
        except Exception:  # noqa: BLE001
            pass

    # GitHub hosted runner 内存有限；默认 2 个容器并发，用户在大内存自托管 runner 上可显式调高。
    workers = max(1, int(os.environ.get("HASHMM_SWEBENCH_WORKERS", "2")))
    rc, out, err = run_official_eval(pred_path, run_id, swe_py=swe_py, max_workers=workers)
    report = _find_report(run_id, pred_path)
    if artifact_dir is not None:
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "harness.log").write_text(
                (out or "") + ("\n===== STDERR =====\n" + err if err else ""), encoding="utf-8"
            )
            if report and report.is_file():
                shutil.copy2(report, artifact_dir / "report.json")
        except Exception:  # noqa: BLE001
            pass
    rep = parse_report(report) if report else {
        "resolved": 0, "total": len(preds), "pass_rate": 0.0,
        "completed": 0, "empty_patch": 0, "errors": len(preds),
    }
    non_empty = sum(1 for p in preds if p["model_patch"].strip())
    detail = (f"Pass@1 {rep['pass_rate']}%（resolved {rep['resolved']}/{rep.get('total', len(preds))}）"
              f"；非空补丁 {non_empty}/{len(preds)}")
    if rc != 0 and rep["resolved"] == 0:
        detail += f"；harness rc={rc} {(err or out)[:150]}"
    resolved_ids = set(rep.get("resolved_ids") or [])
    failed_notes = [note for note in notes if not note.endswith(":ok")]
    generation_failed_ids = {note.split(":", 1)[0] for note in failed_notes}
    for pred in preds:
        iid = str(pred["instance_id"])
        if iid not in resolved_ids and iid not in generation_failed_ids:
            failed_notes.append(f"{iid}:官方 Docker 测试未通过")
    cases = [{
        "instance_id": str(pred["instance_id"]),
        "resolved": str(pred["instance_id"]) in resolved_ids,
        "patch_nonempty": bool(pred["model_patch"].strip()),
        "patch_bytes": len(pred["model_patch"].encode("utf-8")),
        "patch_sha256": hashlib.sha256(pred["model_patch"].encode("utf-8")).hexdigest(),
        "generation_note": notes[index].split(":", 1)[-1],
    } for index, pred in enumerate(preds)]
    return {"kind": "official", "passed": rep["resolved"], "total": rep.get("total", len(preds)),
            "score_pct": rep["pass_rate"], "mode": "full", "detail": detail,
            "pipeline_ok": (rep.get("completed", 0) == len(preds)
                            and rep.get("errors", 0) == 0),
            "comparable": len(preds) >= MIN_COMPARABLE_N,
            "resolved_ids": rep.get("resolved_ids", []),
            "breakdown": {
                "本次提交": str(len(preds)),
                "非空补丁": str(non_empty),
                "harness完成": str(rep.get("completed", 0)),
                "空补丁": str(rep.get("empty_patch", 0)),
                "harness错误": str(rep.get("errors", 0)),
                "补丁生成并发": str(patch_workers),
                "Docker评测并发": str(workers),
                "predictions留档": "diagnostics/swebench_verified/predictions.jsonl",
            },
            "fails": failed_notes[:10],
            "cases": cases}
