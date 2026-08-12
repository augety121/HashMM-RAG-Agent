"""V314 回归测试：大厂标准对齐（SWE-bench Pro / Terminal-Bench 2.x / OSWorld /
MCP Atlas）+ 反 reward-hack + 主链路浏览器。全部离线可测。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_BENCH = _ROOT / "hashmm" / "evaluation" / "benchmarks"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("HASHMM_BENCH_HOME", "HASHMM_TERMINAL_SET", "HASHMM_MODULE_BROWSER"):
        monkeypatch.delenv(k, raising=False)


# ================================================================ 反巧合通过
def test_is_test_path_variants():
    from hashmm.evaluation.benchmarks.swebench_local import _is_test_path
    assert _is_test_path("tests/test_x.py")
    assert _is_test_path("pkg/conftest.py")
    assert _is_test_path("a/testing/utils.py")
    assert _is_test_path("mod_test.py")
    assert not _is_test_path("src/requests/models.py")
    assert not _is_test_path("docs/contest.md")          # "contest" 不是测试路径


def test_touched_source_gate():
    from hashmm.evaluation.benchmarks.swebench_local import touched_source
    assert touched_source(["src/models.py", "tests/test_m.py"])
    assert not touched_source(["tests/test_m.py", "conftest.py"])
    assert not touched_source([])


def test_agent_changed_files_collects_and_dedups(monkeypatch):
    from hashmm.evaluation.benchmarks import swebench_local as SW

    def fake_sh(cmd, cwd=None, env=None, timeout=0):
        if cmd[:2] == ["git", "diff"]:
            return 0, "src/a.py\ntests/test_a.py\nsrc/a.py\n", ""
        if cmd[:2] == ["git", "ls-files"]:
            return 0, "newmod.py\n", ""
        return 0, "", ""
    monkeypatch.setattr(SW, "_sh", fake_sh)
    assert SW.agent_changed_files(Path(".")) == ["src/a.py", "tests/test_a.py", "newmod.py"]


def test_anti_hack_wired_before_test_patch():
    src = (_BENCH / "swebench_local.py").read_text(encoding="utf-8")
    i_changed = src.index("agent_changed_files(work)")
    i_patch = src.index("if not apply_test_patch(inst, work):")
    assert i_changed < i_patch                            # 必须在打官方 test_patch 之前采集
    assert "疑似巧合通过" in src and "公开审计" in src
    assert '"疑似巧合通过(不计分)": str(suspects)' in src


# ================================================================ SWE-bench Pro
def test_pro_missing_dataset_hint(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())
    from hashmm.evaluation.benchmarks import swebench_pro as SP
    r = SP.run(None, limit=3)
    assert r["skip"] and "swebench_pro" in r["detail"]


def test_pro_delegates_display_and_key(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())
    from hashmm.evaluation.benchmarks import swebench_local as SWL
    from hashmm.evaluation.benchmarks import swebench_pro as SP
    SP.dataset_path().write_text(
        '{"instance_id":"x__y-1","repo":"x/y","base_commit":"c","problem_statement":"p",'
        '"FAIL_TO_PASS":"[]","PASS_TO_PASS":"[]","created_at":"2023-01-01"}\n', encoding="utf-8")

    def fail_clone(cmd, cwd=None, env=None, timeout=0):
        if cmd[:2] == ["git", "clone"]:
            return 1, "", "no network"
        if cmd[0] == "rm":
            import shutil
            shutil.rmtree(cmd[-1], ignore_errors=True)
        return 0, "", ""
    monkeypatch.setattr(SWL, "_sh", fail_clone)
    r = SP.run(type("A", (), {"llm_fn": None})(), limit=1)
    assert r["skip"] and "SWE-bench Pro(public)" in r["detail"]


def test_pro_in_registry_presets_sizes_leaderboard():
    from hashmm.evaluation.benchmarks.leaderboard import LEADERBOARD
    from hashmm.evaluation.benchmarks.registry import get_benchmark
    from hashmm.evaluation.benchmarks.sample_stats import (OFFICIAL_SIZES,
                                                           SAMPLE_PRESETS)
    assert get_benchmark("swebench_pro")["lb_key"] == "swebench_pro"
    assert OFFICIAL_SIZES["swebench_pro"]["full"] == 731
    for tier, v in (("smoke", 1), ("quick", 3), ("standard", 50), ("full", 731)):
        assert SAMPLE_PRESETS[tier]["swebench_pro"] == v
    refs = dict(LEADERBOARD["swebench_pro"]["refs"])
    assert refs["Muse Spark 1.1"] == 61.5 and refs["GPT-5.4 (xHigh)"] == 59.1
    assert "scale.com" in LEADERBOARD["swebench_pro"]["source_url"]


def test_verified_leaderboard_refreshed_with_saturation_note():
    from hashmm.evaluation.benchmarks.leaderboard import LEADERBOARD
    v = LEADERBOARD["swebench_verified"]
    assert "500" in v["note"] and "不同脚手架" in v["note"]
    assert v["source_url"] == "https://www.swebench.com/"
    assert v["refs"] and all(0 <= score <= 100 for _, score in v["refs"])


# ================================================================ Terminal 双集
def _mk_task(root: Path, name: str):
    d = root / name
    (d / "tests").mkdir(parents=True)
    (d / "Dockerfile").write_text("FROM python:3.11\n")
    (d / "task.yaml").write_text("instruction: x\n")


def test_terminal_dual_set_auto_prefers_v2(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())
    from hashmm.evaluation.benchmarks import terminal_local as TL
    _mk_task(TL.tasks_dir(), "orig-a")
    assert TL.active_task_set()[0] == "original"          # 只有原版
    _mk_task(TL.tasks_dir_v2(), "tb2-x")
    name, d = TL.active_task_set()
    assert name == "2.x" and d == TL.tasks_dir_v2()       # 装了 2.x 自动优先
    det = TL.detect()
    assert det["task_set"] == "2.x" and det["n_original"] == 1 and det["n_v2"] == 1
    monkeypatch.setenv("HASHMM_TERMINAL_SET", "original")
    assert TL.active_task_set()[0] == "original"          # 显式可切回
    monkeypatch.delenv("HASHMM_TERMINAL_SET")
    tasks, st = TL.select_tasks(10)
    assert st["task_set"] == "2.x" and [t["id"] for t in tasks] == ["tb2-x"]


def test_terminal_leaderboard_matches_pinned_v1_refs():
    from hashmm.evaluation.benchmarks.leaderboard import LEADERBOARD
    lb = LEADERBOARD["terminal_bench"]
    refs = dict(lb["refs"])
    assert refs["Apex2 / Claude 4.5 Sonnet"] == 64.5
    assert lb["source_url"].endswith("/terminal-bench/1.0") and "0.1.1" in lb["note"]


# ================================================================ 主链路浏览器
def test_browser_tools_in_untrusted_set():
    from hashmm.agent.loop import _UNTRUSTED_CONTENT_TOOLS as U
    assert {"browser_open", "browser_read", "browser_act", "browser_screenshot"} <= U


def test_browser_module_registered_and_removable():
    from hashmm.agent.modules import _MODULES
    mod = {m.key: m for m in _MODULES}
    assert "browser" in mod and not mod["browser"].core
    assert {"browser_open", "browser_act", "browser_read", "browser_screenshot"} <= mod["browser"].tools


def test_lite_browser_full_loop_offline(monkeypatch):
    """离线双页微站：open→act(click)→read 全链路（与 selftest 同口径）。"""
    import http.server
    import socketserver
    import threading
    monkeypatch.setenv("HASHMM_BROWSER_ALLOW_PRIVATE", "1")
    PAGES = {"/": "<html><title>首页</title><body>产品介绍 <a href='/docs'>文档</a></body></html>",
             "/docs": "<html><title>文档</title><body>端口 8000</body></html>"}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            b = PAGES.get(self.path, "x").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        from hashmm.tools.browser_kernel import _LiteSession
        sess = _LiteSession()
        snap = sess.open(f"http://127.0.0.1:{srv.server_address[1]}/")
        assert "产品介绍" in snap.text and snap.elements
        r2 = sess.act("click", target="1")
        assert "8000" in getattr(r2, "text", "")
    finally:
        srv.shutdown()
        srv.server_close()


# ================================================================ 新基准注册面
def test_new_benches_dispatch_honest_skip(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())
    from hashmm.evaluation.benchmarks import run_benchmark
    for bid, kw in (("osworld", "接入位"), ("mcp_atlas", "MCP")):
        r = run_benchmark(bid, llm_fn=lambda p: "x")
        assert r["skip"] and kw in r["detail"], (bid, r["detail"][:80])


def test_osworld_detect_two_gates(monkeypatch):
    monkeypatch.setenv("HASHMM_BENCH_HOME", tempfile.mkdtemp())
    from hashmm.evaluation.benchmarks import osworld as OW
    d = OW.detect()
    assert not d["installed"] and not d["has_data"] and not d["vm"]
    (OW.data_dir() / "sub").mkdir(parents=True)
    (OW.data_dir() / "sub" / "t.json").write_text("{}")
    monkeypatch.setenv("HASHMM_OSWORLD_VM", "10.0.0.9:5900")
    d2 = OW.detect()
    assert d2["installed"] and d2["n_tasks"] == 1


def test_install_sh_sections_locked():
    src = (_BENCH / "install.sh").read_text(encoding="utf-8")
    for kw in ("install_swebench_pro()", "install_terminal2()", "install_osworld()",
               "swebench_pro) install_swebench_pro", "terminal-bench-2/tasks",
               "ScaleAI/SWE-bench_Pro"):
        assert kw in src, kw


def test_selftest_new_cards_locked():
    src = (_ROOT / "hashmm" / "api" / "routes" / "selftest.py").read_text(encoding="utf-8")
    for kw in ("bench_swebench_pro", "bench_osworld", "bench_mcp_atlas",
               "_t_browser_tools", "浏览器工具·主链路"):
        assert kw in src, kw
