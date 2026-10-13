"""Facade joining the existing context, compaction and memory boundaries.

It intentionally delegates to the proven V358+ implementations.  The facade
prevents new product surfaces from importing four different context engines
and gives Work one stable, owner-bound contract.
"""
from __future__ import annotations

from typing import Any, Mapping

from hashmm.agent.context_engine import (
    ContextBundle,
    ContextCapsule,
    assemble_context,
    build_context_capsule,
    get_context_engine,
)
from hashmm.work.context_compiler import compile_typed_context


# Keep the public capsule envelope backward compatible.  The independently
# versioned compiler manifest advertises the V668 selection semantics.
CONTEXT_KERNEL_SCHEMA = "hashmm.context-kernel.v1"
_LANE_ORDER = [
    "profile", "task", "memory", "workspace", "retrieval",
    "skill", "episodic", "user_model",
]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def compile_context(
    *,
    owner_id: str,
    conversation_id: str,
    providers: Mapping[str, Any],
    active_goal: str = "",
    criteria: list[Any] | None = None,
    decisions: list[Any] | None = None,
    blockers: list[Any] | None = None,
    provider_profile: Mapping[str, Any] | None = None,
    provider_requirements: Mapping[str, Any] | None = None,
    conversation_revision: str = "",
    session_id: str = "",
) -> tuple[ContextCapsule, dict[str, Any]]:
    """Compile one transient prompt and a safe durable manifest.

    Source bodies remain inside ``ContextCapsule.rendered`` for the active
    model call.  Only the returned public metadata may enter WorkRuntime.
    """
    engine = get_context_engine(
        owner_id, conversation_id, session_id=session_id or conversation_id,
    )
    bundle: ContextBundle = engine.assemble(
        dict(providers or {}),
        order=[lane for lane in _LANE_ORDER if lane in providers],
    )
    capsule = engine.compile_capsule(
        active_goal=active_goal,
        criteria=criteria,
        decisions=decisions,
        blockers=blockers,
        provider_profile=_mapping(provider_profile),
        provider_requirements=_mapping(provider_requirements),
        conversation_revision=conversation_revision,
    )
    profile = _mapping(provider_profile)
    typed_rendered, compiler = compile_typed_context(
        bundle,
        provider=str(profile.get("provider") or ""),
        model=str(profile.get("model") or ""),
        count_fn=profile.get("count_tokens") if callable(profile.get("count_tokens")) else None,
        max_input_tokens=_positive_int(profile.get("max_input_tokens")),
        output_reserve_tokens=_positive_int(
            profile.get("output_reserve_tokens")
            or profile.get("max_output_tokens")
            or 0
        ),
    )
    manifest = capsule.public()
    manifest["context_compiler"] = compiler
    # The typed compiler is authoritative for the current model call.  Only its
    # public loss ledger is durable; source bodies remain transient.
    capsule = ContextCapsule(manifest=manifest, rendered=typed_rendered)
    return capsule, {
        "schema": CONTEXT_KERNEL_SCHEMA,
        "owner_bound": True,
        "conversation_id": str(conversation_id or "")[:160],
        "lanes": [
            {
                "name": source.key,
                "trust": source.trust,
                "hit": source.hit,
                "chars": source.chars,
                "truncated": source.truncated,
                "evicted": source.evicted,
            }
            for source in bundle.sources
        ],
        "capsule": manifest,
        "compiler": compiler,
        "compaction": engine.inspect(),
        "source_bodies_persisted": False,
    }


def compile_stateless_context(
    providers: Mapping[str, Any],
    *,
    active_goal: str = "",
    criteria: list[Any] | None = None,
) -> tuple[ContextCapsule, dict[str, Any]]:
    """Pure helper for previews/tests that must not create a checkpoint."""
    bundle = assemble_context(
        dict(providers or {}),
        order=[lane for lane in _LANE_ORDER if lane in providers],
    )
    capsule = build_context_capsule(
        bundle, active_goal=active_goal, criteria=criteria,
    )
    typed_rendered, compiler = compile_typed_context(bundle)
    manifest = capsule.public()
    manifest["context_compiler"] = compiler
    capsule = ContextCapsule(manifest=manifest, rendered=typed_rendered)
    return capsule, {
        "schema": CONTEXT_KERNEL_SCHEMA,
        "owner_bound": False,
        "preview_only": True,
        "lanes": bundle.observability()["sources"],
        "capsule": capsule.public(),
        "compiler": compiler,
        "source_bodies_persisted": False,
    }
