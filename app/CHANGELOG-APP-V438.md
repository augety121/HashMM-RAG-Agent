# HashMM App V438

- 工作画布接入 `hashmm.work-assurance.v1`，与桌面端读取同一份服务端事实。
- 概览页新增面向用户的交付准备度，展示恢复点、验收通过数和确定性阻断项。
- App 不执行本地推断，不根据页面文案猜测任务完成；交付状态仍由 owner-bound WorkRuntime、完成回执和 revision 决定。
- 版本升级为 1.16.0（133）。

真实跨公网远程控制、对称 NAT、TURN 和长时间稳定性仍需部署环境验收。
