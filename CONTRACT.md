# Output contract

What artemis-radar publishes, for map and 3D clients. This describes schema version **1** (processing version `2026.09.27`). Every statement here is checked against the real samples in `fixtures/` (KICT 2024, and the 2013 Moore tornado from KTLX) by `tests/test_fixtures.py`, except where marked *not verified*.

## Access

Read-only HTTP from the web container (default `127.0.0.1:8096`), no authentication and no CORS. Keep it on a private network.

```text
GET /healthz                       HTTP liveness only ("ok"); says nothing about data freshness
GET /index.json                    configured sites
GET /{SITE}/index.json             retained cycles for one site
GET /{SITE}/{ID}/{FILENAME}        images, numeric volumes, cycle.json
```

`SITE` matches `^[A-Z][A-Z0-9]{3}$` (the sites are whatever `/index.json` lists; the example configuration uses KICT, KVNX, KDDC, KTWX, KGLD, KEAX, KUEX). `ID` matches `^\d{8}_\d{6}$`. Directory listings and anything outside the public tree return 404. Responses carry `Cache-Control: no-cache` and `Last-Modified`.

## Documents

Every JSON document carries `schema_version` (integer, currently 1). A client should reject any other value rather than guess. New keys can be added within a version; clients should ignore keys they don't know.

**`/index.json`**: `sites`, `site_indexes` (site → path), `keep_cycles`, `image_size`, `range_km`, `processing_version`, and `updated_at`.

