# Wallpaper Downloader

Browse ReadMe.club wallpaper catalogs directly on the reader and save sleep-screen images without moving files manually.

## Features

- Browse curated wallpaper catalogs and download images into `/.sleep`.
- Use live catalog discovery on firmware that supports dynamic plugin lists.
- Show thumbnails, grid/list layouts, image preview, and SAVED indicators on capable firmware.
- Configure layout, preview, pagination, highlighting, and cache behavior where those capabilities are available.
- Reuse cached thumbnails and images to reduce repeated network traffic.

## Requirements

- The plugin system must be enabled.
- Wi-Fi is required when browsing or downloading content that is not already cached.

## Notes

The same package supports baseline and capability-aware firmware. Baseline firmware uses the embedded release-time catalog navigation, while enhanced firmware can follow the live catalog index and expose richer image and touch controls.
