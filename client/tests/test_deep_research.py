"""方案7 深度研究模式单测——纯核心 + mock 编排，沙箱直跑。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from hashmm.agent import deep_research as DR
from hashmm.agent.deep_research import Section

P = F = 0
def ck(name, cond):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else: F += 1; print(f"  ✗ {name}  <<< FAIL")

print("=== 1. renumber_body（本地[k]→全局[N]）===")
ck("按映射改写", DR.renumber_body("营收[1]增长[2]", {1: 3, 2: 5}) == "营收[3]增长[5]")
ck("无映射原样保留", DR.renumber_body("见[9]", {1: 2}) == "见[9]")
ck("空正文安全", DR.renumber_body("", {1: 1}) == "")
ck("无引用原样", DR.renumber_body("没有引用的文本", {1: 1}) == "没有引用的文本")

print("\n=== 2. merge_sections（全局去重 + 重编号）===")
shared = {"filename": "共享.pdf", "page": 1, "text": "共享证据"}
secs = [
    Section("A", "x[1]y[2]", [shared, {"filename": "a.pdf", "page": 3, "text": "甲"}]),
    Section("B", "p[1]q[2]", [{"filename": "b.pdf", "page": 5, "text": "乙"}, shared]),
]
rw, gsrc = DR.merge_sections(secs)
ck("共享来源去重→全局3条", len(gsrc) == 3)
ck("A段引用保持[1][2]", rw[0]["body"] == "x[1]y[2]")
ck("B段[1](b.pdf)→[3]", "[3]" in rw[1]["body"])
ck("B段[2](共享)→[1]（与A同号）", rw[1]["body"] == "p[3]q[1]")
ck("空 sections 安全", DR.merge_sections([]) == ([], []))
# 同一文件不同页不算重复
secs2 = [Section("C", "m[1]n[2]", [{"filename": "f.pdf", "page": 1, "text": "t1"},
                                    {"filename": "f.pdf", "page": 2, "text": "t2"}])]
_, g2 = DR.merge_sections(secs2)
ck("同文件不同页→两条来源", len(g2) == 2)

print("\n=== 3. render_sources_block ===")
blk = DR.render_sources_block(gsrc)
ck("含参考来源标题", "参考来源" in blk)
ck("含全局编号 [1][2][3]", "[1]" in blk and "[2]" in blk and "[3]" in blk)
ck("空来源→空串", DR.render_sources_block([]) == "")

print("\n=== 4. build_report（组装长报告）===")
rep = DR.build_report("比亚迪 vs 特斯拉", secs)
ck("段数=2", rep.n_sections == 2)
ck("全局来源=3", rep.n_sources == 3)
ck("报告含标题", "深度研究报告" in rep.markdown)
ck("报告含各子主题标题", "## A" in rep.markdown and "## B" in rep.markdown)
ck("报告含参考来源块", "参考来源" in rep.markdown)
ck("报告含全局引用[3]", "[3]" in rep.markdown)
ck("to_dict 可序列化", isinstance(rep.to_dict(), dict) and "markdown" in rep.to_dict())
ck("空 sections 不抛", isinstance(DR.build_report("q", []), DR.ResearchReport))

print("\n=== 5. plan_subtopics（拆子主题）===")
subs = DR.plan_subtopics("对比 比亚迪 和 特斯拉 的营收与利润", max_parts=4)
ck("拆出多个子主题", isinstance(subs, list) and len(subs) >= 1)
ck("子主题数不超上限", len(DR.plan_subtopics("a、b、c、d、e、f、g", max_parts=3)) <= 3)
ck("空问题安全", isinstance(DR.plan_subtopics(""), list))

print("\n=== 6. _default_summary（无 LLM 兜底带引用）===")
s = DR._default_summary("营收", [{"text": "营收2700亿"}, {"text": "增长20%"}])
ck("兜底摘要带本地引用", "[1]" in s and "[2]" in s)
ck("无来源→说明未检索到", "未检索到" in DR._default_summary("x", []))

print("\n=== 7. run_deep_research 端到端（mock 检索/摘要）===")
def fake_search(subtopic):
    # 每个子主题返回带来源，且都引用一个公共来源以验证跨段去重
    common = {"filename": "common.pdf", "page": 1, "text": "公共依据"}
    if "营收" in subtopic:
        return [common, {"filename": "rev.pdf", "page": 2, "text": "营收数据"}]
    return [common, {"filename": "other.pdf", "page": 3, "text": "其他数据"}]
def fake_summarize(subtopic, sources):
    return f"{subtopic}的结论见证据[1]，另参考[2]。"
rep2 = DR.run_deep_research("对比 营收 和 利润", search_fn=fake_search,
                            summarize_fn=fake_summarize, max_subtopics=2, audit=False)
ck("端到端产出报告", rep2.markdown and "深度研究报告" in rep2.markdown)
ck("跨子主题公共来源被去重", rep2.n_sources < 4)   # 2子主题*2来源=4，去重后<4
ck("报告非空段", rep2.n_sections >= 1)

# search_fn 抛错 → 该段降级，不整体崩
def boom_search(s): raise RuntimeError("检索挂了")
rep3 = DR.run_deep_research("x", search_fn=boom_search, max_subtopics=1, audit=False)
ck("检索全失败仍产出报告（不抛）", isinstance(rep3, DR.ResearchReport) and rep3.markdown)

print("\n=== 8. 方案1 忠实度审计集成 ===")
# 提供真实可对照的来源，让审计能算出接地率
def grounded_search(s):
    return [{"filename": "g.pdf", "page": 1, "text": "比亚迪2024年营业收入为2700亿元人民币"}]
def grounded_sum(s, src):
    return "比亚迪2024年营业收入为2700亿元[1]。"
rep4 = DR.run_deep_research("比亚迪营收", search_fn=grounded_search,
                            summarize_fn=grounded_sum, max_subtopics=1, audit=True)
ck("审计产出 grounding 分(0~1或None)", rep4.grounding is None or (0.0 <= rep4.grounding <= 1.0))

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
# 并把 sys.exit 收进 __main__ 保护，保留 `python tests/test_deep_research.py` 直跑的能力。
def test_all():
    """pytest 入口：任一检查失败即断言失败（不再依赖 sys.exit 传递结果）。"""
    assert F == 0, f"{F} 项检查未通过（详见上方 ✗ 行）"


if __name__ == "__main__":
    sys.exit(1 if F else 0)
