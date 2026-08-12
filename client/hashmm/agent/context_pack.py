"""hashmm/agent/context_pack.py — 上下文工程（V79，方案 C 阶段）。

把检索结果按预算**装箱**进上下文，替代两种粗暴截断：
- `[:8000]` 拦腰斩（一条 source 被切半，模型读到残句）；
- 每条 `[:400]` 等额硬砍（最相关与最不相关待遇相同，预算浪费在尾部低相关项）。

装箱原则（Anthropic context engineering 实践）：
1. **相关性优先**：检索结果本身已按相关性排序，靠前的多给预算；
2. **结构完整**：预算不够装一整条 → 整条丢弃（不留残句），并明确报告丢了几条；
3. **边界感知**：单条内截断也在 段落 > 句号 > 换行 边界收口。

纯函数零依赖，全部可单测。
"""
from __future__ import annotations


def clip_at_boundary(text: str, budget: int, marker: str = "") -> str:
    """在预算附近的自然边界（\\n\\n > 。/！/？ > \\n > 硬切）截断文本。

    超出部分用 marker（默认自动生成"…[后续 N 字省略]"）标注，让模型知道有省略。
    """
    s = text or ""
    if len(s) <= budget:
        return s
    window = s[:budget]
    cut = -1
    for sep in ("\n\n", "。", "！", "？", "\n"):
        pos = window.rfind(sep)
        if pos > budget * 0.6:                      # 边界太靠前则不用（损失过多）
            cut = pos + len(sep)
            break
    if cut < 0:
        cut = budget
    omitted = len(s) - cut
    tail = marker if marker else f"\n…[后续 {omitted} 字因预算省略]"
    return s[:cut].rstrip() + tail


def pack_sources(sources: list, budget: int = 6000,
                 full_head: int = 2, per_item_cap: int = 1600,
                 tail_len: int = 380) -> tuple[str, dict]:
    """把检索结果装箱成带 [N] 编号的上下文块。

    - 前 full_head 条（最相关）给到 per_item_cap 的完整额度；
    - 其余条目给 tail_len 额度；
    - 剩余预算装不下一条的"头部+来源行"时整条丢弃（不留残句）；
    - 返回 (装箱文本, 报告 {"packed", "dropped", "used", "budget"}）。

    source dict 字段：filename / page / text / score（可缺省）。
    """
    parts: list[str] = []
    used = 0
    packed = 0
    dropped = 0
    # 预留装尾提示语（"另有 N 条未展示"）的位置，保证最终文本（含提示语）**绝不超预算**。
    # 之前提示语在预算判定后才追加、未计入 used，会让返回文本轻微越预算（撑上下文窗的隐患）。
    _NOTE_RESERVE = 80
    _pack_budget = max(200, budget - _NOTE_RESERVE) if len(sources or []) > full_head else budget
    for i, s in enumerate(sources or []):
        fn = str(s.get("filename", "未知"))
        page = s.get("page", "?")
        score = s.get("score", s.get("_rrf", 0)) or 0
        try:
            head = f"[{packed + 1}] [{fn} p.{page}] (相关度:{float(score):.2f})\n"
        except Exception:
            head = f"[{packed + 1}] [{fn} p.{page}]\n"
        cap = per_item_cap if i < full_head else tail_len
        body = clip_at_boundary(str(s.get("text", "")), cap, marker="…")
        block = head + body
        cost = len(block) + 2                        # 块间空行
        if used + cost > _pack_budget:
            # 装不下：若连最小条目（头+120字）都装不下 → 本条及之后全部丢弃
            min_block = head + clip_at_boundary(str(s.get("text", "")), 120, marker="…")
            if used + len(min_block) + 2 > _pack_budget:
                dropped = len(sources) - packed
                break
            block = min_block
            cost = len(block) + 2
        parts.append(block)
        used += cost
        packed += 1
    text = "\n\n".join(parts)
    if dropped > 0:
        note = f"\n\n（预算限制：另有 {dropped} 条较低相关度的结果未展示）"
        text += note
        used += len(note)
    return text, {"packed": packed, "dropped": dropped, "used": used, "budget": budget}
