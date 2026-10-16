#!/usr/bin/env python3
"""Unit tests for stack/dedupe/dedupe.py.

This is the one piece of non-trivial logic in the repository that decides
whether a node keeps serving traffic: a node whose address cannot be measured is
treated as dead, and a node that cannot be given its own address is drained out
of the pool. Both decisions are destructive enough to deserve tests that do not
need a running pool.

Run: python3 -m unittest discover -s tests -v
"""

import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("dedupe", REPO / "stack" / "dedupe" / "dedupe.py")
dedupe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dedupe)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeOpener:
    """Returns a canned body per URL, or raises the configured error."""

    def __init__(self, answers):
        self.answers = answers
        self.seen = []

    def open(self, url, timeout=None):
        self.seen.append(url)
        answer = self.answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise urllib.error.URLError(f"no answer configured for {url}")
        return FakeResponse(answer.encode())


def node(name, marker_dir, ip=None, drained=False):
    n = dedupe.Node(name, marker_dir)
    n.ip = ip
    n.drained = drained
    return n


class Node(unittest.TestCase):
    def test_node_id_is_the_trailing_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            n = dedupe.Node("alztrk-warp-f", tmp)
            self.assertEqual(n.node_id, "f")
            self.assertEqual(n.marker, os.path.join(tmp, "f"))

    def test_marker_is_what_the_proxy_watches(self):
        # gate.sh reads /markers/<NODE_ID>; if these ever diverge the drain
        # silently stops working.
        with tempfile.TemporaryDirectory() as tmp:
            n = dedupe.Node("alztrk-warp-c", tmp)
            self.assertTrue(os.path.basename(n.marker) == "c")

    def test_drained_reflects_marker_existence(self):
        with tempfile.TemporaryDirectory() as tmp:
            n = dedupe.Node("alztrk-warp-a", tmp)
            self.assertFalse(n.drained)
            pathlib.Path(n.marker).write_text("because")
            self.assertTrue(dedupe.Node("alztrk-warp-a", tmp).drained)


class CollisionGroups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def groups(self, specs):
        return dedupe.collision_groups([node(n, self.dir, ip=i, drained=d)
                                        for n, i, d in specs])

    def test_solo_addresses_are_not_collisions(self):
        self.assertEqual(self.groups([("alztrk-warp-a", "1.1.1.1", False),
                                      ("alztrk-warp-b", "2.2.2.2", False)]), {})

    def test_shared_address_is_reported(self):
        found = self.groups([("alztrk-warp-a", "1.1.1.1", False),
                             ("alztrk-warp-b", "1.1.1.1", False)])
        self.assertEqual(list(found), ["1.1.1.1"])
        self.assertEqual([n.node_id for n in found["1.1.1.1"]], ["a", "b"])

    def test_three_way_collision_is_one_group(self):
        found = self.groups([(f"alztrk-warp-{c}", "1.1.1.1", False) for c in "abc"])
        self.assertEqual(len(found["1.1.1.1"]), 3)

    def test_unmeasured_nodes_are_ignored(self):
        # An unreachable node must not be filed as a collision against "".
        self.assertEqual(self.groups([("alztrk-warp-a", None, False),
                                      ("alztrk-warp-b", None, False)]), {})

    def test_drained_nodes_are_excluded(self):
        self.assertEqual(self.groups([("alztrk-warp-a", "1.1.1.1", False),
                                      ("alztrk-warp-b", "1.1.1.1", True)]), {})


