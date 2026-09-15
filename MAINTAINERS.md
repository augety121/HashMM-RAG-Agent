# 维护者验证

`Private validation` 仅允许仓库所有者从 main 手动触发。输入为私有 PR #3 当前完整提交号，启动前通过 GitHub API 重新核对 PR 的仓库、状态和 head。分支变化后旧结果不能代表新提交。

保留 Python Ubuntu/Windows、桌面 Node Ubuntu/Windows、前端 Ubuntu、迭代工具 Ubuntu/Windows 七个检查。不把文档检查改名作为产品通过，不跳过旧失败检查，不自动合并。

## 一次性配置

在 `private-validation` environment 配置仅允许 main 部署，并启用维护者审批。凭据只存放于 environment secrets，不能放在仓库变量或文档中：

- `HASHMM_SOURCE_READ`：仅私有 hashmm 仓库，Contents read、Pull requests read，短期有效。
- `HASHMM_STATUS_WRITE`：仅私有 hashmm 仓库，Commit statuses write，短期有效。

读取凭据仅用于核对及 checkout；写状态凭据仅进入独立报告 job，测试 job 不接收该凭据。不要复用个人全仓库令牌。凭据由账号所有者在 GitHub 中创建并填写。

## 数据边界

测试输出重定向到 runner 临时文件，公开仅固定结果；不上传原始日志、缓存、源码、安装包或测试产物。runner 完成后由 GitHub 销毁。私有依赖或失败信息需要在私有环境进一步诊断，不把原始输出粘贴到公共 PR。

这依赖维护者先审查指定私有提交；输出重定向不是恶意代码沙箱，不要运行不可信提交。禁止调试日志模式。公共 workflow 不接受 Issue/PR 正文作为执行内容。新增源码读取权限应由所有者确认后配置。

## 旧源码范围

按所有者要求，当前文件树保留本仓库此前已公开的历史版本（`60aba8d`）源码、README、文档及经隐私清理的安全素材，供开源学习与 PR 协作；已有 LICENSE 保留。当前私有产品源码不公开同步。历史代码恢复不改变私有验证凭据、环境审核、输出隔离或状态回写边界，也不把历史代码的测试当作当前私有产品验收。
