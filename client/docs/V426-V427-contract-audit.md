# V426 → V427 契约审计

## 已收口

| 边界 | V426 风险 | V427 行为 | 证据 |
|---|---|---|---|
| 写入幂等预留 | 数据库异常可能被当作未预留，副作用可以继续 | 抛出 `idempotency_unavailable` 并阻止工具执行 | `tests/test_stress_concurrency.py` |
| 写入幂等提交 | 外部写入后提交失败会被吞掉，调用方可能重复执行 | 返回 `uncertain / idempotency_commit_unavailable`，禁止自动重试 | `tests/test_stress_concurrency.py` |
| Python 代码审查 | 旧路径会产生无法审计的模型审查提示 | 仅输出 AST `independent_verify` trace | `tests/test_chain_executor_verification.py` |
| 验证器异常 | 编译器/验证器异常可能被当作通过 | 返回失败并保留错误类型 | `tests/test_quality_gates_fail_closed.py` |
| Agentic RAG | 检索/生成异常容易退化为空来源回答 | 返回结构化 `errors`，并在 trace 中标记 `seed_error` | `tests/test_uncertainty_gate.py` |

## 未由本地测试替代的验收

真实公网多设备、对称 NAT、TURN relay 覆盖率、24 小时稳定性、Windows Authenticode 和真实第三方 API 配额，仍需要部署环境的实测报告。V427 不把这些环境性条件写成“已通过”。

