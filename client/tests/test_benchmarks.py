"""tests/test_benchmarks.py — 外部基准对标套件自检（V306）。

验证基准接线正确：注册表/运行器/leaderboard 对比/工具调用基准/优雅降级/中枢注册。
用脚本化 LLM 离线跑（不需要 Docker/外部集），证明"勾选即跑"的管线是通的。
"""
import json
import os
import sys as _sys, os as _os
import tempfile
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
_os.environ.setdefault("HASHMM_DATA_DIR", tempfile.mkdtemp())

from hashmm.evaluation import benchmarks as B
from hashmm.evaluation.benchmarks import leaderboard as LB
from hashmm.evaluation.benchmarks import external, registry

import pytest


@pytest.fixture(autouse=True)
def _restore_env_after_each_test():
    """V308 修跨文件污染：本文件多个测试【直接】改 os.environ（不经 monkeypatch），
    把 HASHMM_DATA_DIR / HASHMM_BENCH_HOME 永久指向一次性临时目录并泄漏给后续所有
    测试文件。与 test_model_mirror 的 importlib.reload(database) 叠加时，
    CONV_FILES_ROOT 会在泄漏的目录上被重算 → 先 import 的模块持旧值、后 reload 的
    持新值（双脑）→ test_v50 并跑必挂、单独跑全过（结果依赖执行顺序 = 不可信）。
    此 fixture 在每个测试前快照相关环境变量、测试后恢复，根治泄漏。"""
    keys = ("HASHMM_DATA_DIR", "HASHMM_BENCH_HOME", "HASHMM_AGENTBENCH_ALLOW_LOCAL")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class ToolLLM:
    """按工具调用基准的用例作答的脚本 LLM。"""
    _MAP = {
        "哪些文件": ("run_shell", {"command": "Get-ChildItem"}),
        "读取": ("read_file", {"filename": "note.txt"}),
        "写进": ("create_file", {"filename": "note.txt", "content": "你好"}),
        "几点了": ("get_datetime", {}),
        "计算": ("calculator", {"expression": "(3+4)*5"}),
        "天气": ("get_weather", {"city": "北京"}),
        "搜索": ("web_search", {"query": "最新 AI 新闻"}),
    }

    def __call__(self, prompt):
        return self._json(prompt)

    def quick_call(self, sys_p, user_p, max_tok=None):
        return self._json(user_p)

    def _json(self, p):
        neg = ["关门", "聊聊", "谢谢"]
        if any(k in p for k in neg):
            return json.dumps({"tool": None})
        for k, (t, args) in self._MAP.items():
            if k in p:
                return json.dumps({"tool": t, "args": args}, ensure_ascii=False)
        return json.dumps({"tool": None})


def test_registry_lists_benchmarks():
    ids = [b["id"] for b in B.BENCHMARKS]
    for expect in ["swebench_verified", "terminal_bench", "tool_calling", "tau2_bench", "kotlin_bench"]:
        assert expect in ids, f"缺基准 {expect}"
    assert registry.get_benchmark("tool_calling")["kind"] == "tool"


def test_leaderboard_comparison():
    c = LB.compare("swebench_verified", 60.0)
    assert c["metric"] and c["refs"], "leaderboard 参照缺失"
    assert "67.7" not in str(c["your_score"])   # your_score 是自己的分
    # 90% 工具调用应超过所有参照
    hi = LB.compare("tool_calling", 90.0)
    assert "超过" in hi["ranking"] or "达到" in hi["ranking"]
    # 10% 应低于所有参照
    lo = LB.compare("tool_calling", 10.0)
    assert "低于" in lo["ranking"]


def test_tool_calling_benchmark_runs_and_scores():
    """工具调用基准应真实跑出分数 + 明细 + 对标（不需外部依赖）。"""
    r = B.run_benchmark("tool_calling", llm_fn=ToolLLM())
    assert not r.get("skip"), f"不应跳过：{r}"
    assert r["total"] == 10 and r["passed"] == 10, f"脚本 LLM 应通过严格契约：{r['passed']}/{r['total']}"
    assert "breakdown" in r
    assert r["cases"] and all(c["passed"] for c in r["cases"])
    assert "参数值正确" in r["breakdown"] and "无臆造参数" in r["breakdown"]
    assert r["kind"] == "builtin", "内置最小集应标 builtin"
    assert r["comparable"] is False and r["comparison"] is None, "内置最小集绝不能与 leaderboard 对标"


