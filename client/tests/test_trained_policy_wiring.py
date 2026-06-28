"""tests/test_trained_policy_wiring.py — V103.60：训练好的策略模型接进 streaming 主路径的逻辑。

只测「接入与降级」逻辑（不需 GPU/模型）：
  - 未配置 HASHMM_TRAINED_POLICY_DIR → 返回 None（主路径走通用 LLM，零风险）；
  - 配置了但缺 base 目录 / 加载失败 → 返回 None（安全降级，不抛错）；
  - 单例缓存：失败后不反复重试。
真实模型驱动在你服务器上验证。
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import hashmm.api.streaming as S


def _reset_cache():
    # 清掉模块级单例，保证用例独立
    if hasattr(S, "_TRAINED_POLICY_CACHE"):
        try:
            del S._TRAINED_POLICY_CACHE
        except Exception:
            S._TRAINED_POLICY_CACHE = None
    globals_ = S.__dict__
    globals_.pop("_TRAINED_POLICY_CACHE", None)


def test_no_env_returns_none():
    """未配置 LoRA 目录 → None（走原有通用 LLM 判停）。"""
    _reset_cache()
    os.environ.pop("HASHMM_TRAINED_POLICY_DIR", None)
    assert S._trained_policy_llm_fn() is None


def test_lora_set_but_no_base_returns_none():
    """配了 LoRA 但没配 base 模型 → None（安全降级，不抛错）。"""
    _reset_cache()
    os.environ["HASHMM_TRAINED_POLICY_DIR"] = "/nonexistent/lora"
    os.environ.pop("HASHMM_BASE_MODEL_DIR", None)
    try:
        assert S._trained_policy_llm_fn() is None
    finally:
        os.environ.pop("HASHMM_TRAINED_POLICY_DIR", None)


def test_load_failure_is_safe_and_cached():
    """配了 LoRA + base 但目录不存在 → 加载失败返回 None，且单例缓存（不反复重试）。"""
    _reset_cache()
    os.environ["HASHMM_TRAINED_POLICY_DIR"] = "/nonexistent/lora"
    os.environ["HASHMM_BASE_MODEL_DIR"] = "/nonexistent/base"
    try:
        r1 = S._trained_policy_llm_fn()   # 不应抛错
        assert r1 is None
        # 第二次：命中单例缓存（_TRAINED_POLICY_CACHE 已写入）
        assert "_TRAINED_POLICY_CACHE" in S.__dict__
        r2 = S._trained_policy_llm_fn()
        assert r2 is None
    finally:
        os.environ.pop("HASHMM_TRAINED_POLICY_DIR", None)
        os.environ.pop("HASHMM_BASE_MODEL_DIR", None)
        _reset_cache()


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
