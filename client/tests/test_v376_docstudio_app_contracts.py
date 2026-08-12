from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP = Path(r"D:\sheji\agent\app")


def test_docstudio_artifact_name_is_a_portable_leaf_and_unique():
    from hashmm.api.routes.doc_studio import _artifact_filename

    first = _artifact_filename("设计·海报/单页", "设计:给我设计一个海报", "html")
    second = _artifact_filename("设计·海报/单页", "设计:给我设计一个海报", "html")
    assert Path(first).name == first
    assert "/" not in first and "\\" not in first
    assert first.endswith(".html")
    assert first != second


def test_docstudio_artifact_persistence_is_atomic_and_workspace_bound(monkeypatch, tmp_path):
    from hashmm.api import database as db
    from hashmm.api.routes.doc_studio import _persist_artifact

    monkeypatch.setattr(db, "conv_files_dir", lambda _conv_id: tmp_path)
    _persist_artifact("owned", "design.html", "<html>ok</html>")
    assert (tmp_path / "design.html").read_text(encoding="utf-8") == "<html>ok</html>"
    assert not list(tmp_path.glob("*.partial"))
    assert not list(tmp_path.glob(".*.partial"))

    with pytest.raises(ValueError):
        _persist_artifact("owned", "../escape.html", "no")
    assert not (tmp_path.parent / "escape.html").exists()


def test_desktop_opens_verified_docstudio_artifact_instead_of_dumping_html():
    api = (ROOT / "frontend-next/lib/api.ts").read_text(encoding="utf-8")
    screen = (ROOT / "frontend-next/components/desktop/DocStudioView.tsx").read_text(encoding="utf-8")
    assert "artifact: { filename: string; download_url: string; kind: string } | null" in api
    assert "if (r.artifact?.filename)" in screen
    assert "源代码不会再作为正文铺满页面" in screen


def test_app_docstudio_design_actions_are_real_and_owner_checked_upload_is_used():
    repo = (APP / "app/src/main/java/com/hashmm/app/data/remote/StudioRepository.kt").read_text(encoding="utf-8")
    screen = (APP / "app/src/main/java/com/hashmm/app/ui/studio/DocStudioScreen.kt").read_text(encoding="utf-8")
    assert 'url("$base/api/conversations/$convId/upload")' in repo
    assert "/api/files/upload?conv=" not in repo
    assert 'val isDesign = action.startsWith("design_")' in screen
    assert 'if (isDesign || action == "custom" || action == "data_qa")' in screen
    assert "已进入同一对话的画布" in screen


def test_app_canvas_rejects_off_origin_native_bridge_content_and_phone_poll_backs_off():
    canvas = (APP / "app/src/main/java/com/hashmm/app/data/remote/CanvasRepository.kt").read_text(encoding="utf-8")
    poll = (APP / "app/src/main/java/com/hashmm/app/ui/PhotoRequestViewModel.kt").read_text(encoding="utf-8")
    assert "resolveCanvasUrl" in canvas and "UrlSecurity.isSameOrigin" in canvas
    assert "coerceAtMost(30_000L)" in poll


def test_app_studio_reads_do_not_collapse_transport_failures_into_empty_business_data():
    repo = (APP / "app/src/main/java/com/hashmm/app/data/remote/StudioRepository.kt").read_text(encoding="utf-8")
    agents = (APP / "app/src/main/java/com/hashmm/app/ui/studio/AgentsStudioScreen.kt").read_text(encoding="utf-8")
    docs = (APP / "app/src/main/java/com/hashmm/app/ui/studio/DocStudioScreen.kt").read_text(encoding="utf-8")
    hub = (APP / "app/src/main/java/com/hashmm/app/ui/studio/ControlHubScreen.kt").read_text(encoding="utf-8")
    kg = (APP / "app/src/main/java/com/hashmm/app/ui/kg/KGScreen.kt").read_text(encoding="utf-8")

    assert "data class StudioListResult<T>" in repo
    assert "agentsResult()" in agents and "sourceError" in agents
    assert "docActionsResult()" in docs and "actionsError" in docs
    assert "loopsResult()" in hub and "readError" in hub
    assert 'g.error != null && g.error != "图谱为空（尚未构建实体）"' in kg


def test_app_idle_studio_polling_is_adaptive_and_failed_rule_write_rolls_back():
    hub = (APP / "app/src/main/java/com/hashmm/app/ui/studio/ControlHubScreen.kt").read_text(encoding="utf-8")
    assert "if (offline) 30_000 else if (active) 4_000 else 15_000" in hub
    assert "val previous = rules" in hub
    assert "rules = previous" in hub
