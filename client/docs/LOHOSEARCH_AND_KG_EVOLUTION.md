# LoHoSearch 与知识—Agent 共进化

## LoHoSearch

HashMM 不内置、不伪造 LoHoSearch 题目。运行前必须设置 `HASHMM_LOHOSEARCH_DATA`，并提供同名 `.manifest.json`（或 `HASHMM_LOHOSEARCH_MANIFEST`），其中至少包含 `dataset_id`、`case_count`、`snapshot_id`、`source_url`、`sha256`。

- Smoke：最多 20 题，只验证接线与纵向趋势。
- CI：最多 50 题，使用固定快照回归。
- Full：544 题；只有来源元数据完整、SHA-256 匹配且确实运行 544 题时才标记可与该快照比较。
- 当前默认使用标准化 exact match，报告准确率、pass@1、平均工具步数、p50/p95 延迟和分条件结果。没有证据标注时，`evidence_precision` 与 `unsupported_claim_rate` 明确返回 `null`。

配置示例：

```powershell
$env:HASHMM_LOHOSEARCH_DATA='D:\benchmarks\lohosearch.jsonl'
```

## KG 共进化安全门

搜索得到的新事实不会直接写主图：

1. `POST /api/evolution/kg/{project_id}/candidates` 写入 owner/project 隔离的候选区，要求来源、SHA-256、页码/Chunk 等 provenance。
2. 低置信度拒收；与当前激活版本同 head/relation、不同 tail 的事实进入 `conflict`。
3. `review` 必须由 owner 明确批准；冲突需要 `resolve_conflict=true`。
4. `versions` 晋升要求所有候选已批准、evaluation 明确 `passed=true`、父版本没有变化。
5. 晋升生成内容寻址的不可变 KG 版本；激活指针可回滚。

该控制面只管理隔离候选和版本事实，不自动写 `graph.json`。生产主图同步必须由单独的验证发布步骤消费激活版本。