class DrainMarker(unittest.TestCase):
    """The marker is the only record of a drained node's address, because a
    drained node cannot be measured any more."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_marker_records_the_address(self):
        n = node("alztrk-warp-a", self.dir, ip="104.28.194.129")
        dedupe.drain(n, "no distinct address available; shares 104.28.194.129")
        self.assertEqual(n.marker_ip(), "104.28.194.129")

    def test_marker_records_the_node_address_not_the_one_in_the_reason(self):
        # The reason text names an address too. Parsing "the first address in the
        # file" reads that one back instead of the node's own.
        n = node("alztrk-warp-a", self.dir, ip="104.28.194.129")
        dedupe.drain(n, "no distinct address available; shares 1.1.1.1")
        self.assertEqual(n.marker_ip(), "104.28.194.129")

    def test_reads_an_address_out_of_a_legacy_marker(self):
        # Markers written before the address line was added still have to work:
        # for those, "shares X" does name the address the node was holding.
        n = node("alztrk-warp-a", self.dir)
        pathlib.Path(n.marker).write_text(
            "no distinct address available; shares 104.28.194.129\n2026-10-16T02:10:38\n")
        self.assertEqual(n.marker_ip(), "104.28.194.129")

    def test_ignores_a_marker_with_no_address(self):
        n = node("alztrk-warp-a", self.dir)
        pathlib.Path(n.marker).write_text("something went wrong\n2026-10-16T02:10:38\n")
        self.assertIsNone(n.marker_ip())

    def test_known_ip_prefers_the_measurement(self):
        n = node("alztrk-warp-a", self.dir, ip="1.1.1.1", drained=True)
        pathlib.Path(n.marker).write_text("x\naddress=2.2.2.2\n")
        self.assertEqual(n.known_ip(), "1.1.1.1")

    def test_known_ip_falls_back_to_the_marker(self):
        n = node("alztrk-warp-a", self.dir, ip=None, drained=True)
        pathlib.Path(n.marker).write_text("x\naddress=9.9.9.9\n")
        self.assertEqual(n.known_ip(), "9.9.9.9")


class PollNodeKeepsTheLastKnownAddress(unittest.TestCase):
    """A failed measurement must not erase what the node is known to hold."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_keeps_the_previous_address_when_measurement_fails(self):
        n = node("alztrk-warp-a", self.dir, ip="104.28.194.129")
        with mock.patch.object(dedupe, "measure_ip", return_value=None), \
             mock.patch.object(dedupe.time, "sleep"):
            alive = dedupe.poll_node(n)
        self.assertFalse(alive)
        self.assertEqual(n.ip, "104.28.194.129")

    def test_overwrites_the_previous_address_on_success(self):
        n = node("alztrk-warp-a", self.dir, ip="104.28.194.129")
        with mock.patch.object(dedupe, "measure_ip", return_value="185.137.228.16"), \
             mock.patch.object(dedupe.time, "sleep"):
            dedupe.poll_node(n)
        self.assertEqual(n.ip, "185.137.228.16")
        self.assertIn("185.137.228.16", n.distinct_ever)


