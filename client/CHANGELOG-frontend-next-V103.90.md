# HashMM 真 UI（frontend-next）功能完善 — V103.90

> **重要背景**：此前 V103.90 的客户端功能误加在了 `desktop/app.html`（那只是「未连后端时的连接/兜底页」）。
> 客户端连上后端后，真正渲染的是 **`frontend-next`（Next.js 应用，由壳层服务器从静态导出提供）**。
> 本文件记录的是**改对地方**——在 `frontend-next` 真 UI 里补齐确实缺失的功能。
>
> **⚠️ 生效方式**：这些是 `frontend-next` 的源码改动。要在运行的客户端里看到，必须在你的机器上
> **重新构建** frontend-next（`cd frontend-next && npm run build` → 静态导出到 `out/` → 打包时复制到 `resources/webui`）。
> 沙箱无法构建（无 node_modules、无网络），但源码改动已随包；类型/语法已校验、纯逻辑已单测。

---

## 核查结论（先查后做，避免再次臆测）

对照大厂聊天/RAG 客户端逐项核查 `frontend-next`，**已存在**的不重复造：
- Markdown/代码高亮（`MessageRenderer.tsx`/`CodeBlock.tsx`）、消息复制/重新生成/编辑（`MsgBubble.tsx`）、
  附件拖放（`ChatArea.tsx`+`lib/api.ts`）、命令面板（`CommandPalette.tsx`，含跨会话搜索）、
  导出 MD/HTML/Jupyter（比之前 app.html 做的还多）、设置/主题/字号（`SettingsModal.tsx`）、
  会话历史与日期分组、消息相对时间戳（`lib/render.ts` 的 `relativeTime`，`MsgBubble` 已显示）。

**确实缺失 / 是占位假货**的，本次补齐：

## 一、会话内查找（Ctrl/⌘+F）

`ChatArea` 原本只有 Ctrl+N/D、Ctrl+Shift+C，没有当前对话内查找。新增：
- 纯逻辑 `lib/messageSearch.ts`（`findMatches` 大小写不敏感不重叠、`searchMessages` 逐条+扁平命中列表、`snippet` 居中预览、`stepHit` 环绕；剔除附件占位行 `📎`）。**22 项单测**。
- `ChatArea` 接线：Ctrl/⌘+F 或标题栏放大镜打开搜索栏，显示「当前/总数」，↑↓/回车在命中间跳转、滚动定位并高亮命中消息（命中落在折叠的加窗区会自动展开），Esc 关闭。

## 二、多标签会话

store 原本只有单个 `sid`，无法同时打开多个对话。新增：
- 纯逻辑 `lib/tabs.ts`（`addTab` 上限淘汰、`closeTab` 关当前后激活相邻、`adjacentTab` 环绕、`ensureTab`/`pruneTab`）。**22 项单测**。
- store：`openTabs` + `openTab`/`closeTab`/`syncTabs`；删会话连带摘标签；登出清空。
- `ChatArea`：任何地方设了 `sid` 都经 `syncTabs` 自动开标签（不用改所有调用点）；≥2 个对话时显示标签条，可切换/关闭。

## 三、界面多语言（i18n）

全应用写死中文，设置里的「语言」是**只有中文一项、onChange 空函数的占位假货**。新增真 i18n：
- 引擎 `lib/i18n.ts`：retrofit 策略——用「中文原文」本身作 key，词典做 中→英 映射（zh 返回原文、en 命中返回译文、缺失回退原文，不崩便于补）。含 `translate`/`interpolate`/`isLocale`/`LOCALES`/`EN_DICT`。**18 项单测**。
- `lib/useT.ts`：绑定 store 当前语言的 hook（单独成文件以保 `i18n.ts` 纯净可测）。
- store：`locale` + `setLocale` + localStorage 持久化 + 启动读取。
- 设置面板「语言」接通真 locale（中文/English，即时生效、持久化、启动应用）；`SettingRow`/`Select` 内部翻译，覆盖所有设置标签与选项。
- 侧边栏 `NavItem`/`NavSection`/「新对话」/空态接通翻译（一处改覆盖全部导航项）。
- 词典已含侧边栏导航、聊天区、设置等主文案；全量字符串覆盖为渐进工作（缺失自动回退中文，不影响使用）。

## 四、会话重命名

侧边栏只有固定/删除，无法改对话名（大厂标配）。后端已有 `updateConversation(convId,{title})` PATCH 接口，只缺 UI。新增：
- store：`renameSession`（乐观本地更新）。
- 侧边栏：双击标题或铅笔按钮进入内联编辑，输入框 Enter/失焦保存（`renameSession` + `updateConversation` API），Esc 取消。

## 五、回到底部按钮

流式生成时滚上去后无法快速回到底部（大厂标配）。新增：
- `ChatArea`：监听消息区滚动，离底部 >240px 时右下角浮现「回到底部」按钮，点击平滑滚到底。

---

## 验证

- 纯逻辑单测：`messageSearch` 22 + `tabs` 22 + `i18n` 18 = **62 项全过**（`frontend-next/tests-node/*.mjs`，本地 tsc 优先、沙箱用全局 tsc 兜底）。
- 类型/语法：改动的 `ChatArea.tsx`/`Sidebar.tsx`/`SettingsModal.tsx`/`store.ts`/`i18n.ts`/`useT.ts`/`tabs.ts`/`messageSearch.ts` 均无语法错误（沙箱无 node_modules，完整类型检查在你 `npm run build` 时跑）。
- 真实渲染/交互（搜索高亮、标签切换、改名、滚动按钮、语言切换）需在你的客户端构建后点一遍确认。

## 真机验收清单

