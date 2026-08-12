"""方案6 步级评测单测——mock 事件流，纯函数沙箱直跑。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from hashmm.evaluation import step_eval as S

P = F = 0
def ck(name, cond):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else: F += 1; print(f"  ✗ {name}  <<< FAIL")

def td(name, status="ok", result="x"):
    return ("tool_done", {"name": name, "status": status, "result": result})
def tr(node, detail=""):
    return ("trace", {"node": node, "detail": detail})
def tok(t):
    return ("token", t)

print("=== 1. score_retrieval ===")
ck("无检索→不适用", S.score_retrieval([tok("a")]).applicable is False)
ck("检索全成功→1.0", S.score_retrieval([td("kb_search", result="[1] 命中内容")]).score == 1.0)
r = S.score_retrieval([td("kb_search", result="未找到相关内容"), td("kb_search", result="[1] 命中")])
ck("一半未命中→0.5", abs(r.score - 0.5) < 1e-9)
ck("检索 error→算失败", S.score_retrieval([td("kb_search", status="error", result="")]).score == 0.0)
ck("空结果→算失败", S.score_retrieval([td("deep_search", result="   ")]).score == 0.0)

print("\n=== 2. score_rerank（改写/无进展越多越低）===")
ck("无检索→不适用", S.score_rerank([tok("a")]).applicable is False)
ck("检索无改写→1.0", S.score_rerank([td("kb_search", result="[1] x")]).score == 1.0)
r2 = S.score_rerank([td("kb_search", result="[1]"), tr("retrieval_adapt", "改写"), tr("retrieval_adapt", "再改写")])
ck("两次改写→扣分", r2.score < 1.0 and r2.score > 0)
r3 = S.score_rerank([td("kb_search", result="[1]"), tr("loop", "连续 2 轮检索无新增证据，停止打转")])
ck("无进展打转→扣分", r3.score < 1.0)

print("\n=== 3. score_synthesis ===")
ck("空答案→0", S.score_synthesis([], "").score == 0.0)
ck("兜底道歉→0.3", S.score_synthesis([], "抱歉，我在处理这个任务时没能获取到足够的信息。").score == 0.3)
ck("正常答案→1.0", S.score_synthesis([], "根据资料，2024年营收为2700亿元，同比增长20%。").score == 1.0)
ck("简洁有效答案不误伤", S.score_synthesis([], "营收为2700亿[1]。").score == 1.0)
ck("从 token 事件取答案", S.score_synthesis([tok("一段足够长的"), tok("有效回答内容在这里")]).score == 1.0)

print("\n=== 4. score_verification ===")
ck("无质量门→不适用", S.score_verification([tok("a")]).applicable is False)
ck("门全通过→1.0", S.score_verification([tr("citation", "引用校验：全部引用编号有效 ✓"),
                                         tr("faithfulness", "忠实度校验：3/3 条事实可溯源 ✓")]).score == 1.0)
r4 = S.score_verification([tr("faithfulness", "忠实度校验：2/3 条事实可溯源，1 条待核"),
                           tr("citation", "全部有效 ✓")])
ck("一个门发现问题→扣分", 0 < r4.score < 1.0)
ck("DoD 未完成→扣分", S.score_verification([tr("dod", "任务清单 2 项未完成")]).score < 1.0)

print("\n=== 5. score_routing ===")
ck("无错误无重复→1.0", S.score_routing([td("kb_search"), td("write_file")]).score == 1.0)
ck("工具失败→扣分", S.score_routing([td("kb_search", status="error")]).score < 1.0)
many = [td("kb_search") for _ in range(6)]
ck("同工具刷屏→扣分(空转)", S.score_routing(many).score < 1.0)

print("\n=== 6. score_steps 合成 + 瓶颈归因 ===")
good = [td("kb_search", result="[1] 命中"), tok("根据资料，营收2700亿元，同比增长20%[1]。"),
        tr("citation", "全部有效 ✓"), tr("faithfulness", "3/3 可溯源 ✓")]
rg = S.score_steps(good, answer="根据资料，营收2700亿元，同比增长20%[1]。")
ck("健康回合 overall 高", rg.overall >= 0.95)
ck("健康回合无瓶颈", rg.bottleneck is None)

bad = [td("kb_search", status="ok", result="未找到"), td("kb_search", result="无相关"),
       tr("retrieval_adapt", "改写"), tok("抱歉，我在处理这个任务时没能获取到足够的信息。")]
rb = S.score_steps(bad, answer="抱歉，我在处理这个任务时没能获取到足够的信息。")
ck("坏回合 overall 低", rb.overall < 0.6)
ck("坏回合瓶颈=检索", rb.bottleneck == "retrieval")
ck("脏事件不抛", isinstance(S.score_steps([("x",), None, 42], answer="a"), S.StepReport))
ck("to_dict 可序列化", "stages" in rg.to_dict() and "bottleneck" in rg.to_dict())

print("\n=== 7. judge_fn 可选挂载（RAGAS 式）===")
def fake_judge(q, a): return {"score": 0.8, "dimensions": {"faithfulness": 0.9}}
rj = S.score_steps(good, query="营收", answer="营收2700亿元，增长20%[1]。", judge_fn=fake_judge)
ck("judge 维度被附上", rj.judge is not None and rj.judge.get("score") == 0.8)
def boom_judge(q, a): raise RuntimeError("judge 挂了")
ck("judge 抛错不影响结构打分", S.score_steps(good, answer="x"*40, judge_fn=boom_judge).judge is None)

print("\n=== 8. aggregate_step_reports（系统级瓶颈定位）===")
reports = [
    S.score_steps([td("kb_search", result="未找到"), tok("抱歉，我在处理这个任务时没能获取到足够的信息。")]),
    S.score_steps([td("kb_search", result="无相关"), tok("抱歉，我在处理这个任务时没能获取到足够的信息。")]),
    S.score_steps([td("kb_search", result="[1] 命中"), tok("正常长度的有效回答内容在这里足够长")]),
]
agg = S.aggregate_step_reports(reports)
ck("聚合统计 n=3", agg["n"] == 3)
ck("给出 stage_avg", "retrieval" in agg["stage_avg"])
ck("top_bottleneck=检索（多数坏在检索）", agg["top_bottleneck"] == "retrieval")
ck("空列表安全", S.aggregate_step_reports([])["n"] == 0)

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
# 并把 sys.exit 收进 __main__ 保护，保留 `python tests/test_step_eval.py` 直跑的能力。
def test_all():
    """pytest 入口：任一检查失败即断言失败（不再依赖 sys.exit 传递结果）。"""
    assert F == 0, f"{F} 项检查未通过（详见上方 ✗ 行）"


if __name__ == "__main__":
    sys.exit(1 if F else 0)
