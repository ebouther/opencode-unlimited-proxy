# opencode-free-unlimited

A self-hosted, **zero-cost** OpenAI-compatible gateway for the OpenCode **free
tier** (no API key, no spend), fronted by a pool of rotating egress addresses so
a single IP quota is not the only thing standing between you and a working
session.

> **Read this before you expect "unlimited".**
> This bundle is **not** unlimited and does not claim to be. OpenCode's free
> tier enforces per-egress-address quotas and rate limits. Rotating addresses
> multiplies the budget you can reach; it does not remove it. What you get is
> *more room before you hit a wall*, plus automatic failover when one address
> runs dry. Rotating egress to appear as many different users may also breach
> OpenCode's terms of service — that risk is yours, at your own cost, on your
> own infrastructure. Nothing here is affiliated with or endorsed by OpenCode,
> Cloudflare, or the upstream projects.

---

## What it actually is

| Piece | Role |
|---|---|
| `ghcr.io/jasonxu114514/opencode2api` (GPL-3.0) | The engine. Mints the client identity/session headers that the free tier requires, serves `/v1/*`, and round-robins over a proxy pool. |
| Cloudflare WARP namespace per node (`stack/warp/`) | Gives each node its own egress address, re-established on a rotation interval. |
| tinyproxy per node (`stack/tinyproxy/`) | A CONNECT proxy published on the host, sharing its WARP namespace. |
| `stack/config.json` | Pool definition, `anonymous: true`, `free_only: true`, empty key lists. |

```
opencode CLI ─┐
9router ──────┼─► opencode2api :8080 ──► http://127.0.0.1:18090 ┐ CONNECT ┐
OmniRoute ────┘   (identity + retry)      http://127.0.0.1:18091 ─┤ proxy   ├─► opencode.ai/inference
                                           … one per WARP node   │ (tiny)  │   (free tier, Bearer public)
                                                                       └ WARP ───┘
```

## Measured results (this repo, 2026-10-15, 6 nodes)

- `/v1/models` → 11 free models.
- `23*17` → `391` via CLI (`opencode run`), via 9router (`oc2/*`), and via OmniRoute (`9r/snowbuddy-free`).
- 5/5 consecutive single-turn completions → HTTP 200.
- Sustained agent session: ~57 `/v1/chat/completion` in an hour, 47 × `200`, 11 × `499` (client-side cancellations), 0 upstream `429`.

**Not measured:** multi-day runs, thousands of requests, behaviour under
sustained saturation. Treat the throughput numbers above as a smoke test, not a
capacity guarantee.

---

## Requirements

- **Linux** (needs `/dev/net/tun`; WARP does not work in a Windows/macOS Docker VM).
- **Docker Engine** + **Docker Compose v2** (`docker compose`, not `docker-compose`).
- Privileged containers are required: WARP creates a TUN device and routes
  through it, so each node runs `privileged` + `NET_ADMIN`.
- ~150 MB RAM per node and outbound TCP 443/7844 to Cloudflare.

## Quick start

```bash
git clone <this-repo> && cd opencode-free-unlimited/stack

# optional: edit the pool size before the first start
python3 generate-compose.py --nodes 6

docker compose up -d --build      # first run builds the WARP + tinyproxy images
```

Then verify:

```bash
./../scripts/smoke.sh             # or: ../scripts/smoke.sh
```

Endpoints once it is up:

| What | Where |
|---|---|
| OpenAI-compatible API | `http://127.0.0.1:8080/v1` |
| Web dashboard | `http://127.0.0.1:8081` (`admin` / `changeme-changeme` — change it) |
| Proxy pool | `127.0.0.1:18090` … `18090 + nodes - 1` |

> Use `http://`, and prefer `127.0.0.1` over `localhost`. A browser that has
> cached an HTTPS upgrade for `localhost` will fail with
> `SSL_ERROR_RX_RECORD_TOO_LONG` against a plaintext port.

## Point your tools at it

### OpenCode CLI

```jsonc
// ~/.config/opencode/opencode.jsonc
{
  "model": "opencode-free-unlimited/space-bunny-free",
  "provider": {
    "opencode-free-unlimited": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "opencode-free-unlimited",
      "options": {
        "baseURL": "http://127.0.0.1:8080/v1",
        "apiKey": "local-test-key"          // must match stack/config.json server_keys
      },
      "models": { "space-bunny-free": {} }
    }
  }
}
```

