#!/usr/bin/env python3
"""Build a compact MediaSync identifier index from a Wikidata JSON dump."""

from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import BinaryIO, Iterator


PROPERTIES = {
    "P345": ("imdb", "work"),
    "P4529": ("douban", "work"),
    "P4947": ("tmdb", "movie"),
    "P4983": ("tmdb", "show"),
    "P12558": ("tmdb", "season"),
    "P12559": ("tmdb", "episode"),
    "P12196": ("tvdb", "movie"),
    "P4835": ("tvdb", "show"),
    "P12397": ("tvdb", "season"),
    "P7043": ("tvdb", "episode"),
    "P6127": ("letterboxd", "movie"),
}
IMDB_TITLE_RE = re.compile(r"tt\d+")
QID_RE = re.compile(r"Q([1-9]\d*)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Wikidata .json, .json.gz, or .json.bz2 dump")
    parser.add_argument("--output", type=Path, default=Path("mediasync_ids.sqlite3"))
    parser.add_argument("--manifest", type=Path, help="manifest path; defaults beside the database")
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--progress-every", type=int, default=1_000_000)
    return parser.parse_args()


def open_dump(path: Path) -> BinaryIO:
    if path.suffix == ".bz2":
        return bz2.open(path, "rb")
    if path.suffix == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def entities(stream: BinaryIO) -> Iterator[dict]:
    """Read the Wikidata JSON array one entity at a time."""
    for raw_line in stream:
        line = raw_line.strip()
        if not line or line in {b"[", b"]"}:
            continue
        if line.endswith(b","):
            line = line[:-1]
        if line:
            yield json.loads(line)


def statement_value(statement: dict) -> str | None:
    mainsnak = statement.get("mainsnak") or {}
    if mainsnak.get("snaktype") != "value":
        return None
    datavalue = mainsnak.get("datavalue") or {}
    value = datavalue.get("value")
    if isinstance(value, str) and value:
        return value
    return None


def best_values(statements: list[dict]) -> Iterator[str]:
    usable = [item for item in statements if item.get("rank") != "deprecated"]
    preferred = [item for item in usable if item.get("rank") == "preferred"]
    for statement in preferred or usable:
        value = statement_value(statement)
        if value is not None:
            yield value


def records(entity: dict) -> Iterator[tuple[int, int, str]]:
    match = QID_RE.fullmatch(str(entity.get("id") or ""))
    if not match:
        return
    qid = int(match.group(1))
    claims = entity.get("claims") or {}
    for property_name in PROPERTIES:
        for value in best_values(claims.get(property_name) or []):
            if property_name == "P345" and not IMDB_TITLE_RE.fullmatch(value):
                continue
            yield int(property_name[1:]), qid, value


SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
PRAGMA temp_store=MEMORY;
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE identifiers (
    property_id INTEGER NOT NULL,
    qid INTEGER NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (property_id, value, qid)
) WITHOUT ROWID;
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args: argparse.Namespace) -> dict[str, object]:
    if args.batch_size <= 0 or args.progress_every <= 0:
        raise ValueError("batch-size and progress-every must be positive")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    started = time.monotonic()
    entity_count = 0
    record_count = 0
    batch: list[tuple[int, int, str]] = []

    try:
        with closing(sqlite3.connect(temporary)) as database:
            database.executescript(SCHEMA)
            with closing(open_dump(args.input)) as stream:
                for entity in entities(stream):
                    entity_count += 1
                    for record in records(entity):
                        batch.append(record)
                    if len(batch) >= args.batch_size:
                        before = database.total_changes
                        database.executemany(
                            "INSERT OR IGNORE INTO identifiers VALUES (?, ?, ?)", batch
                        )
                        record_count += database.total_changes - before
                        database.commit()
                        batch.clear()
                    if entity_count % args.progress_every == 0:
                        elapsed = time.monotonic() - started
                        print(
                            f"entities={entity_count:,} ids={record_count:,} elapsed={elapsed:.1f}s",
                            file=sys.stderr,
                        )
            if batch:
                before = database.total_changes
                database.executemany(
                    "INSERT OR IGNORE INTO identifiers VALUES (?, ?, ?)", batch
                )
                record_count += database.total_changes - before
            database.execute(
                "CREATE INDEX identifiers_by_qid ON identifiers(qid, property_id, value)"
            )
            metadata = {
                "schema_version": "1",
                "source": "Wikidata JSON dump",
                "source_file": args.input.name,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "entity_count": str(entity_count),
                "identifier_count": str(record_count),
                "properties": ",".join(PROPERTIES),
            }
            database.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
            database.commit()
            database.execute("PRAGMA optimize")
            database.execute("VACUUM")
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    result: dict[str, object] = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_file": args.input.name,
        "database_file": output.name,
        "database_size": output.stat().st_size,
        "database_sha256": sha256(output),
        "entity_count": entity_count,
        "identifier_count": record_count,
        "properties": {
            key: {"provider": value[0], "scope": value[1]}
            for key, value in PROPERTIES.items()
        },
    }
    manifest = args.manifest or output.with_suffix(output.suffix + ".manifest.json")
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> int:
    args = parse_args()
    result = build(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
