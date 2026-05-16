"""LangGraph agent orchestration for HashMM-RAG (M4).

Public API:
    build_agent — compile the state graph
    AgentState  — the TypedDict schema
    ascii_diagram — pretty-print the topology
"""

from hashmm.agent.graph import ascii_diagram, build_agent
from hashmm.agent.state import AgentState, Intent, RetrievalStrategy

__all__ = [
    "build_agent",
    "AgentState",
    "Intent",
    "RetrievalStrategy",
    "ascii_diagram",
]
