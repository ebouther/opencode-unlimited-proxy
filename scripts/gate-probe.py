#!/usr/bin/env python3
"""Probe each OpenCode free-tier gate in isolation, with a built-in self-check.

    ./scripts/gate-probe.py                     # probe from this host's own egress
    ./scripts/gate-probe.py --proxy http://127.0.0.1:18090
    ./scripts/gate-probe.py --model mimo-v2.6-flash-free

Why this exists
---------------
docs/free-tier-gates.md states four conditions that the free tier refuses requests
without. Asserting them is not the same as demonstrating them, and "it worked
yesterday" does not distinguish "the gate is intact" from "my probe is broken".
So the baseline probe is paired with controls: shapes that must be refused, plus
one that must be *accepted* so a minimum shape is asserted and not just assumed.
If any refusal control comes back 200, the harness is not measuring the gate and
the positive results mean nothing. The run also fails if the baseline itself is
refused, because then a 403 everywhere proves nothing either.

Every probe sends a real request to the free tier and consumes a little of its
per-address quota. Eight probes is a handful of requests.

This script needs no stack running. It talks to opencode.ai directly, so it is
the check to run when you want to know whether upstream moved, independently of
anything this repository builds.
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
import time
import urllib.error
import urllib.request

UPSTREAM = "https://opencode.ai/zen/v1/chat/completions"
# What the real client sends. It works, but it is *not* the rule: measured
# 2026-10-16, gate 3 requires only one tool named exactly `read`, and bash, glob
# and grep are incidental to passing. Naming this CLIENT_TOOLS rather than
# QUARTET is deliberate -- the old name was read as the rule and invited the
# inference that a missing bash would be refused. It would not be.
CLIENT_TOOLS = ("bash", "glob", "grep", "read")
READ_ONLY = ("read",)
NEAR_MISS = ("read_file",)
# The folklore workaround for this gate is a pasted list of eleven or more
# placeholder tools. Ten genuine names and no `read` must still be refused, or
# "add lots of tools" stays standing as a rule and nobody gets to cut the list
# back to one. This probe is the only one that varies the *count*.
LONG_NO_READ = ("bash", "edit", "glob", "grep", "skill",
                "task", "todowrite", "webfetch", "websearch", "write")
BASE62 = string.ascii_letters + string.digits

# The status code alone cannot tell you what to do. `403 FreeTierError` is
# returned for at least three unrelated reasons, and only one of them is the
# request-shape gate this repository documents. Quoting the message is the
# difference between "my headers are wrong" and "no header will ever work from
# here". Reported, not reproduced: the geo variant is documented from operator
# reports, and has never been observed from this host.
REFUSALS = (
    ("geo", ("not available in your country",),
     "region block -- no request shape will fix this from here"),
    ("shape", ("within opencode",),
     "request-shape gate -- this is the gate docs/free-tier-gates.md documents"),
    ("quota", ("freeusagelimiterror", "usage limit", "rate limit"),
     "quota exhausted for this source address -- this is the quota axis, not a gate"),
    ("auth", ("authentication", "unauthorized", "api key"),
     "credential problem, not the free tier"),
    ("version", ("upgrade required",),
     "stale client -- upstream wants a newer fingerprint"),
)

def classify(status, raw) -> tuple[str, str, str]:
    """Return (kind, message, what_to_do) for a refusal body.

    kind is "ok", "unknown" or one of the REFUSALS keys. Matching is on the
    message, not the error type: every shape-gate refusal is typed
    `FreeTierError`, exactly like the geo and quota refusals.
    """
    text = (raw or "").strip()
    try:
        parsed = json.loads(text)
        message = parsed.get("error", {}).get("message", "") or parsed.get("message", "")
        kind_hint = parsed.get("error", {}).get("type", "")
    except ValueError:
        # An SSE frame or a non-JSON error page; the message is still in there.
        message = text
        kind_hint = ""
    lowered = f"{kind_hint} {message} {text[:400]}".lower()
    for kind, needles, advice in REFUSALS:
        if any(needle in lowered for needle in needles):
            return kind, (message or kind_hint or text[:120]), advice
    if str(status) == "200":
        return "ok", "", ""
    return "unknown", (message or kind_hint or text[:120]), \
        "unrecognised refusal -- upstream may have moved; treat the docs as out of date"

def canonical_id(prefix: str, counter: int = 1) -> str:
    """OpenCode CLI ``Identifier.descending()``: ~(unix_ms << 12 | counter) -> 6 bytes -> 12 hex.

    Upstream decodes this back to a timestamp, so a well-formed-but-arbitrary id
    (sha256, UUID, random hex) decodes to a pseudo-random time and is refused.
    """
    ms = int(time.time() * 1000)
    payload = ~((ms << 12) | counter) & ((1 << 48) - 1)
    head = payload.to_bytes(6, "big").hex()
    # The bitwise NOT guarantees this; assert it so a refactor cannot silently
    # start minting ids that decode out of the validity window.
    assert head.endswith("ffe"), f"canonical layout broken: {head}"
    return f"{prefix}_{head}{''.join(random.choice(BASE62) for _ in range(14))}"

def shaped_session() -> str:
    """Right surface shape, wrong encoding — the control for gate 2."""
    return "ses_" + "".join(random.choice("0123456789abcdef") for _ in range(12)) + b62_suffix()

def b62_suffix() -> str:
    return "".join(random.choice(BASE62) for _ in range(14))

def toolset(names=CLIENT_TOOLS) -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": "t",
              "parameters": {"type": "object", "properties": {}}}} for n in names]

class Probe:
    """One request. Everything the gate looks at is a parameter, nothing is global."""

    def __init__(self, name, gate, expect, *, ua, session, stream, tools, model):
        self.name, self.gate, self.expect = name, gate, expect
        self.ua, self.session, self.stream = ua, session, stream
        self.tools, self.model = tools, model

    def fire(self, opener: urllib.request.OpenerDirector, timeout: int):
        body = {"model": self.model,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "stream": self.stream,
                "max_tokens": 60}
        if self.tools is not None:
            body["tools"] = toolset(self.tools)
        headers = {
            "Authorization": "Bearer public",
            "Content-Type": "application/json",
            "User-Agent": self.ua,
            "x-opencode-client": "desktop",
            "x-opencode-project": "global",
            "x-opencode-session": self.session,
            "x-opencode-request": canonical_id("msg"),
            "Accept": "text/event-stream",
        }
        req = urllib.request.Request(UPSTREAM, data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        started = time.time()
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read(220).decode("utf-8", "replace"), time.time() - started
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(220).decode("utf-8", "replace"), time.time() - started
        except Exception as exc:  # noqa: BLE001 - report, do not explode
            return "ERR", repr(exc)[:160], time.time() - started

def build_probes(args) -> list[Probe]:
    ua_ok = args.ua
    def baseline():
        return Probe("baseline", "-", "200", ua=ua_ok, session=canonical_id("ses"),
                     stream=True, tools=CLIENT_TOOLS, model=args.model)
    return [
        baseline(),
        Probe("stream:false", "gate 4", "403", ua=ua_ok, session=canonical_id("ses"),
              stream=False, tools=CLIENT_TOOLS, model=args.model),
        Probe("zero tools", "gate 3", "403", ua=ua_ok, session=canonical_id("ses"),
              stream=True, tools=None, model=args.model),
        # Expecting 200, so this is the positive half of the claim rather than a
        # negative control. Without it, "read alone passes" is untested and the
        # tool count looks load-bearing when it is not.
        Probe("read alone (minimal)", "gate 3", "200", ua=ua_ok,
              session=canonical_id("ses"), stream=True, tools=READ_ONLY,
              model=args.model),
        # The near miss is the one that actually pins the rule: prefix or suffix
        # and the tool does not count, so "I renamed it" is not a fix.
        Probe("near miss: read_file", "gate 3", "403", ua=ua_ok,
              session=canonical_id("ses"), stream=True, tools=NEAR_MISS,
              model=args.model),
        Probe("real names, no read", "gate 3", "403", ua=ua_ok,
              session=canonical_id("ses"), stream=True,
              tools=("bash", "glob", "grep"), model=args.model),
        Probe("ten real names, no read", "gate 3", "403", ua=ua_ok,
              session=canonical_id("ses"), stream=True,
              tools=LONG_NO_READ, model=args.model),
        Probe("non-opencode UA", "gate 1", "403", ua="curl/8.0", session=canonical_id("ses"),
              stream=True, tools=CLIENT_TOOLS, model=args.model),
        Probe("shaped but unencoded session", "gate 2", "403", ua=ua_ok, session=shaped_session(),
              stream=True, tools=CLIENT_TOOLS, model=args.model),
    ]

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proxy", help="send through this HTTP proxy, e.g. http://127.0.0.1:18090")
    ap.add_argument("--model", default="beam-free", help="free-tier model id")
    ap.add_argument("--ua", default="opencode/1.19.0 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14",
                    help="User-Agent to use for the baseline and every negative control")
    ap.add_argument("--timeout", type=int, default=120, help="per-request timeout in seconds")
    args = ap.parse_args()

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": args.proxy, "https": args.proxy} if args.proxy else {})
    )

    print(f"upstream  {UPSTREAM}")
    print(f"model     {args.model}")
    print(f"egress    {args.proxy or 'direct (this host)'}")

    # Computed, not written down. A hardcoded count silently goes stale the
    # moment a probe is added, and then the summary understates what was tested.
    probes = build_probes(args)
    refusals = sum(1 for p in probes if p.expect == "403")
    minimums = sum(1 for p in probes if p.expect == "200" and p.gate != "-")
    print(f"probes    {len(probes)} (1 baseline + {refusals} refusal controls"
          f" + {minimums} minimum-shape control)\n")

    results = []
    for probe in probes:
        status, raw, elapsed = probe.fire(opener, args.timeout)
        got = str(status)
        kind, message, advice = classify(status, raw)
        results.append((probe, got, kind, message, advice))
        mark = "ok  " if got == probe.expect else "FAIL"
        print(f"[{mark}] {probe.name:<30} HTTP {status:<5} expected {probe.expect:<4} ({elapsed:.1f}s)"
              f"  [{probe.gate}]")
        if kind not in ("ok", "shape"):
            # Only annotate the refusals that are *not* the documented gate.
            # A shape refusal on a negative control is the expected result and
            # does not need narrating on every line.
            print(f"        -> {kind}: {advice}")
            print(f"        -> {message[:140]}")
        else:
            print(f"        {raw.strip()[:150]}")

    print()
    expected = {p.name: p.expect for p, *_ in results}
    ok = sum(1 for p, got, *_ in results if expected[p.name] == got)
    total = len(results)
    baseline = next((r for r in results if r[0].gate == "-"), None)

    print(f"self-check: {ok}/{total} probes matched their expected status")

    if baseline and expected[baseline[0].name] != baseline[1]:
        kind, message, advice = baseline[2], baseline[3], baseline[4]
        print(f"\nBASELINE REFUSED ({kind}).")
        print(f"  {message[:160]}")
        print(f"  {advice}")
        if kind in ("geo", "quota", "auth", "version"):
            # These are not gate movement. Telling someone in a blocked region
            # that "upstream may have moved" sends them to debug headers that
            # were never the problem.
            print("\n  This is NOT the request-shape gate, so no header change will\n"
                  "  help. The negative controls above prove nothing either -- a run\n"
                  "  where every probe is refused demonstrates no gate state.")
            return 3
        print("\n  A run where everything is refused demonstrates nothing. Re-run\n"
              "  docs/free-tier-gates.md step 2 before reading anything into the\n"
              "  negative controls.")
        return 2
    if ok != total:
        print("\nOne or more gates no longer behave as documented. Treat\n"
              "docs/free-tier-gates.md as out of date until it explains the delta.")
        return 1
    print("All gates intact.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
