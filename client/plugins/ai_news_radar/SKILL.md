---
name: ai-news-radar
description: 收集并核验 AI 行业热点候选，优先一手来源，输出带日期、来源、置信度和待核验项的研究收件箱；不自动发布。
triggers: [AI 热点, AI 日报, 模型发布, 行业动态, news radar]
allowed-tools: [web_search, fetch_url, create_file]
network: true
filesystem: false
---

# AI 热点雷达

这是一个“候选雷达”，不是新闻自动发布器。每条候选必须保留原始 URL、发布者、发布日期和抓取时间；搜索摘要只能作为线索，不能直接当作事实。

## 工作契约

1. 先检索官方博客、产品更新、官方仓库 Release、论文原文等一手来源。
2. 打开高价值原文，核对事件日期、版本号、模型名称和关键事实。
3. 将结果分为“已核验 / 待核验 / 排除”，并写明排除或待核验原因。
4. 每条结果给出新鲜度、来源可信度、相关度和多来源佐证状态；不要把分数伪装成事实。
5. 没有足够来源时明确报告信息缺口，不用模型记忆补全。
6. 仅在用户明确要求后生成公众号稿件或进行外部发布，并再次请求确认。

## 输出字段

`title`、`event_date`、`published_at`、`source_url`、`publisher`、`status`、`why_it_matters`、`confidence`、`corroboration`、`unknowns`。