def test_skip_when_no_llm():
    r = B.run_benchmark("tool_calling", llm_fn=None)
    # 沙箱可能恰好有 active llm_fn；无则应 skip
    assert "id" in r


def test_external_detect_graceful_without_docker():
    """无 Docker/未安装时探测应返回未安装 + 安装指引，不抛错。"""
    d = external.detect_swebench()
    assert "installed" in d and "hint" in d
    if not d["installed"]:
        assert "install.sh" in d["hint"]


def test_external_full_run_does_not_fake_scores():
    """未安装真集时 full 模式绝不假造分数：要么 skip，要么回退 smoke 并标注。"""
    r = external.run_swebench(type("A", (), {"run_task": lambda *a, **k: {"status": "PASS"},
                                             "answer": lambda *a, **k: "x", "ready": True})(),
                              mode="full")
    # 未安装 → 回退 smoke 或标注未对接，不能声称是 full 的真分
    assert r.get("mode") == "smoke" or r.get("skip"), f"未安装却给了 full 分：{r}"


def test_hub_registration():
    """外部基准已注册进测试中枢 SUITES（可勾选）。"""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "hashmm" / "api" / "routes" / "selftest.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    ids = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SUITES" for t in node.targets):
            if isinstance(node.value, ast.List):
                for el in node.value.elts:
                    if isinstance(el, ast.Dict):
                        for k, v in zip(el.keys, el.values):
                            if isinstance(k, ast.Constant) and k.value == "id" and isinstance(v, ast.Constant):
                                ids.append(v.value)
    bench = [i for i in ids if i.startswith("bench_")]
    assert len(bench) == 13, f"中枢应注册 13 个基准勾选项（V314 +Pro/OSWorld/MCP Atlas）：{bench}"
    assert "外部基准对标" in src.read_text(encoding="utf-8"), "缺「外部基准对标」分组"


# ══════════════════════════════ SWE-bench full-run harness ══════════════════════════════
def test_swebench_predictions_format():
    from hashmm.evaluation.benchmarks import swebench_full as SF
    p = SF.build_prediction("django__django-1", "PATCH")
    assert p["instance_id"] == "django__django-1" and p["model_patch"] == "PATCH"
    assert p["model_name_or_path"] == SF.MODEL_NAME
    import pathlib
    import tempfile as _tf
    pp = pathlib.Path(_tf.mkdtemp()) / "preds.jsonl"
    SF.write_predictions([p, SF.build_prediction("flask__flask-2", "")], pp)
    lines = pp.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2 and json.loads(lines[0])["instance_id"] == "django__django-1"


def test_swebench_report_parse_both_forms():
    from hashmm.evaluation.benchmarks import swebench_full as SF
    import pathlib
    import tempfile as _tf
    # resolved_ids as list
    r1 = pathlib.Path(_tf.mkdtemp()) / "rep.json"
    r1.write_text(json.dumps({"resolved_ids": ["a", "b", "c"], "total_instances": 5}))
    p1 = SF.parse_report(r1)
    assert p1["resolved"] == 3 and p1["total"] == 5 and p1["pass_rate"] == 60.0
    # resolved as int
    r2 = pathlib.Path(_tf.mkdtemp()) / "rep2.json"
    r2.write_text(json.dumps({"resolved": 2, "total": 4}))
    p2 = SF.parse_report(r2)
    assert p2["resolved"] == 2 and p2["pass_rate"] == 50.0
    # missing report
    p3 = SF.parse_report(pathlib.Path(_tf.mkdtemp()) / "nope.json")
    assert p3["resolved"] == 0 and "error" in p3


def test_swebench_full_graceful_without_docker():
    """无 Docker/venv/数据集时 full-run 返回明确 skip，绝不假造分数。"""
    from hashmm.evaluation.benchmarks import swebench_full as SF
    res = SF.run(type("A", (), {"llm_fn": None})(), limit=2)
    # V306：缺 Docker/venv/数据集 → 明确 skip 且 score_pct 为 None（不产生 0% 的假分）
    assert res.get("skip") is True and res.get("score_pct") is None
    assert "detail" in res


