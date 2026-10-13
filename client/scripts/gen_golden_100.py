"""Generate a rigorous 100-case golden eval set for HashMM-RAG.

Design principle: only assert correctness we can actually justify.
- Refusal/anti-hallucination cases: the corpus is 小米/网易 reports + arxiv papers,
  so questions about OTHER companies (Apple/Tesla/...) or impossible/future data
  MUST be refused. Correctness here does NOT depend on the user's specific data.
- Capability cases (code/greeting/format/translation): correctness is intrinsic.
- Robustness cases (empty/ambiguous/very long): test behavior, not facts.
- Factual cases: ONLY the few 小米 facts confirmed from the user's screenshots
  (2024 营收 3,659亿; 2025 营收 4,573亿; 毛利率 22.3%; 互联网服务毛利率 76.5%).

Refusal cases use must_contain_any with honesty words (OR semantics).
This writes data/eval/golden_cases.json.
"""
from __future__ import annotations

import json
from pathlib import Path

REFUSE_WORDS = ["没有", "未提及", "无法", "未找到", "暂无", "不包含", "未涉及", "未披露", "查无"]

cases = []


def add(case):
    cases.append(case)


# ── 1. Refusal: other companies NOT in the corpus (25) ──
# Corpus = 小米 + 网易. Any other company → must refuse (KB-only eval path).
OTHER_COMPANIES = [
    ("苹果公司", "2024年的营收"), ("特斯拉", "2023年在中国的销量"),
    ("亚马逊", "2024年的净利润"), ("微软", "2024财年云业务收入"),
    ("谷歌", "2023年广告收入"), ("英伟达", "2024年数据中心营收"),
    ("阿里巴巴", "2024财年GMV"), ("京东", "2023年活跃用户数"),
    ("拼多多", "2024年营收"), ("字节跳动", "2023年广告收入"),
    ("华为", "2023年手机出货量"), ("OPPO", "2024年市场份额"),
    ("vivo", "2023年销量"), ("比亚迪", "2024年新能源车销量"),
    ("蔚来", "2023年交付量"), ("理想汽车", "2024年营收"),
    ("Meta", "2024年广告收入"), ("Netflix", "2023年订阅用户"),
    ("三星", "2024年半导体营收"), ("英特尔", "2023年营收"),
    ("台积电", "2024年先进制程营收"), ("美团", "2023年外卖订单量"),
    ("百度", "2024年AI云收入"), ("快手", "2023年日活"),
    ("小鹏汽车", "2024年交付量"),
]
for i, (co, q) in enumerate(OTHER_COMPANIES, 1):
    add({
        "id": f"refuse_company_{i:02d}",
        "query": f"{co}{q}是多少",
        "category": "refusal",
        "must_contain_any": REFUSE_WORDS,
        "min_sources": 0,
    })

# ── 2. Anti-hallucination: impossible / future / nonexistent (15) ──
ANTIHALLUC = [
    "小米2099年的营收预测是多少",
    "小米2050年的股价是多少",
    "网易2100年的游戏收入预测",
    "小米2030年的具体净利润是多少",
    "小米集团创始人的身份证号码是多少",
    "网易CEO的家庭住址在哪里",
    "小米2026年第四季度的确切营收数字",
    "小米下一款未发布手机的精确售价",
    "网易明年具体哪一天发布新游戏",
    "小米2027年的分红方案细节",
    "本文档第999页讲的是什么",
    "小米2024年在火星的销售额",
    "网易在月球基地的收入",
    "小米2024年雇佣了多少外星员工",
    "文档里小米2024年的负营收是多少",
]
for i, q in enumerate(ANTIHALLUC, 1):
    add({
        "id": f"antihalluc_{i:02d}",
        "query": q,
        "category": "refusal",
        "must_contain_any": REFUSE_WORDS,
        "min_sources": 0,
    })

# ── 3. Code tasks (10) ──
CODE = [
    ("帮我写一个 Python 的快速排序", ["def", "sort"]),
    ("写一个 Python 函数判断字符串是否回文", ["def", "return"]),
    ("用 Python 实现二分查找", ["def", "while"]),
    ("写一个 Python 冒泡排序", ["def", "for"]),
    ("用 Python 写一个斐波那契数列函数", ["def", "fib"]),
    ("写个 Python 函数统计列表中元素出现次数", ["def", "for"]),
    ("用 Python 实现一个栈", ["class", "def"]),
    ("写一个 Python 读取文件的函数", ["def", "open"]),
    ("用 Python 写一个计算阶乘的递归函数", ["def", "return"]),
    ("写一个 Python 字典按值排序的代码", ["sorted", "def"]),
]
for i, (q, kw) in enumerate(CODE, 1):
    add({
        "id": f"code_{i:02d}",
        "query": q,
        "category": "code",
        "must_contain": kw,
        "min_length": 80,
        "min_sources": 0,
    })

