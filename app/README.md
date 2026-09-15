# HashMM（Android 客户端伴侣 App）

一个**全新、独立**的 HashMM 手机 App，与 HashMM 桌面客户端联动：在手机上打开工作台、远程控制电脑屏幕。

- **独立安装**：包名 `com.hashmm.app`，和 HashLens(`com.hashlens.app`) 各自独立，**不会覆盖**。
- **免登录可进**：打开直接进首页浏览；点「工作台 / 远程控制」时才要求登录，登录后回到该功能。
- **极简干净**：从零搭建，只保留必要依赖（Compose + Hilt + Supabase Auth + WebRTC），APK 体积远小于旧 App。
- **大厂标准**：Compose + Material 3 + Hilt 依赖注入 + DataStore + 单一职责分层。

---

## 一、构建步骤

1. 用 **Android Studio**（近期版本，内置 AGP 9 / Gradle 9.3.1 支持）打开本目录。
   - 首次打开会自动补全 Gradle Wrapper（`gradlew` / `gradle-wrapper.jar`）。若用命令行构建，先执行：`gradle wrapper --gradle-version 9.3.1`，再 `./gradlew assembleDebug`。
2. 等待 Gradle Sync 下载依赖。
3. 点 Run 安装到手机；或 `./gradlew assembleRelease` 出包。

> 工具链版本与你的 HashLens 一致（AGP 9.0 / Kotlin 2.3 / Compose BOM 2026.01 / Hilt 2.59 / supabase 3.2.2 + ktor 3.1.3 / webrtc 1.3.10），所以你现有的 Android Studio 能直接构建。

## 二、配置 `local.properties`

仓库里的 `local.properties` 已预填 Supabase 凭证，但请务必核对：

```
SUPABASE_URL=https://你的项目.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_xxx
DEFAULT_CLIENT_URL=            # 可留空，进 App 后在「设置」里填
```

⚠️ **关键**：`SUPABASE_URL` 必须指向「你创建登录用户的那个项目」。去 Supabase → Project Settings → API 看 Project URL，确认一致。否则用户不在 App 查询的项目里，怎么都登不进。

> 本 App 登录**只用 Supabase Auth**，不依赖 profiles 等任何业务表——所以无需建表、登录开箱即用（你之前 HashLens「缺 profiles 表登不进」的问题在这个 App 里不存在）。
> 若要把某账号密码改成已知值，用随附的 `hashmm-supabase-setup.sql` 里的改密码语句（仅需 auth.users 一句）。

## 三、配置客户端地址

进 App →「设置」→「客户端地址」，填你的 **HashMM 服务地址**（桌面客户端连接的后端，同时供工作台和远程控制使用），例如：

```
http://192.168.1.20:6006        # 局域网
https://your-tunnel.example.com # 公网隧道（Cloudflare Tunnel 等）
```

手机需与该地址**网络可达**。从你的 AutoDL 后端日志看，后端(6006)同时服务前端 UI 和 `/api/remote/ws` 信令，所以**一个地址**即可。

---

## 四、功能说明

| 功能 | 说明 | 是否需登录 |
|---|---|---|
| 首页 | 浏览、查看登录状态 | 否 |
| 工作台 | WebView 内嵌 HashMM 客户端，用同一 Supabase 身份自动登录，查看知识库/对话/管理等 | 是 |
| 远程控制 | WebRTC 投屏被控端桌面，触摸映射为点击/拖拽/长按右键（坐标归一化 0..1000，与 `desktop/remote-viewer.html` 协议一致） | 是 |
| 设置 | 客户端地址、账号、关于 | 否 |

**联动原理**
- 工作台：把当前 Supabase access token 通过 `?sb_token=` 传给客户端 + 注入 `localStorage('hmm_token')`，后端 `verify_any_token` 已支持验 Supabase 身份，故客户端自动同账号登录。
- 远程控制：以 viewer 身份连后端 `WS /api/remote/ws`，与桌面客户端(host)配对后走 WebRTC P2P 直连画面，输入事件经信令回传。

---

## 五、工程结构

```
app/src/main/java/com/hashmm/app/
  HashMMApplication.kt        # @HiltAndroidApp
  MainActivity.kt             # Compose 入口
  di/AppModule.kt             # 提供 SupabaseClient（仅 Auth）
  data/
    settings/SettingsStore.kt # DataStore：客户端地址
    auth/AuthRepository.kt     # Supabase 登录/登出/会话
    remote/RemoteSignalingClient.kt  # WS 信令（与后端协议对齐）
  ui/
    Routes.kt                 # 导航路由
    HashMMApp.kt              # NavHost + 登录门
    theme/                    # Material3 主题
    home/                     # 首页（免登录）
    auth/                     # 登录页
    workbench/                # 工作台 WebView
    remote/                   # 远程控制（WebRTC viewer）
    settings/                 # 设置
```

