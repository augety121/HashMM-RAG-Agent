---
name: 工作画布（work-canvas）
description: "把 agent 自己的工作——进度、调试、评审、对比、改了哪些代码——渲染成一张自包含 HTML 工作画布，在右栏边生成边直播、随 App 换肤、可就地把指令发回 agent。触发词：看板、画布、进度报告、给我个页面看、总结成网页、改了哪里、对比一下选型。只做工作快照，不做产品 UI / 网站（那是 design_prototype 的活）。"
triggers: [看板, 画布, 进度报告, 网页总结, 改了哪里, 变更, 对比选型, dashboard, canvas]
license: MIT
---

# 工作画布：操作手册

## 何时使用
用户要**看懂并处置** agent 的一段工作（长任务到哪了、这轮改了什么、几个方案怎么选），
而不是要一个交付给最终用户的产品页面。产物是**一个 .html 文件**：写盘后前端右栏
（ArtifactPanel 的 CanvasPreview，V215）会自动直播渲染成"工作画布"。

## 产物四型（先选型，再读对应配方）
| 用户想要… | 类型 | 读 |
|---|---|---|
| 长/乱任务到哪了、卡在哪、下一步 | 进度 / 状态报告 | `references/progress-report.md` |
| 审一份分析或方案、留痕、拍板 | 评审 / 决策备忘 | `references/review-decision.md` |
| 几个选项摆一起、挑一个赢家 | 对比 / 榜单 | `references/comparison.md` |
| 看清这轮**改了哪些文件、每一行** | 变更看板（diff） | `references/diff-review.md` |

混合诉求（如"进度 + 拍板"）以主类型打底，借用另一型的区块。

## 流程
1. **选型**并读对应 `references/<类型>.md`（区块顺序、组件配方、红线都在里面）。
2. **只写 body**：复制 `assets/starter.html`，填页头、「需要你拍板」、正文区块；
   保留 `[[PASTE canvas.css]]` / `[[PASTE canvas.js]]` 标记，本地图用 `[[IMG 路径]]`。
3. **装配单文件**：`python assets/assemble.py body.html 画布名.html`（内联+压缩资产、
   图片转 data URI；无 shell 时手工把两份资产粘进标记，模块全部自守卫）。
4. **交付**：`create_file` 把 .html 落盘 → 右栏即以"工作画布"直播；长任务**原地重生成
   同一个文件**保持链接稳定，全部数据齐了才定稿。回复里给路径，不整页复读内容。

## 宿主桥契约（窄腰，跨端只认这三条消息）
| 方向 | 消息 | 说明 |
|---|---|---|
| 画布 → 宿主 | `{type:"wc:ready"}` | 就绪握手，宿主收到即回推主题 |
| 宿主 → 画布 | `{type:"wc:theme", dark, accent, fontSize}` | 随 App 换肤（store 的既有三字段） |
| 画布 → 宿主 | `{type:"wc:prompt", text}` | 回喂：宿主裁 4000 字、写 `pendingPrompt` 回填输入框，**由用户确认发送** |

降级铁律：无宿主（用户双击独立打开）→ 回喂降级为"可粘贴文本"，主题用页内切换；
任一侧缺位另一侧零感知回退，绝不抛错。

## 主题
配色 token 与 `frontend-next/app/globals.css` **逐值同源**（主色 `#2563eb`、暗色走
`<html class="dark">`），宿主会自动同步——**不要自造配色、不要渐变标题、不要 emoji
图标**；克制、扁平、留白，主色只用于强调（对齐 PanelKit 设计语言）。

## 组件速查（V216 扩充）
基础：指标卡 `wc-grid/wc-stat` · 时间线 `wc-timeline` · 变更看板 `wc-changed`（文件点亮 + 逐行 diff）·
对比表 `wc-table` · 调整栏 `wc-tweaks` · 回喂 `wc-composer`。
V216 新增：进度条 `wc-progress` · 趋势角标 `wc-trend` · 迷你趋势图 `data-wc-spark`（真实序列才用）·
提示块 `wc-callout` · 折叠 `wc-details` · 键位 `wc-kbd` · **词级标注** `mark.wc-mark`（diff 行内圈出
改的词）· **热度徽章** `.cnt`+`--heat`（同文件多次改动，fanbox 机制）· 表头排序 `data-wc-sort`。
宿主侧（V216/V217）：右栏自带**设计工具条**（主色/字号画布级覆盖、「跟随 App」）与**版本历史**
（成品每次变化快照，v1…vN 切换；V217 起**跨会话落盘**——服务端 `.wc-versions/` 侧车，
previewFile 同链路）——画布无需自己实现，这些由 ArtifactPanel 原生提供。
用户入口（V217，画布怎么被触发）：① composer 工具条「画布」chip 插指令模板；
② 任何非画布产物右栏头部「画布讲解」按钮；③ 画布内 composer 回喂。三路都走
pendingPrompt 链路：只回填输入框、用户确认后发送。
协议（V222 共七条，画布无需自写、运行时已带）：画布→宿主 wc:ready / wc:prompt /
**wc:ask{text,context}**（选中即问，走轻量单轮 /api/canvas/ask，不进主对话）/
**wc:save{html}**（就地编辑整页回写原文件+自动进版本历史）；宿主→画布 wc:theme /
**wc:edit{on}** / **wc:flush**。

## 红线
- **单文件自包含**：内联 CSS/JS、内嵌图片、离线可开、零外链请求（装配器会拦截漏标记）。
- **署名 footer 强制**：写明哪个 agent + 哪个底座模型 + 日期。
- **「需要你拍板」只列真决策**：不编造、不凑数、不提议没必要的活；没有就写"无"。
- **编码颜色/字母必配图例**；不给读者看没解释的裸分数。
- **非破坏**：画布不改用户源文件；补丁/命令一律"只读展示 + 复制按钮"；回喂只回填
  输入框绝不代发；对外内容先脱敏并标注"发送前请人工复核"。
- **禁 localStorage / sessionStorage**（沙箱 iframe 访问即抛错）；画布内不出现任何
  token、密钥、内网地址（对齐 CSswitch 纪律：凭证永不进画布）。
