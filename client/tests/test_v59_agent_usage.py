"""V59 外部 agent token 用量监控（适配自 fanbox）：增量解析、去重、截断、配额快照。"""
import json
import time

import pytest

pytestmark = pytest.mark.unit


def _write_claude_jsonl(path, msgs):
    with open(path, "w", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")


def _claude_msg(mid, tin, tout, ts=None, model="claude-x"):
    return {"type": "assistant", "timestamp": ts or "2026-06-11T10:00:00Z",
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": tin, "output_tokens": tout,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0}}}


def test_incremental_parse_and_offset(tmp_path):
    from hashmm.agent import agent_usage as au
    au._claude_cache.clear()
    f = tmp_path / "sess.jsonl"
    _write_claude_jsonl(f, [_claude_msg("m1", 100, 20), _claude_msg("m2", 50, 10)])
    st = f.stat()
    ev = au.parse_claude_file(f, st.st_size, st.st_mtime * 1000)
    assert len(ev) == 2 and ev[0]["in"] == 100
    # 追加一条 → 增量只解析新行
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_claude_msg("m3", 7, 3)) + "\n")
    st2 = f.stat()
    ev2 = au.parse_claude_file(f, st2.st_size, st2.st_mtime * 1000)
    assert len(ev2) == 3 and ev2[2]["in"] == 7


def test_duplicate_msg_id_counted_once(tmp_path):
    from hashmm.agent import agent_usage as au
    au._claude_cache.clear()
    f = tmp_path / "dup.jsonl"
    # 同一 message.id 落两行（Claude Code 真实行为）→ 只记一次
    _write_claude_jsonl(f, [_claude_msg("same", 100, 20), _claude_msg("same", 100, 20)])
    st = f.stat()
    ev = au.parse_claude_file(f, st.st_size, st.st_mtime * 1000)
    assert len(ev) == 1


def test_synthetic_model_skipped(tmp_path):
    from hashmm.agent import agent_usage as au
    au._claude_cache.clear()
    f = tmp_path / "syn.jsonl"
    _write_claude_jsonl(f, [_claude_msg("s1", 9, 9, model="<synthetic>"),
                            _claude_msg("s2", 5, 5)])
    st = f.stat()
    ev = au.parse_claude_file(f, st.st_size, st.st_mtime * 1000)
    assert len(ev) == 1 and ev[0]["in"] == 5


def test_truncation_resets_cache(tmp_path):
    from hashmm.agent import agent_usage as au
    au._claude_cache.clear()
    f = tmp_path / "trunc.jsonl"
    _write_claude_jsonl(f, [_claude_msg("a", 100, 10), _claude_msg("b", 100, 10)])
    st = f.stat()
    au.parse_claude_file(f, st.st_size, st.st_mtime * 1000)
    # 文件被改小重写（size < offset）→ 缓存重置，按新内容重来
    _write_claude_jsonl(f, [_claude_msg("c", 1, 1)])
    st2 = f.stat()
    ev = au.parse_claude_file(f, st2.st_size, st2.st_mtime * 1000)
    assert len(ev) == 1 and ev[0]["in"] == 1


def test_partial_line_held_for_next_round(tmp_path):
    from hashmm.agent import agent_usage as au
    au._claude_cache.clear()
    f = tmp_path / "partial.jsonl"
    # 写一条完整 + 一条没有换行结尾的半截
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(_claude_msg("done", 10, 2)) + "\n")
        fh.write('{"type":"assistant","message":{"id":"half","usage":{"input_')  # 断行
    st = f.stat()
    ev = au.parse_claude_file(f, st.st_size, st.st_mtime * 1000)
    assert len(ev) == 1                      # 半截不消费
    # 补全这一行
    with open(f, "a", encoding="utf-8") as fh:
        fh.write('tokens":30,"output_tokens":4}}}\n')
    st2 = f.stat()
    ev2 = au.parse_claude_file(f, st2.st_size, st2.st_mtime * 1000)
    assert len(ev2) == 2 and ev2[1]["in"] == 30


def test_claude_usage_windows(tmp_path):
    from hashmm.agent import agent_usage as au
    au._claude_cache.clear()
    proj = tmp_path / "projects" / "p1"
    proj.mkdir(parents=True)
    now = time.time() * 1000
    old_iso = "2020-01-01T00:00:00Z"   # 远古（不进任何窗口）
    f = proj / "s.jsonl"
    import datetime as dt
    recent_iso = dt.datetime.fromtimestamp(now / 1000).isoformat()
    _write_claude_jsonl(f, [_claude_msg("r", 100, 20, ts=recent_iso),
                            _claude_msg("o", 999, 999, ts=old_iso)])
    _bak = au.CLAUDE_PROJ
    au.CLAUDE_PROJ = tmp_path / "projects"
    try:
        u = au.claude_usage(now_ms=now)
    finally:
        au.CLAUDE_PROJ = _bak
    assert u is not None
    assert u["today"]["input"] == 100        # 只算近期那条
    assert u["week"]["msgs"] == 1


def test_missing_agents_return_none(tmp_path):
    from hashmm.agent import agent_usage as au
    _b1, _b2 = au.CLAUDE_PROJ, au.CODEX_SESS
    au.CLAUDE_PROJ = tmp_path / "nope"
    au.CODEX_SESS = tmp_path / "nope2"
    try:
        assert au.claude_usage() is None
        assert au.codex_usage() is None
        assert au.all_agent_usage() == {"claude_code": None, "codex": None}
    finally:
        au.CLAUDE_PROJ, au.CODEX_SESS = _b1, _b2


def test_codex_snapshot_extraction():
    from hashmm.agent.agent_usage import _extract_codex_snapshot
    d = {"payload": {"info": {
        "token_count": {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
        "rate_limits": {"primary": {"used_percent": 42}}}}}
    snap = _extract_codex_snapshot(d)
    assert snap["tokens"]["total"] == 1200
    assert snap["rate_limits"]["primary"]["used_percent"] == 42
    assert _extract_codex_snapshot({"foo": "bar"}) is None
