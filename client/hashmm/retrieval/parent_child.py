"""hashmm/retrieval/parent_child.py — 父子块检索（面试资料 5.4.2「召回内容上下文扩充」）。

思想（资料原文）：切块粒度小则**匹配精准**、但**上下文残缺**；粒度大则相反。父子块两全其美——
**用小的子块做向量匹配（精准），命中后返回其所属的大父块喂给 LLM（完整）**。对应 LangChain
的 ``ParentDocumentRetriever``（child_splitter 决定入库/召回粒度，parent_splitter 决定喂模型粒度）。

本模块是这套机制的**纯逻辑核心**（不碰索引/网络/GPU，便于单测），复用项目既有 ``TextChunker``
保证切分风格一致：
  · ``split_parent_child(text, doc_id)`` —— 入库侧：先切大父块，每个父块再切小子块，子块携带
    ``parent_id``。入库时**索引子块**（子块的 search_text），父块单独存起来备查。
  · ``build_parent_map(parents)`` —— 建 ``{parent_id: parent_text}`` 查表。
  · ``expand_to_parents(retrieved, parent_map, child_to_parent)`` —— 召回侧：把命中的子块
    **替换成其父块正文并去重**（既精准又完整）。父信息缺失时**原样返回**（安全降级：未启用
    父子块入库的旧语料不受影响）。

工程纪律（与 mqe.py / rerank.py 一致）：纯函数、可注入、**永不抛错**、默认关（``HASHMM_PARENT_CHILD``）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.retrieval.parent_child")

# 默认粒度（资料 5.2.5：父块=喂模型的大块，子块=入库匹配的小块）
DEFAULT_PARENT_SIZE = 1200
DEFAULT_CHILD_SIZE = 400
DEFAULT_CHILD_OVERLAP = 60


def parent_child_enabled() -> bool:
    from hashmm.feature_flags import flag_enabled
    return flag_enabled("HASHMM_PARENT_CHILD")


@dataclass
class ChildChunk:
    child_id: str
    text: str
    parent_id: str
    doc_id: str
    order: int = 0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"chunk_id": self.child_id, "text": self.text, "doc_id": self.doc_id,
                "parent_id": self.parent_id, "order": self.order, **self.meta}


@dataclass
class ParentChunk:
    parent_id: str
    text: str
    doc_id: str
    order: int = 0


def _chunker(size: int, overlap: int):
    """拿一个和项目一致的切块器；拿不到就用内建的确定性兜底切分（保证可单测/永不失败）。"""
    try:
        from hashmm.pipeline.chunker import TextChunker
        return TextChunker(chunk_size=size, chunk_overlap=overlap)
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
        return None


def _fallback_split(text: str, size: int, overlap: int) -> list[str]:
    """无 TextChunker 时的确定性字符窗切分（按句界尽量不切碎，永不抛错）。"""
    s = str(text or "")
    if len(s) <= size:
        return [s] if s.strip() else []
    out, i, n = [], 0, len(s)
    step = max(1, size - overlap)
    while i < n:
        seg = s[i:i + size]
        # 尽量切在句界
        if i + size < n:
            for sep in ("。", "\n", "！", "？", ".", " "):
                p = seg.rfind(sep)
                if p >= size // 2:
                    seg = seg[:p + 1]
                    break
        seg = seg.strip()
        if seg:
            out.append(seg)
        i += max(step, len(seg)) if seg else step
    return out


def _split_text(text: str, size: int, overlap: int, doc_id: str) -> list[str]:
    ck = _chunker(size, overlap)
    if ck is not None:
        try:
            chunks = ck.chunk(text, doc_id=doc_id) if hasattr(ck, "chunk") else None
            if chunks:
                return [getattr(c, "text", str(c)) for c in chunks if str(getattr(c, "text", c)).strip()]
        except Exception as e:  # noqa: BLE001
            log_suppressed(logger, e)
    return _fallback_split(text, size, overlap)


def _mkid(prefix: str, doc_id: str, order: int, text: str) -> str:
    import hashlib
    h = hashlib.md5(f"{doc_id}:{order}:{text[:64]}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{h}"


def split_parent_child(text: str, doc_id: str, *,
                       parent_size: int = DEFAULT_PARENT_SIZE,
                       child_size: int = DEFAULT_CHILD_SIZE,
                       child_overlap: int = DEFAULT_CHILD_OVERLAP) -> tuple[list[ParentChunk], list[ChildChunk]]:
    """入库侧：文档 → (父块列表, 子块列表)。子块携带 parent_id。**永不抛错**。

    索引时用**子块**（更精准的匹配）；父块存起来，召回命中子块后据 parent_id 取回。
    """
    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []
    try:
        p_texts = _split_text(text, parent_size, 0, doc_id)
        for pi, pt in enumerate(p_texts):
            pid = _mkid("parent", doc_id, pi, pt)
            parents.append(ParentChunk(parent_id=pid, text=pt, doc_id=doc_id, order=pi))
            c_texts = _split_text(pt, child_size, child_overlap, doc_id)
            # 父块本身就很短（≤child_size）时，至少产出一个子块=父块本身
            if not c_texts:
                c_texts = [pt] if pt.strip() else []
            for ci, ct in enumerate(c_texts):
                cid = _mkid("child", doc_id, pi * 1000 + ci, ct)
                children.append(ChildChunk(child_id=cid, text=ct, parent_id=pid,
                                           doc_id=doc_id, order=pi * 1000 + ci))
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
    return parents, children


def build_parent_map(parents: list[ParentChunk]) -> dict[str, str]:
    """{parent_id: parent_text}。"""
    out: dict[str, str] = {}
    for p in parents or []:
        try:
            out[p.parent_id] = p.text
        except Exception:  # noqa: BLE001
            continue
    return out


def build_child_to_parent(children: list[ChildChunk]) -> dict[str, str]:
    """{child_id: parent_id}。"""
    out: dict[str, str] = {}
    for c in children or []:
        try:
            out[c.child_id] = c.parent_id
        except Exception:  # noqa: BLE001
            continue
    return out


def _id_of(item) -> str:
    if isinstance(item, dict):
        return str(item.get("chunk_id") or item.get("id") or "")
    return str(getattr(item, "chunk_id", getattr(item, "id", item)) or "")


def _text_of(item) -> str:
    if isinstance(item, dict):
        return str(item.get("text") or "")
    return str(getattr(item, "text", "") or "")


def _parent_of(item, child_to_parent: dict[str, str]) -> str:
    """优先从记录自带的 parent_id/meta 取；否则查 child_to_parent 映射。"""
    if isinstance(item, dict):
        pid = item.get("parent_id") or (item.get("meta") or {}).get("parent_id")
        if pid:
            return str(pid)
    pid = getattr(item, "parent_id", "")
    if pid:
        return str(pid)
    return child_to_parent.get(_id_of(item), "")


def expand_to_parents(retrieved: list, parent_map: dict[str, str],
                      child_to_parent: dict[str, str] | None = None, *,
                      max_parents: int | None = None) -> list[str]:
    """召回侧：命中的子块 → 去重后的父块正文列表（保持命中顺序）。**永不抛错**。

    退化安全：任一命中找不到父块 → 回退用该子块自身正文（绝不丢内容）；
    整个映射为空（未启用父子块入库）→ 相当于原样返回各命中正文。
    """
    child_to_parent = child_to_parent or {}
    out: list[str] = []
    seen: set[str] = set()
    try:
        for item in retrieved or []:
            pid = _parent_of(item, child_to_parent)
            if pid and pid in parent_map:
                key = f"P:{pid}"
                txt = parent_map[pid]
            else:
                # 没有父块信息 → 用子块自身（安全降级）
                cid = _id_of(item)
                key = f"C:{cid or _text_of(item)[:32]}"
                txt = _text_of(item)
            if not str(txt).strip() or key in seen:
                continue
            seen.add(key)
            out.append(txt)
            if max_parents and len(out) >= max_parents:
                break
    except Exception as e:  # noqa: BLE001
        log_suppressed(logger, e)
    return out
