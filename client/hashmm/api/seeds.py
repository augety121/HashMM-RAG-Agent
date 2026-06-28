"""seeds — 离线开箱即用的预置内容（V131 扩充：对标大厂提示词库 / 技能库）。

本地新库是空的；这里种入一套高质量预置：~22 个提示词模板 + ~8 个示例技能。
关键改动（V131）：**逐条按名称幂等**——不再「表非空就整体跳过」。这样以后往
BUILTIN_TEMPLATES / BUILTIN_SKILLS 里加新条目，下次启动会【只补缺失的】，
不重复、不覆盖已有（保留 use_count 等真实反馈）。HASHMM_NO_SEED=1 一键关闭。
分类对齐前端图标：code / document / data / knowledge / general。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# (name, category, prompt, [variables])
BUILTIN_TEMPLATES = [
    # ── 既有 8 个（保留，分类对齐前端） ──
    ("代码评审", "code",
     "请对下面的代码做一次专业评审：\n1) 正确性与边界条件 2) 性能与复杂度 3) 可读性与命名 "
     "4) 安全隐患 5) 给出可直接套用的修改建议（diff 形式）。\n\n```{language}\n{code}\n```",
     ["language", "code"]),
    ("单元测试生成", "code",
     "为下面的 {language} 代码编写完整单元测试：覆盖正常路径、边界条件、异常路径；"
     "使用 {framework} 框架；每个用例附一句中文说明。\n\n```{language}\n{code}\n```",
     ["language", "framework", "code"]),
    ("数据分析报告", "data",
     "基于以下数据/描述，产出一份结构化分析报告：核心结论（3 条以内，先讲结论）→ "
     "关键指标与同环比 → 异常点与可能原因 → 建议行动项。语言简洁，量化优先。\n\n{data}",
     ["data"]),
    ("竞品对比", "data",
     "对比 {a} 与 {b}：维度包括定位、核心功能、技术架构、定价、优劣势；"
     "输出对比表 + 一段选型建议（说明适用场景）。", ["a", "b"]),
    ("论文精读", "knowledge",
     "请精读这篇论文：{paper}\n输出：一句话概括 → 解决什么问题（动机）→ 方法核心"
     "（配公式直觉解释）→ 实验与结论 → 局限与可改进点 → 与相关工作的关系。",
     ["paper"]),
    ("技术方案文档", "document",
     "为「{feature}」写一份技术方案：背景与目标 → 现状与问题 → 方案设计（含架构图文字描述、"
     "数据流、接口定义）→ 风险与回滚 → 里程碑排期。面向工程评审，避免空话。",
     ["feature"]),
    ("会议纪要整理", "document",
     "把下面的会议记录整理成纪要：参会与时间 → 结论与决议（醒目）→ 行动项"
     "（负责人 + 截止时间表格）→ 待定问题。删除口语冗余，保留关键分歧。\n\n{notes}",
     ["notes"]),
    ("中英互译润色", "general",
     "将以下内容在中英之间互译（自动判断方向），要求：忠实原意、术语准确、行文自然；"
     "若是技术内容保留代码与专有名词。翻译后附 2-3 处值得注意的措辞说明。\n\n{text}",
     ["text"]),

    # ── V131 新增：写作 / 生产力 ──
    ("专业邮件起草", "general",
     "帮我写一封邮件。\n收件人/关系：{recipient}\n目的：{goal}\n关键信息：{points}\n"
     "语气：{tone}（如正式/友好/简洁）。要求：主题行 + 正文，开门见山、礼貌得体、"
     "有明确的下一步行动；中文。", ["recipient", "goal", "points", "tone"]),
    ("周报生成", "document",
     "把我这周做的事整理成一份条理清晰的周报：本周完成（按重要性，量化成果）→ "
     "数据/指标 → 进行中 → 下周计划 → 风险与需协调。语气专业、突出价值。\n\n{items}",
     ["items"]),
    ("产品需求文档 PRD", "document",
     "为「{feature}」写一份 PRD：一句话价值主张 → 背景与目标用户 → 用户故事（As a… I want… so that…）→ "
     "功能需求（按优先级 P0/P1/P2）→ 非功能需求 → 验收标准 → 度量指标。面向研发与设计评审。",
     ["feature"]),
    ("简历优化", "document",
     "优化下面这段简历经历，使其更有竞争力：用「动词 + 量化结果 + 方法」的 STAR 句式重写；"
     "突出影响力与数据；去掉空泛形容词；保持真实。目标岗位：{role}。\n\n{experience}",
     ["role", "experience"]),
    ("营销文案", "general",
     "为「{product}」写一组营销文案，面向 {audience}。输出：3 个标题（不同angle）+ "
     "一段主体文案（突出核心收益，含一个行动号召 CTA）+ 5 条可用于社媒的短句。语气{tone}。",
     ["product", "audience", "tone"]),
    ("头脑风暴", "general",
     "围绕「{topic}」做一次结构化头脑风暴：先给 10 个差异化的点子（不评判），再挑出最有潜力的 3 个"
     "展开（价值、可行性、第一步）。鼓励跨界类比，避免平庸答案。", ["topic"]),

    # ── V131 新增：研发 / 数据 ──
    ("SQL 查询编写", "code",
     "根据需求写一条 SQL（方言：{dialect}）。\n表结构：{schema}\n需求：{requirement}\n"
     "要求：给出可直接运行的 SQL + 简要说明思路 + 如有性能隐患给出索引/改写建议。",
     ["dialect", "schema", "requirement"]),
    ("正则表达式", "code",
     "帮我写一个正则表达式：需求是 {requirement}。输出：正则本体 + 逐段解释每个分组的含义 + "
     "3 个匹配示例和 2 个不匹配示例 + 在 {language} 中的调用片段。", ["requirement", "language"]),
    ("Bug 根因分析", "code",
     "帮我定位这个 Bug。\n现象：{symptom}\n相关代码/日志：\n{context}\n"
     "请按：可能原因清单（按概率排序）→ 每个原因的验证方法 → 最可能的根因 → 修复方案 + "
     "防止复发的建议。先给最小可验证假设。", ["symptom", "context"]),
    ("API 文档生成", "document",
     "为下面的接口生成规范的 API 文档：用途 → 请求方法与路径 → 参数表（名称/类型/必填/说明）→ "
     "请求示例 → 响应示例（含错误码）→ 注意事项。\n\n{code}", ["code"]),
    ("代码注释与文档字符串", "code",
     "给下面的 {language} 代码补充清晰的注释与文档字符串：模块/函数职责、参数与返回、"
     "边界与异常、一个用法示例。不改变代码逻辑，只加注释。\n\n```{language}\n{code}\n```",
     ["language", "code"]),
    ("数据清洗与转换", "data",
     "我有一份数据需要清洗/转换。\n样例数据：{sample}\n目标：{goal}\n"
     "请给出：清洗步骤（缺失值/异常值/格式统一/去重的处理）→ 可执行的 {tool} 代码 → "
     "转换前后对照说明。", ["sample", "goal", "tool"]),

    # ── V131 新增：学习 / 知识 ──
    ("概念讲解（费曼）", "knowledge",
     "用费曼方法给我讲清楚「{concept}」：先用一句大白话定义 → 用一个生活化类比 → "
     "拆解关键组成 → 一个最小例子 → 常见误解 → 检验我是否真懂的 2 个问题。受众水平：{level}。",
     ["concept", "level"]),
    ("学习计划", "knowledge",
     "帮我制定学习「{subject}」的计划。现有基础：{background}，每周可投入 {hours} 小时，"
     "目标 {goal}。输出：分阶段路线（里程碑）→ 每阶段资源与练习 → 检验标准 → 常见坑。",
     ["subject", "background", "hours", "goal"]),
]

BUILTIN_SKILLS = [
    # ── 既有 2 个 ──
    {
        "name": "结构化解题",
        "description": "复杂问题先拆解再求解：约束清单 → 分步推演 → 自检结论",
        "triggers": ["帮我分析", "怎么解决", "为什么会", "排查"],
        "prompt": "遇到复杂问题时：1) 先列出已知条件与隐含约束；2) 拆成可独立验证的子问题；"
                  "3) 逐步推演并标注每步依据；4) 得出结论后做一次反向自检（结论倒推条件是否成立）；"
                  "5) 给出可执行的下一步。",
        "tools": [],
    },
    {
        "name": "代码重构助手",
        "description": "重构请求的标准动作：先测后改、小步提交、行为不变",
        "triggers": ["重构", "优化这段代码", "代码太乱"],
        "prompt": "执行重构时：1) 先识别当前行为并补齐缺失的测试基线；2) 列出坏味道与目标结构；"
                  "3) 以小步等价变换推进，每步说明为什么行为不变；4) 输出最终代码 + 变更摘要 + "
                  "建议的回归验证点。",
        "tools": [],
    },
    # ── V131 新增 ──
    {
        "name": "调试助手",
        "description": "系统化排错：复现 → 缩小范围 → 假设验证 → 根因 → 修复",
        "triggers": ["报错", "调试", "为什么不工作", "debug", "异常", "stack trace"],
        "prompt": "排错时遵循科学方法：1) 先确认稳定复现步骤与期望/实际差异；2) 用二分/日志缩小范围，"
                  "定位最小失败用例；3) 提出可证伪的假设并逐个验证（先验证最可能的）；4) 锁定根因后给"
                  "最小修复；5) 补一条回归测试防复发。不要一次改多处。",
        "tools": [],
    },
    {
        "name": "需求澄清",
        "description": "动手前先问清楚：在需求模糊时主动澄清而非臆测",
        "triggers": ["帮我做一个", "我想要", "需求是", "做个功能"],
        "prompt": "当需求存在歧义或缺关键信息时，先用 2-4 个高价值问题澄清（目标用户、成功标准、约束、"
                  "边界），再动手。若信息已足够则直接做并说明所做假设。绝不在关键歧义上凭空猜测。",
        "tools": [],
    },
    {
        "name": "结构化提取",
        "description": "从非结构化文本抽取为规范 JSON/表格，缺失留空不臆造",
        "triggers": ["提取", "抽取", "整理成表格", "结构化", "提取字段"],
        "prompt": "做信息抽取时：1) 先确认目标字段（schema）；2) 逐条从原文抽取，只填原文支持的内容，"
                  "缺失填 null/空并标注；3) 输出规范 JSON 或表格；4) 末尾列出不确定/有歧义的项。"
                  "严禁编造原文没有的信息。",
        "tools": [],
    },
    {
        "name": "研究综述",
        "description": "多来源研究：对比观点、标注证据强度、给出平衡结论",
        "triggers": ["调研", "综述", "研究一下", "对比观点", "现状如何"],
        "prompt": "做研究综述时：1) 先界定问题范围与关键子问题；2) 分主题归纳不同来源的观点，明确"
                  "共识与分歧；3) 标注每个论断的证据强度（强/中/弱/存疑）；4) 给出平衡的结论与仍待"
                  "验证的问题；5) 区分事实与推测。",
        "tools": [],
    },
    {
        "name": "增量交付",
        "description": "大任务拆成可验证的小步，逐步交付并自检",
        "triggers": ["这个项目", "帮我规划", "任务太大", "怎么开始", "拆解任务"],
        "prompt": "面对大任务：1) 先拆成有先后依赖的小步（每步可独立验证、能产出可见结果）；2) 标出"
                  "关键路径与风险点；3) 按步推进，每步完成后自检并说明下一步；4) 不要一次性铺开所有"
                  "事情，先交付最小可用，再迭代。",
        "tools": [],
    },
    {
        "name": "文风改写",
        "description": "保持原意改写文风：更正式/简洁/口语/有说服力",
        "triggers": ["润色", "改写", "换个说法", "更正式", "更简洁", "改文风"],
        "prompt": "做文风改写时：1) 先确认目标风格与受众；2) 保持原意与事实不变，只调整措辞、结构与"
                  "语气；3) 去除冗余与口语赘词，增强可读性；4) 如改动较大，简要说明改了什么、为什么。",
        "tools": [],
    },
]


def _safe_filename(name: str) -> str:
    return "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name) + ".json"


def seed_builtin_content(log=None) -> dict:
    """逐条按名称幂等地补齐预置内容。返回 {templates: n, skills: n}（本次新增数）。"""
    out = {"templates": 0, "skills": 0}
    if os.environ.get("HASHMM_NO_SEED") == "1":
        return out
    _log = log or (lambda *_a: None)

    # 模板：只补「按名称缺失」的（已存在的绝不重复/覆盖）
    try:
        from hashmm.api import database as db
        existing_names = {t.get("name") for t in db.list_templates()}
        for name, cat, prompt, variables in BUILTIN_TEMPLATES:
            if name in existing_names:
                continue
            db.create_template(name, cat, prompt, variables, author="builtin")
            out["templates"] += 1
        if out["templates"]:
            _log(f"[seed] 补齐预置模板 {out['templates']} 个")
    except Exception as e:
        _log(f"[seed] 模板种子跳过: {e}")

    # 手动技能：只补「文件不存在」的（与管理后台手动创建同一存储格式）
    try:
        skills_dir = Path("data/skills")
        skills_dir.mkdir(parents=True, exist_ok=True)
        for s in BUILTIN_SKILLS:
            fpath = skills_dir / _safe_filename(s["name"])
            if fpath.exists():
                continue
            fpath.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
            out["skills"] += 1
        if out["skills"]:
            _log(f"[seed] 补齐预置技能 {out['skills']} 个")
    except Exception as e:
        _log(f"[seed] 技能种子跳过: {e}")
    return out