依赖注入用 Hilt；远程控制的 WebRTC 协议与桌面端参考实现严格一致。

---

## 六、用户数据云同步（v1.1 新增）

登录后，App 从 Supabase 同步用户数据，体验向桌面客户端看齐：

| 数据 | Supabase 表 | 同步时机 |
|---|---|---|
| 会话列表 | `chat_conversations` | **可见先同步**：登录后立即拉取，秒开列表 |
| 会话消息 | `chat_messages` | **点进去再同步**：打开某会话时才拉 |
| 用户设置 | `user_settings` | 后台拉取 |
| 用户记忆 | `user_memory` | 后台拉取 |
| RAG 大文件 / 向量 | —（不进 Supabase） | 由 AutoDL 私有云直连 |

- 这些表**镜像客户端后端真实 SQLite 表**（`hashmm/api/database.py` 的 conversations / messages / user_profiles / user_memory），字段一一对应。
- RLS 保证每个用户只能读写自己的数据。
- **先在 Supabase 跑 `hashmm-supabase-sync.sql` 建好这些表。**

> 数据要先「流进」这些表才能在 App 看到。让**客户端侧也写入 Supabase**是下一步（客户端 user_id 形如 `sb_<uuid>`，写入时去掉 `sb_` 前缀即为表里的 uuid）。在那之前，App 的对话页会显示空列表。

### 后续路线（按优先级）
1. 客户端侧写入 Supabase（数据真正双向同步）。
2. 客户端在线状态 + 任务进度（chat / RAG 解析）实时显示，本地↔远程会话接管（Codex 式）。
3. 全 App 微信简约风 UI 统一打磨。

---

## 七、客户端在线状态 + 任务进度接管（v1.2 新增）

- **客户端在线**：首页实时显示「客户端在线/离线」（同账号）。后端 `GET /api/remote/status` 按同一 uid 统计在线被控端；在线即可远程控制。
- **客户端动态（Codex 式）**：「客户端动态」页每 3 秒轮询 `GET /api/activity`，展示
  - 正在生成回复的对话 —— 点「接管」即在手机上打开该会话；
  - 后台任务（RAG 解析/索引）的进度条。
- 流式对话按用户过滤；后台任务为系统级、仅管理员可见（防多用户泄露）。

> ⚠️ **后端需配置 service_role key 才能写入 Supabase**：在 AutoDL 设环境变量
> `HASHMM_SUPABASE_SERVICE_KEY=<你的 service_role key>`（Supabase → Project Settings → API，**保密、仅服务端**），
> 然后重启后端。`supabase_url` 已在客户端设置里配好。配置后，客户端的会话/消息/设置/记忆会自动写入 Supabase，App 登录即可同步看到。

### 仍在路上
- 接管时的「实时流式」展示（当前接管打开的是云端已同步内容；正在生成的逐字过程需 App 直连客户端 API 拉取，下一步做）。
- 全 App 微信简约风 UI 统一打磨。

---

## 八、远程在线门控 + 实时接管 + 微信风 UI（v1.3 新增）

- **远程控制在线/离线**：进入远程控制后，客户端离线明确显示「客户端离线」并提供「重新检测」；在线显示「客户端在线 · 选择要控制的设备」，同账号才可控。
- **实时接管**：打开会话时——先用 Supabase 缓存秒显，再直连客户端 `GET /api/conversations/{id}/messages` 刷新；若该会话正在生成回复，每 1.5 秒轮询，**逐字实时更新**，标题显示「生成中…」。客户端不可达则回退到云端缓存。
- **微信简约风统一**：关闭 Material You 动态取色（避免跟随壁纸漂移），统一品牌靛蓝 + 中性浅灰；首页与设置改为「灰底白卡」，全 App 视觉一致、克制。

---

## 九、构建 APK（重要）

1. **Android Studio 打开本项目根目录**（不是 app 子目录），等待 Gradle 同步。
   - 同步时 Android Studio 会**自动生成 `local.properties` 并写入你的 `sdk.dir`**（Android SDK 路径），无需手动操作。
2. 菜单 **Build → Build App Bundle(s) / APK(s) → Build APK(s)**，完成后点 locate 拿到 `app/build/outputs/apk/debug/app-debug.apk`。

### 若仍报 “SDK location not found”
说明 Android Studio 没写入 SDK 路径。任选其一：
- 在 **File → Project Structure → SDK Location** 里设好 Android SDK 路径，再 **Sync**；或
- 手动在项目根目录建 `local.properties`（参考随包的 `local.properties.example`），写：
  - Windows：`sdk.dir=C\:\\Users\\你的用户名\\AppData\\Local\\Android\\Sdk`
  - macOS：`sdk.dir=/Users/你的用户名/Library/Android/sdk`