def test_swebench_dataset_load():
    from hashmm.evaluation.benchmarks import swebench_full as SF
    import pathlib
    import tempfile as _tf
    ds = pathlib.Path(_tf.mkdtemp()) / "sd.jsonl"
    ds.write_text('{"instance_id":"a","repo":"x/y","base_commit":"c1"}\n{"instance_id":"b","repo":"p/q","base_commit":"c2"}\n')
    assert len(SF.load_dataset(str(ds), limit=1)) == 1
    assert SF.load_dataset("/nonexistent/path.jsonl") == []


# ══════════════════════════════ 趋势入库 ══════════════════════════════
def test_trend_records_and_summarizes():
    from hashmm.evaluation.benchmarks import trend as T
    import tempfile as _tf
    os.environ["HASHMM_DATA_DIR"] = _tf.mkdtemp()   # 隔离本测试的库
    # 重新加载以吃到新 DATA_DIR
    import importlib
    importlib.reload(T)
    for s in (70.0, 80.0, 90.0):
        T.record_run({"id": "tool_calling", "name": "工具调用", "mode": "full",
                      "score_pct": s, "passed": int(s / 10), "total": 10,
                      "kind": "official", "comparable": True})
    # skip 的不入库
    assert T.record_run({"id": "swebench_verified", "skip": True}) is False
    tr = T.get_trend("tool_calling")
    assert [x["score_pct"] for x in tr] == [70.0, 80.0, 90.0], "趋势应升序"
    s = T.summary()["tool_calling"]
    assert s["latest"] == 90.0 and s["best"] == 90.0 and s["runs"] == 3
    assert s["delta"] == 10.0 and s["trend"] == "↑"



# ══════════════════════════════ V306 分数诚实性（核心红线）══════════════════════════════
class _SmokeLLM:
    def __call__(self, p): return "好的"
    def quick_call(self, s, u, max_tok=None): return "要" if "5 度" in u else "不要"
    def call_with_tools(self, messages, tools=None):
        class M: content = "完成"; tool_calls = None; reasoning_content = ""
        class R: message = M()
        return R()


def test_smoke_never_produces_score_or_comparison():
    """★ smoke 只是管线自检：绝不产生分数、绝不对标、绝不入库。
    （旧版把 1/1 通过当成 100% 拿去和 Claude Code 比 → '已超过全部参照' 的假象，就是这条没守住。）"""
    from hashmm.evaluation.benchmarks import trend as T
    import importlib, tempfile as _tf
    os.environ["HASHMM_DATA_DIR"] = _tf.mkdtemp()
    importlib.reload(T)
    r = B.run_benchmark("swebench_verified", mode="smoke", llm_fn=_SmokeLLM())
    assert r["kind"] == "smoke"
    assert r["score_pct"] is None, "smoke 不许有分数"
    assert r["comparable"] is False, "smoke 不许可对标"
    assert r["comparison"] is None, "smoke 不许有对标结论"
    assert T.get_trend("swebench_verified") == [], "smoke 不许入趋势库（会污染成假数据点）"


def test_only_official_is_comparable():
    """只有 official（真实数据集）才允许与 leaderboard 对标。"""
    from hashmm.evaluation.benchmarks.runner import run_benchmark
    for bid, llm in [("tool_calling", ToolLLM()), ("swebench_verified", _SmokeLLM())]:
        r = run_benchmark(bid, llm_fn=llm)
        if r.get("kind") != "official":
            assert not r.get("comparable"), f"{bid} kind={r.get('kind')} 却标成可对标"
            assert r.get("comparison") is None


def test_bfcl_ast_matching():
    """BFCL 简化 AST 判分：函数名+参数值命中 possible_answer。"""
    from hashmm.evaluation.benchmarks import bfcl
    gt = [{"get_weather": {"city": ["Beijing", "beijing"], "unit": ["", "celsius"]}}]
    assert bfcl._match_ground_truth("get_weather", {"city": "Beijing"}, gt), "命中 + 可选参数省略应通过"
    assert bfcl._match_ground_truth("get_weather", {"city": "beijing", "unit": "celsius"}, gt)
    assert not bfcl._match_ground_truth("get_time", {"city": "Beijing"}, gt), "函数名不符应判负"
    assert not bfcl._match_ground_truth("get_weather", {"city": "Shanghai"}, gt), "参数值不在可接受集应判负"
    assert not bfcl._match_ground_truth("get_weather", {}, gt), "必填参数缺失应判负"
    # 数字/字符串归一
    gt2 = [{"add": {"a": [1], "b": [2]}}]
    assert bfcl._match_ground_truth("add", {"a": "1", "b": 2.0}, gt2), "数字/字符串应归一比较"