**`/{SITE}/index.json`**: `site`, `updated_at`, `state` and `cycles`. `state` holds `checked_at` (the last archive poll), `error` (null or the last failure's message) and `latest_available` (the newest ID in the archive, rendered or not). `cycles` lists complete cycle manifests, newest ID first, at most `keep_cycles`.

**`cycle.json`** (also embedded in the site index) contains:

| Key | Meaning |
| --- | --- |
| `id` | Volume identity: the UTC start time from the Level II file name. |
| `revision` | 1, incremented each time the same `id` is reprocessed from a changed archive object. |
| `volume_start` | `id` as ISO 8601 UTC (`+00:00`). |
| `published_at` | The archive object's S3 LastModified, not the observation time. |
| `downloaded_at`, `rendered_at` | When this service fetched the source and finished processing it (UTC). |
| `source_key`, `source_sha256`, `source_bytes` | The exact archive object that was processed. |
| `processing` | `version`, the Py-ART version, and every setting that affects output. |
| `products` | 2D images, one entry per field × elevation index 0–3 (see below). |
| `volumes` | Numeric reflectivity volumes: `polar` and `grid` (see below). |

All timestamps are UTC ISO 8601. Sweep times use a `Z` suffix; the other times use `+00:00`.

## Time and identity

- A cycle's `id` is the identity of its radar volume. Match 2D images and 3D volumes by `(site, id)`: all products in one `cycle.json` come from the same source file.
- `sweep_start`/`sweep_end` are the actual sweep times. They are **not** monotonic in elevation index, and they can fall on a different UTC date from `id`. In the KICT sample, e0 reflectivity (a repeated low tilt) starts at 2024-05-20T00:00:50Z, while e1 starts at 2024-05-19T23:58:50Z and the volume at 23:58:10Z.
- An image's field timestamps can differ within one elevation index, because split cuts put reflectivity and velocity in separate sweeps.
- **Revisions.** If the archive later holds a different object for an `id` (a different size or LastModified), the cycle is reprocessed and replaced as a whole, with `revision` + 1. URLs stay the same, so cache by `(site, id, revision)` or by each file's `sha256`. During the swap, a request can briefly return 404; retry it after re-reading the index. *Not verified against a real republication:* the fixture's revision is simulated.
- **Late volumes.** History is backfilled newest first, so a volume can appear with an `id` older than cycles already listed. Sort by `id` and don't assume the list only grows at the front.
- **Pruning.** Only the newest `keep_cycles` IDs are kept. A URL can 404 after pruning; re-read the index.
- Show observation age. An old scan served now is still an old scan.

## Readiness

The worker writes each cycle in a staging directory and publishes it with atomic renames, then writes the site index atomically. A cycle is listed only after every file in it is complete. A failed cycle is never listed; the error appears in `state.error`, and the previous cycles stay. Each file's `bytes` and `sha256` in the manifest let a client prove it received the file exactly as published.

## Images (`products`)

Each entry has `field`, `elevation_index` (0 is the lowest available angle, not a fixed angle), `available`, and either a `reason` (when unavailable) or:

`filename` (`e{0-3}_{field}.png`), `url`, `bytes`, `sha256`, `width`, `height`, `elevation_degrees`, `sweep_index`, `sweep_start`, `sweep_end`, `bounds`, `crs`, `range_km`, `units`, `color_scale` (`colormap`, `minimum`, `maximum`), `minimum_shown`, `clutter_filter` and `velocity_dealiased`. Storm-relative velocity also carries `storm_motion`.

| Field | Units | Colors |
| --- | --- | --- |
| `reflectivity` | dBZ | `Standard` −32 to 95, or `NWSRef` −20 to 80 |
| `velocity` | m/s, positive away from the radar | `NWSVel` −70 to 70 |
| `storm_relative_velocity` | m/s, storm motion removed | `NWSVel` −70 to 70 |
| `correlation_coefficient` | unitless | `HomeyerRainbow` 0.2 to 1.05 |

- **Geometry.** Square, north-up PNGs rendered in Web Mercator (EPSG:3857). `bounds` is `[[south, west], [north, east]]` in WGS84 degrees and covers `range_km` around the radar. Rows run north to south and columns west to east. Place an image with a Mercator image overlay, never on a plain latitude/longitude raster.
- **Pixels.** Palette PNG (8-bit indexed) when there are 256 colors or fewer, otherwise RGBA. Each pixel is either fully opaque or fully transparent.
- **Missing values.** Alpha 0 means nothing is drawn: no coverage, no echo, reflectivity below `minimum_shown`, or removed by `clutter_filter`. The image does not distinguish between these. Use the numeric volumes when the distinction matters.
- `color_scale` records the colors actually used; values beyond its limits saturate. The image is a presentation product: don't read numbers back from its colors.

## Numeric reflectivity volumes (`volumes`)

Two entries, `kind: "polar"` and `kind: "grid"`, both `field: "reflectivity"`. When the export fails, both are `available: false` with a `reason`, and the images are unaffected. `VOLUME_PRODUCTS=false` leaves `volumes` empty.

**Values** (both kinds): one `uint8` per cell, at Level II's 0.5 dB resolution:

| Code | Meaning |
| --- | --- |
| 0 | No coverage: outside every beam, or beyond range (grid only). |
| 1 | No echo: covered, but Level II has no value there (below the radar's signal threshold, or range-folded). |
| 2 | Removed as non-meteorological by the dual-polarization clutter filter (`clutter_filter`): CC below `min_correlation` with reflectivity below `clutter_max_dbz`. Strong low-CC echo such as tornado debris is kept as a value, as in the Moore sample. |
| 3–255 | Reflectivity, dBZ = code × 0.5 − 33 (−31.5 to 94.5; values outside saturate). |

No display threshold is applied (`minimum_shown: null`), so clients can threshold as they choose. A missing value is never stored as a number. Level II's own decoder does not distinguish below-threshold gates from range-folded ones, so both are code 1.

**Encoding.** Each file (`volume_polar_reflectivity.bin.gz`, `volume_grid_reflectivity.bin.gz`) is one gzip stream of little-endian arrays placed end to end. The descriptor gives `bytes` and `sha256` of the file as served, and `uncompressed_bytes`. For each array, `arrays` gives `name`, `dtype` (NumPy notation: `|u1`, `<f4`, `<f8`), `shape`, `axes`, byte `offset`, `bytes` and `units`. The file is served as `application/gzip` without `Content-Encoding`, so a client decompresses it itself. `volume.unpack` is the reference decoder, and it rejects any file that does not match its descriptor.

**Polar volume.** This is the source of truth.

- **Contents.** Every elevation angle of the scan, one sweep per angle, in ascending elevation. Each sweep is the surveillance cut, the same sweep selection the images use. Gates run out to `range_km`.
- **Arrays.** `reflectivity` (`ray × gate`, uint8), plus per-ray `azimuth` (degrees clockwise from true north), `elevation` (the antenna's actual degrees) and `time` (seconds since `time_reference`).
- **Sweeps.** `sweeps[]` gives `elevation_degrees` (target), `sweep_index`, `ray_start`, `rays`, `sweep_start`, `sweep_end` and `clutter_filtered` for each sweep. Rays are not the same count in every sweep (720 at super-resolution low tilts, 360 above).
- **Gates.** `range` gives `first_gate_m` and `gate_spacing_m` (the slant range to each gate centre) and `gates`.
- **Radar.** `radar` gives `latitude`/`longitude` (WGS84) and `altitude_m`, the antenna height above mean sea level.

**Grid.** Made only from the polar volume in the same cycle (`derived_from`).

- **Arrays.** `reflectivity` with axes `z × y × x`. z runs bottom up, y south to north (row 0 is the southernmost), x west to east. The grid's rows run the opposite way from the images'.
- **Projection.** `crs.proj4` is an azimuthal equidistant projection centred on the radar, with x east and y north in metres. `x`/`y` give `first_m`, `spacing_m` (`GRID_SPACING_KM`, default 1 km) and `count`; the radar sits at the centre cell.
- **Levels.** `z` gives `first_m`, `spacing_m` (`GRID_LEVEL_M`, default 500 m) and `count`, as heights above mean sea level up to 15 km. Levels below the radar's own coverage are code 0.
- **`bounds`.** The WGS84 bounding box of the grid's corners, larger than its circular coverage.
- **Interpolation (`interpolation`).** Beam heights use the 4/3 effective Earth radius, as Py-ART does. Each sweep is sampled at the nearest ray and gate over a column. A level between two beams is interpolated linearly in height when both have reflectivity; otherwise it takes the nearer beam's code. Half a beamwidth (0.5°) is counted as covered below the lowest beam and above the highest; beyond that is no coverage. Upper sweeps are several degrees apart, so interpolated cells there are display geometry, not measurements. There is no terrain blocking.
- **Size at defaults.** 461 × 461 × 30 cells (6.4 MB uncompressed, about 1.6 MB gzip for the storm sample); the polar volume is about 1.7 MB gzip.

## Not provided

Rainfall, mosaics, echo tops, cross-sections, numeric velocity or CC volumes, and a push or streaming feed. Storm-relative velocity needs current Open-Meteo winds, so archived volumes older than the forecast window have it as an unavailable entry.
