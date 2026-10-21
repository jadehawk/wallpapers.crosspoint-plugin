import hashlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import build_cache_seed as seed


class CacheKeyTests(unittest.TestCase):
    def test_cache_filename_matches_firmware_key_contract(self) -> None:
        url = seed.hosted_thumbnail_url("abc")
        key = f"image-v1|200x300|0|GET|{url}|"
        expected = f"i_{hashlib.md5(key.encode('utf-8')).hexdigest()}.bmp"
        self.assertEqual(seed.cache_filename(url), expected)


class SelectionTests(unittest.TestCase):
    def test_selects_first_unique_items_per_catalog_and_globally_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalogs = [
                {"key": "latest", "title": "Latest", "items": 4, "pages": 2},
                {"key": "categories/dark", "title": "Category · Dark", "items": 3, "pages": 1},
            ]
            (root / "index.json").write_text(json.dumps({"catalogs": catalogs}), encoding="utf-8")
            (root / "latest").mkdir(parents=True)
            (root / "categories" / "dark").mkdir(parents=True)

            # Item C is the static-page look-ahead repeated at the start of page 2.
            (root / "latest" / "1.json").write_text(
                json.dumps({"items": [{"id": "AA"}, {"id": "BB"}, {"id": "CC"}]}), encoding="utf-8"
            )
            (root / "latest" / "2.json").write_text(
                json.dumps({"items": [{"id": "CC"}, {"id": "DD"}]}), encoding="utf-8"
            )
            # B overlaps Latest; E is unique to this catalog.
            (root / "categories" / "dark" / "1.json").write_text(
                json.dumps({"items": [{"id": "BB"}, {"id": "EE"}, {"id": "FF"}]}), encoding="utf-8"
            )

            items, stats = seed.select_seed_items(root, per_catalog=3)
            by_id = {item.wallpaper_id: item for item in items}

            self.assertEqual(set(by_id), {"AA", "BB", "CC", "EE", "FF"})
            self.assertEqual(by_id["BB"].catalogs, ("Category · Dark", "Latest"))
            self.assertEqual(stats[0]["selected"], 3)
            self.assertEqual(stats[1]["selected"], 3)


class ReuseTests(unittest.TestCase):
    def test_small_seed_reuses_large_seed_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "large"
            output = root / "small"
            source.mkdir()
            output.mkdir()
            item = seed.SeedItem("abc", seed.hosted_thumbnail_url("abc"), ("Latest",))
            data = b"BM" + bytes(64)
            (source / seed.cache_filename(item.thumbnail_url)).write_bytes(data)
            from unittest import mock

            with mock.patch.object(seed, "request_bytes", side_effect=AssertionError("network access")):
                image_id, size = seed.build_one(item, output, timeout=1, retries=1, source_cache=source)

            self.assertEqual((image_id, size), ("abc", len(data)))
            self.assertEqual((output / seed.cache_filename(item.thumbnail_url)).read_bytes(), data)


