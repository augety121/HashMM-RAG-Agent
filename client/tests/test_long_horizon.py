"""tests/test_long_horizon.py — 长任务 / 长代码「长程行为」硬合约（V306）。

回答一个具体质疑：现有测试只测短对话、纯内存小算法，太浅。真实使用是**连着问上百次、
边写长代码边改**——这条链路上最容易坏的四件事，本套件逐一变成可复现、带数字的红线：

  1) 上下文压缩正常吗   → compact_history / compact_history_llm 在 100 轮触发压缩后，
                          预算受控、最近轮逐字保留、结构完整、确定性可复现、防套娃幂等。
  2) 中间会不会幻觉      → audit_faithfulness(strict_numbers=True) 抓「引用了来源却断言
                          证据里没有的硬事实」（数字对不上 / 造年份 / Big-O 写反）。
  3) 有没有漂移          → 100 轮里「最初的目标」必须每一轮都还在压缩结果里（目标保持率=1.0）；
                          同输入多次运行输出必须逐字节一致（确定性 = 无运行间漂移）。
  4) 准确性 / 混入无关    → 压缩摘要里出现的内容必须都能溯源到真实历史（无凭空 token）；
                          中途插入一条完全无关消息，最初目标与最近轮不被其污染。

全部基于仓库既有纯函数（零外部依赖、沙箱可跑），不 mock 出不存在的能力。

运行：
    python tests/test_long_horizon.py          # 独立运行，打印带数字的报告
    python -m pytest tests/test_long_horizon.py # 或走 pytest
"""
from __future__ import annotations

import copy
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))

from hashmm.agent import conv_compact as CC
from hashmm.agent import uncertainty as U
from hashmm.evaluation import faithfulness as F

# 目标关键词：整段 100 轮里「最初要什么」的锚点。漂移检测就盯它在不在。
GOAL_KEYWORDS = ["红黑树", "rbtree.h"]
LONG_BUDGET = 16000        # 与 compact_history 默认一致
KEEP_RECENT = 6


# ─────────────────────────── 造数据：一段真实感的 100 轮长代码会话 ───────────────────────────
def build_long_coding_conversation(turns: int = 100, big: bool = True) -> list[dict]:
    """开场是一个明确的长代码任务，随后 turns 轮迭代（改需求 / 交付代码 / 产出文件）。
    big=True 时每条足够大以越过压缩预算（模拟真实长回答）。"""
    pad_req = "需求描述细节 " * (40 if big else 1)
    pad_impl = "实现说明与代码片段 " * (60 if big else 1)
    hist: list[dict] = [
        {"role": "user",
         "content": "帮我写一个 C++ 的红黑树实现，文件名 rbtree.h，支持插入、删除、查找和迭代器。"},
        {"role": "assistant",
         "content": "好的，已创建 rbtree.h，实现 RBTree 类（insert/erase/find）。" + pad_impl},
    ]
    for i in range(2, turns):
        if i % 2 == 0:
            hist.append({"role": "user",
                         "content": f"第{i}轮：再加功能点 feature_{i}，并更新 main.cpp。" + pad_req})
        else:
            hist.append({"role": "assistant",
                         "content": f"第{i}轮：已更新 rbtree.h 与 helper_{i}.cpp，加入 feature_{i - 1}。" + pad_impl})
    return hist


def goal_retention(summary_text: str) -> float:
    """目标保持率 = 目标关键词在压缩结果里出现的比例（1.0 = 完全没丢）。"""
    if not GOAL_KEYWORDS:
        return 1.0
    hit = sum(1 for k in GOAL_KEYWORDS if k in summary_text)
    return hit / len(GOAL_KEYWORDS)


def _tokens_of(text: str) -> set[str]:
    """与忠实度模块同口径分词，避免两套实现漂移。"""
    return set(F.tokenize(text))


