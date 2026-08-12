# HashMM V2801 / Remote Approval Control-Plane Hotfix

V2801 fixes the production remote-approval deadlock observed after V2800.
The account host and Android viewer both registered successfully, but no
`permissionDecision` reached the server because the desktop main process routed
the decision through a hidden capture renderer. That renderer is a media worker,
not a durable control-plane owner, and could silently discard the message while
its local readiness flag lagged the main-process WebSocket.

The desktop main process now receives `permissionRequest`, presents the existing
audited one-shot approval, deduplicates repeated delivery by server session id,
and sends `permissionDecision` directly through the authoritative Remote v4
supervisor socket. The hidden renderer remains responsible only for capture and
media negotiation. Invalid requests fail closed and prompt failures explicitly
send denial when the control socket is available.

The backend now logs redacted `connect` and `permissionDecision` control events,
allowing deployment logs to distinguish identity registration from approval
delivery without exposing tokens, tickets or account identifiers.
