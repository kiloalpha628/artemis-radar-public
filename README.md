# Artemis Radar

A local NEXRAD Level II collector and PNG renderer for the sites in `RADAR_SITES`, up to 10. The example configuration covers **KICT, KVNX, KDDC, KTWX, KGLD, KEAX, and KUEX**. It keeps the latest **10 successfully rendered volumes per site**, with reflectivity, velocity, storm-relative velocity, and correlation coefficient at the **four lowest available elevation angles**: 16 PNGs and one source volume per cycle, or up to 1,120 PNGs and 70 source volumes for seven sites. Each cycle also has **numeric 3D reflectivity**: the polar sweeps at every elevation, and a Cartesian grid made from them.

The published format is specified in [`CONTRACT.md`](CONTRACT.md). Small real samples, including the 2013 Moore tornado, are in [`fixtures/`](fixtures/README.md).

This is an early implementation for development and deployment testing. See [`VALIDATION.md`](VALIDATION.md) for completed checks and known limits. It publishes data for map and 3D clients; it does not include a browser UI.

## Storage and runtime

Two containers share one image: a single worker writes the cache and a lightweight HTTP service reads it. No AWS account, GPU, database, or cloud compute is needed. The public NOAA/Unidata feed and, for storm-relative velocity, the Open-Meteo forecast API still require internet access.

The Compose defaults limit the worker to 2 CPU equivalents and 4 GiB RAM. These are starting limits, not measured production requirements. The HTTP container has a 256 MiB limit.

All downloaded radar data, rendered images, metadata, and processing staging files live under **`RADAR_STORAGE`**, which defaults to **`/srv/scratch/radar`**. Docker image layers and Docker's own bounded logs still use Docker's configured storage location. A 5–10 GB data budget is a planning estimate, not a hard quota; retention is enforced by cycle count. The worker stops ingesting when the data filesystem has less than 2 GB free, preserving the existing images.

```text
/srv/scratch/radar/
  raw/KICT/<volume-time>             original compressed Level II volume
  public/index.json                 configured sites
  public/KICT/index.json            retained cycles, availability, errors
  public/KICT/<volume-time>/
    cycle.json                      timestamps, sizes, measured processing times
    e0_reflectivity.png              transparent map overlays
    e0_velocity.png
    e0_storm_relative_velocity.png
    e0_correlation_coefficient.png
    ... e1, e2, e3 ...
    volume_polar_reflectivity.bin.gz  numeric reflectivity, every sweep
    volume_grid_reflectivity.bin.gz   the same on a Cartesian grid
  tmp/                              staging; never served to clients
  heartbeat                         worker liveness
```

## Quick start (Docker Compose)

Use a Linux host with Docker Engine and the Docker Compose v2 plugin, outbound HTTPS access, and a writable data directory. Docker Desktop can also be used for local evaluation; the Linux scratch-mount checks below do not apply there. The worker uses a Unix advisory lock; native Windows execution is not supported.

Clone the repository and copy the example configuration:

```bash
git clone https://github.com/kiloalpha628/artemis-radar.git
cd artemis-radar
cp .env.example .env
id -u
id -g
```

Edit `.env`: choose one to ten `RADAR_SITES`, set `RADAR_UID` and `RADAR_GID` to your account's numeric IDs, and set `RADAR_STORAGE` to an absolute path on a filesystem with adequate free space. The seven example sites are optional; starting with one site reduces initial processing work.

For local evaluation, create a directory owned by your account:

```bash
mkdir -p "$PWD/data"
```

Set `RADAR_STORAGE` in `.env` to that directory's absolute path, then start both services:

```bash
docker compose config --quiet
docker compose up -d --build
docker compose logs -f worker
```

To use the example `/srv/scratch/radar` path on Linux, first verify that `/srv/scratch` is mounted. Create the data directory with the UID/GID you selected (the example below uses 1000:1000):

```bash
mountpoint -q /srv/scratch && sudo install -d -m 0755 -o 1000 -g 1000 /srv/scratch/radar
```

