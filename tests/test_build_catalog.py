import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tools import build_catalog as catalog


SAMPLE_WALLPAPER = {
    "id": "12345678-1234-1234-1234-123456789abc",
    "title": "Moon and Small Man",
    "category": "minimalist",
    "is_nsfw": False,
    "target_device": "x4",
    "author_name": "jadehawk",
    "width": 480,
    "height": 800,
    "file_size": 123456,
    "download_count": 1284,
    "created_at": "2026-10-01T12:00:00+00:00",
    "format": "x4_ready",
    "thumbnail_path": "https://example.test/thumbnail.jpg",
}


class GalleryParserTests(unittest.TestCase):
    def test_extracts_wallpapers_data_script(self) -> None:
        payload = {"wallpapers": [SAMPLE_WALLPAPER], "total": 1, "totalPages": 1, "pageSize": 32}
        document = (
            "<html><body><script id=\"wallpapers-data\" type=\"application/json\">"
            + json.dumps(payload)
            + "</script></body></html>"
        )
        parsed = catalog.extract_gallery_data(document)
        self.assertEqual(parsed["total"], 1)
        self.assertEqual(parsed["wallpapers"][0]["id"], SAMPLE_WALLPAPER["id"])

    def test_rejects_missing_wallpaper_data(self) -> None:
        with self.assertRaises(ValueError):
            catalog.extract_gallery_data("<html></html>")


class CatalogItemTests(unittest.TestCase):
    def test_native_row_contains_requested_metadata(self) -> None:
        item = catalog.catalog_item(SAMPLE_WALLPAPER)
        self.assertEqual(item["title"], "Moon and Small Man")
        self.assertEqual(item["id"], SAMPLE_WALLPAPER["id"])
        self.assertIn("Minimalist", item["author"])
        self.assertIn("jadehawk", item["author"])
        self.assertIn("1.3k downloads", item["author"])
        self.assertEqual(
            item["url"],
            "https://www.readme.club/api/wallpapers/"
            "12345678-1234-1234-1234-123456789abc/proxy-download?format=original",
        )
        self.assertEqual(item["thumbnail"], catalog.hosted_thumbnail_url(SAMPLE_WALLPAPER["id"]))

    def test_needs_conversion_uses_hosted_wallpaper_bmp(self) -> None:
        converted = dict(SAMPLE_WALLPAPER, format="needs_conversion")
        item = catalog.catalog_item(converted)
        self.assertEqual(item["url"], catalog.hosted_wallpaper_url(SAMPLE_WALLPAPER["id"]))
        self.assertEqual(item["format"], "needs_conversion")

    def test_sensitive_items_are_not_public_candidates(self) -> None:
        sensitive = dict(SAMPLE_WALLPAPER, is_nsfw=True)
        self.assertFalse(catalog.is_public_candidate(sensitive))
        self.assertTrue(catalog.is_public_candidate(SAMPLE_WALLPAPER))


