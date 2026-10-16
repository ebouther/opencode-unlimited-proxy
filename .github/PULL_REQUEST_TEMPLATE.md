## What this changes

<!-- One or two sentences. If it fixes a bug, name the failure it caused. -->

## Why

<!-- What was wrong or missing. If you are correcting a claim in the docs, say
     which one and why it was overstated. -->

## How it was verified

<!-- The part that matters most. What did you run, and what did it print?

     If you fixed a bug, a test that fails before the change and passes after.
     Saying "I reverted the line and watched it fail again" is worth more than
     a green suite, because it shows the test is load-bearing rather than
     merely passing. -->

```
paste the relevant output
```

## Measured or inferred?

<!-- Required for anything touching docs/free-tier-gates.md or the pool's
     behaviour. Pick one per claim, not one for the PR. -->

- [ ] **Measured** — ran it, have the output, dated below
- [ ] **Inferred** — reasoned from observations, not isolated
- [ ] **Speculative** — a guess

Date: <!-- 2026-10-16 -->
Command: <!-- what someone else could run to reproduce the number -->

## Checklist

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `./scripts/smoke.sh` still reports ALL CHECKS PASSED (if the stack is running)
- [ ] If `stack/docker-compose.yml` changed, the change came from `stack/generate-compose.py`
- [ ] If `stack/config.json` changed, the engine restart requirement is noted above
- [ ] No new runtime dependency
- [ ] No secrets, keys, tokens, or egress addresses that identify me