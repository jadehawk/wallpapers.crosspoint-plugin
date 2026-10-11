#!/usr/bin/env python3
"""Build high-quality 1-bit BMP thumbnails for the generated-thumbnails branch."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

from build_cache_seed import SOURCE_THUMBNAIL_URL, request_bytes, thumbnail_to_bmp

DEFAULT_WORKERS = 16
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3


def wallpaper_ids(catalog_root: Path) -> list[str]:
    index = json.loads((catalog_root / "index.json").read_text(encoding="utf-8"))
    ids: set[str] = set()
    for catalog in index.get("catalogs", []):
        key = str(catalog.get("key") or "").strip("/")
        pages = int(catalog.get("pages") or 0)
        for page in range(1, pages + 1):
            payload = json.loads((catalog_root / key / f"{page}.json").read_text(encoding="utf-8"))
            for item in payload.get("items", []):
                wallpaper_id = str(item.get("id") or "").strip()
                if wallpaper_id:
                    ids.add(wallpaper_id)
    return sorted(ids)


def valid_bmp(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 62:
        return False
    with path.open("rb") as handle:
        return handle.read(2) == b"BM"


def build_one(wallpaper_id: str, output: Path, *, timeout: float, retries: int) -> str:
    dest = output / f"{wallpaper_id}.bmp"
    if valid_bmp(dest):
        return "cached"
    data = request_bytes(SOURCE_THUMBNAIL_URL.format(id=wallpaper_id), timeout=timeout, retries=retries)
    bmp = thumbnail_to_bmp(data)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(bmp)
    tmp.replace(dest)
    return "built"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogs", type=Path, default=Path("catalogs"))
    parser.add_argument("--output", type=Path, default=Path("dist/hosted-thumbnails"))
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1 or args.retries < 1:
        raise SystemExit("workers and retries must be positive")
    ids = wallpaper_ids(args.catalogs)
    args.output.mkdir(parents=True, exist_ok=True)
    built = cached = 0
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(build_one, wallpaper_id, args.output, timeout=args.timeout, retries=args.retries): wallpaper_id
            for wallpaper_id in ids
        }
        for completed, future in enumerate(as_completed(futures), 1):
            wallpaper_id = futures[future]
            try:
                result = future.result()
                if result == "built":
                    built += 1
                else:
                    cached += 1
            except Exception as exc:  # noqa: BLE001
                failures.append((wallpaper_id, str(exc)))
            if completed % 250 == 0 or completed == len(ids):
                print(f"processed {completed}/{len(ids)} built={built} cached={cached} failures={len(failures)}", flush=True)
    if failures:
        for wallpaper_id, error in failures[:20]:
            print(f"FAILED {wallpaper_id}: {error}")
        raise SystemExit(f"hosted thumbnail build incomplete: {len(failures)} failure(s)")
    print(f"Done: {len(ids)} thumbnails ({built} new, {cached} already present).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
