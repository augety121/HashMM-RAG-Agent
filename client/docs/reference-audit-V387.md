# V387 外部参考与机制迁移审计

日期：2026-07-22

本审计只记录能够由本地文件、官方文档、运行代码和回归测试复核的机制。产品宣传、模型自述、截图中的状态和未执行示例不作为 HashMM 已具备能力的证据。

## 参考材料与完整性

- Codex 源码压缩包：`D:/edge downloads/codex-main (1).zip`
  - 大小：12,643,011 bytes
  - SHA-256：`5cc0be46bcb226ba884615aab9542e611907cc1176d1e9921bc20cb6065ad9d1`
- Codex 本地源码：`D:/sheji/codex-main`
  - 本轮延续核对工具注册与执行边界，不复制源码。
  - 已审计文件 `codex-rs/core/src/tools/registry.rs` 的 V386 记录：26,535 bytes，SHA-256 `1165a8527aef6e07b16fefeab7daadda8e1699abf825df8d1c13758774b7625d`
- OpenClaw 压缩包：`D:/edge downloads/openclaw-main.zip`
  - 大小：99,793,713 bytes
  - SHA-256：`1ac8d4b6f8ea039e3e2ecb73de1f1c4fe8307f6eb2055a368569a8781a848de4`
- Hermes Agent 压缩包：`D:/edge downloads/hermes-agent-main (4).zip`
  - 大小：77,437,712 bytes
  - SHA-256：`2bbfd002635138aa50cdf6fbcb8acb9fda4335614c8dda9387696a26258174f6`
- 面试实战资料：`C:/Users/Administrator/Desktop/大模型应用!算法学习路线+八股+面试实战4.md`
  - 大小：1,427,064 bytes
  - SHA-256：`ee6d42a4e8a14af33da24d13ab85cf4cefc12c26635b7efa978ffda82f80742f`
- UU 远程静态语言资源：`D:/ruanjian/uu远程/GameViewer/bin/lang/zh_CN/gdstrings.ini`
  - 大小：116,305 bytes
  - SHA-256：`32657ffab4ad20e5b8dd29bbe8b1cd954b97bfe613a13d2eb1490a1a9765d03e`
- vivo 办公套件静态隐私屏文本：`D:/ruanjian/vivo办公套件/pcsuite/vivoControl/depends/x64/privacy_screen_i18n.json`
  - 大小：21,363 bytes
  - SHA-256：`b3684563072ff300d375380346216055cb90b2cd3972124603f160df44690c9f`
- Codex 官方手册与子 Agent 文档：`https://developers.openai.com/codex/codex-manual.md`、`https://learn.chatgpt.com/docs/agent-configuration/subagents.md`
  - 只用于核对有界委派、上下文隔离、可检查任务和沙箱边界等公开机制。

## 本轮采用并重新实现的机制

1. **远程质量从装饰状态变成同一事实链。** 桌面端远程执行器每次采样都生成有界的 `hashmm.remote-quality.v1`，覆盖 RTT、丢包、可用带宽、FPS、质量档位、变化方向和原因。主进程只接受受信远程窗口发送的白名单字段，状态再投影到桌面页面；不传候选地址、SDP 或原始 RTCStats。
2. **App 使用自己的实时测量。** Android 端从已建立的 WebRTC 连接中计算 RTT、丢包、可用带宽和视频帧率，以确定性阈值显示“良好/一般/较差/未测量”，不再用静态“可用”文案冒充网络质量。
3. **Office 交接升级为内容版本握手。** 参考办公套件“系统文件关联、修改后回到任务”的用户路径，HashMM 保留系统应用编辑能力，同时用 SHA-256 内容指纹和单调 revision 区分真实内容变化。同步时按稳定快照读取、上传到属主会话、校验服务器落盘哈希，再用同一哈希确认；编辑期间再次变化会返回冲突，不会错误标记为已同步。
4. **会话文件列表改为增量读取。** 后端列表只返回公开元数据和 revision，不泄露服务器绝对路径；支持 ETag/If-None-Match。桌面端按账号与会话隔离持久缓存，先显示缓存，再后台条件校验；只有 revision 变化才替换可见数据。
5. **远程诊断真正回到 Chat。** 质量卡片可把经过裁剪的网络事实附加为下一轮 Chat 的不可信功能上下文，由 Chat 解释或制定恢复步骤；不会把外部页面或 RTC 原文提升为系统指令，也不会自动发送。
6. **延续 V386 的统一 harness。** Chat、长任务、多 Agent、压缩检查点、RAG 证据、桌面右栏和 App 运行页继续共用冻结作用域、真实 capability 交集、粘性终态和属主绑定恢复点。本轮新增能力通过同一权限与审计边界接入，没有另建平行 demo 链路。
7. **把体验改善建立在可验证状态上。** 桌面远程页和 App 远程页都以少量状态、柔和分组和可执行下一步为主；“良好”“已同步”等正向状态必须由采样或哈希握手产生，页面不会凭存在某个按钮就宣称能力可用。

## 明确未采用

- 不反编译、不复制、不调用 UU 远程或 vivo 办公套件的私有协议、二进制、品牌资产、登录体系或云服务凭据。
- 不声称实现了硬件级/驱动级隐私屏。HashMM 当前只有自身窗口和任务范围内的隐私控制，不会把软件文案当作物理黑屏能力证据。
- 不把公网 TURN 凭据、ICE candidate、SDP、绝对文件路径、原始 RTCStats 或用户文档内容写入质量遥测。
- 不用文件大小和修改时间代替内容身份；它们只用于快速探测，最终同步以稳定读取后的 SHA-256 为准。
- 不把缓存当作权威新数据。缓存先用于快速呈现，后台仍使用 owner-checked API 和条件请求确认版本。
- 不把外部项目页面、示例 Agent、skill 清单或模型生成代码直接注册成 HashMM 能力。没有执行器、权限、审计和回归测试的功能不会向 Chat 暴露。

## 许可证与实现边界

本轮为机制级独立实现，没有把 Codex、OpenClaw、Hermes Agent、UU 远程或 vivo 办公套件源码/资源复制进 HashMM 发布包。外部项目和软件仍受各自许可证、服务条款和商标规则约束；上述哈希只用于确定本轮实际审计输入，不表示 HashMM 与相关产品存在官方兼容、授权或背书关系。
