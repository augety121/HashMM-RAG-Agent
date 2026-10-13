# HashMM App 1.12.0（128）/ Backend V410

## 本轮完成

- 工作台命令和成果验收改为账户隔离的加密 outbox：命令在发送前落盘，断网、超时或不兼容响应均保留同一个请求标识。
- 恢复联网后只使用原 `command_id` / `decision_id` 做幂等核对，并采用有上限的指数退避，不重新创建可能导致重复副作用的命令。
- App 原生工作对象接入服务端 generation 与 ArtifactRevision，包括当前代次、内容哈希、成果修订和工作画布同步哈希。
- 未改变的工作列表、详情和画布继续使用账户隔离快照与私有 ETag；切换账号不会读取上一账号的内存或持久化投影。

## 验证

- `testDebugUnitTest` 全量通过。
- `WorkRuntimeRepositoryTest` 针对 outbox、退避、generation 与 ArtifactRevision 的解析回归通过。

## 已知限制

- 当前仍是本地 debug 签名构建流程；公开分发前需要使用受控 Android keystore 生成 release 签名。
- 离线 outbox 解决的是“不确定响应的幂等核对”，不会绕过服务端 owner、revision、权限或审批检查。