1. **会话内查找**：进对话按 Ctrl/⌘+F，输关键词应高亮命中、显示计数，↑↓/回车跳转定位，Esc 关。
2. **多标签**：依次打开 2+ 个对话，顶部应出现标签条，可点击切换、✕ 关闭（关当前自动切相邻）。
3. **多语言**：设置→语言→English，侧边栏/设置文案应即时变英文，重启保持。
4. **会话重命名**：双击侧边栏对话标题（或铅笔图标），改名后回车应保存（刷新仍在）。
5. **回到底部**：长对话里向上滚，右下角应出现回到底部按钮，点击滚到底。
6. **权限审计深做**：左侧栏「权限审计」应能按风险/状态/关键词过滤、看成功率与平均耗时、点开每条看完整入参、导出 CSV。
7. **用户管理深做**：管理后台「用户管理」应能按角色 tab 过滤（带计数）、搜索用户名/显示名、按角色/名字/时间排序、看创建时间。

---

## 六、权限审计面板深做（左侧栏，对标大厂审计台）

原 `AuditView` 只平铺最近 80 条工具调用，无筛选/搜索/详情/导出。升级到大厂审计台深度：
- 纯逻辑 `lib/auditFilter.ts`：`filterAuditEntries`（按风险 all/high/normal、状态 all/ok/failed、关键词过滤工具名/执行者/租户）、`auditStats`（成功率/平均耗时/高危数）、`auditToCsv`（带表头、字段转义、ISO 时间）、`distinctTools`。**26 项单测**。
- `AuditView` 重建：分段筛选条（风险/状态）+ 关键词搜索；汇总指标卡新增「成功率」「平均耗时」（随筛选实时算）；每条工具调用可**点开展开**看执行者/租户/风险/结果与**完整入参 JSON**；一键**导出 CSV**（导出当前筛选结果）；显示「筛选数/总数」。拉取量从 80 提到 200。

## 七、用户管理深做（管理后台，对标 IAM 风格）

原 `UsersTab` 只有列表 + 增删 + 改角色，无搜索/筛选/排序/统计。升级：
- 纯逻辑 `lib/userFilter.ts`：`filterUsers`（关键词 + 角色过滤）、`sortUsers`（按角色/名字/创建时间，不改原数组）、`userRoleCounts`（角色分布）。**18 项单测**。
- `UsersTab`：顶部**角色 tab**（全部/管理员/用户/只读，各带实时计数，点击即筛选）+ **搜索框**（用户名/显示名）+ **排序下拉**（角色/名字/时间）；用户行补**创建时间**；空态区分「无用户」与「无符合条件」。

---

## 本轮（深做）验证

- 新纯逻辑单测：`auditFilter` 26 + `userFilter` 18，连同既有 `messageSearch` 22 + `tabs` 22 + `i18n` 18 = **106 项全过**。
- 语法：`AuditView.tsx`/`UsersTab.tsx`/`auditFilter.ts`/`userFilter.ts` 在项目真实 `jsx: preserve` 配置下无语法错误（完整类型检查在你 `npm run build` 时跑）。
- 注：早前用 `--jsx react` 校验会对部分写法误报，已确认改用项目配置的 `--jsx preserve` 后干净。

---

## 八、运行轨迹深做（左侧栏，对标可观测平台）

原 `RunsView` 有可展开步骤/复跑，但无汇总指标、无筛选。升级：
- 纯逻辑 `lib/runStats.ts`：`filterRuns`（按状态 all/ok/failed、停止理由、关键词查询过滤）、`runSummary`（运行数/成功率/平均轮数/总 token/平均耗时）、`runTokens`/`runFailed`/`distinctStopReasons`。**23 项单测**。
- `RunsView`：顶部**汇总指标卡**（运行数·成功率·平均轮数·总 token·平均耗时，随筛选实时算）+ **搜索框**（查询文本）+ **状态分段**（全部/成功/失败）+ **停止理由下拉**（自动列出出现过的理由）；无结果空态。复跑/展开步骤保留。

## 九、工具管理深做（管理后台，对标功能开关台）

原 `ToolsTab` 按类别分组 + 单个开关，无搜索/筛选/排序/批量。升级：
- 纯逻辑 `lib/toolFilter.ts`：`filterTools`（关键词 + 启用状态过滤）、`sortTools`（按类别/用量/名字，不改原数组）、`toolSummary`（总数/启用/禁用/累计调用/最常用工具）、`toolsInCategory`。**22 项单测**。
- `ToolsTab`：头部**用量汇总**（启用·禁用·累计调用·最常用工具）+ **搜索框**（名字/描述）+ **状态分段**（全部/启用/禁用）+ **排序下拉**（类别/用量/名字）；每个类别加**「全部启用/全部禁用」批量开关**与类别计数；无结果空态。

---

## 本轮（继续深做）验证

- 新纯逻辑单测：`runStats` 23 + `toolFilter` 22，连同前述共 **151 项全过**（`messageSearch` 22 + `tabs` 22 + `i18n` 18 + `auditFilter` 26 + `userFilter` 18 + `runStats` 23 + `toolFilter` 22）。
- 语法：`RunsView.tsx`/`ToolsTab.tsx`/`runStats.ts`/`toolFilter.ts` 在 `jsx: preserve` 下无语法错误。
- 已核查 `UsageView`/`DiscoveryView`/`QualityView`/`KBsTab`：DiscoveryView（一键动作闭环）与 QualityView（指标卡/SLO/明细/一键改善）本就较丰富；UsageView 读本机 agent 数据、KBsTab 库数通常少，深做空间有限。故本轮聚焦 RunsView 与 ToolsTab 两个确有缺口者。

## 真机验收补充

8. **运行轨迹深做**：左侧栏「运行轨迹」（需 `HASHMM_AGENT_TRACE=1` 且有运行记录）应显示汇总指标卡，可按成功/失败、停止理由、关键词筛选。
9. **工具管理深做**：管理后台「工具」应能搜索、按启用/禁用筛选、按用量/类别/名字排序、每个类别一键全开/全关。

---

## 十、用量面板可视化（左侧栏，由扁平数字升级为图形）