def test_bfcl_skips_cleanly_without_data():
    from hashmm.evaluation.benchmarks import bfcl
    import tempfile as _tf
    os.environ["HASHMM_BENCH_HOME"] = _tf.mkdtemp()   # 空目录 = 没数据
    d = bfcl.detect()
    assert d["installed"] is False and "install.sh" in d["hint"]
    r = bfcl.run(type("A", (), {"answer": lambda *a, **k: "{}"})())
    assert r["skip"] is True and r["score_pct"] is None, "没数据必须 SKIP，不许造分"


def test_terminal_full_graceful_without_docker():
    from hashmm.evaluation.benchmarks import terminal_full as TF
    d = TF.detect()
    assert "installed" in d and "hint" in d
    r = TF.run(type("A", (), {"llm_fn": None})(), limit=2)
    if not d["installed"]:
        assert r["skip"] is True and r["score_pct"] is None, "缺 Docker/仓库必须 SKIP，不许造分"


def test_terminal_full_parse_results():
    from hashmm.evaluation.benchmarks import terminal_full as TF
    import pathlib as _pl, tempfile as _tf
    p = _pl.Path(_tf.mkdtemp()) / "results.json"
    p.write_text(json.dumps({"results": [{"is_resolved": True}, {"is_resolved": False}, {"is_resolved": True}]}))
    r = TF.parse_results(p)
    assert r["resolved"] == 2 and r["total"] == 3 and r["pass_rate"] == 66.7
    p2 = _pl.Path(_tf.mkdtemp()) / "r2.json"
    p2.write_text(json.dumps({"n_resolved": 3, "n_tasks": 10}))
    assert TF.parse_results(p2)["pass_rate"] == 30.0
    cmd = TF.build_cmd("/py", "rid", 5, "m:A", _pl.Path("/out"))
    assert "run" in cmd and "--n-tasks" in cmd and "5" in cmd


def test_report_builds_with_honest_labels():
    from hashmm.evaluation.benchmarks import report
    md = report.build_markdown()
    assert "HashMM 外部基准对标" in md
    assert "不可" in md and "smoke" in md, "报告必须带诚实标注"



# ══════════════════════════ V306 新增 4 基准（全部用真实数据格式验证）══════════════════════════
def test_registry_has_nine_benchmarks():
    ids = [b["id"] for b in B.BENCHMARKS]
    for e in ["tool_calling", "tau2_bench", "gaia", "kotlin_bench", "webvoyager",
              "swebench_verified", "agentbench_os", "terminal_bench", "webarena"]:
        assert e in ids, f"缺基准 {e}"
    # 免 Docker 的必须标出来（这是用户能真跑的关键）
    nodocker = [b["id"] for b in B.BENCHMARKS if b.get("no_docker")]
    assert len(nodocker) >= 5, f"免 Docker 基准应 >=5：{nodocker}"


def test_gaia_official_scoring():
    """GAIA 官方 quasi-exact-match（用真实答案格式）。"""
    from hashmm.evaluation.benchmarks import gaia
    assert gaia.score_answer("egalitarian", "egalitarian")
    assert gaia.score_answer("Egalitarian.", "egalitarian"), "去标点小写"
    assert gaia.score_answer("34,689", "34689"), "数字去千分位"
    assert not gaia.score_answer("34690", "34689"), "数字不同应判负"
    assert not gaia.score_answer("about 41", "41"), "带多余词的不算"
    assert gaia.score_answer("right, down, left", "right, down, left"), "列表逐元素"
    assert not gaia.score_answer("", "41"), "空答案判负"
    assert gaia._extract_final("推理...\nFINAL ANSWER: 34689") == "34689"


def test_gaia_skips_without_data():
    from hashmm.evaluation.benchmarks import gaia
    import tempfile as _tf
    os.environ["HASHMM_BENCH_HOME"] = _tf.mkdtemp()
    r = gaia.run(type("A", (), {"llm_fn": None})())
    assert r["skip"] is True and r["score_pct"] is None


