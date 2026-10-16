#!/usr/bin/env python3
"""Unit tests for the classification in scripts/gate-probe.py.

The probe's whole value is telling someone *why* they were refused. A `403` on
its own is ambiguous across at least three unrelated causes, and getting the
answer wrong sends a reader to debug request headers when the request can never
succeed -- or, worse, tells someone whose headers are fine that upstream moved.

Run: python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent

# The module is `gate-probe.py`: a hyphen rules out a plain import.
_spec = importlib.util.spec_from_file_location("gate_probe", REPO / "scripts" / "gate-probe.py")
gp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gp)


def body(kind: str, message: str) -> str:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}})


SHAPE = body("FreeTierError",
             "OpenCode's free tier can only be used from within OpenCode. "
             "Download it at https://opencode.ai and keep building.")
GEO = body("FreeTierError", "not available in your country")
QUOTA = body("FreeUsageLimitError", "free usage limit reached for this address")


class ClassifyShapeRefusals(unittest.TestCase):
    def test_the_documented_gate(self):
        kind, message, advice = gp.classify(403, SHAPE)
        self.assertEqual(kind, "shape")
        self.assertIn("within OpenCode", message)
        self.assertIn("free-tier-gates.md", advice)

    def test_geo_is_not_the_gate(self):
        # The whole reason this function exists: same status, same error type,
        # completely different remedy.
        kind, message, advice = gp.classify(403, GEO)
        self.assertEqual(kind, "geo")
        self.assertIn("region block", advice)

    def test_quota_is_not_the_gate(self):
        kind, _, advice = gp.classify(429, QUOTA)
        self.assertEqual(kind, "quota")
        self.assertIn("not a gate", advice)

    def test_quota_dressed_as_free_tier_error_is_still_quota(self):
        # The status code is the same 403. Only the message separates them.
        mislabelled = body("FreeTierError", "free usage limit reached for this address")
        self.assertEqual(gp.classify(403, mislabelled)[0], "quota")

    def test_auth_is_not_the_gate(self):
        kind, _, _ = gp.classify(401, body("Unauthorized", "authentication required"))
        self.assertEqual(kind, "auth")

    def test_stale_client_is_not_the_gate(self):
        kind, _, _ = gp.classify(426, body("Error", "Upgrade Required"))
        self.assertEqual(kind, "version")


class ClassifyRobustness(unittest.TestCase):
    def test_a_success_is_not_classified_as_a_refusal(self):
        self.assertEqual(gp.classify(200, "data: {\"choices\":[] }")[0], "ok")

    def test_an_unrecognised_refusal_says_so(self):
        # Better to admit we do not know than to guess 'shape' and send someone
        # off to rewrite their headers.
        kind, _, advice = gp.classify(403, body("FreeTierError", "something new entirely"))
        self.assertEqual(kind, "unknown")
        self.assertIn("out of date", advice)

    def test_reads_an_sse_framed_error(self):
        # A streaming endpoint can deliver the error inside the event stream
        # rather than as the HTTP body.
        framed = 'data: ' + GEO
        self.assertEqual(gp.classify(403, framed)[0], "geo")

    def test_reads_a_bare_message_field(self):
        self.assertEqual(
            gp.classify(403, json.dumps({"message": "not available in your country"}))[0],
            "geo")

    def test_survives_an_empty_body(self):
        kind, _, advice = gp.classify(403, "")
        self.assertEqual(kind, "unknown")
        self.assertTrue(advice)

    def test_survives_a_non_json_error_page(self):
        kind, _, _ = gp.classify(502, "<html>502 Bad Gateway</html>")
        self.assertEqual(kind, "unknown")

    def test_survives_a_truncated_body(self):
        # The probe reads only the first 220 bytes, so a long message arrives
        # cut off. Classification must depend on the part that survives, which
        # is exactly what a real response looks like: the verdict first, the
        # marketing boilerplate after it.
        long_message = ("OpenCode's free tier can only be used from within OpenCode. "
                        + "Download it at https://opencode.ai and keep building. " * 20)
        full = body("FreeTierError", long_message)
        self.assertGreater(len(full), 220, "the fixture must actually be truncated")
        self.assertEqual(gp.classify(403, full[:220])[0], "shape")


class RefusalKindsAreExhaustive(unittest.TestCase):
    def test_no_duplicate_kind(self):
        kinds = [kind for kind, _, _ in gp.REFUSALS]
        self.assertEqual(len(kinds), len(set(kinds)))

    def test_every_refusal_carries_advice(self):
        # The advice line is the actionable part; a bare label is no better than
        # printing the raw error.
        for kind, needles, advice in gp.REFUSALS:
            with self.subTest(kind=kind):
                self.assertTrue(needles)
                self.assertGreater(len(advice), 20)


if __name__ == "__main__":
    unittest.main()