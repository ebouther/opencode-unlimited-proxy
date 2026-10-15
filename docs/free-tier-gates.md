# The four free-tier gates — and why this bundle works at all

OpenCode's free tier (`Authorization: Bearer public`) is gated on the server
side. A third-party gateway is refused with `403 FreeTierError` unless it
reproduces a specific client fingerprint. This is the single most important
piece of knowledge in this repository, so it is documented here rather than
buried in an issue tracker.

Everything below is sourced from public, live-verified reports:

| Source | What it contributed |
|---|---|
| [9router #4101](https://github.com/decolua/9router/issues/4101) (+ PR #4105, #4132) | Gates 1, 3, 4 — live bisection with a genuine client held constant |
| [OmniRoute #16571](https://github.com/diegosouzapw/OmniRoute/issues/16571) (+ comment `issuecomment-6161953486`) | Gate 2 — the session *value* is validated, not just its presence |
| [OmniRoute #16328](https://github.com/diegosouzapw/OmniRoute/issues/16328) | Cross-host confirmation that header presence alone is not enough |
| [OmniRoute #14911](https://github.com/diegosouzapw/OmniRoute/issues/14911) (PR #14013) | Independent reimplementation of the same four conditions |
| [opencode2api v1.3.8](https://github.com/jasonxu114514/opencode2api/releases/tag/v1.3.8) (PR #69/#70) | `CanonicalSessionID` — the reference implementation this bundle relies on |

---

## Gate 1 — User-Agent

Upstream requires `User-Agent: opencode/<version>` with **version ≥ 1.17.0**.
A bare `opencode`, or any third-party client UA, is refused with
`403 FreeTierError`. Older versions get `426 Upgrade Required`.

A real client sends something like:

```
User-Agent: opencode/1.19.0 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14
```

## Gate 2 — canonical session id

`x-opencode-session` must match:

```
/^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$/      # 30 characters
```

…and it is not enough to look right. Upstream decodes the 12 hex characters back
through OpenCode CLI's `Identifier.descending()` layout and rejects values whose
implied timestamp falls outside a validity window:

- 48-bit payload: `~(unix_ms << 12 | counter)`, bitwise NOT
- rendered as 6 bytes → 12 hex chars, so the **low 3 hex always read `ffe`**
- decoded timestamp must be recent (measured window: at least ±30 days)

Any other encoding — random hex, a sha256 digest, a UUID — decodes to a
pseudo-random timestamp and is refused. This is why gateways that "just hash a
seed" 403 even though they send a perfectly well-formed `ses_` id. The gate
appeared around **2026-10-13**.

If you mint sessions yourself: build the id at mint time, keep it stable for
the life of the session (it drives prompt-cache affinity), and hex-encode the
first **6 bytes**, not `subarray(0, 12)` — that is 12 bytes, and interpolating
a `Buffer` UTF-8-decodes it into mojibake.

## Gate 3 — the tool-signature quartet

With `Bearer public`, upstream inspects the tool declarations:

- **0–3 tools → 403**
- the quartet **`bash`, `glob`, `grep`, `read`** → 200 on both
  `/chat/completions` and `/responses`
- extra tools alongside the quartet are fine
- *fake* tool names instead of the quartet → 403

Consequence worth internalising: **a plain chat caller that sends no tools at
all is refused.** Generic OpenAI-compatible clients (`curl`, most SDKs, simple
agents) fail by default. This is the most common silent cause of
`FreeTierError` for newcomers.

## Gate 4 — streaming

`stream: false` is refused with 403 on both endpoints even when everything else
is valid. Callers asking for JSON still have to be served by forcing SSE
upstream and reassembling the response.

---

## What this bundle does about it

The engine, `ghcr.io/jasonxu114514/opencode2api`, implements all four:

| Gate | Engine behaviour |
|---|---|
| 1 | Sets a versioned `opencode/<ver>` UA |
| 2 | `CanonicalSessionID` (v1.3.8+): descending identifier layout, real ids preserved for cache affinity |
| 3 | Merges the quartet into the outgoing tool list, preserving caller tools verbatim |
| 4 | Forces `stream: true` upstream, reassembles JSON for non-stream callers |

Verified live against this bundle (2026-10-16), engine v1.4.0:

| Request shape | Result |
|---|---|
| 1 message, no `tools`, `stream` omitted | `200`, content returned |
| 1 message, no `tools`, `"stream": false` | `200`, content returned |
| 1 fake tool (`totally_fake_tool`), no quartet, `stream: false` | `200`, content returned |

The last two are exactly the shapes that return 403 from a gateway that only
does gates 1–2. `scripts/smoke.sh` deliberately sends the zero-tool +
`stream:false` shape, because it is the cheapest canary for upstream changing
these rules again.

## When upstream moves again

This fingerprint is not a contract — it has already changed twice (≈2026-10-11
and ≈2026-10-13). When requests start failing with `403 FreeTierError`, or with
`200` responses containing an apology and no completion, check in this order:

1. `docker logs ofu-engine | grep -iE '403|FreeTierError|426'`
2. `./scripts/smoke.sh` — does the zero-tool shape still pass?
3. Bump the engine: `stack/generate-compose.py` pins `v1.4.0`; check
   [opencode2api releases](https://github.com/jasonxu114514/opencode2api/releases)
   for a newer tag, edit `ENGINE_IMAGE`, then `docker compose up -d --build`
4. Compare against the linked upstream issues — the bisection method in
   #4101 (hold everything constant, vary one field) is what found gates 3–4.

## Related upstream state (as of 2026-10-16)

- **OmniRoute** is fixing this natively in
  [PR #16598](https://github.com/diegosouzapw/OmniRoute/pull/16598) (minting
  descending-identifier sessions). The maintainer reports cross-host probes in
  #16328 still receive 403 with valid sessions, so it is **not** resolved yet;
  latest release v3.8.51 predates it.
- **9router** has the equivalent fix in flight (#4105, #4132) and a standing
  request for native proxy-pool rotation in
  [#3354](https://github.com/decolua/9router/issues/3354) — the exact problem
  this bundle's WARP pool works around today.
- When either lands, this bundle remains useful for **quota headroom** (more
  egress buckets), not for compatibility.