原 `UsageView`（54 行）只是几行 token 数字。升级为可视化：
- 纯逻辑 `lib/usageStats.ts`：`relBars`（一组数值归一为相对条宽，非零至少给最小可见宽）、`pctSplit`（两数占比、和保证为 100）、`sumTokens`（跨来源合计、忽略非数值）、`fmtCompact`（1.2k/1.2M 紧凑格式）。**20 项单测**。
- `UsageView`：顶部**合计指标卡**（近 7 天 Claude+Codex 合计 token、Claude 今日消息、Codex 5h 窗口已用）；Claude Code 三周期（5h/今天/7天）改为**相对对比条**直观看趋势；Codex 增加**输入/输出占比条**与配额已用进度条。

## 十一、主动发现优先级筛选（左侧栏）

原 `DiscoveryView` 平铺所有发现项。升级：
- 纯逻辑 `lib/discoveryFilter.ts`：`filterFindings`（按优先级）、`priorityCounts`（高/中/低分级计数）、`sortByPriority`（高优先级置顶、稳定排序）、`actionableCount`（可一键处理条数）、`normPriority`/`isActionable`。**22 项单测**。
- `DiscoveryView`：顶部**优先级筛选 tab**（全部/高/中/低，各带计数）+ 扫描行显示「N 项可一键处理」+ 列表按优先级置顶。
- **顺手修了个隐患**：原动作状态用列表索引 `i` 作 key，一旦筛选/排序索引变化会错位；改为按「标题+动作类型」稳定 key，筛选后动作状态不再串。

## 十二、知识库搜索（管理后台）

原 `KBsTab` 仅列表。升级：标题显示库总数；超过 3 个时出现**搜索框**（按名字/描述）；列表按名字排序；空态区分「无库」与「无结果」。（知识库通常不多，故为轻量增强，未过度造工具。）

> **关于 QualityView**：上轮已核查，它本就有核心指标卡、SLO 横幅、一键改善（建社区/重建索引）、延迟/成本/路由/图谱四类明细分解，属于已经较深的面板。在不引入新后端指标的前提下，强行加深只会是堆砌，故如实保留不动。

---

## 本轮（第三轮深做）验证

- 新纯逻辑单测：`discoveryFilter` 22 + `usageStats` 20，连同前述共 **193 项全过**（9 个测试文件：messageSearch 22 / tabs 22 / i18n 18 / auditFilter 26 / userFilter 18 / runStats 23 / toolFilter 22 / discoveryFilter 22 / usageStats 20）。
- 语法：`UsageView.tsx`/`DiscoveryView.tsx`/`KBsTab.tsx`/`usageStats.ts`/`discoveryFilter.ts` 在 `jsx: preserve` 下无语法错误。

## 真机验收补充

10. **用量可视化**：左侧栏「用量」应显示合计指标卡、Claude 三周期对比条、Codex 输入/输出占比条（需本机跑过 claude/codex 才有数据）。
11. **主动发现筛选**：左侧栏「主动发现」有发现项时，顶部应出现优先级筛选 tab（带计数），高优先级项排在前。
12. **知识库搜索**：管理后台「知识库」超过 3 个时出现搜索框，可按名字筛选。

---

## 十三、记忆中心深做（左侧栏）

原 `MemoryView` 加载 200 条记忆只按类别分组平铺，无搜索/筛选/排序。升级：
- 纯逻辑 `lib/memoryFilter.ts`：`filterGroups`（按关键词+类别过滤、空组移除）、`sortGroupItems`（组内按置信度/最近使用排序）、`memoryStats`（总数/平均置信度/类别数）、`memoryCategories`/`countItems`。**20 项单测**。
- `MemoryView`：**类别筛选 tab**（全部+各类别带计数）+ **搜索框**（标签/内容）+ **排序下拉**（默认/置信度/最近使用）+ 平均置信度徽章；空态区分「无记忆」与「无结果」。

## 十四、模型路由深做（左侧栏）

原 `ModelRoutingView` 逐角色配置 + 实测，但无分布概览、无批量。升级：
- 纯逻辑 `lib/routingStats.ts`：`routingCounts`（实际走本地/云端的任务数、设为自动的数）、`filterTasks`（按 local/cloud/auto 过滤）、`setAll`（批量设同值）、`currentSetting`/`effectiveBackend`/`hasOverride`。**21 项单测**。
- `ModelRoutingView`：**分布筛选条**（全部/本地/云端/自动，各带实时计数，点击即筛选）+ **批量按钮**（全部本地/全部云端/全部自动）；列表按筛选显示。

## 十五、自我进化深做（左侧栏）

原 `EvolutionView` 技能与经验都只平铺。升级：
- 纯逻辑 `lib/skillStats.ts`：`sortSkills`（按质量/用量）、`skillsSummary`（平均质量/累计调用）、`classifyOutcome`/`filterEpisodes`/`episodeOutcomeCounts`（经验按成功/失败/未知过滤与计数）。**25 项单测**。
- `EvolutionView`：技能区加**排序下拉**（默认/质量/用量）+ 汇总（平均质量·累计用量）；经验区加**结果筛选 tab**（全部/成功/失败/未知带计数）。

## 十六、模型配置深做（管理后台）

原 `ModelsTab` 底部模型列表只平铺。升级：
- 纯逻辑 `lib/modelFilter.ts`：`filterModels`（按关键词+服务商过滤）、`distinctProviders`、`modelStats`（总数/服务商数/是否已设默认）、`countByProvider`。**18 项单测**。
- `ModelsTab`：标题显示「N 个 · M 家服务商 · 是否已设默认」；配置超过 2 个时出现**搜索框**（配置名/模型名/URL）+ **服务商筛选条**（各带计数）；空态区分「无配置」与「无结果」。

---

## 本轮（第四轮深做）验证

- 新纯逻辑单测：`memoryFilter` 20 + `routingStats` 21 + `skillStats` 25 + `modelFilter` 18，连同前述共 **13 个测试文件、277 项断言全过**。
- 语法：`MemoryView.tsx`/`ModelRoutingView.tsx`/`EvolutionView.tsx`/`ModelsTab.tsx` + 四个新 lib 在 `jsx: preserve` 下无语法错误。

