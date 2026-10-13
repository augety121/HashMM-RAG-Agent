#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建 KG 向量库（EntityVDB / RelationVDB）—— 让 KG 语义检索用上当前的图。

为什么需要：改图/重建图后，磁盘上的 entity.faiss / relation.faiss 是旧的。
本次实测服务加载到 `EntityVDB 12 / RelationVDB 7946`，但当前图是 543 实体 / 557 关系
（7946 是老正则图的关系数）——KG 语义检索一直在用错的索引。本命令从当前图重新编码并覆盖保存。

注意：需要 BGE-M3 编码器（占显存）。**请先停掉服务再跑**（避免和服务抢 GPU）。不调用 LLM，不动图结构。

用法：
    # 先 Ctrl+C 停掉 uvicorn 服务，然后：
    CUDA_VISIBLE_DEVICES=0 python -m hashmm.kg.kg_vdb_rebuild
"""
from __future__ import annotations

import sys

from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.vdb_rebuild")


def _old_counts() -> tuple[int, int]:
    """读旧 vdb 的条目数（仅用于打印对比）。失败返回 (-1,-1)。"""
    e = r = -1
    try:
        from hashmm.kg.entity_vdb import EntityVDB
        ev = EntityVDB()
        if ev.load():
            e = len(ev)
    except Exception:
        pass
    try:
        from hashmm.kg.relation_vdb import RelationVDB
        rv = RelationVDB()
        if rv.load():
            r = len(rv)
    except Exception:
        pass
    return e, r


def rebuild() -> dict:
    """从当前已存图重建并覆盖保存 entity/relation VDB。返回统计；永不抛错。"""
    out = {"ok": False, "entities": 0, "relations": 0,
           "old_entities": -1, "old_relations": -1, "kg_entities": 0, "kg_relations": 0}
    try:
        from hashmm.kg.storage import KGStorage
        from hashmm.kg.entity_vdb import EntityVDB
        from hashmm.kg.relation_vdb import RelationVDB
    except Exception as e:
        out["error"] = f"import failed: {e!r}"
        return out

    out["old_entities"], out["old_relations"] = _old_counts()

    try:
        kg, _ = KGStorage().load()
    except Exception as e:
        out["error"] = f"load KG failed: {e!r}"
        return out
    out["kg_entities"] = getattr(kg, "num_entities", 0)
    out["kg_relations"] = getattr(kg, "num_relations", 0)

    try:
        ev = EntityVDB()
        out["entities"] = ev.build_from_kg(kg)   # 用 BGE-M3 重新编码
        ev.save()
    except Exception as e:
        out["error"] = f"entity vdb rebuild failed: {e!r}"
        return out

    try:
        rv = RelationVDB()
        out["relations"] = rv.build_from_kg(kg)
        rv.save()
    except Exception as e:
        out["error"] = f"relation vdb rebuild failed: {e!r}"
        return out

    out["ok"] = True
    return out


def main() -> int:
    print("=" * 64)
    print("重建 KG 向量库（EntityVDB / RelationVDB）—— 需 BGE-M3，请先停服务")
    print("=" * 64)
    res = rebuild()
    print(f"当前图：实体 {res['kg_entities']} / 关系 {res['kg_relations']}")
    print(f"旧索引：实体 {res['old_entities']} / 关系 {res['old_relations']}  ← 过期")
    if res.get("ok"):
        print(f"✓ 重建完成：实体向量 {res['entities']} / 关系向量 {res['relations']}（已覆盖保存）")
        print("\n下一步：重启服务，KG 语义检索即用上新索引。")
        return 0
    print(f"✗ 失败：{res.get('error')}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
