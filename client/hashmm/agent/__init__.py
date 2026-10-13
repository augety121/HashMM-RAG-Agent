"""HashMM agent package.

The active agent is `hashmm.agent.loop.AgentLoop` (the ReAct tool loop that
backs the live server). Worker delegation lives in `hashmm.agent.worker`.

NOTE: the legacy LangGraph-style agent (graph/nodes/state) is NOT imported here
eagerly anymore — it was superseded by loop.py. Import those modules directly
only if you are running the old standalone scripts.
"""
# Intentionally minimal. Lazy access to the legacy graph API for old scripts:
def __getattr__(name):  # PEP 562 lazy import
    if name in ("build_agent", "ascii_diagram"):
        from hashmm.agent.graph import build_agent, ascii_diagram
        return {"build_agent": build_agent, "ascii_diagram": ascii_diagram}[name]
    if name in ("AgentState", "Intent", "RetrievalStrategy"):
        from hashmm.agent.state import AgentState, Intent, RetrievalStrategy
        return {"AgentState": AgentState, "Intent": Intent,
                "RetrievalStrategy": RetrievalStrategy}[name]
    raise AttributeError(f"module 'hashmm.agent' has no attribute {name!r}")
