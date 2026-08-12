# HashMM App V281

日期：2026-07-19

- 首页 Chat 与详情 Chat 接入同一套 `hashmm.tool-approval.v1` 工具批准卡，不再只有桌面端能处理高风险任务。
- 批准卡展示工具、原因、风险、有效期和服务端脱敏后的参数；支持拒绝、批准并继续、换设备后继续，按钮状态由服务端权威结果刷新。
- 批准接口只记录决定，App 随后向原 Chat 提交正常继续轮次，工具仍由桌面/服务端 AgentLoop 在权限、Hook 和审计链内执行；手机端不伪造本地执行结果。
- 动态页将跨端待办标记为“等待工具批准”，点击回到原 Chat 接管，与“等待输入”和“正在生成”区分。
- 新增批准协议解析与跨端渲染回归测试；首页和详情页共用相同数据语义，避免两个 Chat 入口状态不一致。
- 版本：versionCode 97，versionName 1.10.56。

## 验证与交付

- Android 单元测试：123 passed，0 failed，0 skipped。
- `assembleDebug` 通过；APK 元数据核验为 versionCode 97 / versionName 1.10.56。
- APK：`HashMM-App-1.10.56-debug.apk`，70,252,107 bytes，SHA-256 `dac0f964756fa4ce23c0627908098f4ecd0edb51617c409dc7c1be3167390143`。
- 与 backend V348 联动；旧服务器不会返回结构化批准记录，因此部署时需要同步升级服务器。
