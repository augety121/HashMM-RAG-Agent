"""方案4 不确定性闸（终答阶段）单测——纯函数，沙箱直跑。

区别于已有的 tests/test_uncertainty_gate.py（那个测 retrieval/agentic.py 的检索阶段多跳闸）；
本测覆盖 agent/uncertainty.py：在【终答阶段】综合置信度+检索离散度+自一致性，决定是否标存疑。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from hashmm.agent import uncertainty as U

P = F = 0
def ck(name, cond):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else: F += 1; print(f"  ✗ {name}  <<< FAIL")

print("=== 1. retrieval_solidity（检索分离散度→扎实度）===")
ck("无分→None", U.retrieval_solidity([]) is None)
ck("None 列表→None", U.retrieval_solidity(None) is None)
strong = U.retrieval_solidity([5.0, 1.0, 0.5])
weak = U.retrieval_solidity([0.3, 0.25, 0.2])
ck("强匹配(高分+明显胜出)扎实度高", strong > 0.7)
ck("一堆弱分扎实度低", weak < 0.4)
ck("强 > 弱", strong > weak)
ck("结果裁剪在[0,1]", 0.0 <= U.retrieval_solidity([99.0]) <= 1.0)
tie = U.retrieval_solidity([5.0, 5.0, 5.0])
ck("并列高分扎实度合理(0.5~1)", 0.5 <= tie <= 1.0)

print("\n=== 2. self_consistency（答案自一致性）===")
ck("<2份→None", U.self_consistency(["只有一份"]) is None)
ck("空→None", U.self_consistency([]) is None)
same = U.self_consistency(["比亚迪营收2700亿", "比亚迪营收约2700亿元"])
diff = U.self_consistency(["答案是A公司的事", "完全不同讲的是B集团的另一回事"])
ck("一致答案分高", same > 0.5)
ck("不一致答案分低", diff < 0.3)
ck("完全相同→1.0", abs(U.self_consistency(["一样的话", "一样的话"]) - 1.0) < 1e-9)

print("\n=== 3. assess_uncertainty 合成判定 ===")
r_strong = U.assess_uncertainty("比亚迪2024营收", [{"score": 4.5}, {"score": 1.0}],
                                "营收为2700亿[1]", grounding_ratio=0.95)
ck("强证据→low", r_strong.level == "low")
ck("强证据→answer", r_strong.decision == "answer")
ck("强证据不确定度低", r_strong.uncertainty < 0.4)

r_none = U.assess_uncertainty("冷门问题", [], "可能是这样吧", grounding_ratio=None)
ck("无来源→high", r_none.level == "high")
ck("无来源→insufficient", r_none.decision == "insufficient")
ck("无来源原因含'没有检索到'", any("没有检索到" in x for x in r_none.reasons))

r_med = U.assess_uncertainty("某问题", [{"score": 2.4}, {"score": 2.2}],
                             "大概是这样", grounding_ratio=0.5)
ck("中等证据→medium 或 high", r_med.level in ("medium", "high"))
ck("中等证据→需标注", U.should_mark(r_med))

r_inc = U.assess_uncertainty("歧义问题", [{"score": 4.0}], "讲的是X的事情",
                             grounding_ratio=0.8,
                             samples=["讲的是X的事情", "其实完全是Y另一码事的描述"])
ck("含不一致采样→记入原因", any("不一致" in x for x in r_inc.reasons))

print("\n=== 4. 永不抛异常（脏输入）===")
ck("脏分数不抛", isinstance(U.retrieval_solidity(["x", None, 3.0]), float))
ck("脏来源不抛", isinstance(U.assess_uncertainty("q", [{"no_score": 1}], "a"), U.UncertaintyReport))
ck("空输入安全", U.assess_uncertainty("", [], "").decision in ("answer", "hedge", "insufficient"))

print("\n=== 5. 标注与 UI 载荷（低误报）===")
ck("low 不标注", not U.should_mark(r_strong))
ck("low 标注文案为空", U.build_uncertainty_note(r_strong) == "")
ck("insufficient 标注含'资料不足'", "资料不足" in U.build_uncertainty_note(r_none))
ck("hedge/insufficient 标注含原因", "原因" in U.build_uncertainty_note(r_none) or not r_none.reasons)
ui = U.summarize_for_ui(r_none)
ck("UI 载荷含 flagged/level/note", ui["flagged"] is True and ui["level"] == "high" and "note" in ui)
ck("to_dict 可序列化", isinstance(r_none.to_dict(), dict) and "uncertainty" in r_none.to_dict())

print("\n=== 6. 与忠实度互补：接地率作为强信号纳入 ===")
hi_g = U.assess_uncertainty("q", [{"score": 3.0}], "a[1]", grounding_ratio=0.95).uncertainty
lo_g = U.assess_uncertainty("q", [{"score": 3.0}], "a", grounding_ratio=0.1).uncertainty
ck("低接地率不确定度更高", lo_g > hi_g)

print(f"\n{'='*46}\n结果：PASS={P}  FAIL={F}")

# ── V308 修 P0-6（测试假绿）──────────────────────────────────────────
# 原先此处是【顶层裸 sys.exit()】。测试文件被 import 时（pytest 收集阶段、
# 自制 runner 的 exec_module）会立即抛 SystemExit 杀死宿主进程：
#   · 真 pytest → INTERNALERROR: mainloop: caught unexpected SystemExit
#   · _mini_runner → SystemExit 不是 Exception 子类，except Exception 抓不到，
#     整个套件以退出码 0 提前终止 → 后续测试文件从未运行却报“全绿”。
# 且本文件的 ck()/ok() 只累加计数、【不抛异常】，故即使不崩，pytest 也只会
# 报 "no tests ran"——检查结果永远变不成测试结论。
# 修法：补一个真正的 pytest 入口断言（读取 import 期已算好的失败计数），
# 并把 sys.exit 收进 __main__ 保护，保留 `python tests/test_uncertainty_answer_gate.py` 直跑的能力。
def test_all():
    """pytest 入口：任一检查失败即断言失败（不再依赖 sys.exit 传递结果）。"""
    assert F == 0, f"{F} 项检查未通过（详见上方 ✗ 行）"


if __name__ == "__main__":
    sys.exit(1 if F else 0)
