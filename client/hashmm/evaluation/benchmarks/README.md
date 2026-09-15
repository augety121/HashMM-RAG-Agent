# HashMM 外部基准对标 —— 操作步骤

## 第 0 步：先自检环境（一眼看清你能跑哪些）

```bash
cd /root/autodl-tmp/hashmm/evaluation/benchmarks
bash check_env.sh
```
它会告诉你：Docker 能不能用、每个基准的数据/工具就绪没、网络通不通、Serper 配了没。

## 关于"我的容器为什么不能 Docker"

你的 AutoDL 是**容器实例**。SWE-bench / Terminal-bench 的官方评测**要给每个任务再单独起一个
Docker 容器**（容器套容器 = docker-in-docker），这需要**特权模式**，AutoDL 的算力实例默认不给。
`check_env.sh` 会明确告诉你你这台到底行不行。

**但这不影响大局** —— 下面 4 个基准**根本不需要 Docker**，在你的容器里直接能跑出真实可对标分数：
BFCL、τ²-bench、GAIA、Kotlin。SWE-bench 没 Docker 会自动走"本机 venv 模式"。

## 如果你看到一堆莫名的 100%

那是**早期 smoke 管线自检误入库的假分数**（老代码没记 kind）。点卡片上的 **🧹 清理历史假分**
按钮，或 `POST /api/selftest/bench/purge`，一键清掉。现在的代码：smoke 绝不入库、生成报告前也会自动清污染。

---

# HashMM 外部基准对标 —— 详细操作步骤

## 先说清楚：为什么你之前看到"全是 100%、全都超过顶尖 agent"

**那是我的设计 bug，不是你没搞对。** 三个原因叠加：

1. **你的后端跑在 smoke 模式**。你是在**后端已经启动之后**才 `export HASHMM_BENCH_MODE=full` 的——
   环境变量对**已经在跑的进程无效**。所以中枢里跑的全是 smoke（界面徽标也确实写着"管线验证(smoke)"）。
2. **smoke 本来只是"管线自检"**（跑 1 个玩具任务，比如"写个 add 函数"），你的 deepseek 当然能过 →
   1/1 = **100%**。旧代码却把这个 100% 拿去和 Claude Code 的公开分对比 → "已达到/超过全部参照"。
   **这是假分数，已经修掉**：现在 smoke **不产生任何分数、不做任何对标**。
3. **SWE-bench / Terminal-bench 的真集需要 Docker**，而你的 AutoDL 容器**没有 Docker**
   （install.sh 日志里明确报了 `!! 未检测到 docker`）。没 Docker 就跑不了真集——现在会**如实跳过**。

修完之后，分数分成三级，界面上也分开显示：

| 类型 | 含义 | 会不会和顶尖 agent 对标 |
|---|---|---|
| **official** | 跑**官方数据集** | ✅ 会 |
| **builtin** | 内置最小集（我自造的十几个用例） | ❌ 不会（玩具题拿高分≠达到顶尖水平） |
| **smoke** | 只验证"agent 能不能被这个基准驱动" | ❌ **根本不出分数** |

---

## 第一步（强烈建议先做）：跑 BFCL —— 不需要 Docker，装完立刻出**真实可对标分数**

BFCL（Berkeley Function-Calling Leaderboard）的**官方数据就在 GitHub 的 gorilla 仓库里**，
你的机器能连 GitHub（SWE-bench、terminal-bench 都 clone 成功了），所以**不需要 Docker、不需要 HuggingFace**。

```bash
cd /root/autodl-tmp/hashmm/evaluation/benchmarks
bash install.sh bfcl
```

装完确认一下数据在：
```bash
find ~/hashmm-benchmarks/gorilla -path '*data*' -name '*simple*.json' | head
```

**然后——关键的一步——让环境变量对【后端进程】生效**：

