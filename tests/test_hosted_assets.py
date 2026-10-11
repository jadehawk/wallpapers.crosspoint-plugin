import tempfile
import unittest
from pathlib import Path

from tools import hosted_assets


class HostedAssetPathTests(unittest.TestCase):
    def test_asset_shard_uses_first_two_characters_lowercase(self) -> None:
        self.assertEqual(hosted_assets.asset_shard("C6a82de5-d681-44ea-99f7-f9805afed042"), "c6")
        self.assertEqual(hosted_assets.asset_shard("convert-me"), "co")

    def test_asset_shard_rejects_short_ids(self) -> None:
        with self.assertRaises(ValueError):
            hosted_assets.asset_shard("x")

    def test_hosted_urls_use_hosted_assets_branch_and_shards(self) -> None:
        wallpaper_id = "c6a82de5-d681-44ea-99f7-f9805afed042"
        self.assertEqual(
            hosted_assets.hosted_thumbnail_url(wallpaper_id),
            "https://raw.githubusercontent.com/jadehawk/wallpapers.crosspoint-plugin/"
            "hosted-assets/thumbs/c6/c6a82de5-d681-44ea-99f7-f9805afed042.bmp",
        )
        self.assertEqual(
            hosted_assets.hosted_wallpaper_url(wallpaper_id),
            "https://raw.githubusercontent.com/jadehawk/wallpapers.crosspoint-plugin/"
            "hosted-assets/wallpapers/c6/c6a82de5-d681-44ea-99f7-f9805afed042.bmp",
        )

    def test_sharded_bmp_path_is_relative_to_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = hosted_assets.sharded_bmp_path(root, "ABcdef")
            self.assertEqual(path, root / "ab" / "ABcdef.bmp")


if __name__ == "__main__":
    unittest.main()
