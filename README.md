<p align="center">
  <img src="docs/media/demo.gif" alt="A direct call to the OpenCode free tier is refused with 403 FreeTierError; the same request through this local gateway answers 42" width="820">
</p>

# opencode-unlimited-proxy

<p align="center">A self-hosted, <strong>zero-cost</strong> OpenAI-compatible gateway for the OpenCode <strong>free tier</strong>, fronted by rotating egress addresses. No API key, no spend.</p>

<p align="center">
  <a href="actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/ebouther/opencode-unlimited-proxy/ci.yml?branch=main&label=CI"></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/github/license/ebouther/opencode-unlimited-proxy"></a>
</p>

Both halves of that recording are real, not a mockup. The top command goes
straight to `opencode.ai/zen/v1` and gets the refusal the free tier hands out by
default. The bottom one goes through this gateway and gets an answer, with no
upstream API key configured anywhere in it. What the gateway removes is the
refusal. What the WARP pool removes is the per-egress-block quota ceiling, which
is a separate problem and a separate cause.

## Get it running

> **Linux, tested path.** The default mode runs the engine in the host network
> namespace and needs outbound UDP to Cloudflare on port 2408. The legacy
> external-pool mode additionally needs privileged containers and
> `/dev/net/tun`; Docker Desktop on macOS and Windows provides neither.
> [What that rules out, and what it does not](#on-macos-or-windows).

```bash
git clone https://github.com/ebouther/opencode-unlimited-proxy.git
cd opencode-unlimited-proxy/stack

# Cloudflare's terms apply to the WARP tunnel. Read them first:
# https://www.cloudflare.com/application/terms/
cp .env.example .env && sed -i 's/WARP_ACCEPT_TOS=0/WARP_ACCEPT_TOS=1' .env

# Strongly recommended: your routed IPv6 /64. Each WARP exit then gets its
# own egress /64, which is the block the free tier meters.
ip -6 addr show scope global          # find the prefix, e.g. 2001:db8:...::/64
sed -i 's|^WARP_SOURCE_IPV6=.*|WARP_SOURCE_IPV6=2001:db8:1234:5678::/64|' .env

docker compose up -d
```

Linux, Docker Compose v2, outbound UDP 2408, and ideally a routed IPv6 `/64`.
The engine image is pulled, not built. The first start takes seconds, then WARP
registration adds a little more.

| What | Where |
|---|---|
| OpenAI-compatible API | `http://127.0.0.1:8080/v1` |
| Web dashboard | `http://127.0.0.1:8081` (`admin` / `changeme-changeme` — [change it](SECURITY.md)) |
| WARP pool | engine-managed; `/healthz` reports `warp.active` and `warp.standby`, with no local proxy ports |

Then point a client at it. One entry each, written out in full
[below](#point-your-tools-at-it):

| Client | What to enter |
|---|---|
| OpenCode CLI | a provider on `http://127.0.0.1:8080/v1`, key `local-test-key` |
| 9router | OpenAI-compatible node, base `http://127.0.0.1:8080/v1`, key `local-test-key`, prefix `oc2`, **API type `chat`** |
| OmniRoute | OpenAI-compatible node on `http://127.0.0.1:8080/v1`, then `<prefix>/<model>` from a combo |

Two ways to narrow that down before committing to any of it: the
[60-second probe](#check-the-claim-in-60-seconds-install-nothing) tests the
upstream claim with no Docker at all, and if you already have proxies,
[two services are enough](#variations-verification-and-defaults) — no WARP, no
Cloudflare terms.

## What this does, and what it does not

**Does**
- Serves OpenCode's anonymous free-tier models through an OpenAI-compatible API,
  with **no key and no cost** — nine at the time of writing, listed in
  [OpenCode CLI](#opencode-cli) below.
- Reproduces the complete client fingerprint the free tier gates on — including
  the two most people miss: a tool named exactly `read`, and forced
  `stream: true`. See [`docs/free-tier-gates.md`](docs/free-tier-gates.md).
- Multiplies your usable budget across N egress blocks, and fails over
  automatically when one block is exhausted or unhealthy.

**Does not**
- Remove the upstream quota. It is metered **per egress block**: a `/64` on
  IPv6 and a `/32` on IPv4, reset daily at 00:00 UTC. The ceiling is roughly
  `N × per-block quota`, so `N` is the number of *distinct* blocks you can
  obtain. Cloudflare's WARP IPv4 egress is one shared NAT address that other
  users routinely exhaust; a routed IPv6 `/64` lets each exit bind its own
  source and get its own block. Measured here on 2026-10-27, see
  [the quota axis](docs/free-tier-gates.md#not-a-gate-the-quota-axis).
- Guarantee that any block has budget. WARP is a shared consumer network:
  exits repeat, are geolocated unexpectedly, and can be refused or already
  exhausted. The engine rotates on `429`/`403` until an exit with headroom
  answers or the attempt budget is spent.
- Carry a warranty against upstream changes. The free-tier fingerprint is not a
  published contract and has already changed twice (≈2026-10-11, ≈2026-10-13).
- Carry any affiliation with OpenCode, Cloudflare, or the upstream projects.

> Rotating egress to reach more of a free tier than a single address allows may
> breach OpenCode's terms of service. That call is yours, on your own
> infrastructure, at your own cost.

---

## What it actually is

The default stack is one engine process and one userspace WARP pool. The engine
(`opencode2api` v1.4.4+) registers Cloudflare WARP exits itself, keys them by
egress block, and rotates on `429`/`403`. No privileged containers, no TUN, no
external CONNECT proxies.

The load-bearing detail is the number of **distinct egress blocks**. Usage is
metered per `/64` (IPv6) or `/32` (IPv4), with a daily reset at 00:00 UTC.
Three exits behind one block are one budget, not three. Cloudflare's WARP IPv4
egress is a single shared NAT address that other users routinely exhaust, so
rotating that address buys nothing. With `warp.stack: ipv6` and a routed
`source_ipv6` prefix, each exit binds its own outer source address and gets a
different `/64`, which is why IPv6 is the recommended path.

The engine's pool is dynamic: `warp.pool_size` is an upper bound, the active
ring follows concurrency, and an exit answered with `429` or `403` is retired
and re-registered immediately. `retry.max_attempts` controls how many exits one
request may walk before returning the last refusal.

The legacy external-pool mode (`warp.enabled: false`) is still generated and
documented: it runs one privileged WARP namespace and one tinyproxy per node,
plus `ofu-dedupe`. It remains useful on hosts without a routed IPv6 `/64`, or
as a reference for the de-duplication problem. It shares Cloudflare's IPv4 NAT,
so it is no longer the default.

| Piece | Role |
|---|---|
| `ghcr.io/jasonxu114514/opencode2api` (GPL-3.0) | The engine. Mints the client identity/session headers, serves `/v1/*`, runs the userspace WARP pool, and rotates exits on `429`/`403`. |
| `stack/config.json` | `anonymous: true`, `free_only: true`, empty key lists, and the `warp` block. |
| `WARP_SOURCE_IPV6` in `.env` | The routed `/64` each exit binds its outer source from. Injected by `config-init`, never committed. |
| `stack/warp/`, `stack/tinyproxy/`, `stack/dedupe/` | The legacy external pool, used only when `warp.enabled: false`. |

```
opencode CLI ─┐
9router ──────┼─► opencode2api :8080 ──► userspace WARP exits ──► opencode.ai/inference
OmniRoute ────┘   (identity + retry)      one /64 per exit        (free tier, Bearer public)
```

> Using your own proxies? Set `warp.enabled: false`, put your endpoints in the
> `proxies` array of `config.json`, and `docker compose up -d config-init
> opencode-engine` to skip the WARP, `tinyproxy` and `ofu-dedupe` services.
> That is the way past WARP's address-space ceiling: egress you control.

## Measured results (this repo, 2026-10-15, 6 nodes)

- `/v1/models` → the live free catalogue (9 models on 2026-10-19). The count is
  upstream's to change; query the endpoint rather than trusting any number
  written here.
- `6*7 ?` → `42`. Re-measured 2026-10-18 through the gateway and through 9router
  (`oc2/beam-free`). Through OmniRoute (`9r/snowbuddy-free`) the same request
  returns HTTP 200.
- 5/5 consecutive single-turn completions → HTTP 200.
- Sustained agent session: ~57 `/v1/chat/completion` in an hour, 47 × `200`, 11 × `499` (client-side cancellations), 0 upstream `429`.

**Not measured:** multi-day runs, thousands of requests, behaviour under
sustained saturation. Treat the throughput numbers above as a smoke test, not a
capacity guarantee.

---

## Check the claim in 60 seconds, install nothing

The cheapest way to find out whether this repository is telling the truth is to
ask upstream directly. No Docker, no WARP, no terms to accept, nothing left
running afterwards:

```bash
git clone https://github.com/ebouther/opencode-unlimited-proxy.git
cd opencode-unlimited-proxy
./scripts/gate-probe.py
```

That is the load-bearing half of the whole bundle — the conditions the free
tier refuses requests without, each probed in isolation. The run is paired with
controls: shapes that must be refused, and one that must be accepted. It exits
non-zero rather than reporting anything if the baseline itself is refused.

That is not ceremony. A run where every probe comes back 403 proves nothing,
and a probe that cannot tell you that is worse than no probe.

If the baseline comes back `429` where it expects `200`, that is the egress
block's quota, not a gate and not a broken install. The `Retry-After` points at
the daily reset (00:00 UTC). The default stack has no local proxy ports; check
`/healthz` for the WARP pool and let the engine rotate. In the legacy external
pool you can still pin one node:

```bash
./scripts/gate-probe.py --proxy http://127.0.0.1:18090
```

If that passes and you want the rotating pool behind it, carry on.

## Requirements

- **Linux** (native). The default engine-managed WARP mode needs
  `network_mode: host` and outbound UDP 2408. The legacy external pool
  additionally needs `/dev/net/tun` plus `privileged` and `NET_ADMIN`, which
  Docker Desktop on macOS and Windows does not hand out.
- **Docker Engine** + **Docker Compose v2** (`docker compose`, not `docker-compose`).
- A routed IPv6 `/64` for the recommended path. Without one, WARP falls back to
  its shared IPv4 NAT, which is usually exhausted.
- Outbound TCP 443 and UDP 2408 to Cloudflare. The legacy pool also uses TCP
  7844.
- Roughly 100 MB for the engine; the legacy pool adds ~150 MB per node.

## On macOS or Windows

There is no supported path today. This section says exactly what stops it,
rather than suggesting something that will fail on you.

Two pieces are still Linux-bound:

- **The legacy WARP nodes** (only when `warp.enabled: false`). Each one creates a
  TUN device and routes through it, which needs `/dev/net/tun` and privileged
  containers.
- **The engine's host networking.** It runs with `network_mode: host` so the
  userspace WARP tunnel can bind a source address from your routed `/64`. On
  Docker Desktop that host namespace belongs to the VM, so the prefix and the
  UDP path are not yours.

The engine image itself is not the problem: it is published for both `amd64`
and `arm64`, so it runs natively on Apple Silicon. What does not work is how this
stack reaches the internet, not the gateway in the middle.

**If you want to close the gap**, the piece to rewrite is the network path, not
the engine. Putting the engine on an ordinary bridge network, addressing the pool
by service name instead of `127.0.0.1`, and listening on `0.0.0.0` inside the
container while publishing to `127.0.0.1` on the host: that configuration was
built and measured end to end on Linux, answering correctly through the pool with
the loopback-only binding preserved. It has never been run on Docker Desktop, and
that is the honest gap.

Until someone runs it there, your realistic options are a Linux host, or a remote
proxy reachable by hostname — `proxies` accepts any URL, so
`http://user:pass@your-proxy:3128` is a valid entry. That path is untested here,
so treat it as a lead rather than a recipe.

## Variations, verification and defaults

The commands in [get it running](#get-it-running) bring up the default
engine-managed WARP pool. This section is everything that varies from that.

**A larger WARP pool**: raise `warp.pool_size` in `stack/config.json` (1-32).
The active ring follows concurrency, so a quiet deployment stays small. Raise
it when several users share the gateway:

```json
"warp": { "enabled": true, "pool_size": 8, "stack": "ipv6", "source_ipv6": "" }
```

**The legacy external pool** is still generated. Set `"warp": { "enabled":
false }` in `stack/config.json`, then:

```bash
python3 generate-compose.py --nodes 6
docker compose up -d --build
```

`docker-compose.yml` is generated and CI checks it against
`generate-compose.py`, so editing that file by hand will fail the
`generated files are in sync` job.

Already have your own proxies? Skip WARP entirely: set `warp.enabled: false`,
put them in the `proxies` array of `config.json`, then start only the two
services that do not touch Cloudflare —

```bash
docker compose up -d config-init opencode-engine
```

No WARP, no `tinyproxy`, no `ofu-dedupe`, and you never touch Cloudflare's
terms.

Then verify:

```bash
../scripts/smoke.sh
```

`smoke.sh` checks the bundle end to end and exits non-zero if anything is off.
In the default mode, `/healthz` is the useful signal: `warp.status` must be
`ready`, and `warp.active` tells you how many exits are in the ring. A request
can still take a few attempts when a fresh exit is already at its daily limit;
the engine rotates on `429` rather than failing the request immediately.

In the legacy external pool, a drained node is not a fault: `ofu-dedupe` drains
the surplus when WARP cannot give every node its own address, and a drained
node stops listening on purpose. `2/2 serving, 4 drained` is a healthy result
there, and the check that carries the meaning is that every serving node holds a
distinct address.

To check *upstream* — whether the
free-tier fingerprint is still what this repository says it is, independently
of anything running locally:

```bash
../scripts/gate-probe.py          # baseline + controls, see --help
```

**Both listeners bind to `127.0.0.1` by default**, which is every documented use
of this stack. To reach it from another machine, put it behind your own TLS
terminator or a VPN rather than widening the bind: the API is anonymous by
design (`anonymous: true`, see above), so anything that can reach `:8080` can
spend your quota.

Two defaults in `stack/config.json` are the upstream's and are meant to be
changed before this is exposed anywhere:

| Setting | Shipped value | Why it matters |
|---|---|---|
| `server_keys` | `["local-test-key"]` | The only thing stopping unbilled traffic |
| `webui.username` / `webui.password` | `admin` / `changeme-changeme` | The dashboard is the admin surface |

Set both in `stack/config.json`, then `docker compose up -d` — `config-init`
re-seeds the volume from that file on every start, so an edit that is not
followed by a restart is silently discarded.

## Point your tools at it

### OpenCode CLI

```jsonc
// ~/.config/opencode/opencode.jsonc
{
  "model": "opencode-unlimited-proxy/beam-free",
  "provider": {
    "opencode-unlimited-proxy": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "opencode-unlimited-proxy",
      "options": {
        "baseURL": "http://127.0.0.1:8080/v1",
        "apiKey": "local-test-key"          // must match stack/config.json server_keys
      },
      "models": {
        "beam-free": {},
        "big-pickle": {},
        "deepseek-v4.1-flash-free": {},
        "glm-5.3-flash-free": {},
        "longcat-2.5-preview-free": {},
        "mimo-v2.6-flash-free": {},
        "minimax-m3.1-flash-free": {},
        "nemotron-3.5-lightning-free": {},
        "nemotron-3.5-super-vl-free": {}
      }
    }
  }
}
```

**The list above is a snapshot, not a contract.** The engine asks OpenCode for the
live catalogue on every request, so `/v1/models` is always the authoritative answer.
See [the model list moves](#the-model-list-moves).

`beam-free` is the default above as a safe starting point: it is the first
model in the live catalogue and answered every probe here. It does reason, so a
tight `max_tokens` budget may go to thinking rather than the answer — raise
`max_tokens` if the answer comes back empty, or pick a model whose reasoning
you have measured yourself.

What I have actually measured, on the anonymous path only:

| | single turn | multi-turn |
|---|---|---|
| All nine | answered | — |
| `ll-alpha-free` | yes | yes, but only at `max_tokens` ≥ 300 |
| `glm-5.3-flash-free` | yes | one correct answer, then `429`, then `502` on every node |
| `big-pickle` | yes | spends the budget on reasoning |
| `beam-free` | yes | not tested; reasoning model, see Troubleshooting |

The remaining eight are untested on multi-turn, so "default" here means a safe
starting point and not a ranking. Nothing in this repository tells you which
model is best, and if you find that out it would be worth writing up.

```bash
opencode run "23*17 ?"
```

### 9router

Add an OpenAI-compatible connection:

| Field | Value |
|---|---|
| Base URL | `http://127.0.0.1:8080/v1` |
| API key | `local-test-key` |
| Model prefix | `oc2`, or anything you like |
| API type | `chat` |

Then reference a model as `<prefix>/<model>`, so `oc2/beam-free`. Measured end
to end through 9router: HTTP 200, and `6*7 ?` came back `42`.

`API type` is the field lists like this usually skip, and it is not cosmetic. All
nine free models currently on the tier are chat-native, which is what `chat` is
for. (The two `muse-spark-*-contributor-free` models that were `responses`-native
upstream were retired off the tier on 2026-10-19, so the `responses` case no
longer applies to the free catalogue today.)

Expect 9router to mark the connection `unavailable` from its own connectivity test.
That was the state of a working node here: `testStatus: unavailable`, a 403 from
upstream, and ordinary routed use through the same node working fine. The likely
reason is that the test request does not carry the client fingerprint the free tier
gates on. Check it with a real request rather than the test badge:

```bash
curl -s http://127.0.0.1:3000/v1/chat/completions \
  -H "Authorization: Bearer <your-9router-key>" -H 'Content-Type: application/json' \
  -d '{"model":"oc2/beam-free","messages":[{"role":"user","content":"23*17 ? Reply with just the number."}]}'
```

### OmniRoute

Add an OpenAI-compatible node pointing at `http://127.0.0.1:8080/v1`, with
`local-test-key` as the key, then reference it as `<node-prefix>/<model>` from a
combo.

This one is given at the HTTP level only: the base URL, the key and the prefix are
what the gateway speaks, and they are verified. It has not been clicked through
OmniRoute's own interface, so treat the field names as the ones to look for rather
than a screenshot's worth of certainty.

## The model list moves

Every model name in this file is a snapshot, not a contract. Upstream adds and
retires anonymous models, and this repository does not control that. During the
last round of work here, `cat-jam-free` was retired and `beam-free` appeared on
the same afternoon, with no announcement either way. On 2026-10-19
`space-bunny-free` and the two `muse-spark-*-contributor-free` models were
marked `deprecated` upstream and dropped from the free catalogue, so they no
longer resolve through this gateway; `minimax-m3.1-flash-free` took their place.
If you still have one of them in a client config, switch the name.

So treat `/v1/models` as the truth and any list here as a convenience:

```bash
curl -s http://127.0.0.1:8080/v1/models -H "Authorization: Bearer local-test-key" \
  | jq -r '.data[].id'
```

Listing models explicitly in a client config only populates the model picker at
startup. It is not a filter. A name upstream has retired will fail to resolve, and
a new one will not appear until you add it yourself.

If a model you expected is gone, the fix is a different name, not a different
configuration.

## Scaling the address pool

The default mode is configured in `stack/config.json`, not generated:

```json
"warp": {
  "enabled": true,
  "pool_size": 4,
  "stack": "ipv6",
  "source_ipv6": ""
}
```

- `pool_size` is an upper bound (1-32). The active ring follows concurrency,
  preloads half of it, and retires idle exits after 60 seconds.
- `source_ipv6` comes from `WARP_SOURCE_IPV6` in `.env`. Each exit binds a
  different source address from that routed prefix, so each gets its own
  egress `/64`.
- `retry.max_attempts` (6 in the shipped config) is how many exits one request
  may walk before returning the last `429`. Raise it when more exits are
  already burned by other WARP users.

More exits mean more `/64` buckets, but not every block is fresh: WARP exits
are shared, and a new one can already be at its daily limit. The engine retires
a refused exit and re-registers immediately, so a request may take a few
attempts before it finds headroom.

The legacy external pool is still resized with the generator:

```bash
cd stack
# first set "warp": { "enabled": false } in config.json
python3 generate-compose.py --nodes 10            # ports 18090-18099
python3 generate-compose.py --nodes 10 --base-port 19090
docker compose up -d --build
```

`generate-compose.py` rewrites both `docker-compose.yml` and the `proxies`
array in `stack/config.json`. In external mode, `ofu-dedupe` owns rotation and
drains nodes that cannot get their own address; see `stack/dedupe/dedupe.py`.
That mode shares Cloudflare's IPv4 NAT and is capped by what the colo hands
out, which is why it is no longer the default.

> Using your own proxies? Set `warp.enabled: false`, fill `proxies` (or
> `proxyfile`) with your endpoints, and start only `config-init` and
> `opencode-engine`. The WARP, `tinyproxy` and `ofu-dedupe` services are not
> started at all.

## Monitoring

- **Dashboard** (`http://127.0.0.1:8081`): request distribution, upstream health,
  counters, error rates.
- **Default mode, WARP pool**:
  ```bash
  curl -s http://127.0.0.1:8080/healthz | jq .warp
  docker logs ofu-engine | grep -iE 'warp|429|403'
  ```
  `/healthz` reports `status`, `active`, `standby`, and `cooling`. The engine
  logs `egress ... ready at <prefix>` for each registration and
  `Rotating ... (HTTP 429)` when it retires a block. Some exits are already at
  their daily limit when they register; that is expected, and the rotation is
  the fix.
- **Legacy external pool**:
  ```bash
  docker logs ofu-dedupe                     # pool line: live / distinct
  docker logs ofu-engine | grep -iE 'proxy became|rate limit|unavailable'
  docker compose -f stack/docker-compose.yml logs -f tinyproxy-a
  ```
  `ofu-dedupe` prints one line per pass:
  ```
  pool: 6/6 nodes live, 6 distinct addresses | alztrk-warp-a=104.28.194.131 ...
  ```
  A `<-- collisions` suffix means it is still working on it; `DRAINED` in the
  log means a node lost its proxy on purpose. A drain is reversible and the
  arbiter reverses it on its own: each drain records the address the node was
  holding, and as soon as nobody holds that address any more the node is put
  back. Watch for `revived, back in the pool holding <ip>` to know it happened.
- **Probe a single legacy node**:
  ```bash
  curl -s -x http://127.0.0.1:18090 https://api.ipify.org; echo
  ```
- **Undo a legacy drain by hand** — normally unnecessary, since the arbiter
  revives a node once its address frees up:
  ```bash
  docker exec ofu-dedupe rm /markers/b && docker logs -f ofu-proxy-b
  docker exec ofu-dedupe cat /markers/b
  ```
  The marker says which address the node was holding, which is what the next
  pass compares against.

Signs that a legacy node is exhausted: the engine logs `proxy became
unavailable`, the dashboard flips it to `Unavailable`, and completions start
failing after the configured retries.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `proxyconnect tcp: dial tcp 127.0.0.1:1809x: connect: connection refused` | Legacy external pool only: that node's proxy is down. `docker compose ps`, then `docker compose up -d tinyproxy-a`. |
| `no healthy anonymous proxies available` | Legacy external pool only: every node failed its health check. Check WARP registration with `docker logs ofu-warp-a`. |
| Edits to `stack/config.json` seem ignored | The engine reads `/var/lib/opencode2api/config.json` from its **named volume** and prefers it over your file. `config-init` re-seeds it on `docker compose up -d`; to force it: `docker compose down && docker volume rm stack_ofu-engine && docker compose up -d`. |
| `rename … device or resource busy` at startup | You bind-mounted the config path directly; it must be seeded by `config-init` instead. |
| `SSL_ERROR_RX_RECORD_TOO_LONG` in the browser | You are on `https://` and/or `localhost`. Use `http://127.0.0.1:8081`. |
| `FreeTierError` (403) | Check the **message**, not just the type — at least four different causes share it. `can only be used from within OpenCode` is a gate and `./scripts/gate-probe.py` says which one. `not available in your country` is a region block that no header fixes. See [the gates doc](docs/free-tier-gates.md#not-a-gate-the-region-block). |
| `warp` is `degraded` or `starting` in `/healthz` | WARP registration failed or is rate-limited. Check `docker logs ofu-engine` for `WARP` and `429`. Outbound UDP 2408 must be allowed. |
| `FreeUsageLimitError` (429) | Not a gate. The egress block ran out of budget, reset daily at 00:00 UTC. Check `Retry-After`; on the default mode, `/healthz` shows the WARP pool and `docker logs ofu-engine` shows exits being retired and re-registered. Raise `warp.pool_size` or `retry.max_attempts`, set a routed IPv6 `/64`, or wait for the reset. See [the quota axis](docs/free-tier-gates.md#not-a-gate-the-quota-axis). |
| `[400] … messages[N].reasoning` on multi-turn chats | Known conversion bug in the upstream engine on reasoning models. It was observed on `cat-jam-free`, which has since been retired off the tier, so the model that triggered it is gone but the class of model is not: `ll-alpha-free` and `beam-free` both reason today. I have not seen this 400 reproduce on them, so treat it as a watch item rather than a known outcome. Pick a non-reasoning model for multi-turn if you can (none currently on the free tier), or disable the reasoning field client-side. `space-bunny-free` was the previous recommendation but is now deprecated upstream. |
| A reasoning model returns **HTTP 200 with empty content** | The budget went to reasoning, not the answer. This is not a gateway fault — the request succeeded. `big-pickle` spent 55 of 60 tokens on reasoning across 4 of 6 probes and returned nothing; at `max_tokens: 200` it was 0 of 4. `ll-alpha-free` on multi-turn returned 200 with empty content at `max_tokens: 120`, and the correct answer at 300. `beam-free` reasons as well. The reasoning tokens are not hidden: `/v1/messages` returns them as explicit `thinking` blocks and `/v1/responses` reports `incomplete_details`. Raise `max_tokens`, or pick a model with lower reasoning overhead. `space-bunny-free` (which reported `reasoning_tokens: 0`) is now deprecated upstream and no longer on the free tier. |
| Node restarts in a loop, `bind: address already in use` | Something else holds 8080/8081. The engine uses host networking; free the ports. |

## Legal and terms of service

**Publishing this repository is fine. Operating it is where the risk lives.**
The two are separate questions, and the second one is not about licences.

### Licences — verified compatible
`MIT` for this repository's own files. The engine
(`ghcr.io/jasonxu114514/opencode2api`, **GPL-3.0**) is *not* redistributed
here, only referenced as a public container image, so no GPL obligation attaches
to these files. `stack/warp/` (legacy external pool) is MIT code from
`alztrk/opencode-ip-rotator` with its licence text preserved. `tinyproxy`
(GPL-2.0) is installed from Alpine at build time, not redistributed. See
[`NOTICE`](NOTICE).

Using the name "opencode" is nominative, to state compatibility. No affiliation
with or endorsement by OpenCode is claimed or implied.

### Cloudflare WARP — read this before running the default pool
The default egress is Cloudflare WARP. The engine implements the tunnel itself
(userspace WireGuard, no TUN), but the traffic still leaves through Cloudflare's
consumer network under the [Cloudflare Application Terms of
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
product is for. Running several exits against the consumer service sits outside
the "one device you control" model in §2. Neither is clearly permitted, and
neither is clearly forbidden — that is the honest reading.

What this means in practice: the account can be closed, usage can be limited or
blocked, and complaints go to a dedicated abuse mailbox
(`warpabuse@cloudflare.com`). Cloudflare's liability for consumer products is
capped, and disputes go to arbitration. A breach of terms is a contract matter,
not a criminal one — and publishing this repository is not the breach; running it
is.

Because of that, **the WARP terms are never accepted for you**: `config-init`
refuses to seed the config unless you set `WARP_ACCEPT_TOS=1` in `stack/.env`,
which is an explicit act of yours. The legacy external nodes enforce the same
gate.

### The alternative that sidesteps all of it
This bundle makes no assumption about where the proxies come from. `config.json`
takes any HTTP/SOCKS proxy list. If you have your own egress — a residential
pool, a datacentre fleet, a company gateway — use that and start only
`docker compose up -d config-init opencode-engine`. Cloudflare's terms then have
no bearing on your setup, and neither do their rotators' limits.

### OpenCode's own terms

**Not verified here, and this is a real contract question.** OpenCode's terms for
the free tier have not been read or checked for this repository, and the honest
answer is that only you can decide whether your use complies.

The distinction this repository draws, so that the reasoning is at least visible
rather than hidden in the implementation:

| What the bundle does | What it deliberately does not do |
|---|---|
| Makes a gateway send the request shape the official client sends, so it is treated as one | Does not forge, replay or otherwise abuse a paid entitlement |
| Publishes the exact conditions, with a probe that can falsify them | Does not obscure that the point of those conditions is to refuse non-official callers |
| Ships a pool of distinct egress addresses | Does not pretend those addresses are anything other than what they are |

The second row is the awkward one and deserves to be said plainly: **the reason
the free tier refuses third-party callers is that it is meant for the official
client.** Working around that refusal is the whole function of this bundle. Some
operators in this ecosystem draw the line there — one maintainer declined to help
with an essentially identical problem on the grounds that it breaches Anomaly's
terms. That is a reasonable position and you may hold it too.

The egress pool is a second, separate question. Quota is *believed* to be
bucketed per source address (see the quota axis section — inferred, not measured),
which is why several addresses mean more headroom. If it is not, the pool adds
nothing on that axis and you are left with the request-shape workaround alone.

**Decide for yourself, at the scale you intend to run.** A local single-user
gateway is a different proposition from a hosted service with many users, and the
line between them is not one this repository can draw for you.

---

## Known limitations

- The quota is per egress block (`/64` IPv6, `/32` IPv4), reset daily at
  00:00 UTC. More distinct blocks means more budget, never unlimited budget.
- Cloudflare's WARP IPv4 egress is one shared NAT address. Without a routed
  IPv6 `/64`, every exit competes for the same exhausted bucket. That is why
  `WARP_SOURCE_IPV6` is the recommended path, not an optional extra.
- WARP exits are shared and not all fresh. A newly registered exit can already
  be at its daily limit; the engine rotates on `429`/`403`, so a request may
  take a few attempts and some seconds before it lands on headroom.
- WARP is a shared consumer network: exits can repeat, be geolocated
  unexpectedly, or be blocked outright.
- `config-init` overwrites the engine config in the volume on every `up`.
  Local edits made through the dashboard are lost on the next `up -d`.
- The legacy external pool (`warp.enabled: false`) still builds two images and
  needs privileged containers and `/dev/net/tun`. `stack/warp/` is third-party
  MIT code; see `NOTICE`.

## Repository layout

```
stack/
  docker-compose.yml     generated — do not hand-edit
  config.json            engine config + WARP pool
  generate-compose.py    compose generator (engine-WARP and legacy modes)
  warp/                  legacy external WARP node image (vendored, MIT)
  tinyproxy/             legacy CONNECT proxy image + drain gate
  dedupe/                legacy address de-duplicator
scripts/smoke.sh         one-command verification (needs the stack running)
scripts/gate-probe.py    isolates each upstream gate; needs no stack running
tests/                   unit tests, standard library only
docs/free-tier-gates.md  the four upstream gates, plus the quota axis
docs/gate-timeline.md    dated record of the gates moving, and what it cost
NOTICE                   third-party attribution
```

## Development

```bash
python3 -m unittest discover -s tests -v   # 135 tests, no dependencies, no stack
```

The tests cover the pool generator, the de-duplicator's decision logic, and the
shipped configuration — the three places where a silent mistake costs you a pool
or exposes something you did not mean to. None of them need Docker, network, or
a running stack.

Contributions are welcome; [`CONTRIBUTING.md`](CONTRIBUTING.md) explains the
conventions and, more importantly, how to tell a measured claim from an inferred
one. [`SECURITY.md`](SECURITY.md) covers the threat model, which is short: this
is a local single-user gateway and both listeners bind loopback by default.

CI runs the tests, byte-compiles every Python file, parses and shellchecks every
shell script, validates the compose file, and asserts that `docker-compose.yml`
still matches what `generate-compose.py` generates. That last check is why the
generated file is safe to commit: a hand edit fails CI instead of failing on
your machine later.

Two conventions worth knowing before a first patch:

- **`stack/docker-compose.yml` is generated.** Change `stack/generate-compose.py`
  and re-run it. Editing the compose file directly is caught by CI.
- **A drain writes the address into its marker.** `ofu-dedupe` cannot measure a
  drained node — stopping its proxy is what draining means — so the marker is the
  only surviving record of what that node was holding, and therefore the only
  way the arbiter can notice the address has become free and bring it back. If
  you touch drain logic, keep that invariant.

## Licenses

This repository's own files: **MIT** (`LICENSE`).
Vendored/third-party: see `NOTICE` — notably `stack/warp/` (MIT, © alztrk) and
the engine image `ghcr.io/jasonxu114514/opencode2api` (**GPL-3.0**).
