# HashMM App V294

## Chat 图关联证据

- App 与桌面端共用服务端 Graph Engineering 检索链路：查询时把知识图谱命中的实体、关系按 `chunk_id` 回接到原始文档切片。
- 来源卡可识别 `graph_evidence`，以“图关联”标记真实的补充证据；普通来源保持原有展示。
- 图关联证据仍由服务端执行文档权限过滤、数量上限和来源审计，App 不在本地猜测或生成关系。
- 新增消息来源协议回归，覆盖 `graph_evidence` 字段保留和旧消息缺少 `method` 时的兼容行为。

## 兼容性

- 版本：`1.10.70`（`versionCode 111`）。
- 旧消息没有 `method` 字段时继续按普通来源显示。
- 验证：`137/137` 单元测试、Kotlin 编译与 debug APK 构建通过。
