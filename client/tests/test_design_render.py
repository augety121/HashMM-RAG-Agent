"""受控设计渲染器 测试。

覆盖：默认关、白名单防护（危险命令全拒）、能力探测。
重点是安全边界——这是对外服务的关键。
"""
import pytest

pytestmark = pytest.mark.unit


def test_disabled_by_default(clean_env):
    """HASHMM_DESIGN_RENDER 未开 → 拒绝渲染（零暴露面）。"""
    from hashmm.api import design_render as DR
    assert DR.render_enabled() is False
    r = DR.html_to_image("<h1>x</h1>")
    assert r["ok"] is False
    assert "disabled" in r["reason"]


@pytest.mark.parametrize("danger", [
    ["rm", "-rf", "/"],
    ["curl", "http://evil.com"],
    ["bash", "-c", "whoami"],
    ["sh", "-c", "ls"],
    ["python3", "-c", "import os"],
    ["cat", "/etc/passwd"],
    ["wget", "x"],
])
def test_whitelist_rejects_dangerous_commands(danger):
    """白名单：任何非渲染命令一律拒绝（核心安全保证）。"""
    from hashmm.api import design_render as DR
    ok, msg = DR._run_whitelisted(danger)
    assert ok is False
    assert "白名单" in msg


@pytest.mark.parametrize("tool", [
    "playwright", "wkhtmltoimage", "ffmpeg", "libreoffice", "pandoc", "dot", "convert",
])
def test_whitelist_allows_render_tools(tool):
    """白名单：渲染工具名本身被允许（即使本机没装，不会因名字被拒）。

    用一个不存在的子命令触发，断言失败原因不是'白名单'（而是执行失败/工具缺失）。
    """
    from hashmm.api import design_render as DR
    ok, msg = DR._run_whitelisted([tool, "--__nonexistent_flag__"])
    # 可能因工具没装而失败，但不应是"白名单"拒绝
    if not ok:
        assert "白名单" not in msg


def test_capabilities_reports_structure(clean_env, monkeypatch):
    """能力探测返回完整结构（不执行命令，只探测）。"""
    monkeypatch.setenv("HASHMM_DESIGN_RENDER", "1")
    from hashmm.api import design_render as DR
    cap = DR.capabilities()
    for key in ("enabled", "html_to_image", "images_to_video", "office_to_image",
                "doc_convert", "diagram", "image_ops"):
        assert key in cap
    assert cap["enabled"] is True