## 真机验收补充

13. **记忆中心**：左侧栏「记忆中心」有记忆时，应能按类别 tab 筛选、搜索、按置信度/最近使用排序。
14. **模型路由**：左侧栏「模型路由」应显示本地/云端/自动分布计数，可按其筛选，可一键全部本地/云端/自动。
15. **自我进化**：左侧栏「自我进化」技能可按质量/用量排序，经验可按成功/失败/未知筛选。
16. **模型配置**：管理后台「模型」配置较多时出现搜索框与服务商筛选条。

---

## 十七、技能管理深做（管理后台）

原 `SkillsTab` auto/manual 双 tab + 增删 + 反馈，但两个列表纯平铺、无搜索/排序。升级：
- 复用并扩展 `lib/skillStats.ts`：新增 `filterSkills`（按名字/描述/触发词过滤），复用既有 `sortSkills`。skillStats 单测增至 **31 项**。
- `SkillsTab`：加**搜索框**（名字/描述/触发词，作用于当前 tab）；auto tab 加**排序下拉**（默认/质量分/用量）；筛选无结果有独立提示。

## 十八、提示词模板深做（管理后台）

原 `TemplatesTab` 已有搜索 + 分类筛选 + 增删改，但**分类筛选按钮写死 4 个**（general/code/analysis/document），而系统定义了 9 个分类（写作/研究/客服/数据/智能体筛不到）；且无排序。升级：
- 纯逻辑 `lib/templateSort.ts`：`sortTemplates`（按用量/名字/创建时间）。**9 项单测**。
- `TemplatesTab`：分类筛选改为**显示全部 9 个已知分类**（取自 CATEGORY_LABELS，可换行）+ 加**排序下拉**（默认/用量/名字/最新）。

---

## 关于 DocsTab / KGTab / SettingsTab（如实说明，未硬加深）

按"先读后做、不为凑数堆砌"的原则，这三个核查后认为本身已足够深或深做空间有限，故**保留不动**：

- **DocsTab（339 行）**：已是全功能文档管理台——文件名搜索、按状态 tab 过滤（带各状态计数）、排序、文件夹分组、批量重新解析、单篇重新解析。继续加只会重复已有能力。
- **KGTab（231 行）**：已是丰富的知识图谱仪表盘——实体/关系/密度/社区统计卡、实体类型分布（已按数量排序的条形）、高连接度实体 Top 15、图谱可视化。Top 实体本就是精选子集，再加筛选边际收益很小。
- **SettingsTab（164 行）**：聚焦的"搜索/联网后端"配置面板，条目只有少数几个，且已带"测试搜索"实测功能。条目少，搜索/排序无意义。

> 这是诚实的工程判断：到这个阶段，多数面板已有合适深度，继续无差别加筛选排序属于边际递减甚至堆砌。需要时仍可针对具体新需求再做。

---

## 本轮（第五轮深做）验证

- 新增/扩展纯逻辑单测：`skillStats` 增至 31（+filterSkills 6）、新增 `templateSort` 9，连同前述共 **14 个测试文件、292 项断言全过**。
- 语法：`SkillsTab.tsx`/`TemplatesTab.tsx` + `templateSort.ts` 在 `jsx: preserve` 下无语法错误。

## 真机验收补充

17. **技能管理**：管理后台「技能」auto/manual 两 tab 都应能搜索；auto tab 可按质量分/用量排序。
18. **提示词模板**：管理后台「模板」分类筛选应能看到全部 9 个分类（含写作/研究/客服/数据/智能体），并可按用量/名字/最新排序。

---

## 🔧 构建修复：AuditView 在真机 `next build`(SWC) 编译失败

**现象**：你真机 `npm run build` 报 `AuditView.tsx:125 x Unexpected character '：'` / `Expected '</', got 'style'`，前端构建中断、出不来 `out/index.html`。

**根因**：AuditView 展开详情里写了「中文文本紧跟嵌套 `<span>`」——`<span>风险：<span style={...}>{值}</span></span>`。Next 用的 **SWC** 编译器（不是 tsc）在这种结构下，会把全角冒号 `：` 当成 JSX 文本的非法终止符而报错。（之前我用 `tsc --jsx preserve` 校验通过，但 tsc 与 SWC 的 JSX 词法不同，没覆盖到——这是我校验方式的盲区。）

**修复**：把该块四个标签（执行者/租户/风险/结果）从裸中文文本改为显式 JSX 字符串表达式 `{"执行者："}` 等，并把内联 style 里的三元颜色预计算成变量；条件渲染 `&&` 改为 `? : null`。彻底消除「中文紧贴开标签」的写法。

**排查范围**：扫描了我全部改动文件 + 整个 `components/` 目录，确认 `中文：<span>` 这类写法**仅 AuditView 一处**，其余无（现存代码库本就不这么写）。同时确认 `style` 里 `X === "Y" ? a : b` 三元在 PanelKit 等现存文件也在用、能正常 build，不是问题所在。

> 诚实提醒：沙箱里没有 SWC/esbuild、也没网络，无法本地跑真实 `next build`。此修复已针对确认的根因、并扫掉了同类写法；但若你重新 build 后报到别的文件，请把红字发我，我按同样方式继续定位（大概率仍是某处「中文紧贴嵌套标签」或其他 SWC 与 tsc 的 JSX 词法差异）。

---

## 🔧 构建修复 #2：ModelsTab 类型检查失败（`next build` 类型阶段）

**现象**：前端**编译已通过**（上次 AuditView 修复生效），但卡在类型检查：`ModelsTab.tsx:23 Type error: Conversion of type 'ModelConfig[]' to type 'ModelRec[]' ... 'is_default' ... number 不能比较 boolean`。

