from pathlib import Path

from hashmm.agent import team


ROOT = Path(__file__).resolve().parents[1]


def test_team_members_have_stable_child_thread_identity_and_timestamps():
    team_id = "tm-v353-thread"
    with team._TEAMS_LOCK:
        team._TEAMS.pop(team_id, None)
    try:
        team._team_new(
            team_id,
            {"uid": "owner", "sub": "owner"},
            "完成一份核验报告",
            "conv-parent",
            "team.html",
            [{"role": "研究员", "task": "核验事实"}, {"role": "审校员", "task": "检查遗漏"}],
        )
        state = team.get_team(team_id, "owner")
        assert state and len(state["roles"]) == 2
        first = state["roles"][0]
        assert first["agent_id"].startswith("agent-1-")
        assert first["thread_id"].startswith(team_id + ":")
        assert first["parent_thread_id"] == "conv-parent"
        assert first["created_at"] > 0
        assert first["started_at"] is None and first["finished_at"] is None

        team._team_role(team_id, 0, "run")
        running = team.get_team(team_id, "owner")["roles"][0]
        assert running["started_at"] > 0 and running["finished_at"] is None
        team._team_role(team_id, 0, "ok", finding="已核验")
        done = team.get_team(team_id, "owner")["roles"][0]
        assert done["finished_at"] >= done["started_at"]

        record = team._orchestration_record(team_id, "parallel", team.get_team(team_id, "owner")["roles"])
        assert record["members"][0]["thread_id"] == done["thread_id"]
        assert record["members"][0]["parent_thread_id"] == "conv-parent"
    finally:
        with team._TEAMS_LOCK:
            team._TEAMS.pop(team_id, None)


def test_unified_workspace_contract_is_wired_to_chat_and_isolated_desktop_browser():
    render = (ROOT / "frontend-next/lib/render.ts").read_text(encoding="utf-8")
    renderer = (ROOT / "frontend-next/components/MessageRenderer.tsx").read_text(encoding="utf-8")
    workspace = (ROOT / "frontend-next/components/WorkspaceInspector.tsx").read_text(encoding="utf-8")
    browser = (ROOT / "frontend-next/components/BrowserInspector.tsx").read_text(encoding="utf-8")
    desktop_main = (ROOT / "desktop/main.js").read_text(encoding="utf-8")
    desktop_preload = (ROOT / "desktop/preload.js").read_text(encoding="utf-8")
    artifact = (ROOT / "frontend-next/lib/artifact.ts").read_text(encoding="utf-8")

    assert 'data-hashmm-browser-link="1"' in render
    assert "openBrowserInInspector(anchor.href" in renderer
    assert '<BrowserInspector />' in workspace
    # V355: remote pages no longer live in renderer <webview>.  Electron owns a
    # sandboxed WebContentsView and exposes only narrow navigation/bounds IPC.
    assert "new WebContentsView" in desktop_main
    assert 'partition: "persist:browser-use"' in desktop_main
    assert "nodeIntegration: false" in desktop_main and "sandbox: true" in desktop_main
    assert "embeddedMount" in browser and "embeddedNavigate" in browser
    assert "browser:embeddedMount" in desktop_preload
    assert 'createElement("webview")' not in browser and "<webview" not in browser
    assert "artifactTabs" in artifact and ".slice(-8)" in artifact


def test_canvas_click_opens_a_local_artifact_when_persistence_is_unavailable():
    chat = (ROOT / "frontend-next/components/ChatArea.tsx").read_text(encoding="utf-8")
    panel = (ROOT / "frontend-next/components/ArtifactPanel.tsx").read_text(encoding="utf-8")
    assert 'artifactDraft: { filename: fname, content: html }' in chat
    assert 'openArtifact(sid, { filename: fname, download_url: "" })' in chat
    assert 'artifactDraft: { filename, content: m.html }' in panel
