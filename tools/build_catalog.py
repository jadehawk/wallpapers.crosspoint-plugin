#!/usr/bin/env python3
"""Build paged CrossPoint wallpaper catalogs from the public ReadMe.club gallery.

The generated files are static JSON pages suitable for CrossPoint's generic
``device.json`` catalog browser. Each page contains PAGE_SIZE visible items plus
one look-ahead item so the firmware can decide whether to show "Next page".
"""

from __future__ import annotations

import argparse
import html
import json
import random
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .hosted_assets import hosted_thumbnail_url, hosted_wallpaper_url
except ImportError:  # Direct script execution: python tools/build_catalog.py
    from hosted_assets import hosted_thumbnail_url, hosted_wallpaper_url

VERSION = "0.1.1"
SITE_BASE = "https://www.readme.club"
GALLERY_URL = f"{SITE_BASE}/wallpapers"
DOWNLOAD_URL = f"{SITE_BASE}/api/wallpapers/{{id}}/proxy-download?format=original"
DEFAULT_RAW_BASE = (
    "https://raw.githubusercontent.com/jadehawk/"
    "wallpapers.crosspoint-plugin/main/catalogs"
)
PAGE_SIZE = 16
SOURCE_PAGE_SIZE = 32
DEFAULT_RANDOM_COUNT = 96
DEFAULT_PACK_MONTHS = 3
DEFAULT_WORKERS = 8
USER_AGENT = f"wallpapers.crosspoint-plugin/{VERSION} (+https://www.readme.club)"

CATEGORY_LABELS: dict[str, str] = {
    "custom": "Custom",
    "dark": "Dark",
    "light": "Light",
    "minimalist": "Minimalist",
    "other": "Other",
    "pop_culture": "Pop Culture",
}

CATEGORY_PACK_SLUGS: dict[str, str] = {
    "custom": "custom",
    "dark": "dark",
    "light": "light",
    "minimalist": "minimalist",
    "other": "other",
    "pop_culture": "pop_culture",
}

# A catalog may combine more than one ReadMe.club compatibility filter. X4C is
# currently represented by the X4-family catalog until ReadMe.club exposes a
# dedicated X4C filter.
DEVICE_CATALOGS: tuple[dict[str, Any], ...] = (
    {"key": "devices/x3", "title": "X3", "fits": ("x3",)},
    {
        "key": "devices/x4-family",
        "title": "X4 / X4 Pro / X4C",
        "fits": ("x4", "x4pro"),
    },
    {
        "key": "devices/picco",
        "title": "Picco",
        "fits": ("boox-picco",),
    },
    {
        "key": "devices/sticky",
        "title": "Seeed reTerminal Sticky",
        "fits": ("seeedstudio-sticky",),
    },
    {
        "key": "devices/paper-pro-move",
        "title": "Paper Pro Move",
        "fits": ("remarkable-paper-move",),
    },
)

WALLPAPER_LINK_RE = re.compile(
    r'href=["\'](?:https://www\.readme\.club)?/wallpapers/[^"\']*?-([0-9a-fA-F]{8})(?:["\'/?#])'
)


