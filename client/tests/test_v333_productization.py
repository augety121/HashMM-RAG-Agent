"""V333 regression tests: packaged SDK, paired attribution, and desktop controls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import threading
import tomllib
import urllib.error

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_exposes_installable_hashmm_package():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "hashmm-rag"
    assert data["project"]["requires-python"] == ">=3.10"
    assert data["tool"]["setuptools"]["packages"]["find"]["include"] == ["hashmm*"]
    assert data["tool"]["setuptools"]["dynamic"]["dependencies"]["file"] == ["requirements.txt"]


class _Response:
    def __init__(self, value):
        self.raw = json.dumps(value, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.raw


def test_sdk_search_contract_and_auth(monkeypatch):
    from hashmm.client import HashMMClient

    seen = {}

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, method=request.get_method(), timeout=timeout,
                    auth=request.get_header("Authorization"),
                    body=json.loads(request.data.decode("utf-8")))
        return _Response({"query": "营收", "results": [], "num_results": 0})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = HashMMClient("http://127.0.0.1:6006/", api_key="secret", timeout=7)
    out = client.search(" 营收 ", top_k=3)
    assert out["query"] == "营收"
    assert seen == {"url": "http://127.0.0.1:6006/v1/search", "method": "POST",
                    "timeout": 7.0, "auth": "Bearer secret",
                    "body": {"query": "营收", "top_k": 3}}


def test_sdk_raises_structured_http_error(monkeypatch):
    from hashmm.client import HashMMClient, HashMMHTTPError

    def fail(request, timeout):
        body = io.BytesIO(json.dumps({"error": {"code": "auth_error",
            "message": "unauthorized", "details": {"reason": "bad key"}}}).encode())
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, body)

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(HashMMHTTPError) as exc:
        HashMMClient("http://localhost:6006").search("x")
    assert exc.value.status_code == 401
    assert exc.value.error_code == "auth_error"
    assert exc.value.details == {"reason": "bad key"}


@pytest.mark.parametrize("query,top_k", [("", 5), ("ok", 0), ("ok", 101)])
def test_sdk_rejects_invalid_requests_before_network(query, top_k):
    from hashmm.client import HashMMClient
    with pytest.raises(ValueError):
        HashMMClient("http://localhost:6006").search(query, top_k=top_k)


def test_baseline_scope_is_thread_isolated(monkeypatch):
    from hashmm.evaluation.benchmarks.adapter import baseline_mode, baseline_scope

    monkeypatch.delenv("HASHMM_BENCH_BASELINE", raising=False)
    gate = threading.Barrier(2)

    def read(value):
        with baseline_scope(value):
            gate.wait(timeout=3)
            return baseline_mode()

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(read, [True, False]))
    assert values == [True, False]
    assert baseline_mode() is False


def test_idempotency_side_effect_verifies_create_file_content(tmp_path, monkeypatch):
    """A same-named but different file must invalidate a cached create_file write."""
    from hashmm.agent import loop as loop_mod
    from hashmm.api import tool_registry

    monkeypatch.setattr(tool_registry, "get_files_dir", lambda _conv: tmp_path)
    target = tmp_path / "result.py"
    target.write_text("broken", encoding="utf-8")
    assert loop_mod._idem_verify_side_effect(
        "create_file", {"filename": "result.py", "content": "fixed"}, "c",
    ) is False
    assert loop_mod._idem_verify_side_effect(
        "create_file", {"filename": "result.py", "content": "broken"}, "c",
    ) is True


def test_comparison_runner_accepts_explicit_baseline_scope(monkeypatch):
    from hashmm.evaluation.benchmarks import runner as runner_mod
    from hashmm.evaluation.benchmarks.adapter import baseline_mode

    ids = ["tau2_bench", "gaia"]
    monkeypatch.setattr(runner_mod, "comparable_candidates", lambda: [
        {"id": bid, "name": bid, "runnable": True, "hint": ""} for bid in ids
    ])
    monkeypatch.setattr(runner_mod, "run_benchmark", lambda bid, **_kw: {
        "id": bid, "baseline_seen": baseline_mode(), "skip": False,
    })
    out = runner_mod.run_for_comparison(ids, parallel=2, baseline=True)
    assert [r["baseline_seen"] for r in out] == [True, True]


def test_paired_mode_is_wired_through_ci_backend_and_desktop():
    remote = (ROOT / "scripts" / "remote_bench_runner.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "docker-benchmarks.yml").read_text(encoding="utf-8")
    route = (ROOT / "hashmm" / "api" / "routes" / "selftest.py").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend-next" / "components" / "desktop" / "BenchmarkCards.tsx").read_text(encoding="utf-8")
    api = (ROOT / "frontend-next" / "lib" / "api.ts").read_text(encoding="utf-8")

    assert "--paired-baseline" in remote and "phase_modes = [True, False]" in remote
    assert "paired_baseline:" in workflow and "--paired-baseline" in workflow
    assert 'body.get("paired_baseline"' in route and "baseline=True" in route
    assert "同跑裸模型基线（推荐）" in frontend and "开始正式跑测" in frontend
    assert "paired_baseline" in api and "parallel" in api
