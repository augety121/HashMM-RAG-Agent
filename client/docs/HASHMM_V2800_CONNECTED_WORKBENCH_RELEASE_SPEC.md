# HashMM V2800 / Connected Workbench Release

## 1. Release contract

V2800 is a coordinated release, not a UI-only patch. The version matrix is:

- Product/API: `28.0.0`
- Backend: `V2800 / 2.8.0`
- Desktop/WebUI/native installer: `17.0.0`
- Android: `12.0.0 (280)`
- Remote protocol: `hashmm.remote.v4`
- Provider contract: `hashmm.provider-fabric.v2`

The source manifest in `hashmm/release-manifest.json` is authoritative. A
client built for V2800 must not be paired with a server that does not expose
the authenticated remote v4 routes.

## 2. Remote approval and media boundary

Device discovery, user approval, session authorisation and media negotiation
are distinct states. Selecting a device may send a permission request, but it
must not start WebRTC or compatibility media timers. Only a `ready` event with
a non-empty session id, a current viewer ticket and the `view` scope may enter
media negotiation.

The server issues host and viewer tickets before publishing `ready`. If either
ticket cannot be issued, the session is revoked and both peers receive a
terminal failure. Ticket generation is carried in the ready/refresh messages
so stale asynchronous work cannot overwrite a newer session. Compatibility
preview begins only after the viewer requests it and the host confirms
`compatStarted`.

Android refreshes an expired Supabase session once after a control-plane 401,
then rebuilds bootstrap and the single-use socket ticket. It never loops with
the same expired access token.

## 3. Conversation attachment pipeline

Drag, paste, file selection and folder selection enter one queue. Directories
are enumerated recursively with relative paths. Absolute paths, parent
traversal, duplicate files, more than 50 files and files over 256 MiB are
rejected before upload.

Queues belong to a concrete Chat draft. Switching Chat cannot carry selected
files into another conversation. Upload uses the owner-scoped conversation
resource endpoint with real byte progress, at most three concurrent requests,
cancellation and retry. Successful receipts are reused; a failed file does not
cause already uploaded bytes to be sent again.

## 4. User Provider Fabric and Sub2API

Logged-in users configure authorised upstreams under Settings > Models & API.
The page supports official APIs, authorised Sub2API deployments, compatible
gateways and explicit local runtimes. It exposes connection creation, health
probe, channels, route preview, explicit Chat activation and deletion.

Provider credentials are encrypted at rest, never returned by overview APIs,
and never shared with HashMM's northbound `/v1` API keys. Public providers must
use HTTPS and pass SSRF checks. Every object lookup is owner-scoped; another
account receives the same not-found response as an absent object. Saving a
connection never silently changes the paid endpoint used by Chat.

Android's ordinary-user model settings expose the same authorised Sub2API
provider preset. Users must test and explicitly select the resulting model.

## 5. Verification and honest limits

Required source gates are backend regression tests, frontend tests/typecheck/
production build, desktop Node contracts, Android unit tests, and the desktop
release source verifier. A distributable installer additionally requires the
native build pipeline and matching EXE, `.sha256` and `.release.json`.

Local and simulated tests do not prove real symmetric-NAT traversal, TURN UDP/
TCP/TLS reachability, multi-hour stability, Android store signing or Windows
Authenticode identity. Those remain deployment acceptance items and must not be
reported complete without two real devices and artifact-signature evidence.

## 6. Verified build evidence

The V2800 source and release run completed with 1,740 backend tests passing
(8 skipped), 234 frontend tests passing across 64 files, all 77 desktop Node
regression files passing, and the Android unit/debug/release build completing
109 Gradle tasks. The native seven-stage pipeline verified the source, packaged
Electron application and assembled payload before producing the installer.

The installer SHA-256 is
`a114e01e0f47c5bc4fd5546285bcc36be97347588d5ededf094deedf048193d3` and
matches both sidecars. This is integrity evidence only: the build manifest says
`publisher_signature.verified_by_build=false`, so Authenticode identity remains
an explicit public-release prerequisite.
