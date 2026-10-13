# App Room 迁移（V306）—— 把分页加密 JSON 换成真数据库

## 为什么是"补丁"而不是直接改进主源码
Room 是**编译期代码生成**（KSP 生成 DAO/DB 实现）。本沙箱无法拉取 Room 的 AAR（无 Maven 访问），
因此**无法在此环境编译验证 Room 版本与你 AGP/Kotlin 的兼容性**。为不冒险弄坏你现在能构建的工程，
Room 代码以补丁形式提供（这些 .kt 已过 ktlint 解析器校验，语法无误），你在能构建的环境里按下列 3
步接入并跑一次构建即可。分页方案（LocalStore）仍在，未被破坏——Room 接好后再切换。

## 收益
- 追加/更新单条消息 = **单行写 O(1)**（旧分页方案是 O(页)，整会话 JSON 重写是 O(n)）。
- 按会话取消息、按时间排序**走索引**；长会话可**分页读**（`getMessagesPage`），不整表加载。
- 可选静态加密：接 SQLCipher 只需在 `Room.databaseBuilder(...).openHelperFactory(...)` 传一个
  用 SecureKeyStore 密钥打开的 SupportFactory，DAO/实体零改动。

## 接入 3 步
1. **加依赖**：按 `build.gradle.additions.txt` 改 `gradle/libs.versions.toml` 与 `app/build.gradle.kts`。
2. **放代码**：把 `java/com/hashmm/app/data/db/` 下 5 个文件拷到
   `app/src/main/java/com/hashmm/app/data/db/`。
3. **切换 LocalStore**：把 `LocalStore` 的 `getMessages/putMessages` 委托给 `RoomMessageStore`
   （用 `AppDatabase.get(context).messageDao()` 构造）。示例：
   ```kotlin
   private val roomStore by lazy { RoomMessageStore(AppDatabase.get(ctx).messageDao()) }
   suspend fun getMessages(userId: String, convId: String) = roomStore.getMessages(convId)
   suspend fun putMessages(userId: String, convId: String, list: List<ChatMessage>) =
       roomStore.putMessages(convId, list)
   ```
   （注意：Room 的 DAO 是 suspend，调用点需在协程里；SyncRepository 已在 suspend 上下文。）
4. **一次性迁移旧数据**（可选）：首次启动时用现有 `LocalStore.getMessages` 读出各会话旧消息，
   `roomStore.putMessages` 写入 Room，再删除旧分页文件（`SecureCache.delete`）。

## 构建后建议加的测试
- Room `MessageDao` 的仪器测试（androidTest）：并发 upsert 不丢、分页边界、replaceConv 事务性。
- 迁移测试：旧分页数据 → Room 后条数/内容一致。

## 附:DAO 仪器测试(V306 补充)
`androidTest/com/hashmm/app/data/db/MessageDaoTest.kt`(已随补丁提供,parse-clean):
- 排序走 sortKey、同 id upsert 覆盖、**并发 upsert 不丢**、分页边界、**replaceConv 事务性替换**、deleteConv 隔离。
- 接入 Room 后拷到 `app/src/androidTest/java/com/hashmm/app/data/db/`,加依赖
  `androidTestImplementation(libs.androidx.room.testing)` 或用现有 androidTest runner,
  `./gradlew connectedAndroidTest` 在设备/模拟器上跑。
- 另:`RoomMessageStore.parseMillis` 的排序键口径已用独立验证确认与 `ChatMessageOps.convSortKey`
  **完全一致**(ISO/epoch 秒/毫秒→毫秒、非法→0、最新在前),迁移后消息顺序不变。
