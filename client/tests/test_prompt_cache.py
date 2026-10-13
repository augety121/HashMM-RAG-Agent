"""P2-2 LLM prompt 缓存测试（对标 Codex prompt_cache_key）。"""
import importlib.util as _ilu

import pytest

pytestmark = pytest.mark.unit
if _ilu.find_spec("hashmm.prompt_cache") is None:
    pytestmark = [pytest.mark.unit, pytest.mark.skip(reason="hashmm.prompt_cache 未部署（旧版本代码）")]


def test_disabled_passthrough(clean_env):
    """默认关 → 每次都调用底层（零行为变化）。"""
    from hashmm import prompt_cache as PC
    calls = [0]

    def fn():
        calls[0] += 1
        return f"r{calls[0]}"

    PC.cached_llm_call("keyword", "m", [{"role": "user", "content": "x"}], fn)
    PC.cached_llm_call("keyword", "m", [{"role": "user", "content": "x"}], fn)
    assert calls[0] == 2


def test_cacheable_task_hits(clean_env, monkeypatch):
    """开启 + 确定性任务 + 相同输入 → 命中缓存（底层只调一次）。"""
    monkeypatch.setenv("HASHMM_PROMPT_CACHE", "1")
    monkeypatch.setenv("HASHMM_PIPELINE_CACHE", "1")
    from hashmm import prompt_cache as PC
    calls = [0]

    def fn():
        calls[0] += 1
        return f"r{calls[0]}"

    r1 = PC.cached_llm_call("keyword", "m", [{"role": "user", "content": "y"}], fn)
    r2 = PC.cached_llm_call("keyword", "m", [{"role": "user", "content": "y"}], fn)
    assert calls[0] == 1
    assert r1 == r2


def test_main_answer_never_cached(clean_env, monkeypatch):
    """主答案任务（answer/reasoning）永不缓存 —— 每次新鲜生成。"""
    monkeypatch.setenv("HASHMM_PROMPT_CACHE", "1")
    monkeypatch.setenv("HASHMM_PIPELINE_CACHE", "1")
    from hashmm import prompt_cache as PC
    calls = [0]

    def fn():
        calls[0] += 1
        return f"r{calls[0]}"

    PC.cached_llm_call("answer", "m", [{"role": "user", "content": "z"}], fn)
    PC.cached_llm_call("answer", "m", [{"role": "user", "content": "z"}], fn)
    assert calls[0] == 2


def test_is_cacheable(clean_env):
    """缓存白名单：辅助任务可缓存，主答案不可。"""
    from hashmm import prompt_cache as PC
    assert PC.is_cacheable("keyword") is True
    assert PC.is_cacheable("title") is True
    assert PC.is_cacheable("answer") is False
    assert PC.is_cacheable("reasoning") is False


def test_empty_result_not_cached(clean_env, monkeypatch):
    """空结果不缓存（避免缓存失败响应）。"""
    monkeypatch.setenv("HASHMM_PROMPT_CACHE", "1")
    monkeypatch.setenv("HASHMM_PIPELINE_CACHE", "1")
    from hashmm import prompt_cache as PC
    calls = [0]

    def fn():
        calls[0] += 1
        return ""   # 空结果

    PC.cached_llm_call("keyword", "m", [{"role": "user", "content": "e"}], fn)
    PC.cached_llm_call("keyword", "m", [{"role": "user", "content": "e"}], fn)
    assert calls[0] == 2   # 空结果不缓存，第二次仍调用
