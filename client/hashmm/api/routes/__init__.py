"""Route registry — import all APIRouters here."""
from hashmm.api.routes.auth import router as auth_router
from hashmm.api.routes.admin import router as admin_router
from hashmm.api.routes.system import router as system_router
from hashmm.api.routes.conversations import router as conversations_router
from hashmm.api.routes.projects import router as projects_router
from hashmm.api.routes.kg import router as kg_router
from hashmm.api.routes.kb import router as kb_router
from hashmm.api.routes.files import router as files_router
from hashmm.api.routes.ocr import router as ocr_router, resource_router
from hashmm.api.routes.evolution import router as evolution_router
from hashmm.api.routes.tools import router as tools_router
from hashmm.api.routes.mcp_server import router as mcp_server_router
from hashmm.api.routes.desktop_updates import router as desktop_updates_router
from hashmm.api.routes.public_api import router as public_api_router
from hashmm.api.routes.llm_raw import router as llm_raw_router
from hashmm.api.routes.user_memory import router as user_memory_router
from hashmm.api.routes.remote_signal import router as remote_signal_router
from hashmm.api.routes.remote_relay import router as remote_relay_router
from hashmm.api.routes.remote_sessions import router as remote_sessions_router
from hashmm.api.routes.channels import router as channels_router
from hashmm.api.routes.stt import router as stt_router
from hashmm.api.routes.credentials import router as credentials_router
from hashmm.api.routes.dispatch import router as dispatch_router
from hashmm.api.routes.images import router as images_router
from hashmm.api.routes.session_ops import router as session_ops_router
from hashmm.api.routes.voice_ops import router as voice_ops_router
from hashmm.api.routes.skill_packs import router as skill_packs_router
from hashmm.api.routes.feed import router as feed_router
from hashmm.api.routes.model_route import router as model_route_router
from hashmm.api.routes.canvas_ask import router as canvas_ask_router
from hashmm.api.routes.canvas_share import router as canvas_share_router
from hashmm.api.routes.canvas_templates import router as canvas_templates_router
from hashmm.api.routes.canvas_locks import router as canvas_locks_router
from hashmm.api.routes.canvas_evidence import router as canvas_evidence_router
from hashmm.api.routes.notifications import router as notifications_router
from hashmm.api.routes.market import router as market_router
from hashmm.api.routes.hooks import router as hooks_router
from hashmm.api.routes.briefing import router as briefing_router
from hashmm.api.routes.team_ops import router as team_ops_router
from hashmm.api.routes.collab import router as collab_router
from hashmm.api.routes.workspace_gw import router as workspace_gw_router
from hashmm.api.routes.doc_studio import router as doc_studio_router
from hashmm.api.routes.loops import router as loops_router
from hashmm.api.routes.code_review import router as code_review_router
from hashmm.api.routes.models_user import router as models_user_router
from hashmm.api.routes.selftest import router as selftest_router
from hashmm.api.routes.figma_import import router as figma_router
from hashmm.api.routes.context_inspect import router as context_router
from hashmm.api.routes.mem_layered import router as mem_layered_router
from hashmm.api.routes.runtime_capabilities import router as runtime_capabilities_router
from hashmm.api.routes.profile import router as profile_router
from hashmm.api.routes.work_runtime import router as work_runtime_router
from hashmm.api.routes.user_work import router as user_work_router
from hashmm.api.routes.workspaces_v2 import router as workspaces_v2_router
from hashmm.api.routes.search_integrations import router as search_integrations_router
from hashmm.api.routes.okf import router as okf_router
from hashmm.api.routes.platform_keys import router as platform_keys_router
from hashmm.api.routes.me import router as me_router
from hashmm.api.routes.retrieval_fabric import router as retrieval_fabric_router
from hashmm.api.routes.workspace_runtime import router as workspace_runtime_router
from hashmm.api.routes.provider_fabric import router as provider_fabric_router
from hashmm.api.routes.canvas_ops import router as canvas_ops_router

all_routers = [
    auth_router,
    admin_router,
    system_router,
    conversations_router,
    projects_router,
    kg_router,
    kb_router,
    files_router,
    ocr_router,
    resource_router,
    evolution_router,
    tools_router,
    mcp_server_router,
    desktop_updates_router,
    public_api_router,
    llm_raw_router,
    user_memory_router,
    remote_signal_router,
    remote_relay_router,
    remote_sessions_router,
    channels_router,
    stt_router,
    credentials_router,
    dispatch_router,
    images_router,
    session_ops_router,
    voice_ops_router,
    skill_packs_router,
    feed_router,
    model_route_router,
    canvas_ask_router,
    canvas_share_router,
    canvas_templates_router,
    canvas_locks_router,
    canvas_evidence_router,
    notifications_router,
    market_router,
    hooks_router,
    briefing_router,
    profile_router,
    team_ops_router,
    collab_router,
    workspace_gw_router,
    doc_studio_router,
    loops_router,
    code_review_router,
    models_user_router,
    selftest_router,
    figma_router,
    context_router,
    mem_layered_router,
    runtime_capabilities_router,
    work_runtime_router,
    user_work_router,
    workspaces_v2_router,
    search_integrations_router,
    okf_router,
    platform_keys_router,
    me_router,
    retrieval_fabric_router,
    workspace_runtime_router,
    provider_fabric_router,
    canvas_ops_router,
]
