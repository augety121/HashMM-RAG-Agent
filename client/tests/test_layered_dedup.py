"""tests/test_layered_dedup.py — 分层记忆去重护栏（V306，修 4.md 分层记忆去重误判）。

针对 selftest「分层记忆·端到端」的失败模式"无关/矛盾的被误合"：合并前加否定极性 +
关键数字差异护栏，让"住在北京"↔"不住在北京"、"TTL 300"↔"TTL 600"不再被当重复合并，
同时保持真正的同主体重复/超集仍然合并。纯规则、无 LLM，沙箱可跑。
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
from hashmm.memory import layered as L

Atom = L.Atom


def _a(content, typ="fact"):
    return Atom(id="x", content=content, type=typ, priority=1, scene_name=None,
                source_message_ids=[], metadata={}, created=0.0, updated=0.0)


def _dec(new, existing):
    return L._dedup_decision(_a(new), [_a(e) for e in existing])[0]


def test_true_duplicate_still_merges():
    assert _dec("用户喜欢结构化的回答", ["用户喜欢结构化简洁的回答"]) == "update"
    assert _dec("北京", ["北京大学"]) == "update"
    assert _dec("完全相同", ["完全相同"]) == "skip"


def test_negation_not_merged():
    assert _dec("用户住在北京", ["用户不住在北京"]) == "store", "否定极性相反被误合"
    assert _dec("服务已启用", ["服务未启用"]) == "store"


def test_different_numbers_not_merged():
    assert _dec("缓存 TTL 是 300 秒", ["缓存 TTL 是 600 秒"]) == "store", "关键数字不同被误合"


def test_unrelated_stored():
    assert _dec("用户喜欢喝咖啡", ["系统用 PostgreSQL 存储"]) == "store"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    print("分层记忆去重护栏（V306）"); p = f = 0
    for n, fn in tests:
        try:
            fn(); print(f"  ✓ {n}"); p += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {n}: {e}"); f += 1
    print(f"结果：PASS={p}  FAIL={f}")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    _sys.exit(_run_all())
