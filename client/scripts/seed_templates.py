
"""Seed default prompt templates."""
import sys, os
# 项目根加入 sys.path，按现在的包结构 import（旧的直接 import database 已失效，
# 因为 database.py 现在 from hashmm.utils import ...，必须能找到 hashmm 包）。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hashmm.api import database as db

TEMPLATES = [
    ("Python 项目脚手架", "code",
     "帮我用 Python 创建一个完整的 {project_type} 项目，包含 main.py, utils.py, requirements.txt, README.md",
     ["project_type"]),
    ("PyTorch 模型实现", "code",
     "帮我用 PyTorch 实现 {model_name}，包含模型定义、训练循环、验证和推理代码",
     ["model_name"]),
    ("数据分析报告", "document",
     "帮我写一份关于 {topic} 的数据分析报告，包含数据概述、分析方法、关键发现和结论建议",
     ["topic"]),
    ("学术 PPT", "document",
     "帮我做一个关于 {topic} 的学术演讲 PPT，15-20 页，包含背景、方法、实验、结论",
     ["topic"]),
    ("论文综述", "document",
     "帮我写一份 {field} 领域的研究综述，涵盖关键方法、技术演进、当前挑战和未来方向",
     ["field"]),
    ("代码审查", "code",
     "帮我审查以下代码，找出 bug、性能问题和安全隐患，并给出修改建议",
     []),
    ("算法实现", "code",
     "帮我用 {language} 实现 {algorithm}，包含完整的代码、测试用例和复杂度分析",
     ["language", "algorithm"]),
    ("技术对比", "knowledge",
     "帮我对比 {tech_a} 和 {tech_b}，从原理、性能、适用场景和优缺点几个维度进行分析",
     ["tech_a", "tech_b"]),
]

if __name__ == "__main__":
    # 幂等：已存在同名模板就跳过，避免重复跑时插重复。
    try:
        existing = {t.get("name") for t in db.list_templates()}
    except Exception:
        existing = set()
    added = 0
    for name, cat, prompt, vars in TEMPLATES:
        if name in existing:
            print(f"  · 跳过(已存在) {name}")
            continue
        try:
            db.create_template(name, cat, prompt, vars)
            added += 1
            print(f"  ✅ {name}")
        except Exception as e:
            print(f"  ⚠️ {name}: {e}")
    print(f"\n✅ 新增 {added} 个模板（共定义 {len(TEMPLATES)} 个，已存在的已跳过）")
