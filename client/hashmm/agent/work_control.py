"""Owner-bound command plane for durable work runs.

Commands are admitted exactly once, dispatched at most once, and represented
by durable events.  The event ledger is the source of truth shared by Chat,
desktop and App; executor output is never treated as persistence evidence.
"""
from __future__ import annotations

import asyncio
from typing import Any

from hashmm.agent import work_runtime
from hashmm.api import database as db
from hashmm.utils import get_logger

logger = get_logger("hashmm.work_control")


def _failure(state: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": state, **extra}


def _append_control_event(
    run_id: str,
    owner: str,
    *,
    command_id: str,
    event_type: str,
    expected_revision: int | None,
    summary: str = "",
    status: str = "",
    payload: dict[str, Any] | None = None,
    snapshot_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one control event with deterministic replay semantics.

    The key is stable across network retries.  A revision conflict is retried
    once against the latest owner-checked revision; the unchanged key still
    makes the operation idempotent.
    """
    idem = f"work-control:{command_id}:{event_type}"
    kwargs = {
        "user_id": owner,
        "event_type": event_type,
        "summary": summary,
        "status": status,
        "payload": payload,
        "snapshot_updates": snapshot_updates,
        "idempotency_key": idem,
    }
    result = work_runtime.append_event_once(
        run_id, expected_revision=expected_revision, **kwargs,
    )
    if result.get("state") == "revision_conflict":
        current = result.get("current_revision")
        if isinstance(current, int) and current >= 1:
            result = work_runtime.append_event_once(
                run_id, expected_revision=current, **kwargs,
            )
    return result


def _event_revision(result: dict[str, Any], fallback: int) -> int:
    run = result.get("run")
    try:
        revision = int(run.get("revision")) if isinstance(run, dict) else fallback
    except (TypeError, ValueError):
        revision = fallback
    return max(1, revision)


async def execute_command(
    run_id: str,
    *,
    user: dict[str, Any],
    command_id: str,
    action: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Admit once, dispatch at most once, then expose the durable outcome."""
    owner = str(user.get("uid") or "")
    admission = work_runtime.admit_command(
        run_id,
        user_id=owner,
        command_id=command_id,
        action=action,
        expected_revision=expected_revision,
    )
    state = str(admission.get("state") or "invalid")
    if state == "duplicate":
        command = admission.get("command") or {}
        return {
            "ok": command.get("status") == "applied",
            "duplicate": True,
            "command": command,
            "run": work_runtime.get_run(run_id, owner, limit=1),
        }
    if state != "admitted":
        return _failure(
            state,
            current_revision=admission.get("current_revision"),
            run=admission.get("run"),
        )

    run = admission["run"]
    safe_action = str(action or "").strip().lower()
    request_event = _append_control_event(
        run_id,
        owner,
        command_id=command_id,
        event_type="control_requested",
        expected_revision=run.get("revision"),
        summary={
            "pause": "User requested a work pause",
            "resume": "User requested work resume",
            "cancel": "User requested work cancellation",
            "retry": "User requested a work retry",
        }.get(safe_action, "User requested work control"),
        payload={"command_id": command_id, "action": safe_action},
        snapshot_updates={"last_control": {"id": command_id, "action": safe_action}},
    )
    if request_event.get("state") not in {"applied", "duplicate"}:
        command = work_runtime.finalize_command(
            command_id,
            user_id=owner,
            status="failed",
            result={
                "error": "event_persist_failed",
                "event_state": request_event.get("state"),
            },
        )
        return _failure(
            "event_persist_failed",
            command=command,
            run=request_event.get("run") or work_runtime.get_run(run_id, owner, limit=1),
        )
    event_revision = _event_revision(request_event, int(run.get("revision") or 1))

    applied = False
    result: dict[str, Any] = {}
    failure_code = "executor_rejected"
    try:
        kind = str(run.get("kind") or "")
        source_id = str(run.get("source_id") or "")
        if kind == "loop":
            from hashmm.agent import loop_engine

            if safe_action == "pause":
                applied = loop_engine.pause_loop(source_id, owner)
            elif safe_action == "resume":
                applied = loop_engine.resume_loop(source_id, owner)
            elif safe_action == "cancel":
                applied = loop_engine.stop_loop(source_id, owner)
        elif kind == "team":
            from hashmm.agent import team as team_engine

            if safe_action == "cancel":
                stopped = team_engine.request_team_stop(source_id, owner)
                applied = bool(stopped and stopped.get("stop_requested"))
                if stopped:
                    result["team_status"] = str(stopped.get("status") or "")
            elif safe_action == "retry":
                retried = await team_engine.retry_team(user, source_id)
                applied = bool(retried and retried.get("ok"))
                if retried:
                    result.update({
                        "linked_team_id": str(retried.get("team_id") or ""),
                        "linked_run_id": str(retried.get("work_run_id") or ""),
                    })
                    if retried.get("conflict"):
                        failure_code = "executor_state_changed"
        elif kind in {"browser", "computer"} and safe_action == "cancel":
            applied = db.cancel_pending_file_request(source_id, owner)
            if not applied:
                failure_code = "already_claimed_or_finished"
        elif kind == "workflow" and safe_action == "retry":
            # Scheduled work retries are new owner-bound occurrences.  The
            # source id is server-created as schedule:<task>:<occurrence>; only
            # the owner-scoped scheduler entry can be run again.
            source_parts = source_id.split(":", 2)
            if len(source_parts) == 3 and source_parts[0] == "schedule":
                from hashmm import scheduler
                retried = await asyncio.to_thread(
                    scheduler.run_task_now_for_tenant,
                    source_parts[1],
                    owner,
                )
                applied = bool(retried.get("ok"))
                if retried.get("work_run_id"):
                    result["linked_run_id"] = str(retried["work_run_id"])
                if not applied:
                    failure_code = str(retried.get("error") or "executor_rejected")
            else:
                failure_code = "unsupported"
        else:
            failure_code = "unsupported"
    except Exception as exc:  # command is finalized; raw executor data is not persisted
        logger.warning("work command %s failed: %s", command_id, type(exc).__name__)
        failure_code = "executor_error"

    if applied:
        command = work_runtime.finalize_command(
            command_id, user_id=owner, status="applied", result=result,
        )
        # Loop/team executors emit their own precise transition. Device queue
        # cancellation has no executor callback, so the control plane owns it.
        if run.get("kind") in {"browser", "computer"}:
            outcome_event = _append_control_event(
                run_id,
                owner,
                command_id=command_id,
                event_type="cancelled",
                expected_revision=event_revision,
                status="cancelled",
                summary="Desktop claim was cancelled before execution",
                payload={"command_id": command_id},
            )
        elif safe_action == "retry":
            outcome_event = _append_control_event(
                run_id,
                owner,
                command_id=command_id,
                event_type="retry_started",
                expected_revision=event_revision,
                summary="A new independent retry run was created; the original remains auditable",
                payload={"command_id": command_id, **result},
                snapshot_updates={"retry": result},
            )
        else:
            outcome_event = {
                "state": "applied",
                "run": work_runtime.get_run(run_id, owner, limit=1),
            }
        if outcome_event.get("state") not in {"applied", "duplicate"}:
            # The executor already ran. Do not claim a clean success when its
            # durable outcome event could not be written.
            uncertain = work_runtime.finalize_command(
                command_id,
                user_id=owner,
                status="failed",
                result={
                    **result,
                    "error": "event_persist_failed",
                    "action_applied": True,
                    "event_state": outcome_event.get("state"),
                },
            )
            return _failure(
                "event_persist_failed",
                uncertain=True,
                command=uncertain,
                run=outcome_event.get("run") or work_runtime.get_run(run_id, owner, limit=100),
            )
        return {
            "ok": True,
            "duplicate": False,
            "command": command,
            "run": work_runtime.get_run(run_id, owner, limit=100),
        }

    command = work_runtime.finalize_command(
        command_id,
        user_id=owner,
        status="failed",
        result={"error": failure_code},
    )
    failure_event = _append_control_event(
        run_id,
        owner,
        command_id=command_id,
        event_type="control_failed",
        expected_revision=event_revision,
        summary=(
            "The work state changed before the control action could be applied"
            if failure_code != "executor_error"
            else "The control executor failed"
        ),
        payload={"command_id": command_id, "action": safe_action, "error": failure_code},
    )
    if failure_event.get("state") not in {"applied", "duplicate"}:
        failure_code = "event_persist_failed"
    return {
        "ok": False,
        "error": failure_code,
        "command": command,
        "run": failure_event.get("run") or work_runtime.get_run(run_id, owner, limit=100),
    }
