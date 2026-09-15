"""hashmm/channels/replies.py — IM 回复的共享纯逻辑（分块 / 人格 / 引用格式化）。

借鉴 fanbox：① 手机场景人格（简洁、先结论、别刷屏）；② 长回复按"段落→句尾→空格→硬切"
语义边界分块（飞书/微信单条消息都有长度上限，桌面端啰嗦的回复在手机上要拆开发）。
纯函数、无依赖、可单测。
"""
from __future__ import annotations

# 手机场景人格（注入到系统提示前缀）。改编自 fanbox 的微信人格，去掉了专有称呼，通用化。
MOBILE_PERSONA = (
    "你正通过即时通讯（飞书/微信）与用户对话，回复会显示在手机上。请：用简洁直接的中文、"
    "适合手机阅读；先给结论，细节按需再展开；除非用户明确要求，不要贴大段代码或长列表；"
    "回答基于检索到的企业知识库内容，并在末尾标注来源。"
)

# 句尾标点（中英）—— 分块时的二级边界
_SENT_END = "。！？!?…"


def _best_cut(window: str) -> int:
    """在 window 内找最靠后的"好断点"，返回切割位置（含该边界）。
    优先级：段落空行 > 句尾标点 > 换行 > 空格 > 硬切。只在断点过半处才采用，
    避免切出过小的碎块（否则宁可硬切，充分利用长度）。"""
    n = len(window)
    half = n * 0.5
    # 1) 段落空行
    i = window.rfind("\n\n")
    if i >= half:
        return i + 2
    # 2) 句尾标点
    best = -1
    for ch in _SENT_END:
        j = window.rfind(ch)
        if j > best:
            best = j
    if best >= half:
        return best + 1
    # 3) 换行
    i = window.rfind("\n")
    if i >= half:
        return i + 1
    # 4) 空格
    i = window.rfind(" ")
    if i >= half:
        return i + 1
    # 5) 硬切
    return n


def chunk_for_im(text: str, limit: int = 1800) -> list[str]:
    """把长文本切成每段 ≤ limit 的多段，尽量在语义边界断开。永不抛错。
    limit 默认 1800（飞书单条 30KB 但中文按字算保守取值；微信 iLink 实测约 1000-2000 字一条）。"""
    text = (text or "").strip()
    if not text:
        return []
    if limit <= 0 or len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    guard = 0
    while len(rest) > limit and guard < 10000:
        guard += 1
        cut = _best_cut(rest[:limit])
        head = rest[:cut].rstrip()
        if head:
            chunks.append(head)
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return [c for c in chunks if c]


def format_sources(sources: list, *, max_items: int = 3) -> str:
    """把 RAG 来源压成一行紧凑引用，附在回复末尾。空来源返回 ""。永不抛错。
    例：来源：年报2024.pdf p12 ｜ 制度手册.docx §3.2"""
    try:
        if not sources:
            return ""
        parts = []
        seen = set()
        for s in sources[:max_items * 2]:
            if not isinstance(s, dict):
                continue
            name = (s.get("filename") or s.get("source") or s.get("doc_id") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            tag = name
            page = s.get("page")
            sec = (s.get("section") or "").strip()
            if isinstance(page, int) and page > 0:
                tag += f" p{page}"
            elif sec:
                tag += f" §{sec[:20]}"
            parts.append(tag)
            if len(parts) >= max_items:
                break
        return ("来源：" + " ｜ ".join(parts)) if parts else ""
    except Exception:
        return ""
