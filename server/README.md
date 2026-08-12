# HashMM Server V2802

本目录提供经过清单与 SHA-256 校验的 HashMM V2802 Linux / AutoDL 服务器升级包，以及不会覆盖用户数据的部署说明。

## 下载

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| [`hashmm-server-V2802-20260812-150306.zip`](releases/V2802/hashmm-server-V2802-20260812-150306.zip) | 8.02 MiB | `0B4FF8B8A160CA205E023A40EBBE82DBD712538B34E8FDF454977D07B1C1C001` |

校验文件：[`hashmm-server-V2802-20260812-150306.zip.sha256`](releases/V2802/hashmm-server-V2802-20260812-150306.zip.sha256)

```bash
sha256sum -c hashmm-server-V2802-20260812-150306.zip.sha256
```

## 包内容

服务器包包含：

- `hashmm/` 后端、RAG、Agent、Remote 和 Provider Fabric
- 启动脚本与 Python 依赖清单
- 协议契约、数据库迁移、插件、技能和部署示例
- `SERVER-PACKAGE.json` 文件级 SHA-256 清单
- V2802 版本事实源与发布说明

服务器包明确排除：

- 桌面端、Web UI 和原生安装器
- 测试运行缓存和构建产物
- `.env`、真实密钥、令牌与设备凭据
- 用户数据、日志、ProjectVault、模型、索引和旧压缩包

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

unzip hashmm-server-V2802-20260812-150306.zip
cp .env.example .env
chmod 600 .env

python -m pip install -r requirements.txt
chmod +x start-hashmm1.sh
./start-hashmm1.sh doctor
./start-hashmm1.sh
```

首次部署只复制 `.env.example`。已有服务器升级时，禁止用模板覆盖现有 `.env`。

## 安全覆盖升级

```bash
cd /root/autodl-tmp

# 1. 备份配置；不要打印密钥值
cp -p .env .env.before-v2802
chmod 600 .env.before-v2802

# 2. 校验升级包
sha256sum -c hashmm-server-V2802-20260812-150306.zip.sha256

# 3. 覆盖代码，不删除运行数据
unzip -o hashmm-server-V2802-20260812-150306.zip -d /root/autodl-tmp

# 4. 诊断并启动
chmod +x start-hashmm1.sh
./start-hashmm1.sh doctor
./start-hashmm1.sh
```

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

代码回滚前先保留失败日志和当前版本清单，不要删除用户数据：

```bash
cd /root/autodl-tmp
cp -p .env.before-v2802 .env
chmod 600 .env
```

然后恢复上一版经过校验的服务器包并重新运行 `doctor`。数据库 schema 已升级时，应按照对应迁移文档执行向前修复，不要直接删除数据库文件。
