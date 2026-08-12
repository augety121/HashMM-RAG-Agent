# V375 外部参考审计与适配记录

本文件只记录本轮实际检查和落地的参考。外部仓库、压缩包、文档与输出均按不可信输入处理；许可证允许参考不等于其运行行为适合进入 HashMM。

## OfficeCLI

- 用户提供文件：`OfficeCLI-main.zip`
- SHA-256：`574721B8F53815055D75730B7277211EF7373A335D9BCB1831CEA6FABE48063F`
- 仓库许可证：Apache-2.0，含 NOTICE。
- 审计结论：其 Python SDK 是外部 CLI 的薄封装，使用命名管道并可触发联网安装。HashMM 没有复制或嵌入该 CLI，也没有让最终用户接触命令行。
- 实际适配：实现应用内 `hashmm.office-artifact.v1` 结构检查器，使用项目受控运行时中的 `python-docx`、`python-pptx`、`openpyxl`。能力通过 Chat 工具 `inspect_office` 和文档工坊 `office_audit` 共用，并将验证结果写入运行证据。

## Hallmark

- 用户提供文件：`hallmark-main.zip`
- SHA-256：`22508F6ACC344148F5D32E777FC5799782C95A60F5C9EA32E2DB2B877DF4C29A`
- 仓库许可证：MIT。
- 审计结论：采用其“生成后必须经过明确质量门”的通用机制，不复制其产品 UI 或业务实现。
- 实际适配：新增 `hashmm.design-quality.v1`，只报告能从 HTML/SVG 确定观察的离线可渲染性、文档元数据、可访问性线索和固定宽度风险。未运行视觉回归时，不宣称对比度、审美或跨浏览器正确。

## awesome-llm-apps

- 用户提供文件：`awesome-llm-apps-main.zip`
- SHA-256：`9D0D2DAEE560E96191B6EC994EC1DD796E049837B47AC39998DF020E89465BF6`
- 仓库许可证：Apache-2.0。
- 审计结论：示例覆盖面可用于建立故障分类，但示例框架、演示密钥流程和单页应用不直接进入生产架构。
- 实际适配：新增 `hashmm.rag-diagnostics.v1`，从既有检索清单、工具轨迹和评测证据识别 grounding drift、chunk boundary、long-chain drift、tool reliability、eval blind spot 与 config reproducibility。无法从现有证据判断的 embedding mismatch、stale index、memory leak 和 multi-tenant isolation 会明确标为不可评估。

## vivo 办公套件

- 用户提供公开产品页：`https://pc.vivo.com/#/`；该产品不是开源仓库，本轮未发现可审计的公开 SDK、私有协议或稳定包名契约。
- 采用边界：桌面端只通过 Windows 已注册文件关联交接 DOCX、XLSX 与 PPTX；Android 只通过标准 `ACTION_VIEW` 和 OpenXML MIME 类型交接。vivo 办公套件或其它办公应用注册对应格式后即可被系统选择。
- 实际适配：桌面端使用不透明交接 ID 绑定单个会话文件，检测本地修改后由用户显式同步回当前会话；回传先上传到同后缀临时文件并经过确定性 Office 解析，验证失败不会覆盖原文件。App 下载同源、带认证的文件到系统下载目录后交给用户选择的本机应用。
- 未采用：未硬编码 vivo 进程路径、包名、私有 URI、账号体系、云同步或自动化接口；因此不宣称能够调用未公开的跨设备或云端能力。

## UU 远程参考边界

- 用户提供公开产品页：`https://uuyc.163.com/`。本轮没有取得可审计的公开协议、SDK 或服务端接口，因此不复制、不冒充兼容其闭源信令和传输协议。
- 实际适配：继续完善 HashMM 自有 WebRTC 远控链路：信令认证失败等待新令牌、断线按有界退避重连、旧连接代次失效、会话隐私状态以主进程观测值为准，并区分“远端内容保护”和物理屏幕熄灭。
- 生产安全：移除随包发布的第三方公共 TURN 默认账号。默认只提供 STUN；严格 NAT、企业网络或稳定中继必须由管理员配置自有 TURN 服务与凭据。能力不可达时明确显示配置边界，不回退到未知公共中继。

## 不等同声明

本轮实现是基于 HashMM 自身 Python、FastAPI、SQLite、Electron、Next.js 与 Android 架构的独立适配。以上项目或产品的存在不能证明 HashMM 与其协议兼容，也不能证明生成结果正确。可用性以本仓库确定性测试、发布门、运行证据和用户验收为准。
