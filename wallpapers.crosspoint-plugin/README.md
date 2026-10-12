# Wallpaper Downloader

CrossPoint plugin for browsing ReadMe.club wallpapers directly on the reader.

The same plugin works in two modes: baseline CrossPoint firmware uses the embedded catalog picker and standard list UI, while capability-aware firmware automatically uses the live dynamic catalog index plus grid/list thumbnails, preview, settings, and cache features.

## Version

0.1.1

## What it does

The native CrossPoint catalog browser exposes generated ReadMe.club collections including:

- Random Mix
- Latest
- Popular
- X3
- X4 / X4 Pro / X4C
- Picco
- Seeed reTerminal Sticky
- Paper Pro Move
- Custom, Dark, Light, Minimalist, Other, and Pop Culture categories
- Recent monthly curated packs

Each native row shows the wallpaper title plus its category, uploader, and download count. Selecting an entry downloads the wallpaper through ReadMe.club's tracked download endpoint into `/.sleep` on the SD card.

The generated catalog also keeps useful metadata such as thumbnail URL, source device, dimensions, creation time, and source format so a richer WebUI can be added later without changing the catalog source.

## Device compatibility

Device lists follow ReadMe.club's own `fits` filters instead of trying to infer compatibility from dimensions.

ReadMe.club does not currently expose a separate X4C compatibility filter, so the **X4 / X4 Pro / X4C** list uses the X4-family compatibility results for now.

## Sleep-screen setup

Set CrossPoint's sleep-screen mode to **Custom**. Downloaded wallpapers are stored in `/.sleep`, which CrossPoint uses for rotating custom sleep images.

## Catalog source

Wallpaper metadata and downloads come from ReadMe.club. The repository's catalog generator periodically converts the public gallery metadata into small paged JSON files for CrossPoint's native catalog browser.
