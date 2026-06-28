"""方案5 单测：检索漂移控制 + 有界循环进度判定（纯方法，沙箱直跑）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from types import SimpleNamespace
from hashmm.agent.loop import AgentLoop

P = F = 0
def ck(name, cond):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else: F += 1; print(f"  ✗ {name}  <<< FAIL")


class _DummyLLM:
    def call_with_tools(self, messages, tools=None):
        class _M:
            content = "ok"; tool_calls = None; reasoning_content = ""
        class _R:
            message = _M()
        return _R()


loop = AgentLoop(llm_fn=_DummyLLM(), system_prompt="", user_id="t", conv_id="t")

print("=== 1. _evidence_novelty（去重增量条数）===")
ck("空池 + 新证据 → 全是新的", AgentLoop._evidence_novelty([], ["A 片段", "B 片段"]) == 2)
ck("全是池里已有 → 0 新增", AgentLoop._evidence_novelty(["A 片段", "B 片段"], ["A 片段", "B 片段"]) == 0)
ck("部分新 → 只数新的", AgentLoop._evidence_novelty(["A 片段"], ["A 片段", "C 全新片段"]) == 1)
ck("新证据内部重复只算一次", AgentLoop._evidence_novelty([], ["X 片段", "X 片段"]) == 1)
ck("空新证据 → 0", AgentLoop._evidence_novelty(["A"], []) == 0)
ck("None 安全", AgentLoop._evidence_novelty(None, None) == 0)
# 同一 chunk 前160字相同视为重复
long_a = "比亚迪2024年营收" + "细" * 200
long_b = "比亚迪2024年营收" + "细" * 200 + "尾部不同"
ck("前160字相同→视为重复", AgentLoop._evidence_novelty([long_a], [long_b]) == 0)

print("\n=== 2. _intent_drift（检索词是否偏离原意）===")
ck("原问题与改写高度相关→不漂", loop._intent_drift("比亚迪2024年营收多少", "比亚迪 2024 营业收入") is False)
ck("改写完全换了主体→漂移", loop._intent_drift("比亚迪2024年营收多少", "特斯拉自动驾驶技术原理") is True)
ck("空 query 不漂", loop._intent_drift("", "随便什么") is False)
ck("完全相同不漂", loop._intent_drift("营收查询", "营收查询") is False)
ck("过短当前词不漂(避免误报)", loop._intent_drift("比亚迪2024年营收多少", "营") is False)
ck("同义改写不漂", loop._intent_drift("公司利润率", "企业 利润 率") is False)

print("\n=== 3. _drift_guidance（一次性拉回提示）===")
turn = SimpleNamespace()
g1 = loop._drift_guidance("比亚迪2024营收", "特斯拉电池技术", turn)
ck("漂移→产出拉回提示", g1 is not None and "意图漂移" in g1)
ck("提示含原始问题", "比亚迪2024营收" in g1)
ck("已标记 drift_guided", getattr(turn, "drift_guided", False) is True)
g2 = loop._drift_guidance("比亚迪2024营收", "又一次乱改", turn)
ck("每 run 只提示一次（第二次 None）", g2 is None)
turn2 = SimpleNamespace()
ck("不漂移→不提示", loop._drift_guidance("营收查询", "营收 数据 查询", turn2) is None)

print("\n=== 4. 进度计数模拟（dispatch 逻辑等价）===")
# 模拟连续无新增证据的累加与重置
turn3 = SimpleNamespace(kb_evidence=[], no_progress_count=0)
def feed(blocks):
    pool = getattr(turn3, "kb_evidence", None) or []
    novel = AgentLoop._evidence_novelty(pool, blocks)
    if novel == 0:
        turn3.no_progress_count = getattr(turn3, "no_progress_count", 0) + 1
    else:
        turn3.no_progress_count = 0
    pool.extend(blocks); turn3.kb_evidence = pool[-24:]
feed(["证据1", "证据2"])                 # 新增 → 重置
ck("首轮有新证据 no_progress=0", turn3.no_progress_count == 0)
feed(["证据1", "证据2"])                 # 重复 → +1
ck("重复一轮 no_progress=1", turn3.no_progress_count == 1)
feed(["证据1"])                          # 仍重复 → +1
ck("再重复 no_progress=2 达停止线", turn3.no_progress_count == 2)
ck("达到 NO_PROGRESS_LIMIT", turn3.no_progress_count >= AgentLoop.NO_PROGRESS_LIMIT)
feed(["全新证据X"])                       # 又有新 → 重置
ck("出现新证据后 no_progress 归零", turn3.no_progress_count == 0)

print(f"\n{'='*46}\n结果：PASS={P}  FAIL={F}")
sys.exit(1 if F else 0)