class PageSeedTests(unittest.TestCase):
    def test_page_filename_matches_firmware_key_including_default_list(self) -> None:
        browse = {
            "method": "GET", "page_size": 16, "items": "items",
            "fields": {"title": "title", "author": "author", "id": "id", "url": "url"},
        }
        url = "https://example.test/catalog/latest/{page}.json"
        expected_key = "page-v1|GET|https://example.test/catalog/latest/2.json||-1|16|items|title|author|id|url|"
        expected = "p_" + hashlib.md5(expected_key.encode("utf-8")).hexdigest() + ".json"
        self.assertEqual(seed.page_cache_filename(url, 2, -1, browse, {}), expected)

    def test_only_complete_pages_are_seeded_with_matching_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalogs = root / "catalogs"
            latest = catalogs / "latest"
            latest.mkdir(parents=True)
            url = "https://example.test/catalog/latest/{page}.json"
            (catalogs / "index.json").write_text(json.dumps({"catalogs": [
                {"key": "latest", "url": url, "title": "Latest", "items": 3, "pages": 2}
            ]}), encoding="utf-8")
            def item(wallpaper_id: str) -> dict[str, str]:
                return {"id": wallpaper_id, "thumbnail": seed.hosted_thumbnail_url(wallpaper_id)}
            first = json.dumps({"items": [item("aa"), item("bb")]})
            (latest / "1.json").write_text(first, encoding="utf-8")
            (latest / "2.json").write_text(json.dumps({"items": [item("bb"), item("cc")]}), encoding="utf-8")
            manifest = {
                "browse": {
                    "url": url, "method": "GET", "page_size": 16, "items": "items",
                    "fields": {"title": "title", "author": "author", "id": "id", "url": "url"},
                    "lists": [{"url": "https://example.test/other/{page}.json"}, {"url": url}],
                },
                "cache": {"enabled": True},
            }
            device = root / "device.json"
            device.write_text(json.dumps(manifest), encoding="utf-8")
            cache_dir = root / ".cache"
            cache_dir.mkdir()
            pages, size, stats = seed.seed_catalog_pages(
                catalogs, cache_dir, {"aa", "bb"}, device, captured_at=1800000000
            )
            self.assertEqual(pages, 3)  # dynamic 0, static 1, and default -1
            self.assertEqual(stats[0]["complete_pages"], 1)
            self.assertGreater(size, 3 * len(first))
            for index in (0, 1, -1):
                filename = seed.page_cache_filename(url, 1, index, manifest["browse"], manifest["cache"])
                self.assertEqual((cache_dir / filename).read_text(encoding="utf-8"), first)
                self.assertEqual(
                    json.loads((cache_dir / filename).with_suffix(".meta").read_text(encoding="utf-8")),
                    {"captured_at": 1800000000},
                )
                next_name = seed.page_cache_filename(url, 2, index, manifest["browse"], manifest["cache"])
                self.assertFalse((cache_dir / next_name).exists())

    def test_seed_zip_validator_checks_page_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / ".cache"
            cache.mkdir()
            (cache / "i_abc.bmp").write_bytes(b"BM" + bytes(64))
            (cache / "p_def.json").write_text('{"items":[]}', encoding="utf-8")
            (cache / "p_def.meta").write_text('{"captured_at":1800000000}', encoding="utf-8")
            summary = {"built_images": 1, "built_pages": 1}
            summary_path = root / "summary.json"
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            zip_path = root / "seed.zip"
            seed.write_seed_zip(root, zip_path, summary_path)
            seed.verify_seed_zip(zip_path, summary)
            (cache / "p_def.meta").unlink()
            seed.write_seed_zip(root, zip_path, summary_path)
            with self.assertRaises(ValueError):
                seed.verify_seed_zip(zip_path, summary)


class BmpTests(unittest.TestCase):
    def test_thumbnail_conversion_produces_top_down_one_bit_bmp(self) -> None:
        source = Image.new("L", (120, 240), 180)
        encoded = io.BytesIO()
        source.save(encoded, format="JPEG", quality=90)

        bmp = seed.thumbnail_to_bmp(encoded.getvalue())
        self.assertEqual(bmp[:2], b"BM")
        self.assertEqual(struct.unpack_from("<I", bmp, 10)[0], 62)
        width, height = struct.unpack_from("<ii", bmp, 18)
        bits_per_pixel = struct.unpack_from("<H", bmp, 28)[0]

        # Scale-to-cover 200x300 mirrors the firmware converter: 120x240 -> 200x400.
        self.assertEqual((width, height), (200, -400))
        self.assertEqual(bits_per_pixel, 1)
        row_bytes = ((width + 31) // 32) * 4
        self.assertEqual(len(bmp), 62 + row_bytes * abs(height))


if __name__ == "__main__":
    unittest.main()
