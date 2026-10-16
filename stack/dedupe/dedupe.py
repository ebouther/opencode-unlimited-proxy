#!/usr/bin/env python3
"""Cluster-aware address de-duplicator for the WARP egress pool.

Why this exists
---------------
Cloudflare WARP draws egress addresses from a small per-region pool. Six
containers rotating independently land on the same address most of the time,
and the loss is not cosmetic: free-tier quota is bucketed per source address,
so five nodes sharing one address are five nodes sharing one bucket. The
nominal pool size overstates the real one.

Each pass this service:

  1. measures the *real* egress address of every node by making a request
     through that node's own CONNECT proxy,
  2. asks one colliding node to re-register until the addresses are distinct,
  3. marks as *drained* any node that still cannot be given its own address.

Two things this deliberately does not do
-----------------------------------------
*Trust the rotator's own report.* rotator.py caches the address it saw at
startup and only refreshes it when its own rotation succeeds. If a rotation
half-succeeds -- WARP hands out a new registration but the rotator decides it
failed -- the cache goes stale and the cluster believes two nodes are
distinct when they are not. So the address here is always measured, never
assumed.

*Rotate aggressively.* Changing address requires a fresh WARP registration
(plain disconnect/connect does not move the node), and Cloudflare rate-limits
the registration API with HTTP 429. Every rotation is metered globally and the
default spacing is deliberately slow. Rushing the API turns the whole pool into
a crash loop, which is worse than a few nodes sharing an address.

This is a best-effort optimiser, never a hard requirement: if it dies, the pool
keeps working with whatever addresses it has.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("dedupe")

ROTATOR_PORT = 8001
PROXY_PORT = 8888
IP_URLS = (
    "https://api.ipify.org?format=json",
    "https://ifconfig.me/ip",
)
DEFAULT_MARKER_DIR = "/markers"
DEFAULT_MIN_ROTATE_GAP = 90.0
DEFAULT_MAX_ROTATIONS_PER_PASS = 2
# A node that keeps landing on an occupied address this many times has probably
# been offered the whole address space.
DEFAULT_MAX_ROLLS = 4
# Rotations refused or unreachable are transient (an active stream, or the API
# being busy). They get their own, larger budget before counting as a failure.
DEFAULT_MAX_RETRIES = 3
# Seconds to wait after asking a node to rotate, before measuring again.
DEFAULT_SETTLE = 12.0
DEFAULT_HTTP_TIMEOUT = 20.0

# Timestamp of the last rotation issued, so the pool as a whole stays under the
# registration rate limit instead of each node racing on its own clock.
_last_rotation_at = 0.0
_min_rotate_gap = DEFAULT_MIN_ROTATE_GAP


def wait_for_gap() -> None:
    global _last_rotation_at
    if not _last_rotation_at:
        return
    elapsed = time.monotonic() - _last_rotation_at
    if elapsed < _min_rotate_gap:
        time.sleep(_min_rotate_gap - elapsed)


class Node:
    """One WARP node: its rotator API, its CONNECT proxy, its drain marker."""

    def __init__(self, name: str, marker_dir: str) -> None:
        self.name = name
        self.node_id = name.rsplit("-", 1)[-1]
        self.marker = os.path.join(marker_dir, self.node_id)
        self.api = f"http://{name}:{ROTATOR_PORT}"
        self.proxy = f"http://{name}:{PROXY_PORT}"
        self.ip: str | None = None
        self.drained = os.path.exists(self.marker)
        self.distinct_ever: set[str] = set()

    def __str__(self) -> str:  # pragma: no cover - logging helper
        return f"{self.name}={self.ip or '?'}{' (drained)' if self.drained else ''}"

    def marker_ip(self) -> str | None:
        """The address recorded in this node's drain marker, if any.

        A drained node cannot be measured -- that is what draining means, the
        proxy has stopped listening -- so its address has to come from
        somewhere else or the node can never be revived. The marker is written
        with the address on every drain, which is what makes the way back out
        possible without a human deleting the file.

        The `address=` line is authoritative. Scanning the file for any
        address-looking token is not: the reason text also names one ("shares
        <ip>"), and taking the first match reads back the wrong address.
        """
        try:
            with open(self.marker, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            return None
        match = re.search(r"^address=(\S+)$", text, re.MULTILINE)
        if match:
            try:
                return str(ipaddress.ip_address(match.group(1)))
            except ValueError:
                return None
        # Marker written before the address was recorded explicitly: the reason
        # names the address the node was sharing, which is the same thing.
        match = re.search(r"shares\s+(\S+)", text)
        if match:
            try:
                return str(ipaddress.ip_address(match.group(1)))
            except ValueError:
                return None
        return None

    def known_ip(self) -> str | None:
        """Best available address: measured if possible, else last seen, else
        whatever the drain marker recorded."""
        return self.ip or self.marker_ip()


def measure_ip(node: Node) -> str | None:
    """Ask an IP echo service through this node's CONNECT proxy.

    Returns None when no service gives back something that parses as an IP
    address. That is a destructive signal -- an unmeasurable node looks dead and
    gets drained -- so the bar for "this is an address" is the stdlib parser,
    not a digit check. An earlier version required dotted-quad segments, which
    rejected IPv6 outright: an IPv6-only node measured as dead and was drained
    while smoke.sh, which accepts colons, still counted it as alive.
    """
    for url in IP_URLS:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": node.proxy, "https": node.proxy})
        )
        try:
            with opener.open(url, timeout=DEFAULT_HTTP_TIMEOUT) as response:
                body = response.read(256).decode(errors="replace").strip()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.debug("%s: %s failed (%s)", node.name, url, exc)
            continue
        if not body:
            continue
        if body.startswith("{"):
            try:
                body = json.loads(body).get("ip", "")
            except ValueError:
                continue
        candidate = body.split()[0] if body.split() else ""
        if not candidate:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            log.debug("%s: %s returned %r, which is not an address", node.name, url, candidate)
    return None


def poll_node(node: Node) -> bool:
    """Refresh one node's measured address and drained flag.

    Two readings a few seconds apart: a node whose address is mid-rotation
    should not be treated as settled just because it answered once.

    A failed measurement must not erase the last known address. The node still
    has whatever address it was last seen on, and forgetting it is what turns a
    temporary blip into a permanent drain: with no address to compare, the
    revival check can never see the node's address become free again.

    Returns whether the node answered *now*. That is deliberately not the same
    question as "do we know an address for it": an unmeasurable node is skipped
    by the collision pass so its stale address is never mistaken for a current
    one, but its address is kept so it can be revived later.
    """
    first = measure_ip(node)
    time.sleep(4)
    measured = measure_ip(node) or first
    if measured:
        node.ip = measured
        node.distinct_ever.add(measured)
    node.drained = os.path.exists(node.marker)
    return measured is not None


def poll_all(nodes: list[Node]) -> list[Node]:
    """Measure every node concurrently; returns the ones that answered."""
    reachable: list[Node] = []
    threads = [
        threading.Thread(
            target=lambda n=n: reachable.append(n) if poll_node(n) else None
        )
        for n in nodes
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=DEFAULT_HTTP_TIMEOUT + 5)
    return reachable


def collision_groups(nodes: list[Node]) -> dict[str, list[Node]]:
    """Group live nodes by address; only addresses held by 2+ nodes collide."""
    by_ip: dict[str, list[Node]] = {}
    for node in nodes:
        if node.ip and not node.drained:
            by_ip.setdefault(node.ip, []).append(node)
    return {ip: group for ip, group in by_ip.items() if len(group) > 1}


def wait_stable_ip(node: Node, max_wait: float = 60.0, spacing: float = 10.0) -> str | None:
    """Measure the node's address until two readings agree, or time runs out.

    rotator.py keeps re-registering for a while after answering the rotation
    request, so a single reading taken straight afterwards can catch the node
    mid-rotation. Acting on that would mean chasing an address that is already
    gone, and spending a registration on it.
    """
    deadline = time.monotonic() + max_wait
    previous = measure_ip(node)
    while time.monotonic() < deadline:
        time.sleep(spacing)
        current = measure_ip(node)
        if current and current == previous:
            return current
        if current:
            previous = current
    return previous


def roll(node: Node, previous_ip: str | None) -> str:
    """Ask a node to re-register, then measure what it actually got.

    Returns "changed", "same", "busy" or "unreachable".
    """
    global _last_rotation_at
    wait_for_gap()
    log.info("%s: requesting rotation (was %s)", node.name, node.ip)
    outcome = "unreachable"
    try:
        request = urllib.request.Request(f"{node.api}/rotate", method="POST")
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode())
        outcome = "ok" if body.get("status") == "success" else "busy"
        if outcome == "busy":
            log.info("%s: rotator reported no change", node.name)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("%s: rotation request failed (%s)", node.name, exc)
    finally:
        _last_rotation_at = time.monotonic()

    # The rotator's answer is a hint, not a fact: re-registering may have moved
    # the node even when it reported failure, and it keeps going afterwards.
    new_ip = wait_stable_ip(node)
    if not new_ip:
        log.warning("%s: could not measure the address after rotating", node.name)
        return outcome if outcome != "ok" else "unreachable"
    node.ip = new_ip
    node.distinct_ever.add(new_ip)
    if previous_ip and new_ip == previous_ip:
        log.warning("%s: still %s after re-registration", node.name, new_ip)
        return "same"
    log.info("%s: now %s", node.name, new_ip)
    return "changed"


def drain(node: Node, reason: str) -> None:
    """Write the marker the node's CONNECT proxy watches.

    The address goes into the marker deliberately. Once the marker exists the
    node cannot be measured, so this is the only surviving record of what it was
    holding -- and therefore the only way to notice later that the address has
    become free and the node should come back.
    """
    try:
        with open(node.marker, "w", encoding="utf-8") as handle:
            handle.write(f"{reason}\n{time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
            if node.ip:
                handle.write(f"address={node.ip}\n")
    except OSError as exc:
        log.error("%s: could not write marker %s (%s)", node.name, node.marker, exc)
        return
    node.drained = True
    log.error("%s: DRAINED -- %s", node.name, reason)


def revive(node: Node) -> None:
    """Clear the drain marker: its address is free again."""
    held = node.known_ip()
    try:
        os.remove(node.marker)
    except OSError as exc:
        log.error("%s: could not clear marker %s (%s)", node.name, node.marker, exc)
        return
    node.drained = False
    # Read the address before the marker goes away; afterwards the only record
    # of what this node was holding has just been deleted.
    log.info("%s: revived, back in the pool holding %s", node.name, held or "an unknown address")


def revive_if_free(nodes: list[Node]) -> None:
    """Bring drained nodes back when nobody holds their address any more.

    A drained node is not measured -- its proxy has stopped listening -- so the
    address to check comes from the drain marker rather than a live reading.
    Comparing against a live measurement here is the trap: it is always None,
    the node never looks free, and the drain is permanent until a human deletes
    the marker by hand.
    """
    live = [n for n in nodes if not n.drained]
    if not live:
        return  # never revive the whole pool at once
    used = {n.ip for n in live if n.ip}
    for node in nodes:
        if not node.drained:
            continue
        held = node.known_ip()
        if held and held not in used:
            log.info("%s: address %s is free again, reviving", node.name, held)
            revive(node)


def resolve_collisions(
    nodes: list[Node],
    max_rolls: int,
    max_retries: int,
    max_rotations: int,
    settle: float,
    dry_run: bool,
) -> tuple[int, int]:
    """One convergence pass. Returns (rotations_issued, nodes_drained)."""
    for ip, group in collision_groups(nodes).items():
        log.warning(
            "%d nodes share %s: %s", len(group), ip, ", ".join(n.name for n in group)
        )

    # hard: rotations that produced an address we already had, i.e. real
    #       pressure on the address space.
    # soft: rotations refused or unreachable, i.e. transient.
    hard = {node.name: 0 for node in nodes}
    soft = {node.name: 0 for node in nodes}
    rolls = 0

    while rolls < max_rotations:
        groups = collision_groups(nodes)
        if not groups:
            break

        # Work the worst group first, and only one of its members at a time:
        # rotating every member just makes them trade addresses.
        target = None
        for _ip, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            candidates = [
                n for n in group if hard[n.name] < max_rolls or soft[n.name] < max_retries
            ]
            if candidates:
                candidates.sort(key=lambda n: (hard[n.name], soft[n.name]))
                target = candidates[0]
                break
        if target is None:
            break

        if dry_run:
            log.warning(
                "[dry-run] %s would rotate (shared %s, %d/%d)",
                target.name,
                target.ip,
                hard[target.name],
                max_rolls,
            )
            hard[target.name] += 1
            rolls += 1
            continue

        outcome = roll(target, target.ip)
        if outcome == "changed":
            hard[target.name] = 0
            soft[target.name] = 0
            rolls += 1
        elif outcome == "same":
            hard[target.name] += 1
            rolls += 1
        else:
            soft[target.name] += 1
            if soft[target.name] > max_retries:
                # A persistent refusal is not evidence about the address space,
                # but it is still no progress, so it eventually counts as one.
                hard[target.name] += 1
                soft[target.name] = 0
                log.warning("%s: %s %d times in a row", target.name, outcome, max_retries + 1)
        if settle:
            time.sleep(settle)

    drained = 0
    for ip, group in collision_groups(nodes).items():
        log.error(
            "still sharing %s after %d rotations: %s",
            ip,
            max_rolls,
            ", ".join(n.name for n in group),
        )
        # Keep the first node of each colliding group; the others have nothing
        # to add and would only spend the same bucket twice.
        for node in group[1:]:
            if dry_run:
                log.warning("[dry-run] %s would be drained", node.name)
                drained += 1
            else:
                drain(node, f"no distinct address available; shares {ip}")
                drained += 1
    return rolls, drained


def one_pass(
    nodes: list[Node],
    max_rolls: int,
    max_retries: int,
    max_rotations_per_pass: int,
    settle: float,
    dry_run: bool = False,
) -> tuple[int, int] | None:
    """One measure/decide/act cycle. Returns (rotations, drains), or None when
    no node answered.

    Revival gets the full node list, not just the ones that answered: a drained
    node's proxy has stopped listening, so it is never reachable, so passing
    only the reachable set would make the revival check structurally unable to
    fire. That is what made drains permanent.
    """
    reachable = poll_all(nodes)
    if not reachable:
        log.warning("no node answered; will retry")
        return None

    live = [n for n in reachable if not n.drained]
    distinct = {n.ip for n in live}
    log.info(
        "pool: %d/%d nodes live, %d distinct addresses%s | %s",
        len(live),
        len(reachable),
        len(distinct),
        "" if len(distinct) >= len(live) else "  <-- collisions",
        " ".join(str(n) for n in reachable),
    )

    revive_if_free(nodes)
    rolls, drained = resolve_collisions(
        reachable, max_rolls, max_retries, max_rotations_per_pass, settle, dry_run
    )
    return rolls, drained


def main() -> int:
    parser = argparse.ArgumentParser(description="De-duplicate a WARP egress pool.")
    parser.add_argument(
        "--nodes",
        default=os.environ.get("POOL_NODES", ""),
        help="comma-separated node names, e.g. alztrk-warp-a,alztrk-warp-b",
    )
    parser.add_argument(
        "--markers",
        default=os.environ.get("MARKER_DIR", DEFAULT_MARKER_DIR),
        help="directory of per-node marker files, shared with the proxies",
    )
    parser.add_argument("--interval", type=int, default=30, help="seconds between passes")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be rotated or drained without changing anything",
    )
    parser.add_argument("--max-rolls", type=int, default=DEFAULT_MAX_ROLLS)
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument(
        "--max-rotations-per-pass",
        type=int,
        default=DEFAULT_MAX_ROTATIONS_PER_PASS,
        help="rotations allowed per pass; each one costs a WARP registration",
    )
    parser.add_argument(
        "--min-rotate-gap",
        type=float,
        default=DEFAULT_MIN_ROTATE_GAP,
        help="minimum seconds between any two rotations in the pool",
    )
    parser.add_argument("--settle", type=float, default=DEFAULT_SETTLE)
    args = parser.parse_args()

    global _min_rotate_gap
    _min_rotate_gap = args.min_rotate_gap

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    names = [n for n in args.nodes.replace(" ", "").split(",") if n]
    if not names:
        log.error("no nodes given; set POOL_NODES or pass --nodes")
        return 2

    nodes = [Node(name, args.markers) for name in names]
    log.info(
        "dedupe starting: %d nodes, interval=%ds, max_rolls=%d, "
        "max_rotations_per_pass=%d, min_rotate_gap=%.0fs, markers=%s%s",
        len(nodes),
        args.interval,
        args.max_rolls,
        args.max_rotations_per_pass,
        _min_rotate_gap,
        args.markers,
        " (dry run)" if args.dry_run else "",
    )

    while True:
        try:
            result = one_pass(
                nodes,
                args.max_rolls,
                args.max_retries,
                args.max_rotations_per_pass,
                args.settle,
                args.dry_run,
            )
            if result and any(result):
                log.info("pass summary: %d rotations, %d drained", *result)
        except Exception:  # keep the loop alive; this service is optional
            log.exception("unexpected error during pass")

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
