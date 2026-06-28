"""Skill 意图技能 + 设计能力 测试。

覆盖：skill 加载、触发匹配精准度、设计方法论注入、PPT 主题。
"""
import pytest

pytestmark = pytest.mark.unit


def test_skills_load():
    """skill 从 skills/*.json 加载成功。"""
    from hashmm.api import intent_engine as IE
    skills = IE.load_skills()
    assert len(skills) >= 1
    names = [s.get("name") for s in skills]
    assert "设计与原型" in names
    assert "设计评审" in names


def test_skill_trigger_matching():
    """触发匹配精准：设计/评审命中对应技能，财务查询不误触发设计。"""
    from hashmm.api import intent_engine as IE
    IE.load_skills()
    design = [m.get("name") for m in IE.match_skills("帮我做个落地页原型")]
    assert "设计与原型" in design
    critique = [m.get("name") for m in IE.match_skills("给这个设计评审打分")]
    assert "设计评审" in critique
    fin = [m.get("name") for m in IE.match_skills("小米2024营收多少")]
    assert "财务数据分析" in fin
    assert "设计与原型" not in fin   # 不误触发


def test_ppt_prompt_has_design_methodology():
    """PPT 生成 prompt 注入了 huashu-design 设计方法论。"""
    from hashmm.api import prompts
    p = prompts.get_system_prompt("pptx")
    for principle in ("一页一个核心", "反 AI slop", "标题即结论"):
        assert principle in p


def test_html_prompt_exists_with_antislop():
    """HTML 生成 prompt 存在且含反 slop 方法论。"""
    from hashmm.api import prompts
    h = prompts.get_system_prompt("html")
    assert "反 AI slop" in h


def test_ppt_themes_available():
    """新增的现代配色主题就位。"""
    import hashmm.api.pptx_builder as PB
    src = open(PB.__file__).read()
    for theme in ("ink", "forest", "midnight", "warmgray"):
        assert f'"{theme}"' in src
