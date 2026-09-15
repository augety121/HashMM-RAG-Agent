# HashMM 长程行为测试日志（长任务 / 长代码 / 多支对话 / 幻觉 / 上下文压缩）
- 时间：2026-07-13 11:04:46
- 用例数：20
==================================================================
  ✓ test_artifact_files_tracked_across_100_turns
  ✓ test_compaction_is_deterministic
  ✓ test_compaction_triggers_and_bounds_budget
  ✓ test_compaction_zero_change_under_budget
  ✓ test_enumeration_and_derived_counts_not_flagged
  ✓ test_faithfulness_ratio_on_fixed_mini_dataset
  ✓ test_goal_never_drifts_over_growing_conversation
  ✓ test_grounded_answer_not_false_flagged_strict
  ✓ test_hallucinated_number_is_caught_strict
  ✓ test_irrelevant_injection_does_not_pollute_goal_or_recent
  ✓ test_llm_compaction_falls_back_and_preserves_recent
  ✓ test_long_task_goal_and_progress_survive_compaction
  ✓ test_long_task_hallucination_caught_after_compaction
  ✓ test_multi_branch_same_chat_isolation
  ✓ test_recent_turns_preserved_verbatim
  ✓ test_recompaction_is_idempotent_no_nesting
  ✓ test_regenerate_branch_keeps_prefix_and_diverges
  ✓ test_retrieval_solidity_ranks_evidence_strength
  ✓ test_self_consistency_flags_drifting_samples
  ✓ test_summary_has_no_hallucinated_tokens
------------------------------------------------------------------
## 场景数字画像
- 100 轮会话：11964 tok → 压缩后 7 条 / 1660 tok（降 86%）
- 多支对话：A 支压缩后含[B]内容=False（应 False）；B 支含[A]内容=False（应 False）；两支均保留'限流器'目标=True
- 长任务幻觉：编造 table_99/999 秒 → strict 判 ok=False（应 False），number_unsupported=1 条
==================================================================
结果：PASS=20  FAIL=0
