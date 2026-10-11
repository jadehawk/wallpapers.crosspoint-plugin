#!/usr/bin/env python3
"""Build a downloadable CrossPoint thumbnail-cache seed from generated catalogs.

The seed intentionally contains only persistent thumbnail BMPs. Catalog JSON remains
live and small; after a user installs the seed, opening a catalog still refreshes its
JSON normally, but the first visible thumbnails can be rendered from SD immediately.

By default the script selects the first 100 unique wallpapers from every catalog in
``catalogs/index.json`` and globally de-duplicates wallpaper IDs. Those first entries
match the order users see in each generated catalog (Latest/categories/devices are
newest-first; Popular and curated packs retain their own catalog order).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import struct
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from PIL import Image

THUMBNAIL_URL = (
    "https://api.readme.club/storage/v1/object/public/"
    "wallpapers-public/wallpapers/{id}/thumbnail.jpg"
)
CACHE_IMAGE_PREFIX = "image-v1|200x300|1|GET|"
TARGET_WIDTH = 200
TARGET_HEIGHT = 300
DEFAULT_PER_CATALOG = 100
DEFAULT_WORKERS = 12
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
USER_AGENT = "wallpapers.crosspoint-plugin cache-seed-builder/1"


@dataclass(frozen=True)
class SeedItem:
    wallpaper_id: str
    thumbnail_url: str
    catalogs: tuple[str, ...]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def cache_filename(thumbnail_url: str) -> str:
    # Must stay byte-for-byte aligned with PluginCatalogActivity::cacheImagePath().
    key = f"{CACHE_IMAGE_PREFIX}{thumbnail_url}|"
    return f"i_{hashlib.md5(key.encode('utf-8')).hexdigest()}.bmp"


def iter_catalog_items(catalog_root: Path, catalog: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    key = str(catalog.get("key") or "").strip("/")
    pages = max(0, int(catalog.get("pages") or 0))
    seen: set[str] = set()
    for page_number in range(1, pages + 1):
        page_path = catalog_root / key / f"{page_number}.json"
        if not page_path.is_file():
            raise FileNotFoundError(f"catalog page missing: {page_path}")
        payload = load_json(page_path)
        for item in payload.get("items", []):
            wallpaper_id = str(item.get("id") or "").strip()
            if not wallpaper_id or wallpaper_id in seen:
                continue
            seen.add(wallpaper_id)
            yield dict(item)


def select_seed_items(catalog_root: Path, per_catalog: int) -> tuple[list[SeedItem], list[dict[str, Any]]]:
    if per_catalog < 1:
        raise ValueError("per_catalog must be positive")
    index = load_json(catalog_root / "index.json")
    catalogs = list(index.get("catalogs") or [])
    memberships: dict[str, set[str]] = {}
    catalog_stats: list[dict[str, Any]] = []

    for catalog in catalogs:
        key = str(catalog.get("key") or "")
        title = str(catalog.get("title") or key)
        selected_ids: list[str] = []
        for item in iter_catalog_items(catalog_root, catalog):
            wallpaper_id = str(item.get("id") or "").strip()
            if not wallpaper_id:
                continue
            selected_ids.append(wallpaper_id)
            memberships.setdefault(wallpaper_id, set()).add(title)
            if len(selected_ids) >= per_catalog:
                break
        catalog_stats.append(
            {
                "key": key,
                "title": title,
                "selected": len(selected_ids),
                "available": int(catalog.get("items") or 0),
            }
        )

    items = [
        SeedItem(
            wallpaper_id=wallpaper_id,
            thumbnail_url=THUMBNAIL_URL.format(id=wallpaper_id),
            catalogs=tuple(sorted(titles)),
        )
        for wallpaper_id, titles in sorted(memberships.items())
    ]
    return items, catalog_stats


def request_bytes(url: str, *, timeout: float, retries: int) -> bytes:
    last_error: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                if not data:
                    raise RuntimeError("empty response")
                return data
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(3.0, 0.5 * attempt))
    raise RuntimeError(f"failed to download {url}: {last_error}")


def _scale_to_cover(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    width, height = image.size
    if width < 1 or height < 1:
        raise ValueError("invalid source image dimensions")
    scale = max(max_width / width, max_height / height)
    out_width = max(1, int(width * scale))
    out_height = max(1, int(height * scale))
    if (out_width, out_height) == image.size:
        return image
    return image.resize((out_width, out_height), Image.Resampling.LANCZOS)


def _atkinson_1bit(gray: Image.Image) -> tuple[int, int, bytearray]:
    gray = gray.convert("L")
    width, height = gray.size
    pixels = [float(value) for value in gray.tobytes()]
    output = bytearray(width * height)

    def add_error(x: int, y: int, error: float) -> None:
        if 0 <= x < width and 0 <= y < height:
            idx = y * width + x
            pixels[idx] = min(255.0, max(0.0, pixels[idx] + error))

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            old = pixels[idx]
            new = 255 if old >= 128 else 0
            output[idx] = new
            error = (old - new) / 8.0
            add_error(x + 1, y, error)
            add_error(x + 2, y, error)
            add_error(x - 1, y + 1, error)
            add_error(x, y + 1, error)
            add_error(x + 1, y + 1, error)
            add_error(x, y + 2, error)
    return width, height, output


def encode_top_down_1bit_bmp(width: int, height: int, pixels: bytearray) -> bytes:
    row_bytes = ((width + 31) // 32) * 4
    image_size = row_bytes * height
    pixel_offset = 62
    file_size = pixel_offset + image_size
    out = io.BytesIO()
    out.write(b"BM")
    out.write(struct.pack("<IHHI", file_size, 0, 0, pixel_offset))
    out.write(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 1, 0, image_size, 2835, 2835, 2, 2))
    out.write(b"\x00\x00\x00\x00\xff\xff\xff\x00")

    for y in range(height):
        packed = bytearray(row_bytes)
        row_start = y * width
        for x in range(width):
            if pixels[row_start + x] >= 128:
                packed[x // 8] |= 1 << (7 - (x % 8))
        out.write(packed)
    return out.getvalue()


def thumbnail_to_bmp(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        scaled = _scale_to_cover(source.convert("L"), TARGET_WIDTH, TARGET_HEIGHT)
    width, height, pixels = _atkinson_1bit(scaled)
    return encode_top_down_1bit_bmp(width, height, pixels)


def build_one(item: SeedItem, cache_dir: Path, *, timeout: float, retries: int) -> tuple[str, int]:
    bmp_path = cache_dir / cache_filename(item.thumbnail_url)
    if bmp_path.is_file() and bmp_path.stat().st_size > 62:
        return item.wallpaper_id, bmp_path.stat().st_size
    data = request_bytes(item.thumbnail_url, timeout=timeout, retries=retries)
    bmp = thumbnail_to_bmp(data)
    bmp_path.write_bytes(bmp)
    return item.wallpaper_id, len(bmp)


def write_seed_zip(seed_root: Path, zip_path: Path, summary_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(seed_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(seed_root).as_posix())
        archive.write(summary_path, "wallpaper-cache-seed.json")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogs", type=Path, default=Path("catalogs"))
    parser.add_argument("--output", type=Path, default=Path("dist/cache-seed"))
    parser.add_argument("--zip", dest="zip_path", type=Path, default=Path("dist/wallpaper-cache-seed.zip"))
    parser.add_argument("--summary", type=Path, default=Path("dist/wallpaper-cache-seed.json"))
    parser.add_argument("--limit-per-catalog", type=int, default=DEFAULT_PER_CATALOG)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--max-images", type=int, default=None, help="development-only cap after global de-duplication")
    parser.add_argument("--dry-run", action="store_true", help="select and report seed membership without downloading images")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1 or args.retries < 1 or args.limit_per_catalog < 1:
        raise SystemExit("workers, retries, and limit-per-catalog must be positive")

    items, catalog_stats = select_seed_items(args.catalogs, args.limit_per_catalog)
    if args.max_images is not None:
        items = items[: max(0, args.max_images)]

    print(
        f"Selected {len(items)} globally unique thumbnails from {len(catalog_stats)} catalogs "
        f"(up to {args.limit_per_catalog} per catalog).",
        flush=True,
    )
    if args.dry_run:
        return 0

    if args.output.exists():
        shutil.rmtree(args.output)
    cache_dir = args.output / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    failures: list[tuple[str, str]] = []
    total_bytes = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_map = {
            pool.submit(build_one, item, cache_dir, timeout=args.timeout, retries=args.retries): item for item in items
        }
        for future in as_completed(future_map):
            item = future_map[future]
            try:
                _, size = future.result()
                total_bytes += size
                completed += 1
                if completed % 100 == 0 or completed == len(items):
                    print(f"  built {completed}/{len(items)} thumbnails", flush=True)
            except Exception as exc:  # noqa: BLE001 - aggregate failures for a useful workflow summary
                failures.append((item.wallpaper_id, str(exc)))
                print(f"  FAILED {item.wallpaper_id}: {exc}", flush=True)

    summary = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_index": "catalogs/index.json",
        "selection": "first entries in each generated catalog, globally de-duplicated",
        "limit_per_catalog": args.limit_per_catalog,
        "catalog_count": len(catalog_stats),
        "selected_unique_images": len(items),
        "built_images": completed,
        "failed_images": len(failures),
        "expanded_cache_bytes": total_bytes,
        "cache_format": {
            "path": ".cache/i_<md5>.bmp",
            "key": "image-v1|200x300|1|GET|<thumbnail-url>|",
            "bmp": "1-bit top-down BMP; scale-to-cover 200x300; Atkinson dither",
        },
        "catalogs": catalog_stats,
        "failures": [{"id": wallpaper_id, "error": error} for wallpaper_id, error in failures],
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if failures:
        raise SystemExit(f"cache seed incomplete: {len(failures)} thumbnail(s) failed")

    write_seed_zip(args.output, args.zip_path, args.summary)
    zip_size = args.zip_path.stat().st_size
    print(
        f"Done: {completed} thumbnails, {total_bytes / (1024 * 1024):.2f} MiB expanded, "
        f"{zip_size / (1024 * 1024):.2f} MiB ZIP -> {args.zip_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
