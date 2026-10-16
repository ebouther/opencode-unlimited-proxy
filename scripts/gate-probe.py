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
So the baseline probe is paired with four negative controls: shapes that must be
refused. If any negative control comes back 200, the harness is not measuring
the gate and the positive results mean nothing. The run also fails if the
baseline itself is refused, because then a 403 everywhere proves nothing either.

Every probe sends a real request to the free tier and consumes a little of its
per-address quota. Six probes is a handful of requests.

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
QUARTET = ("bash", "glob", "grep", "read")
BASE62 = string.ascii_letters + string.digits

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

def toolset(names=QUARTET) -> list[dict]:
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
                     stream=True, tools=QUARTET, model=args.model)
    return [
        baseline(),
        Probe("stream:false", "gate 4", "403", ua=ua_ok, session=canonical_id("ses"),
              stream=False, tools=QUARTET, model=args.model),
        Probe("zero tools", "gate 3", "403", ua=ua_ok, session=canonical_id("ses"),
              stream=True, tools=None, model=args.model),
        Probe("fake tool names", "gate 3", "403", ua=ua_ok, session=canonical_id("ses"),
              stream=True, tools=("_noop", "websearch2"), model=args.model),
        Probe("non-opencode UA", "gate 1", "403", ua="curl/8.0", session=canonical_id("ses"),
              stream=True, tools=QUARTET, model=args.model),
        Probe("shaped but unencoded session", "gate 2", "403", ua=ua_ok, session=shaped_session(),
              stream=True, tools=QUARTET, model=args.model),
    ]

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proxy", help="send through this HTTP proxy, e.g. http://127.0.0.1:18090")
    ap.add_argument("--model", default="space-bunny-free", help="free-tier model id")
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
    print(f"probes    {len(build_probes(args))} (1 baseline + 5 negative controls)\n")

    results = []
    for probe in build_probes(args):
        status, raw, elapsed = probe.fire(opener, args.timeout)
        got = str(status)
        results.append((probe, got))
        mark = "ok  " if got == probe.expect else "FAIL"
        print(f"[{mark}] {probe.name:<30} HTTP {status:<5} expected {probe.expect:<4} ({elapsed:.1f}s)"
              f"  [{probe.gate}]")
        print(f"        {raw.strip()[:150]}")

    print()
    expected = {p.name: p.expect for p, _ in results}
    ok = sum(1 for p, got in results if expected[p.name] == got)
    total = len(results)

    print(f"self-check: {ok}/{total} probes matched their expected status")

    if not any(p.gate == "-" and expected[p.name] == got for p, got in results):
        print("\nBASELINE REFUSED. A run where everything 403s demonstrates nothing --\n"
              "upstream may have moved. Re-run docs/free-tier-gates.md step 2 before\n"
              "reading anything into the negative controls.")
        return 2
    if ok != total:
        print("\nOne or more gates no longer behave as documented. Treat\n"
              "docs/free-tier-gates.md as out of date until it explains the delta.")
        return 1
    print("All gates intact.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
