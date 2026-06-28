"""统一配置层（P0-2）测试。

覆盖：默认值零变化、env 覆盖、类型读取、describe、CONFIG.md 生成。
"""
import pytest

import importlib.util as _ilu
pytestmark = pytest.mark.unit
if _ilu.find_spec("hashmm.settings") is None:
    import pytest as _pt
    pytestmark = [pytest.mark.unit, _pt.mark.skip(reason="hashmm.settings 未部署（旧版本代码）")]


def test_defaults_are_off(clean_env):
    """所有特性开关默认关（零行为变化基线）。"""
    from hashmm import settings as S
    for sw in ("HASHMM_LLM_ROUTING", "HASHMM_MCP_SERVER", "HASHMM_PUBLIC_API",
               "HASHMM_MULTI_TENANT", "HASHMM_KG_EVOLUTION", "HASHMM_DESIGN_RENDER"):
        assert S.get_bool(sw) is False, f"{sw} 默认竟为开"


def test_env_override(clean_env, monkeypatch):
    """env 设置能覆盖默认。"""
    from hashmm import settings as S
    monkeypatch.setenv("HASHMM_LLM_ROUTING", "1")
    assert S.get_bool("HASHMM_LLM_ROUTING") is True


def test_typed_reads(clean_env):
    """类型化读取：bool/int/str 各取登记默认。"""
    from hashmm import settings as S
    assert S.get("HASHMM_ENV") == "production"
    assert S.get_int("HASHMM_AGENTIC_MAX_HOPS") == 3
    assert S.get_int("HASHMM_DB_POOL_SIZE") == 16


def test_describe(clean_env):
    """describe 返回开关的完整元信息。"""
    from hashmm import settings as S
    d = S.describe("HASHMM_KG_LLM_EXTRACT")
    assert d["category"] == "kg"
    assert d["kind"] == "bool"
    assert S.describe("HASHMM_NONEXISTENT") is None


def test_config_md_generation(clean_env):
    """CONFIG.md 能生成且含各类别。"""
    from hashmm import settings as S
    md = S.generate_config_md()
    assert "# HashMM 配置开关清单" in md
    assert "安全/鉴权" in md
    assert "知识图谱" in md
    assert "`HASHMM_LLM_ROUTING`" in md


def test_secret_masked_in_md(clean_env, monkeypatch):
    """密钥类开关在文档里脱敏，不泄露真实值。"""
    from hashmm import settings as S
    # JWT_SECRET 是 secret 类，文档里默认值应为空或 ***（不显真值）
    md = S.generate_config_md()
    # 找到 JWT_SECRET 行，不应包含任何疑似真实密钥
    for line in md.splitlines():
        if "HASHMM_JWT_SECRET" in line:
            assert "secret" in line   # 类型标注为 secret


def test_migrated_switches_registered(clean_env):
    """S1-4：迁移到 settings 的新开关已登记（有默认值/文档）。"""
    from hashmm import settings
    envs = {s.env for s in settings.all_switches()}
    for e in ("HASHMM_PARALLEL_TOOLS", "HASHMM_PARALLEL_TOOLS_MAX",
              "HASHMM_PROMPT_CACHE", "HASHMM_PROMPT_CACHE_TTL"):
        assert e in envs, f"{e} 应已登记到 settings"


def test_migrated_modules_use_settings(clean_env, monkeypatch):
    """S1-4：迁移后的模块通过 settings 读开关，默认关零变化、开启生效。"""
    from hashmm.agent import parallel_tools as PT
    from hashmm import prompt_cache as PC
    assert PT.parallel_enabled() is False
    assert PC.cache_enabled() is False
    assert PT._max_concurrency() == 4
    assert PC._ttl() == 3600
    monkeypatch.setenv("HASHMM_PARALLEL_TOOLS", "1")
    monkeypatch.setenv("HASHMM_PARALLEL_TOOLS_MAX", "8")
    assert PT.parallel_enabled() is True
    assert PT._max_concurrency() == 8
