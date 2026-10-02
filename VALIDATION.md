# Branch and personal-information review — October 1, 2026

- Refreshed the remote branches. All three feature/fix branch tips are ancestors of `main`: `feature/palette-storm-relative`, `feature/standard-reflectivity-palette`, and `fix/dockerfile-storm-motion`. Their changes are already merged, and GitHub has no open pull requests. The repository was still private when checked.
- Reviewed working files and branch history for names, email addresses, personal paths, private network addresses, credentials, and home-server references. PNG fixtures have no text/EXIF metadata, and gzip fixtures have no original-filename/comment headers.
- Replaced the first name in the new MIT copyright notice with the repository owner's public GitHub handle. Repository URLs and the handle remain because they identify the project and its copyright owner.
- The nine original commits contain the author's first name and personal Gmail address in author/committer metadata. Older documents also refer to the Artemis deployment host, ARGUS and Tilt clients, and a Plex/Immich workload. The current documentation uses general host/client instructions; `Artemis Radar` remains the project name. Deleting merged branches does not remove these details from `main` history.
- Radar site identifiers and coordinates in examples/fixtures describe public NOAA radar stations and archived storms, not a private home address. The example site list reflects a regional configuration but does not establish the owner's location. Operating-system and resource measurements are technical validation context.
- No personal email addresses, personal home-directory paths, private IPv4 addresses, or common credential patterns were found in the current project files. The pattern scan is limited and is not a complete security audit.
- The release-preparation commit uses the public GitHub handle and GitHub no-reply address. Existing history was not rewritten, and no visibility change was requested in this branch-maintenance pass.

# Public-release review — October 1, 2026

## Completed

- Reviewed tracked source, configuration, documentation, fixtures, and reachable Git history (9 commits, 109 unique blobs).
- Pattern-based history scan found no matches for common AWS/GitHub/provider credentials, private keys, credential-bearing URLs, quoted secret assignments, private IPv4 addresses, or personal home-directory paths. This is a limited scan, not a guarantee that all sensitive content has been detected.
- Added the project MIT license and the unmodified Py-ART 2.2.5 notice, with Dockerfile instructions to include both and the third-party notice document in the image.
- Generalized deployment and client documentation; documented Open-Meteo attribution/service terms and the HTTP service's access boundary; expanded Git and Docker ignore rules for local configuration, credentials, tools, and runtime data.
- All 43 existing tests passed without skips in a fresh Python 3.12 environment with `requirements.txt` installed. The HTTP isolation test passed with loopback socket access. The storm-motion test also passed; the earlier dependency-free failure is not evidence of a runtime bug.
- Docker Compose configuration validated using `.env.example`. Relative documentation links, license copy equality, and `git diff --check` passed.

## Remaining public-release considerations

- Existing commits include the author's personal email address. Changing future Git configuration does not remove it from old commits. History was not rewritten.
- The `Standard` palette was sampled from RadarScope screenshots. Its provenance is recorded, but no reuse permission record is present in this repository. Review that before distributing it and the generated `Standard` fixtures; selecting `NWSRef` affects future output only. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- The Docker engine was unavailable, so the updated image was not built or run. Live multi-site operation and client rendering remain deployment checks.
- The initial review prepared changes locally. Repository visibility and security settings were not changed; private vulnerability reporting was not verified.

# Validation record — September 27, 2026: numeric volumes, provenance, fixtures

## Completed locally

- **Tests.** All 43 automated tests pass on Python 3.12 with the pinned dependencies (Py-ART 2.2.5, NumPy 2.5.3). The new tests cover:
  - the volume settings and their limits
  - value encoding; beam geometry against the standard beam equation
  - features landing at known grid positions and heights
  - height interpolation only between two values
  - no coverage below and above the beams and beyond range
  - pack/unpack round trip and rejection of truncated, empty, or mismatched files
  - manifest versions and checksums
  - revised-volume replacement and worker reprocessing
  - every fixture against its provenance and the contract
  - the Moore tornado debris signature kept by the clutter filter, and present in the grid and images