```bash
opencode run "23*17 ?"
```

### 9router

Add an OpenAI-compatible provider node:

| Field | Value |
|---|---|
| Base URL | `http://127.0.0.1:8080/v1` |
| API key | `local-test-key` |
| Model prefix | `oc2` (your choice) |

### OmniRoute

Add an OpenAI-compatible node pointing at `http://127.0.0.1:8080/v1`, then
reference it as `<node-prefix>/<model>` from a combo.

## Scaling the address pool

The pool is generated. Resize it and re-apply:

```bash
cd stack
python3 generate-compose.py --nodes 10            # ports 18090-18099
# or start from another base port:
python3 generate-compose.py --nodes 10 --base-port 19090
docker compose up -d --build
```

`generate-compose.py` rewrites both `docker-compose.yml` and the `proxies`
array in `stack/config.json`. Each node re-establishes its WARP tunnel every
`WARP_ROTATION_INTERVAL` (default 120 s, staggered by 25 s per node so the pool
never rotates in lockstep).

Adding nodes multiplies how many distinct egress buckets you have; it does not
create quota that does not exist. WARP addresses come from a shared Cloudflare
pool, so two nodes may occasionally land on the same address — check with
`../scripts/smoke.sh`, which prints the address behind each node.

## Monitoring

- **Dashboard** (`http://127.0.0.1:8081`): request distribution, per-proxy health
  and counters, error rates.
- **Logs**:
  ```bash
  docker logs ofu-engine | grep -iE 'proxy became|rate limit|unavailable'
  docker compose -f stack/docker-compose.yml logs -f tinyproxy-a
  ```
- **Probe a single node**:
  ```bash
  curl -s -x http://127.0.0.1:18090 https://api.ipify.org; echo
  ```

Signs that a node is exhausted: the engine logs `proxy became unavailable`, the
dashboard flips it to `Unavailable`, and completions start failing after the
configured retries.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `proxyconnect tcp: dial tcp 127.0.0.1:1809x: connect: connection refused` | That node's proxy is down. `docker compose ps`, then `docker compose up -d tinyproxy-a`. |
| `no healthy anonymous proxies available` | Every node failed its health check. Check WARP registration: `docker logs ofu-warp-a`. |
| Edits to `stack/config.json` seem ignored | The engine reads `/var/lib/opencode2api/config.json` from its **named volume** and prefers it over your file. `config-init` re-seeds it on `docker compose up -d`; to force it: `docker compose down && docker volume rm stack_ofu-engine && docker compose up -d`. |
| `rename … device or resource busy` at startup | You bind-mounted the config path directly; it must be seeded by `config-init` instead. |
| `SSL_ERROR_RX_RECORD_TOO_LONG` in the browser | You are on `https://` and/or `localhost`. Use `http://127.0.0.1:8081`. |
| `FreeTierError` / `429` on every request | The engine could not present a valid identity, or the address bucket is empty. Check `docker logs ofu-engine`. |
| `[400] … messages[N].reasoning` on multi-turn chats | Known conversion bug in the upstream engine for some models (`cat-jam-free` observed). Use `space-bunny-free` for multi-turn, or disable the reasoning field client-side. |
| Node restarts in a loop, `bind: address already in use` | Something else holds 8080/8081. The engine uses host networking; free the ports. |

## Known limitations

- Per-egress quota is still finite; rotation widens it, never removes it.
- WARP is a shared consumer network: addresses can repeat, be geolocated
  unexpectedly, or be blocked outright.
- First run builds two images (`ubu`-based WARP image, Alpine tinyproxy) — needs
  internet.
- `config-init` overwrites the engine config in the volume on every
  `up`. Local edits made through the dashboard are lost on the next `up -d`.
- `stack/warp/` is third-party MIT code; see `NOTICE`.

## Repository layout

```
stack/
  docker-compose.yml     generated — do not hand-edit
  config.json            engine config + proxy pool
  generate-compose.py    pool generator (resize + proxy list)
  warp/                  Cloudflare WARP node image (vendored, MIT, see warp/LICENSE)
  tinyproxy/             CONNECT proxy image
scripts/smoke.sh         one-command verification
NOTICE                   third-party attribution
```

## Licenses

This repository's own files: **MIT** (`LICENSE`).
Vendored/third-party: see `NOTICE` — notably `stack/warp/` (MIT, © alztrk) and
the engine image `ghcr.io/jasonxu114514/opencode2api` (**GPL-3.0**).