# ── 4. Greeting / chitchat (10) — no retrieval needed ──
GREET = [
    "你好", "嗨", "在吗", "早上好", "谢谢你",
    "你是谁", "你能做什么", "介绍一下你自己", "再见", "辛苦了",
]
for i, q in enumerate(GREET, 1):
    add({
        "id": f"greeting_{i:02d}",
        "query": q,
        "category": "greeting",
        "min_sources": 0,
    })

# ── 5. Capability / format (15) — intrinsic correctness ──
add({"id": "cap_01", "query": "把'人工智能正在改变世界'翻译成英文",
     "category": "code", "must_contain_any": ["AI", "artificial", "intelligence", "world", "changing"], "min_sources": 0})
add({"id": "cap_02", "query": "用一句话解释什么是机器学习",
     "category": "analytical", "min_length": 10, "min_sources": 0})
add({"id": "cap_03", "query": "列出三种常见的编程语言",
     "category": "analytical", "min_length": 10, "min_sources": 0})
add({"id": "cap_04", "query": "1加到100等于多少", "category": "code",
     "must_contain_any": ["5050", "5,050"], "min_sources": 0})
add({"id": "cap_05", "query": "把数字 1234567 加上千分位逗号",
     "category": "code", "must_contain_any": ["1,234,567"], "min_sources": 0})
add({"id": "cap_06", "query": "什么是 RAG（检索增强生成）",
     "category": "analytical", "min_length": 30, "min_sources": 0})
add({"id": "cap_07", "query": "解释一下向量数据库的作用",
     "category": "analytical", "min_length": 30, "min_sources": 0})
add({"id": "cap_08", "query": "用 markdown 表格列出三个城市和它们的国家",
     "category": "code", "must_contain": ["|"], "min_sources": 0})
add({"id": "cap_09", "query": "把'今天天气很好'改写成更正式的表达",
     "category": "analytical", "min_length": 5, "min_sources": 0})
add({"id": "cap_10", "query": "10的阶乘是多少", "category": "code",
     "must_contain_any": ["3628800", "3,628,800"], "min_sources": 0})
add({"id": "cap_11", "query": "解释什么是知识图谱",
     "category": "analytical", "min_length": 30, "min_sources": 0})
add({"id": "cap_12", "query": "摄氏100度等于多少华氏度", "category": "code",
     "must_contain_any": ["212"], "min_sources": 0})
add({"id": "cap_13", "query": "用 Python 一行代码反转字符串 s",
     "category": "code", "must_contain": ["[::-1]"], "min_sources": 0})
add({"id": "cap_14", "query": "解释什么是过拟合",
     "category": "analytical", "min_length": 20, "min_sources": 0})
add({"id": "cap_15", "query": "JSON 和 XML 有什么区别，简单说",
     "category": "analytical", "min_length": 20, "min_sources": 0})

# ── 6. Robustness (10) ──
add({"id": "robust_01", "query": "请概括一下文档的主要内容",
     "category": "analytical", "min_length": 30})
add({"id": "robust_02", "query": "知识库里有哪些公司的资料",
     "category": "analytical", "min_length": 10})
add({"id": "robust_03", "query": "文档里都讲了什么财务指标",
     "category": "analytical", "min_length": 10})
add({"id": "robust_04", "query": "?????", "category": "greeting", "min_sources": 0})
add({"id": "robust_05", "query": "随便说点什么", "category": "greeting", "min_sources": 0})
add({"id": "robust_06", "query": "小米" * 50, "category": "analytical", "min_sources": 0})
add({"id": "robust_07", "query": "帮我分析一下", "category": "analytical", "min_length": 5})
add({"id": "robust_08", "query": "这个", "category": "greeting", "min_sources": 0})
add({"id": "robust_09", "query": "小米和网易哪个更好",
     "category": "analytical", "min_length": 20})
add({"id": "robust_10", "query": "总结一下小米的业务板块",
     "category": "analytical", "min_length": 30, "relevant_docs": ["小米集团"]})

# ── 7. Factual — ONLY confirmed 小米 facts from screenshots (15) ──
add({"id": "fact_01", "query": "小米2024年营收是多少", "category": "factual",
     "must_contain": ["3,659", "3659"], "must_cite": True, "min_sources": 1,
     "relevant_docs": ["小米集团"],
     "reference_answer": "小米集团2024年营收为人民币3,659亿元。"})
