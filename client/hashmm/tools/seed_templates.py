#!/usr/bin/env python3
"""一键重建默认提示词模板 —— 幂等补种，不覆盖你已有的。

背景：早前一次 `rm -rf data` 事故清空过 `./data`（见 DATA_LOSS_POSTMORTEM_AND_RECOVERY.md），
`prompt_templates` 表此后一直是空的（users/models 你手工重建了，模板没人补）。这导致
管理后台「模板」页显示「0 个模板」。代码与接口本身正常（/api/admin/templates 返回 200），
只是表里没数据 —— 本脚本把一批常用模板种回去。

特点：
  - **幂等**：按模板 name 去重，已存在的跳过，不重复插、不覆盖你后来新建的任何模板。
  - **不动其它表**、不删任何数据、不碰 data 目录下别的文件。
  - 用项目自己的 `database.create_template`，category 与前端筛选标签一致
    （general 通用 / code 代码 / analysis 分析 / document 文档）。

用法（真机，项目根 /root/autodl-tmp 下，服务开不开都行）：
    python -m hashmm.tools.seed_templates          # 补种缺失的默认模板
    python -m hashmm.tools.seed_templates --list   # 只看当前有哪些，不写入
    python -m hashmm.tools.seed_templates --dry     # 演练，只打印将要新增的，不写库
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# 默认模板集。variables 用 {var} 占位，前端「使用模板」时可替换。
# 对标大厂 RAG-Agent（Dify / Coze / RAGFlow / Cherry Studio）的场景库，覆盖
# 通用/代码/分析/文档/写作/研究/客服/数据/智能体 九大类，~35 个常用模板。
DEFAULT_TEMPLATES: list[dict] = [
    # ── 通用 general ──
    {
        "name": "知识库问答",
        "category": "general",
        "prompt": "基于知识库内容回答下面的问题，引用来源用 [1][2] 标注；若知识库不足以回答，"
                  "明确说明并补充你的判断。\n\n问题：{question}",
        "variables": ["question"],
    },
    {
        "name": "多文档对比",
        "category": "general",
        "prompt": "对比以下若干主体在指定维度上的异同，给出结构化对比表与一句话结论。\n\n"
                  "对比对象：{subjects}\n对比维度：{dimensions}",
        "variables": ["subjects", "dimensions"],
    },
    {
        "name": "要点总结",
        "category": "general",
        "prompt": "把下面的内容总结成 {n} 条要点，每条不超过 30 字，按重要性排序。\n\n内容：{content}",
        "variables": ["n", "content"],
    },
    {
        "name": "追问澄清",
        "category": "general",
        "prompt": "用户的问题可能有歧义：{question}。请先列出 2-3 个需要澄清的关键点，"
                  "再给出在不同假设下的简要回答。",
        "variables": ["question"],
    },
    {
        "name": "分步推理",
        "category": "general",
        "prompt": "请一步步推理解决下面的问题，先列出已知条件与求解目标，再逐步推导，最后给出结论。\n\n"
                  "问题：{question}",
        "variables": ["question"],
    },
    # ── 代码 code ──
    {
        "name": "代码审查",
        "category": "code",
        "prompt": "审查下面的代码，指出潜在 bug、安全隐患、性能问题与可读性改进，按严重程度排序，"
                  "并给出修改建议（必要时附改写片段）。\n\n```\n{code}\n```",
        "variables": ["code"],
    },
    {
        "name": "函数实现",
        "category": "code",
        "prompt": "用 {language} 实现一个函数：{requirement}。要求：含类型标注、边界处理、"
                  "简短 docstring，并给一个使用示例。",
        "variables": ["language", "requirement"],
    },
    {
        "name": "报错诊断",
        "category": "code",
        "prompt": "下面是一段报错/堆栈，请判断最可能的根因（不要猜多个都写），给出定位思路与修复方案。\n\n"
                  "报错：\n{error}\n\n相关代码（可选）：\n{code}",
        "variables": ["error", "code"],
    },
    {
        "name": "单元测试生成",
        "category": "code",
        "prompt": "为下面的 {language} 代码生成单元测试，覆盖正常路径、边界与异常情况，使用 {framework} 框架。\n\n"
                  "```\n{code}\n```",
        "variables": ["language", "framework", "code"],
    },
    {
        "name": "代码重构",
        "category": "code",
        "prompt": "重构下面的代码，目标是 {goal}（如可读性/性能/解耦）。保持外部行为不变，"
                  "说明每处改动的理由。\n\n```\n{code}\n```",
        "variables": ["goal", "code"],
    },
    {
        "name": "SQL 编写与优化",
        "category": "code",
        "prompt": "根据需求写一条 SQL：{requirement}。表结构：{schema}。给出 SQL 并说明索引/性能注意点。",
        "variables": ["requirement", "schema"],
    },
    {
        "name": "正则表达式",
        "category": "code",
        "prompt": "写一个正则表达式匹配：{pattern_desc}。给出正则、解释每部分含义、并附 2-3 个匹配/不匹配示例。",
        "variables": ["pattern_desc"],
    },
    # ── 分析 analysis ──
    {
        "name": "财务数据分析",
        "category": "analysis",
        "prompt": "基于知识库中 {company} 的资料，分析其 {period} 的营收、利润、增长与结构，"
                  "用数据说话并标注来源，最后给一段趋势判断。",
        "variables": ["company", "period"],
    },
    {
        "name": "SWOT 分析",
        "category": "analysis",
        "prompt": "针对 {subject}，结合知识库资料做 SWOT 分析（优势/劣势/机会/威胁），"
                  "每项 2-4 条，附简要依据。",
        "variables": ["subject"],
    },
    {
        "name": "竞品分析",
        "category": "analysis",
        "prompt": "基于知识库资料，对比 {product} 与其主要竞品，从功能、定价、目标用户、优劣势"
                  "几个维度做表格化分析，并给出差异化建议。",
        "variables": ["product"],
    },
    {
        "name": "趋势研判",
        "category": "analysis",
        "prompt": "基于知识库中关于 {topic} 的历年数据，分析其变化趋势，指出拐点与驱动因素，"
                  "并对下一阶段做谨慎预测（标注不确定性）。",
        "variables": ["topic"],
    },
    {
        "name": "风险评估",
        "category": "analysis",
        "prompt": "针对 {subject}，识别主要风险点，按发生概率 × 影响程度排序，"
                  "每项给出缓解措施。",
        "variables": ["subject"],
    },
    {
        "name": "因果归因",
        "category": "analysis",
        "prompt": "现象：{phenomenon}。请基于知识库资料分析最可能的原因（区分直接/间接、"
                  "内因/外因），避免把相关当因果，标注证据强度。",
        "variables": ["phenomenon"],
    },
    # ── 文档 document ──
    {
        "name": "生成 PPT 大纲",
        "category": "document",
        "prompt": "围绕主题「{topic}」，基于知识库内容生成一份 {pages} 页的演示文稿大纲，"
                  "每页含标题与 3-5 个要点，逻辑连贯、重点突出。",
        "variables": ["topic", "pages"],
    },
    {
        "name": "撰写报告",
        "category": "document",
        "prompt": "基于知识库资料撰写一份关于「{topic}」的报告，包含背景、现状、分析、结论与建议，"
                  "正文用流畅中文段落，关键数据标注来源。",
        "variables": ["topic"],
    },
    {
        "name": "会议纪要",
        "category": "document",
        "prompt": "把下面的会议记录整理成规范纪要：包含议题、关键决策、待办事项（含负责人）、"
                  "后续计划。\n\n记录：\n{content}",
        "variables": ["content"],
    },
    {
        "name": "邮件撰写",
        "category": "document",
        "prompt": "帮我写一封 {tone}（如正式/友好）的邮件，目的：{purpose}，收件人：{recipient}。"
                  "给出主题行与正文。",
        "variables": ["tone", "purpose", "recipient"],
    },
    {
        "name": "产品需求文档",
        "category": "document",
        "prompt": "为功能「{feature}」写一份精简 PRD：背景与目标、用户故事、功能点、验收标准、"
                  "非功能需求、风险。",
        "variables": ["feature"],
    },
    {
        "name": "操作手册",
        "category": "document",
        "prompt": "基于知识库资料，为「{task}」编写一份分步操作手册，每步含目的、操作、预期结果，"
                  "并附常见问题排查。",
        "variables": ["task"],
    },
    # ── 写作 writing ──
    {
        "name": "文案润色",
        "category": "writing",
        "prompt": "润色下面的文字，使其更{style}（如专业/简洁/有感染力），保持原意，"
                  "标出主要改动。\n\n原文：{content}",
        "variables": ["style", "content"],
    },
    {
        "name": "标题生成",
        "category": "writing",
        "prompt": "为下面的内容生成 5 个吸引人的标题，风格 {style}，控制在 20 字内。\n\n内容：{content}",
        "variables": ["style", "content"],
    },
    {
        "name": "营销文案",
        "category": "writing",
        "prompt": "为产品「{product}」写一段面向 {audience} 的营销文案，突出 {selling_point}，"
                  "语气有号召力但不浮夸。",
        "variables": ["product", "audience", "selling_point"],
    },
    {
        "name": "中英互译",
        "category": "writing",
        "prompt": "把下面的内容翻译成 {target_lang}，保持术语准确、语气自然，"
                  "专有名词首次出现时保留原文。\n\n原文：{content}",
        "variables": ["target_lang", "content"],
    },
    # ── 研究 research ──
    {
        "name": "文献综述",
        "category": "research",
        "prompt": "基于知识库中关于 {field} 的资料，写一份研究综述：涵盖关键方法、技术演进、"
                  "当前挑战与未来方向，引用来源。",
        "variables": ["field"],
    },
    {
        "name": "论文速读",
        "category": "research",
        "prompt": "用结构化方式总结这篇材料：研究问题、方法、主要发现、局限、对我的启发。\n\n"
                  "材料：{content}",
        "variables": ["content"],
    },
    {
        "name": "研究计划",
        "category": "research",
        "prompt": "围绕课题「{topic}」拟一份研究计划：研究问题、假设、方法路线、里程碑、"
                  "可能风险与备选方案。",
        "variables": ["topic"],
    },
    # ── 客服 support ──
    {
        "name": "客服回复",
        "category": "support",
        "prompt": "你是 {product} 的客服。基于知识库回答用户问题，语气耐心专业；"
                  "若知识库无答案，引导用户提供更多信息或转人工。\n\n用户问题：{question}",
        "variables": ["product", "question"],
    },
    {
        "name": "FAQ 生成",
        "category": "support",
        "prompt": "基于知识库中关于 {topic} 的资料，整理一份 FAQ（10 条以内），"
                  "问题口语化、答案简洁准确。",
        "variables": ["topic"],
    },
    {
        "name": "工单分类",
        "category": "support",
        "prompt": "把下面的用户工单分类（如 bug/咨询/投诉/功能建议），判断优先级，"
                  "并给出建议的首次回复。\n\n工单：{content}",
        "variables": ["content"],
    },
    # ── 数据 data ──
    {
        "name": "数据洞察",
        "category": "data",
        "prompt": "下面是一组数据，请提炼 3-5 条关键洞察，指出异常值与可能原因，"
                  "并建议下一步分析方向。\n\n数据：{content}",
        "variables": ["content"],
    },
    {
        "name": "图表建议",
        "category": "data",
        "prompt": "我想展示「{intent}」这类信息，数据维度有：{dimensions}。"
                  "请推荐合适的图表类型并说明理由。",
        "variables": ["intent", "dimensions"],
    },
    # ── 智能体 agent ──
    {
        "name": "任务分解",
        "category": "agent",
        "prompt": "把目标「{goal}」分解成可执行的子任务清单，标注依赖关系与建议顺序，"
                  "每个子任务给出完成标准。",
        "variables": ["goal"],
    },
    {
        "name": "工具调用规划",
        "category": "agent",
        "prompt": "为完成「{goal}」，在可用工具 {tools} 中规划调用步骤，说明每步用什么工具、"
                  "输入什么、预期产出。",
        "variables": ["goal", "tools"],
    },
]


def main():
    ap = argparse.ArgumentParser(description="重建默认提示词模板（幂等）")
    ap.add_argument("--list", action="store_true", help="只列出当前模板，不写入")
    ap.add_argument("--dry", action="store_true", help="演练：只打印将新增的，不写库")
    args = ap.parse_args()

    from hashmm.api import database as db

    existing = db.list_templates()
    existing_names = {t.get("name") for t in existing}

    print(f"当前已有模板：{len(existing)} 个")
    for t in existing:
        print(f"  · [{t.get('category')}] {t.get('name')}")

    if args.list:
        return

    to_add = [t for t in DEFAULT_TEMPLATES if t["name"] not in existing_names]
    if not to_add:
        print("\n所有默认模板都已存在，无需补种。")
        return

    print(f"\n将补种 {to_add.__len__()} 个缺失的默认模板：")
    for t in to_add:
        print(f"  + [{t['category']}] {t['name']}")

    if args.dry:
        print("\n(演练模式，未写入)")
        return

    added = 0
    for t in to_add:
        try:
            db.create_template(t["name"], t["category"], t["prompt"],
                               variables=t["variables"], author="system")
            added += 1
        except Exception as e:
            print(f"  ! 跳过 {t['name']}：{type(e).__name__}: {e}")

    final = db.list_templates()
    print(f"\n完成：新增 {added} 个，现共 {len(final)} 个模板。刷新管理后台「模板」页即可看到。")


if __name__ == "__main__":
    main()
