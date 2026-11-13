#!/usr/bin/env python3
"""Unit tests for scripts/proxy-pool.py."""

import importlib.util
import io
import pathlib
import unittest
import urllib.error
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("proxy_pool", REPO / "scripts" / "proxy-pool.py")
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)


class FakeResponse:
    def __init__(self, status=200, body=b""):
        self.status = status
        self.body = body

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class ParseProxiesFromText(unittest.TestCase):
    def test_strips_comments_and_blank_lines(self):
        sample = """
        # Comment line
        http://1.1.1.1:8080
        
        # Another comment
        socks5://2.2.2.2:1080
        """
        parsed = pp.parse_proxies_from_text(sample)
        self.assertEqual(parsed, ["http://1.1.1.1:8080", "socks5://2.2.2.2:1080"])

    def test_deduplicates_while_preserving_order(self):
        sample = "http://1.1.1.1:8080\nhttp://2.2.2.2:8080\nhttp://1.1.1.1:8080\n"
        parsed = pp.parse_proxies_from_text(sample)
        self.assertEqual(parsed, ["http://1.1.1.1:8080", "http://2.2.2.2:8080"])


class CheckProxyLogic(unittest.TestCase):
    def test_ignores_empty_or_comment(self):
        ok, lat, note = pp.check_proxy("# comment")
        self.assertFalse(ok)
        self.assertEqual(note, "ignored")

    def test_detects_success_response(self):
        fake_resp = FakeResponse(200, b"OK")
        fake_opener = mock.MagicMock()
        fake_opener.open.return_value = fake_resp
        with mock.patch("urllib.request.build_opener", return_value=fake_opener):
            ok, lat, note = pp.check_proxy("http://10.0.0.1:8080", timeout=1.0)
            self.assertTrue(ok)
            self.assertIn("HTTP 200", note)

    def test_detects_geoblock(self):
        fake_err = urllib.error.HTTPError(
            url="https://opencode.ai/inference",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"not available in your country"}}'),
        )
        fake_opener = mock.MagicMock()
        fake_opener.open.side_effect = fake_err
        with mock.patch("urllib.request.build_opener", return_value=fake_opener):
            ok, lat, note = pp.check_proxy("http://10.0.0.1:8080", timeout=1.0)
            self.assertFalse(ok)
            self.assertEqual(note, "403 GeoBlocked")

    def test_detects_quota_exhaustion(self):
        fake_err = urllib.error.HTTPError(
            url="https://opencode.ai/inference",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"free usage limit reached"}}'),
        )
        fake_opener = mock.MagicMock()
        fake_opener.open.side_effect = fake_err
        with mock.patch("urllib.request.build_opener", return_value=fake_opener):
            ok, lat, note = pp.check_proxy("http://10.0.0.1:8080", timeout=1.0)
            self.assertFalse(ok)
            self.assertEqual(note, "429 QuotaExhausted")

    def test_upstream_reachable_status_counts_as_reachable(self):
        fake_err = urllib.error.HTTPError(
            url="https://opencode.ai/inference",
            code=405,
            msg="Method Not Allowed",
            hdrs={},
            fp=io.BytesIO(b"Method Not Allowed"),
        )
        fake_opener = mock.MagicMock()
        fake_opener.open.side_effect = fake_err
        with mock.patch("urllib.request.build_opener", return_value=fake_opener):
            ok, lat, note = pp.check_proxy("http://10.0.0.1:8080", timeout=1.0)
            self.assertTrue(ok)
            self.assertIn("Reachable", note)

    def test_connection_error_is_reported(self):
        fake_opener = mock.MagicMock()
        fake_opener.open.side_effect = urllib.error.URLError("Connection refused")
        with mock.patch("urllib.request.build_opener", return_value=fake_opener):
            ok, lat, note = pp.check_proxy("http://10.0.0.1:8080", timeout=1.0)
            self.assertFalse(ok)
            self.assertIn("Connection refused", note)


if __name__ == "__main__":
    unittest.main()
