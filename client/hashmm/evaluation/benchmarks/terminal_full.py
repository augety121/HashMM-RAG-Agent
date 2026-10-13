"""Terminal-bench full-run 真机对接（V306）。

与 swebench_full 同样的红线：
  · **纯标准库**（subprocess/json/os/pathlib），不 import 任何第三方包；
  · Terminal-bench 装在**独立 venv**（install.sh 建），本模块通过 subprocess 调它 ——
    **绝不动你后端主 Python 环境、绝不改任何包版本**；
  · 缺 Docker / 缺仓库 / 缺 venv → 明确 SKIP，**绝不假造分数**。

Terminal-bench 的每个任务都在自己的 Docker 容器里跑（这是它隔离终端环境的方式），
因此 **full-run 强依赖 Docker**。AutoDL 的容器实例通常不提供宿主 Docker（无法嵌套），
这时本模块会如实 SKIP 并告诉你原因与替代方案（见 README「Docker 不可用怎么办」）。

对接方式：Terminal-bench 支持自定义 agent（`tb run --agent-import-path <import-path>`）。仓库随附
`scripts/hashmm_tb_agent.py` 适配器（调用 OpenAI 兼容模型端点驱动任务），本模块负责拉起 `tb run`
并解析它产出的 results.json → 任务成功率。
"""
from __future__ import annotations

from collections import Counter
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def _sh(cmd: list[str], cwd: str | None = None, timeout: int = 600,
        env: dict[str, str] | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


from ._paths import bench_home  # 统一路径：AutoDL 上默认落 /root/autodl-tmp/hashmm-benchmarks


def has_docker() -> bool:
    if not shutil.which("docker"):
        return False
    return _sh(["docker", "info"], timeout=8)[0] == 0


def tb_python() -> str | None:
    """Terminal-bench 独立 venv 的 python（install.sh 建）。"""
    cand = bench_home() / "tb-venv" / "bin" / "python"
    return str(cand) if cand.exists() else None


def repo_dir() -> Path:
    return bench_home() / "terminal-bench"


def detect() -> dict:
    docker = has_docker()
    repo = repo_dir().is_dir()
    venv = tb_python() is not None
    ok = docker and repo and venv
    if ok:
        hint = ""
    elif not docker:
        hint = ("Docker 不可用 —— Terminal-bench 每个任务都在容器里跑，没有 Docker 就无法评测。"
                "AutoDL 容器实例通常不提供 Docker（见 README「Docker 不可用怎么办」的三个替代方案）。")
    elif not repo:
        hint = "未 clone terminal-bench：运行 benchmarks/install.sh terminal"
    else:
        hint = "terminal-bench 独立 venv 未建：运行 benchmarks/install.sh terminal"
    return {"installed": ok, "docker": docker, "repo": repo, "venv": venv, "hint": hint}


def parse_results(results_path: Path) -> dict:
    """解析官方聚合 results.json，并保留逐题 failure mode/test/token 诊断。"""
    if not results_path.exists():
        return {"resolved": 0, "total": 0, "pass_rate": 0.0, "error": "无结果文件"}
    try:
        d = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"resolved": 0, "total": 0, "pass_rate": 0.0, "error": str(e)}

    # 形状 A：{"results": [{"task_id":..., "is_resolved": true}, ...]}
    results = d.get("results") if isinstance(d, dict) else (d if isinstance(d, list) else None)
    if isinstance(results, list) and results:
        cases: list[dict] = []
        failure_modes: Counter[str] = Counter()
        input_tokens = 0
        output_tokens = 0
        unknown_outcomes = 0
        passed_statuses = {"resolved", "passed", "success"}
        failed_statuses = {"unresolved", "failed", "failure", "error"}
        for index, row in enumerate(results):
            if not isinstance(row, dict):
                unknown_outcomes += 1
                cases.append({"task_id": f"unknown-{index + 1}", "resolved": None,
                              "failure_mode": "invalid_result_row"})
                failure_modes["invalid_result_row"] += 1
                continue
            raw_resolved = row.get("is_resolved", row.get("resolved"))
            status = str(row.get("status") or "").strip().lower()
            if isinstance(raw_resolved, bool):
                is_resolved: bool | None = raw_resolved
            elif status in passed_statuses:
                is_resolved = True
            elif status in failed_statuses:
                is_resolved = False
            else:
                is_resolved = None
                unknown_outcomes += 1
            failure_mode = str(row.get("failure_mode") or "unset").strip().lower()
            failure_modes[failure_mode] += 1
            parser_results = row.get("parser_results")
            if not isinstance(parser_results, dict):
                parser_results = {}
            input_tokens += int(row.get("total_input_tokens") or 0)
            output_tokens += int(row.get("total_output_tokens") or 0)
            cases.append({
                "task_id": str(row.get("task_id") or f"unknown-{index + 1}"),
                "resolved": is_resolved,
                "failure_mode": failure_mode,
                "parser_results": {str(k): str(v) for k, v in parser_results.items()},
                "input_tokens": int(row.get("total_input_tokens") or 0),
                "output_tokens": int(row.get("total_output_tokens") or 0),
            })
        total = len(results)
        resolved = sum(case["resolved"] is True for case in cases)
        return {"resolved": resolved, "total": total,
                "pass_rate": round(100.0 * resolved / max(1, total), 1),
                "cases": cases, "failure_modes": dict(sorted(failure_modes.items())),
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "unknown_outcomes": unknown_outcomes}
    # 形状 B：{"n_resolved": x, "n_tasks": y} / {"accuracy": 0.42}
    if isinstance(d, dict):
        resolved = d.get("n_resolved") or d.get("resolved") or 0
        total = d.get("n_tasks") or d.get("total") or 0
        if total:
            return {"resolved": int(resolved), "total": int(total),
                    "pass_rate": round(100.0 * int(resolved) / int(total), 1)}
        acc = d.get("accuracy")
        if acc is not None:
            return {"resolved": 0, "total": 0, "pass_rate": round(100.0 * float(acc), 1)}
    return {"resolved": 0, "total": 0, "pass_rate": 0.0, "error": "结果格式未识别"}


