"""V58 内置设计技能（huashu-design 适配版）：注册幂等、触发注入、未命中零变化。"""
import pytest

pytestmark = pytest.mark.unit


def _cleanup():
    try:
        from hashmm.api import database as db
        with db._conn() as c:
            c.execute("DELETE FROM skills WHERE id='builtin-huashu-design'")
        from hashmm.evolution import skill_manager as sm
        if sm._manager is not None:
            sm._manager._skills = [s for s in sm._manager._skills
                                   if s.id != "builtin-huashu-design"]
    except Exception:
        pass


def test_ensure_idempotent():
    from hashmm.agent.builtin_skills import ensure_builtin_skills
    _cleanup()
    assert ensure_builtin_skills() is True      # 首次：写入
    assert ensure_builtin_skills() is False     # 同版本：不动（保留质量分演化）


def test_trigger_match_and_inject():
    from hashmm.agent.builtin_skills import ensure_builtin_skills, HUASHU_SKILL_ID
    from hashmm.evolution.skill_manager import get_skill_manager
    ensure_builtin_skills()
    mgr = get_skill_manager()
    hits = mgr.match_skills("帮我做一个产品发布会的PPT，要好看")
    assert any(s.id == HUASHU_SKILL_ID for s in hits)
    msgs = mgr.inject_skill_context("做个发布会海报",
                                    [{"role": "system", "content": "助手"}])
    assert "设计模式" in msgs[0]["content"]
    assert "三方向" in msgs[0]["content"]
    assert "create_file" in msgs[0]["content"]   # 产出协议适配进了提示


def test_unrelated_query_zero_change():
    from hashmm.agent.builtin_skills import ensure_builtin_skills
    from hashmm.evolution.skill_manager import get_skill_manager
    ensure_builtin_skills()
    base = [{"role": "system", "content": "助手"}]
    out = get_skill_manager().inject_skill_context("解释一下快速排序的时间复杂度", base)
    assert "设计模式" not in out[0]["content"]


def test_loop_build_messages_injects_on_design_query():
    from hashmm.agent.loop import AgentLoop
    from hashmm.agent.builtin_skills import ensure_builtin_skills
    ensure_builtin_skills()
    loop = AgentLoop(llm_fn=object(), system_prompt="助手", conv_id="cDesign")
    sys_design = loop._build_messages("给我们的开源项目做一个落地页", [], "")[0]["content"]
    assert "设计模式" in sys_design and "反 AI slop" in sys_design
    sys_plain = loop._build_messages("查一下腾讯营收", [], "")[0]["content"]
    assert "设计模式" not in sys_plain            # 未命中零变化


def test_assets_archived_with_attribution():
    from pathlib import Path
    base = Path("hashmm/skills/huashu-design")
    assert (base / "SKILL.md").exists()
    assert (base / "design-styles.md").exists()
    attr = (base / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "MIT" in attr and "alchaincyf" in attr
