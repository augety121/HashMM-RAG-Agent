# HashMM Cloudflare Computer Worker

This is HashMM's authenticated gateway over the official
`@cloudflare/computer@0.1.0-alpha.1` preview package. It uses a Durable Object
SQLite VFS and the published package's `WorkerBackend`; it is not a
reimplementation. The later source snapshot renamed this API to
`WorkerShellBackend`, but that snapshot is not what npm currently resolves for
the same alpha version.

Before deployment, set the service-to-service secret:

```powershell
npx wrangler secret put HASHMM_GATEWAY_TOKEN
npm run deploy
```

Then configure the HashMM server (never the App or renderer) with
`HASHMM_CLOUDFLARE_COMPUTER_URL`, `HASHMM_CLOUDFLARE_COMPUTER_TOKEN`, and
`HASHMM_CLOUDFLARE_COMPUTER_ENABLED=1`.

The gateway allowlist intentionally rejects `curl`. Live web retrieval is
handled by HashMM Retrieval Fabric, which has source verification and audit
semantics that a raw shell command does not provide.
