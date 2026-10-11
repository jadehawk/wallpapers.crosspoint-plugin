from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location("check_catalog_updates", TOOLS / "check_catalog_updates.py")
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)


class CatalogUpdateCheckTests(unittest.TestCase):
    def test_load_local_snapshot_reads_inventory_and_first_latest_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "latest").mkdir()
            (root / "index.json").write_text(
                json.dumps({"inventory": {"reported_total": 123, "reported_pages": 4}}),
                encoding="utf-8",
            )
            (root / "latest" / "1.json").write_text(
                json.dumps({"items": [{"id": "A"}, {"id": "B"}, {"id": "C"}]}),
                encoding="utf-8",
            )

            snapshot = check.load_local_snapshot(root)

        self.assertEqual(snapshot.reported_total, 123)
        self.assertEqual(snapshot.reported_pages, 4)
        self.assertEqual(snapshot.latest_ids, ("A", "B", "C"))

    def test_compare_snapshots_reports_no_change_when_equal(self) -> None:
        snapshot = check.CatalogSnapshot(123, 4, ("A", "B", "C"))
        self.assertEqual(check.compare_snapshots(snapshot, snapshot), [])

    def test_compare_snapshots_detects_total_pages_and_latest_ids(self) -> None:
        local = check.CatalogSnapshot(123, 4, ("A", "B", "C"))
        remote = check.CatalogSnapshot(124, 5, ("X", "A", "B"))
        reasons = check.compare_snapshots(local, remote)
        self.assertIn("reported_total 123 -> 124", reasons)
        self.assertIn("reported_pages 4 -> 5", reasons)
        self.assertIn("latest wallpaper IDs changed", reasons)

    def test_remote_probe_fetches_second_page_only_when_needed(self) -> None:
        pages = {
            1: {
                "total": 64,
                "totalPages": 2,
                "wallpapers": [
                    {"id": "A", "created_at": "2026-10-10T12:00:00Z", "is_nsfw": False},
                    {"id": "hidden", "created_at": "2026-10-10T11:00:00Z", "is_nsfw": True},
                ],
            },
            2: {
                "total": 64,
                "totalPages": 2,
                "wallpapers": [
                    {"id": "B", "created_at": "2026-10-10T10:00:00Z", "is_nsfw": False},
                    {"id": "C", "created_at": "2026-10-10T09:00:00Z", "is_nsfw": False},
                ],
            },
        }

        with patch.object(check.catalog, "fetch_gallery_page", side_effect=lambda page, **_: pages[page]) as fetch:
            snapshot = check.fetch_remote_snapshot(compare_count=3, timeout=1.0, retries=1)

        self.assertEqual(snapshot.reported_total, 64)
        self.assertEqual(snapshot.reported_pages, 2)
        self.assertEqual(snapshot.latest_ids, ("A", "B", "C"))
        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
