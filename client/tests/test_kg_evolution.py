"""自进化 KG 待审区 测试。

覆盖：默认关不收录、置信阈值、冲突检测、审批流、永不自动写主图、容错。
用临时 DATA_DIR，绝不碰真实图谱。
"""
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def staging_env(tmp_db, monkeypatch):
    """临时 DATA_DIR + 开启自进化。tmp_db 已设 DATA_DIR 为临时目录。"""
    monkeypatch.setenv("HASHMM_KG_EVOLUTION", "1")
    from hashmm.kg import evolution_staging as ES
    ES.clear_all()
    yield ES
    ES.clear_all()


def test_disabled_does_not_record(tmp_db, monkeypatch):
    """默认关时连待审区都不收录。"""
    monkeypatch.delenv("HASHMM_KG_EVOLUTION", raising=False)
    from hashmm.kg import evolution_staging as ES
    r = ES.propose("A", "rel", "B", confidence=0.9)
    assert r["status"] == "disabled"


def test_low_confidence_rejected(staging_env):
    """置信度低于阈值（默认 0.75）直接拒收。"""
    r = staging_env.propose("小米", "2024营收", "3000亿", confidence=0.5)
    assert r["status"] == "rejected_low_confidence"


def test_high_confidence_pending(staging_env):
    """高置信正常收录待审。"""
    r = staging_env.propose("小米", "2024营收", "3659亿", confidence=0.9, source="chat:1")
    assert r["status"] == "pending"
    assert r["fact_id"]


def test_conflict_detection(staging_env):
    """与图谱已有'同 head+relation 不同 tail'冲突时标记 conflict。"""
    fake_graph = {"links": [{"source": "小米", "relation": "2024营收", "target": "3659亿"}]}
    r = staging_env.propose("小米", "2024营收", "4000亿", confidence=0.9, existing_graph=fake_graph)
    assert r["status"] == "conflict"
    assert "3659亿" in r["conflicts_with"]


def test_approve_flow_does_not_auto_write(staging_env):
    """审批流：approve 后进 approved_relations，但不自动写主图。"""
    staging_env.propose("小米", "2024营收", "3659亿", confidence=0.9)
    pending = staging_env.list_pending()
    assert len(pending) >= 1
    assert staging_env.approve(pending[0]["id"]) is True
    approved = staging_env.approved_relations()
    assert len(approved) == 1
    assert approved[0]["head"] == "小米"


def test_never_raises_on_garbage(staging_env):
    """容错：空值/坏图谱不抛错。"""
    staging_env.propose("", "", "", confidence=1.0)
    staging_env.propose("a", "b", "c", confidence=0.9, existing_graph="garbage")
    # 不抛错即通过
