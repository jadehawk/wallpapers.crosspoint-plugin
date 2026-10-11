import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import build_hosted_wallpapers as hosted


class CatalogSelectionTests(unittest.TestCase):
    def test_selects_unique_needs_conversion_ids_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "latest").mkdir(parents=True)
            (root / "popular").mkdir(parents=True)
            (root / "index.json").write_text(
                json.dumps(
                    {
                        "catalogs": [
                            {"key": "latest", "pages": 1},
                            {"key": "popular", "pages": 1},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "latest" / "1.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {"id": "convert-me", "format": "needs_conversion"},
                            {"id": "ready", "format": "x4_ready"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "popular" / "1.json").write_text(
                json.dumps({"items": [{"id": "convert-me", "format": "needs_conversion"}]}),
                encoding="utf-8",
            )

            self.assertEqual(hosted.wallpaper_ids(root), ["convert-me"])


class BmpTests(unittest.TestCase):
    def test_conversion_produces_crosspoint_native_4bit_bmp(self) -> None:
        source = Image.new("RGB", (600, 600))
        for y in range(source.height):
            value = int(round(255 * y / max(1, source.height - 1)))
            for x in range(source.width):
                source.putpixel((x, y), (value, value, value))
        encoded = io.BytesIO()
        source.save(encoded, format="PNG")

        bmp = hosted.image_to_device_bmp(encoded.getvalue())

        self.assertEqual(bmp[:2], b"BM")
        self.assertEqual(struct.unpack_from("<I", bmp, 10)[0], 70)
        self.assertEqual(struct.unpack_from("<I", bmp, 14)[0], 40)
        self.assertEqual(struct.unpack_from("<ii", bmp, 18), (480, -800))
        self.assertEqual(struct.unpack_from("<HH", bmp, 26), (1, 4))
        self.assertEqual(struct.unpack_from("<I", bmp, 30)[0], 0)
        self.assertEqual(struct.unpack_from("<I", bmp, 46)[0], 4)
        self.assertEqual(bmp[54:70], bytes((0, 0, 0, 0, 85, 85, 85, 0, 170, 170, 170, 0, 255, 255, 255, 0)))
        self.assertEqual(len(bmp), 192070)

        with Image.open(io.BytesIO(bmp)) as decoded:
            self.assertEqual(decoded.size, (480, 800))
            self.assertEqual(decoded.mode, "P")
            used = set(decoded.getdata())
            self.assertTrue(used.issubset({0, 1, 2, 3}))
            self.assertGreaterEqual(len(used), 3)

    def test_valid_bmp_checks_full_crosspoint_contract(self) -> None:
        source = Image.new("L", (480, 800), 128)
        encoded = io.BytesIO()
        source.save(encoded, format="PNG")
        bmp = hosted.image_to_device_bmp(encoded.getvalue())

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ready.bmp"
            path.write_bytes(bmp)
            self.assertTrue(hosted.valid_bmp(path))

            malformed = bytearray(bmp)
            struct.pack_into("<H", malformed, 28, 8)
            path.write_bytes(malformed)
            self.assertFalse(hosted.valid_bmp(path))


if __name__ == "__main__":
    unittest.main()
