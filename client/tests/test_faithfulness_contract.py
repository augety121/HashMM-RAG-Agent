"""忠实度合约隔离单测——纯 stdlib，沙箱直跑。覆盖事实句判定/切句/支撑判定/门控。"""
import sys
import os as _os; sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))

from hashmm.evaluation import faithfulness as F

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  <<< FAIL")


print("=== 1. 切句 ===")
sents = F.split_sentences("公司A营收26.7亿[1]。公司B更高[2]！它怎么样？\n第三行内容。")
check("中文句号/感叹/问号切句", len(sents) == 4)
check("保留句内角标", "[1]" in sents[0])

print("\n=== 2. strip_noise 不把代码/标题当事实 ===")
ns = F.strip_noise("# 标题\n```python\nx=5576\n```\n正文5576个向量[1]。")
check("代码块被剥离", "x=5576" not in ns)
check("正文保留", "5576个向量" in ns)

print("\n=== 3. 数据数字抽取（裸单数字不算）===")
check("小数算数据", F._data_numbers("准确率90.0%") != [])
check("≥2位整数算数据", F._data_numbers("共5576个向量") != [])
check("数字+单位算数据", F._data_numbers("提升3倍") != [])
check("年份算数据", F._data_numbers("2024年发布") != [])
check("裸单数字(枚举)不算", F._data_numbers("我分3步说明") == [])
check("序号步骤不算", F._data_numbers("第2步是检索") == [] or "2步" not in str(F._data_numbers("第2步是检索")))

print("\n=== 4. 事实句判定 ===")
check("含数据→事实句", F.is_factual_claim("该索引含5576个向量[1]") is True)
check("含比较→事实句", F.is_factual_claim("方案A比方案B更准确[1]") is True)
check("书名号实体→事实句", F.is_factual_claim("它引用了《年度报告》的结论[1]") is True)
check("元话语句→豁免", F.is_factual_claim("综上所述，效果不错") is False)
check("纯过渡句→豁免", F.is_factual_claim("首先我们来看检索") is False)
check("问句→豁免", F.is_factual_claim("这个方案怎么样呢？") is False)
check("过短句→豁免", F.is_factual_claim("好的[1]") is False)
check("枚举句不误判", F.is_factual_claim("我分3步来说明这个流程") is False)

print("\n=== 5. 引用抽取 ===")
check("抽取多个角标", F.extract_citations("见[1]和[3]的内容") == [1, 3])
check("排除markdown链接", F.extract_citations("点[这里](http://x)") == [])
check("排除数组下标", F.extract_citations("a[0]=1") == [])

print("\n=== 6. normalize_sources 多形态 ===")
check("dict带text", F.normalize_sources([{"text": "abc"}]) == ["abc"])
check("纯字符串", F.normalize_sources(["xyz"]) == ["xyz"])
check("content字段", F.normalize_sources([{"content": "def"}]) == ["def"])
check("空过滤", F.normalize_sources([{"text": ""}, "  "]) == [])

print("\n=== 7. 词法支撑 ===")
ev = ["公司A在2024年的营收达到26.7亿元，同比增长显著。"]
check("高重叠→高支撑", F.lexical_support("公司A营收26.7亿元", ev) > 0.5)
check("无关句→低支撑", F.lexical_support("天气预报说明天下雨", ev) < 0.2)

print("\n=== 8. 主审计：全部正确引用 → ok ===")
answer_ok = "公司A在2024年营收达到26.7亿元[1]。该数字同比增长明显[1]。"
ev_ok = [{"text": "公司A 2024年营收26.7亿元，同比增长明显，是历史新高。"}]
r = F.audit_faithfulness(answer_ok, ev_ok)
check("checked=True", r.checked is True)
check("ok=True", r.ok is True)
check("无unsupported", r.unsupported == [])
check("无uncited", r.uncited == [])
check("ratio=1.0", r.ratio == 1.0)

print("\n=== 9. 主审计：事实句无引用 → uncited ===")
answer_unc = "公司A在2024年营收达到26.7亿元。"   # 无 [N]
r = F.audit_faithfulness(answer_unc, ev_ok)
check("命中uncited", len(r.uncited) == 1)
check("ok=False", r.ok is False)

print("\n=== 10. 主审计：引用了但证据完全不支持 → unsupported ===")
answer_bad = "公司A在2024年的净利润高达88亿元[1]。"
ev_mismatch = [{"text": "今天天气晴朗，气温适宜，适合户外活动散步。"}]
r = F.audit_faithfulness(answer_bad, ev_mismatch)
check("命中unsupported(近乎零重叠)", len(r.unsupported) == 1)
check("ok=False", r.ok is False)