- 或设系统环境变量 `ANDROID_HOME` 指向 SDK 目录后重启 Android Studio。

> Supabase 配置已放在 `gradle.properties`（publishable key 是客户端公开 key，可提交），所以**不再依赖 local.properties 填凭证**；`local.properties` 现在只负责 `sdk.dir`，交给 Android Studio 自动管理。要换 Supabase 项目就改 `gradle.properties` 里那两行。

---

## 十、任务进度改为 Supabase Realtime 实时推送（v1.4）+ Workbench 精修

- **实时任务进度（不再轮询）**：「客户端动态」页改用 **Supabase Realtime** 订阅 `client_activity` 表——对话开始生成、RAG 解析进度、任务结束都是**服务端推送、秒级到达**，无需定时拉取。点进行中的对话即可接管（接管页直连客户端逐字实时显示）。
  - 后端：对话开始生成→写入 `client_activity`(kind=chat)，完成→删除；RAG 解析→按阶段节流写入(kind=job, done/total)，完成→删除。RAG 任务归属触发它的用户，RLS 保证只看自己的。
  - **⚠️ 需在 Supabase 跑 `hashmm-realtime-activity.sql`**（建表 + 开启 Realtime 发布），并确保后端配了 `HASHMM_SUPABASE_SERVICE_KEY`。
- **Workbench 精修**：接入页加品牌 logo + 圆角按钮；WebView 加载时显示进度圈，不再白屏。登录页按钮也圆角化，与全 App 微信风统一。

---

## 十一、离线优先本地缓存 + 增量同步 + 账号隔离（v1.5，对标大厂）

**数据读取改为离线优先**：会话/消息先从本地加密缓存秒显，再增量同步刷新。

- **增量同步**：每条记录带服务端 `updated_at`（触发器统一写），本地记「上次同步时间」，每次只拉 `updated_at > 上次同步` 的变更，按主键 upsert 进本地——**变更覆盖、未变保留**，省流量、可离线。
- **会话详情三层**：本地缓存秒显 → Supabase 增量同步（统一时间戳写缓存）→ 客户端直连实时（流式逐字覆盖显示）。
- **安全（双层）**：
  - 服务端 **RLS**：每行只有本人可读写，别的账号读不到。
  - 本地 **静态加密 + 账号隔离**：缓存用 AES256-GCM 加密，密钥由 **Android Keystore** 托管（设备外无法解密）；每个账号独立目录；**退出账号即清空该账号缓存**，别的账号读不到上一个账号的数据。

**⚠️ 需先在 Supabase 跑 `hashmm-delta-sync.sql`**（给同步表加 updated_at + 触发器）。三个 SQL 都放在后端包的 `sql/` 目录里。

### 仍可继续
- 客户端侧也做同款本地缓存层（桌面已有 SQLite 作本地源，可加「拉取 App 端变更」的反向增量）。
- App 端写操作（置顶/改名/删除会话）回写 Supabase，实现完全双向联动。

---

## 十二、实时联动（v1.6）：客户端一改，App 立刻变

- **会话列表实时**：订阅 Supabase Realtime（`chat_conversations`）——客户端新建/改名/置顶/删除会话，App 列表**秒级更新**，不再只在打开页面时拉。
- **会话详情实时**：订阅 `chat_messages`（按 conv_id）——该会话有新消息，App 详情自动刷新；正在生成时由直连客户端的逐字轮询负责。
- **同步状态**：列表顶部同步时显示转圈，让"正在同步"可见（大厂体感）。
- 触发即增量：收到实时事件后只拉变更、按主键覆盖本地缓存，省流量。

**⚠️ 需在 Supabase 跑 `hashmm-realtime-sync.sql`**（把 chat_conversations / chat_messages 加入 Realtime 发布），否则 App 收不到这两张表的实时事件。四个 SQL 都在后端包 `sql/` 目录，按编号顺序运行。

### 关于「完全双向联动」（下一步，需谨慎一起做）
现已实现**客户端 → App 的实时联动**（安全，只动 App + Supabase）。反方向 **App → 客户端**（在 App 里置顶/改名/删除→同步回客户端）需要两件事配套做，否则会出现两端不一致：
1. App 端写操作回写 Supabase；
2. 客户端从 Supabase 反向拉取并应用到本地 SQLite，且要处理**回声循环**（用内容比对跳过自己刚推的变更）与**线程安全**（在请求线程里写 SQLite，不在后台线程碰连接池）。

这是成熟客户端的数据层改动，风险较高，我会单独、谨慎地做，避免破坏现在能跑的系统。
