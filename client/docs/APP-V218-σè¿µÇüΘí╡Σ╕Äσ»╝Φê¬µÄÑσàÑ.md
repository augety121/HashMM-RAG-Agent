# App（V213 客户端）V218 接入说明 —— 动态页 · 底部导航 · 工作台

> 先说清楚边界：**App 的 Kotlin/Compose 工程不在本仓库**（全树无 .kt / AndroidManifest），
> 原生 UI 我这轮物理上改不了。本轮做的是"App 能立刻用起来"的三件事：
> ① 后端补上动态页一直缺的数据源（`GET /api/feed`，本仓库已实现并注册）；
> ② 给出底部导航"太大"的具体收敛参数（Compose 代码片段，贴进 App 工程即可）；
> ③ 工作台 WebView 的完善清单。把 App 仓库发过来，我直接按本文实现。

---

## 一、动态页：`GET /api/feed`（后端已就绪，V218 起）

动态页此前只有 token 用量——不是 App 没做，是后端没有可喂"动态"的端点。现在有了：

```
GET /api/feed
Authorization: Bearer <token>
```

返回（逐块独立降级，任一块故障其余照常）：

| 字段 | 内容 | 动态页建议呈现 |
|---|---|---|
| `release` | 后端代码版本（如 "V218"） | 为空/低于 V218 → 顶部提示"后端待升级" |
| `usage` | token/成本类摘要（白名单抽取，形状随部署） | 保留现有用量卡，数据源可切到这里 |
| `recent_conversations` | 最近 8 个会话 {id,title,updated_at} | "最近对话"卡，点击跳会话 |
| `recent_files` | 跨会话最近 10 个产物 {conv_id,conv_title,filename,size,mtime,download_url} | "最新产物"流（.html 标"工作画布"角标，点击走 App 既有文件预览 /view 页） |
| `runners` | 派活 runner 心跳 | "我的电脑"在线/离线小卡 |
| `scheduled` | 定时任务最近结果（管理员见明细 ≤6；普通用户只给 {count}） | "定时任务"卡：名称 + 上次结果摘要 |
| `episodes` | 经验回放最近 5 条 {created_at,summary} | "系统学到了什么"轻量列表 |

动态页信息架构建议（自上而下）：后端状态条（release 校验）→ 用量卡 → 最新产物流 →
最近对话 → runner/定时任务双小卡 → 经验回放。一次 `/api/feed` 拉齐，下拉刷新重拉即可。

**旧后端探测**：`GET /api/health` 现返回 `release` 字段；无该字段或版本过旧时，
动态页顶部提示并给出"同步后端源码并重启"指引（与桌面端 V218 的 404 退避同一口径）。

## 二、底部导航"有点大"：具体收敛参数

Material3 `NavigationBar` 默认 80dp 高、图标 24dp、常显 label——观感偏重。建议：

```kotlin
NavigationBar(
    modifier = Modifier.height(64.dp),          // 80 → 64
    tonalElevation = 0.dp,
) {
    items.forEach { item ->
        NavigationBarItem(
            selected = ...,
            onClick = { ... },
            icon = { Icon(item.icon, contentDescription = item.label,
                          modifier = Modifier.size(22.dp)) },   // 24 → 22
            label = { Text(item.label, fontSize = 11.sp) },     // 默认 → 11sp
            alwaysShowLabel = false,   // 只有选中项显示文字，进一步减重（可选，二选一）
        )
    }
}
```

两档方案：**轻收敛**（64dp/22dp/11sp、label 常显）；**重收敛**（同尺寸 + `alwaysShowLabel=false`，
未选中只剩图标，最接近主流 IM 的轻盈感）。四个 tab（对话/动态/工作台/我的）不变。

## 三、工作台（WebView）完善清单

工作台经 WebView 加载同一套 webui（`WorkbenchScreen.kt:105` loadUrl），V217/V218 的
前端改动（画布三入口、画布预览修复）随 webui 自动生效，Kotlin 侧建议补齐宿主体验：

1. **下拉刷新**：SwipeRefresh 包 WebView，`webView.reload()`。
2. **加载态与失败重试**：`onPageStarted/Finished` 驱动细进度条；`onReceivedError`
   给全屏"加载失败 · 重试"占位（当前失败是白屏）。
3. **返回键**：`canGoBack()` 先 `goBack()`，到根再退出 tab（避免误退 App）。
4. **文件下载/预览**：`recent_files.download_url` 与画布 .html 一律走后端既有
   `/api/conversations/{id}/files/{name}/view`（V182 起的手机适配预览页），不跳外部浏览器。
5. **画布宽度**：webui 图标栏在 <768px 自动隐藏（V217 hidden md:flex），WebView 无需处理。

## 四、把 App 仓库发我之后，我按本文直接落地的顺序

动态页接 /api/feed（含空态/错误态/下拉刷新）→ 底部导航收敛 → 工作台四项宿主体验 →
"我的"页顺手核一遍（头像/退出/后端地址展示）。
