# HashMM 自有 TURN 部署

这是一套生产模板，不是“已经替你部署完成”的声明。需要一台具有固定公网 IP 的 Linux 主机、一个解析到该主机的域名和有效 TLS 证书。

1. 为 UDP/TCP 3478、TCP 5349 和 UDP 49160–49200 配置安全组与主机防火墙。若缩窄中继端口范围，需要按并发量进行容量验证。
2. 生成至少 32 字节随机 shared secret，只写入 HashMM 服务端私有 `.env`：
   `HASHMM_TURN_SHARED_SECRET=...`。
3. 同一 secret 通过环境变量传给渲染命令，生成权限为 0600 的 `runtime/turnserver.conf`：
   `python scripts/render-turn-config.py --realm turn.example.com --external-ip 203.0.113.10 --cert /certs/fullchain.pem --pkey /certs/privkey.pem`。
4. 复制 `.env.example` 为 `.env`，把镜像改成经过审核的固定版本或 digest，把证书目录设为宿主机真实目录。
5. 启动：`docker compose --env-file .env up -d`。
6. HashMM 后端配置：
   `HASHMM_TURN_URLS=["turn:turn.example.com:3478?transport=udp","turn:turn.example.com:3478?transport=tcp","turns:turn.example.com:5349?transport=tcp"]`
   `HASHMM_REMOTE_REQUIRE_TURN=1`
   `HASHMM_REQUIRE_SECURE_REMOTE=1`
   `HASHMM_PUBLIC_URL=https://你的HashMM域名`
7. 使用已登录账号调用 `GET /api/remote/v4/preflight` 检查控制面与 TURN 配置声明；该结果只证明配置已加载。最终必须在两端诊断中同时看到 relay candidate/selected pair 和首帧里程碑，才能证明真实网络可用。

HashMM 按 coturn TURN REST 约定向已认证设备签发短期用户名和 HMAC-SHA1 密码。shared secret 不会发给客户端。模板默认禁止通过中继访问私网、环回、链路本地和组播目标。

Docker 的 host network 是 Linux 专用。本模板未为 Windows Docker Desktop 宣称等价行为。TLS 证书续期、TURN 带宽、并发配额、出口账单和 DDoS 防护必须由真实部署环境验收。
