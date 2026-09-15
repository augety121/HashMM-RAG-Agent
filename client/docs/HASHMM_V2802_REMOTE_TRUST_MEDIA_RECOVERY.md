# HashMM V2802 / Remote Trust and Media Recovery

V2802 closes the production failure where an approved App session could wait
forever because `viewerJoined` was emitted while the hidden capture renderer
was not ready. The release also makes the authorization policy explicit.

## Authorization contract

- Two Remote v4 devices authenticated to the same Supabase owner are approved
  by the server without a second desktop prompt. Each socket still requires an
  independently minted, single-use admission ticket and each media session has
  short-lived, role/device/generation-bound tickets.
- LAN pairing-code access still requires the six-digit code on first use.
  Successful pairing creates a revocable trusted-device credential. The host
  stores only its SHA-256 digest; subsequent connections may resume without
  re-entering the code.
- Dangerous power actions keep their separate per-operation desktop prompt.

## Media recovery contract

- Main-process control messages use an at-least-once Electron IPC bridge with a
  renderer-ready handshake, delivery identifiers, acknowledgements and retry.
- While WebRTC direct/TURN negotiation is incomplete, the Electron main
  process can capture the primary display and publish the authenticated JPEG
  compatibility preview. This path does not depend on the hidden renderer and
  stops when the viewer reports WebRTC media active.
- Relay URLs never carry bearer credentials. Host and viewer authenticate with
  role-bound `Remote` tickets in the Authorization header.

## Acceptance evidence

- Same-account v4 connect returns `viewerJoined` and `ready` directly.
- A renderer that starts after `viewerJoined` receives the queued message and
  acknowledges it.
- Native compatibility preview publishes a first-frame milestone.
- A code-paired LAN device reconnects with the trusted credential; invalid or
  revoked credentials fail closed and require a new code.

Android `12.0.0 (280)` remains wire-compatible; no APK source changed in this
release. Product/API is `28.0.2`, backend is `V2802 / 2.8.2`, and desktop/native
installer is `17.0.2`.
