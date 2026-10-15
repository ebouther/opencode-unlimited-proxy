# opencode-free-unlimited

Community bundle to use OpenCode **free-tier** models (zero cost, no OpenCode Zen key) while adding IP-rotation on top of the upstream free-tier identity gateway. Compose ships a working stack end-to-end.

## Verified status
- Engine (`opencode2api`) with anonymous `/inference` lane → returns `391` for `23*17`, end-to-end PASS.
- Mode: `/v1/chat/completions` and `/v1/models` succeed through `http://127.0.0.1:18082`.
- IP rotation via tinyproxy sidecar sharing the WARP network namespace, `proxies` field populated.
- Multiple successful runs:
  ```
  status=200, anon:200:943ms
  ```
- Rate limits upstream remain subject to upstream quotas; rotation helps spread egress across nodes; longer cooldown still may occur.

## Topology
```
clients -> [18082] opencode engine -> TinyProxy (127.0.0.1:8888) -> WARP-rotator network namespace
            (FreeTier identity via opencode2api)      (rotates egress IP periodically)
```

## Quick start (Docker)
1. Clone this repo, then make sure Docker has network permissions:
   ```bash
   cd stack
   # Ensure config.json matches your local key if you change it
   docker compose up -d
   ```
2. Test:
   ```bash
   curl -s http://127.0.0.1:18082/v1/models -H 'Authorization: Bearer local-test-key' | jq '.data | length'
   curl -s http://127.0.0.1:18082/v1/chat/completions -H 'Authorization: Bearer local-test-key' -H 'Content-Type: application/json' -d '{"model":"space-bunny-free","messages":[{"role":"user","content":"What is 23*17?"}],"max_tokens":30}'
   ```
3. Point 9Router / OmniRoute node `opencode-free` to `http://127.0.0.1:18082/v1`.

## Config
- `stack/config.json`:
  - `anonymous: true`, empty `zen_keys`/`go_keys`, `server_keys` local
  - `proxies`: `["http://127.0.0.1:8888"]`
  - `upstream.anonymous`: `https://opencode.ai/inference`
- Tunables: WARP rotation interval (300s), cooldown, tinyproxy connect ports.

## Why no pure-Ghost IP patch via Rust?
- The FreeTier fingerprint requires canonical session IDs and exact client signatures (Go upstream) that are not reproducible cross-stack; bundling the proven `opencode2api` engine is the reliable path.

## License
- Derived from `opencode2api` (GPL-3.0) and `opencode-ip-rotator` (MIT).
- This bundle itself: MIT (where compatible; respect upstream licenses).