class ReviveIfFree(unittest.TestCase):
    """A drained node comes back only when its address is genuinely unclaimed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def drained(self, name, ip):
        n = node(name, self.dir, ip=None, drained=True)
        pathlib.Path(n.marker).write_text(
            f"no distinct address available; shares {ip}\n2026-10-16T02:10:38\naddress={ip}\n")
        return n

    def test_revives_when_the_address_is_free(self):
        live = node("alztrk-warp-a", self.dir, ip="1.1.1.1")
        dead = self.drained("alztrk-warp-b", "2.2.2.2")
        dedupe.revive_if_free([live, dead])
        self.assertFalse(dead.drained)
        self.assertFalse(pathlib.Path(dead.marker).exists())

    def test_does_not_revive_a_still_shared_address(self):
        live = node("alztrk-warp-a", self.dir, ip="1.1.1.1")
        dead = self.drained("alztrk-warp-b", "1.1.1.1")
        dedupe.revive_if_free([live, dead])
        self.assertTrue(dead.drained)
        self.assertTrue(pathlib.Path(dead.marker).exists())

    def test_revives_an_unmeasurable_node_once_its_address_frees_up(self):
        # The regression this fixes: a drained node's proxy is stopped, so it
        # can never be measured, so `node.ip` was always None and the revival
        # check could never fire. The drain became permanent.
        live = node("alztrk-warp-a", self.dir, ip="1.1.1.1")
        dead = self.drained("alztrk-warp-b", "1.1.1.1")
        dedupe.revive_if_free([live, dead])
        self.assertTrue(dead.drained)  # still colliding

        live.ip = "2.2.2.2"  # the holder moves on
        dedupe.revive_if_free([live, dead])
        self.assertFalse(dead.drained)

    def test_never_revives_the_whole_pool(self):
        # Reviving everyone at once just re-creates the collision that caused
        # the drain, one pass later.
        dead = [self.drained(f"alztrk-warp-{c}", f"1.1.1.{i}")
                for i, c in enumerate("abc")]
        dedupe.revive_if_free(dead)
        self.assertTrue(all(n.drained for n in dead))
        self.assertTrue(all(pathlib.Path(n.marker).exists() for n in dead))

    def test_leaves_a_drained_node_with_no_known_address_alone(self):
        # Nothing recorded, nothing to compare: reviving would be a guess.
        live = node("alztrk-warp-a", self.dir, ip="1.1.1.1")
        mystery = node("alztrk-warp-b", self.dir, ip=None, drained=True)
        pathlib.Path(mystery.marker).write_text("unknown cause\n")
        dedupe.revive_if_free([live, mystery])
        self.assertTrue(mystery.drained)


class MeasureIp(unittest.TestCase):
    """Whatever is measured here decides the pool's arithmetic, so parsing is
    tested hard: a bad parse silently shrinks the pool."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.n = node("alztrk-warp-a", self.dir)

    def measure_with(self, answers):
        opener = FakeOpener(answers)
        with mock.patch.object(dedupe.urllib.request, "build_opener", return_value=opener):
            return dedupe.measure_ip(self.n)

    def test_parses_the_json_form(self):
        # IP_URLS[0] is api.ipify.org?format=json
        ip = self.measure_with({dedupe.IP_URLS[0]: json.dumps({"ip": "1.2.3.4"})})
        self.assertEqual(ip, "1.2.3.4")

    def test_parses_the_plain_text_form(self):
        ip = self.measure_with({dedupe.IP_URLS[1]: "5.6.7.8\n"})
        self.assertEqual(ip, "5.6.7.8")

    def test_takes_only_the_first_field_of_a_noisy_body(self):
        ip = self.measure_with({dedupe.IP_URLS[1]: "5.6.7.8  hostname.example"})
        self.assertEqual(ip, "5.6.7.8")

    def test_falls_through_to_the_second_service(self):
        ip = self.measure_with({dedupe.IP_URLS[1]: "5.6.7.8"})
        self.assertEqual(ip, "5.6.7.8")

    def test_ignores_malformed_json(self):
        self.assertIsNone(self.measure_with({dedupe.IP_URLS[0]: "{not json"}))

    def test_ignores_an_empty_body(self):
        self.assertIsNone(self.measure_with({dedupe.IP_URLS[0]: "", dedupe.IP_URLS[1]: ""}))

    def test_ignores_an_html_error_page(self):
        self.assertIsNone(self.measure_with({dedupe.IP_URLS[0]: "<html>503</html>",
                                             dedupe.IP_URLS[1]: "<html>503</html>"}))

    def test_returns_none_when_both_services_fail(self):
        self.assertIsNone(self.measure_with({}))

    def test_accepts_an_ipv6_egress(self):
        # Regression: the parser used to require dotted-quad segments, so an
        # IPv6-only node measured as dead and got drained while smoke.sh -- which
        # accepts colons -- still counted it as alive.
        addr = "2a09:bac1:27e0:2018::219:4"
        self.assertEqual(self.measure_with({dedupe.IP_URLS[0]: json.dumps({"ip": addr})}), addr)

    def test_rejects_something_that_is_not_an_address(self):
        for junk in ("unknown", "localhost", "1.2.3", "999.1.1.1", "1.1.1.1.1"):
            with self.subTest(junk=junk):
                self.assertIsNone(self.measure_with({dedupe.IP_URLS[0]: junk,
                                                     dedupe.IP_URLS[1]: junk}))

    def test_an_unreachable_node_measures_as_none(self):
        # The signal that gets a node drained; it must not be produced by a
        # parsing accident.
        self.assertIsNone(self.measure_with({}))


class LogCapture:
    """dedupe logs through the logging module, not stderr, so capture it there."""

    def __enter__(self):
        import logging
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setLevel(logging.DEBUG)
        self.logger = logging.getLogger("dedupe")
        self._prev_handlers, self._prev_level = self.logger.handlers, self.logger.level
        self.logger.handlers = [self.handler]
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        return self.stream

    def __exit__(self, *exc):
        self.logger.handlers = self._prev_handlers
        self.logger.setLevel(self._prev_level)
        self.logger.propagate = True
        return False