def _attach_agent_diagnostics(rep: dict, run_root: Path) -> None:
    """把自定义 agent 的精简命令轨迹并入逐题结果；坏日志绝不影响官方分数。"""
    by_task = {str(case.get("task_id")): case for case in (rep.get("cases") or [])}
    for transcript_path in run_root.glob("*/*/agent-logs/hashmm-transcript.json"):
        try:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            if not isinstance(transcript, list):
                continue
            task_id = transcript_path.parents[2].name
            case = by_task.get(task_id)
            if case is None:
                continue
            commands = [str(x.get("command")) for x in transcript
                        if isinstance(x, dict) and x.get("command")]
            parse_failures = sum(
                isinstance(x, dict) and "response" in x and "error" in x for x in transcript
            )
            errors = [str(x.get("error")) for x in transcript
                      if isinstance(x, dict) and x.get("error") and "response" not in x]
            observations = [str(x.get("observation")) for x in transcript
                            if isinstance(x, dict) and x.get("observation")]
            warnings = [str(x.get("warning")) for x in transcript
                        if isinstance(x, dict) and x.get("warning")]
            case.update({
                "command_count": len(commands),
                "parse_failures": parse_failures,
                "last_command": commands[-1][-500:] if commands else "",
                "last_observation": observations[-1][-1000:] if observations else "",
                "agent_errors": errors[-3:],
                "agent_warnings": warnings[-3:],
            })
        except Exception:  # noqa: BLE001
            continue


