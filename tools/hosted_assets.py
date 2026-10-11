#!/usr/bin/env python3
"""Shared hosted-asset paths for the generated CrossPoint wallpaper payload."""

from __future__ import annotations

from pathlib import Path

HOSTED_ASSET_BRANCH = "hosted-assets"
HOSTED_ASSET_BASE_URL = (
    "https://raw.githubusercontent.com/jadehawk/wallpapers.crosspoint-plugin/"
    f"{HOSTED_ASSET_BRANCH}"
)


def asset_shard(wallpaper_id: str) -> str:
    """Return the deterministic two-character shard for a wallpaper ID."""

    value = str(wallpaper_id).strip()
    if len(value) < 2:
        raise ValueError("wallpaper ID must contain at least two characters")
    return value[:2].lower()


def hosted_thumbnail_url(wallpaper_id: str) -> str:
    value = str(wallpaper_id).strip()
    return f"{HOSTED_ASSET_BASE_URL}/thumbs/{asset_shard(value)}/{value}.bmp"


def hosted_wallpaper_url(wallpaper_id: str) -> str:
    value = str(wallpaper_id).strip()
    return f"{HOSTED_ASSET_BASE_URL}/wallpapers/{asset_shard(value)}/{value}.bmp"


def sharded_bmp_path(output_root: Path, wallpaper_id: str) -> Path:
    value = str(wallpaper_id).strip()
    return output_root / asset_shard(value) / f"{value}.bmp"
