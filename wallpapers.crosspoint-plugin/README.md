# Wallpaper Downloader

CrossPoint plugin for browsing ReadMe.club wallpapers directly on the reader.

## Version

0.1.4

## Install with Plugin Hub

Plugin Hub is the preferred installation and update path.

1. Enable the device plugin system if it is not already enabled.
2. Open **Plugins** and launch **Plugin Hub**.
3. Open the Community catalog and refresh it if needed.
4. Find **Wallpaper Downloader** and choose **Install** or **Update**.
5. Open Wallpaper Downloader from the device Plugins UI after installation.

The version is supplied by the plugin manifest, not in the plugin title.

The same Plugin Hub package works on both baseline and capability-aware firmware; there is no separate enhanced plugin build.

## Dual-mode compatibility

- **Baseline plugin firmware:** uses the embedded catalog picker and standard native list UI. Browse the release-time catalog lists, page through wallpapers, and download selected images directly into `/.sleep`.
- **Capability-aware/enhanced firmware:** the same package automatically uses the live dynamic catalog index and enables thumbnail-backed grid/list layouts, configurable grid dimensions and row highlighting, image preview, SAVED indicators, plugin settings, and progressive image caching.
- **Touch-capable enhanced devices:** expose the touch-only **Hide pagination buttons** setting. With pagination hidden, swipe **up** moves to the next visible batch and swipe **down** moves to the previous visible batch; crossing a catalog-page boundary advances to Next or Previous. Touch preview provides on-screen Close/Save controls, while physical-button behavior is unchanged.
- Enhanced-only fields are declarative capabilities. Firmware that does not support them ignores them and continues using the baseline behavior from the same package.

## What it does

The native catalog exposes generated ReadMe.club collections including:

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

Each catalog item retains the wallpaper title, category, uploader, download count, source-device metadata, dimensions, thumbnail URL, source format, and tracked download URL. Selecting an entry downloads the wallpaper into `/.sleep` on the SD card.

## Device compatibility

Device lists follow ReadMe.club's own `fits` filters instead of inferring compatibility from dimensions. ReadMe.club does not currently expose a separate X4C compatibility filter, so the **X4 / X4 Pro / X4C** list uses the X4-family compatibility results.

## Sleep-screen setup

Set CrossPoint's sleep-screen mode to **Custom**. Downloaded wallpapers are stored in `/.sleep`, which CrossPoint uses for rotating custom sleep images.

## Catalog source

Wallpaper metadata and downloads come from ReadMe.club. The repository generator periodically converts the public gallery metadata into small paged JSON files. Baseline firmware uses the embedded release-time catalog index, while capability-aware firmware can consume the live index so category changes can appear without a plugin release.

## Cache seed sizes

Where supported, open **Settings → Cache → Cache seed size** to choose **Small** (default), **Medium**, or **Large**, then select **Download seed**. The picker warns that larger seeds may take several minutes to install. The archives include matching thumbnails and catalog pages; their exact image/page counts vary with catalog updates. Firmware without these cache capabilities continues using the standard browsing/download experience.
