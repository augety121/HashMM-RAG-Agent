"""HashMM's user-facing Work domain.

The modules in :mod:`hashmm.agent` remain the execution engines.  This package
is the stable product boundary shared by Chat, desktop and the Android App:
one vocabulary, one state machine and one owner-scoped command/query service.
"""
from hashmm.work.domain import (
    WORKSPACE_SCHEMA,
    RUN_SCHEMA,
    WorkState,
    canonical_state,
    legacy_status,
    transition_allowed,
)
from hashmm.work.workspace_kernel import (
    build_workspace_snapshot,
    create_workspace_run,
    get_workspace_run,
)
from hashmm.work.protocol import (
    PROTOCOL_SCHEMA,
    ActionRecord,
    ArtifactRef,
    EvidenceReceipt,
    ObservationRecord,
    WorkSpec,
    build_work_envelope,
)

__all__ = [
    "WORKSPACE_SCHEMA",
    "RUN_SCHEMA",
    "WorkState",
    "canonical_state",
    "legacy_status",
    "transition_allowed",
    "build_workspace_snapshot",
    "create_workspace_run",
    "get_workspace_run",
    "PROTOCOL_SCHEMA",
    "ActionRecord",
    "ArtifactRef",
    "EvidenceReceipt",
    "ObservationRecord",
    "WorkSpec",
    "build_work_envelope",
]
