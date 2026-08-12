# HashMM V2700 / Agent Compatibility Release

## 1. Release objective

V2700 closes the gap between source capability and deployable artifacts. The
release matrix is Product/API `27.0.0`, Backend `V2700 / 2.7.0`, Desktop and
native installer `16.0.0`, and Android `11.0.0 (270)`. The only version source
is `hashmm/release-manifest.json`.

The production incident supplied for this release is a generation mismatch:
the running server reported V2300 while Desktop and Android requested
`/api/remote/v4/*`. A V2300 server cannot serve those routes and correctly
returned 404. Replacing authentication providers or repeatedly reconnecting
cannot repair a missing route. Server V2700 must be deployed before V2700
clients are accepted.

## 2. Remote compatibility contract

- Account identity, device discovery, approval, audit and signalling use the
  authenticated HashMM API control plane.
- Screen media and input prefer WebRTC direct ICE, then the operator-owned
  TURN service. They are not proxied as full-rate media through the HashMM API.
- `/api/remote/v4/bootstrap`, `/socket-ticket`, `/devices`,
  `/diagnostics/self`, `/preflight` and `/ws` ship together.
- Bootstrap returns the minimum Desktop version from the unified release
  manifest. An environment override may only narrow an intentional rollout.
- A 404 on required v4 routes is terminal `server_upgrade_required`, not an
  infinite retry. A 401 is an authentication/session fault, not "device
  offline". No UI may merge these states.
- Remote readiness is not remote success. Success requires an approved session
  and a rendered first frame on the viewer. TURN configuration is not proof
  that relay candidates were selected.

Automated loopback tests prove protocol and permission behavior only. Cross-NAT
first-frame latency, TURN UDP/TCP/TLS selection, reconnect behavior and 24-hour
stability still require two real devices after deployment. V2700 does not claim
the private codec network or commercial SLA of a proprietary remote product.

## 3. Provider Fabric / Sub2API

An authenticated user can create an owner-scoped Provider connection, choose
`sub2api` or an OpenAI-compatible provider, add encrypted credentials and
channels, probe health, preview the redacted route, and explicitly activate it
for Chat. The upstream key is encrypted at rest and never returned to the UI.

Routing is performed on the real request path: enabled and healthy channel
filtering, circuit state, priority, deterministic weighted choice, sticky
conversation/project affinity, per-channel leases and bounded retry. Object IDs
are owner-checked. Missing and unauthorized records use non-enumerable
responses where applicable. HashMM login does not silently grant access to a
third-party Sub2API account; users must explicitly authorize a connection.

## 4. Composer attachments

Desktop/Web Chat accepts file picker and drag-and-drop input through one upload
pipeline. Exact bytes are uploaded once, represented by a receipt, and attached
to the request scope. The Agent reads request-scoped attachments before broad
retrieval. File names, extracted text and embedded instructions are untrusted
data. Upload progress, parse/OCR state, retry and removal are explicit; dropping
a file never executes it.

## 5. HashMM behavior kernel

The uploaded Claude/Codex reference materials were reviewed by immutable SHA-256
and adapted as product-independent operating rules. Their source prompts and
foreign tool schemas are not embedded or redistributed.

`hashmm.behavior-kernel.v1` is built only from the AgentLoop effective tool set
after permission filtering. It therefore cannot grant a new tool. It defines:

- attachment-first evidence use and prompt-injection isolation;
- current-information retrieval when an approved search tool exists;
- task-plan maintenance for dependent work;
- verification after writes and artifact generation;
- waiting/approval/retry as non-terminal states;
- a strategy change after repeated equivalent failure;
- completion receipts instead of prose-only completion;
- public decisions and evidence without exposing or persisting hidden
  chain-of-thought.

## 6. Deployment order

1. Back up server data and `.env`; deploy the V2700 server ZIP and run doctor.
2. Verify `/api/health` reports V2700 and authenticated
   `/api/remote/v4/bootstrap` returns `hashmm.remote.v4`.
3. Verify Cloudflare passes HTTPS/WSS without caching API or WebSocket traffic.
4. Upgrade Desktop to 16.0.0 and Android to 11.0.0 (270).
5. Sign in with the same Supabase project/account on both devices, approve the
   viewer, and verify first-frame evidence on two real networks.

Rolling clients forward before the server is explicitly unsupported. The old
V2300 process must not remain behind the public hostname after the health check
claims V2700.

## 7. Release gates

- Backend: targeted authorization, remote, provider, attachment and behavior
  tests, followed by `python -m pytest -q tests`.
- Frontend: `npm test`, `npm run typecheck`, `npm run build`.
- Desktop: remote supervisor/security tests and
  `python scripts/verify-release.py --source-only`.
- Android: unit/lint/debug build; production APK requires the configured signing
  certificate and must fail closed when it is absent.
- Windows: only `installer-native/build-all.bat`; the EXE must match its
  `.sha256` and `.release.json`.
- Server/App packages: package manifest, version and SHA-256 must agree with the
  unified release manifest.