The bind mount does not create a missing host directory automatically. If using a separate disk, ensure it is mounted before the stack starts after reboot. `docker compose down` stops the stack; the bind-mounted data remains on disk.

### Komodo deployment

Create a Git-backed Stack using `kiloalpha628/artemis-radar`, branch `main`, run directory `.`, and compose file `compose.yaml` (also Komodo's default). Select your GitHub provider account and put the `.env.example` values in the Stack's Environment section.

Set **Run Build on**, **Auto Pull off**, **Poll for Updates off**, and **Auto Update off**. Save the settings, then use **Deploy**. `artemis-radar:local` is built on the deployment host; it is not a published Docker Hub image. Both services explicitly use `pull_policy: build` so Compose builds from the repository rather than attempting to fetch that tag. Repeated builds reuse Docker's build cache.

If an earlier deployment reports `pull access denied for artemis-radar`, confirm Auto Pull is disabled, retrieve the latest `main` configuration, and deploy again. Do not use the image Pull action for this stack. Seeing the public `python:3.12-slim-bookworm` base image download during the build is expected.

The default HTTP binding is loopback, port 8096. Check locally on the deployment host:

```bash
curl http://127.0.0.1:8096/healthz
curl http://127.0.0.1:8096/index.json
curl http://127.0.0.1:8096/KICT/index.json
```

For direct LAN access, set `RADAR_BIND_IP` to the host's LAN address and recreate the web service. The built-in HTTP server has no authentication, TLS, or rate limiting; use it on a trusted network or behind a reverse proxy that supplies those controls. A public Git repository does not require exposing this service to the internet. A client in another container must use a shared Docker network or the host's reachable LAN endpoint; its own `127.0.0.1` refers to itself. CORS is not enabled; use a server proxy or serve the client through the same origin. See [`SECURITY.md`](SECURITY.md).

The web service can become healthy before the first radar cycle is ready. Its health endpoint only checks HTTP liveness. Worker liveness is checked separately, and data freshness is indicated by the observation timestamps and site `state` in the JSON.

## Using the output in a client

1. Read `/KICT/index.json` (or another configured site). `cycles` is newest first.
2. Select a cycle, a field, and `elevation_index` 0–3. Check `available` before using it.
3. Use its `url` and `bounds` to place the PNG over an EPSG:3857 web map. Bounds are `[[south, west], [north, east]]` in geographic degrees, suitable for Leaflet `imageOverlay`.
4. Display `elevation_degrees`, `sweep_start`, and the units. Frames are transparent overlays without baked-in map labels or legends. `color_scale` identifies the colormap and its limits for a matching legend.
5. For animation, play cycles oldest to newest. Fetch frames on demand; do not decode all 1,120 possible images in the example configuration into the browser's memory at once.

The rendering is already in Web Mercator; placing it on a plain latitude/longitude raster without projection would be incorrect. Default coverage is 230 km from each radar and the PNG is 1536 × 1536 pixels. These are configurable in `.env`. Higher output resolution does not create additional radar resolution.

`e0` means the lowest available elevation, not a guaranteed fixed angle. Scan strategies vary, so read the actual angle in each product. Split reflectivity/velocity cuts are matched by nominal elevation. For repeated low tilts, the latest sweep containing each field is selected; the timestamps can differ between the fields. Missing fields/elevations are explicit unavailable entries, never silently copied from a neighboring elevation.

Reflectivity weaker than `MIN_REFLECTIVITY_DBZ` (default 10 dBZ) is left transparent, hiding the clear-air echoes, ground clutter, and birds and insects that otherwise show as a solid disk around the radar. Set it lower to see weaker echoes (drizzle, light snow), or leave it empty to draw everything; each product records the threshold used as `minimum_shown`.

**Dual-polarization cleanup.** A gate whose correlation coefficient (CC) is below `MIN_CORRELATION` (default 0.85) *and* whose reflectivity is below `CLUTTER_MAX_DBZ` (default 35) is treated as non-meteorological (ground clutter, birds, insects) and hidden in reflectivity and velocity. Strong low-CC returns — hail cores and tornado debris — are always kept. Leave `MIN_CORRELATION` empty to turn the filter off. Low split cuts carry velocity in a Doppler sweep without CC; there, CC comes from the surveillance sweep at the same angle, matched by nearest azimuth over identical range gates. Reflectivity is taken from the surveillance sweep, which carries CC. Products record the filter used as `clutter_filter`.

**Velocity** is Level II radial velocity in m/s, negative toward the radar and positive away. With `DEALIAS_VELOCITY` (default true) it is unfolded with Py-ART's region-based dealiasing; if that fails for a sweep, the measured velocity is kept and the product carries `velocity_dealiased: false` and a `note`. Velocity is shown only where reflectivity reaches `MIN_REFLECTIVITY_DBZ` and the gate is not clutter. Rainfall retrieval, mosaics, and numeric velocity volumes are not produced.

**Storm-relative velocity** (`storm_relative_velocity`, m/s, same colors and sign as velocity) is velocity with the storm's own motion removed, which makes rotation inside a moving storm easier to see. Storm motion is the Bunkers right-mover estimate (the 0–6 km mean wind plus 7.5 m/s to the right of the 0–6 km shear) from Open-Meteo forecast winds at the radar for the hour nearest the volume: 10 m and 925, 850, 700, 500, and 400 hPa, keeping levels between the ground and 6 km above it. Its component along each beam, `(u·sin az + v·cos az)·cos elevation`, is subtracted from the (dealiased) velocity. Each product records the motion used as `storm_motion` (`u`, `v`, `speed` in m/s, `direction_from` in degrees). The forecast is fetched about once an hour per radar (coordinates rounded to 0.01°, no key) and the last good one is kept if a refresh fails; when no winds are available the product is an unavailable entry with a `reason`, and the other products are unaffected. Set `STORM_RELATIVE_VELOCITY=false` to turn it off, which also stops the Open-Meteo requests. The configured free Open-Meteo endpoint is for non-commercial use under its [API terms](https://open-meteo.com/en/terms). Forecast data require [CC BY 4.0 attribution](https://open-meteo.com/en/licence). When displaying storm-relative products, credit [Open-Meteo](https://open-meteo.com/) and describe the transformation from forecast winds to storm motion. For deployments that cannot use this endpoint, set `STORM_RELATIVE_VELOCITY=false`; a paid endpoint/API key is not configurable in this version.

**Correlation coefficient** (`correlation_coefficient`, unitless, `HomeyerRainbow` from 0.2 to 1.05) is drawn wherever reflectivity reaches `MIN_REFLECTIVITY_DBZ`, without the clutter filter: low CC inside a strong rotating storm (a debris signature) is the point of the product.

**Reflectivity colors** are set by `REFLECTIVITY_PALETTE`: `Standard` (default) approximates the RadarScope "Standard (low-filter)" scale, read from its color bar and calibrated against the app's own readings (−32 to 95 dBZ; each 10 dBZ band starts bright and darkens toward the next, and below 20 dBZ is a dim blue-grey); `NWSRef` is the classic NWS scale (−20 to 80 dBZ). Each product records the scale used as `color_scale` (`colormap`, `minimum`, `maximum`), so clients can draw a matching legend. Velocity uses −70 to 70 m/s; values beyond a scale's limits saturate it, while the original source is retained.

**Numeric reflectivity volumes** (`volumes` in each cycle, `VOLUME_PRODUCTS`, default true) are for 3D clients.

- **Polar volume.** The cleaned reflectivity at every elevation of the scan, one sweep per angle, on the radar's own rays and gates out to `RANGE_KM`. The clutter filter is applied; the display threshold is not.
- **Grid.** Made only from that polar volume. It is `GRID_SPACING_KM` (default 1 km) apart horizontally around the radar and `GRID_LEVEL_M` (default 500 m) apart vertically, up to 15 km above sea level. Heights between sweeps are interpolated, and cells outside the beams are marked no coverage.
- **Values.** Both use one byte per cell (0.5 dB steps), with separate codes for no coverage, no echo, and clutter, so a missing value is never a number.
- **Cost.** At defaults, the pair adds about 3.3 MB and under a second of processing per volume (storm sample).
- **Format.** The file format, geometry, and interpolation rules are in `CONTRACT.md`; `volume.unpack` is a reference decoder.

Images are saved as palette (8-bit indexed) PNGs when they use 256 or fewer colors, which they normally do. The conversion is exact — every pixel keeps its original color and transparency — and roughly halves the file size; an image with more colors stays full-color.

## Timing, retention, and recovery

- This version polls complete volume files, not streaming chunks. Upstream scan collection/publication latency is separate from local rendering time.
- One volume is processed per site per pass, newest first, followed by backfilling available history. All sites share a single worker; simultaneous arrivals queue, so each added site lengthens the pass for the others. A 20-second polling setting is a minimum pass interval, not a promise of 20-second updates for every site.
- Cold startup fills history over multiple passes. API histories can contain fewer than 10 cycles until enough scans are available and rendered.
- Cycle manifests record download seconds, render seconds, source/image bytes, publication time, and publication-to-ready seconds. `published_at` is the archive object's S3 LastModified time, not the original observation time; republished historical objects can have much later dates. Backfilled historical scans naturally have large publication-to-ready values; use newly arriving scans for latency benchmarks.
- A volume whose archive object changes (different size or LastModified) is reprocessed and replaced as a whole, with `revision` incremented. Every file in `cycle.json` lists its `bytes` and `sha256`, and the manifest records the source checksum, `downloaded_at`, and the processing version and settings. JSON documents carry `schema_version`.
- Only completed cycles appear in the index. Failed downloads/renders preserve the previous cycle, emit a site error, and retry after five minutes. Other sites continue.
- Yesterday and today are searched in UTC to bridge midnight. A radar offline for longer retains its cached history and reports no newly available volumes. Clients must show observation age rather than imply cached images are live.
- Removing a site from configuration does not delete that site's old data. Reclaim it manually only after confirming it is no longer needed.
- A retained cycle is removed when an eleventh successful cycle arrives. A client holding a pruned URL should refresh the index after a 404.
- Only one worker may write a data directory; an advisory lock rejects a second writer.

## Development checks

Use Python 3.12, matching the Docker image, in a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Install all dependencies before interpreting a test run: missing numerical/rendering libraries cause tests to skip, and missing `requests` prevents the storm-motion test from exercising its mock. The tests use committed fixtures and mocked upstream services, so they do not download live radar data. The HTTP test binds a local loopback port. `tools/make_fixtures.py` regenerates `fixtures/` from the archived sources listed in `fixtures/sources.json` (see `fixtures/README.md`).

To process at most one new volume per site without leaving the worker running, first stop the existing worker, then:

```bash
docker compose stop worker
docker compose run --rm worker python app.py worker --once
docker compose start worker
```

Application dependencies are in `requirements.txt`; the Py-ART version is pinned. This is not a fully locked dependency tree. Save the successfully tested container image/digest before subsequent upgrades.

## Upstream references

- [Public NEXRAD archive and real-time feeds](https://registry.opendata.aws/noaa-nexrad/)
- [Py-ART NEXRAD reading example](https://arm-doe.github.io/pyart/examples/io/plot_nexrad_data_aws.html)
- [NOAA Level II measurements](https://www.roc.noaa.gov/level-two-data-types.php)

Data source: NOAA/NWS NEXRAD, distributed through the public Unidata archive. The rendered images are locally processed products, not unaltered NOAA products.

## Contributing and licensing

Bug reports and pull requests are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development and fixture guidance.

Project code and documentation are licensed under [MIT](LICENSE). Dependencies and upstream data retain their own terms; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), including Py-ART's complete license notice. The `Standard` palette was sampled from RadarScope's color bar; its provenance is described in `palettes.py`. RadarScope is a third-party product, and this project does not claim affiliation or endorsement.
