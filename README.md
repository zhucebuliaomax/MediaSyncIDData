# MediaSync ID Data

Builds a compact local SQLite index containing only the external identifiers
MediaSync uses. No titles, descriptions, images, labels, or people are stored.

## Included Wikidata properties

| Property | Provider | Scope |
| --- | --- | --- |
| P345 | IMDb (`tt...` titles only) | work |
| P4529 | Douban | work |
| P4947 | TMDB | movie |
| P4983 | TMDB | show |
| P12558 | TMDB | season |
| P12559 | TMDB | episode |
| P12196 | TheTVDB | movie |
| P4835 | TheTVDB | show |
| P12397 | TheTVDB | season |
| P7043 | TheTVDB | episode |
| P6127 | Letterboxd | movie |

## Monthly download and release build

Download all selected properties from Wikidata Query Service, validate them,
filter IMDb to `tt...` titles, build SQLite, compress it, and generate checksums:

```bash
python3 build_release.py
```

Outputs:

```text
dist/mediasync_ids.sqlite3
dist/mediasync_ids.sqlite3.gz
dist/manifest.json
dist/SHA256SUMS
```

Valid TSV files can be reused for local testing:

```bash
python3 build_release.py --work-dir data/tsv --reuse-existing
```

The GitHub Actions workflow in `.github/workflows/monthly-release.yml` runs at
03:23 UTC on the first day of every month. It can also be started manually. The
workflow tests the builder, downloads a fresh snapshot, and creates or updates a
release named `data-YYYY-MM` using the repository's built-in `GITHUB_TOKEN`.

No API token or repository secret is required. The workflow needs repository
`contents: write` permission, which is declared explicitly in the workflow.

## Database format

The SQLite database stores property and Q identifiers as integers to keep the
index small. For example, `property_id=4947` means `P4947`, and `qid=190050`
means `Q190050`.

Lookup an entity by a known ID:

```sql
SELECT qid FROM identifiers
WHERE property_id = 4947 AND value = '550';
```

Return every retained ID for that entity:

```sql
SELECT property_id, value FROM identifiers
WHERE qid = 190050 ORDER BY property_id, value;
```

## Test

```bash
python3 -m unittest discover -s tests -v
```

Wikidata structured data is available under CC0.
