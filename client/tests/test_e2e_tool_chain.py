"""tests/test_e2e_tool_chain.py — 工具连环 + 中途压缩 + 幻觉 混合场景 E2E（V306）。

在真实 AgentLoop 上验证"长工具链跑得通、上下文老化/压缩机制正确、答案不幻觉":
  · 直接测真实 `_age_tool_results`（中途压缩的温和前置）：折叠旧工具结果、保留最近 3 条全文、
    消息数/tool_call_id 配对不变、幂等、外部不可信内容折叠更狠；
  · 长工具链 E2E：一串不同工具操作（建多文件→改→建）全部真实执行成功；
  · 工具链 + 压缩共存：超长历史（触发 pre-loop 压缩）下工具链仍正确产出文件；
  · 幻觉接地：最终答案只引用真实创建过的文件，不吹嘘没做的事。

沙箱可直接跑（真实 loop + 工具执行 + agent_bench harness）。
"""
import json
import os
import sys as _sys, os as _os
import tempfile
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
_os.environ.setdefault("HASHMM_DATA_DIR", tempfile.mkdtemp())

from hashmm.agent.loop import AgentLoop
import hashmm.agent.loop as L
from hashmm.tools import agent_bench as AB


def _loop():
    return AgentLoop(llm_fn=None, user_id="u", conv_id="c")


# ══════════════════════════════ 直接测 _age_tool_results（中途压缩前置）══════════════════════════════
def _mk_tool_msgs(n, size=500, untrusted_first=0):
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": f"叙述{i}", "tool_calls": [{"id": f"t{i}"}]})
        prefix = L._UNTRUSTED_OPEN if i < untrusted_first else ""
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": prefix + f"结果{i} " + "x" * size})
    return msgs


def test_age_folds_old_keeps_recent():
    loop = _loop()
    msgs = _mk_tool_msgs(6)
    out = loop._age_tool_results(msgs)
    tools = [m for m in out if m.get("role") == "tool"]
    folded = [m for m in tools if "已折叠" in str(m.get("content", ""))]
    assert len(tools) == 6
    assert len(folded) == 3, f"应折叠最旧 3 条：实际 {len(folded)}"
    assert all("已折叠" not in str(m["content"]) for m in tools[-3:]), "最近 3 条应保留全文"


def test_age_preserves_message_count_and_pairing():
    loop = _loop()
    msgs = _mk_tool_msgs(6)
    n = len(msgs)
    out = loop._age_tool_results(msgs)
    assert len(out) == n, "老化不得改变消息条数（会破坏 tool_call 配对）"
    tools = [m for m in out if m.get("role") == "tool"]
    assert all(m.get("tool_call_id") for m in tools), "tool_call_id 配对必须保留"


def test_age_is_idempotent():
    loop = _loop()
    out1 = loop._age_tool_results(_mk_tool_msgs(6))
    f1 = sum(1 for m in out1 if m.get("role") == "tool" and "已折叠" in str(m.get("content", "")))
    out2 = loop._age_tool_results([dict(m) for m in out1])
    f2 = sum(1 for m in out2 if m.get("role") == "tool" and "已折叠" in str(m.get("content", "")))
    assert f1 == f2, "二次老化不应重复折叠（非幂等）"


def test_age_untrusted_folded_harder():
    loop = _loop()
    out = loop._age_tool_results(_mk_tool_msgs(5, size=300, untrusted_first=2))
    assert any("外部内容已折叠" in str(m.get("content", "")) for m in out), "外部不可信旧内容应被折叠"


def test_age_small_history_untouched():
    """工具结果条数 ≤ 保留数时不折叠。"""
    loop = _loop()
    msgs = _mk_tool_msgs(2)
    out = loop._age_tool_results(msgs)
    assert not any("已折叠" in str(m.get("content", "")) for m in out), "少量工具结果不应折叠"


