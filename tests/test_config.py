#!/usr/bin/env python3
"""Tests for the shipped stack/config.json.

This file is the stack's security posture as much as its configuration: it is
what decides who can reach the gateway and what the gateway is willing to spend.
The assertions below encode the defaults a newcomer should get without having to
read the JSON, so a later edit that quietly reopens one of them fails CI instead
of failing on someone else's host.

Run: python3 -m unittest discover -s tests -v
"""

import json
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO / "stack" / "config.json").read_text())

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def host_of(listen: str) -> str:
    return listen.rsplit(":", 1)[0].strip("[]")


class ListenersAreNotExposed(unittest.TestCase):
    """The shipped default must not be reachable from another machine.

    `anonymous: true` means the API has no credential of its own: whoever can
    reach :8080 can spend the pool's quota. Binding to a wildcard address is
    therefore not a convenience, it is the only thing standing between the
    internet and someone's quota. Every documented use of this stack is
    localhost, so loopback is also the only binding that fits.
    """

    def assert_not_exposed(self, listen, label):
        self.assertIn(
            listen.split(":")[0], LOOPBACK_HOSTS,
            f"{label} binds {listen!r}, which listens on every interface",
        )

    def test_api_listens_on_loopback(self):
        self.assert_not_exposed(CONFIG["listen"], "the API")

    def test_dashboard_listens_on_loopback(self):
        self.assert_not_exposed(CONFIG["webui"]["listen"], "the dashboard")

    def test_a_wildcard_bind_is_only_allowed_alongside_a_real_key(self):
        # Documenting the coupling, not forbidding it: someone who sets a real
        # key may legitimately widen the bind. They should not be able to widen
        # it by accident on the anonymous default, though.
        if CONFIG.get("anonymous"):
            for label, listen in (("API", CONFIG["listen"]),
                                  ("dashboard", CONFIG["webui"]["listen"])):
                self.assertIn(
                    host_of(listen), LOOPBACK_HOSTS,
                    f"{label} is exposed while the gateway is anonymous",
                )


class NothingIsSpentByAccident(unittest.TestCase):
    """The zero-cost property is the reason the pool is worth having."""

    def test_no_personal_keys_are_shipped(self):
        self.assertEqual(CONFIG.get("zen_keys"), [])
        self.assertEqual(CONFIG.get("go_keys"), [])

    def test_only_free_models_are_selectable(self):
        self.assertTrue(CONFIG["models"]["free_only"])

    def test_the_free_tier_is_reachable_without_a_key(self):
        # This is the feature, not an oversight: a local single-user gateway
        # should not need a credential to talk to its own tools.
        self.assertTrue(CONFIG.get("anonymous"))

    def test_the_shipped_key_is_a_placeholder_not_a_secret(self):
        # A real key committed to a public repo would be a leak. This asserts
        # the placeholder is still obviously a placeholder.
        for key in CONFIG.get("server_keys", []):
            self.assertIn("local-test-key", key, f"a non-placeholder key is committed: {key}")


class PoolMatchesTheEngine(unittest.TestCase):
    def test_proxies_are_loopback(self):
        # The engine shares the host network namespace, so a proxy on a
        # non-loopback address would be both unreachable in practice and
        # needlessly visible on the network.
        for proxy in CONFIG["proxies"]:
            self.assertTrue(proxy.startswith("http://127.0.0.1:"), proxy)

    def test_ports_are_contiguous(self):
        ports = [int(p.rsplit(":", 1)[1]) for p in CONFIG["proxies"]]
        self.assertEqual(ports, list(range(ports[0], ports[0] + len(ports))))

    def test_no_duplicate_ports(self):
        ports = [p.rsplit(":", 1)[1] for p in CONFIG["proxies"]]
        self.assertEqual(len(ports), len(set(ports)))


if __name__ == "__main__":
    unittest.main()