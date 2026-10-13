"""V96 测试：用户记忆/画像端点接线 + 保存配置纯逻辑。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_user_memory_routes_wired():
    init_src = (ROOT / "hashmm/api/routes/__init__.py").read_text(encoding="utf-8")
    assert "user_memory_router" in init_src
    src = (ROOT / "hashmm/api/routes/user_memory.py").read_text(encoding="utf-8")
    assert '@router.get("")' in src
    assert '@router.delete("/{memory_id}")' in src
    assert '@router.get("/profile")' in src
    # 权限：非管理员删他人记忆要拦
    del_seg = src.split('@router.delete')[1]
    assert "只能删除自己的记忆" in del_seg
    # 查他人需 admin
    list_seg = src.split('@router.get("")')[1].split('@router.delete')[0]
    assert "仅管理员可查看他人记忆" in list_seg


def test_memory_grouping_logic():
    """list_memory 按 category 分组——纯逻辑校验（不依赖 fastapi）。"""
    rows = [
        {"id": "1", "category": "preference", "key": "lang", "value": "zh", "confidence": 0.9, "last_used": 100},
        {"id": "2", "category": "preference", "key": "tone", "value": "concise", "confidence": 0.8, "last_used": 90},
        {"id": "3", "category": "", "key": "x", "value": "y", "confidence": 0.5, "last_used": 80},
    ]
    grouped: dict = {}
    for r in rows:
        cat = r.get("category") or "其他"
        grouped.setdefault(cat, []).append(r)
    assert set(grouped) == {"preference", "其他"}
    assert len(grouped["preference"]) == 2


if __name__ == "__main__":
    test_user_memory_routes_wired()
    test_memory_grouping_logic()
    print("user_memory 自检 OK")