class WallpaperDataParser(HTMLParser):
    """Extract the contents of <script id="wallpapers-data">."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._capture = False
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attributes = {key.lower(): value for key, value in attrs}
        if attributes.get("id") == "wallpapers-data":
            self._capture = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capture:
            self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._chunks.append(data)

    @property
    def data(self) -> str:
        return "".join(self._chunks).strip()


def extract_gallery_data(document: str) -> dict[str, Any]:
    parser = WallpaperDataParser()
    parser.feed(document)
    if not parser.data:
        raise ValueError("ReadMe.club page did not contain #wallpapers-data")
    payload = json.loads(html.unescape(parser.data))
    if not isinstance(payload, dict) or not isinstance(payload.get("wallpapers"), list):
        raise ValueError("ReadMe.club #wallpapers-data has an unexpected shape")
    return payload


def _request(url: str, *, timeout: float, retries: int) -> str:
    last_error: BaseException | None = None
    for attempt in range(1, retries + 1):
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public source
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 8))
    assert last_error is not None
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def gallery_url(*, page: int, sort: str = "latest", fits: str | None = None) -> str:
    params: list[tuple[str, str]] = [("page", str(page)), ("sort", sort)]
    if fits:
        params.append(("fits", fits))
    return f"{GALLERY_URL}?{urlencode(params)}"


def fetch_gallery_page(
    page: int,
    *,
    sort: str = "latest",
    fits: str | None = None,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    return extract_gallery_data(
        _request(gallery_url(page=page, sort=sort, fits=fits), timeout=timeout, retries=retries)
    )


def _dedupe_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        wallpaper_id = str(record.get("id") or "").strip()
        if not wallpaper_id:
            continue
        by_id[wallpaper_id] = dict(record)
    return list(by_id.values())


def crawl_gallery(
    *,
    fits: str | None,
    timeout: float,
    retries: int,
    workers: int,
    max_pages: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    first = fetch_gallery_page(1, fits=fits, timeout=timeout, retries=retries)
    total_pages = int(first.get("totalPages") or 1)
    requested_pages = min(total_pages, max_pages) if max_pages else total_pages
    records: list[Mapping[str, Any]] = list(first["wallpapers"])

    if requested_pages > 1:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(
                    fetch_gallery_page,
                    page,
                    fits=fits,
                    timeout=timeout,
                    retries=retries,
                ): page
                for page in range(2, requested_pages + 1)
            }
            completed: dict[int, list[dict[str, Any]]] = {}
            for future in as_completed(futures):
                page = futures[future]
                payload = future.result()
                completed[page] = list(payload["wallpapers"])
            for page in sorted(completed):
                records.extend(completed[page])

    deduped = _dedupe_records(records)
    return deduped, {
        "reported_total": int(first.get("total") or 0),
        "reported_pages": total_pages,
        "fetched_pages": requested_pages,
        "fetched_unique": len(deduped),
    }


def is_public_candidate(record: Mapping[str, Any]) -> bool:
    return bool(record.get("id")) and not bool(record.get("is_nsfw"))


def category_label(category: Any) -> str:
    raw = str(category or "other").strip()
    return CATEGORY_LABELS.get(raw, raw.replace("_", " ").title() or "Other")


def uploader_name(record: Mapping[str, Any]) -> str:
    for key in ("author_name", "reddit_username", "instagram_username"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return "Unknown"


def compact_number(value: Any) -> str:
    try:
        number = max(0, int(value or 0))
    except (TypeError, ValueError):
        number = 0
    if number < 1000:
        return str(number)
    if number < 1_000_000:
        return f"{number / 1000:.1f}k".replace(".0k", "k")
    return f"{number / 1_000_000:.1f}m".replace(".0m", "m")


def catalog_item(record: Mapping[str, Any]) -> dict[str, Any]:
    wallpaper_id = str(record.get("id") or "").strip()
    title = str(record.get("title") or "Untitled wallpaper").strip() or "Untitled wallpaper"
    category = category_label(record.get("category"))
    uploader = uploader_name(record)
    downloads = int(record.get("download_count") or 0)
    source_format = str(record.get("format") or "")
    download_url = hosted_wallpaper_url(wallpaper_id) if source_format == "needs_conversion" else DOWNLOAD_URL.format(id=wallpaper_id)
    return {
        "id": wallpaper_id,
        "title": title,
        "author": f"{category} · {uploader} · {compact_number(downloads)} downloads",
        "url": download_url,
        "category": str(record.get("category") or "other"),
        "uploader": uploader,
        "downloads": downloads,
        "device": str(record.get("target_device") or ""),
        "width": int(record.get("width") or 0),
        "height": int(record.get("height") or 0),
        "thumbnail": hosted_thumbnail_url(wallpaper_id),
        "created_at": str(record.get("created_at") or ""),
        "format": source_format,
    }


def sort_latest(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(record) for record in records),
        key=lambda record: (str(record.get("created_at") or ""), str(record.get("id") or "")),
        reverse=True,
    )


def sort_popular(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(record) for record in records),
        key=lambda record: (
            int(record.get("download_count") or 0),
            str(record.get("created_at") or ""),
            str(record.get("id") or ""),
        ),
        reverse=True,
    )


def static_pages(items: Sequence[Mapping[str, Any]], page_size: int = PAGE_SIZE) -> list[list[dict[str, Any]]]:
    """Return CrossPoint pages with one look-ahead item on non-final pages."""

    if page_size < 1:
        raise ValueError("page_size must be positive")
    page_count = max(1, (len(items) + page_size - 1) // page_size)
    pages: list[list[dict[str, Any]]] = []
    for page in range(1, page_count + 1):
        start = (page - 1) * page_size
        pages.append([dict(item) for item in items[start : start + page_size + 1]])
    return pages


def write_json(path: Path, payload: Any, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(text, encoding="utf-8")


def write_catalog(output_root: Path, key: str, records: Sequence[Mapping[str, Any]]) -> int:
    items = [catalog_item(record) for record in records]
    pages = static_pages(items)
    catalog_dir = output_root / key
    catalog_dir.mkdir(parents=True, exist_ok=True)
    for page_number, page_items in enumerate(pages, start=1):
        write_json(catalog_dir / f"{page_number}.json", {"items": page_items})
    return len(pages)


def recent_months(count: int, today: date | None = None) -> list[str]:
    if count < 1:
        return []
    cursor = (today or date.today()).replace(day=1)
    months: list[str] = []
    for _ in range(count):
        months.append(cursor.strftime("%Y-%m"))
        if cursor.month == 1:
            cursor = cursor.replace(year=cursor.year - 1, month=12)
        else:
            cursor = cursor.replace(month=cursor.month - 1)
    return months


def extract_pack_prefixes(document: str) -> list[str]:
    seen: set[str] = set()
    prefixes: list[str] = []
    for match in WALLPAPER_LINK_RE.finditer(document):
        prefix = match.group(1).lower()
        if prefix not in seen:
            seen.add(prefix)
            prefixes.append(prefix)
    return prefixes


def fetch_pack_prefixes(
    month: str,
    category: str,
    *,
    timeout: float,
    retries: int,
) -> list[str]:
    slug = f"{CATEGORY_PACK_SLUGS[category]}-{month}"
    url = f"{SITE_BASE}/wallpapers/packs/{slug}"
    try:
        return extract_pack_prefixes(_request(url, timeout=timeout, retries=retries))
    except RuntimeError as exc:
        # Missing pack/category pages are expected while a new month's curation is
        # still being assembled. Only suppress an underlying HTTP 404.
        cause: BaseException | None = exc.__cause__
        if isinstance(cause, HTTPError) and cause.code == 404:
            return []
        raise


def build_month_pack(
    month: str,
    records_by_id: Mapping[str, Mapping[str, Any]],
    *,
    timeout: float,
    retries: int,
) -> list[dict[str, Any]]:
    by_prefix = {wallpaper_id[:8].lower(): dict(record) for wallpaper_id, record in records_by_id.items()}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for category in CATEGORY_LABELS:
        for prefix in fetch_pack_prefixes(month, category, timeout=timeout, retries=retries):
            record = by_prefix.get(prefix)
            if not record or not is_public_candidate(record):
                continue
            wallpaper_id = str(record["id"])
            if wallpaper_id in seen:
                continue
            seen.add(wallpaper_id)
            selected.append(record)
    return selected


def raw_catalog_url(raw_base: str, key: str) -> str:
    return f"{raw_base.rstrip('/')}/{key}/{{page}}.json"


def build_device_manifest(raw_base: str, catalog_specs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not catalog_specs:
        raise ValueError("at least one catalog is required")
    lists = [
        {"title": str(spec["title"]), "url": raw_catalog_url(raw_base, str(spec["key"]))}
        for spec in catalog_specs
    ]
    catalog_root = raw_base.rstrip("/")
    return {
        "title": "Wallpaper Downloader",
        "description": (
            "Browse ReadMe.club wallpapers on stock CrossPoint and enable enhanced grid, preview, settings, and cache "
            "features on capable firmware."
        ),
        "config": {"file": "settings.json"},
        "ui": {
            "default_tab": "browse",
            "tabs": [
                {"id": "browse", "title": "Wallpapers", "type": "browse"},
                {"id": "settings", "title": "Settings", "type": "settings"},
            ],
        },
        "settings": [
            {
                "key": "layout",
                "title": "Layout",
                "type": "choice",
                "default": "grid",
                "options": [
                    {"title": "Grid", "value": "grid"},
                    {"title": "List", "value": "list"},
                ],
            },
            {
                "key": "grid_columns",
                "title": "Grid columns",
                "type": "choice",
                "default": "2",
                "options": [
                    {"title": "1 column", "value": "1"},
                    {"title": "2 columns", "value": "2"},
                    {"title": "3 columns", "value": "3"},
                    {"title": "4 columns", "value": "4"},
                ],
            },
            {
                "key": "grid_rows",
                "title": "Grid rows",
                "type": "choice",
                "default": "2",
                "options": [
                    {"title": "1 row", "value": "1"},
                    {"title": "2 rows", "value": "2"},
                    {"title": "3 rows", "value": "3"},
                ],
            },
            {
                "key": "list_thumbnails",
                "title": "Show thumbnails in list view",
                "type": "toggle",
                "default": True,
            },
            {
                "key": "allow_image_preview",
                "title": "Allow Image Preview",
                "type": "toggle",
                "default": True,
            },
            {
                "key": "selection_style",
                "title": "Row highlight",
                "type": "choice",
                "default": "light",
                "options": [
                    {"title": "Light", "value": "light"},
                    {"title": "Theme", "value": "theme"},
                    {"title": "Outline", "value": "outline"},
                    {"title": "Dark", "value": "dark"},
                ],
            },
            {
                "key": "cache_refresh",
                "title": "Refresh cache",
                "type": "choice",
                "default": "7d",
                "options": [
                    {"title": "Always", "value": "always"},
                    {"title": "3 days", "value": "3d"},
                    {"title": "7 days", "value": "7d"},
                    {"title": "14 days", "value": "14d"},
                ],
            },
            {
                "key": "cache_limit_mb",
                "title": "Cache storage",
                "type": "choice",
                "default": "32",
                "options": [
                    {"title": "8 MB", "value": "8"},
                    {"title": "16 MB", "value": "16"},
                    {"title": "32 MB", "value": "32"},
                    {"title": "64 MB", "value": "64"},
                    {"title": "128 MB", "value": "128"},
                ],
            },
        ],
        "browse": {
            "format": "json",
            "url": raw_catalog_url(raw_base, "latest"),
            "method": "GET",
            "items": "items",
            "page_size": PAGE_SIZE,
            "fields": {
                "title": "title",
                "author": "author",
                "id": "id",
                "url": "url",
                "thumbnail": "thumbnail",
            },
            "lists_url": f"{catalog_root}/index.json",
            "view": "{cfg.layout}",
            "grid_columns": "{cfg.grid_columns}",
            "grid_rows": "{cfg.grid_rows}",
            "grid_page_size": "16",
            "list_page_size": "16",
            "list_thumbnail": "{cfg.list_thumbnails}",
            "selection_style": "{cfg.selection_style}",
            "image": {
                "url": "{thumbnail}",
                "method": "GET",
                "format": "bmp",
                "radius": 6,
            },
            # Stock CrossPoint uses this release-time snapshot. Capability-aware
            # firmware prefers lists_url and keeps these entries as offline/fetch fallback.
            "lists": lists,
        },
        "cache": {
            "enabled": True,
            "refresh": "{cfg.cache_refresh}",
            "limit_mb": "{cfg.cache_limit_mb}",
            "seed": {
                "url": (
                    "https://github.com/jadehawk/wallpapers.crosspoint-plugin/releases/download/"
                    "cache-seed/wallpaper-cache-seed.zip"
                )
            },
        },
        "download": {
            "url": "{url}",
            "dest_dir": "/.sleep",
            "filename": "{id}.bmp",
            "preview_on_select": "{cfg.allow_image_preview}",
        },
        "author": "jadehawk",
        "version": VERSION,
    }

def build_catalog_specs(
    safe_records: Sequence[Mapping[str, Any]],
    device_memberships: Mapping[str, set[str]],
    monthly_packs: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    random_count: int,
    random_seed: str,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    random_records = [dict(record) for record in safe_records]
    random.Random(random_seed).shuffle(random_records)
    specs.append(
        {
            "key": "random",
            "title": "Random Mix",
            "records": random_records[: max(1, random_count)],
        }
    )
    specs.append({"key": "latest", "title": "Latest", "records": sort_latest(safe_records)})
    specs.append({"key": "popular", "title": "Popular", "records": sort_popular(safe_records)})

    records_by_id = {str(record["id"]): dict(record) for record in safe_records}
    for device in DEVICE_CATALOGS:
        ids = device_memberships.get(str(device["key"]), set())
        selected = [records_by_id[wallpaper_id] for wallpaper_id in ids if wallpaper_id in records_by_id]
        specs.append(
            {
                "key": device["key"],
                "title": device["title"],
                "records": sort_latest(selected),
            }
        )

    for category, label in CATEGORY_LABELS.items():
        selected = [record for record in safe_records if record.get("category") == category]
        specs.append(
            {
                "key": f"categories/{category}",
                "title": f"Category · {label}",
                "records": sort_latest(selected),
            }
        )

    for month in monthly_packs:
        specs.append(
            {
                "key": f"packs/{month}",
                "title": f"Monthly Pack · {month}",
                "records": [dict(record) for record in monthly_packs[month]],
            }
        )
    return specs


def build_device_memberships(
    *,
    records_by_id: dict[str, dict[str, Any]],
    timeout: float,
    retries: int,
    workers: int,
    max_pages: int | None,
) -> tuple[dict[str, set[str]], dict[str, Any]]:
    memberships: dict[str, set[str]] = {}
    stats: dict[str, Any] = {}
    filter_cache: dict[str, tuple[list[dict[str, Any]], dict[str, int]]] = {}

    for device in DEVICE_CATALOGS:
        ids: set[str] = set()
        device_stats: dict[str, Any] = {"filters": {}}
        for fits in device["fits"]:
            if fits not in filter_cache:
                filter_cache[fits] = crawl_gallery(
                    fits=fits,
                    timeout=timeout,
                    retries=retries,
                    workers=workers,
                    max_pages=max_pages,
                )
            records, filter_stats = filter_cache[fits]
            device_stats["filters"][fits] = filter_stats
            for record in records:
                wallpaper_id = str(record.get("id") or "")
                if wallpaper_id:
                    records_by_id.setdefault(wallpaper_id, record)
                    if is_public_candidate(record):
                        ids.add(wallpaper_id)
        memberships[str(device["key"])] = ids
        device_stats["unique"] = len(ids)
        stats[str(device["key"])] = device_stats
    return memberships, stats


def rewrite_hosted_asset_urls(catalog_root: Path) -> tuple[int, int, int]:
    """Rewrite existing catalog rows to the hosted-assets sharded URLs."""
    changed_pages = 0
    changed_thumbnails = 0
    changed_wallpapers = 0
    for path in sorted(catalog_root.rglob("*.json")):
        if path.name == "index.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        page_changed = False
        for item in payload.get("items", []):
            wallpaper_id = str(item.get("id") or "").strip()
            if not wallpaper_id:
                continue
            expected_thumbnail = hosted_thumbnail_url(wallpaper_id)
            if item.get("thumbnail") != expected_thumbnail:
                item["thumbnail"] = expected_thumbnail
                changed_thumbnails += 1
                page_changed = True
            if str(item.get("format") or "") == "needs_conversion":
                expected_url = hosted_wallpaper_url(wallpaper_id)
                if item.get("url") != expected_url:
                    item["url"] = expected_url
                    changed_wallpapers += 1
                    page_changed = True
        if page_changed:
            write_json(path, payload)
            changed_pages += 1
    return changed_pages, changed_thumbnails, changed_wallpapers


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("catalogs"), help="generated catalog directory")
    parser.add_argument(
        "--rewrite-hosted-urls-only",
        action="store_true",
        help="rewrite thumbnail and needs_conversion URLs in the existing catalog tree; no network requests",
    )
    parser.add_argument(
        "--device-json",
        type=Path,
        default=Path("wallpapers.crosspoint-plugin/device.json"),
        help="generated CrossPoint device manifest",
    )
    parser.add_argument("--raw-base", default=DEFAULT_RAW_BASE, help="public raw URL prefix for generated catalogs")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="parallel ReadMe.club requests")
    parser.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="request attempts")
    parser.add_argument("--random-count", type=int, default=DEFAULT_RANDOM_COUNT, help="size of Random Mix")
    parser.add_argument(
        "--random-seed",
        default=date.today().isoformat(),
        help="deterministic seed; defaults to today's date",
    )
    parser.add_argument(
        "--pack-months",
        type=int,
        default=DEFAULT_PACK_MONTHS,
        help="number of recent monthly packs to expose",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="development-only limit applied to each ReadMe.club crawl",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.rewrite_hosted_urls_only:
        changed_pages, changed_thumbnails, changed_wallpapers = rewrite_hosted_asset_urls(args.output)
        print(
            f"Done: rewrote {changed_thumbnails} thumbnail URL(s) and {changed_wallpapers} hosted wallpaper URL(s) "
            f"across {changed_pages} catalog page(s)."
        )
        return 0
    if args.workers < 1 or args.retries < 1 or args.random_count < 1:
        raise SystemExit("workers, retries, and random-count must be positive")

    print("Fetching ReadMe.club wallpaper inventory…", flush=True)
    all_records, all_stats = crawl_gallery(
        fits=None,
        timeout=args.timeout,
        retries=args.retries,
        workers=args.workers,
        max_pages=args.max_pages,
    )
    records_by_id = {str(record["id"]): record for record in all_records if record.get("id")}

    print("Fetching device compatibility memberships…", flush=True)
    memberships, device_stats = build_device_memberships(
        records_by_id=records_by_id,
        timeout=args.timeout,
        retries=args.retries,
        workers=args.workers,
        max_pages=args.max_pages,
    )

    safe_records = [record for record in records_by_id.values() if is_public_candidate(record)]
    excluded_sensitive = len(records_by_id) - len(safe_records)

    months = recent_months(args.pack_months)
    monthly_packs: dict[str, list[dict[str, Any]]] = {}
    for month in months:
        print(f"Fetching monthly pack {month}…", flush=True)
        monthly_packs[month] = build_month_pack(
            month,
            records_by_id,
            timeout=args.timeout,
            retries=args.retries,
        )

    specs = build_catalog_specs(
        safe_records,
        memberships,
        monthly_packs,
        random_count=args.random_count,
        random_seed=args.random_seed,
    )

    if args.output.exists():
        shutil.rmtree(args.output)
    args.output.mkdir(parents=True, exist_ok=True)

    index_entries: list[dict[str, Any]] = []
    for spec in specs:
        records = list(spec["records"])
        pages = write_catalog(args.output, str(spec["key"]), records)
        index_entries.append(
            {
                "key": spec["key"],
                "title": spec["title"],
                "items": len(records),
                "pages": pages,
                "url": raw_catalog_url(args.raw_base, str(spec["key"])),
            }
        )
        print(f"  {spec['title']}: {len(records)} items / {pages} pages", flush=True)

    manifest = build_device_manifest(args.raw_base, specs)
    # Keep the dual-mode manifest compact so static fallback lists have room to grow under the 8 KiB firmware limit.
    write_json(args.device_json, manifest)
    manifest_bytes = args.device_json.stat().st_size
    if manifest_bytes > 8192:
        raise RuntimeError(f"generated device.json is {manifest_bytes} bytes; CrossPoint limit is 8192")

    metadata = {
        "schema": 1,
        "generator_version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": GALLERY_URL,
        "partial": bool(args.max_pages),
        "page_size": PAGE_SIZE,
        "random_seed": args.random_seed,
        "inventory": all_stats,
        "unique_records_after_device_crawls": len(records_by_id),
        "public_candidates": len(safe_records),
        "excluded_sensitive": excluded_sensitive,
        "device_memberships": device_stats,
        "catalogs": index_entries,
    }
    write_json(args.output / "index.json", metadata, pretty=True)

    print(
        f"Done: {len(safe_records)} public wallpapers, {len(specs)} catalogs; "
        f"device.json={manifest_bytes} bytes",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
