# Fixtures

A small, redistributable sample of artemis-radar's output (2.6 MB), with provenance for every file in `provenance.json`. It covers the output contract with real, synthetic, revised, and invalid examples. The format is described in [`../CONTRACT.md`](../CONTRACT.md). `tests/test_fixtures.py` checks every file against its provenance and the contract.

| Requested sample | Where | Real or synthetic |
| --- | --- | --- |
| Top-level and site indexes | `real/index.json` (sites KICT and KTLX), `real/KICT/index.json`, `real/KTLX/index.json` | Real |
| One complete KICT volume with all available fields | `real/KICT/20240519_235810/`: `cycle.json` and 12 images (reflectivity, velocity and CC at 4 elevations). Storm-relative velocity is 4 unavailable entries with their reason. | Real |
| Paired 2D image and numeric 3D reflectivity volume | Same cycle: `e*_reflectivity.png`, `volume_polar_reflectivity.bin.gz` (14 sweeps) and `volume_grid_reflectivity.bin.gz`, made from that polar volume | Real |
| Masked/no-coverage cells | The real grid contains all four value classes (no coverage, no echo, clutter, reflectivity). `synthetic/` adds a tiny pair with known features at known positions and heights (`expected_features`). | Real, and synthetic |
| Incomplete or invalid payloads | `invalid/`: a truncated grid, an empty grid, truncated JSON, an unknown `schema_version`, a product missing required fields, and an invalid site ID. Each record has an `expected_rejection`. | Synthetic, derived from real and synthetic files |
| Tornado debris signature | `real/KTLX/20130520_202058/`: the May 20, 2013 Moore, Oklahoma EF5 tornado over central Moore, about 20 km west of KTLX. `cases/KTLX_20130520_202058_debris.json` lists its 421 lowest-tilt debris gates (CC below 0.8, reflectivity 35–70 dBZ). The clutter filter must keep them. | Real |
| Revised or delayed volume | `revised/KICT/`: the same `id` reprocessed as `revision: 2` after a simulated later LastModified. Output checksums are unchanged. | Real source, simulated republication |

No real revised volume was available, so the revision is simulated and labelled synthetic. A delayed (backfilled) volume is described in the contract but has no fixture.

## Source and provenance

- **Sources.** Two real Level II volumes from the public NOAA/Unidata archive, listed with their size, sha256, archive LastModified, and retrieval time in [`sources.json`](sources.json):
  - `2024/05/19/KICT/KICT20240519_235810_V06` (15.6 MB): Wichita storms. The primary sample; the revised and invalid fixtures are derived from it.
  - `2013/05/20/KTLX/KTLX20130520_202058_V06.gz` (9.8 MB): the Moore tornado at about 20:21 UTC.
  - The source files are not committed. NOAA/NWS data are subject to the [NWS data-use policy](https://www.weather.gov/disclaimer), and the generated files are locally processed products, not unaltered NOAA products.
- **Debris gates.** CC is not among the fixtures' numeric data. The debris gates were found in the Level II source by the generator (within 5 km of 35.34 N, 97.48 W) and recorded as polar-volume indexes.
- **How the real fixtures were made.** They came from the collector's own code (`app.process_volume`), processing version `2026.09.27`, with Py-ART 2.2.5.
- **Reduced settings.** To keep the fixtures small, they use a 256-pixel image, 100 km range, and a 2 km grid with 1000 m levels; the defaults are 1536 px, 230 km, 1 km and 500 m.
  - Every other setting is the default: 10 dBZ display threshold, clutter filter at CC 0.85 / 35 dBZ, dealiasing on, `Standard` palette.
  - `processing.settings` in `cycle.json` records the exact values.
- **Storm-relative velocity.** It is unavailable because no archived winds aloft exist for 2013 or 2024. That is also what the live service reports for an archived volume.
- **What each `provenance.json` record gives:**
  - Identity and time: site, field and product, acquisition time (sweep or volume start), and retrieval time.
  - How it was made: the source object and its checksum, generation time, processing version, and settings.
  - The file itself: its checksum and size, units, coordinate reference, dimensions, axis order, missing-value representation, and whether it is synthetic.

## Regenerating

Download the sources into one directory outside the repository, then run this with the application's dependencies installed:

```bash
mkdir -p /path/outside/repo && cd /path/outside/repo
curl -O https://unidata-nexrad-level2.s3.amazonaws.com/2024/05/19/KICT/KICT20240519_235810_V06
curl -O https://unidata-nexrad-level2.s3.amazonaws.com/2013/05/20/KTLX/KTLX20130520_202058_V06.gz
cd -  # back to the repository
python tools/make_fixtures.py /path/outside/repo
python -m unittest discover -s tests
```

The generator refuses a file that doesn't match `sources.json`. To add a source, add its entry there (with the time you retrieved it) and rerun. Generation timestamps in the JSON change each run. The images and volumes are deterministic for the same source, settings and library versions.
