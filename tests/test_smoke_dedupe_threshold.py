#!/usr/bin/env python3
"""Pin the de-duplication threshold in scripts/smoke.sh.

This is a textual test, not a behavioural one, and deliberately so: the check it
guards needs a live six-node pool to mean anything, and CI has none of that.
What it does guard is the specific regression that was actually live: the
threshold compared the distinct-address count against the *configured* pool size
while a drained node holds no address by design. On a correctly working pool of
six with four distinct addresses, `distinct >= total - 1` is unsatisfiable, so
the smoke test went red on a healthy install.

Textual assertions are used elsewhere in this suite for the same reason, for
example test_probe_count_is_never_hardcoded_in_the_output.
"""

import pathlib
import re
import unittest

SMOKE = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "smoke.sh"


class SmokeDedupeThreshold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = SMOKE.read_text(encoding="utf-8")

    def test_threshold_is_not_derived_from_the_configured_pool_size(self):
        # The bug, verbatim. If this string comes back, the check is grading the
        # pool against a count that drained nodes can never contribute to.
        self.assertNotIn(
            "$((total - 1))",
            self.src,
            "smoke.sh compares distinctness against the configured pool size again",
        )

    def test_threshold_is_compared_against_the_nodes_that_answered(self):
        # `ok` is the count of nodes that returned an address. Distinctness only
        # means anything relative to those.
        self.assertRegex(
            self.src,
            r'\[ "\$distinct" -ge "\$ok" \]',
            "smoke.sh must compare the distinct count against the answering nodes",
        )

    def test_an_empty_pool_does_not_pass_vacuously(self):
        # distinct == ok == 0 would otherwise satisfy the comparison above.
        self.assertRegex(
            self.src,
            r'\[ "\$ok" -gt 0 \]',
            "smoke.sh must not pass the de-duplication check when nothing answered",
        )

    def test_the_check_names_the_serving_count_in_its_output(self):
        # So a red line tells you whether the collision is real or the pool is
        # short, instead of leaving you to compare two numbers in different
        # denominators.
        self.assertIn("$distinct/$ok serving distinct", self.src)
        self.assertNotIn("$distinct/$total distinct", self.src)

    def test_the_reason_is_written_down_next_to_the_threshold(self):
        # The whole point of the change is that a future reader understands why
        # `total` is not used here. A bare threshold invites someone to "fix" it
        # back.
        block = self.src.split("distinct egress addresses across")[1][:900]
        self.assertIn("drained node holds no address by design", block)
        self.assertIn("ofu-dedupe", block)


if __name__ == "__main__":
    unittest.main()