def test_tau2_official_harness_wiring():
    """τ² 走官方 harness：命令构造 + 官方 reward 解析。"""
    from hashmm.evaluation.benchmarks import tau2_full as T2
    cmd = T2.build_cmd("/py", "retail", "deepseek-v4-pro", 10, "/logs")
    assert "run.py" in cmd and "--env" in cmd and "retail" in cmd
    assert "--model-provider" in cmd and "openai" in cmd, "必须用 OpenAI 兼容协议喂后端模型"
    import pathlib as _pl, tempfile as _tf
    d = _pl.Path(_tf.mkdtemp())
    (d / "r.json").write_text(json.dumps([{"reward": 1.0}, {"reward": 0.0}, {"reward": 1.0}, {"reward": 0.5}]))
    rep = T2.parse_results(d)
    assert rep["passed"] == 2 and rep["total"] == 4 and rep["pass_rate"] == 50.0, "官方 reward==1 才算成功"


def test_kotlin_real_compile_scoring_contract():
    """Kotlin 判分必须是【真编译真跑】，不是静态检查。"""
    from hashmm.evaluation.benchmarks import kotlin_bench as KB
    import inspect
    src = inspect.getsource(KB.compile_and_run)
    assert "kotlinc" in src and "java" in src, "必须真调 kotlinc/java"
    # 代码抽取（模型常带 markdown 围栏）
    assert "fun sum" in KB.extract_code("```kotlin\nfun sum(a: Int) = a\n```")
    assert KB.extract_code("fun f() = 1").strip() == "fun f() = 1"
    import tempfile as _tf
    os.environ["HASHMM_BENCH_HOME"] = _tf.mkdtemp()
    r = KB.run(type("A", (), {"answer": lambda *a, **k: ""})())
    assert r["skip"] is True and r["score_pct"] is None, "没 kotlinc/数据必须 SKIP"


def test_agentbench_safety_filter():
    """★ 保护用户服务器：危险命令的任务必须被过滤掉，且默认关闭。"""
    from hashmm.evaluation.benchmarks import agentbench_os as AB2
    danger = [
        {"description": "x", "start": "rm -rf /"},
        {"description": "x", "start": "apt-get install nginx"},
        {"description": "x", "start": "useradd hacker"},
        {"description": "x", "start": "systemctl stop ssh"},
        {"description": "x", "start": "mkfs.ext4 /dev/sda1"},
        {"description": "x", "start": "echo x > /etc/passwd"},
    ]
    for t in danger:
        assert not AB2.is_safe(t), f"危险任务竟然通过了安全过滤：{t['start']}"
    assert AB2.is_safe({"description": "x", "start": "echo hello > /tmp/a.txt"}), "正常任务不该被误杀"
    # 默认必须关闭
    os.environ.pop("HASHMM_AGENTBENCH_ALLOW_LOCAL", None)
    assert AB2.enabled() is False, "AgentBench 本机执行必须默认关闭"
    r = AB2.run(type("A", (), {"llm_fn": None})())
    assert r["skip"] is True, "没开开关必须跳过"


def test_agentbench_match_scoring():
    from hashmm.evaluation.benchmarks import agentbench_os as AB2
    t = {"evaluation": {"match": "2"}}
    assert AB2.score("答案是 2", t)
    assert AB2.score("2", t)
    assert not AB2.score("", t)


def test_webarena_honest_about_docker():
    """WebArena 必须如实说明跑不了（需要 Docker 自建网站），不许假装能跑。"""
    from hashmm.evaluation.benchmarks import webvoyager
    st = webvoyager.webarena_status()
    assert st["runnable"] is False and "Docker" in st["reason"]
    r = B.run_benchmark("webarena", llm_fn=ToolLLM())
    assert r["skip"] is True and r["score_pct"] is None


def test_swebench_local_no_docker_path():
    """没 Docker 时走本机 venv 模式；缺数据集要 SKIP，不许造分。"""
    from hashmm.evaluation.benchmarks import swebench_local as SL
    import tempfile as _tf
    os.environ["HASHMM_BENCH_HOME"] = _tf.mkdtemp()
    r = SL.run(type("A", (), {"llm_fn": None})())
    assert r["skip"] is True and r["score_pct"] is None
    # FAIL_TO_PASS 可能是 JSON 字符串
    assert SL._as_list('["test_a", "test_b"]') == ["test_a", "test_b"]
    assert SL._as_list(["x"]) == ["x"]
    assert SL._as_list(None) == []


