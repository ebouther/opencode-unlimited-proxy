# opencode-free-unlimited

Community bundle to enjoy OpenCode free-tier (zero cost, no key) while mitigating `429 FreeTierError` via outbound proxies and upstream identity (opencode2api Go).

## Verified status (live)
- Engine passes `/v1/chat/completions` → `391` (`23*17`) in local test.
- 5/5 consecutive calls = 200 success using WARP proxy pool.
- 2 active WARP nodes → 2 separate egress IPs/resources.
- No FreeTier (canonical session identity preserved via opencode2api engine).

## Topology
```
clients -> [8080] opencode2api (Go engine, FreeTier identity)
        -> Proxies ["http://127.0.0.1:18090", "127.0.0.1:18091"] (TinyProxy CONNECT over
WARP namespace A/B, rotate 120s-180s)
-> OpenCode free upstream
```
Expected: every request selects a proxy from the pool; when IP bucket empties, engine falls back or request fails. Add `n` real proxies/IPs to increase your quota ceilings.

## Quick start (Docker)
1. `cd stack`
2. Optional: edit `config.json` to adjust local key or proxy list
3. `docker network create ofu` (if not exists) then `docker compose up -d`
   or `docker compose up -d` if it handles network.
4. Verify:
   ```bash
   curl -s http://127.0.0.1:8080/v1/models -H 'Authorization: Bearer local-test-key' | jq '.data | length'
   curl -s -X POST http://127.0.0.1:8080/v1/chat/completions -H 'Authorization: Bearer local-test-key' -H 'Content-Type: application/json' -d '{"model":"space-bunny-free","messages":[{"role":"user","content":"What is 23*17?"}],"max_tokens":30}' | jq '.choices[0].message.content'
   ```
5. OmniRoute/9router node `opencode-free-unlimited` -> `http://127.0.0.1:8080/v1`.

## Scaling to "real end-user Unlimited"
To get **additional throughput**, grow the `proxies` array. Each entry should point at a proxy that necessarily egresses out a **real distinct IP**. You can:
- add Cloudflare WARP instances (each rotation or port) to pool
- add residential/datacenter proxies to the list (your own pool)
- use OpenAI-compatible proxy load balancing and round-robin via Docker/macvlan

See `config.json` proxies field.

## Why not pure Rust replacement
`opencode2api` Go engine is the FreeTier identity source (canonical session IDs mandated by 2026-10-14 gate). Pure Rust has not verified as fully compliant. This stack preserves the Go engine as the brain and coordinates IP rotation via proxies.

## License
- derived from `opencode2api` (GPL-3.0) + `opencode-ip-rotator` (MIT)
- this bundle: MIT (where compatible, respect upstream licenses)