**根因**：`ModelConfig.is_default` 是 **number**（0/1），而我 `ModelRec.is_default` 写成了 **boolean**，类型不重叠、`as` 转换被拒。我本地用 `--noResolve` 校验跳过了真实类型解析，且 `useState` 无 react 类型时变 `any`、`any as X` 永不报错，所以没测出来——这是我校验方式的第二个盲区。

**修复**：
1. `modelFilter.ts`：`ModelRec.is_default` 改为 `boolean | number`；`modelStats` 的 `hasDefault` 判断从 `=== true` 改为真值 `!!m.is_default`（这同时修正了一个逻辑 bug——对 number 0/1 用 `=== true` 永远是 false）。
2. `skillStats.ts`：移除 `SkillRec` 的索引签名 `[k: string]: unknown`。

**这次换了更可靠的验证方式**：搭了一个能**解析真实类型**的检查 harness（重声明各组件本地类型 + 导入真实 `ModelConfig` 与各 util 的 Rec 类型，复现全部 `as` 转换），用 `@/` 路径解析、`strict:false`（与项目一致）做类型检查——不依赖 react。这个 harness **多揪出一个我本会漏的错误**：SkillsTab 把无索引签名的 `EvolutionSkill`/`LegacySkill` 转 `SkillRec`（有索引签名）失败。修掉后，**全部 11 处 `as` 转换、覆盖所有 util 的调用，类型检查通过**。harness 验证后已删除。

> 仍然诚实：沙箱无 react 类型，无法跑**完整**的组件级类型检查（JSX/hooks）。但已用真实类型把"跨类型转换"这一最易出错、也正是本次报错的面全部验过；组件内部用 `any` state 的字段访问不报错、用具名 state 的访问的都是存在字段、PanelKit 的 `StatCard.value` 是 `ReactNode`（数字/字符串都合法）均已确认。若重新 build 仍报到别处，请发我红字继续。

---

## 🔧 运行时修复 #3：收起左边栏报 React error #300（Hooks 规则违反）

**现象**：build 完整成功、exe 正常打出，但运行时点「收起左边栏」整个界面崩，报 `Minified React error #300`（"Rendered fewer hooks than expected... accidental early return statement"）。

**根因（我的 bug）**：`Sidebar.tsx` 里有一句既有的早返回 `if (!sbOpen) return null;`。我之前加会话重命名功能时，把两个 hook（`useState(editId)`、`useState(editText)`）**写在了这句早返回的后面**。侧栏展开时 `sbOpen=true`，两个 hook 正常执行；一点收起，`sbOpen` 变 false → 早返回 `return null` → 这两个 useState 被跳过 → 本次渲染的 hook 数量比上次少 → React 抛 #300。这是 Rules of Hooks 违反：所有 hook 必须无条件、在任何 return 之前调用。

**修复**：把这两个 `useState` 上移到 `if (!sbOpen) return null;` **之前**；`startRename`/`saveRename` 是普通函数（非 hook），留在原处即可（闭包正常引用上移后的状态）。改完后：Sidebar 末个 hook 在早返回前一行，早返回之后无任何 hook 调用。

**排查范围**：扫描了**全部 16 个改动组件**的「组件体级早返回 vs hook 位置」。确认其余组件的早返回都在 helper 函数（`stopReasonInfo`/`priorityInfo`/`outcomeStyle`/`fileIcon`，返回的是对象不是组件级返回）或事件处理器（`if(!id)return`）里，组件体内的 hook 全部在任何早返回之前、且无条件调用——**仅 Sidebar 一处有此问题，已修**。

> 说明：Hooks 顺序 bug 是可静态判定的（逐行核对 hook 与早返回的先后即可），不依赖运行时。已逐一核对全部改动组件。

---

## 🔧 客户端问题 #4：质量评测报「HTTP 502」（实际后端已跑完）

**现象**：管理后台「质量评测」点运行评测，客户端弹「评测失败：Error: HTTP 502」。但容器后端日志显示评测**实际跑完且返回 200**：
`Enhanced eval: 100/100 passed ... POST /api/admin/eval/run-enhanced → 200 (819244ms)`——耗时 **819 秒 ≈ 13.6 分钟**。

**根因（不是代码 bug，是长请求撞网关超时）**：`run-enhanced` 是同步跑（100 用例 × 完整 RAG × LLM 评审），要十几分钟。客户端到服务器之间的网关（反向代理 / AutoDL 端口转发）有超时，单个请求拖太久它就先返回 502，后端却仍在跑、最后跑完并**把结果存进了「运行记录」**（`run_all_enhanced` 末尾 `save_run`）。所以客户端「失败」是假象，评测其实成功了。

**修复（前端，不动后端）**：`EvalPanel` 的运行逻辑从「死等那条会被 502 掐断的响应」改为**发起 + 轮询**：
1. 发起前先记下已有 run_id；
2. 发起评测（其响应若被网关 502 则忽略）；
3. 每 12 秒轮询 `/eval/runs`，新记录一出现即判定完成、显示通过率/平均分，并提示「结果已存入运行记录」；
4. 全程显示「评测进行中…已 X 分 Y 秒」，并告知可关窗口、完成后在运行记录查看；30 分钟兜底超时。
若后端比网关快（小用例集），直连响应也会被直接采用、显示完整明细。

## 🔧 客户端问题 #4 同类排查：批量重新解析

按「有没有其他这种问题」排查了所有可能长耗时的前端→后端调用：
- **批量重新解析**（DocsTab `/api/admin/docs/batch-reparse`，UI 自己标注"需要较长时间")：**同款 502 风险**。已改容错——网关返回非 2xx 或抛错时，不再显示「请求失败」，而是提示「网关已断开，但服务器仍在后台解析，请稍后刷新查看文档状态」，并 30 秒后自动再刷新一次。
- **KG 一键构建**（`/api/kg/build-from-chunks`）：UI 标注且日志证实「通常 3 秒内、不需要 LLM」——快，无 502 风险，不改。
- **索引重建**（`rebuildIndex`）：后端本就是**异步触发**（提示"已触发后台重建，稍后生效"，立即返回）——无 502 风险，不改。
- **重建社区**（`rebuildCommunities`）：533 实体上秒级——无风险，不改。
- 单篇文档重解析：一般较快、风险低，本次未改（如遇大 PDF 超时再按同法处理）。

