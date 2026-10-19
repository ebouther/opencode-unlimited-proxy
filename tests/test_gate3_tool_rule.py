#!/usr/bin/env python3
"""Tests pinning gate 3's tool-declaration rule.

An earlier revision of this repository asserted gate 3 required a four-tool
"quartet" and that 0-3 tools were refused. Both claims were false, and both
were formed from a single passing configuration rather than a one-variable-at-a-
time comparison. Measured 2026-10-16, the rule is: exactly one tool named
`read`, and nothing else about the declarations matters.

These tests exist so the claim cannot drift back. They pin the probe's own probe
table -- the expectations it asserts against upstream -- rather than re-testing
upstream, so they run offline and stay meaningful when upstream moves.

Run: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gate_probe", REPO / "scripts" / "gate-probe.py")
gp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gp)


class ToolRuleIsStatedCorrectly(unittest.TestCase):
    def test_read_is_the_load_bearing_name(self):
        self.assertIn("read", gp.CLIENT_TOOLS)

    def test_the_constant_is_not_named_as_the_rule(self):
        # A reader who greps for the rule should not find a name that reads
        # like a specification of it.
        self.assertFalse(hasattr(gp, "QUARTET"),
                         "QUARTET reads as the gate's rule; it is only the "
                         "client's toolset. Rename it if this fails.")

    def test_minimal_and_near_miss_are_declared(self):
        self.assertEqual(gp.READ_ONLY, ("read",))
        self.assertEqual(gp.NEAR_MISS, ("read_file",))


class ProbeTablePinsTheRule(unittest.TestCase):
    """The probe is the public artefact. Its expectations are the claim."""

    def setUp(self):
        class Args:
            ua = "opencode/test"
            model = "beam-free"
            proxy = None
            timeout = 10
        self.probes = {p.name: p for p in gp.build_probes(Args())}

    def test_read_alone_is_asserted_to_pass(self):
        # The positive half. Without it, count looks load-bearing when it is not,
        # and a future change could tighten the rule without anything noticing.
        probe = self.probes["read alone (minimal)"]
        self.assertEqual(probe.expect, "200")
        self.assertEqual(probe.tools, ("read",))

    def test_a_renamed_read_is_asserted_to_fail(self):
        # The near miss is the whole point: renaming is not a fix.
        probe = self.probes["near miss: read_file"]
        self.assertEqual(probe.expect, "403")
        self.assertEqual(probe.tools, ("read_file",))

    def test_real_names_without_read_are_asserted_to_fail(self):
        # This is the case that refutes "quartet": three genuine client tools
        # and no `read` must be refused.
        probe = self.probes["real names, no read"]
        self.assertEqual(probe.expect, "403")
        self.assertNotIn("read", probe.tools)

    def test_a_long_list_without_read_is_asserted_to_fail(self):
        # The count is not the carrier. Ten genuine names is the size the
        # folklore workaround actually ships, so if length were load-bearing
        # this would pass and people would keep pasting all of it.
        probe = self.probes["ten real names, no read"]
        self.assertEqual(probe.expect, "403")
        self.assertEqual(len(probe.tools), 10)
        self.assertNotIn("read", probe.tools)

    def test_zero_tools_is_still_refused(self):
        self.assertEqual(self.probes["zero tools"].expect, "403")

    def test_baseline_carries_read(self):
        self.assertIn("read", self.probes["baseline"].tools)

    def test_every_gate_is_covered(self):
        gates = {p.gate for p in self.probes.values()}
        self.assertEqual(gates, {"-", "gate 1", "gate 2", "gate 3", "gate 4"})

    def test_gate_three_is_covered_by_five_probes(self):
        # Not a limit for its own sake: the probes bracket the rule from every
        # side (passes with `read`, fails without, fails when renamed, fails
        # when long), and one assertion per side would not catch a rule that
        # drifted to "read or read_file" or to "lots of tools".
        covering = [n for n, p in self.probes.items() if p.gate == "gate 3"]
        self.assertEqual(len(covering), 5)

    def test_probe_count_is_never_hardcoded_in_the_output(self):
        # The summary line computes from build_probes, so adding a probe cannot
        # leave the printed count quietly wrong.
        import inspect
        source = inspect.getsource(gp.main)
        self.assertNotIn("(1 baseline + 5", source)


if __name__ == "__main__":
    unittest.main()