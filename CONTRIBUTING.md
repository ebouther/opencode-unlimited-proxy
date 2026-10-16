# Contributing

Thanks for looking. This repository is small and deliberately boring; the bar
is not "does it work on my machine", it is "can someone else tell whether it
works".

## Run the checks

```bash
python3 -m unittest discover -s tests -v   # 114 tests, stdlib only, no Docker
./scripts/smoke.sh                         # needs the stack running
```

CI runs the tests, byte-compiles every Python file, parses and shellchecks every
shell script, validates the compose file, and asserts that `docker-compose.yml`
still matches what `generate-compose.py` produces. Nothing needs installing —
if a check would require a dependency, that is a bug in the check.

## The one rule that matters most

**Distinguish what you measured from what you inferred.**

Most of this project's value is a set of claims about how an undocumented
upstream behaves. Those claims are only worth anything while they are honest
about their own strength, so writing down which is which is the single most
useful thing a contribution can do.

| Strength | Means | Write it as |
|---|---|---|
| Measured | You ran it and have the output | "6/6 distinct, measured 2026-10-16" |
| Inferred | Reasoned from observations, not isolated | "quota appears to be bucketed per source address" |
| Speculated | A guess | "I suspect the cooldown is the cause" |

A claim that turns out to be weaker than stated is worth correcting in public,
and is not held against you. A claim that stays strong because it was labelled
weak from the start is the whole point. Concretely, when you change
[`docs/free-tier-gates.md`](docs/free-tier-gates.md), say which of the three a
statement is, and if you measured it, say how you measured it — ideally with a
command someone else can run.

## Things that will trip you up

- **`stack/docker-compose.yml` is generated.** Change
  `stack/generate-compose.py` and re-run it. Hand-editing the compose file
  fails the `generated files are in sync` CI job.
- **`stack/config.json` needs an engine restart.** `config-init` re-seeds the
  volume on every `up`, but the engine reads the config once at startup. An
  edit with no restart of `opencode-engine` is silently discarded, which is a
  genuinely confusing way to lose an afternoon.
- **A drain records the address into its marker, on purpose.** `ofu-dedupe`
  cannot measure a drained node — stopping its proxy is what draining means — so
  the marker is the only surviving record of what that node held, and therefore
  the only way the arbiter can notice the address freed up. This invariant
  exists because losing it made drains permanent.
- **WARP only offers a handful of addresses per region.** Six nodes in one colo
  yielded five usable ones. A node sitting drained at steady state is correct
  behaviour, not a bug; `smoke.sh` reports it as drained rather than broken for
  that reason.
- **The tests mock the network, never the clock or the filesystem.** If a test
  needs a running pool to be meaningful, the logic it is checking probably
  belongs in a function that does not.

## What a good PR looks like here

- A failing test that fails for the reason you claim, added before the fix.
  Saying "I checked and reverting the line makes it fail again" is worth more
  than a passing suite, because it proves the test is load-bearing.
- A measured number in the description, with the date.
- No dependency added to the runtime path. The stack must keep working on a
  machine with `python3` and `docker` and nothing else.

## Scope

In scope: the pool, the de-duplicator, the gate documentation, the verification
scripts, the stack configuration.

Out of scope, and a good use of an issue rather than a PR: improving the
upstream engine itself (pin the fork, send it upstream), and working around
WARP's address ceiling — that ceiling is structural. Past it you need your own
egress, which the README explains how to plug in.

## Code of conduct

Be plain, be specific, and argue with the measurement.