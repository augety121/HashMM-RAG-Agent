"""tests/test_feature_presets_agentic.py — V103.53：feature_presets 的 agentic 档。

验证新增的「智能检索」预设（P0 增益的默认开启路径）与既有契约：
  - agentic 档 = recommended + 检索回路三件套 + 纠正检索；
  - 别名 p0/search/smart/retrieval → agentic；
  - 显式设过的开关绝不被预设覆盖；
  - basic 仍为零开启；max 包含 agentic 的全部项。
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hashmm.feature_presets import (
    resolve_preset_name, apply_preset, PRESETS, FLAG_DOCS, describe_preset,
)


def test_agentic_aliases():
    for alias in ("agentic", "p0", "search", "smart", "retrieval"):
        assert resolve_preset_name(alias) == "agentic", alias


def test_agentic_bundle_contents():
    a = PRESETS["agentic"]
    # 检索回路三件套必须在
    for f in ("HASHMM_AGENTIC_RETRIEVAL", "HASHMM_CONFIDENCE", "HASHMM_UNCERTAINTY_GATE", "HASHMM_CORRECTIVE_RETRIEVAL"):
        assert a.get(f) == "1", f
    # recommended 的低风险项也都在（agentic ⊇ recommended）
    for f in PRESETS["recommended"]:
        assert f in a, f
    # 但不含 max 专属的重项（不擅自把 HyDE/多查询塞进来）
    assert "HASHMM_HYDE" not in a
    assert "HASHMM_MULTIQUERY" not in a
    assert "HASHMM_VERIFIER" not in a


def test_basic_is_empty_and_max_superset():
    assert PRESETS["basic"] == {}
    # max ⊇ agentic
    for f in PRESETS["agentic"]:
        assert f in PRESETS["max"], f


def test_explicit_env_not_overridden():
    env = {"HASHMM_PRESET": "agentic", "HASHMM_UNCERTAINTY_GATE": "0"}
    applied = apply_preset("agentic", environ=env)
    assert env["HASHMM_UNCERTAINTY_GATE"] == "0"        # 用户显式关，保留
    assert "HASHMM_UNCERTAINTY_GATE" not in applied
    assert env.get("HASHMM_AGENTIC_RETRIEVAL") == "1"   # 未显式设的被开启


def test_all_flags_have_docs():
    for f in PRESETS["agentic"]:
        assert f in FLAG_DOCS and FLAG_DOCS[f] and "(无说明)" not in FLAG_DOCS[f], f


def test_describe_runs():
    s = describe_preset("agentic")
    assert "agentic" in s and "不确定性闸" in s


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
