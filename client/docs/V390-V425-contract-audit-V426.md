# HashMM V390–V425 契约审计与 V426 交付说明

日期：2026-07-26  
版本：Backend V426 / Desktop、MCP、Native source 1.23.0  
数据库 schema：27（本轮没有新增迁移）

这份文档只记录仓库中可以由代码、测试或构建输出复现的事实。它不把页面存在、模型自述、端口可达或版本号当成“已完成”的证据。

## 审计结论

从 V390 到 V425 的主链路已经在代码中串起来：用户目标进入 WorkRuntime，检索和上下文生成留下有界的证据投影，工具动作绑定执行回执，结果经过独立门禁后才可以标记为可交付，桌面端和 App 读取同一份 owner-bound 工作投影。管理员仍可进入 RAG、Agent、MCP、Hook、权限、远程和质量控制面；普通用户不需要理解这些内部对象。

本轮还修复了四类会导致“看起来成功、实际上无法核验”的问题：

1. 执行步骤缺少 `call_id` 或回执时，`run_manifest` 不再放行完成状态；旧的 V346 成功样例已经补成真实的 proof-carrying receipt。
2. 交付质量检查器或回答验证器异常时，结果现在是“无法确认”，不会伪造 `passed=True`。
3. Tool governance、严格计划 Hook 和远程浏览器定位在分类器、Hook 或 DOM 匹配异常时拒绝或要求人工确认，不再把未知状态当成安全状态。
4. Electron 登录刷新改为主进程加密 vault、单次刷新和 CAS 轮换；旧的异步刷新不能覆盖较新的登录，也不会把 refresh token 放回渲染器 localStorage。

## V390–V425 逐段对照

| 版本段 | 已落地的仓库契约 | 确定性验证 | 仍未声称完成的外部门禁 |
| --- | --- | --- | --- |
| V390 | 因果证据图、上下文胶囊、工具步骤与执行回执绑定、完成门禁 | `tests/test_v390_evidence_fabric.py`、`tests/test_v346_task_contract.py` | 真实长对话的 token/延迟曲线 |
| V391 | 浏览器与画布共享证据定位；定位记录含受限 fingerprint、confidence 和 match reason；弱匹配拒绝 | `tests/test_v391_browser_canvas_twin.py`、`desktop/tests-node/test_embedded_browser.js` | DOM 大规模扰动 benchmark、90% 定位率 |
| V392 | 持久 Agent Mesh、邮箱/协作投影、独立交付检查 | `tests/test_v392_agent_mesh.py` | 多模型、多区域真实并发成本 |
| V393–V394 | 候选 Skill 隔离、CAS 发布/回滚、成对 replay 门禁 | `tests/test_v393_governed_skill_evolution.py`、`tests/test_v394_skill_replay_gate.py` | 长期 Skill 漂移和真实业务样本 |
| V395–V400 | 用户工作投影、action inbox、统一画布/结果回执/受治理决定；前端把内部模块折叠成 Work、结果和材料 | `tests/test_v395_work_presentation.py`、`tests/test_v400_work_canvas.py`、前端 V395/V400 测试 | 视觉可用性和跨设备实时 UX 仍需人工验收 |
| V401 | 明确 anonymous/authenticated/refreshing/offline-valid/reauth-required；Electron refresh token 只在主进程 vault，CAS 防旧刷新覆盖新登录 | `frontend-next/__tests__/authSessionLifecycle.test.ts`、`desktop/tests-node/test_auth_session_vault.js`、typecheck | 24 小时登录连续性、真实公网断网恢复 |
| V402 | 原子工作代次、幂等事件和 owner-bound 投影已在数据库/WorkRuntime 中实现 | V400 与后端全量套件 | 多实例数据库故障转移 |
| V403 | provider/model/wire API 能力契约；模型不能扩展提供商能力；不支持的 tool/options 在请求前拒绝 | `tests/test_model_providers.py` | 每个商业 API 的真实额度、限流和错误码矩阵 |
| V404–V410 | context lifecycle、adaptive mesh、证据/质量/Hook 治理和跨端工作连续性模块已接入主链；本轮补齐 fail-closed 异常路径 | 后端全量套件、前端全量套件、Node 安全契约 | 100-turn 压缩保持率、成本收益显著性、完整 OS sandbox |
| V411–V415 | 远程工作进入 WorkRuntime；scope、短时票据、重放保护、WebRTC DataChannel/MJPEG fallback、HTTPS/WSS 门和窄 preload/IPC | `tests/test_v421_remote_work.py`、Node remote/security tests | 真实公网、对称 NAT、自有 TURN 的跨运营商验收 |
| V416–V420 | owner-bound 持久远程审计、动态 TURN 凭据模板、真实 relay/soak acceptance 条件和生产 readiness 投影 | `tests/test_v421_remote_work.py`、`tests/test_v425_remote_continuity.py` | 24 小时、99.5% 可用率、relay 覆盖率必须在真实环境采集 |
| V421–V425 | action inbox、实际传输摘要、最小权限设备接力、正常完成/partial/not_observed 回执；旧票据和 predecessor 失效 | `tests/test_v421_remote_work.py`、`tests/test_v425_remote_continuity.py` | 尚未在公网双设备、弱网和对称 NAT 上取得 acceptance receipt |

“已落地”表示仓库有实现并由确定性检查覆盖；“未声称完成”不是失败，而是必须部署真实环境后才能得到的证据。

## 本轮验证命令

后端使用随桌面端提供的 Python 运行时，并把外部 pytest 工具临时放在导入路径中，避免污染发布运行时：

```powershell
.\desktop\runtime\python\python.exe -c "import sys; p=r'D:\Anaconda3\Lib\site-packages'; sys.path.insert(0,p); import pytest; sys.path.remove(p); raise SystemExit(pytest.main(['-q','tests']))"
```

前端：

```powershell
cd frontend-next
npm test
npm run typecheck
npm run build
```

桌面端与发布源门禁：

```powershell
node --check desktop/main.js
node --check desktop/preload.js
node --check desktop/modules/embedded-browser.js
node desktop/tests-node/test_auth_session_vault.js
node desktop/tests-node/test_ipc-guard.js
node desktop/tests-node/test_preload_contract.js
node desktop/tests-node/test_embedded_browser.js
.\desktop\runtime\python\python.exe desktop/scripts/verify-release.py --source-only
```

## 明确限制

- 本轮没有伪造公网双设备、对称 NAT、TURN、24 小时稳定性或 Authenticode 签名结果；这些必须在用户自己的部署环境执行并保存 acceptance receipt。
- 本轮没有改变 Supabase 密钥、JWT、密码或 `.env`；发布包不包含私有 `.env`、数据库、日志、模型、桌面端和安装器目录。
- Android 仅继承已有协议/投影契约，本轮没有重新生成 APK/AAB。
- 旧的普通开发者 observer Hook 可以记录失败但不阻断；涉及权限、计划或安全策略的 critical Hook 走 fail-closed。这是有意区分，不是遗漏。