def _export_diagnostics(run_root: Path) -> None:
    """保存官方聚合结果/运行日志/agent transcript，供 Actions Artifact 离线审计。"""
    artifact_root = (os.environ.get("HASHMM_BENCH_ARTIFACT_DIR") or "").strip()
    if not artifact_root:
        return
    try:
        dest = Path(artifact_root) / "diagnostics" / "terminal"
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("results.json", "run_metadata.json", "run.log"):
            source = run_root / name
            if source.is_file():
                shutil.copy2(source, dest / name)
        for source in run_root.glob("*/*/agent-logs/hashmm-transcript.json"):
            task_id = source.parents[2].name
            trial_id = source.parents[1].name
            safe_name = f"{task_id}__{trial_id}".replace("/", "_")[:180] + ".json"
            shutil.copy2(source, dest / safe_name)
    except Exception:  # noqa: BLE001
        pass


def _find_results(run_dir: Path) -> Path | None:
    for pat in ("results.json", "*/results.json", "**/results.json"):
        try:
            hits = sorted(run_dir.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
            if hits:
                return hits[0]
        except Exception:  # noqa: BLE001
            continue
    return None


def build_cmd(py: str, run_id: str, limit: int, agent_path: str, out_dir: Path) -> list[str]:
    """构造 tb run 命令（抽出来便于单测，不必真跑）。"""
    return [
        # terminal_bench.cli.tb 是一个 package，没有 __main__.py；直接 `-m ...cli.tb`
        # 会报 "No module named ...cli.tb.__main__"。上游 0.2.18 的 console script
        # `tb` 明确指向 terminal_bench.cli.tb.main:app，因此从该模块启动，且继续使用
        # venv Python，避免缓存恢复后 console-script shebang 路径失效。
        py, "-m", "terminal_bench.cli.tb.main", "run",
        # 上游明确说明 0.1.1 是 Terminal-Bench 1.x 官方排行榜任务集；不使用会漂移的 head。
        "--dataset", "terminal-bench-core==0.1.1",
        "--agent-import-path", agent_path,
        "--n-tasks", str(limit),
        "--n-concurrent", str(max(1, int(os.environ.get("HASHMM_TB_CONCURRENCY", "2")))),
        "--run-id", run_id,
        "--output-path", str(out_dir),
    ]


def run(adapter, *, limit: int = 5) -> dict:
    det = detect()
    if not det["installed"]:
        return {"kind": "official", "skip": True, "score_pct": None, "mode": "full",
                "detail": det["hint"]}

    # ★ V326：官方 harness 路径（有 Docker，如 CI）也遵循样本档位（set_forced_sample / HASHMM_BENCH_SAMPLE），
    #   和无 Docker 的 terminal_local 一致。此前只用硬编码 limit，导致 CI 里 --sample 被忽略、恒跑 5 题。
    from .sample_stats import MIN_COMPARABLE_N, resolve_limit
    limit = resolve_limit("terminal", limit)

    py = tb_python()
    run_id = f"hashmm_{int(time.time())}"
    out_dir = bench_home() / "tb-runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    agent_path = os.environ.get("HASHMM_TB_AGENT", "scripts.hashmm_tb_agent:HashMMAgent")

    # tb-venv 从 terminal-bench 仓库目录启动。显式把当前 HashMM 仓库根加入
    # PYTHONPATH，保证自定义 agent 模块可导入，而不是依赖调用者碰巧从哪个目录启动。
    child_env = os.environ.copy()
    project_root = str(Path(__file__).resolve().parents[3])
    old_pythonpath = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = (
        project_root + (os.pathsep + old_pythonpath if old_pythonpath else "")
    )
    # terminal-bench 0.2.18 先把任务名放进 set，再截取 n_tasks；不固定 hash seed 时
    # quick/standard 每次进程会抽到不同任务，导致 1/5 与 0/5 无法做纵向比较。
    child_env["PYTHONHASHSEED"] = os.environ.get("HASHMM_TB_PYTHONHASHSEED", "0")

    # 不从 terminal-bench Git 仓库 cwd 启动，否则 Python 会优先导入仓库 head，绕过
    # install.sh 固定的 0.2.18 包。项目根同时能稳定导入 scripts.hashmm_tb_agent。
    # ★ V332：tb run 总时长可调（HASHMM_TB_TIMEOUT，秒；默认 7200=2h）。
    #   standard=50 题在 2 并发下 2h 可能不够（题均 >4.8min 即触顶被杀、不留 results.json）。
    #   GitHub 免费 job 上限 6h → workflow 已把它设为 19800（5.5h），本地按需覆盖。
    try:
        tb_timeout = int(os.environ.get("HASHMM_TB_TIMEOUT", "7200"))
    except Exception:  # noqa: BLE001
        tb_timeout = 7200
    tb_timeout = max(600, tb_timeout)
    rc, out, err = _sh(build_cmd(py, run_id, limit, agent_path, out_dir),
                       cwd=project_root, timeout=tb_timeout, env=child_env)
    res_file = _find_results(out_dir)
    rep = parse_results(res_file) if res_file else {"resolved": 0, "total": 0, "pass_rate": 0.0}
    if not res_file:
        _hint = (f"；已达 HASHMM_TB_TIMEOUT={tb_timeout}s 上限被杀——调大该值、"
                 f"降低 --sample 档位或提高 HASHMM_TB_CONCURRENCY" if rc == 124 else "")
        return {"kind": "official", "skip": True, "score_pct": None, "mode": "full",
                "detail": f"tb run 未产出结果文件（rc={rc}{_hint}）：{(err or out)[:200]}"}
    run_root = res_file.parent
    _attach_agent_diagnostics(rep, run_root)
    _export_diagnostics(run_root)
    cases = rep.get("cases") or []
    fails: list[str] = []
    for case in cases:
        if case.get("resolved") is True:
            continue
        mode = str(case.get("failure_mode") or "unset")
        parser_results = case.get("parser_results") or {}
        failed_tests = [str(name) for name, status in parser_results.items()
                        if str(status).strip().lower() not in {"passed", "skipped"}]
        parts = [f"failure_mode={mode}"]
        if failed_tests:
            parts.append("failed_tests=" + ",".join(failed_tests[:5]))
        if case.get("parse_failures"):
            parts.append(f"JSON解析失败={case['parse_failures']}")
        if case.get("agent_errors"):
            parts.append("agent_error=" + str(case["agent_errors"][-1])[:160])
        if case.get("agent_warnings"):
            parts.append("agent_warning=" + str(case["agent_warnings"][-1])[:120])
        parts.append(f"commands={case.get('command_count', 0)}")
        fails.append(f"{case.get('task_id', 'unknown')}: " + "；".join(parts))
    failure_modes = rep.get("failure_modes") or {}
    failure_text = ", ".join(f"{k}={v}" for k, v in failure_modes.items())
    detail = f"Terminal-bench 任务成功率 {rep['pass_rate']}%（{rep['resolved']}/{rep['total']}）"
    if failure_text:
        detail += f"；failure modes: {failure_text}"
    concurrency = max(1, int(os.environ.get("HASHMM_TB_CONCURRENCY", "2")))
    return {
        "kind": "official", "skip": False, "mode": "full",
        "passed": rep["resolved"], "total": rep["total"], "score_pct": rep["pass_rate"],
        "pipeline_ok": rc == 0 and rep["total"] == limit,
        "comparable": rep["total"] >= MIN_COMPARABLE_N,
        "detail": detail,
        "breakdown": {
            "本次提交": str(rep["total"]),
            "评测并发": str(concurrency),
            "固定采样seed": child_env["PYTHONHASHSEED"],
            "failure modes": failure_text or "none",
            "输入tokens": str(rep.get("input_tokens", 0)),
            "输出tokens": str(rep.get("output_tokens", 0)),
            "未知结果": str(rep.get("unknown_outcomes", 0)),
        },
        "fails": fails[:10],
        "cases": cases,
    }
