# 维护者验证

当前公开文件仅允许说明、通用 Actions 与合成隐私检查。私有产品源码不能进入任一公开 PR；包括旧版本恢复、复制到 docs/scripts 或打包上传的形式。

`Private validation` 保留七项受控检查，`Private platform candidates` 提供苹果/Linux 原生宿主。仅仓库所有者从 main 手动触发，指定准确开放私有 PR 及完整 head；复用 private-validation 环境中的 HASHMM_SOURCE_READ（Contents/Pull requests read）与 HASHMM_STATUS_WRITE（Commit statuses write）。不扩展权限，不在文档写令牌。详情见 [平台说明](docs/PRIVATE-PLATFORM-BUILDS.md)。

源码读取凭据仅用于准入和 checkout，状态凭据仅进入报告步骤；私有子进程不接收它们。原始输出只在临时 runner 文件中，平台附件仅上传 age 密文并保留3天，解密身份不进入 GitHub。禁止调试模式与原文上传兜底；不要运行未经审核的私有提交，输出重定向不构成恶意代码沙箱。

原始七项测试失败与原生平台候选分别记录；签名、安装、GPU、真实页面和生产验收分开。用户已明确授权本轮相关构建修复 PR 合并及标准 runner 执行，不包含收费资源、商店发布或生产部署。

当前分支撤下历史产品源码是普通可恢复提交，不清除旧分支/提交/PR/缓存。维护历史隐私需要单独处理整个 Git 引用和 GitHub 保留对象，不能将当前文件树白名单通过说成历史不可见。
