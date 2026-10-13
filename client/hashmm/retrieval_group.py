"""检索相关模块的聚合入口（P2-1 模块归类）。

零破坏归类：原模块位置不动（旧 import 照常工作），额外提供按职责分组的整洁入口：
    from hashmm.retrieval_group import kb_search_bridge, QueryPlanner, AutoRetriever

注意：项目已有 `hashmm/retrieval/` 子包（pipeline 等），本入口命名 `retrieval_group`
以避免与之冲突，仅作"顶层散落检索模块"的聚合发现入口。

归入的顶层检索模块：
  - retriever_bridge    检索桥接（kb_search_bridge 主入口）
  - retrieval_pipeline  BM25+向量+RRF+重排主链
  - query_planner       查询规划（多步检索）
  - auto_retrieval      自动检索上下文
  - chat_retrieval      对话检索策略
  - retrieval_quality   检索质量评估
"""
from __future__ import annotations

try:
    from hashmm.retriever_bridge import init_retriever, get_pipeline, kb_search_bridge  # noqa: F401
except Exception:
    pass

try:
    from hashmm.query_planner import QueryPlanner, ExecutionPlan, SearchStep  # noqa: F401
except Exception:
    pass

try:
    from hashmm.auto_retrieval import AutoRetriever, RetrievalContext  # noqa: F401
except Exception:
    pass


def modules() -> list[str]:
    return [
        "retriever_bridge", "retrieval_pipeline", "query_planner",
        "auto_retrieval", "chat_retrieval", "retrieval_quality",
    ]
