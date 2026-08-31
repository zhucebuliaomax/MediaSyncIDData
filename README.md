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

## Build

Download the weekly Wikidata JSON dump from:

<https://dumps.wikimedia.org/wikidatawiki/entities/>

Then run:

```bash
python3 dump_ids.py latest-all.json.bz2 --output mediasync_ids.sqlite3
```

The build streams the compressed dump, applies Wikidata's truthy rank behavior,
and atomically creates:

```text
mediasync_ids.sqlite3
mediasync_ids.sqlite3.manifest.json
```

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
