#!/usr/bin/env python3
"""Pin the idempotency of the WARP node entrypoint.

The regression this guards: `warp-cli registration new` fails with
"Old registration is still around" whenever the node already has a registration
in its writable layer (which survives a `docker compose up` that restarts
rather than recreates the container). The previous entrypoint treated every
failure as a transient Cloudflare 429, retried 12 times, then exited 1 without
ever reaching `exec python3 rotator.py`. Result: port 8001 never opened on any
node, the dedupe arbiter could only drain colliding nodes, and the pool
collapsed from six to two.

These are textual checks, like test_smoke_dedupe_threshold.py: the behaviour
needs a live six-node pool to mean anything, and CI has none of that.
"""

import pathlib
import re
import unittest

ENTRYPOINT = pathlib.Path(__file__).resolve().parent.parent / "stack" / "warp" / "entrypoint.sh"
ROTATOR = pathlib.Path(__file__).resolve().parent.parent / "stack" / "warp" / "rotator.py"
DEDUPE = pathlib.Path(__file__).resolve().parent.parent / "stack" / "dedupe" / "dedupe.py"


class WarpEntrypointRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entry = ENTRYPOINT.read_text(encoding="utf-8")
        cls.rotator = ROTATOR.read_text(encoding="utf-8")
        cls.dedupe = DEDUPE.read_text(encoding="utf-8")

    def test_registration_is_checked_before_calling_new(self):
        # A node that already has a registration must short-circuit, not be
        # forced through `registration new`.
        self.assertIn("registration show", self.entry)
        self.assertRegex(
            self.entry,
            r"if warp-cli --accept-tos registration show",
            "entrypoint must probe for an existing registration before calling new",
        )

    def test_old_registration_is_treated_as_success(self):
        # The deterministic failure mode must not be retried as a 429.
        self.assertIn("Old registration is still around", self.entry)
        self.assertIn("existing registration present", self.entry)

    def test_old_registration_breaks_out_instead_of_sleeping(self):
        # The code path (not the explanatory comment) must break out of the
        # retry loop instead of sleeping and retrying.
        marker = 'grep -qi "Old registration is still around"'
        self.assertIn(marker, self.entry)
        idx = self.entry.index(marker)
        tail = self.entry[idx:idx + 220]
        self.assertIn("break", tail)
        self.assertNotIn('sleep "$delay"', tail)

    def test_transient_failures_are_still_retried(self):
        # The 429 path must remain, so a real rate limit is still ridden out.
        self.assertIn("registration new", self.entry)
        self.assertIn("retrying in", self.entry)
        self.assertIn("WARP_REGISTRATION_ATTEMPTS", self.entry)

    def test_rotator_http_failure_is_not_swallowed(self):
        # A bare `except Exception` that only logs the message hides the cause.
        # The listener failure must be logged with its traceback.
        self.assertNotIn('log.error(f"Failed to start rotator HTTP listener: {e}")',
                         self.rotator)
        self.assertIn("log.exception", self.rotator)

    def test_dedupe_names_a_dead_control_plane(self):
        # Connection refused is not a generic rotation failure; it means the
        # node's rotator never started. It must be reported as such.
        self.assertIn("control plane", self.dedupe)
        self.assertIn("connection refused", self.dedupe.lower())


if __name__ == "__main__":
    unittest.main()