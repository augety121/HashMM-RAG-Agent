# HashMM V1200 大版本总 Spec

状态：已实施到源码，外部网络与 24 小时实机门禁待部署环境验收  
版本：Backend/SDK `V1200 / 1.2.0`，Desktop/WebUI/MCP/Native `3.2.0`  
核心协议：`secure-remote-workspace/3.0`、`agent-retrieval-fabric/1.0`

## 1. 本版目标与非目标

V1200 将两个此前分散的能力收敛为可验证的生产边界：

1. Secure Remote Workspace 3.0：所有公网会话必须经过 HTTPS/WSS、统一身份、owner 校验、短期 TURN 凭据、持久审计和真实公网验收。
2. Agent Retrieval Fabric 1.0：RAG-Agent 使用统一的多提供商检索运行、证据合同、时效标注、来源互证和本地 RAG 对照，不再由每个工具维护一套搜索字符串。

本版不实施量化 Agent，不承诺复制 Google、百度或豆包的私有索引规模，也不把搜索排序、模型摘要或多来源重复结果称为“事实为真”。

## 2. Secure Remote Workspace 3.0

### 2.1 信任边界

- 身份：公网 API 使用 HashMM/Supabase 已验证访问令牌；对象继续执行 owner-scoped 查询，未找到与越权返回相同 404。
- 传输：生产环境只接受 `HASHMM_PUBLIC_URL=https://...`，并要求 `HASHMM_REQUIRE_SECURE_REMOTE=1`。
- 代理：`X-Forwarded-Proto` 只在直接连接地址属于 `HASHMM_TRUSTED_PROXIES` 时生效；默认只信任 IPv4/IPv6 loopback。
- RTC：生产 readiness 要求动态 TURN 短期凭据；共享密钥不下发客户端。
- 凭据：模型 API Key 不再写入 Supabase `app_config`。SQL 迁移删除历史 `direct_llm`，RLS 只允许读取白名单内的非敏感发现项。
- JWT：Supabase JWKS 校验同时约束签名、过期、audience 和项目 issuer，防止其他 Supabase 项目签发的有效 token 被混用。

### 2.2 公网拓扑

推荐拓扑为：

```text
Desktop / App
  -> HTTPS/WSS + Supabase access token
Cloudflare Tunnel 或受控 HTTPS 网关
  -> localhost:6006
HashMM
  -> owner ACL / audit / WorkRun
  -> coturn 临时凭据（需要远程媒体/控制时）
```

仓库提供 `deploy/secure-remote/cloudflared-config.yml.example` 与 `hashmm-v1200.env.example`。Tunnel 是出站连接，适用于服务器没有独立可路由公网 IP 的场景。

`111.115.7.14:20014` 只能在校园网访问属于上游 NAT、ACL、端口映射或路由事实；源码不能改变运营商路由。必须选择以下一种真实部署：

- 在 AutoDL 控制台使用平台给出的“自定义服务”完整公网地址并配置 HTTPS；或
- 用自有域名创建 outbound Tunnel，将域名路由到 `127.0.0.1:6006`。

在域名、Tunnel 凭据和 AutoDL 控制面尚未提供前，不能声称校外公网已经打通。

### 2.3 readiness v2

`GET /api/remote/readiness` 的 `production_ready` 只有以下事实全部为真才成立：

- `secure_transport_enforced`
- `public_https_url`
- `supabase_identity`
- `temporary_turn_credentials`
- `public_multi_device_acceptance`（30 天内）
- `twenty_four_hour_soak`（14 天内）
- `persistent_audit`

因此页面配置完成不等于生产验收完成。

## 3. Agent Retrieval Fabric 1.0

### 3.1 数据流

```text
SearchRequest
  -> provider selection + deadline
  -> parallel provider adapters
  -> canonical URL + dedupe
  -> timestamp/freshness annotation
  -> cross-provider listing corroboration
  -> optional local RAG comparison
  -> durable SearchRun + Events + Evidence
  -> API / Chat web_search compatibility facade
```

所有网页、标题、摘要和远程错误均是不可信输入。用户可见响应只返回稳定错误类别，不返回供应商异常中的密钥、请求正文或内部地址。

### 3.2 请求模式

- `fast`：一个可用提供商，低延迟。
- `verified`：最多三个已配置提供商并行，执行条目级互证。
- `deep`：最多六个已配置提供商，扩大召回。
- `rag_live`：在 verified 基础上调用 owner/project 约束的本地 RAG，并输出差异线索。

`rag_live.possible_update` 只是词项差异提示，不能自动宣布知识库过期，更不能绕过人工审核写回生产知识库。