print("\n=== 11. 无证据 → 不判定(checked=False, ok=True) ===")
r = F.audit_faithfulness("公司A营收26.7亿[1]", [])
check("checked=False", r.checked is False)
check("ok=True(无依据不定罪)", r.ok is True)

print("\n=== 12. 灰区给benefit-of-the-doubt(压假阳性) ===")
# 句子部分词命中证据(中等重叠)，无 judge → 不应判 unsupported
answer_gray = "公司A的营收情况在报告中有详细披露和分析说明[1]。"
ev_gray = [{"text": "公司A的营收情况、利润结构、现金流在年度报告中均有披露。"}]
r = F.audit_faithfulness(answer_gray, ev_gray)
check("中等重叠不误伤", len(r.unsupported) == 0)

print("\n=== 13. judge 兜底：judge 判不支持 → unsupported ===")
def judge_no(sentence, ev):
    return False    # 模拟 judge 判定"不支持"
answer_j = "公司A的市场份额位列行业第一并持续扩大领先优势[1]。"
ev_j = [{"text": "公司A是一家成立于2010年的科技企业，主营软件开发业务。"}]
r = F.audit_faithfulness(answer_j, ev_j, judge_fn=judge_no)
# 该句含"第一/领先"(比较级)→事实句；与证据低重叠→灰区→judge 判 False
check("judge=False触发unsupported", len(r.unsupported) >= 1)
check("judge_calls计数", r.judge_calls >= 1)

print("\n=== 14. judge 兜底：judge 判支持 → supported ===")
def judge_yes(sentence, ev):
    return True
r2 = F.audit_faithfulness(answer_j, ev_j, judge_fn=judge_yes)
check("judge=True则不报unsupported", len(r2.unsupported) == 0)

print("\n=== 15. judge 调用上限 ===")
calls = {"n": 0}
def judge_count(s, e):
    calls["n"] += 1
    return None
many = "。".join([f"指标{i}达到{i*11+10}的高水平并显著领先同业[1]" for i in range(10)]) + "。"
ev_x = [{"text": "无关内容"}]
r = F.audit_faithfulness(many, ev_x, judge_fn=judge_count, max_judge_calls=3)
check("judge调用不超上限", calls["n"] <= 3)

print("\n=== 16. 门控决策 ===")
rep = F.FaithfulnessReport(checked=True, unsupported=[{"idx": 0, "sentence": "x", "cited": [1]}])
check("有unsupported→要修正", F.should_request_revision(rep) is True)
rep2 = F.FaithfulnessReport(checked=True, uncited=[{"idx": 0, "sentence": "x"}])
check("仅1条uncited→不修正(容忍)", F.should_request_revision(rep2) is False)
rep3 = F.FaithfulnessReport(checked=True, uncited=[{"idx": 0, "sentence": "x"}, {"idx": 1, "sentence": "y"}])
check("≥2条uncited→要修正", F.should_request_revision(rep3) is True)
rep4 = F.FaithfulnessReport(checked=False)
check("未检查→不修正", F.should_request_revision(rep4) is False)

print("\n=== 17. 修正指引/UI摘要可生成 ===")
instr = F.build_revision_instruction(rep)
check("修正指引含关键词", "忠实度" in instr and "无法确认" in instr)
ui = F.summarize_for_ui(rep)
check("UI摘要结构", ui["checked"] is True and "flagged" in ui)

print("\n=== 18. 真实场景：HashMM自述水位(全引用) ===")
real_answer = ("HashMM 的真实索引包含5576个向量、覆盖68个文档[1]。"
               "知识图谱含533个实体与546个关系[2]。"
               "Self-RAG 在多跳集上准确率达到90.0%[3]。")
real_ev = [
    {"text": "data/vector_index 真实索引：5576 向量 / 68 文档，BM25 同规模。"},
    {"text": "知识图谱：533 实体 / 546 关系 / 33 社区，已落地。"},
    {"text": "Self-RAG（自我批判 + 忠实度门控）多跳准确率 90.0%，是当前最高水位。"},
]
r = F.audit_faithfulness(real_answer, real_ev)
check("真实自述全接地→ok", r.ok is True)
check("3条事实句全supported", r.supported == 3)

print(f"\n{'='*48}\n结果：PASS={PASS}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
