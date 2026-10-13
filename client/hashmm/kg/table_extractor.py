"""hashmm/kg/table_extractor.py — 表格 → 知识图谱（V104 P2，对标 EvoGraph-R1 多模态超图）。

问题：表格此前只被拍平成 markdown 文本进检索，"小米2024营收多少"这类"行键×列头→值"的
结构化问题答不好。本模块把每个数据格具体化(reification)成一条可查三元组：

    行键 --[列头]--> 值        例：小米集团 --[2024营收]--> 3659亿

这正是 EvoGraph-R1 把"表格行"当作 n 元关系接进图谱的落地方式——用成对边表达，
零改图模型、对检索最友好。另加一个"表格实体"做导航与跨模态接地（链回所属章节）。

设计铁律：纯 Python（无 LLM、无新依赖）、确定性、**永不抛错**（任何异常 → 返回空）、
关系数量封顶（防超大表撑爆图谱）、(head,relation,tail) 去重。所有产物 modality="table"。
"""
from __future__ import annotations

from hashmm.kg.extractor import Entity, Relation

# 单元格清洗：去首尾空白、合并内部多空白、限长（防超长单元格）
_MAX_CELL = 120
_MAX_RELATIONS = 400      # 单表关系上限（防超大表）
_MAX_ROWS = 200           # 只处理前 N 行
_MAX_COLS = 40            # 只处理前 N 列


def _clean(cell) -> str:
    s = ("" if cell is None else str(cell)).strip()
    if not s:
        return ""
    s = " ".join(s.split())
    return s[:_MAX_CELL]


def _looks_like_header(cells: list[str]) -> bool:
    """首行是否像表头：非空单元格里"非纯数字"占多数（表头一般是文字）。"""
    vals = [c for c in cells if c]
    if not vals:
        return False
    non_numeric = sum(1 for c in vals if not _is_numeric(c))
    return non_numeric >= max(1, len(vals) // 2)


def _is_numeric(s: str) -> bool:
    t = s.replace(",", "").replace("，", "").replace("%", "").replace("¥", "").replace("$", "").strip()
    if not t:
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def extract_table_kg(
    table_data,
    *,
    caption: str = "",
    section: str = "",
    source_id: str = "",
    table_index: int = 0,
) -> tuple[list[Entity], list[Relation]]:
    """把一张表（rows×cols）转成 (entities, relations)。永不抛错，失败返回 ([], [])。

    Args:
        table_data: list[list[str]]，行×列；约定首行为表头、首列为行键。
        caption / section: 表标题 / 所属章节（用于命名表实体与跨模态接地）。
        source_id: 文档/块来源 id（写入 source_ids，做溯源）。
        table_index: 同文档内第几张表（命名兜底）。
    """
    try:
        return _extract(table_data, caption, section, source_id, table_index)
    except Exception:
        return [], []


def _extract(table_data, caption, section, source_id, table_index):
    if not table_data or not isinstance(table_data, (list, tuple)):
        return [], []
    # 清洗 + 裁剪规模
    rows = []
    for r in list(table_data)[:_MAX_ROWS]:
        if not isinstance(r, (list, tuple)):
            continue
        rows.append([_clean(c) for c in list(r)[:_MAX_COLS]])
    # 丢全空行
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        return [], []
    ncol = max(len(r) for r in rows)
    if ncol < 2:
        return [], []
    # 补齐每行列数
    rows = [r + [""] * (ncol - len(r)) for r in rows]

    headers = rows[0]
    if not _looks_like_header(headers):
        # 首行不像表头：用"列1/列2/…"兜底，且把首行当数据行
        headers = [f"列{c + 1}" for c in range(ncol)]
        data_rows = rows
    else:
        data_rows = rows[1:]

    src = [source_id] if source_id else []
    table_name = (caption or "").strip() or (f"表格@{section}".strip() if section else f"表格{table_index + 1}")
    table_name = table_name[:_MAX_CELL]

    entities: list[Entity] = []
    relations: list[Relation] = []
    seen_ent: set = set()
    seen_rel: set = set()

    def add_ent(name, etype):
        if not name:
            return
        k = (name, etype)
        if k in seen_ent:
            return
        seen_ent.add(k)
        entities.append(Entity(name=name, entity_type=etype, source_ids=list(src),
                               description=f"出自{table_name}", modality="table"))

    def add_rel(h, rel, t, ht, tt):
        if not (h and rel and t) or h == t:
            return
        k = (h, rel, t)
        if k in seen_rel:
            return
        seen_rel.add(k)
        relations.append(Relation(head=h, head_type=ht, relation=rel, tail=t, tail_type=tt,
                                   description=f"出自{table_name}", source_ids=list(src),
                                   modality="table"))

    # 表实体 + 跨模态接地（链回章节）
    add_ent(table_name, "表格")
    if section:
        add_ent(section, "章节")
        add_rel(table_name, "位于章节", section, "表格", "章节")

    # 逐格具体化为三元组：行键 --列头--> 值
    for r in data_rows:
        row_key = r[0]
        if not row_key:
            continue
        add_ent(row_key, "表行")
        add_rel(table_name, "包含行", row_key, "表格", "表行")
        for c in range(1, ncol):
            header = headers[c] if c < len(headers) else f"列{c + 1}"
            value = r[c]
            if not header or not value or value == row_key:
                continue
            add_rel(row_key, header, value, "表行", "值")
            if len(relations) >= _MAX_RELATIONS:
                return entities, relations
    return entities, relations
