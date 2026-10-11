#!/usr/bin/env python3
"""Lightweight check for wallpaper inventory changes before a full catalog rebuild.

The check compares ReadMe.club's reported inventory total and the newest public
wallpaper IDs against the already-published ``catalogs`` snapshot. It normally
needs only the first source page; a second/third page is fetched only when the
first page does not contain enough public entries to compare the local first
catalog page.

This script never writes or regenerates catalog files.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import build_catalog as catalog


@dataclass(frozen=True)
class CatalogSnapshot:
    reported_total: int
    reported_pages: int
    latest_ids: tuple[str, ...]


def load_local_snapshot(catalog_root: Path) -> CatalogSnapshot:
    index = json.loads((catalog_root / "index.json").read_text(encoding="utf-8"))
    latest = json.loads((catalog_root / "latest" / "1.json").read_text(encoding="utf-8"))
    inventory = index.get("inventory") or {}
    latest_ids = tuple(
        str(item.get("id") or "").strip()
        for item in latest.get("items", [])
        if str(item.get("id") or "").strip()
    )
    if not latest_ids:
        raise RuntimeError("local catalogs/latest/1.json contains no wallpaper IDs")
    return CatalogSnapshot(
        reported_total=int(inventory.get("reported_total") or 0),
        reported_pages=int(inventory.get("reported_pages") or 0),
        latest_ids=latest_ids,
    )


def fetch_remote_snapshot(*, compare_count: int, timeout: float, retries: int) -> CatalogSnapshot:
    if compare_count < 1:
        raise ValueError("compare_count must be positive")

    first = catalog.fetch_gallery_page(1, sort="latest", timeout=timeout, retries=retries)
    total_pages = int(first.get("totalPages") or 1)
    records = list(first.get("wallpapers") or [])

    page = 2
    while len([record for record in records if catalog.is_public_candidate(record)]) < compare_count and page <= total_pages:
        payload = catalog.fetch_gallery_page(page, sort="latest", timeout=timeout, retries=retries)
        records.extend(payload.get("wallpapers") or [])
        page += 1
        if page > 3:  # keep this a lightweight probe even if the source is unexpectedly noisy
            break

    public_records = [record for record in records if catalog.is_public_candidate(record)]
    latest_ids = tuple(str(record["id"]) for record in catalog.sort_latest(public_records)[:compare_count])
    return CatalogSnapshot(
        reported_total=int(first.get("total") or 0),
        reported_pages=total_pages,
        latest_ids=latest_ids,
    )


def compare_snapshots(local: CatalogSnapshot, remote: CatalogSnapshot) -> list[str]:
    reasons: list[str] = []
    if local.reported_total != remote.reported_total:
        reasons.append(f"reported_total {local.reported_total} -> {remote.reported_total}")
    if local.reported_pages != remote.reported_pages:
        reasons.append(f"reported_pages {local.reported_pages} -> {remote.reported_pages}")
    if len(remote.latest_ids) < len(local.latest_ids):
        reasons.append(
            f"remote latest probe returned only {len(remote.latest_ids)} public IDs; local page has {len(local.latest_ids)}"
        )
    elif tuple(remote.latest_ids[: len(local.latest_ids)]) != local.latest_ids:
        reasons.append("latest wallpaper IDs changed")
    return reasons


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogs", type=Path, default=Path("catalogs"))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="optional GitHub Actions output file; appends changed= and reason= values",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.retries < 1:
        raise SystemExit("retries must be positive")

    local = load_local_snapshot(args.catalogs)
    remote = fetch_remote_snapshot(compare_count=len(local.latest_ids), timeout=args.timeout, retries=args.retries)
    reasons = compare_snapshots(local, remote)
    changed = bool(reasons)

    result = {
        "changed": changed,
        "reasons": reasons,
        "local": asdict(local),
        "remote": asdict(remote),
    }
    print(json.dumps(result, indent=2))

    if args.github_output is not None:
        reason = "; ".join(reasons) if reasons else "source inventory matches committed catalogs"
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"changed={'true' if changed else 'false'}\n")
            output.write(f"reason={reason}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