- **System Python.** On the Mac's Python 3.9 without the dependencies, 16 tests skip. One storm-motion test fails there, and it fails the same way without these changes.
- **Real volume at default settings.** The same archived KICT volume, re-downloaded, was processed through `app.process_volume` at default settings, with storm-relative velocity off. The polar volume has 14 sweeps (0.48° to 19.5°, 6,120 rays × 912 gates).
- **Visual check.** A 3 km slice, the column maximum, and a west–east section of the grid show the storms in the same places as the e1 reflectivity image, north-up. The no-coverage floor rises with range as expected.
- **HTTP.** The grid file served by the HTTP handler arrives as `application/gzip` without `Content-Encoding`, and its sha256 matches the manifest.
- **Moore tornado.** KTLX 2013-05-20 20:20:58 UTC was processed through the same path at the fixture settings. The lowest tilt has 421 debris gates within 5 km of central Moore, all kept by the clutter filter. The debris ball is visible in the e0 reflectivity image about 20 km west of the radar. The e0 velocity image shows no clear couplet at 256 px, and the raw gate-to-gate velocity maxima sit at the ±26 m/s Nyquist limit, so velocity near the tornado was not verified.
- **Determinism.** Regenerating the fixtures twice gave byte-identical images and volumes.

| Measurement (local Mac, outside Docker) | Result |
|---|---:|
| Render, 12 images + both volumes, first in a fresh process | 18.5 seconds |
| Volume export alone, warm | 0.84 seconds |
| Polar volume (gzip) | 1,703,659 bytes |
| Grid, 461 × 461 × 30 (gzip) | 1,566,916 bytes |
| Peak resident memory of that process | ~853 MiB |

At this sample's size, the volumes add about 200 MB for 60 retained cycles. The memory figure includes 12 images, not the 8 measured below, so the two peaks are not directly comparable.

## Not verified

- The Docker image was not built, and the deployment host was not accessed.
- The grid was not compared cell by cell with Py-ART's own gridding (`grid_from_radars`), which uses a different method (weighted neighbours).
- No real republished volume was available: the revision fixture simulates one.
- One storm volume from one site was tested; other scan strategies and sites still need live exercise.

# Validation record — September 26, 2026

## Completed locally

- Seven automated tests: UTC midnight listing, excluding metadata files, ten-cycle retention with source cleanup, independent site retention, split/repeated sweep selection, invalid site rejection, failed-render preservation, HTTP endpoint/path isolation, and continuing other sites after one upstream failure. Several tests cover more than one behavior.
- Docker Compose configuration parsed and validated using the Docker Desktop Compose binary.
- Python 3.12 environment installed the declared dependencies successfully.
- Downloaded and decoded a real archived Wichita volume: `2024/05/19/KICT/KICT20240519_235810_V06` from `unidata-nexrad-level2`.
- Rendered all eight products at 1536 × 1536 pixels: reflectivity and velocity at approximately 0.483°, 0.879°, 1.318°, and 1.802°.
- Checked every image's dimensions, RGBA format, and transparent/opaque alpha range. Visually inspected the lowest-elevation reflectivity output.
- Successfully wrote the cycle manifest and site index using the actual collector/publisher functions.

## Sample measurements

These are measurements on the local Mac, outside Docker, not production benchmarks.

| Measurement | Result |
|---|---:|
| Compressed source volume | 15,630,650 bytes |
| Eight PNG overlays combined | 6,878,487 bytes |
| Source download | 0.674 seconds |
| First render, including cold imports/font cache | 34.636 seconds |
| Warm render with modules already loaded | 4.949 seconds |
| Warm process peak resident memory | 721,551,360 bytes (~688 MiB) |

At this one sample's size, 60 volumes plus 480 images occupy about **1.35 GB**, before filesystem/metadata overhead and staging. This is an extrapolation from one storm volume, not a guarantee across all sites and scan strategies. Network/cache conditions also affect download timing.

The archived object's S3 LastModified date is in 2025, while the scan was observed in 2024. It was not a live latency test. `publication_to_ready_seconds` for this historical run must not be used as a real-time latency measurement.

## Remaining deployment verification

The local Docker engine was not running, so the image was **not built or run as a container**. The deployment host has not been accessed or modified.

On the deployment host, verify the Linux image build, data mount/ownership, startup under Komodo or Docker Compose, freshness and throughput for every configured site under the host's normal workload, and the browser integration's map alignment and legends. A basic image overlay API is implemented; a client UI is not part of this package.

The real-data sample covered one Wichita storm volume. Different scan strategies and all other sites still need live exercise, although missing products and field/elevation selection have automated coverage.
