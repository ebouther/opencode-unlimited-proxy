#!/usr/bin/env python3
"""Manage, test, and validate proxy pools for opencode-unlimited-proxy.

Usage:
  ./scripts/proxy-pool.py --test "http://127.0.0.1:8888"
  ./scripts/proxy-pool.py --file proxies.txt --output stack/proxies.txt
  ./scripts/proxy-pool.py --file proxies.txt --update-config

This provides an alternative egress route to Cloudflare WARP. If you have your own
HTTP, HTTPS, or SOCKS proxies (residential, datacenter, Tor, SSH tunnels, etc.),
this tool validates them against OpenCode upstream endpoints before adding them
to the gateway configuration.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

log = logging.getLogger("proxy-pool")
UPSTREAM_TEST_URL = "https://opencode.ai/inference"
DEFAULT_TIMEOUT = 10.0


def check_proxy(proxy_url: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, float, str]:
    """Test a proxy against OpenCode upstream.

    Returns:
      (is_working, latency_seconds, status_or_error)
    """
    normalized = proxy_url.strip()
    if not normalized or normalized.startswith("#"):
        return False, 0.0, "ignored"

    if "://" not in normalized:
        normalized = f"http://{normalized}"

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": normalized, "https": normalized})
    )
    # Realistic headers
    opener.addheaders = [
        ("User-Agent", "opencode/1.19.0 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"),
        ("Accept", "*/*"),
    ]

    start = time.monotonic()
    req = urllib.request.Request(UPSTREAM_TEST_URL, method="HEAD")
    try:
        with opener.open(req, timeout=timeout) as resp:
            elapsed = time.monotonic() - start
            return True, elapsed, f"HTTP {resp.status}"
    except urllib.error.HTTPError as err:
        elapsed = time.monotonic() - start
        # 400, 401, 403 (with FreeTierError), 404, 405, 429 means upstream reached
        if err.code in (400, 401, 403, 404, 405, 429):
            body = err.read(300).decode("utf-8", errors="replace").lower()
            if "not available in your country" in body:
                return False, elapsed, "403 GeoBlocked"
            if "free usage limit reached" in body:
                return False, elapsed, "429 QuotaExhausted"
            return True, elapsed, f"HTTP {err.code} (Reachable)"
        return False, elapsed, f"HTTP {err.code}"
    except Exception as exc:
        elapsed = time.monotonic() - start
        return False, elapsed, str(exc)[:60]


def parse_proxies_from_text(text: str) -> list[str]:
    """Extract valid non-comment proxy lines."""
    results: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            results.append(line)
    return list(dict.fromkeys(results))


def test_proxies_concurrently(
    proxies: list[str],
    concurrency: int = 10,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[tuple[str, bool, float, str]]:
    """Test a list of proxies concurrently."""
    results: list[tuple[str, bool, float, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_map = {
            executor.submit(check_proxy, proxy, timeout): proxy
            for proxy in proxies
        }
        for future in concurrent.futures.as_completed(future_map):
            proxy = future_map[future]
            try:
                ok, lat, note = future.result()
            except Exception as e:
                ok, lat, note = False, 0.0, str(e)
            results.append((proxy, ok, lat, note))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Test and manage proxy pool for OpenCode gateway.")
    parser.add_argument("--test", help="Test a single proxy URL.")
    parser.add_argument("--file", "-f", help="Path to file containing proxy URLs (one per line).")
    parser.add_argument("--output", "-o", help="Write working proxies to this file.")
    parser.add_argument(
        "--update-config",
        action="store_true",
        help="Update stack/config.json with working proxies and set warp.enabled: false.",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Timeout in seconds per proxy.")
    parser.add_argument("--concurrency", "-c", type=int, default=10, help="Concurrent test threads.")
    args = parser.parse_args()

    if args.test:
        ok, lat, note = check_proxy(args.test, timeout=args.timeout)
        print(f"Proxy:   {args.test}")
        print(f"Status:  {'PASS' if ok else 'FAIL'} ({note})")
        print(f"Latency: {lat * 1000:.1f}ms")
        return 0 if ok else 1

    proxies: list[str] = []
    if args.file:
        path = pathlib.Path(args.file)
        if not path.exists():
            print(f"Error: Proxy file not found: {args.file}", file=sys.stderr)
            return 1
        proxies = parse_proxies_from_text(path.read_text(encoding="utf-8"))
    elif not sys.stdin.isatty():
        proxies = parse_proxies_from_text(sys.stdin.read())

    if not proxies:
        print("No proxies provided. Use --test <url>, --file <path>, or pipe via stdin.", file=sys.stderr)
        return 1

    print(f"Testing {len(proxies)} proxies with concurrency={args.concurrency}...")
    tested = test_proxies_concurrently(proxies, concurrency=args.concurrency, timeout=args.timeout)

    working = [p for p, ok, lat, note in tested if ok]
    for proxy, ok, lat, note in tested:
        status_tag = "\033[32mPASS\033[0m" if ok else "\033[31mFAIL\033[0m"
        print(f"[{status_tag}] {proxy} ({lat * 1000:.0f}ms) - {note}")

    print(f"\nResult: {len(working)}/{len(proxies)} proxies functional.")

    if args.output:
        out_path = pathlib.Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(working) + "\n", encoding="utf-8")
        print(f"Saved working proxies to {args.output}")

    if args.update_config:
        cfg_path = pathlib.Path(__file__).resolve().parent.parent / "stack" / "config.json"
        if not cfg_path.exists():
            print(f"Error: {cfg_path} not found.", file=sys.stderr)
            return 1
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg.setdefault("warp", {})["enabled"] = False
        cfg["proxies"] = working if working else ["direct"]
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {cfg_path}: warp.enabled=false, proxies={cfg['proxies']}")

    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