```bash
# 编辑你的启动脚本，把这两行加到启动命令【之前】
vim ~/autodl-tmp/start-hashmm.sh
```
在 `start-hashmm.sh` 里加上：
```bash
export HASHMM_BENCH_HOME=/root/hashmm-benchmarks
export HASHMM_BENCH_MODE=full
```

**重启后端**（不重启，环境变量不会生效）：
```bash
# Ctrl+C 停掉当前后端，然后
./start-hashmm.sh
```

最后：**测试中枢 →「外部基准对标」→ 只勾「工具调用基准（BFCL）」→ 运行自测**。

这次你会看到：
- 徽标显示 **`真集模式(full)`**（而不是 smoke）
- 卡片上标 **`官方数据集·可对标`**（绿色）
- 一个**真实的分数**（不会再是 100%——BFCL 是真题，deepseek 大概率落在 70–90% 区间）
- 分数条上有 Claude Code / 顶尖模型的**参照刻度**，以及你的**真实位置**

---

## 第二步（可选）：SWE-bench / Terminal-bench —— 需要 Docker

```bash
bash install.sh swebench    # 独立 venv + 数据集（走 hf-mirror 镜像，绕过 huggingface 不通）
bash install.sh terminal    # 独立 venv
```

### ⚠️ Docker 不可用怎么办
这两个基准的官方评测**每个任务都在 Docker 容器里跑**（这是它们隔离环境的方式），
没有 Docker 就**无法产出真实分数**——这不是 HashMM 的限制，是基准本身的要求。
AutoDL 的容器实例通常**不提供宿主 Docker**（不能嵌套）。你有三个选择：

- **A（推荐）**：先靠 **BFCL** 拿到真实可对标分数（见第一步），SWE-bench 以后有条件再说；
- **B**：换一台**支持 Docker 的机器**（自有服务器、或支持 dind 的云实例）跑 SWE-bench；
- **C**：用 SWE-bench 官方的 **Modal 云端后端**（需要 Modal 账号 token）。

在没有 Docker 的机器上，这两项会**如实跳过并告诉你原因**，**绝不会假装给你一个分数**。

---

## 目录结构

| 文件 | 作用 |
|---|---|
| `registry.py` | 基准清单（中枢据此渲染勾选项） |
| `runner.py` | 统一入口；**决定分数能不能对标（official/builtin/smoke）** |
| `adapter.py` | 把 HashMM agent 包装成基准驱动需要的接口 |
| `bfcl.py` | ★ **真实 BFCL**（官方数据 + 简化 AST 判分，无需 Docker/HF） |
| `tool_calling.py` | 内置最小集（没装 BFCL 数据时的退路，**不可对标**） |
| `smoke.py` | 管线自检（**不产生分数**） |
| `external.py` | SWE-bench / Terminal-bench 的探测与降级 |
| `swebench_full.py` | SWE-bench 真机 harness：clone→改→git diff→predictions→官方 harness→Pass@1 |
| `terminal_full.py` | Terminal-bench 真机 harness：tb run → 解析 results → 成功率 |
| `leaderboard.py` | 顶尖 agent 公开分参照 |
| `trend.py` | 每次**真实**跑分入库（smoke 不入库），看迭代趋势 |
| `report.py` | 一键生成对标报告（markdown） |
| `install.sh` | 安装脚本（**全部装进独立 venv/目录，绝不碰你的主环境**） |

## 判分口径的诚实说明
- **BFCL**：用官方数据，**简化 AST 判分**（函数名 + 参数值命中官方 possible_answer），覆盖
  `simple`/`multiple` 两类。与官方评测脚本可能有细微出入（官方还有 live/parallel/multi-turn 类别
  与更细的类型规则），所以标为"官方数据·简化AST"，可做**量级对比**，不等同官方复现值。
- **SWE-bench**：直接调官方 harness 在 Docker 里跑，Pass@1 就是官方口径。
- **leaderboard 参照**：取自各基准官方榜单/供应商公开报告的量级值，随版本变化，以官方最新为准。
