# HashMM V1900 / Work OS 5.0 + Remote Presence 3.0 + Agent Catalog 1.0

## 1. Release objective

V1900 turns four previously adjacent surfaces into one truthful work system:

- Today shows one primary next action, remaining approvals, active work and recent deliverables without duplicating the same run.
- Scheduled work is an owner-scoped object with a schedule, destination, run history and explicit enabled state.
- Plugins separates user-facing capabilities from administrator review of executable third-party plugins.
- Device Handoff distinguishes local service health, Agent runner presence, authenticated signaling, a registered remote host and a live remote session.
- Agent Catalog exposes the complete vendored Agency Agents library without loading every prompt into every model call or granting imported roles any authority.

## 2. Remote truth model

The following facts are independent and must never be presented as synonyms:

1. `local_service_running`: the local remote service process exists.
2. `agent_ready`: a dispatch runner can accept Agent work.
3. `signal_authenticated`: a viewer or host authenticated to the signaling protocol.
4. `remote_ready`: an owner-scoped host has a live lease and routable connection.
5. `session_active`: a permissioned remote session exists.

The App device picker may say “可连接” only when an item from `/api/remote/v2/devices` has `remote_ready=true`. Negotiating `hashmm.remote.v2` proves protocol compatibility, not the existence of a computer.

Desktop host startup uses an explicit renderer-ready/bootstrap handshake. Account socket tickets are single-use; after a disconnect the renderer stops and the main process obtains a fresh ticket. Host renderer load/crash state is retained in diagnostics instead of becoming an unobservable empty list.

## 3. Device identity and security

- Device leases are scoped to the authenticated Supabase owner ID, not e-mail text.
- Object IDs and remote connections are owner checked.
- Stale generations cannot revive an older device connection.
- Remote control remains opt-in and permission scoped; protocol presence never implies control approval.
- Offline device records are history, not proof of availability.

## 4. Agent Catalog

The upstream `msitarzewski/agency-agents` MIT archive is vendored as immutable Markdown role assets with an index containing path, category, license and SHA-256. The accepted V1900 catalog contains exactly 271 roles in 18 categories; together with 14 HashMM core roles the UI exposes 285 collaborators.

Imported text is treated as untrusted role guidance:

- no imported role receives filesystem, shell, network, plugin or approval authority;
- the complete Markdown prompt is loaded only for a selected role;
- automatic team formation receives all core roles plus at most 32 locally shortlisted imported summaries;
- manual selection can search and filter the entire catalog;
- imported instructions cannot override system rules, evidence requirements or tool policy.

## 5. Desktop information architecture

### Today

One focus object is shown once. If more approvals exist, only the remaining items appear in “其他待处理”. Active work and recent results are separate projections of the same work ledger.

### Scheduled

The left side is the schedule object list; the right side is create/edit/detail/history. Status is factual: background-ready, manual-only, enabled, disabled, running or last terminal result.

### Plugins

User capability launchers stay separate from skills/search configuration and administrator plugin review. Built-in capabilities are not described as installed executable plugins.

### Device Handoff

The list comes from Remote Presence for computers and from Dispatch for clearly labelled Agent execution nodes. A runner cannot enable “进入桌面”. Visual treatment is flat: semantic icons are not placed in decorative colored boxes and hover does not move rows.

## 6. Mobile contract

The App retains its existing navigation and layout. The device picker removes the misleading `V2` success badge and reports one of: channel unavailable, channel connected but waiting for a computer, or a remote-ready computer discovered. Android release identity is `7.0.0 (190)`.

## 7. Acceptance gates

- Catalog import deterministically produces 271 unique IDs and preserves the upstream license.
- Catalog prompts are path confined, size bounded and lazily loaded.
- Desktop hidden-host ready/bootstrap and fresh-ticket recovery contracts pass.
- Frontend typecheck, unit tests and production build pass.
- Android Kotlin compilation passes with the updated remote status semantics.
- Backend targeted authorization/presence tests and full test suite pass before release packaging.
- Native installer source gate passes; a distributable EXE is accepted only after its EXE, `.sha256` and `.release.json` hashes agree.

## 8. Explicit limitation

The current presence store is a SQLite lease suitable for a single backend instance. V1900 continues to report `multi_instance_ready=false`; Redis/NATS or another shared routing adapter is not claimed until deployed and tested across instances.
