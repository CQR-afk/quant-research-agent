from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from quantlab.research import universe_freeze


class UniverseFreezeTests(unittest.TestCase):
    def make_master(self, *, future_return: float | None = None) -> tuple[pd.DataFrame, Path]:
        anchor_path = Path("config/ashare_universe_qfq_pilot.json")
        anchors = json.loads(anchor_path.read_text(encoding="utf-8"))["members"]
        rows = []
        for index, anchor in enumerate(anchors):
            rows.append({"market": "CN_A", "symbol": anchor["symbol"], "exchange": anchor["exchange"],
                         "security_type": "A_share_main_board", "listing_date": "2017-01-01" if index != 6 else "2020-01-01",
                         "industry": "industry-anchor", "name": anchor["name"], "metadata_source": "fixture"})
        for index in range(120):
            row = {"market": "CN_A", "symbol": f"{index + 100000:06d}.{'SH' if index % 2 else 'SZ'}",
                   "exchange": "SSE" if index % 2 else "SZSE", "security_type": "A_share_main_board",
                   "listing_date": "2016-01-01", "industry": f"industry-{index % 6}",
                   "name": f"Candidate {index}", "metadata_source": "fixture"}
            if future_return is not None:
                row["future_return"] = future_return
            rows.append(row)
        return pd.DataFrame(rows), anchor_path

    def freeze(self, master: pd.DataFrame, anchor_path: Path):
        with patch.object(universe_freeze, "_json_write_atomic"):
            return universe_freeze.build_and_freeze_breadth_universes(
                master, anchor_config_path=anchor_path, candidate_snapshot_time="2026-10-05T00:00:00Z",
                source_hash="source-hash", source_files=[], output_root=Path("unused"),
                seed="fixed-test-seed",
            )

    def test_deterministic_order_nested_pools_and_outcome_independence(self) -> None:
        master, anchor_path = self.make_master(future_return=-1e9)
        first = self.freeze(master, anchor_path)
        master["future_return"] = 1e9
        second = self.freeze(master, anchor_path)
        self.assertEqual(first["candidate_order"], second["candidate_order"])
        self.assertEqual(first["u50"]["universe_hash"], second["u50"]["universe_hash"])
        self.assertEqual(first["u100"]["universe_hash"], second["u100"]["universe_hash"])
        u11 = set(first["u11"])
        u50 = set(first["u50"]["symbols"])
        u100 = set(first["u100"]["symbols"])
        self.assertTrue(u11 < u50 < u100)
        self.assertEqual(len(u50), 50)
        self.assertEqual(len(u100), 100)
        self.assertFalse(first["future_return_used"])
        self.assertEqual(first["survivorship_limitation_code"], "SURVIVORSHIP_LIMITED_PROVIDER_UNIVERSE")
        self.assertEqual(len(first["candidate_order"]), first["eligible_new_candidate_count"])
        self.assertEqual(first["selected_u100_candidate_order"], first["candidate_order"][:89])

    def test_late_listing_anchor_is_exception_but_new_late_stock_is_rejected(self) -> None:
        master, anchor_path = self.make_master()
        master.loc[master["symbol"] == "100005.SH", "listing_date"] = "2020-01-01"
        master.loc[master["symbol"] == "100006.SZ", "listing_date"] = None
        with patch.object(universe_freeze, "_json_write_atomic"):
            result = universe_freeze.build_and_freeze_breadth_universes(
                master, anchor_config_path=anchor_path, candidate_snapshot_time="2026-10-05T00:00:00Z",
                source_hash="source-hash", source_files=[], output_root=Path("unused"), seed="fixed-test-seed",
            )
        self.assertIn("300750.SZ", result["u11"])
        rejected = {item["symbol"]: item["reason"] for item in result["rejections"]}
        self.assertIn("listed_after_2017-12-31", rejected["100005.SH"])
        self.assertIn("listing_date_missing", rejected["100006.SZ"])


if __name__ == "__main__":
    unittest.main()