def test_trend_no_cross_kind_pollution():
    """★ 修你截图里的'最高100% ↓-25%'：老 smoke 的假分不能和真实分混在一起比。"""
    from hashmm.evaluation.benchmarks import trend as T
    import importlib, tempfile as _tf
    os.environ["HASHMM_DATA_DIR"] = _tf.mkdtemp()
    importlib.reload(T)
    # 老版本残留的 smoke 100%（kind=smoke）
    T.record_run({"id": "kotlin_bench", "score_pct": 100.0, "passed": 1, "total": 1,
                  "kind": "smoke", "comparable": False})
    # 现在的真实分（kind=official）
    T.record_run({"id": "kotlin_bench", "score_pct": 60.0, "passed": 18, "total": 30,
                  "kind": "official", "comparable": True})
    T.record_run({"id": "kotlin_bench", "score_pct": 70.0, "passed": 21, "total": 30,
                  "kind": "official", "comparable": True})
    s = T.summary()["kotlin_bench"]
    assert s["kind"] == "official"
    assert s["best"] == 70.0, f"最高分不该被老 smoke 的 100% 污染：{s['best']}"
    assert s["delta"] == 10.0, f"应与同 kind 的上次(60%)比，而不是 smoke：{s['delta']}"



def test_purge_removes_polluted_keeps_real():
    """★ 修你报告里的假 100%：清理无 kind 的老记录 + smoke，保留真实分。"""
    from hashmm.evaluation.benchmarks import trend as T
    import importlib, tempfile as _tf, sqlite3, time
    os.environ["HASHMM_DATA_DIR"] = _tf.mkdtemp()
    importlib.reload(T)
    with sqlite3.connect(str(T._db_path())) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS bench_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, bench_id TEXT, name TEXT, mode TEXT, score_pct REAL, passed INTEGER,
            total INTEGER, detail TEXT, meta TEXT)""")
        # 老 smoke 残留（meta 空 = 无 kind）
        c.execute("INSERT INTO bench_runs(ts,bench_id,score_pct,passed,total,meta) VALUES(?,?,?,?,?,?)",
                  (time.time(), "tau2_bench", 100.0, 1, 1, "{}"))
        # kind=smoke 的记录
        c.execute("INSERT INTO bench_runs(ts,bench_id,score_pct,passed,total,meta) VALUES(?,?,?,?,?,?)",
                  (time.time(), "kotlin_bench", 100.0, 1, 1, '{"kind":"smoke"}'))
        # 真实 official 分
        c.execute("INSERT INTO bench_runs(ts,bench_id,score_pct,passed,total,meta) VALUES(?,?,?,?,?,?)",
                  (time.time(), "tool_calling", 75.0, 60, 80, '{"kind":"official","comparable":true}'))
    n = T.purge_polluted()
    assert n == 2, f"应删除 2 条污染（无kind + smoke）：删了 {n}"
    sm = T.summary()
    assert "tau2_bench" not in sm and "kotlin_bench" not in sm, "假 100% 应被清掉"
    assert sm.get("tool_calling", {}).get("latest") == 75.0, "真实分应保留"


def test_get_trend_filters_no_kind_records():
    """历史趋势也不显示无 kind 的老污染记录。"""
    from hashmm.evaluation.benchmarks import trend as T
    import importlib, tempfile as _tf, sqlite3, time
    os.environ["HASHMM_DATA_DIR"] = _tf.mkdtemp()
    importlib.reload(T)
    with sqlite3.connect(str(T._db_path())) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS bench_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, bench_id TEXT, name TEXT, mode TEXT, score_pct REAL, passed INTEGER,
            total INTEGER, detail TEXT, meta TEXT)""")
        c.execute("INSERT INTO bench_runs(ts,bench_id,score_pct,passed,total,meta) VALUES(?,?,?,?,?,?)",
                  (time.time()-10, "tool_calling", 100.0, 1, 1, "{}"))            # 无 kind
        c.execute("INSERT INTO bench_runs(ts,bench_id,score_pct,passed,total,meta) VALUES(?,?,?,?,?,?)",
                  (time.time(), "tool_calling", 75.0, 60, 80, '{"kind":"official"}'))  # 真实
    h = T.get_trend("tool_calling")
    assert len(h) == 1 and h[0]["score_pct"] == 75.0, f"只应保留有 kind 的真实记录：{h}"



