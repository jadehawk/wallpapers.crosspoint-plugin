#!/usr/bin/env python3
"""Build device-ready grayscale BMPs for wallpapers that ReadMe.club marks needs_conversion."""

from __future__ import annotations

import argparse
import io
import json
import struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageOps

try:
    from .build_cache_seed import request_bytes
    from .build_catalog import DOWNLOAD_URL
    from .hosted_assets import sharded_bmp_path
except ImportError:  # Direct script execution: python tools/build_hosted_wallpapers.py
    from build_cache_seed import request_bytes
    from build_catalog import DOWNLOAD_URL
    from hosted_assets import sharded_bmp_path

TARGET_WIDTH = 480
TARGET_HEIGHT = 800
DEFAULT_WORKERS = 8
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
BMP_BPP = 4
BMP_COLORS = (0, 85, 170, 255)
BMP_PIXEL_OFFSET = 14 + 40 + len(BMP_COLORS) * 4


def wallpaper_ids(catalog_root: Path) -> list[str]:
    index = json.loads((catalog_root / "index.json").read_text(encoding="utf-8"))
    ids: set[str] = set()
    for catalog in index.get("catalogs", []):
        key = str(catalog.get("key") or "").strip("/")
        pages = int(catalog.get("pages") or 0)
        for page in range(1, pages + 1):
            payload = json.loads((catalog_root / key / f"{page}.json").read_text(encoding="utf-8"))
            for item in payload.get("items", []):
                if str(item.get("format") or "") != "needs_conversion":
                    continue
                wallpaper_id = str(item.get("id") or "").strip()
                if wallpaper_id:
                    ids.add(wallpaper_id)
    return sorted(ids)


def _row_bytes(width: int) -> int:
    return ((width * BMP_BPP + 31) // 32) * 4


def valid_bmp(path: Path) -> bool:
    if not path.is_file():
        return False
    expected_size = BMP_PIXEL_OFFSET + _row_bytes(TARGET_WIDTH) * TARGET_HEIGHT
    if path.stat().st_size != expected_size:
        return False
    try:
        header = path.read_bytes()[:54]
        if len(header) < 54 or header[:2] != b"BM":
            return False
        pixel_offset = struct.unpack_from("<I", header, 10)[0]
        dib_size = struct.unpack_from("<I", header, 14)[0]
        width, height = struct.unpack_from("<ii", header, 18)
        planes, bpp = struct.unpack_from("<HH", header, 26)
        compression = struct.unpack_from("<I", header, 30)[0]
        colors_used = struct.unpack_from("<I", header, 46)[0]
    except (OSError, struct.error):
        return False
    return (
        pixel_offset == BMP_PIXEL_OFFSET
        and dib_size >= 40
        and width == TARGET_WIDTH
        and height == -TARGET_HEIGHT
        and planes == 1
        and bpp == BMP_BPP
        and compression == 0
        and colors_used == len(BMP_COLORS)
    )


def _fit_grayscale(source: Image.Image) -> Image.Image:
    image = source.convert("L")
    if image.width < 1 or image.height < 1:
        raise ValueError("invalid source dimensions")
    return ImageOps.fit(
        image,
        (TARGET_WIDTH, TARGET_HEIGHT),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )


def _atkinson_native_indices(image: Image.Image) -> bytearray:
    """Offline-dither an L image to CrossPoint's native 0/85/170/255 levels."""
    if image.mode != "L" or image.size != (TARGET_WIDTH, TARGET_HEIGHT):
        raise ValueError("expected fitted 480x800 grayscale image")

    width, height = image.size
    work = [float(value) for value in image.tobytes()]
    output = bytearray(width * height)

    def add_error(x: int, y: int, error: float) -> None:
        if 0 <= x < width and 0 <= y < height:
            idx = y * width + x
            work[idx] = max(0.0, min(255.0, work[idx] + error))

    for y in range(height):
        row = y * width
        for x in range(width):
            idx = row + x
            old = work[idx]
            level = max(0, min(3, int(round(old / 85.0))))
            new = float(BMP_COLORS[level])
            output[idx] = level
            error = (old - new) / 8.0
            add_error(x + 1, y, error)
            add_error(x + 2, y, error)
            add_error(x - 1, y + 1, error)
            add_error(x, y + 1, error)
            add_error(x + 1, y + 1, error)
            add_error(x, y + 2, error)
    return output


def encode_top_down_4bit_bmp(width: int, height: int, indices: bytearray) -> bytes:
    if len(indices) != width * height:
        raise ValueError("pixel index buffer does not match dimensions")
    row_bytes = ((width * BMP_BPP + 31) // 32) * 4
    image_size = row_bytes * height
    file_size = BMP_PIXEL_OFFSET + image_size
    out = io.BytesIO()
    out.write(b"BM")
    out.write(struct.pack("<IHHI", file_size, 0, 0, BMP_PIXEL_OFFSET))
    out.write(
        struct.pack(
            "<IiiHHIIiiII",
            40,
            width,
            -height,
            1,
            BMP_BPP,
            0,
            image_size,
            2835,
            2835,
            len(BMP_COLORS),
            len(BMP_COLORS),
        )
    )
    for gray in BMP_COLORS:
        out.write(bytes((gray, gray, gray, 0)))

    for y in range(height):
        packed = bytearray(row_bytes)
        row_start = y * width
        for x in range(width):
            level = indices[row_start + x] & 0x0F
            if x & 1:
                packed[x >> 1] |= level
            else:
                packed[x >> 1] |= level << 4
        out.write(packed)
    return out.getvalue()


def image_to_device_bmp(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        image = _fit_grayscale(source)
    indices = _atkinson_native_indices(image)
    return encode_top_down_4bit_bmp(TARGET_WIDTH, TARGET_HEIGHT, indices)


def build_one(wallpaper_id: str, output: Path, *, timeout: float, retries: int) -> str:
    dest = sharded_bmp_path(output, wallpaper_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if valid_bmp(dest):
        return "cached"
    data = request_bytes(DOWNLOAD_URL.format(id=wallpaper_id), timeout=timeout, retries=retries)
    bmp = image_to_device_bmp(data)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(bmp)
    tmp.replace(dest)
    if not valid_bmp(dest):
        dest.unlink(missing_ok=True)
        raise ValueError("generated BMP failed CrossPoint compatibility validation")
    return "built"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogs", type=Path, default=Path("catalogs"))
    parser.add_argument("--output", type=Path, default=Path("dist/hosted-wallpapers"))
    parser.add_argument("--id", dest="ids", action="append", default=[], help="build only this needs_conversion wallpaper ID; repeatable")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers < 1 or args.retries < 1:
        raise SystemExit("workers and retries must be positive")

    available = wallpaper_ids(args.catalogs)
    if args.ids:
        requested = {str(value).strip() for value in args.ids if str(value).strip()}
        unknown = requested.difference(available)
        if unknown:
            raise SystemExit(f"requested ID is not a needs_conversion catalog item: {', '.join(sorted(unknown))}")
        ids = sorted(requested)
    else:
        ids = available

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
            except Exception as exc:
                failures.append((wallpaper_id, str(exc)))
            if completed % 50 == 0 or completed == len(ids):
                print(f"processed {completed}/{len(ids)} built={built} cached={cached} failures={len(failures)}", flush=True)
    if failures:
        for wallpaper_id, error in failures[:20]:
            print(f"FAILED {wallpaper_id}: {error}")
        raise SystemExit(f"hosted wallpaper build incomplete: {len(failures)} failure(s)")
    print(f"Done: {len(ids)} hosted wallpapers ({built} new, {cached} already present).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
