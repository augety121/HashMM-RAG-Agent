"""V97 测试：跨会话搜索 + 技能策展（Hermes 范式）+ 安装类型逻辑。"""
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ── skill_curator 纯逻辑（Hermes curator 范式）──

def test_curator_archives_idle_keeps_pinned():
    from hashmm.api.skill_curator import assess_skill, assess_skills, summarize
    now = time.time()
    # 置顶永远 keep（即使久未用）
    pinned = {"id": "p", "pinned": True, "last_used": now - 999 * 86400, "use_count": 0}
    assert assess_skill(pinned, now)["action"] == "keep"
    # 久未用 + 低使用 → archive
    idle = {"id": "i", "last_used": now - 60 * 86400, "use_count": 1, "quality_score": 0.5}
    assert assess_skill(idle, now)["action"] == "archive"
    # 低质但在用 → flag（不归档）
    lowq = {"id": "l", "last_used": now - 1 * 86400, "use_count": 5, "quality_score": 0.1}
    assert assess_skill(lowq, now)["action"] == "flag_low_quality"
    # 活跃 → keep
    active = {"id": "a", "last_used": now - 1 * 86400, "use_count": 10, "quality_score": 0.8}
    assert assess_skill(active, now)["action"] == "keep"

    summary = summarize(assess_skills([pinned, idle, lowq, active], now))
    assert summary["archive"] == 1 and summary["keep"] == 2 and summary["flag_low_quality"] == 1


def test_curator_never_deletes_only_archives():
    """run_curation 只调 archive_skill，绝不删除。"""
    from hashmm.api.skill_curator import run_curation
    now = time.time()
    skills = [
        {"id": "old", "last_used": now - 90 * 86400, "use_count": 0, "quality_score": 0.5},
        {"id": "keep", "last_used": now, "use_count": 5, "quality_score": 0.9},
    ]
    archived = []
    r = run_curation(lambda: skills, lambda sid, reason: archived.append(sid),
                     enabled=True, now=now)
    assert r["ran"] and archived == ["old"]
    # 未启用时不动
    r2 = run_curation(lambda: skills, lambda sid, reason: archived.append(sid), enabled=False)
    assert r2["ran"] is False


def test_curator_should_run_interval():
    from hashmm.api.skill_curator import should_run
    now = time.time()
    assert should_run(now - 25 * 3600, interval_hours=24, now=now) is True
    assert should_run(now - 1 * 3600, interval_hours=24, now=now) is False


# ── session_search 纯逻辑 ──

def test_search_helpers():
    from hashmm.api.session_search import _fts_query, _snippet
    assert _fts_query("hello world") == '"hello" OR "world"'
    assert _fts_query('quote"inject') == '"quote" OR "inject"'  # 引号被剥离
    assert _fts_query("   ") == '""'
    snip = _snippet("这是一段很长的文本里面藏着关键词然后后面还有很多内容" * 3, "关键词")
    assert "关键词" in snip and len(snip) < 200


# ── 接线 ──

def test_search_endpoint_wired():
    src = (ROOT / "hashmm/api/routes/user_memory.py").read_text(encoding="utf-8")
    assert '@router.get("/search")' in src
    assert "session_search" in src


def test_install_state_logic():
    """安装类型判定逻辑（镜像 main.js detectInstallType 的分支）。"""
    def classify(prev, cur):
        if not prev or not prev.get("version"):
            return "fresh"
        if prev["version"] != cur:
            return "update"
        return "normal"
    assert classify(None, "1.2.0") == "fresh"
    assert classify({"version": "1.1.0"}, "1.2.0") == "update"
    assert classify({"version": "1.2.0"}, "1.2.0") == "normal"


if __name__ == "__main__":
    test_curator_archives_idle_keeps_pinned()
    test_curator_never_deletes_only_archives()
    test_curator_should_run_interval()
    test_search_helpers()
    test_install_state_logic()
    print("V97 search/curator 自检 OK")
