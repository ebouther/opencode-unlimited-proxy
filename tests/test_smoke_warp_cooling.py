#!/usr/bin/env python3
"""Pin WARP pool quota exhaustion condition in scripts/smoke.sh."""

import pathlib
import unittest

SMOKE = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "smoke.sh"


class SmokeWarpQuotaExhaustionCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = SMOKE.read_text(encoding="utf-8")

    def test_warp_cooling_skip_requires_zero_active(self):
        # When status is not ready or active < 1, SKIP must only trigger if
        # active is truly 0 alongside cooling > 0.
        self.assertIn(
            'elif [ "${active:-0}" -eq 0 ] && [ "${cooling:-0}" -gt 0 ]; then',
            self.src,
            "smoke.sh should only mark QUOTA_EXHAUSTED if active exits are 0",
        )


if __name__ == "__main__":
    unittest.main()
