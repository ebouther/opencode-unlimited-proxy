# Security policy

## What this project is, and what that means for its security

This is a **local, single-user gateway**. It runs on your machine, serves your
tools, and spends nobody's money but your own. It is not built to be exposed to
the internet, and the defaults reflect that: both listeners bind `127.0.0.1`.

Read this section before exposing it anywhere. It is short because the honest
answer is short.

## Defaults as shipped

| Surface | Default | Reachable from |
|---|---|---|
| API `:8080` | `listen: 127.0.0.1:8080`, `anonymous: true` | this machine only |
| Dashboard `:8081` | `listen: 127.0.0.1:8081` | this machine only |
| API key | `server_keys: ["local-test-key"]` | — |
| Dashboard login | `admin` / `changeme-changeme` | — |

`anonymous: true` is the feature, not a mistake: the gateway is meant to be
usable by your own tools without a credential. It also means the API has no
authentication of its own, so **the bind address is the only access control on
it.** If you widen that bind without first setting a real key and putting it
behind something that terminates TLS, you have published a way to spend your
quota.

The API key you set is still not an authentication system in the usual sense.
Do not rely on it alone to make an internet-facing service safe.

## Before exposing this to another machine

1. Set a real `server_keys` value and a real `webui.password` in
   `stack/config.json`.
2. Restart the engine, not just the stack. `config-init` re-seeds the config
   volume on every `up`, but the engine only reads it at startup:
   ```bash
   docker compose -f stack/docker-compose.yml up -d
   docker compose -f stack/docker-compose.yml restart opencode-engine
   ```
   An edit with no restart is silently discarded.
3. Put it behind a TLS terminator or a VPN. Do not expose a plaintext HTTP
   listener holding an admin surface.
4. Confirm what is actually listening — `network_mode: host` means the engine
   binds the host directly, and the compose file's `ports:` will not tell you:
   ```bash
   ss -ltnp | grep -E '8080|8081'
   ```

## The WARP nodes are privileged by necessity

Each `alztrk-warp-*` container runs with `privileged: true`, `cap_add: NET_ADMIN`
and `/dev/net/tun`, because a WARP tunnel is a network-namespace construct and
this is how it is built. That is inherent to the approach, not an oversight.

The practical consequences:

- **Do not run untrusted code inside these containers.** There is none by
  design, but a `docker exec` into one is root on your host.
- **The WARP containers run WARP's own client**, which is a third-party binary
  from a project this repository does not control. See `NOTICE`.
- **Each node is a distinct egress.** That is the whole point, and it is also
  what makes the pool attractive to abuse. Six nodes means six source
  addresses, which is more capacity than one person needs. See the terms note
  in the README before scaling it up.

## Out of scope

- **Traffic isolation between tenants.** There is one pool and one quota. Not
  multi-tenant.
- **Rate limiting or abuse control.** The engine retries, it does not police.
- **Auditing.** There is a request ring buffer in the engine, not an audit log.
- **Protection against a malicious upstream.** The gateway's four request-shape
  gates are reverse-engineered behaviour, documented in
  [`docs/free-tier-gates.md`](docs/free-tier-gates.md). They are implemented by
  the pinned upstream engine, not by this repository. If the upstream changes,
  those gates change with it — see the known limitations in the README.

## Reporting a vulnerability

Open a GitHub issue, or a private advisory if the details would help an
attacker first. Please include what you observed, the steps, and the version or
commit.

Two things that are **not** vulnerabilities here, so you do not have to report
them:

- WARP nodes sharing an egress address. That is expected: the address pool per
  region is small, and `ofu-dedupe` drains the surplus. It is documented.
- The four request-shape gates failing on a correctly-shaped client request.
  The gate probes in `scripts/gate-probe.py` reproduce the current behaviour;
  if it has changed, that is genuinely interesting and worth reporting as an
  upstream-behaviour change.