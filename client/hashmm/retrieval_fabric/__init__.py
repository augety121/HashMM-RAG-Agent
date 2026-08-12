"""HashMM Agent Retrieval Fabric 1.0.

The fabric returns auditable evidence records.  It deliberately does not claim
that ranking scores prove factual truth.
"""

from .contracts import SearchRequest, SearchRunState
from .service import RetrievalFabric, get_retrieval_fabric

__all__ = ["SearchRequest", "SearchRunState", "RetrievalFabric", "get_retrieval_fabric"]
