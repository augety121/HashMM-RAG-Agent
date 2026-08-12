# HashMM Server V2802

本目录是服务器部署入口。服务端源码位于 [`../client/hashmm`](../client/hashmm)，启动脚本和依赖清单位于 [`../client`](../client)。正式服务器升级 ZIP 由 GitHub Releases 分发，不进入 Git 历史。

## 当前版本

- Product/API：`28.0.2`
- Backend：`V2802 / 2.8.2`
- Remote：`hashmm.remote.v4`
- Python：`3.12`

版本事实源：[`../client/hashmm/release-manifest.json`](../client/hashmm/release-manifest.json)。

## 升级原则

升级服务器代码时必须保留：

- `.env`
- `data/`
- 本地模型目录
- 向量索引、知识库数据和 ProjectVault 用户数据
- Cloudflare Tunnel 与 TURN 的服务器侧凭据

不要把这些内容复制回 GitHub。

## 推荐升级流程

```bash
cd /root/autodl-tmp

# 1. 备份当前配置；不要输出密钥值
cp -p .env .env.before-v2802
chmod 600 .env.before-v2802

# 2. 在本地校验从 Release 下载的 ZIP
sha256sum -c hashmm-server-V2802-*.zip.sha256

# 3. 解压覆盖源码，但不要删除 data、模型、索引或 .env
unzip -o hashmm-server-V2802-*.zip -d /root/autodl-tmp

# 4. 启动诊断与服务
chmod +x start-hashmm1.sh
./start-hashmm1.sh doctor
./start-hashmm1.sh
```

## 健康检查

```bash
curl -fsS http://127.0.0.1:6006/api/health
curl -fsS https://hashmm.hashlens.org/api/health
```

返回值必须明确包含当前版本和关键组件状态。公网入口应由 Cloudflare Tunnel 或同等 HTTPS 反向代理提供，Uvicorn 只监听受控本地地址。

## 必要配置边界

- Supabase issuer/project 必须与桌面端和 App 使用的账号系统一致。
- `HASHMM_REQUIRE_AUTH=1`。
- 公网远程必须启用安全远程门禁和可信代理校验。
- CORS 只允许真实产品域名与必要的 loopback 开发地址。
- service-role key 仅在服务器进程环境中存在，禁止写入 App、README、日志或安装包。

## 发布验收

1. 启动日志显示 `V2802`。
2. `/api/health` 本机和 HTTPS 域名均返回 200。
3. 同一账号桌面端和 App 可以发现彼此。
4. Remote v4 同账号连接不重复请求普通桌面批准。
5. 验证码设备首次配对后可以通过可撤销信任恢复。
6. App 收到真实首帧；不能只以 WebSocket 已连接作为成功依据。
7. 多用户对象、项目、会话和任务仍保持 owner 隔离。
