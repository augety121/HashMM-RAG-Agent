"""Community detection and summarization for the knowledge graph.

Uses Louvain/greedy modularity for community detection (no leidenalg dependency),
then generates LLM summaries for each community to enable global-level retrieval.
"""
from __future__ import annotations
import json
from typing import Any, Callable
from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.community")


COMMUNITY_SUMMARY_PROMPT = """你是学术知识总结专家。以下是一组相关实体和它们之间的关系。
请为这个知识社区写一段 100-200 字的摘要，概括其核心主题和关键关系。

## 实体
{entities}

## 关系
{relations}

请直接输出摘要文本，不要 JSON 或 markdown："""


class CommunityManager:
    """Detect communities in the KG and generate summaries.

    Uses networkx built-in community detection (no extra dependencies).
    Summaries are generated using LLM for global-level retrieval.
    """

    def __init__(self, llm_fn: Callable | None = None):
        self.llm_fn = llm_fn
        self.communities: dict[int, CommunityInfo] = {}

    def set_llm(self, llm_fn: Callable):
        self.llm_fn = llm_fn

    def detect_communities(self, graph) -> dict[int, list[str]]:
        """Detect communities using greedy modularity (works with DiGraph).

        Args:
            graph: KnowledgeGraph instance

        Returns:
            {community_id: [node_key1, node_key2, ...]}
        """
        import networkx as nx

        g = graph.graph
        if g.number_of_nodes() < 3:
            return {}

        # Convert to undirected for community detection
        ug = g.to_undirected()

        # Remove isolated nodes
        ug.remove_nodes_from(list(nx.isolates(ug)))
        if ug.number_of_nodes() < 3:
            return {}

        # Use greedy modularity (built-in, no extra deps)
        try:
            from networkx.algorithms.community import greedy_modularity_communities
            communities_gen = greedy_modularity_communities(ug)
            communities_list = [list(c) for c in communities_gen]
        except Exception:
            # Fallback: label propagation
            try:
                from networkx.algorithms.community import label_propagation_communities
                communities_list = [list(c) for c in label_propagation_communities(ug)]
            except Exception:
                logger.warning("Community detection failed")
                return {}

        # Filter tiny communities (< 3 nodes)
        communities_list = [c for c in communities_list if len(c) >= 3]

        # Assign community IDs to nodes in the original graph
        result: dict[int, list[str]] = {}
        for cid, members in enumerate(communities_list):
            result[cid] = members
            for node in members:
                if g.has_node(node):
                    g.nodes[node]["community"] = cid

        logger.info(f"Detected {len(result)} communities "
                    f"({sum(len(m) for m in result.values())} nodes covered)")
        return result

    def summarize_communities(self, graph,
                              communities: dict[int, list[str]],
                              on_progress: Callable[[int, int], None] | None = None,
                              ) -> dict[int, "CommunityInfo"]:
        """Generate LLM summaries for each community.

        Args:
            graph: KnowledgeGraph instance
            communities: Output of detect_communities()
            on_progress: Callback(completed, total)

        Returns:
            {community_id: CommunityInfo}
        """
        total = len(communities)
        self.communities = {}
        # v17 Phase 88: stop summarizing after repeated LLM failures (e.g. 402),
        # so a dead endpoint doesn't fire one failing call per community.
        import os as _os
        try:
            _fail_threshold = max(1, int(_os.environ.get("HASHMM_KG_LLM_MAX_FAILS", "12")))
        except ValueError:
            _fail_threshold = 12
        _consec_fails = 0
        _llm_down = False

        for i, (cid, members) in enumerate(communities.items()):
            # Build context for the community
            entities_text = []
            for node in members[:20]:  # Limit to avoid huge prompts
                attrs = graph.graph.nodes.get(node, {})
                name = attrs.get("name", node)
                etype = attrs.get("type", "")
                desc = attrs.get("description", "")
                entities_text.append(f"- {name} ({etype}): {desc}")

            relations_text = []
            for u, v, attrs in graph.graph.edges(data=True):
                if u in members and v in members:
                    head_name = graph.graph.nodes[u].get("name", u)
                    tail_name = graph.graph.nodes[v].get("name", v)
                    rel = attrs.get("relation", "相关")
                    relations_text.append(f"- {head_name} → {rel} → {tail_name}")
                    if len(relations_text) >= 15:
                        break

            # Generate summary
            summary = ""
            if self.llm_fn and entities_text and not _llm_down:
                prompt = COMMUNITY_SUMMARY_PROMPT.format(
                    entities="\n".join(entities_text),
                    relations="\n".join(relations_text) or "（无明确关系）",
                )
                try:
                    system_msg = "你是学术知识总结专家。"
                    if hasattr(self.llm_fn, 'quick_call'):
                        summary = self.llm_fn.quick_call(system_msg, prompt, max_tok=300)
                    else:
                        # Simple callable: combine into single prompt
                        import inspect
                        sig = inspect.signature(self.llm_fn)
                        if len(list(sig.parameters)) == 1:
                            summary = self.llm_fn(f"{system_msg}\n\n{prompt}")
                        else:
                            summary = self.llm_fn([
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": prompt},
                            ])
                    _consec_fails = 0
                except Exception as e:
                    _consec_fails += 1
                    if _consec_fails >= _fail_threshold and not _llm_down:
                        _llm_down = True
                        logger.error(
                            f"社区摘要连续失败 {_consec_fails} 次，已熔断、跳过剩余社区的 LLM 摘要"
                            f"（最近错误：{str(e)[:120]}）。常见原因：API 余额不足(402)。"
                            f"图谱本身已建好；摘要可在恢复 LLM（或改用本地 Qwen2.5）后重建社区。"
                        )
                    else:
                        logger.warning(f"Community {cid} summarization failed: {e}")
                    summary = f"包含 {len(members)} 个实体的知识社区"
            elif self.llm_fn and entities_text and _llm_down:
                summary = f"包含 {len(members)} 个实体的知识社区"

            info = CommunityInfo(
                community_id=cid,
                members=members,
                summary=summary.strip(),
                size=len(members),
            )
            self.communities[cid] = info

            if on_progress:
                on_progress(i + 1, total)

        logger.info(f"Summarized {len(self.communities)} communities")
        return self.communities

    def get_community_for_entity(self, graph, entity_name: str) -> "CommunityInfo | None":
        """Get the community info for an entity."""
        key = entity_name.lower().strip()
        if not graph.graph.has_node(key):
            return None
        cid = graph.graph.nodes[key].get("community", -1)
        return self.communities.get(cid)

    def get_relevant_communities(self, entities: list[str]) -> list["CommunityInfo"]:
        """Get communities relevant to a set of entities."""
        relevant_cids: set[int] = set()
        for e in entities:
            for cid, info in self.communities.items():
                if e.lower().strip() in info.members:
                    relevant_cids.add(cid)
        return [self.communities[cid] for cid in relevant_cids if cid in self.communities]

    def to_dict(self) -> dict:
        """Serialize all communities for persistence."""
        return {
            str(cid): info.to_dict()
            for cid, info in self.communities.items()
        }

    def from_dict(self, data: dict):
        """Load communities from serialized dict."""
        self.communities = {}
        for cid_str, info_data in data.items():
            cid = int(cid_str)
            self.communities[cid] = CommunityInfo(
                community_id=cid,
                members=info_data.get("members", []),
                summary=info_data.get("summary", ""),
                size=info_data.get("size", 0),
            )


class CommunityInfo:
    """Information about a detected community."""

    def __init__(self, community_id: int, members: list[str],
                 summary: str = "", size: int = 0):
        self.community_id = community_id
        self.members = members
        self.summary = summary
        self.size = size or len(members)

    def to_dict(self) -> dict:
        return {
            "community_id": self.community_id,
            "members": self.members,
            "summary": self.summary,
            "size": self.size,
        }