> 说明：根因是网关对单个长请求的超时，前端无法让网关为一条请求多等十几分钟；真正稳的做法就是「发起 + 轮询持久化结果」（评测已这么改）。若希望彻底统一，可由后端把这类长任务都改成「返回任务 ID + 轮询状态」的异步作业模式（需后端改动）。

---

## 🐛 桌面问题 #5：Computer Use 写文件报 `path ... Received undefined`（根因：tool_call 被截断）

**现象**：「电脑操作」让 AI 写一个完整文件（如 400 行 Transformer），AI 反复 `write_file {}`（空参数）报 `The "path" argument must be of type string ... Received undefined`，连续 3 次被打转检测停掉。但同一会话里 `create_file`（走别的机制）能成。

**根因（定位到后端一行）**：生产「电脑操作」走 `lib/cu.ts → 后端 /api/llm/tools → run_tools_chat`。该函数里 `MAX_TOKENS = 2048` 写死。写一个 13377 字的文件时，整份内容在 tool_call 的 `arguments`(JSON 字符串)里，远超 2048 token → 模型响应被**截断成残缺 JSON** → 前端 `JSON.parse(arguments)` 失败 → 回退成 `args={}` → `write_file` 收到 `undefined` 的 path。`create_file` 不受影响是因为它不走这条 tools 通道。

**修复（多层）**：
1. **后端根因**（`hashmm/api/llm_tools_core.py`）：`MAX_TOKENS` 2048→8192，且**优先采用模型自身配置的 max_tokens**（管理后台通常设了 16384）。这样大文件的 tool_call 参数不再被截断。已用 mock 验证：模型配 16384→用 16384；没配→回退 8192；脏值→回退 8192。既有 `test_v92_llm_raw` 3/3 仍通过。
2. **另一条 Agent 循环**（`desktop/main.js` chatToolsOnce，供 Cockpit 的 cu:runLoop 用）：同样补上 max_tokens（默认 8192，可经 cuMaxTokens 配）。
3. **执行处兜底**（`desktop/main.js` cuExecOnce 的 write_file）：校验 path——为空时返回**清晰错误**（提示「请提供路径，或只给文件名存到默认目录，并把完整 content 一起传」）而非晦涩的 Node 报错；相对路径/裸文件名自动落到默认目录；自动建父目录。

## 🪟 桌面问题 #6：点 X 直接关窗（后台还在跑）→ 改为弹窗让用户选

**现象**：点右上角 X，窗口关了但程序仍在后台（托盘）运行；用户希望像大厂软件那样**点 X 弹窗让用户选「最小化到托盘 / 退出程序」**。

**修复**：
- `desktop/services/window-service.js` 新增纯函数 `decideCloseAction(isQuitting, trayOnClose)` → `"tray"|"quit"|"ask"`（三态，可单测，已验证 5/5）。`trayOnClose` 即用户「记住的选择」：true=最小化、false=退出、未设=每次问。
- `desktop/main.js` close 处理重写：未记住选择时**弹原生对话框**「最小化到托盘 / 退出程序 / 取消」，带「记住我的选择，不再询问」勾选；勾了就存进 trayOnClose，下次不再问。
- 退出流程安全：`before-quit` 已置 isQuitting=true，故托盘退出 / Cmd+Q / 系统关机 / 更新重启都**不会被弹窗拦截**，对话框只在用户**直接点 X** 时出现。

## 📁 桌面问题 #7：Computer Use 写文件可选位置 + 软件内设默认位置（原来没有）

**现象**：AI 写文件默认落 C 盘；用户希望**写时能选位置**，且**软件里能设默认保存位置**（原先缺失）。

**修复**：
- 新增配置 `cuFileDir` + 辅助函数 `cuDefaultDir()`（优先用户配置，否则系统下载文件夹，再兜底主目录）。write_file 的相对路径/裸文件名都落到这里。
- **写时可选位置**：write_file 的确认对话框新增「选择位置…」按钮 → 打开系统保存对话框，用户挑好路径后覆盖写入位置（确认框还会预览「将保存到 …」）。
- **软件内设默认**：新增 IPC `cu:getFileDir/setFileDir/pickFileDir`（preload 同步暴露 hashmmCU）；「电脑操作记录」面板的安全级别下方新增**「默认文件保存位置」**：显示当前目录 +「选择…」（文件夹选择框）+「默认」（恢复系统下载文件夹）。

