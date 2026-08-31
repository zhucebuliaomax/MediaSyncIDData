from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from build_release import PROPERTIES, run


class BuildReleaseTest(unittest.TestCase):
    def test_reuses_tsv_and_builds_release_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tsv_dir = root / "tsv"
            output_dir = root / "dist"
            tsv_dir.mkdir()
            for index, property_name in enumerate(PROPERTIES, 1):
                value = "tt0137523" if property_name == "P345" else f"value-{index}"
                extra = '\n<http://www.wikidata.org/entity/Q42>\t"nm0010930"' if property_name == "P345" else ""
                if property_name == "P4529":
                    extra = "\n<http://www.wikidata.org/entity/Q42>\t<http://www.wikidata.org/.well-known/genid/example>"
                (tsv_dir / f"{property_name}.tsv").write_text(
                    f'?item\t?value\n<http://www.wikidata.org/entity/Q190050>\t{json.dumps(value)}{extra}\n'
                )
            args = argparse.Namespace(
                endpoint="https://query.wikidata.org/sparql",
                user_agent="test",
                work_dir=tsv_dir,
                output_dir=output_dir,
                timeout=1,
                retries=0,
                reuse_existing=True,
            )
            manifest = run(args)
            self.assertEqual(manifest["identifier_count"], len(PROPERTIES))
            database = output_dir / "mediasync_ids.sqlite3"
            with sqlite3.connect(database) as connection:
                count = connection.execute("SELECT COUNT(*) FROM identifiers").fetchone()[0]
            self.assertEqual(count, len(PROPERTIES))
            with gzip.open(output_dir / "mediasync_ids.sqlite3.gz", "rb") as stream:
                self.assertEqual(stream.read(16), b"SQLite format 3\x00")
            self.assertTrue((output_dir / "manifest.json").is_file())
            self.assertTrue((output_dir / "SHA256SUMS").is_file())


if __name__ == "__main__":
    unittest.main()
