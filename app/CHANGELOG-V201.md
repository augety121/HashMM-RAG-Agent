# App V201 变更记录

## 1. 对话页「电脑任务」菜单重做（截图反馈的风格脱节问题）
- 原来是 Material3 默认 `DropdownMenu`：淡紫容器色 + 红色分组标题，与全 App 白底品牌红设计语言脱节，长菜单还会被底部裁切。
- 重做成底部抽屉 `DispatchSheet`，与 ChatHomeScreen 快捷操作、工作台任务卡同一套视觉：
  - 头部：标题 17sp SemiBold +「在你的电脑上执行，结果回到这段对话」说明；
  - 分组标签：12sp 灰字（替换原来的品牌红小字——红色只留给强调，不做分组装饰）；
  - 条目：38dp 淡染方块图标容器 + 15sp Medium 标题 + 12sp 副标题（QuickActionRow 同款）；
  - 文件类型快捷项（PDF/Word/Excel/PPT/图片/最近 10 个/今天的）收成一组胶囊 chip，不再占 7 行；
  - 内容可滚动、可拖拽关闭，不再有裁切。
- 功能链路完全不动：needInput 提示、onComputerTask/onRequestFile 下发、kind 码全部保持。

## 2. 主题根修：浮层紫调的根因
- 根因：`Theme.kt` 只覆盖了 surface/surfaceVariant，没覆盖 M3 的 `surfaceContainer` 家族——
  M3 基线的这组中性色自带紫色底调，所有 DropdownMenu / ModalBottomSheet / Dialog 默认取它，全都发紫。
- 修复：`Color.kt` 新增品牌中性灰的 Light/Dark Container 五级（Lowest→Highest），
  `Theme.kt` 两套 colorScheme 全量接入，并把 `surfaceTint` 钉为 surface（关掉高程染色）。
- 效果：全 App 一次生效——长按消息菜单、会话列表菜单、所有抽屉/对话框统一为干净的白/近黑浮层。

## 3. 路线图阶段 D（App 侧）：语音发送接入意图编排
- 语音转写自动发送前，先调 `/api/voice/orchestrate`（FileDispatchRepository.orchestrate）：
  - `dispatch`（涉本机文件/操作）→ 自动映射到既有电脑任务通道（browser_use→agent、seq→seq、file→取文件、其余→智能助理），
    用户消息以【电脑任务】形态落屏，桌面端执行、结果回本会话（Supabase 同步，手机直接看到）；
  - `clarify`（意图太模糊）→ 原话恢复到输入框 + 澄清问题 Toast 提示，补一句直接重发，不猜错方向白跑；
  - `local`（纯问答/写作）→ 与打字发送完全一致。
- 可靠性兜底：编排接口任何失败都静默回退普通发送——语音功能绝不因新链路故障而失效。
- 打字发送行为完全不变（编排只挂在语音自动发送这一个入口）。

## 变更文件
- ui/chat/ChatDetailScreen.kt（菜单重做 + 语音编排接线）
- ui/chat/ChatDetailViewModel.kt（sendVoice + voiceClarify）
- data/remote/FileDispatchRepository.kt（orchestrate）
- ui/theme/Color.kt / Theme.kt（surfaceContainer 家族）