### 3.3 提供商矩阵

| Provider | 定位 | V1200 状态 |
|---|---|---|
| 百度千帆 AI Search | 官方 raw/AI web search | 已接适配器；需真实账号密钥验收 |
| Brave Search | 独立 Web 索引 | 已接适配器；需真实账号密钥验收 |
| Exa | 语义/内容检索 | 已接适配器；需真实账号密钥验收 |
| Google Search Grounding | Gemini 托管 Grounding | 已接引用抽取；不是原始 Google 索引 API |
| Tavily | Agent 搜索 | 已接适配器 |
| Serper | Google 结果聚合兼容 | 已接适配器，不冒充 Google 官方 API |
| 豆包兼容接口 | 用户已有兼容服务 | 保留 Beta 标识，不冒充火山官方 SLA |
| DuckDuckGo | 无密钥兜底 | 已接；网络/限流可能降级 |
| Bing Search API | 已退役 | 从执行顺序移除，能力端点显式返回 retired |

参考的上游事实以官方文档为准：[AutoDL 端口映射](https://www.autodl.com/docs/port/)、[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectivity-options/)、[百度千帆 AI Search](https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5)、[Google Search Grounding](https://ai.google.dev/gemini-api/docs/google-search)、[Bing Search API 退役公告](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement)。

### 3.4 API

- `GET /api/v1/search/providers`：当前账号可用性和提供商类型。
- `POST /api/v1/search`：创建并执行 owner-scoped SearchRun；`background=true` 先返回 queued run，客户端可轮询事件或取消。
- `GET /api/v1/search/{run_id}`：读取持久运行；越权与不存在均 404。
- `GET /api/v1/search/{run_id}/events?after=`：增量读取有序事件。
- `POST /api/v1/search/{run_id}/cancel`：仅非终态可取消。
- `POST /api/v1/search/{run_id}/resume`：失败/取消/部分完成创建新的运行并记录来源谱系。

请求示例：

```json
{
  "query": "目标问题",
  "mode": "rag_live",
  "max_results": 10,
  "freshness_days": 7,
  "project_id": "owner-visible-project",
  "background": true
}
```

### 3.5 证据语义

- `corroborated_listing`：不同提供商返回同一 canonical URL 或同标题条目。
- `single_source`：只有一个来源且 URL/摘要齐全。
- `insufficient_metadata`：缺 URL 或摘要。
- `fresh/stale/unknown`：仅由可解析发布时间与请求时间窗计算。

这些字段是审计信息，不是 Truth Score。最终回答仍必须逐主张引用，并对冲突、时效不明与单一来源明确降级。

## 4. 持久化与安全

- `search_runs` 保存 owner、请求、状态、稳定错误码和最终结构化结果。
- `search_run_events` 保存单调序号、provider 完成/失败与状态迁移。
- 搜索密钥继续使用现有 `secrets_crypto` 加密，不改变 pip 依赖集合。
- 普通用户配置按 owner 隔离；平台级配置仍限管理员。
- 自定义 provider endpoint 默认拒绝，只有管理员显式设置 `HASHMM_ALLOW_CUSTOM_SEARCH_ENDPOINTS=1` 才开放；生产默认避免把后端变成密钥转发器。

## 5. 前端与 Chat 集成

- 设置/插件页升级为 Agent Retrieval Fabric 提供商选择器，支持账号级保存、启停、真实链路探测和删除。
- 管理后台平台检索移除 Bing，增加百度、Brave、Exa、Gemini、Serper、Tavily 和豆包选择。
- 旧 `web_search` 工具成为兼容门面，输出 SearchRun ID、来源和编号证据；账号显式豆包配置继续优先，避免升级改变用户行为。
- 新 API 可被后续专用检索工作台和其他 Agent 使用；本轮不建立量化 Agent 专属逻辑。

## 6. 生产验收门

发布声明必须分别通过：

1. Python 检索/安全/授权回归和全量测试。
2. 前端测试、TypeScript、production build。
3. Desktop Node 与 source-only release gate。
4. 原生安装器完整流水线，EXE 的 SHA-256 同时匹配 `.sha256` 和 `.release.json`。
5. 每个启用搜索 provider 用真实密钥执行 canary，并记录延迟、结果数与稳定错误类别。
6. 校外网络两台设备通过 HTTPS/WSS、Supabase 登录、TURN relay 双端证据和 24 小时 soak。

未完成第 5/6 项时，只能称“源码实现/待环境验收”，不能称提供商或公网生产可用。SHA-256 只证明字节完整性，不代表 Authenticode 发布者身份。