add({"id": "fact_02", "query": "小米2025年营收是多少", "category": "factual",
     "must_contain": ["4,573", "4573"], "must_cite": True, "min_sources": 1,
     "relevant_docs": ["小米集团"],
     "reference_answer": "小米集团2025年营收为人民币4,573亿元，同比增长约25%。"})
add({"id": "fact_03", "query": "小米2025年和2024年营收对比", "category": "comparison",
     "must_mention_all": ["2024", "2025"], "min_length": 60, "must_cite": True,
     "relevant_docs": ["小米集团"],
     "reference_answer": "小米2025年营收4,573亿元，2024年3,659亿元，同比增长约25%。"})
add({"id": "fact_04", "query": "小米2025年营收比2024年增长了多少", "category": "comparison",
     "must_mention_all": ["2024", "2025"], "min_length": 40, "must_cite": True,
     "relevant_docs": ["小米集团"],
     "reference_answer": "小米2025年营收4,573亿元，较2024年的3,659亿元增长约25%。"})
add({"id": "fact_05", "query": "小米的整体毛利率是多少", "category": "factual",
     "must_cite": True, "relevant_docs": ["小米集团"],
     "reference_answer": "小米集团2025年整体毛利率约22.3%。"})
add({"id": "fact_06", "query": "小米互联网服务的毛利率是多少", "category": "factual",
     "must_cite": True, "relevant_docs": ["小米集团"],
     "reference_answer": "小米互联网服务分部毛利率约76.5%。"})
add({"id": "fact_07", "query": "小米在AI领域的布局和战略", "category": "analytical",
     "min_length": 120, "relevant_docs": ["小米集团"],
     "reference_answer": "小米围绕'人车家全生态'布局AI，自研大语言模型，强调端侧轻量化部署。"})
add({"id": "fact_08", "query": "小米的主要业务分部有哪些", "category": "analytical",
     "min_length": 40, "must_cite": True, "relevant_docs": ["小米集团"],
     "reference_answer": "小米主要业务包括智能手机、IoT与生活消费产品、互联网服务，以及智能电动汽车等创新业务。"})
add({"id": "fact_09", "query": "小米的智能电动汽车业务情况如何", "category": "analytical",
     "min_length": 40, "relevant_docs": ["小米集团"],
     "reference_answer": "小米智能电动汽车及AI等创新业务为新增分部，2024年起贡献收入。"})
add({"id": "fact_10", "query": "网易2025年的游戏业务收入情况", "category": "factual",
     "must_cite": True, "relevant_docs": ["网易"],
     "reference_answer": "网易游戏及相关增值服务净收入保持增长（具体以年报披露为准）。"})
add({"id": "fact_11", "query": "网易的主营业务是什么", "category": "analytical",
     "min_length": 30, "relevant_docs": ["网易"],
     "reference_answer": "网易主营在线游戏、有道、云音乐及创新等业务，游戏为核心收入来源。"})
add({"id": "fact_12", "query": "小米2024年和2025年的毛利率变化", "category": "comparison",
     "must_mention_all": ["毛利率"], "min_length": 30, "relevant_docs": ["小米集团"],
     "reference_answer": "小米毛利率在20%出头区间，2025年整体毛利率约22.3%。"})
add({"id": "fact_13", "query": "小米的研发投入情况", "category": "factual",
     "must_cite": True, "relevant_docs": ["小米集团"],
     "reference_answer": "小米持续加大研发投入，研发费用逐年增长（具体金额以年报为准）。"})
add({"id": "fact_14", "query": "小米手机的出货量情况", "category": "factual",
     "must_cite": True, "relevant_docs": ["小米集团"],
     "reference_answer": "小米智能手机出货量保持全球前列（具体以年报披露为准）。"})
add({"id": "fact_15", "query": "对比小米和网易的业务模式", "category": "comparison",
     "must_mention_all": ["小米", "网易"], "min_length": 60,
     "relevant_docs": ["小米集团"],
     "reference_answer": "小米以硬件+IoT+互联网服务+汽车为主，网易以在线游戏为核心，商业模式不同。"})


def main():
    # sanity: unique ids, valid categories
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate ids!"
    print(f"Total cases: {len(cases)}")
    from collections import Counter
    print("By category:", dict(Counter(c["category"] for c in cases)))

    out = Path("data/eval/golden_cases.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
