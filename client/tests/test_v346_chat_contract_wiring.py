"""Static cross-client gates for the V346 semantic task contract.

These assertions do not replace runtime tests.  They protect the named links
that previously regressed independently: backend SSE, Desktop parsing/store,
Supabase payloads, App history models, and real transport cancellation.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = Path(r"D:\sheji\agent\app\app\src\main\java\com\hashmm\app")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backend_emits_and_persists_semantic_completion_data():
    streaming = _read(ROOT / "hashmm/api/streaming.py")
    database = _read(ROOT / "hashmm/api/database.py")
    supabase = _read(ROOT / "hashmm/api/supabase_sync.py")
    assert '_sse("task_contract"' in streaming
    assert '"run_manifest": run_manifest' in streaming
    assert "async for ev in _stream_llm_async" in streaming
    assert '_generation_stop_reason = "interrupted"' in streaming
    for field in ("groundings", "run_manifest"):
        assert field in database
        assert field in supabase


def test_desktop_consumes_contract_and_final_handoff():
    api = _read(ROOT / "frontend-next/lib/api.ts")
    store = _read(ROOT / "frontend-next/lib/store.ts")
    panel = _read(ROOT / "frontend-next/components/RightContextPanel.tsx")
    assert 'eventType === "task_contract"' in api
    assert "taskContract" in store
    assert "runManifest.handoff" in panel


def test_app_keeps_history_fields_and_cancels_transport():
    sync_model = _read(APP / "data/sync/SyncModels.kt")
    repository = _read(APP / "data/remote/ChatLiveRepository.kt")
    direct = _read(APP / "data/remote/DirectLlmRepository.kt")
    manager = _read(APP / "data/remote/LiveChatManager.kt")
    detail = _read(APP / "ui/chat/ChatDetailViewModel.kt")
    home = _read(APP / "ui/chat/ChatHomeViewModel.kt")
    assert '@SerialName("run_manifest")' in sync_model
    assert "groundings" in sync_model
    assert "activeStreams" in repository and ".cancel()" in repository
    assert "activeCalls" in direct and "fun cancel(requestKey: String)" in direct
    assert '"task_contract"' in manager and '"progress"' in manager
    assert "liveManager.cancel(convId)" in detail
    assert "liveManager.cancel(it)" in home
