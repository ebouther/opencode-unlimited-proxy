# Gate change timeline

The four gates in [`free-tier-gates.md`](free-tier-gates.md) are not a contract.
They are reverse-engineered behaviour of an undocumented upstream, and they have
already moved twice in one week. This file is the dated record of *when* and
*what*, so that the next person hitting `403 FreeTierError` can tell whether
their request broke or upstream did.

**How to read the entries.** Each one separates what was measured from what was
inferred. Where an entry is a reconstruction rather than a contemporaneous
record, it says so. This timeline is maintained by hand and will drift; the
authoritative claim about the gates is always the probe, not this file.

---

## ≈2026-10-11 — session identifier layout changes

**Measured (indirectly).** A previously working client shape stopped returning
completions. The failure was not a clean `403`: responses came back `200` with
an apology and no content, which reads as success to any client that only
checks the status code.

The cause was a change in how a session identifier is laid out. `opencode2api`
v1.3.8 introduced `CanonicalSessionID` (PR #69/#70) — a descending-identifier
layout that preserves real identifiers for cache affinity instead of inventing
them per request. Requests built against the old layout were no longer
recognised.

**What it cost.** Most of a debugging session spent looking at the client, since
a `200` gives nothing to grep for.

---

## ≈2026-10-13 — tool-signature quartet and streaming

**Measured, with negative controls.** This change is the one the bisection method
in upstream #4101 isolated: hold everything constant, vary exactly one field.

Two gates turned out to be involved:

| Gate | Field varied | Result |
|---|---|---|
| 3 | tool signature quartet: absent tools, a changed signature, or the wrong protocol | `403` |
| 4 | `stream: false` | `403` |

A request that succeeds proves nothing on its own. What identifies a gate is the
pair — one request that works and one that differs in a single field and does
not. That method is why these two are documented as gates at all rather than as
folklore, and it is preserved in `scripts/gate-probe.py`.

---

## 2026-10-16 — the four gates confirmed, and the host pinned

**Measured.** One baseline request plus five negative controls, each varying
exactly one field:

| Shape | Result |
|---|---|
| baseline (correct shape) | `200` with content |
| `stream: false` | `403` |
| zero tools | `403` |
| fake tool names | `403` |
| non-opencode User-Agent | `403` |
| shaped but unencoded session | `403` |

`6/6` identical from the host's direct egress and through two WARP nodes
(`:18090`, `:18091`). The engine is pinned to `opencode2api` v1.4.0 in
`stack/generate-compose.py`.

Two independent confirmations on this date:

- **`--ua opencode/1.10.0` reproduces the refusal**, which makes the gate probe
  usable as a self-test: it exits `0` while the gates hold and `2` when the
  baseline itself is refused. That is the property that distinguishes "gate 3
  moved" from "this host is being refused".
- **A reasoning model can answer `200` with an empty body** and be perfectly
  healthy — `big-pickle` spent 55 of 60 tokens on reasoning across 4 of 6 probes.
  An empty completion is not a gate failure and should not be read as one. Raise
  `max_tokens`; see the troubleshooting table in the README.

---

## Known limits of this record

- **Not every egress has been tested.** The three passing egresses observed on
  2026-10-16 are all Cloudflare: `185.137.228.16` (the host's direct egress) and
  `104.28.194.131` / `104.28.226.129` (two WARP nodes). No residential or
  non-Cloudflare egress was available, so **egress-reputation effects are not
  ruled out.** If someone with a different network sees the baseline refused
  while the negative controls also fail, that is a different problem from a moved
  gate, and it would be worth reporting.
- **The ≈2026-10-11 and ≈2026-10-13 entries are reconstructed** from the pinned
  engine's changelog and the shape of the failures, not from a contemporaneous
  probe run. They are directionally reliable and should not be treated as
  minute-by-minute history.
- **Upstream state cited elsewhere goes stale.** The note about OmniRoute #16598
  and release v3.8.51 in `free-tier-gates.md` was true on 2026-10-16 and may not
  be now.
- **The time-varying refusal is somebody else's observation.** The entry for
  2026-10-14 above reports a probe set that stopped working mid-day on a fixed
  egress. It has not been reproduced from this host, and one uncontrolled run
  cannot separate an upstream change from a reputation change from measurement
  drift. It is the most promising thing to test properly: `gate-probe.py`
  exits `2` when the baseline is refused, so it doubles as an alarm.

---

## Reporting a change

Open an issue from the **gate behaviour observation** template. It asks for the
pair — a baseline that works and a single-field variation that does not — because
that pair is the only thing that identifies a gate rather than a flaky host.