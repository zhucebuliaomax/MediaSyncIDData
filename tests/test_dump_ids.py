from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def statement(value: str, rank: str = "normal") -> dict:
    return {
        "rank": rank,
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {"type": "string", "value": value},
        },
    }


class DumpIdsTest(unittest.TestCase):
    def test_builds_truthy_compact_index(self) -> None:
        entities = [
            {
                "id": "Q190050",
                "claims": {
                    "P345": [statement("tt0137523")],
                    "P4529": [statement("old", "deprecated"), statement("1292000")],
                    "P4947": [statement("550")],
                    "P6127": [statement("fight-club")],
                },
            },
            {
                "id": "Q42",
                "claims": {"P345": [statement("nm0010930")]},
            },
            {
                "id": "Q108456738",
                "claims": {
                    "P4983": [statement("wrong"), statement("125988", "preferred")],
                    "P4835": [statement("403245")],
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.json"
            source.write_text("[\n" + ",\n".join(map(json.dumps, entities)) + "\n]\n")
            output = root / "ids.sqlite3"
            subprocess.run(
                [sys.executable, str(ROOT / "dump_ids.py"), str(source), "--output", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            with sqlite3.connect(output) as database:
                rows = database.execute(
                    "SELECT property_id, qid, value FROM identifiers ORDER BY 1, 2, 3"
                ).fetchall()
            self.assertEqual(
                rows,
                [
                    (345, 190050, "tt0137523"),
                    (4529, 190050, "1292000"),
                    (4835, 108456738, "403245"),
                    (4947, 190050, "550"),
                    (4983, 108456738, "125988"),
                    (6127, 190050, "fight-club"),
                ],
            )
            self.assertTrue(output.with_suffix(".sqlite3.manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
