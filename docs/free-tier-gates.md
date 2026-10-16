# The free-tier gates — and why this bundle works at all

OpenCode's free tier (`Authorization: Bearer public`) is gated on the server
side. A third-party gateway is refused with `403 FreeTierError` unless it
reproduces a specific client fingerprint. This is the single most important
piece of knowledge in this repository, so it is documented here rather than
buried in an issue tracker.

There are four such gates, and they are all about the **shape of one request**.
They are not the whole story: quota is a separate axis with its own failure
mode, covered under [Not a gate: the quota axis](#not-a-gate-the-quota-axis)
below. Confusing the two is the most common misdiagnosis — a caller can satisfy
every gate in this document perfectly and still be refused, with a different
status code, for an unrelated reason.

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

## Proving the four gates yourself

Every claim above is one field away from its counterfactual, so the four can be
isolated mechanically rather than argued:

```bash
./scripts/gate-probe.py                                  # this host's own egress
./scripts/gate-probe.py --proxy http://127.0.0.1:18090   # through one pool node
```

It sends one baseline that must be accepted and five probes that each break
exactly one gate and must be refused. It needs no stack running — it talks to
`opencode.ai` directly, which is the point: it tells you whether upstream moved,
independently of anything this repository builds.

The self-check is the part that matters. A 200 proves nothing unless the same
harness can also produce a 403, so the run reports `6/6 probes matched their
expected status` and fails otherwise. A baseline that is itself refused is
reported separately and exits `2`, because a run where everything is `403`
demonstrates nothing — that is what an upstream change looks like, and it must
not be mistaken for four healthy gates.

Verified 2026-10-16, `6/6`, identical from the host's direct egress and through
two pool nodes.

---

## Not a gate: the quota axis

The four gates above decide whether a request is *admitted*. Separately, the
free tier meters usage, and when an address runs out the refusal is a different
status code with a different name:

| | Did the request run? | Status | Type |
|---|---|---|---|
| Shape gates 1–4 | no — refused before execution | `403` | `FreeTierError` |
| Quota axis | yes — it ran, the budget was spent | `429` | `FreeUsageLimitError` |

A caller can satisfy all four gates perfectly, send a textbook request, and
still get a `429`. That is not a gate failure and nothing about the fingerprint
will fix it.

**What is measured here.** Six nodes were serving three distinct source
addresses. Five nodes shared one address, so five nodes drew on one budget.
Widening the pool to six distinct addresses removed those 429s, with the
identity and request shape held constant across the change — which rules out
the fingerprint and points at the address as the variable.

**What is inferred, not measured.** That upstream's counter actually keys on
the source address. No controlled A/B was run here: two addresses, identical
request streams, watching each start returning 429. The observation is
consistent with per-address bucketing and hard to explain otherwise, since the
pool widened throughput with nothing else changed — but it is a strong
inference from one system, not a measurement of upstream's accounting.

**How to falsify it.** Send a fixed request stream through two nodes and record
when each begins returning 429. Per-address bucketing predicts the two curves
starting at different times. A global or per-key cap predicts them starting
together. That experiment is cheap; it simply has not been done, so treat the
claim accordingly when you rely on it.

This axis is what the proxy pool exists for. It is also why the pool refuses to
pretend: `ofu-dedupe` measures real egress so that six nodes are not quietly
serving as three.

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
3. `./scripts/gate-probe.py` — which gate moved? Independent of the stack, and
   it distinguishes "gate 3 changed" from "this host is being refused".
4. Bump the engine: `stack/generate-compose.py` pins `v1.4.0`; check
   [opencode2api releases](https://github.com/jasonxu114514/opencode2api/releases)
   for a newer tag, edit `ENGINE_IMAGE`, then `docker compose up -d --build`
5. Compare against the linked upstream issues — the bisection method in
   #4101 (hold everything constant, vary one field) is what found gates 3–4.

A `429 FreeUsageLimitError` skips all of this. That is the quota axis above, and
none of these steps will move it.

## Related upstream state (as of 2026-10-16)

- **OmniRoute** is fixing this natively in
  [PR #16598](https://github.com/diegosouzapw/OmniRoute/pull/16598) (minting
  descending-identifier sessions). The maintainer reports cross-host probes in
  #16328 still receive 403 with valid sessions, so it is **not** resolved yet;
  latest release v3.8.51 predates it.
- **#16328 has an open question this repository can partially answer.** Several
  operators there suspect the residual refusal is *egress reputation* rather
  than request shape. As of 2026-10-16 the baseline shape succeeds from three
  distinct Cloudflare source addresses on this host, so it is not a uniform
  Cloudflare block. That does **not** rule reputation bucketing out — all three
  are Cloudflare addresses, and no residential egress was available to test
  with. What it does establish is that the refusal is not universal, which the
  reports from blocked hosts could not show on their own.
- **The refusal appears to vary over time on a fixed egress, which is harder to
  explain than static reputation.** In #16328 on 2026-10-14 an operator reported
  that the exact probes which had returned `200` earlier the same day were being
  refused again, from the same host, with session value held constant. If that
  holds, then the blocked hosts are not permanently blocked and the gate is not
  a property of their address alone. **This is reported, not reproduced here** —
  it comes from someone else's host, and a single uncontrolled day of probing
  cannot separate "the upstream changed" from "that egress's reputation
  changed" from "my measurement drifted". It is worth repeating: re-run
  `scripts/gate-probe.py` before and after a long idle period.
- **9router** has the equivalent fix in flight (#4105, #4132) and a standing
  request for native proxy-pool rotation in
  [#3354](https://github.com/decolua/9router/issues/3354) — the exact problem
  this bundle's WARP pool works around today.
- When either lands, this bundle remains useful for **quota headroom** (more
  egress buckets), not for compatibility.