class ResolveCollisionsDryRun(unittest.TestCase):
    """--dry-run must predict the real pass without touching anything."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_reports_nothing_when_the_pool_is_clean(self):
        nodes = [node(f"alztrk-warp-{c}", self.dir, ip=f"1.1.1.{i}") for i, c in enumerate("abc")]
        with LogCapture() as out:
            rolls, drained = dedupe.resolve_collisions(nodes, 4, 3, 2, 0, True)
        self.assertEqual((rolls, drained), (0, 0))
        self.assertNotIn("would rotate", out.getvalue())

    def test_spends_its_rotation_budget_then_drains_the_surplus(self):
        # Two nodes on one address: the dry run cannot actually move either, so
        # the pass ends with one node rotated twice and one drained. That is the
        # shape of a pool with no room left, and the dry run has to predict it.
        nodes = [node("alztrk-warp-a", self.dir, ip="1.1.1.1"),
                 node("alztrk-warp-b", self.dir, ip="1.1.1.1")]
        with LogCapture() as out:
            rolls, drained = dedupe.resolve_collisions(nodes, 4, 3, 2, 0, True)
        self.assertEqual(rolls, 2)
        self.assertEqual(drained, 1)
        self.assertIn("would rotate", out.getvalue())
        self.assertIn("would be drained", out.getvalue())

    def test_respects_the_per_pass_rotation_cap(self):
        nodes = [node(f"alztrk-warp-{c}", self.dir, ip="1.1.1.1") for c in "abcd"]
        with LogCapture():
            rolls, _ = dedupe.resolve_collisions(nodes, 4, 3, 2, 0, True)
        self.assertEqual(rolls, 2)

    def test_predicts_draining_the_surplus(self):
        nodes = [node(f"alztrk-warp-{c}", self.dir, ip="1.1.1.1") for c in "abc"]
        with LogCapture() as out:
            _, drained = dedupe.resolve_collisions(nodes, 0, 0, 0, 0, True)
        # Three nodes on one address: one is kept, the other two are surplus.
        self.assertEqual(drained, 2)
        self.assertIn("would be drained", out.getvalue())


class WaitForGap(unittest.TestCase):
    """Rotations are globally metered: Cloudflare answers 429 above a rate."""

    def test_first_rotation_is_immediate(self):
        dedupe._last_rotation_at = 0.0
        dedupe._min_rotate_gap = 90.0
        with mock.patch.object(dedupe.time, "sleep") as slept:
            dedupe.wait_for_gap()
        slept.assert_not_called()

    def test_waits_out_the_remainder(self):
        dedupe._min_rotate_gap = 90.0
        dedupe._last_rotation_at = dedupe.time.monotonic() - 80.0
        with mock.patch.object(dedupe.time, "sleep") as slept:
            dedupe.wait_for_gap()
        slept.assert_called_once()
        self.assertAlmostEqual(slept.call_args[0][0], 10.0, delta=1.0)

    def tearDown(self):
        dedupe._last_rotation_at = 0.0
        dedupe._min_rotate_gap = dedupe.DEFAULT_MIN_ROTATE_GAP


class MainArgumentHandling(unittest.TestCase):
    def test_refuses_to_start_without_a_pool(self):
        with mock.patch.object(dedupe.argparse, "ArgumentParser") as parser_cls:
            argv = mock.Mock(nodes="", dry_run=False, once=True, interval=1, markers="/tmp",
                             max_rolls=1, max_retries=1, max_rotations_per_pass=1,
                             min_rotate_gap=0, settle=0)
            parser_cls.return_value.parse_args.return_value = argv
            with redirect_stdout(io.StringIO()):
                self.assertEqual(dedupe.main(), 2)


class OnePassRevivalReachesDrainedNodes(unittest.TestCase):
    """The regression that made drains permanent.

    Revival used to be handed only the nodes that had answered the measurement.
    A drained node never answers -- stopping its proxy is what draining means --
    so it was never in that list, and the revival check could not run for it
    even once its address became free. The pool only ever shrank.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def build_pool(self):
        live = node("alztrk-warp-b", self.dir, ip="104.28.226.130")
        drained = node("alztrk-warp-c", self.dir, ip=None, drained=True)
        pathlib.Path(drained.marker).write_text(
            "no distinct address available; shares 104.28.194.129\n"
            "2026-10-16T02:10:38\naddress=104.28.194.129\n"
        )
        return live, drained

    def test_revives_a_drained_node_the_measurement_never_returns(self):
        live, drained = self.build_pool()
        nodes = [live, drained]
        with mock.patch.object(dedupe, "poll_all", return_value=[live]) as polled, \
             mock.patch.object(dedupe, "resolve_collisions", return_value=(0, 0)), \
             LogCapture():
            dedupe.one_pass(nodes, 4, 3, 2, 0, True)

        # Sanity: the drained node really is invisible to the measurement.
        self.assertNotIn(drained, polled.return_value)
        # ...and it still came back.
        self.assertFalse(drained.drained, "drained node was not revived once its address freed up")
        self.assertFalse(pathlib.Path(drained.marker).exists())

    def test_returns_none_when_nothing_answered(self):
        live, drained = self.build_pool()
        with mock.patch.object(dedupe, "poll_all", return_value=[]), LogCapture():
            self.assertIsNone(dedupe.one_pass([live, drained], 4, 3, 2, 0, True))
        # No measurement, no action: a pool-wide blip must not drain the pool.
        self.assertTrue(drained.drained)


if __name__ == "__main__":
    unittest.main()
