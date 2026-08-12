# HashMM App 1.10.80（121）

日期：2026-07-22

- 运行轨迹读取 `hashmm.retrieval-run.v1` 的真实检索状态、路由、候选/证据数量、尝试次数、耗时与 ACL 状态，不用占位统计补空。
- Graph RAG 诊断展示扩展新增的原始证据数，以及缺失或无权节点的跳过数量；不可访问节点不会进入上下文。
- 多智能体运行事件展示真实角色会话状态、工具调用数量和授权能力数量，与桌面右侧运行面板共用服务端事实源。
- App 仍按伴随式工作入口定位：查看、审批、发起和接力可以移动完成；本地文件、Browser Use 和 Computer Use 的特权副作用交给同账号在线桌面执行器。
- `testDebugUnitTest` 与 `assembleDebug` 通过。

交付：`release-app/HashMM-App-1.10.80-121-debug.apk`

SHA-256：`ef6a6e5784d3edc31cf5002764f08cccc2b988c6b5fb4fba72757926b6047f1f`

说明：该 APK 为 debug-signed 测试包，不是应用商店正式签名包。
