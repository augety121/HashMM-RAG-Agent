# HashMM Server V2802

本目录保留历史 V2802 的部署说明。原服务器 ZIP 含真实部署配置，已撤下；请使用 [`../client/`](../client/) 中已清理的历史源码，填写自己的本地配置。当前文件清理不代表旧 Git 历史已清除。

## 当前版本

- Product / API：`28.0.2`
- Backend：`V2802 / 2.8.2`
- Remote：`hashmm.remote.v4`
- Database Schema：`28`
- 推荐 Python：`3.12`

版本事实源：[`../client/hashmm/release-manifest.json`](../client/hashmm/release-manifest.json)

## 首次部署

```bash
mkdir -p /root/autodl-tmp
cd /root/autodl-tmp

git clone https://github.com/augety121/HashMM-RAG-Agent.git
cd HashMM-RAG-Agent/client
cp .env.example .env
chmod 600 .env

python -m pip install -r requirements.txt
chmod +x start-hashmm1.sh
./start-hashmm1.sh doctor
./start-hashmm1.sh
```

首次部署只复制 `.env.example`。已有服务器升级时，禁止用模板覆盖现有 `.env`。

## 安全覆盖升级

预打包下载已撤下。请在独立目录准备并测试源码候选，备份配置和数据库，再按实际部署方式切换代码。不能用示例配置覆盖已有 `.env`。

升级时必须保留：

- `.env`
- `data/` 与 ProjectVault 用户数据
- 本地模型目录
- FAISS / BM25 索引与知识库数据
- Cloudflare Tunnel 与 TURN 的服务器侧凭据

## 健康检查

```bash
curl -fsS http://127.0.0.1:6006/api/health
curl -fsS https://your-hashmm-domain.example/api/health
```

响应必须明确包含 `status`、`ready`、`version`、`release` 和关键组件状态。公网返回 200 只能证明 HTTP 链路可用，不能代替登录、跨端同步或 Remote 首帧验收。

## 生产安全边界

- Supabase issuer/project 必须与桌面端和 App 一致。
- `HASHMM_REQUIRE_AUTH=1`。
- `HASHMM_REQUIRE_SECURE_REMOTE=1`。
- 公网入口使用 HTTPS/WSS；Uvicorn 只监听受控本地地址。
- `HASHMM_TRUSTED_PROXIES` 只包含真实代理来源。
- CORS 只允许产品域名和必要的 loopback 开发地址。
- service-role key 只存在于服务器进程环境中。
- `.env` 权限保持 `0600`，禁止提交到 Git 或写入日志。

## V2802 验收清单

1. `./start-hashmm1.sh doctor` 无 FAIL。
2. 启动日志显示 `V2802 / 2.8.2`。
3. 本机与 HTTPS `/api/health` 均返回 200。
4. 同一账号桌面端与 App 能发现彼此。
5. Remote v4 同账号连接不重复请求普通桌面批准。
6. 验证码设备首次配对后可通过可撤销信任恢复。
7. App 收到真实远程首帧；不能只以 WebSocket 已连接作为成功依据。
8. 多用户对象、项目、会话、模型连接和任务保持 owner 隔离。
9. 关机、重启等危险动作仍要求独立确认。

## 回滚

代码回滚前保留失败日志、当前版本和配置备份，不要删除用户数据。恢复自己验证过的上一版代码与兼容配置；数据库 schema 已升级时按对应迁移文档修复，不能直接删除数据库。