# ══════════════════════════ V306 第十六批：answer 修复 + 详细报告 + terminal 本机 ══════════════════════════
def test_run_task_returns_answer():
    """★ GAIA 0% 的根因：run_task 之前不返回 answer，取 r['answer'] 恒为空。"""
    from hashmm.tools import agent_bench as AB

    class LLM:
        def call_with_tools(self, messages, tools=None):
            class M:
                content = "推理...\nFINAL ANSWER: 42"
                tool_calls = None
                reasoning_content = ""

            class R:
                message = M()
            return R()

        def quick_call(self, *a, **k):
            return "x"

        def __call__(self, p):
            return "x"

    t = AB.Task(id="t", category="gaia", turns=["q"], requires=set(),
                scorers=[AB.answer_nonempty(min_len=1)], max_seconds=30)
    r = AB.run_task(t, LLM())
    assert "answer" in r, "run_task 必须返回 answer（否则 GAIA/WebVoyager/AgentBench 全 0 分）"
    assert "FINAL ANSWER: 42" in r["answer"]
    assert "tools_used" in r


def test_report_is_detailed():
    """报告必须详细：环境体检 + 逐题明细 + 优化建议。"""
    from hashmm.evaluation.benchmarks import trend as T, report as R
    import importlib, tempfile as _tf
    os.environ["HASHMM_DATA_DIR"] = _tf.mkdtemp()
    importlib.reload(T)
    importlib.reload(R)
    T.record_run({"id": "tool_calling", "name": "BFCL", "score_pct": 72.5, "passed": 58, "total": 80,
                  "kind": "official", "comparable": True,
                  "breakdown": {"选错函数": "14", "参数不对": "8"},
                  "cases": [{"id": "s0", "cat": "simple", "ok": False, "q": "查天气",
                             "pred": "search()", "gold": "get_weather", "why": "选错函数"}]})
    md = R.build_markdown()
    assert "环境体检" in md and "逐题明细" in md and "优化建议" in md
    assert "选错函数" in md, "优化建议应据失败模式生成"
    assert len(md) > 3000, f"详细报告应显著更长：{len(md)}"


def test_gaia_diagnoses_no_search():
    """GAIA 要能诊断出'没联网搜索'（你的真实问题）。"""
    from hashmm.evaluation.benchmarks import report as R
    s = {"latest": 0.0, "comparable": True, "kind": "official",
         "breakdown": {"⚠️没联网搜索的题": "20/20"},
         "cases": [{"level": "1", "ok": False}]}
    tips = R._suggestions("gaia", s)
    joined = " ".join(tips)
    assert "搜索" in joined and "SERPER" in joined.upper(), "应诊断出搜索问题并给出 Serper 检查建议"


def test_terminal_local_no_docker():
    """Terminal-bench 本机模式：官方任务筛选 + 危险过滤 + 官方 pytest。"""
    from hashmm.evaluation.benchmarks import terminal_local as TL
    import inspect
    src = inspect.getsource(TL.run_official_tests)
    assert "pytest" in src, "必须跑官方 pytest 判分"
    # 危险任务过滤
    import tempfile as _tf
    from pathlib import Path
    d = Path(_tf.mkdtemp())
    (d / "Dockerfile").write_text("FROM python:3.13" + chr(10) + "RUN apt-get install evil")
    (d / "task.yaml").write_text("instruction: x")
    (d / "tests").mkdir()
    # V312：apt 从"一票否决"改为【能力判定】——没有 apt 能力时仍如实跳过；
    # 有能力（root+apt-get，如 AutoDL/本沙箱）时可跑（起跑前自动装包）。
    assert not TL.is_runnable_locally(d, allow_apt=False), "无 apt 能力时应跳过"
    assert TL.is_runnable_locally(d, allow_apt=True), "有 apt 能力时应可跑（V312 扩池）"
    d2 = Path(_tf.mkdtemp())
    (d2 / "Dockerfile").write_text("FROM ghcr.io/laude-institute/t-bench/python-3-13:latest" + chr(10) + "RUN pip install numpy")
    (d2 / "task.yaml").write_text("instruction: do something")
    (d2 / "tests").mkdir()
    assert TL.is_runnable_locally(d2), "纯 pip 的任务本机可跑"
    os.environ["HASHMM_BENCH_HOME"] = _tf.mkdtemp()
    r = TL.run(type("A", (), {"llm_fn": None})())
    assert r["skip"] is True, "没装数据要 SKIP"


