# HashMM 测试套件

标准化 pytest 套件 —— **一个功能一个测试文件，可反复跑**。

> **与历史测试共存**：项目原有大量 `test_v17_phaseXX.py`（"每做一个 phase 测一次"的历史测试，
> 上千项）。本套件的 `test_<功能>.py` 与它们**文件名不同、互不冲突**，是更标准化的补充。
> 历史测试若有"模块顶层执行建数据"的反模式（collection 阶段就崩、会中断整个会话），
> 见根目录 `PYTEST_FIX_GUIDE.md` 的修复方法。

## 跑测试

```bash
# 只跑本标准套件（推荐，避免历史测试的 collection error 干扰）
python -m pytest tests/test_llm_router.py tests/test_hooks.py tests/test_tenancy.py \
  tests/test_kg_evolution.py tests/test_design_render.py tests/test_skills_design.py \
  tests/test_contracts_api.py tests/test_eval_timeline.py

# 全部（若某文件 collection 出错，加 --continue-on-collection-errors 不中断其余）
python -m pytest --continue-on-collection-errors

# 只跑单元测试
python -m pytest -m unit

# 改检索/KG 前必跑回归
python -m pytest -m regression

## 测试分层（标记）

| 标记 | 含义 | 何时跑 |
|---|---|---|
| `unit` | 纯逻辑，无需 GPU/模型/data | 任何时候，最快 |
| `contract` | 接口返回结构稳定性（/v1 API、MCP、OTLP） | 改接口前后 |
| `regression` | 主检索链等关键路径，防改坏 | **改检索/KG 前必跑** |
| `needs_data` | 需真实 data/ 语料 | 有 data 时；无则自动跳过 |
| `needs_gpu` | 需 GPU/模型 | 有 GPU 时；无则自动跳过 |
| `needs_render` | 需渲染工具（playwright 等） | 装了工具时 |

## 测试文件 → 功能映射

| 文件 | 覆盖的功能 |
|---|---|
| `test_llm_router.py` | F11 云-本地路由 + 任务路由可配置 |
| `test_hooks.py` | 多生命周期 hooks（PreTool/PostTool/PreCompact/SubagentStop） |
| `test_tenancy.py` | 多租户隔离 + 配额 |
| `test_kg_evolution.py` | 自进化 KG 待审区 |
| `test_design_render.py` | 受控设计渲染器（白名单安全） |
| `test_skills_design.py` | skill 意图技能 + 设计能力（huashu-design 迁移） |
| `test_contracts_api.py` | 对外 /v1 API + MCP + OTLP 契约 |
| `test_retrieval_regression.py` | 检索主链回归（needs_data） |
| `test_eval_timeline.py` | eval 保真 + agent 时间线 |

## 设计原则

1. **隔离**：需 DB 的测试用临时 sqlite（`tmp_db` fixture），绝不碰真实 data。
2. **独立**：每个测试可单独跑、可反复跑、无副作用、无顺序依赖。
3. **优雅跳过**：缺 GPU/data/工具的测试自动跳过（不是失败）。
4. **默认关验证**：每个新特性都测"关闭时零行为变化"。

## 沙箱验证（无 pytest 时）

`tests/_mini_runner.py` 是一个轻量 pytest 兼容运行器，用于在没装 pytest 的环境快速验证测试逻辑。
真机请用 `python -m pytest`（功能完整）。
