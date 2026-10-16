# opencode-unlimited-proxy

A self-hosted, **zero-cost** OpenAI-compatible gateway for the OpenCode **free
tier** (no API key, no spend), fronted by a pool of rotating egress addresses so
a single IP quota is not the only thing standing between you and a working
session.

## What this does, and what it does not

**Does**
- Serves OpenCode's free-tier models (`space-bunny-free`, `mimo-v2.6-flash-free`,
  `big-pickle`, …) through an OpenAI-compatible API, with **no key and no cost**.
- Reproduces the complete client fingerprint the free tier gates on — including
  the two most people miss: the `{bash, glob, grep, read}` tool quartet and
  forced `stream: true`. See [`docs/free-tier-gates.md`](docs/free-tier-gates.md).
- Multiplies your usable budget across N egress addresses, and fails over
  automatically when one address goes unhealthy or runs dry.

**Does not**
- Remove the upstream quota. It appears to be metered **per egress address**, so
  the ceiling is roughly `N × per-address quota`, not infinity — and `N` is the
  number of *distinct* addresses you can actually obtain, not the number of
  containers you started. With WARP that is roughly the size of the regional
  address pool, about seven from one French colo. Beyond that, only egress you
  control will do it. That per-address model is inferred from observed
  behaviour rather than measured at upstream — see
  [the quota axis](docs/free-tier-gates.md#not-a-gate-the-quota-axis).
- Guarantee that any address is accepted. WARP is a shared consumer network:
  addresses repeat, are geolocated unexpectedly, and can be refused outright.
- Carry a warranty against upstream changes. The free-tier fingerprint is not a
  published contract and has already changed twice (≈2026-10-11, ≈2026-10-13).
- Carry any affiliation with OpenCode, Cloudflare, or the upstream projects.

> Rotating egress to reach more of a free tier than a single address allows may
> breach OpenCode's terms of service. That call is yours, on your own
> infrastructure, at your own cost.

---

## What it actually is

Six WARP containers, six CONNECT proxies, one OpenAI-compatible gateway.

The load-bearing detail is not the number of containers, it is the number of
**distinct** egress addresses. Usage is metered per source address — six nodes
behind one address are one budget, not six. (That metering model is inferred
from this stack's own behaviour rather than measured at upstream; see
[Not a gate: the quota axis](docs/free-tier-gates.md#not-a-gate-the-quota-axis)
for what was actually observed and how to falsify it.) WARP hands out addresses
from a small per-region pool and assigns them semi-randomly, so nodes collide by
default. Measured here on a French colo: `104.28.194.129-.132`,
`104.28.226.128-.130` and `185.137.228.16` — about seven addresses for six
nodes, which is barely enough to be worth de-duplicating at all.

That is what `ofu-dedupe` is for. Once per pass it measures each node's real
egress address through that node's own proxy, asks one colliding node to
re-register, and repeats until the addresses are distinct. A node that cannot
be given its own address is *drained*: its CONNECT proxy stops listening, the
engine's health check drops it, and the pool stops pretending to be bigger
than it is.

Two constraints shape it, both learned the hard way:

- **Changing address costs a WARP registration.** A plain
  `disconnect`/`connect` does not move the node. Cloudflare rate-limits the
  registration API with HTTP 429, so rotations are metered globally — one at a
  time, at least 90 seconds apart, at most two per pass. A pool that churns
  registrations ends up crash-looping, which is worse than a collision.
- **The rotator's own address report cannot be trusted.** `rotator.py` caches
  the address it saw at startup and only refreshes it when *its* rotation
  succeeds. If a rotation half-succeeds, the cache goes stale and the cluster
  believes two nodes are distinct when they are not. So `ofu-dedupe` measures
  the egress itself and never asks.

The result: six nodes, six distinct addresses, and the pool then sits still —
no rotation, no rate-limit pressure, nothing to babysit.

> Using your own proxies? Delete the `alztrk-warp-*` / `tinyproxy-*` services and
> `ofu-dedupe` from `docker-compose.yml`, put your endpoints in the `proxies`
> array of `config.json`, and the rest of the stack is unchanged. That is also
> the only way past WARP's address-space ceiling: more buckets than WARP can
> supply means egress you control.

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
git clone <this-repo> && cd opencode-unlimited-proxy/stack

# optional: edit the pool size before the first start
python3 generate-compose.py --nodes 6

# The WARP nodes need the Cloudflare terms of service to be accepted explicitly.
# Read them first: https://www.cloudflare.com/application/terms/
cp .env.example .env && sed -i 's/WARP_ACCEPT_TOS=0/WARP_ACCEPT_TOS=1/' .env

docker compose up -d --build      # first run builds the WARP + tinyproxy images
```

Already have your own proxies? Skip the WARP nodes entirely: put them in the
`proxies` array of `config.json`, delete the `alztrk-warp-*` / `tinyproxy-*`
services from `docker-compose.yml`, and you never touch Cloudflare's terms.

Then verify:

```bash
./../scripts/smoke.sh             # or: ../scripts/smoke.sh
```

`smoke.sh` checks the bundle end to end. To check *upstream* — whether the
free-tier fingerprint is still what this repository says it is, independently
of anything running locally:

```bash
../scripts/gate-probe.py          # one baseline + five negative controls
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

**Before this host is reachable by anything but you**, change the dashboard
credentials *and* the API key. As shipped, `stack/config.json` carries the
upstream defaults — `admin` / `changeme-changeme` for the dashboard, and
`server_keys: ["local-test-key"]` for the API — and the dashboard binds
`0.0.0.0:8081`, so it will listen on every interface. The API key is what stops
unbilled traffic; the dashboard is the admin surface. Set both in
`stack/config.json`, then `docker compose up -d` (`config-init` re-seeds the
volume from that file on every start).

## Point your tools at it

### OpenCode CLI

```jsonc
// ~/.config/opencode/opencode.jsonc
{
  "model": "opencode-unlimited-proxy/space-bunny-free",
  "provider": {
    "opencode-unlimited-proxy": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "opencode-unlimited-proxy",
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
array in `stack/config.json`.

Nodes no longer rotate on a timer by default: `ofu-dedupe` owns rotation, and
it only rotates to break a collision. Left to churn on their own, the nodes
simply undo each other's work every couple of minutes and burn WARP
registrations until Cloudflare starts answering 429. The per-node interval is
still there if you want it (`--node-rotation 300`), but a de-duplicated pool
does not need it.

**More nodes is not automatically more buckets.** WARP only offers a limited set
of addresses per region — about seven from one French colo — so past that point
`ofu-dedupe` will drain the surplus instead of pretending. Watch the `pool:`
line in `docker logs ofu-dedupe` to see what you actually have, and
`../scripts/smoke.sh` asserts it. Scaling past the WARP ceiling means bringing
your own egress: fill the `proxies` array in `config.json` with your own
endpoints, drop the `alztrk-warp-*` / `tinyproxy-*` / `ofu-dedupe` services, and
the engine keeps working unchanged.

## Monitoring

- **Dashboard** (`http://127.0.0.1:8081`): request distribution, per-proxy health
  and counters, error rates.
- **Logs**:
  ```bash
  docker logs ofu-dedupe                     # pool line: live / distinct
  docker logs ofu-engine | grep -iE 'proxy became|rate limit|unavailable'
  docker compose -f stack/docker-compose.yml logs -f tinyproxy-a
  ```
- **Pool state** — `ofu-dedupe` prints one line per pass:
  ```
  pool: 6/6 nodes live, 6 distinct addresses | alztrk-warp-a=104.28.194.131 ...
  ```
  A `<-- collisions` suffix means it is still working on it; `DRAINED` in the
  log means a node lost its proxy on purpose. Nothing needs doing either way.
- **Probe a single node**:
  ```bash
  curl -s -x http://127.0.0.1:18090 https://api.ipify.org; echo
  ```
- **Undo a drain**:
  ```bash
  docker exec ofu-dedupe rm /markers/b && docker logs -f ofu-proxy-b
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
| `FreeTierError` (403) | A gate is unsatisfied, or upstream moved. `./scripts/gate-probe.py` says which gate, without involving this stack. |
| `FreeUsageLimitError` (429) | Not a gate. The address ran out of budget — nothing in the fingerprint will fix it. See [the quota axis](docs/free-tier-gates.md#not-a-gate-the-quota-axis). Widen the pool or wait. |
| `[400] … messages[N].reasoning` on multi-turn chats | Known conversion bug in the upstream engine for some models (`cat-jam-free` observed). Use `space-bunny-free` for multi-turn, or disable the reasoning field client-side. |
| Node restarts in a loop, `bind: address already in use` | Something else holds 8080/8081. The engine uses host networking; free the ports. |

## Legal and terms of service

**Publishing this repository is fine. Operating it is where the risk lives.**
The two are separate questions, and the second one is not about licences.

### Licences — verified compatible
`MIT` for this repository's own files. The engine
(`ghcr.io/jasonxu114514/opencode2api`, **GPL-3.0**) is *not* redistributed
here, only referenced as a public container image, so no GPL obligation attaches
to these files. `stack/warp/` is MIT code from `alztrk/opencode-ip-rotator` with
its licence text preserved. `tinyproxy` (GPL-2.0) is installed from Alpine at
build time, not redistributed. See [`NOTICE`](NOTICE).

Using the name "opencode" is nominative, to state compatibility. No affiliation
with or endorsement by OpenCode is claimed or implied.

### Cloudflare WARP — read this before running the default pool
The default egress source is the Cloudflare WARP client. It is a **consumer VPN
product** under the [Cloudflare Application Terms of
Service](https://www.cloudflare.com/application/terms/), and those terms say
things that matter here:

> **§3 — VPN Profile**: "the Application operates similarly to a VPN product or
> service, **the Application is not designed to hide your identity** from the
> Internet properties you access from your device."

> **§2 — Scope of License**: use is licensed "on the device for which it is
> provided and that you own or control", is non-transferable, and must not
> "create derivative works".

> **§6 — User Responsibilities**: "Cloudflare may not be used to **misrepresent**
> or to act on behalf of others", and must not "create an **undue burden** on the
> Application or the networks". A complaint to Cloudflare can result in
> termination "without notice".

Rotating egress to appear as several different users is outside what §3 says the
product is for. Running several instances of the consumer client in containers
sits outside the "one device you control" model in §2. Neither is clearly
permitted, and neither is clearly forbidden — that is the honest reading.

What this means in practice: the account can be closed, usage can be limited or
blocked, and complaints go to a dedicated abuse mailbox
(`warpabuse@cloudflare.com`). Cloudflare's liability for consumer products is
capped, and disputes go to arbitration. A breach of terms is a contract matter,
not a criminal one — and publishing this repository is not the breach; running it
is.

Because of that, **the WARP terms are never accepted for you**: the nodes refuse
to start unless you set `WARP_ACCEPT_TOS=1` in `stack/.env`, which is an
explicit act of yours.

### The alternative that sidesteps all of it
This bundle makes no assumption about where the proxies come from. `config.json`
takes any HTTP/SOCKS proxy list. If you have your own egress — a residential
pool, a datacentre fleet, a company gateway — use that and drop the
`alztrk-warp-*` / `tinyproxy-*` services entirely. Cloudflare's terms then have
no bearing on your setup, and neither do their rotators' limits.

### OpenCode's own terms
Not verified here. Before operating this at scale, read OpenCode's terms for the
free tier yourself: if they prohibit circumventing quotas, that is a second
contract question independent of Cloudflare's.

---

## Known limitations

- Per-egress quota is still finite; a wider pool widens it, never removes it.
- WARP offers a limited set of addresses per region (about seven from one French
  colo). `ofu-dedupe` makes the pool match what is actually available and drains
  the overflow — it cannot invent addresses. More buckets than that means egress
  you control, not WARP.
- Every address change costs a WARP registration, and Cloudflare rate-limits
  registrations. The pool therefore converges slowly on purpose; a collision
  that resolves in a few minutes is cheaper than a crash loop.
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
  tinyproxy/             CONNECT proxy image + drain gate
  dedupe/                address de-duplicator (keeps the pool honest)
scripts/smoke.sh         one-command verification
scripts/gate-probe.py    isolates each upstream gate; needs no stack running
docs/free-tier-gates.md  the four upstream gates, plus the quota axis
NOTICE                   third-party attribution
```

## Licenses

This repository's own files: **MIT** (`LICENSE`).
Vendored/third-party: see `NOTICE` — notably `stack/warp/` (MIT, © alztrk) and
the engine image `ghcr.io/jasonxu114514/opencode2api` (**GPL-3.0**).