def test_install_sh_handles_multiple_targets():
    """★ 修 install.sh 只认第一个参数的 bug（你 Kotlin 没装上的原因）。"""
    import pathlib
    sh = (pathlib.Path(__file__).resolve().parents[1] /
          "hashmm" / "evaluation" / "benchmarks" / "install.sh").read_text(encoding="utf-8")
    assert 'TARGETS=("$@")' in sh, "必须接收所有参数，不能只认 $1"
    assert 'for T in "${TARGETS[@]}"' in sh, "必须循环处理每个目标"
    assert "安装结果自检" in sh, "装完要自检，明确告诉用户装上没有"



# ══════════════════════════ V306 第十七批：路径修复 + HumanEval/MBPP ══════════════════════════
@pytest.mark.skipif(os.name == "nt", reason="AutoDL /root 路径选择仅适用于 Linux")
def test_bench_home_prefers_autodl_tmp():
    """★ 修路径 bug：AutoDL 上默认落 /root/autodl-tmp，不再污染 /root、重启不丢。"""
    from hashmm.evaluation.benchmarks import _paths
    import importlib, unittest.mock as mock
    os.environ.pop("HASHMM_BENCH_HOME", None)
    importlib.reload(_paths)
    with mock.patch("pathlib.Path.is_dir", lambda self: str(self) == "/root/autodl-tmp"):
        assert str(_paths.bench_home()) == "/root/autodl-tmp/hashmm-benchmarks"
    # 环境变量最高优先
    os.environ["HASHMM_BENCH_HOME"] = "/custom/x"
    importlib.reload(_paths)
    assert str(_paths.bench_home()) == "/custom/x"
    os.environ.pop("HASHMM_BENCH_HOME", None)


def test_all_benchmarks_use_shared_path():
    """所有基准模块必须用共享 _paths，不能各自写死 ~/hashmm-benchmarks。"""
    import pathlib
    d = pathlib.Path(__file__).resolve().parents[1] / "hashmm" / "evaluation" / "benchmarks"
    offenders = []
    for f in d.glob("*.py"):
        if f.name == "_paths.py":
            continue
        txt = f.read_text(encoding="utf-8")
        if 'Path.home() / "hashmm-benchmarks"' in txt and "def bench_home" in txt:
            offenders.append(f.name)
    assert not offenders, f"这些文件仍写死了路径，没用共享 _paths：{offenders}"


def test_humaneval_registered():
    ids = [b["id"] for b in B.BENCHMARKS]
    assert "humaneval" in ids
    from hashmm.evaluation.benchmarks.registry import get_benchmark
    assert get_benchmark("humaneval")["no_docker"] is True


def test_humaneval_real_execution_scoring():
    """★ HumanEval/MBPP 判分：真执行，正确解过、错误解负、不安全代码跳过。"""
    from hashmm.evaluation.benchmarks import humaneval_bench as HE
    # 正确程序
    ok, _ = HE._run_program("def add(a,b):" + chr(10) + "    return a+b" + chr(10) + "assert add(1,2)==3" + chr(10))
    assert ok, "正确程序应通过"
    # 错误程序
    bad, _ = HE._run_program("def add(a,b):" + chr(10) + "    return a-b" + chr(10) + "assert add(1,2)==3" + chr(10))
    assert not bad, "错误程序应判负"
    # 不安全代码不执行
    unsafe, why = HE._run_program("import os" + chr(10) + "os.system('echo hi')" + chr(10))
    assert not unsafe and "不安全" in why, "含 import os/system 的代码必须跳过（保护服务器）"
    # 代码抽取
    assert "def f" in HE.extract_code("```python" + chr(10) + "def f(): pass" + chr(10) + "```")


def test_humaneval_skips_without_data():
    from hashmm.evaluation.benchmarks import humaneval_bench as HE
    import tempfile as _tf
    os.environ["HASHMM_BENCH_HOME"] = _tf.mkdtemp()
    r = HE.run(type("A", (), {"answer": lambda *a, **k: "return 1"})())
    assert r["skip"] is True and r["score_pct"] is None
    os.environ.pop("HASHMM_BENCH_HOME", None)


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("外部基准对标套件自检（V306）")
    p = f = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ✓ {name}"); p += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}"); f += 1
    print(f"结果：PASS={p}  FAIL={f}")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())