> 诚实边界：以上桌面端改动（main.js / preload.js / window-service.js）与后端（llm_tools_core.py）**我无法在沙箱里跑 Electron/真实模型做运行时验证**（无 Electron/显示/GPU）。已做：全部 `node --check`/Python 语法通过、`decideCloseAction` 纯逻辑 5/5、后端 max_tokens 取值 mock 验证、desktop 既有测试（agent-loop 7、computeruse 7、cu_guard 12）+ 后端 llm_raw 3 全过。Electron 原生对话框/保存框/IPC 的实际行为需你在真机验证。
>
> ⚠️ 生效方式（多组件）：① **后端**改了 `llm_tools_core.py`——需在 AutoDL 容器重启后端；② **桌面端**改了 frontend-next + desktop/*.js——需在 Windows 机器 `cd frontend-next && npm run build` 后用 electron-builder 重新打包。

---

## 🪟 桌面问题 #8：退出后仍在后台、双击 exe 打不开 —— second-instance 没显示隐藏窗口

**现象**：软件最小化到托盘（或自以为退出）后，双击 exe 没反应、打不开；任务管理器里还残留 HashMM 进程。

**根因**：单实例锁的 `second-instance` 处理只在 `mainWindow.isMinimized()` 时 restore，但「最小化到托盘」是 `hide()` —— 此时 `isVisible()=false` 但 `isMinimized()=false`，原代码只 `focus()` 不会把隐藏窗口显示出来 → 双击 exe 撞单实例锁、触发 second-instance、却没把窗口拉出来 → 看着像「打不开」。

**修复**（`desktop/main.js`）：
- `second-instance`：先 `isMinimized()→restore`，再 **`!isVisible()→show()`**（关键），最后 `focus()`；若 `mainWindow` 已销毁但进程还在 → `boot()` 重建主窗口。
- `before-quit`：清理（托盘/终端/watcher/后端/壳服务）后加 **1.5s 强制退出兜底**（`app.exit(0)`），防止某个未关闭 handle 卡住导致「已退出但后台残留进程」。

## 🔗 桌面问题 #9：用户协议/隐私页「← 返回」会重开一个软件实例

**现象**：协议/隐私页是从主界面 `window.open("/terms")` 弹出的独立窗口；页脚「← 返回 HashMM-RAG」是 `href="/"`，点了会把那个小窗导航到 `/` → 在小窗里重新加载整个 app，看着像又开了一个软件（图5）。

**修复**：新增 `components/BackLink.tsx`（client 组件，server 页可引入）：点返回 = **`window.close()` 关闭本协议窗口**回主界面，而不是导航到 `/`；极少数环境 `window.close` 无效时 150ms 后兜底回首页。`app/terms/page.tsx` 与 `app/privacy/page.tsx` 的返回链接都换成 `<BackLink/>`（隐私页的「服务条款」链接保留原样）。

## 🎨 桌面问题 #10：关闭弹窗是系统蓝框 → 改为主界面同风格的 app 内弹窗

**现象**：点 X 弹出的是 Electron 系统原生蓝框（图4），与软件整体 UI 风格不一致。

**修复**（前后端联动）：
- `desktop/main.js`：close 处理不再用 `dialog.showMessageBox`，而是 `webContents.send("app:confirmClose")` 通知前端；新增**模块级** `ipcMain.on("app:closeChoice")` 接收用户选择（tray/quit/cancel + 记住）后执行（hide / app.quit / 保持打开）。决策三态仍由 WindowService 纯函数 `decideCloseAction` 判定（5/5 单测）。
- `desktop/preload.js`：`hashmmDesktop` 加 `onConfirmClose` / `closeChoice`。
- `frontend-next/components/CloseConfirmModal.tsx`（新）：与 WelcomeModal 同风格的弹窗（`var(--bg-primary)`/`var(--accent)`/圆角/遮罩模糊），「最小化到托盘 / 退出程序 / 取消」+「记住我的选择」勾选；挂载在 `App.tsx`。
- 退出安全：`before-quit` 仍置 isQuitting，托盘退出 / Cmd+Q / 系统关机都不会触发该弹窗，只在用户**直接点 X** 时出现。

## 💰 桌面功能 #11：cc-switch 式 API 余额 + Claude/Codex 用量与重置面板

**需求**：参考 cc-switch（farion1231/cc-switch v3.13 的「配额/余额内联显示」），在「用量」页看到 **API 剩余金额**、Claude 5 小时/每周用量、Codex 重置窗口、API 使用情况。

**实现**：
- **API 余额**（新）：`hashmm/api/routes/admin.py` 加 `GET /api/admin/provider-balance`（require_admin，密钥留服务端不外泄）。当前默认模型属 DeepSeek 时调 `GET https://api.deepseek.com/user/balance`（已 web 核实接口与返回结构），返回 `total_balance/granted_balance/topped_up_balance/currency/is_available`；非 DeepSeek 返回 `supported:false`，前端提示「暂不支持」。`frontend-next/lib/api.ts` 加 `getProviderBalance()`。
- **「用量」页**（`components/desktop/UsageView.tsx`，复用已有 `agentUsage` 基础设施）：顶部新增 **「API 余额」卡片**（总余额大字 + 充值/赠送余额 + 币种 + 余额不足提示 + 刷新按钮）；**Codex 卡片**新增 5 小时窗口 / 每周窗口的「X 小时 Y 分后重置」（取自 `rate_limits.primary/secondary` 的 `resets_in_seconds`）。Claude Code 的近 5 小时/今天/近 7 天 token 用量条形原本就有。

> 诚实边界：① Claude Code 的「精确重置时间」本地 `~/.claude` 会话文件里没有（只有滚动窗口用量），所以 Claude 侧展示的是用量窗口而非倒计时；Codex 的重置取自其会话里的 `rate_limits`，确有该字段时才显示。② DeepSeek 余额接口已 web 核实，但**真机实际调用未测**（沙箱无网络、无该 API Key）。③ 完整 cc-switch 能力（多提供商切换、OAuth、各家余额）是更大的工程，本版聚焦你在用的 DeepSeek 余额 + 已有的 Claude/Codex 用量。④ 所有桌面端（main.js/preload.js）与 Electron 行为我**无法在沙箱跑运行时验证**，已做全部语法校验 + decideCloseAction 5/5 + desktop 既有测试全过 + 后端 admin.py 语法通过。

> ⚠️ 生效方式（多组件）：① **后端**改了 `admin.py`——需在 AutoDL 容器重启后端；② **桌面端**改了 frontend-next + desktop/*.js——需在 Windows 机器 `cd frontend-next && npm run build` 后用 electron-builder 重新打包。

---

## 🎨 桌面设计 #12：系统 11 个页面 UI 重做 —— 升级设计系统（白卡浮于画布 · 渐变页头 · 卡片层次）

**问题**：侧栏「系统」下 11 个页（用量/后端连接/远程桌面/记忆中心/自我进化/质量看板/权限审计/定时任务/模型路由/运行轨迹/主动发现）观感"半成品"——根因在设计 token：卡片用 `--bg-secondary(#f8f9fa)` 贴在白底 `--bg-primary(#fff)` 上、阴影仅 0.04 透明度，卡片几乎与背景融为一体，整体发灰、扁平、没层次。

**方案（最高杠杆：升级共享设计系统，一次性拉高所有页）**：11 个页里 9 个已用统一设计系统 `ui/PanelKit`，2 个（后端连接/远程）是裸样式。
- **新增明/暗双模层级 token**（`app/globals.css`，纯加法不破坏现有）：`--canvas`（浅灰画布）、`--surface`（纯白卡片，暗色下反而提亮）、`--surface-2`（卡内浅井）、`--hairline`（极细分隔线）、`--accent-grad`（图标渐变）、`--shadow-card`/`--shadow-pop`（卡片/悬停阴影）。明暗都保证**卡片"浮"在画布上**，清晰层次取代发灰。
- **升级 PanelKit 基元**（改一处，9 个页全受益）：① PanelShell 用 `--canvas` 画布 + 更舒展间距；② PageHeader 换**渐变图标方章（白图标）+ 更大标题 + 底部细分隔线**——每页都有了"大厂页头";③ Card 改白卡 + 大厂式 hover（抬升 2px + 阴影增强 + 描边微染主色，见新增 `.pk-card`/`.pk-card-i`）;④ StatCard 加图标小方章 + 更大更紧致的数字;⑤ 空/加载/错误态更精致。
- **裸样式页接入**：`BackendView`（重写为 PanelShell+PageHeader+Card，逻辑逐字保留）、`RemoteView`（局部 Card 升级 + 外壳换 PanelShell + 加 PageHeader + 嵌套灰井改 `--surface-2`）、`SemanticCard`（裸灰卡→白卡）。

**顺手修**：图2「API 余额查询失败」的提示改得更准——之前文案误导（你确实是管理员+DeepSeek），最可能是**后端没重启**（前端已更新但 AutoDL 后端还没加载 `/api/admin/provider-balance` 新接口 → 404）。新文案明确提示"若刚更新版本请确认后端也已重启"。

> 诚实边界：这是纯视觉升级，**我无法在沙箱里渲染预览**（无 `next build`/浏览器），已做全部 TSX 语法校验（PanelKit + 11 个页 + SemanticCard 全过）。配色/层次/间距是按成熟设计规律（Linear/Vercel/Stripe 式"白卡+画布+克制阴影"）来定的，真实观感需你 `npm run build` 后在桌面端看；若某处想再调（圆角/留白/字号/主色浓度），告诉我具体哪里，我精修。
>
> ⚠️ 生效方式：改的全是 frontend-next（含 globals.css + PanelKit + 各页）——在 Windows 机器 `cd frontend-next && npm run build` 后用 electron-builder 重新打包即可（本批无后端改动；但 API 余额那条仍需 AutoDL 后端已重启过上版的 admin.py）。

---

## 🐛+🎨 桌面 #13：修「加了刷新没东西」根因 + 布局（卡片撑满行 · 空状态做厚）

**① 记忆中心「教它记住」加了没反应 —— 根因找到并修复（不是后端、不是 db）**
逐层排查：db 层在沙箱直测 `save_user_memory`+`get_user_memories` 完全正常（存→取拿得到，0→1→2 条正确）；后端 `add_memory`/`list_memory` 都用 `user["uid"]`，一致；表结构 `last_used`/`confidence` 都有 DEFAULT，INSERT 不会失败。**真正的 bug 在前端 `lib/api.ts`：`_fetch` 对 GET 有 2 秒去重缓存（`DEDUP_TTL=2000`）。** `listMemory` 是 GET——你填表（>2 秒）后点「记住」→`addMemory`(POST 成功)→`load()` 重新拉 `listMemory`(GET)，**命中 2 秒内的旧空缓存** → 加了/刷新都看不到刚写的记忆。
- **通用修法**：`_fetch` 里**任何写操作（非 GET）成功后 `_dedupCache.clear()`**——一次修掉所有"写完紧接着拉列表却拿到旧缓存"的页。
- **你让我找的"类似的"确实有**：`ScheduledView`（定时任务，创建后 reload 命中旧缓存，同 bug）、`DiscoveryView`（创建定时任务）——全被这个通用修法覆盖。
- 顺手：记忆保存失败时显示**真实错误**（不再只是"保存失败"），避免"没反应"。

**② 布局：卡片不再"挤左空右"**
根因：`CardGrid` 用 `auto-fill`——卡片少时保留空列、卡片挤在左边、右侧大片留白（图1 用量的 2 张统计卡、Claude/Codex 卡都这样）。**改成 `auto-fit`**：卡片自动**撑满整行**（2 张→各 50%，3 张→各 1/3），右侧空白消失。这一处修复让所有用 CardGrid 的页（用量/记忆/运行轨迹/质量…）布局都更合理。

**③ 空状态做厚（不再"太单调"）**
`StateView` 空态从"悬空的小图标+一行字"升级为**居中聚焦卡片**（有面/边/阴影的盒子 + 图标章 + 标题 + 说明 + 可选操作区），并支持 `title`/`action`（向后兼容）：
- 记忆中心空态：标题「还没有记忆」+「教它记住一条」按钮 + **3 个可点击示例 chip**（点了直接预填表单，如"以后写代码都用中文注释"）。
- 定时任务空态：标题 +「新建任务」按钮（直接展开表单）。
- 运行轨迹「遥测未开启」：从一条灰色细条 → 正经空态卡（图标 + 标题 + 如何开启 `HASHMM_AGENT_TRACE=1` 的说明）。

> 诚实边界：纯前端，**沙箱无法渲染预览**，已做全部 TSX 语法校验（api.ts/PanelKit/记忆/定时/运行轨迹/用量 全过）+ db 层逻辑实测。图1 那条「API 余额取不到」**极可能是后端没重启**（前端已更新但 AutoDL 后端还没加载上版 `/api/admin/provider-balance` 新接口 → 404），不是这次的代码 bug；重启后端即可。生效：`cd frontend-next && npm run build` 后 electron 重打包；本批无后端代码改动。
