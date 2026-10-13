# 用免费 CI 跑 Docker 基准，结果回传后端

## 你的处境
你只有一台服务器（AutoDL 容器），**不支持 Docker / 嵌套虚拟化**。而 SWE-bench、
WebArena、OSWorld、Terminal-bench 这四个基准**必须有 Docker** 才能跑出官方口径的分。
所以这些分数没法在你的机器上直接产出。

## 解决办法：把 Docker 部分外包给免费 CI
用 **GitHub Actions**（每月 2000 分钟免费额度、Ubuntu runner 自带 Docker）在云端跑这些
基准，**模型推理仍走你自己的后端/模型**（不额外花钱），跑完把分数 POST 回你的后端 →
自动进「和大厂对比」和对比图。

```
GitHub Actions (有 Docker)                你的服务器 (有模型, 有对比库)
┌─────────────────────────┐              ┌──────────────────────────┐
│ 1. 下载基准数据集         │              │                          │
│ 2. Docker 起环境         │──推理请求───▶│  你的模型 (OpenAI 兼容)   │
│ 3. 跑 agent 改代码/操作   │◀──返回──────│                          │
│ 4. 官方判分             │              │                          │
│ 5. POST 分数 ───────────┼──/bench/ingest▶│  写入 bench_runs         │
└─────────────────────────┘              │  → 「和大厂对比」自动显示  │
                                         └──────────────────────────┘
```

## 三步配置

### ① 后端开启结果接收（在启动脚本里加一行）
```bash
export HASHMM_BENCH_INGEST_TOKEN=$(openssl rand -hex 24)   # 生成一个长随机串，记下来
```
没配这个 token，`/bench/ingest` 端点会拒绝一切请求（防止别人往你库里灌假分数）。
另外确保你的模型有一个 **OpenAI 兼容入口**（vLLM 默认就有：`http://IP:8000/v1`）。

### ② 把代码推到 GitHub，配 5 个 Secrets

**方式 A（推荐 · 服务器网慢就用这个）：上传"CI 最小包"**
CI 只需要 `hashmm/`（去 skills）+ `scripts/remote_bench_runner.py` + `.github/workflows/` +
`requirements.txt`，约 **6MB**（完整源码 37MB）。交付包里的 `HashMM-GitHub-CI最小包.zip`
就是这个——解压后在**你自己电脑**上按包内 README.md 用网页拖拽或 git 推到你的仓库即可，
不用占服务器带宽。

**方式 B：在服务器上推完整源码**
```bash
cd /root/autodl-tmp/hashmm          # 解压源码的目录
bash setup_github.sh https://github.com/你的用户名/仓库名.git
```
`setup_github.sh` 会自动 git 初始化+提交+推送，并在推送前**扫描密钥**（发现会被推上去的
token/service_role 等会立即中止），`.gitignore` 也已排除密钥/模型/数据/venv。
先去 https://github.com/new 建一个【空】Private 仓库，再去 https://github.com/settings/tokens
生成一个 classic token（勾 repo 权限）当密码用。详细图文见脚本头部注释。

> 私有仓库没问题：GitHub Actions 对私有仓库同样有每月 2000 分钟免费额度。

**配 5 个 Secrets**（仓库 → Settings → Secrets and variables → Actions → New repository secret）：

| Secret | 值 |
|---|---|
| `HASHMM_BACKEND_URL` | 你后端公网地址，如 `http://111.115.7.14:20014` |
| `HASHMM_BENCH_INGEST_TOKEN` | 和①里同一个 token |
| `HASHMM_OPENAI_BASE` | 模型 OpenAI 兼容入口，如 `http://你的IP:8000/v1` |
| `HASHMM_OPENAI_KEY` | 模型 key（没有填 `EMPTY`） |
| `HASHMM_OPENAI_MODEL` | 模型名，如 `Qwen2.5-7B-Instruct` |

> 注意：CI 要能访问你后端和模型的公网地址。AutoDL 的外部映射端口（你现在是 20014）就是干这个的。

### ③ 跑
仓库 → Actions → 「Docker Benchmarks → 回传后端」→ Run workflow → 填基准名
（如 `swebench terminal`）→ 运行。跑完回你后端「测试中枢 → 外部基准对标 → 和大厂对比」
就能看到这几个基准的分数了。

## 不想用 GitHub？其它免费/低成本 Docker 环境同理
`scripts/remote_bench_runner.py` 不依赖 GitHub，任何有 Docker 的机器都能跑：
- **GitHub Codespaces**：每月 60 小时免费，容器内直接 `python scripts/remote_bench_runner.py ...`
- **Gitpod / 本地带 Docker 的电脑 / 便宜的云主机**：设好上面 5 个环境变量后同样一条命令。

## 手动本地验证接收端点通不通（不跑真基准）
```bash
curl -X POST http://你的后端/api/selftest/bench/ingest \
  -H "Authorization: Bearer <你的token>" -H "Content-Type: application/json" \
  -d '{"bench_id":"swebench","name":"SWE-bench Verified","score_pct":42.0,"passed":21,"total":50,"kind":"official"}'
# 返回 {"ok":true,...} 就说明通了；然后去「和大厂对比」应能看到这条。
```

## 安全说明
- `/bench/ingest` 用 Bearer token 常数时间比较鉴权，未配 token 时整个端点禁用。
- 回传的分数会被**强制标注来源为"远程CI"**（detail 和 breakdown 里都写明"本机无Docker，
  外部Docker环境跑出并回传"），透明可查，不会和你本机跑的分混淆。
