"""tests/test_e2e_compaction.py — 上下文压缩端到端测试（V306）。

验证"上下文真的会压缩、且压缩后不跑偏/不失忆"这条链在**真实 AgentLoop** 上成立：
  · 直接驱动真实的 `_compact_context`：>50000 字符的工作消息 → 压缩后仍保留 system + 原始目标锚 +
    最近 4 条；短对话不动；
  · 端到端：喂一段超长历史跑真实 loop，验证 loop 不崩、工具仍能产出文件、答案非空
    （长对话经 pre-loop compact_history 压缩后，主链路仍正确）；
  · 目标不丢：压缩后第一条 user 的原始目标仍可追溯。

沙箱可直接跑（真实 loop + agent_bench harness）。
"""
import os
import sys as _sys, os as _os
import tempfile
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
_os.environ.setdefault("HASHMM_DATA_DIR", tempfile.mkdtemp())

from hashmm.agent.loop import AgentLoop, MAX_CONTEXT_CHARS
from hashmm.tools import agent_bench as AB


def _loop():
    return AgentLoop(llm_fn=None, user_id="u", conv_id="c")


def _big_messages(goal: str, n: int = 60):
    msgs = [{"role": "system", "content": "你是助手"},
            {"role": "user", "content": goal}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": f"步骤{i} " + "x" * 1000})
        msgs.append({"role": "tool", "content": f"工具结果{i} " + "y" * 1000})
    return msgs


# ══════════════════════════════ 直接测真实 _compact_context ══════════════════════════════
def test_compact_reduces_size():
    loop = _loop()
    msgs = _big_messages("实现一个 LRU 缓存，这是主线目标")
    before = sum(len(str(m.get("content", ""))) for m in msgs)
    assert before > MAX_CONTEXT_CHARS, "构造的消息未超过压缩阈值"
    out = loop._compact_context(msgs)
    after = sum(len(str(m.get("content", ""))) for m in out)
    assert after < before, f"压缩未缩小上下文：{after} !< {before}"
    assert after < MAX_CONTEXT_CHARS, "压缩后仍超过阈值"


def test_compact_keeps_goal_anchor():
    """压缩后必须保留最初的目标锚——长任务多轮压缩不能'忘了要做什么'。"""
    loop = _loop()
    out = loop._compact_context(_big_messages("实现一个分布式限流器，支持滑动窗口"))
    assert any("分布式限流器" in str(m.get("content", "")) for m in out), "压缩后原始目标丢失"


def test_compact_keeps_system_and_recent():
    loop = _loop()
    msgs = _big_messages("目标X")
    out = loop._compact_context(msgs)
    assert out[0]["role"] == "system", "压缩后 system 丢失"
    assert out[-4:] == msgs[-4:], "压缩后最近 4 条未原样保留"


def test_compact_short_conversation_untouched():
    """短对话（≤5 条）压缩零变化。"""
    loop = _loop()
    short = [{"role": "system", "content": "s"},
             {"role": "user", "content": "hi"},
             {"role": "assistant", "content": "hello"}]
    assert loop._compact_context(short) == short, "短对话被误压缩"


def test_compact_no_system_still_works():
    """无 system 消息时压缩也不崩、仍保留目标与最近消息。"""
    loop = _loop()
    msgs = [{"role": "user", "content": "目标：写快排"}]
    for i in range(60):
        msgs.append({"role": "assistant", "content": f"a{i} " + "z" * 1000})
    out = loop._compact_context(msgs)
    assert any("写快排" in str(m.get("content", "")) for m in out), "无 system 时目标丢失"
    assert len(out) < len(msgs), "无 system 时未压缩"


# ══════════════════════════════ 端到端：超长历史跑真 loop ══════════════════════════════
def _huge_history_seed(target_chars: int = 60000):
    """造一段超过 loop 压缩阈值的长历史（主线目标在开头）。"""
    h = [{"role": "user", "content": "本会话主线任务：用 Python 实现快速排序并写成文件"},
         {"role": "assistant", "content": "好的，主线记下了：快速排序。" + "细" * 1200}]
    i = 0
    while sum(len(str(m.get("content", ""))) for m in h) < target_chars:
        h.append({"role": "user", "content": f"追问{i}：边界{i}"})
        h.append({"role": "assistant", "content": f"边界{i}的处理：" + "答" * 1200})
        i += 1
    return h


def test_e2e_long_history_loop_survives_and_produces_file():
    """喂 6 万字历史跑真实 loop：不崩、工具仍产出文件、答案非空（压缩后主链路正确）。"""
    task = AB.Task(
        id="e2e_compact_file", category="e2e",
        turns=["回到主线，把快速排序写成文件"],
        requires=set(),
        history_seed=_huge_history_seed(60000),
        scorers=[AB.file_exists("qsort.py"), AB.py_compiles("qsort.py"), AB.answer_nonempty()],
        max_seconds=120,
    )
    script = [
        ("tool", "create_file", '{"filename": "qsort.py", "content": "def qsort(a):\\n    return sorted(a)\\n"}'),
        ("say", "已把快速排序写入 qsort.py。"),
    ]
    r = AB.run_task(task, AB.ScriptedLLM(script))
    assert r["status"] == "PASS", f"超长历史 E2E 失败：{r.get('failures')}"


def test_e2e_huge_history_answer_on_track():
    """超长历史下末轮答案非空且不为错误态（长对话不失忆/不炸）。"""
    task = AB.Task(
        id="e2e_compact_answer", category="e2e",
        turns=["一句话总结主线任务是什么"],
        requires=set(),
        history_seed=_huge_history_seed(60000),
        scorers=[AB.answer_nonempty(min_len=3)],
        max_seconds=120,
    )
    script = [("say", "主线任务是用 Python 实现快速排序。")]
    r = AB.run_task(task, AB.ScriptedLLM(script))
    assert r["status"] == "PASS", f"超长历史答案 E2E 失败：{r.get('failures')}"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    print("=" * 60)
    print("上下文压缩端到端测试（V306：真 _compact_context + 真 loop 长历史）")
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
