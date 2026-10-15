# opencode-free-unlimited

Community tool to enjoy OpenCode **free-tier** models (zero-cost, no key, no OpenCode Zen billing) while mitigating upstream `429` / `FreeTierError` rate limits.

## Status (verified)
- **Free-tier engine**: `opencode2api` v1.4.x keeps the upstream 2026-10-14 fingerprint and passes chat completions (known-good).
- **Rust frontend** in this repo: builds, serves `/health`, `/v1/models`, `/v1/chat/completions` with the published CLI fingerprint. **Does not yet pass the upstream FreeTier gate** because a non-source-validated component of the identity (TLS / HTTP/2 profile / connection-level signal) still differs. Don't expect it to replace `opencode2api` Go by itself.

## Architecture
```
clients -> [port 8080] opencode2api (Go, free-tier identity) -> OpenCode free lane
            ^ optional client-side IP rotation via proxy list (proxies field)
```
Recommended for now: keep `opencode2api` as the upstream engine and `proxies` pointing to a rotating WARP CONNECT proxy (see Docker Compose below).

## Quick start
1. Get a copy of `opencode2api` v1.4+:
   ```bash
   git clone --depth 1 https://github.com/jasonxu114514/opencode2api
   cd opencode2api && go build -o opencode2api ./cmd/opencode2api  # needs Go 1.24+
   ```
   Or download prebuilt binaries from their Releases.
2. Copy `config.example.json` → `config.json` with:
   - `anonymous: true`
   - `zen_keys: []`
   - `go_keys: []`
   - `server_keys: ["your-local-key"]`
   - Optional: `proxies: ["http://127.0.0.1:8081"]` for IP rotation.
   - `upstream.anonymous`: `https://opencode.ai/inference`
3. Run the engine:
   ```bash
   ./opencode2api -config config.json -listen 127.0.0.1:8080
   ```
4. Point 9Router / OmniRoute nœud `opencode-free` to `http://127.0.0.1:8080/v1`.

## Rotation (optional, Docker)
`compose.yaml` ships a reference for running:
- `opencode2api` engine on 8080,
- a WARP-aware CONNECT proxy for egress IP rotation,
- healthchecks.
Run: `docker compose up -d`.

## IP rotation: why `proxies` matters
opencode2api honours a `proxies` list (direct, http/https, socks5/socks5h) and round-robins attempts through each entry. To rotate egress IP over Cloudflare WARP, supply a local CONNECT proxy; the masked upstream request still keeps the free-tier identity untouched.

## License
- This repo's own code: MIT
- `opencode2api` engine: GPL-3.0 (jasonxu114514)
- `opencode-ip-rotator`: MIT (alztrk)

## Notes
- Do **not** rewrite the FreeTier identity in Rust from scratch (failed this session); keeping the Go engine is the stable path.
- No spend on OpenCode Zen keys.
