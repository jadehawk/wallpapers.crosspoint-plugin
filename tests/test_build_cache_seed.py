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
        url = "https://api.readme.club/storage/v1/object/public/wallpapers-public/wallpapers/abc/thumbnail.jpg"
        key = f"image-v1|200x300|1|GET|{url}|"
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
                json.dumps({"items": [{"id": "A"}, {"id": "B"}, {"id": "C"}]}), encoding="utf-8"
            )
            (root / "latest" / "2.json").write_text(
                json.dumps({"items": [{"id": "C"}, {"id": "D"}]}), encoding="utf-8"
            )
            # B overlaps Latest; E is unique to this catalog.
            (root / "categories" / "dark" / "1.json").write_text(
                json.dumps({"items": [{"id": "B"}, {"id": "E"}, {"id": "F"}]}), encoding="utf-8"
            )

            items, stats = seed.select_seed_items(root, per_catalog=3)
            by_id = {item.wallpaper_id: item for item in items}

            self.assertEqual(set(by_id), {"A", "B", "C", "E", "F"})
            self.assertEqual(by_id["B"].catalogs, ("Category · Dark", "Latest"))
            self.assertEqual(stats[0]["selected"], 3)
            self.assertEqual(stats[1]["selected"], 3)


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
