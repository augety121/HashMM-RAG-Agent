"""Route registry — import all APIRouters here."""
from hashmm.api.routes.auth import router as auth_router
from hashmm.api.routes.admin import router as admin_router
from hashmm.api.routes.system import router as system_router
from hashmm.api.routes.conversations import router as conversations_router
from hashmm.api.routes.projects import router as projects_router
from hashmm.api.routes.kg import router as kg_router
from hashmm.api.routes.kb import router as kb_router
from hashmm.api.routes.files import router as files_router
from hashmm.api.routes.evolution import router as evolution_router
from hashmm.api.routes.tools import router as tools_router
from hashmm.api.routes.mcp_server import router as mcp_server_router
from hashmm.api.routes.desktop_updates import router as desktop_updates_router
from hashmm.api.routes.public_api import router as public_api_router
from hashmm.api.routes.llm_raw import router as llm_raw_router
from hashmm.api.routes.user_memory import router as user_memory_router
from hashmm.api.routes.remote_signal import router as remote_signal_router
from hashmm.api.routes.remote_relay import router as remote_relay_router
from hashmm.api.routes.profile import router as profile_router
from hashmm.api.routes.channels import router as channels_router

all_routers = [
    auth_router,
    admin_router,
    system_router,
    conversations_router,
    projects_router,
    kg_router,
    kb_router,
    files_router,
    evolution_router,
    tools_router,
    mcp_server_router,
    desktop_updates_router,
    public_api_router,
    llm_raw_router,
    user_memory_router,
    remote_signal_router,
    remote_relay_router,
    profile_router,
    channels_router,
]