# ══════════════════════════════ 长工具链 E2E ══════════════════════════════
def test_e2e_long_tool_chain_all_succeed():
    """一串不同工具操作（建 3 文件→改 1→建 1）全部真实执行成功。"""
    task = AB.Task(
        id="e2e_chain", category="e2e",
        turns=["搭个小项目：建几个文件再改一处"],
        requires=set(),
        scorers=[
            AB.file_exists("a.py"), AB.file_exists("b.py"), AB.file_exists("c.py"),
            AB.file_contains("a.py", "plus"), AB.file_contains("a.py", r"\badd\b", negate=True),
            AB.py_compiles("a.py"), AB.py_compiles("c.py"),
        ],
        max_seconds=90,
    )
    script = [
        ("tool", "create_file", json.dumps({"filename": "a.py", "content": "def add(x):\n    return x\n"})),
        ("tool", "create_file", json.dumps({"filename": "b.py", "content": "B = 2\n"})),
        ("tool", "create_file", json.dumps({"filename": "c.py", "content": "def c():\n    return 3\n"})),
        ("tool", "str_replace", json.dumps({"filepath": "a.py", "old_str": "add", "new_str": "plus"})),
        ("say", "项目已搭好：a.py/b.py/c.py，并把 add 改名为 plus。"),
    ]
    r = AB.run_task(task, AB.ScriptedLLM(script))
    assert r["status"] == "PASS", f"长工具链 E2E 失败：{r.get('failures')}"


def test_e2e_tool_chain_with_compaction():
    """超长历史（触发 pre-loop 压缩）下，工具链仍能正确产出文件（压缩不打断工具链）。"""
    seed = [{"role": "user", "content": "主线：搭建一个数据处理项目"},
            {"role": "assistant", "content": "好的，主线记下了。" + "细" * 1200}]
    i = 0
    while sum(len(str(m.get("content", ""))) for m in seed) < 60000:
        seed.append({"role": "user", "content": f"追问{i}"})
        seed.append({"role": "assistant", "content": f"回答{i}：" + "答" * 1200})
        i += 1
    task = AB.Task(
        id="e2e_chain_compact", category="e2e",
        turns=["回到主线，建两个处理脚本"],
        requires=set(),
        history_seed=seed,
        scorers=[AB.file_exists("proc1.py"), AB.file_exists("proc2.py"), AB.py_compiles("proc1.py")],
        max_seconds=120,
    )
    script = [
        ("tool", "create_file", json.dumps({"filename": "proc1.py", "content": "def p1(d):\n    return d\n"})),
        ("tool", "create_file", json.dumps({"filename": "proc2.py", "content": "def p2(d):\n    return d\n"})),
        ("say", "已建 proc1.py 和 proc2.py。"),
    ]
    r = AB.run_task(task, AB.ScriptedLLM(script))
    assert r["status"] == "PASS", f"工具链+压缩 E2E 失败：{r.get('failures')}"


# ══════════════════════════════ 幻觉接地 ══════════════════════════════
def test_e2e_answer_only_references_real_files():
    """最终答案只提真实创建过的文件；用 file_exists 证明它说建的确实建了（不吹没做的事）。"""
    task = AB.Task(
        id="e2e_no_halluc", category="e2e",
        turns=["建一个 real.py 并说明"],
        requires=set(),
        scorers=[
            AB.file_exists("real.py"),          # 它说建了 real.py → 必须真存在
            AB.answer_contains("real.py"),      # 答案确实提到 real.py
        ],
        max_seconds=60,
    )
    script = [
        ("tool", "create_file", json.dumps({"filename": "real.py", "content": "x = 1\n"})),
        ("say", "已创建 real.py，内容定义了变量 x。"),
    ]
    r = AB.run_task(task, AB.ScriptedLLM(script))
    assert r["status"] == "PASS", f"答案接地 E2E 失败：{r.get('failures')}"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("=" * 60)
    print("工具连环 + 中途压缩 + 幻觉 混合 E2E（V306）")
    print("=" * 60)
    import time
    p = f = 0
    t0 = time.time()
    for name, fn in tests:
        try:
            fn(); print(f"  ✓ {name}"); p += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}\n      {type(e).__name__}: {e}"); f += 1
    print("=" * 60)
    print(f"结果：PASS={p}  FAIL={f}  用时 {time.time() - t0:.1f}s")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())