class HostedAssetUrlRewriteTests(unittest.TestCase):
    def test_rewrites_all_thumbnails_and_only_conversion_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = root / "latest" / "1.json"
            page.parent.mkdir(parents=True)
            page.write_text(
                json.dumps(
                    {
                        "items": [
                            {"id": "convert-me", "format": "needs_conversion", "url": "https://old.example/original"},
                            {"id": "already-ready", "format": "x4_ready", "url": "https://keep.example/original"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "index.json").write_text(json.dumps({"catalogs": []}, indent=2), encoding="utf-8")

            changed = catalog.rewrite_hosted_asset_urls(root)
            payload = json.loads(page.read_text(encoding="utf-8"))

            self.assertEqual(changed, (1, 2, 1))
            self.assertEqual(payload["items"][0]["url"], catalog.hosted_wallpaper_url("convert-me"))
            self.assertEqual(payload["items"][1]["url"], "https://keep.example/original")
            self.assertEqual(payload["items"][0]["thumbnail"], catalog.hosted_thumbnail_url("convert-me"))
            self.assertEqual(payload["items"][1]["thumbnail"], catalog.hosted_thumbnail_url("already-ready"))
            self.assertEqual(catalog.rewrite_hosted_asset_urls(root), (0, 0, 0))


class PaginationTests(unittest.TestCase):
    def test_non_final_page_has_one_lookahead_item(self) -> None:
        items = [{"id": str(index)} for index in range(34)]
        pages = catalog.static_pages(items, page_size=16)
        self.assertEqual(len(pages), 3)
        self.assertEqual(len(pages[0]), 17)
        self.assertEqual(len(pages[1]), 17)
        self.assertEqual(len(pages[2]), 2)
        self.assertEqual(pages[0][-1]["id"], "16")
        self.assertEqual(pages[1][0]["id"], "16")
        self.assertEqual(pages[1][-1]["id"], "32")
        self.assertEqual(pages[2][0]["id"], "32")

    def test_empty_catalog_still_has_page_one(self) -> None:
        self.assertEqual(catalog.static_pages([], page_size=16), [[]])

    def test_write_catalog_uses_items_wrapper(self) -> None:
        records = [dict(SAMPLE_WALLPAPER, id=f"{index:08d}-1234-1234-1234-123456789abc") for index in range(17)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            page_count = catalog.write_catalog(root, "latest", records)
            self.assertEqual(page_count, 2)
            first = json.loads((root / "latest" / "1.json").read_text(encoding="utf-8"))
            second = json.loads((root / "latest" / "2.json").read_text(encoding="utf-8"))
            self.assertEqual(len(first["items"]), 17)
            self.assertEqual(len(second["items"]), 1)


class PackTests(unittest.TestCase):
    def test_pop_culture_pack_slug_matches_live_site_route(self) -> None:
        self.assertEqual(catalog.CATEGORY_PACK_SLUGS["pop_culture"], "pop_culture")

    def test_extracts_unique_wallpaper_id_prefixes(self) -> None:
        document = """
        <a href="/wallpapers/moon-and-small-man-12345678">One</a>
        <a href="https://www.readme.club/wallpapers/moon-and-small-man-12345678">Duplicate</a>
        <a href="/wallpapers/great-wave-deadbeef?ref=pack">Two</a>
        """
        self.assertEqual(catalog.extract_pack_prefixes(document), ["12345678", "deadbeef"])

    def test_recent_months_crosses_year_boundary(self) -> None:
        self.assertEqual(
            catalog.recent_months(3, date(2027, 1, 15)),
            ["2027-01", "2026-12", "2026-11"],
        )


class DeviceManifestTests(unittest.TestCase):
    def test_manifest_keeps_static_lists_and_advertises_dynamic_enhancements(self) -> None:
        specs = [
            {"key": "random", "title": "Random Mix", "records": []},
            {"key": "latest", "title": "Latest", "records": []},
            {"key": "devices/x3", "title": "X3", "records": []},
        ]
        manifest = catalog.build_device_manifest("https://example.test/catalogs", specs)
        self.assertEqual(manifest["title"], "Wallpaper Downloader v0.1.3")
        self.assertEqual(manifest["browse"]["page_size"], 16)
        self.assertEqual(manifest["browse"]["fields"]["url"], "url")
        self.assertEqual(manifest["browse"]["fields"]["thumbnail"], "thumbnail")
        self.assertEqual(manifest["browse"]["lists_url"], "https://example.test/catalogs/index.json")
        self.assertEqual(manifest["browse"]["view"], "{cfg.layout}")
        self.assertEqual(manifest["browse"]["hide_pagination"], "{cfg.hide_pagination}")
        hide_pagination = next(setting for setting in manifest["settings"] if setting["key"] == "hide_pagination")
        self.assertEqual(hide_pagination["visible_if"], {"device_capability": "touch"})
        self.assertEqual(manifest["cache"]["refresh"], "{cfg.cache_refresh}")
        self.assertEqual(manifest["download"]["url"], "{url}")
        self.assertEqual(manifest["download"]["preview_on_select"], "{cfg.allow_image_preview}")
        self.assertEqual(manifest["download"]["dest_dir"], "/.sleep")
        self.assertEqual(manifest["version"], "0.1.3")
        self.assertEqual(len(manifest["browse"]["lists"]), 3)
        self.assertEqual(
            manifest["browse"]["lists"][0]["url"],
            "https://example.test/catalogs/random/{page}.json",
        )

    def test_full_current_list_set_stays_below_crosspoint_manifest_limit(self) -> None:
        specs = [
            {"key": "random", "title": "Random Mix", "records": []},
            {"key": "latest", "title": "Latest", "records": []},
            {"key": "popular", "title": "Popular", "records": []},
        ]
        specs.extend({"key": d["key"], "title": d["title"], "records": []} for d in catalog.DEVICE_CATALOGS)
        specs.extend(
            {"key": f"categories/{key}", "title": f"Category · {label}", "records": []}
            for key, label in catalog.CATEGORY_LABELS.items()
        )
        specs.extend(
            {"key": f"packs/{month}", "title": f"Monthly Pack · {month}", "records": []}
            for month in ("2026-10", "2026-09", "2026-08")
        )
        encoded = json.dumps(catalog.build_device_manifest(catalog.DEFAULT_RAW_BASE, specs), ensure_ascii=False).encode()
        self.assertLessEqual(len(encoded), 8192)


if __name__ == "__main__":
    unittest.main()
