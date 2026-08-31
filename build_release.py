#!/usr/bin/env python3
"""Download selected Wikidata IDs and build release-ready SQLite artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Iterator


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
QID_RE = re.compile(rb"<http://www\.wikidata\.org/entity/Q([1-9]\d*)>")
IMDB_TITLE_RE = re.compile(r"tt\d+")
HEADER = b"?item\t?value"
DEFAULT_ENDPOINT = "https://query.wikidata.org/sparql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--user-agent",
        default="MediaSyncIDData/1.0 (+https://github.com/)",
        help="Wikimedia requires a descriptive User-Agent with contact information",
    )
    parser.add_argument("--work-dir", type=Path, default=Path("build/tsv"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="reuse valid TSV files instead of downloading them",
    )
    return parser.parse_args()


def query_for(property_name: str) -> str:
    return f"SELECT ?item ?value WHERE {{ ?item wdt:{property_name} ?value. }}"


def retry_delay(error: BaseException, attempt: int) -> float:
    if isinstance(error, urllib.error.HTTPError):
        retry_after = error.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 120.0)
    return min(float(2**attempt), 30.0)


def download(
    property_name: str,
    destination: Path,
    endpoint: str,
    user_agent: str,
    timeout: int,
    retries: int,
) -> None:
    parameters = urllib.parse.urlencode({"query": query_for(property_name)})
    url = f"{endpoint}?{parameters}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/tab-separated-values",
            "User-Agent": user_agent,
        },
    )
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(retries + 1):
        temporary.unlink(missing_ok=True)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP status {response.status}")
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            validate_tsv(temporary, property_name)
            os.replace(temporary, destination)
            return
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            temporary.unlink(missing_ok=True)
            if attempt >= retries:
                raise RuntimeError(
                    f"failed to download {property_name} after {retries + 1} attempts"
                ) from error
            delay = retry_delay(error, attempt)
            print(
                f"{property_name}: {error}; retrying in {delay:g}s",
                file=sys.stderr,
            )
            time.sleep(delay)


def decode_value(raw: bytes) -> str | None:
    # Wikidata's truthy predicate can expose a generated URI for a statement
    # whose value is unknown. It is not an external identifier and is skipped.
    if raw.startswith(b"<http://www.wikidata.org/.well-known/genid/") and raw.endswith(b">"):
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid SPARQL TSV string") from error
    if not isinstance(value, str) or not value:
        raise ValueError("identifier value must be a non-empty string")
    return value


def tsv_records(path: Path, property_name: str) -> Iterator[tuple[int, int, str]]:
    property_id = int(property_name[1:])
    with path.open("rb") as stream:
        if stream.readline().rstrip(b"\r\n") != HEADER:
            raise ValueError(f"{path}: invalid TSV header")
        for line_number, line in enumerate(stream, 2):
            columns = line.rstrip(b"\r\n").split(b"\t", 1)
            if len(columns) != 2:
                raise ValueError(f"{path}:{line_number}: expected two columns")
            qid_match = QID_RE.fullmatch(columns[0])
            if not qid_match:
                raise ValueError(f"{path}:{line_number}: invalid Wikidata item")
            value = decode_value(columns[1])
            if value is None:
                continue
            if property_name == "P345" and not IMDB_TITLE_RE.fullmatch(value):
                continue
            yield property_id, int(qid_match.group(1)), value


def validate_tsv(path: Path, property_name: str) -> int:
    count = 0
    for _ in tsv_records(path, property_name):
        count += 1
    if count == 0:
        raise ValueError(f"{path}: no usable identifiers")
    return count


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


def build_database(tsv_dir: Path, output: Path) -> tuple[int, dict[str, int]]:
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.unlink(missing_ok=True)
    counts: dict[str, int] = {}
    try:
        with closing(sqlite3.connect(temporary)) as database:
            database.executescript(SCHEMA)
            for property_name in PROPERTIES:
                path = tsv_dir / f"{property_name}.tsv"
                before = database.total_changes
                database.executemany(
                    "INSERT OR IGNORE INTO identifiers VALUES (?, ?, ?)",
                    tsv_records(path, property_name),
                )
                counts[property_name] = database.total_changes - before
                database.commit()
            database.execute(
                "CREATE INDEX identifiers_by_qid ON identifiers(qid, property_id, value)"
            )
            total = sum(counts.values())
            metadata = {
                "schema_version": "1",
                "source": "Wikidata Query Service truthy statements",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "identifier_count": str(total),
                "properties": ",".join(PROPERTIES),
            }
            database.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
            database.commit()
            database.execute("PRAGMA optimize")
            database.execute("VACUUM")
            result = database.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {result}")
        os.replace(temporary, output)
        return total, counts
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compress(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as input_stream, temporary.open("wb") as raw_output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=0) as output:
            shutil.copyfileobj(input_stream, output, length=1024 * 1024)
    os.replace(temporary, destination)


def run(args: argparse.Namespace) -> dict[str, object]:
    if args.timeout <= 0 or args.retries < 0:
        raise ValueError("timeout must be positive and retries cannot be negative")
    work_dir = args.work_dir.resolve()
    output_dir = args.output_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    for property_name in PROPERTIES:
        destination = work_dir / f"{property_name}.tsv"
        if args.reuse_existing and destination.exists():
            count = validate_tsv(destination, property_name)
            print(f"{property_name}: reusing {count:,} usable rows", file=sys.stderr)
            continue
        print(f"{property_name}: downloading", file=sys.stderr)
        download(
            property_name,
            destination,
            args.endpoint,
            args.user_agent,
            args.timeout,
            args.retries,
        )
        count = validate_tsv(destination, property_name)
        print(f"{property_name}: downloaded {count:,} usable rows", file=sys.stderr)

    database = output_dir / "mediasync_ids.sqlite3"
    archive = output_dir / "mediasync_ids.sqlite3.gz"
    total, counts = build_database(work_dir, database)
    compress(database, archive)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest: dict[str, object] = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source": args.endpoint,
        "identifier_count": total,
        "properties": {
            name: {
                "provider": details[0],
                "scope": details[1],
                "identifier_count": counts[name],
            }
            for name, details in PROPERTIES.items()
        },
        "database": {
            "filename": database.name,
            "size": database.stat().st_size,
            "sha256": sha256(database),
        },
        "archive": {
            "filename": archive.name,
            "size": archive.stat().st_size,
            "sha256": sha256(archive),
            "compression": "gzip",
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (output_dir / "SHA256SUMS").write_text(
        f"{manifest['database']['sha256']}  {database.name}\n"
        f"{manifest['archive']['sha256']}  {archive.name}\n"
        f"{sha256(manifest_path)}  {manifest_path.name}\n"
    )
    return manifest


def main() -> int:
    manifest = run(parse_args())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