# ══════════════════════════════ 1. 上下文压缩正常吗 ══════════════════════════════
def test_compaction_triggers_and_bounds_budget():
    """越过预算 → 触发压缩：条数骤降、token 估算被压回预算量级、摘要标记就位。"""
    hist = build_long_coding_conversation(100, big=True)
    raw_tokens = CC.estimate_tokens(hist)
    out = CC.compact_history(hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    assert len(out) == KEEP_RECENT + 1, f"应压成 1 摘要+{KEEP_RECENT} 最近，实际 {len(out)}"
    assert CC.SUMMARY_MARK in out[0]["content"], "缺结构化摘要标记"
    compacted_tokens = CC.estimate_tokens(out)
    # 压缩后 token 必须显著下降（这里 100 轮长会话至少降到原来的一半以下）
    assert compacted_tokens < raw_tokens * 0.6, \
        f"压缩未有效降本：{raw_tokens} → {compacted_tokens}"
    # 摘要正文长度受 _SUMMARY_CAP 约束（不会因会话变长而无限膨胀）
    assert len(out[0]["content"]) <= CC._SUMMARY_CAP + 600, "摘要正文超出预算上限"


def test_compaction_zero_change_under_budget():
    """预算内 → 逐字节零变化（不该为短对话平白引入摘要，破坏保真）。"""
    short = build_long_coding_conversation(6, big=False)
    out = CC.compact_history(short, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    assert out == [h for h in short if isinstance(h, dict)], "预算内不应改写历史"
    assert CC.SUMMARY_MARK not in "".join(m["content"] for m in out)


def test_recent_turns_preserved_verbatim():
    """最近 K 轮必须逐字保留（正在进行的工作不能被摘要吃掉）。"""
    hist = build_long_coding_conversation(100, big=True)
    out = CC.compact_history(hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    tail_original = hist[-KEEP_RECENT:]
    tail_compacted = out[-KEEP_RECENT:]
    for a, b in zip(tail_original, tail_compacted):
        # 允许对"单条巨型消息"截断，但短消息必须逐字一致
        if len(str(a["content"])) <= CC._RECENT_MSG_CAP:
            assert a["content"] == b["content"], "最近轮被非预期改写"
            assert a["role"] == b["role"]


def test_artifact_files_tracked_across_100_turns():
    """跨 100 轮，早期产出的文件名仍被显式记住（长代码续作不失忆）。"""
    hist = build_long_coding_conversation(100, big=True)
    out = CC.compact_history(hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    summ = out[0]["content"]
    assert "本会话已生成/提到的文件" in summ, "缺文件清单"
    assert "rbtree.h" in summ, "最初的核心文件 rbtree.h 在 100 轮后丢失"


def test_llm_compaction_falls_back_and_preserves_recent():
    """LLM 交接单压缩：给定可用 llm_fn 时保留最近原文 + 一条交接单；llm_fn 缺失/太短则
    安全回落确定性压缩，且绝不把 tool 结果当切点（不切断工具调用对）。"""
    hist = build_long_coding_conversation(60, big=True)

    # 无 llm_fn → 回落确定性压缩（与 compact_history 等价，不抛错）
    fb = CC.compact_history_llm(hist, llm_fn=None)
    assert isinstance(fb, list) and fb, "回落结果异常"

    # 有 llm_fn → 交接单必须是"摘要"而不是"续写答案"（Do NOT continue 契约的行为面）
    calls = {"n": 0}

    def fake_llm(prompt: str) -> str:
        calls["n"] += 1
        # 交接单助手只应产出结构化摘要，这里回一个合法摘要
        return "【目标】实现 rbtree.h 红黑树\n【进度】已产出 rbtree.h, main.cpp\n【未决】迭代器删除语义"

    out = CC.compact_history_llm(hist, llm_fn=fake_llm, keep_recent_tokens=4000)
    assert calls["n"] == 1, "交接单压缩应只调用一次 LLM"
    assert CC.SUMMARY_MARK in out[0]["content"], "交接单缺摘要标记"
    # 最近轮仍在（交接单只替换早段）
    assert any("第59轮" in m["content"] or "第58轮" in m["content"] for m in out[1:]), \
        "交接单压缩后最近轮丢失"


# ══════════════════════════════ 2. 确定性 / 幂等（无运行间漂移、防套娃） ══════════════════════════════
def test_compaction_is_deterministic():
    """同输入多次压缩 → 逐字节一致。确定性=消除"同一段历史两次跑出不同摘要"的漂移源。"""
    hist = build_long_coding_conversation(100, big=True)
    outs = [CC.compact_history(copy.deepcopy(hist), keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
            for _ in range(5)]
    for o in outs[1:]:
        assert o == outs[0], "压缩非确定性：同输入产生了不同输出（运行间漂移）"


def test_recompaction_is_idempotent_no_nesting():
    """把已压缩的历史再压一次：摘要不套娃、目标锚点不丢。模拟"越聊越长、反复压缩"。"""
    hist = build_long_coding_conversation(100, big=True)
    once = CC.compact_history(hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    # 人为把它接着变长再压（模拟又聊了几十轮）
    grown = once + build_long_coding_conversation(40, big=True)[2:]
    twice = CC.compact_history(grown, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    assert twice[0]["content"].count(CC.SUMMARY_MARK) <= 1, "摘要发生套娃嵌套"
    assert goal_retention(twice[0]["content"]) == 1.0, "二次压缩后最初目标丢失（漂移）"


# ══════════════════════════════ 3. 漂移：100 轮全程目标保持 ══════════════════════════════
def test_goal_never_drifts_over_growing_conversation():
    """从 8 轮一路长到 120 轮，每一步压缩后"最初的目标"都必须 100% 还在。
    这是"聊久了就跑题/失忆"的直接回归红线。"""
    base = build_long_coding_conversation(120, big=True)
    worst = 1.0
    checked = 0
    for n in range(8, 121, 8):
        hist = base[:n]
        out = CC.compact_history(hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
        summ_all = "\n".join(m["content"] for m in out)
        r = goal_retention(summ_all)
        worst = min(worst, r)
        checked += 1
        assert r == 1.0, f"第 {n} 轮目标保持率 {r} < 1.0（发生漂移）"
    assert checked >= 10 and worst == 1.0


# ══════════════════════════════ 4. 中间过程幻觉（strict 硬事实接地） ══════════════════════════════
def test_hallucinated_number_is_caught_strict():
    """引用了来源、却断言证据里没有的硬事实（O(1) / 造年份）→ strict 模式必须判未接地。"""
    sources = [
        "红黑树是一种自平衡二叉搜索树，插入和删除的时间复杂度是 O(log n)。",
        "红黑树每个节点是红色或黑色，根节点是黑色。",
    ]
    hall = "红黑树的插入时间复杂度是 O(1)[1]，并且它由 Guido van Rossum 在 1995 年发明[2]。"
    rep = F.audit_faithfulness(hall, sources, strict_numbers=True)
    assert not rep.ok, "明显幻觉未被 strict 模式判未接地"
    assert len(rep.unsupported) >= 1
    assert F.should_request_revision(rep), "幻觉应触发一次修正"
    # 默认（非 strict）模式：仍记录到 number_unsupported，便于前端标灰而不改判定语义
    rd = F.audit_faithfulness(hall, sources)
    assert len(rd.number_unsupported) >= 1, "默认模式也应记录硬事实缺据（供观测）"


def test_grounded_answer_not_false_flagged_strict():
    """接地良好的答案在 strict 模式下不得误伤（低假阳性是硬要求）。"""
    sources = [
        "红黑树插入删除是 O(log n)，根节点是黑色，从根到叶路径含相同数目黑节点。",
    ]
    good = "红黑树插入是 O(log n)[1]，根节点为黑色[1]。"
    rep = F.audit_faithfulness(good, sources, strict_numbers=True)
    assert rep.ok, f"接地答案被误判：{rep.to_dict()}"
    assert rep.number_unsupported == []


def test_enumeration_and_derived_counts_not_flagged():
    """枚举步骤 / 裸单数字不算硬事实，避免"分3步"被误报为幻觉。"""
    sources = ["按顺序完成配置、构建、测试三个阶段即可上线。"]
    ans = "第一步配置，第二步构建，第三步测试，分3步完成[1]。"
    rep = F.audit_faithfulness(ans, sources, strict_numbers=True)
    assert rep.number_unsupported == [], f"枚举被误报：{rep.number_unsupported}"


def test_faithfulness_ratio_on_fixed_mini_dataset():
    """固定 mini 数据集上的准确性红线：接地样本 ratio=1.0，植入的幻觉样本被 strict 抓到。
    可作为"当前 commit 的忠实度基准"，防回归。"""
    dataset = [
        # (sources, answer, expect_ok_strict)
        (["Python 由 Guido van Rossum 于 1991 年首次发布。"],
         "Python 于 1991 年首次发布[1]。", True),
        (["HTTP 200 表示请求成功，404 表示资源不存在。"],
         "HTTP 200 表示成功[1]，404 表示未找到[1]。", True),
        # 幻觉：把 1991 说成 1989（证据里没有 1989）
        (["Python 由 Guido van Rossum 于 1991 年首次发布。"],
         "Python 于 1989 年首次发布[1]。", False),
        # 幻觉：复杂度写反
        (["快速排序平均时间复杂度是 O(n log n)。"],
         "快速排序平均复杂度是 O(n^2)[1]。", False),
    ]
    correct = 0
    for sources, ans, expect_ok in dataset:
        rep = F.audit_faithfulness(ans, sources, strict_numbers=True)
        if rep.ok == expect_ok:
            correct += 1
    acc = correct / len(dataset)
    assert acc == 1.0, f"固定集忠实度判定准确率 {acc} < 1.0"


# ══════════════════════════════ 5. 混入无关内容 / 摘要污染 ══════════════════════════════
def test_summary_has_no_hallucinated_tokens():
    """压缩摘要里出现的每个 token 都必须能在原始历史里找到——摘要不得"凭空造词"。
    这是"压缩过程本身会不会引入幻觉/无关内容"的直接检验。"""
    hist = build_long_coding_conversation(100, big=True)
    out = CC.compact_history(hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    summ = out[0]["content"]
    # 剥掉压缩器自己的固定脚手架词（这些是模板，不算"造词"）；按同口径分词并入允许集，
    # 因为 tokenize 会把中文切成 2-gram，脚手架整词也会产生 bigram。
    scaffold_phrases = ["早前对话摘要", "中间", "轮略", "本会话已生成", "提到的文件",
                        "用户", "助手", "工具"]
    allowed = set()
    for h in hist:
        allowed |= _tokens_of(str(h["content"]))
    for p in scaffold_phrases:
        allowed |= _tokens_of(p)
    summ_tokens = _tokens_of(summ)
    novel = {t for t in summ_tokens if t not in allowed and len(t) > 1}
    # 允许极少量因截断产生的边界二元组噪声，但不应出现成片新词
    assert len(novel) <= 3, f"摘要疑似引入未溯源内容：{list(novel)[:10]}"


def test_irrelevant_injection_does_not_pollute_goal_or_recent():
    """会话中途插入一条完全无关的消息（跑题/串台），压缩后：最初目标仍在、最近轮仍是本任务，
    无关内容至多被折叠成脉络的一行，不得顶替目标或污染最近工作区。"""
    hist = build_long_coding_conversation(100, big=True)
    # 在第 50 位插入一条与红黑树毫不相干的消息
    noise = {"role": "user", "content": "顺便问一下，今天上海的天气怎么样？适合去外滩散步吗？"}
    hist.insert(50, noise)
    out = CC.compact_history(hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    summ_all = "\n".join(m["content"] for m in out)
    # 目标不被无关内容挤掉
    assert goal_retention(summ_all) == 1.0, "无关消息导致最初目标丢失"
    # 最近工作区仍是本任务（不应是天气话题占据最近轮）
    tail_text = "\n".join(m["content"] for m in out[1:])
    assert ("rbtree" in tail_text) or ("feature_" in tail_text) or ("helper_" in tail_text), \
        "最近轮被无关话题污染"


def test_self_consistency_flags_drifting_samples():
    """答案自一致性：稳定复述同一事实 → 分数明显高于"东拉西扯各说各话"。
    可作为"多次采样是否在漂移/瞎猜"的信号。"""
    stable = ["红黑树插入是 O(log n)", "红黑树的插入复杂度为 O(log n)", "插入复杂度 O(log n) 红黑树"]
    drift = ["红黑树插入是 O(log n)", "香蕉是黄色的水果", "今天天气很好适合散步"]
    s_stable = U.self_consistency(stable)
    s_drift = U.self_consistency(drift)
    assert s_stable is not None and s_drift is not None
    assert s_stable > s_drift, f"自一致性未能区分稳定与漂移：{s_stable} vs {s_drift}"
    assert s_drift <= 0.1, "明显跑题的样本自一致性应接近 0"


def test_retrieval_solidity_ranks_evidence_strength():
    """检索扎实度：有一个明显胜出的强匹配 > 一堆挤在低分的弱匹配。防"证据都不行还硬答"。"""
    strong = U.retrieval_solidity([0.92, 0.30, 0.20])
    weak = U.retrieval_solidity([0.12, 0.10, 0.09])
    assert strong is not None and weak is not None
    assert strong > weak, f"扎实度未区分强弱证据：{strong} vs {weak}"


# ══════════════════════════════ 6. 同一 chat 多支对话（分支隔离 + 共享前缀保持）══════════════════════════════
def _branch(prefix: list[dict], tag: str, turns: int) -> list[dict]:
    """从共享前缀派生一条分支：追加该分支特有的 turns 轮（带唯一 tag 便于查串扰）。"""
    h = [dict(m) for m in prefix]
    for i in range(turns):
        role = "user" if i % 2 == 0 else "assistant"
        h.append({"role": role, "content": f"[{tag}] 第{i}轮：围绕 {tag} 专属议题 topic_{tag}_{i}。" + ("细节 " * 60)})
    return h


def test_multi_branch_same_chat_isolation():
    """同一个 chat 里从同一处分叉出两支对话（如用户在某条上"重新生成/改问"）：
    压缩后两支必须（1）都保住最初共享目标；（2）各自只含本支议题，绝不串入另一支的内容。
    这是"多支对话互不污染"的直接红线。"""
    shared_prefix = [
        {"role": "user", "content": "帮我设计一个分布式限流器，要求支持滑动窗口和令牌桶两种算法。"},
        {"role": "assistant", "content": "好的，我们先确定接口 RateLimiter。" + ("讨论 " * 40)},
    ]
    branchA = _branch(shared_prefix, "A", 60)   # A 支：深入滑动窗口
    branchB = _branch(shared_prefix, "B", 60)   # B 支：深入令牌桶

    outA = CC.compact_history(branchA, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    outB = CC.compact_history(branchB, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    txtA = "\n".join(m["content"] for m in outA)
    txtB = "\n".join(m["content"] for m in outB)

    # (1) 共享目标在两支都保住
    assert "限流器" in txtA and "限流器" in txtB, "分叉后共享目标丢失"
    # (2) 分支隔离：A 支不含 B 专属议题标记，反之亦然
    assert "[B]" not in txtA and "topic_B_" not in txtA, "A 支串入了 B 支内容（多支对话污染）"
    assert "[A]" not in txtB and "topic_A_" not in txtB, "B 支串入了 A 支内容（多支对话污染）"
    # 各支保留自己的议题
    assert "[A]" in txtA and "[B]" in txtB, "分支自身议题在压缩后丢失"


def test_regenerate_branch_keeps_prefix_and_diverges():
    """模拟"在第 N 条重新生成"产生的分叉：两个候选回答共享同一问题前缀，
    但各自延展。压缩后共享问题保留，两候选内容不互相覆盖。"""
    prefix = [{"role": "user", "content": "用一句话解释 Raft 的 leader 选举，然后给伪代码。"}]
    cand1 = prefix + [{"role": "assistant", "content": "候选1：Raft 通过任期与多数票选主。" + ("方案一 " * 80)}]
    cand2 = prefix + [{"role": "assistant", "content": "候选2：Raft 用随机超时触发选举。" + ("方案二 " * 80)}]
    o1 = CC.compact_history(cand1 + [{"role": "user", "content": "继续"}] * 1, keep_recent=KEEP_RECENT, char_budget=2000)
    o2 = CC.compact_history(cand2 + [{"role": "user", "content": "继续"}] * 1, keep_recent=KEEP_RECENT, char_budget=2000)
    t1 = "\n".join(m["content"] for m in o1)
    t2 = "\n".join(m["content"] for m in o2)
    assert "Raft" in t1 and "Raft" in t2, "共享问题前缀丢失"
    assert "候选1" not in t2 and "候选2" not in t1, "两个重新生成候选互相串了"


# ══════════════════════════════ 7. 长任务：跨压缩的幻觉检测 ══════════════════════════════
def test_long_task_hallucination_caught_after_compaction():
    """长任务（多步执行）跑到中途触发上下文压缩，随后模型给出的"进度总结"里若混入
    证据/历史里没有的硬事实（编造完成了没做的步骤、编造数字），strict 接地必须抓到。
    这是"长任务中间过程会不会幻觉"的直接检验。"""
    # 造一个 12 步任务的长历史（每步足够大以触发压缩）
    task_hist = [{"role": "user", "content": "执行数据迁移任务：从 MySQL 迁到 PostgreSQL，共 12 步，逐步汇报。"}]
    done_steps = []
    for i in range(1, 13):
        task_hist.append({"role": "assistant", "content": f"第{i}步完成：迁移表 table_{i}，耗时约 {i*3} 秒。" + ("执行细节 " * 50)})
        done_steps.append(f"table_{i}")
    # 压缩（长历史）
    compacted = CC.compact_history(task_hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    # 用压缩后的"已完成文件/进度"作为证据，检验一份进度总结的忠实度
    evidence = [m["content"] for m in compacted]
    # 真实总结（只讲做过的）→ 应接地
    honest = "已完成 table_1 到 table_12 的迁移[1]。"
    # 幻觉总结：编造一个没迁的表 + 编造总耗时数字（历史里没有 999）
    halluc = "已完成 table_99 的迁移[1]，总耗时 999 秒[1]。"
    rep_ok = F.audit_faithfulness(honest, evidence, strict_numbers=True)
    rep_bad = F.audit_faithfulness(halluc, evidence, strict_numbers=True)
    # honest 里的 table_1..table_12 与 evidence 重叠高；表号是 data-number 但都在证据里 → 接地
    assert rep_bad.ok is False or len(rep_bad.number_unsupported) >= 1, \
        "长任务中编造的表号/耗时未被抓到"
    assert len(rep_bad.number_unsupported) >= 1, "编造的 999/table_99 应记入 number_unsupported"


def test_long_task_goal_and_progress_survive_compaction():
    """长任务压缩后：任务目标（迁移 MySQL→PostgreSQL）与早期完成的步骤仍可追溯，
    不会"跑了一半忘了在干嘛"。"""
    task_hist = [{"role": "user", "content": "执行数据迁移任务：从 MySQL 迁到 PostgreSQL，共 12 步。"}]
    for i in range(1, 13):
        task_hist.append({"role": "assistant", "content": f"第{i}步完成：迁移表 table_{i}。" + ("细节 " * 60)})
    out = CC.compact_history(task_hist, keep_recent=KEEP_RECENT, char_budget=LONG_BUDGET)
    summ = out[0]["content"]
    assert ("迁移" in summ) or ("MySQL" in summ) or ("PostgreSQL" in summ), "长任务目标压缩后丢失"


# ─────────────────────────── 独立运行入口（打印带数字报告）───────────────────────────
def _run_all():
    import sys
    import io as _io
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    log = _io.StringIO()

    def emit(s=""):
        print(s)
        log.write(s + "\n")

    emit("# HashMM 长程行为测试日志（长任务 / 长代码 / 多支对话 / 幻觉 / 上下文压缩）")
    import datetime as _dt
    emit(f"- 时间：{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    emit(f"- 用例数：{len(tests)}")
    emit("=" * 66)
    passed = failed = 0
    results = []
    for name, fn in tests:
        try:
            fn()
            emit(f"  ✓ {name}")
            results.append((name, True, ""))
            passed += 1
        except AssertionError as e:
            emit(f"  ✗ {name}\n      {e}")
            results.append((name, False, str(e)))
            failed += 1
        except Exception as e:  # noqa: BLE001
            emit(f"  ✗ {name}（异常）\n      {type(e).__name__}: {e}")
            results.append((name, False, f"{type(e).__name__}: {e}"))
            failed += 1

    emit("-" * 66)
    # 带数字的长程画像（肉眼可核）
    hist = build_long_coding_conversation(100, big=True)
    out = CC.compact_history(hist)
    emit(f"## 场景数字画像")
    emit(f"- 100 轮会话：{CC.estimate_tokens(hist)} tok → 压缩后 {len(out)} 条 / "
         f"{CC.estimate_tokens(out)} tok（降 {100 - round(CC.estimate_tokens(out) / max(1, CC.estimate_tokens(hist)) * 100)}%）")

    # 多支对话画像
    sp = [{"role": "user", "content": "帮我设计一个分布式限流器。"},
          {"role": "assistant", "content": "好的。" + ("讨论 " * 40)}]
    oA = CC.compact_history(_branch(sp, "A", 60)); oB = CC.compact_history(_branch(sp, "B", 60))
    tA = "\n".join(m["content"] for m in oA); tB = "\n".join(m["content"] for m in oB)
    emit(f"- 多支对话：A 支压缩后含[B]内容={'[B]' in tA}（应 False）；B 支含[A]内容={'[A]' in tB}（应 False）；两支均保留'限流器'目标={'限流器' in tA and '限流器' in tB}")

    # 长任务幻觉画像
    th = [{"role": "user", "content": "数据迁移 12 步。"}]
    for i in range(1, 13):
        th.append({"role": "assistant", "content": f"第{i}步完成：迁移 table_{i}。" + ("细节 " * 50)})
    ev = [m["content"] for m in CC.compact_history(th)]
    bad = F.audit_faithfulness("已完成 table_99[1]，总耗时 999 秒[1]。", ev, strict_numbers=True)
    emit(f"- 长任务幻觉：编造 table_99/999 秒 → strict 判 ok={bad.ok}（应 False），number_unsupported={len(bad.number_unsupported)} 条")
    emit("=" * 66)
    emit(f"结果：PASS={passed}  FAIL={failed}")

    # 落盘日志
    try:
        import os as _os
        out_dir = _os.environ.get("HASHMM_TEST_LOG_DIR", ".")
        path = _os.path.join(out_dir, "long_horizon_test_log.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(log.getvalue())
        print(f"\n[日志已保存] {path}")
    except Exception as _e:  # noqa: BLE001
        print(f"[日志保存失败] {_e}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(_run_all())
