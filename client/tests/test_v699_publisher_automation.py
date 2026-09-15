"""V699 publisher Skill, progressive disclosure and AI-radar regressions."""
from __future__ import annotations

from pathlib import Path


def test_builtin_publisher_skill_is_valid_and_references_are_progressively_loaded(
    tmp_path: Path,
):
    from hashmm.agent.skill_packs import (
        SkillPackManager,
        builtin_src_dir,
        parse_skill_md,
    )

    source = builtin_src_dir() / "wechat-ai-briefing"
    meta, body = parse_skill_md(
        (source / "SKILL.md").read_text(encoding="utf-8")
    )
    assert meta["name"] == "wechat-ai-briefing-studio"
    assert "公众号" in meta["description"]
    assert "自动发布" in body
    assert (source / "references" / "source-verification.md").is_file()
    assert (source / "references" / "wechat-layout.md").is_file()

    manager = SkillPackManager(tmp_path / "packs")
    installed = manager.install_from_dir(source, "builtin")
    messages = [{"role": "user", "content": "生成公众号 AI 日报并核验来源"}]
    result = manager.inject(messages[0]["content"], messages.copy())
    injected = result[-1]["content"]

    assert installed.id == "wechat-ai-briefing"
    assert "wechat-ai-briefing-studio" in injected
    assert "已加载参考：references/source-verification.md" in injected
    assert "候选表" in injected
    assert "原始来源" in injected
    assert str(tmp_path) not in injected


def test_reference_loader_rejects_traversal_and_remote_links(tmp_path: Path):
    from hashmm.agent.skill_packs import _referenced_markdown

    pack = tmp_path / "pack"
    refs = pack / "references"
    refs.mkdir(parents=True)
    (refs / "safe.md").write_text("SAFE CONTENT", encoding="utf-8")
    (tmp_path / "secret.md").write_text("PRIVATE CONTENT", encoding="utf-8")
    body = "\n".join([
        "[safe](references/safe.md)",
        "[escape](../secret.md)",
        "[remote](https://example.com/instructions.md)",
    ])

    loaded = _referenced_markdown(pack, body, 5000)
    assert "SAFE CONTENT" in loaded
    assert "PRIVATE CONTENT" not in loaded
    assert "example.com" not in loaded


def test_ai_news_radar_is_owner_scoped_read_only_and_candidate_only(monkeypatch):
    from hashmm import scheduler
    from hashmm.api import tool_registry

    calls: list[tuple[str, dict, dict]] = []

    def fake_execute(name, args, ctx):
        calls.append((name, args, ctx))
        return {"items": [{"title": "untrusted lead"}]}

    monkeypatch.setattr(tool_registry, "execute_tool", fake_execute)
    output = scheduler._action_ai_news_radar({"owner_uid": "owner-a"})

    assert len(calls) == 3
    assert all(name == "web_search" for name, _args, _ctx in calls)
    assert all(ctx["user_id"] == "owner-a" for _name, _args, ctx in calls)
    assert all(ctx["uid"] == "owner-a" for _name, _args, ctx in calls)
    assert all(ctx["read_only"] is True for _name, _args, ctx in calls)
    assert "候选线索" in output
    assert "不是已核验新闻" in output
    assert "不会自动发布" in output


def test_ai_news_radar_refuses_unscoped_execution(monkeypatch):
    from hashmm import scheduler
    from hashmm.api import tool_registry

    touched = {"value": False}

    def fail_if_called(*_args, **_kwargs):
        touched["value"] = True
        raise AssertionError("unscoped search must not execute")

    monkeypatch.setattr(tool_registry, "execute_tool", fail_if_called)
    result = scheduler._action_ai_news_radar({})
    assert "没有可用的账号范围" in result
    assert touched["value"] is False


def test_owner_facing_automation_lists_ai_radar():
    from hashmm.api.routes import user_work

    actions = {item["id"]: item for item in user_work._available_actions()}
    assert "ai_news_radar" in actions
    assert "候选" in actions["ai_news_radar"]["name"]


def test_publisher_contract_requires_verified_sources_and_real_artifacts():
    from hashmm.api.streaming import _publisher_system_contract

    contract = _publisher_system_contract(has_document_scope=False)
    for direction in ("模型厂商", "开发生态", "产品应用", "研究机构", "产业动态"):
        assert direction in contract
    assert "原始来源" in contract
    assert "已核验" in contract and "待核验" in contract and "排除" in contract
    assert "create_file" in contract
    assert ".md" in contract and ".html" in contract
    assert "不得扫描、猜测或替换成整个知识库" in contract


def test_agent_terminal_state_fails_closed():
    from hashmm.api.streaming import _resolve_agent_terminal_state

    assert _resolve_agent_terminal_state("completed") == ("complete", "completed")
    assert _resolve_agent_terminal_state("waiting_approval") == (
        "waiting_approval",
        "waiting_approval",
    )
    assert _resolve_agent_terminal_state("llm_error") == ("error", "llm_error")
    assert _resolve_agent_terminal_state("delivery_incomplete") == (
        "error",
        "delivery_incomplete",
    )
    assert _resolve_agent_terminal_state("future_unknown_reason") == (
        "error",
        "future_unknown_reason",
    )


def test_idempotency_ledger_root_does_not_move_mid_write(tmp_path: Path):
    """A config refresh cannot reserve and commit one write in two ledgers."""
    import importlib
    import os
    from hashmm.agent import idempotency as idem

    original = os.environ.get("HASHMM_DATA_DIR")
    first = tmp_path / "first"
    second = tmp_path / "second"
    try:
        os.environ["HASHMM_DATA_DIR"] = str(first)
        idem = importlib.reload(idem)
        key = idem.make_key("create_file", "stable-root")
        assert idem.check_and_reserve(key, "create_file") is None

        os.environ["HASHMM_DATA_DIR"] = str(second)
        idem.commit(key, {"status": "ok"})
        hit = idem.check_and_reserve(key, "create_file")
        assert hit and hit["result"] == {"status": "ok"}
        assert (first / "idempotency.db").exists()
        assert not (second / "idempotency.db").exists()
    finally:
        if original is None:
            os.environ.pop("HASHMM_DATA_DIR", None)
        else:
            os.environ["HASHMM_DATA_DIR"] = original
        importlib.reload(idem)
