"""Regenerate fixtures/ from the archived Level II volumes in fixtures/sources.json.

    python tools/make_fixtures.py DIRECTORY

DIRECTORY (outside Git) holds the original archive files under their own names;
each is checked against the bytes and sha256 in sources.json. The real fixtures
are made by the collector's own code, app.process_volume, at reduced image size,
range, and grid resolution so they stay small; the provenance records the
settings. Synthetic and invalid fixtures are labelled so. Needs the application's
dependencies (requirements.txt).
"""
import argparse
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app  # noqa: E402

FIXTURES = ROOT / "fixtures"
SETTINGS = {"image_size": 256, "range_km": 100.0, "min_reflectivity": 10.0,
            "options": {"min_correlation": 0.85, "clutter_max_dbz": 35.0, "dealias": True,
                        "reflectivity_palette": "Standard", "volume_products": True,
                        "grid_spacing_km": 2.0, "grid_level_m": 1000.0}}
PNG_MISSING = ("alpha 0 (fully transparent) where nothing is drawn: no coverage, no echo, below minimum_shown, "
               "or removed by clutter_filter; the image does not distinguish these")
VOLUME_MISSING = "uint8 flag codes: 0 no coverage, 1 no echo, 2 clutter; values are codes 3-255, dBZ = code * 0.5 - 33"
JSON_MISSING = "an unavailable product is an entry with available false and a reason; null marks a setting not in use"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(path, **fields):
    return {"path": path.relative_to(FIXTURES).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path), **fields}


JSON_FIELDS = {"units": None, "crs": None, "dimensions": None, "axis_order": None, "missing_value": JSON_MISSING}


def real_records(directory, manifest, common, kind):
    """Provenance for one site's index, one cycle, and its files."""
    records = []
    site = manifest["site"]
    cycle_dir = directory / site / manifest["id"]
    json_fields = JSON_FIELDS
    records.append(record(directory / site / "index.json", product="site index", field=None,
                          acquisition_time=manifest["volume_start"], **json_fields, **common,
                          description=f"{site} retained cycles, newest first ({kind})."))
    records.append(record(cycle_dir / "cycle.json", product="cycle manifest", field=None,
                          acquisition_time=manifest["volume_start"], **json_fields, **common,
                          description=f"Every product and volume of the cycle ({kind})."))
    if kind == "revision 2":
        return records
    for product in manifest["products"]:
        if not product["available"]:
            continue
        records.append(record(
            cycle_dir / product["filename"], product=f"image e{product['elevation_index']}", field=product["field"],
            acquisition_time=product["sweep_start"], units=product["units"],
            crs="EPSG:3857 pixels; bounds [[south, west], [north, east]] in EPSG:4326 degrees",
            dimensions=[product["height"], product["width"]],
            axis_order="rows north to south, columns west to east (north-up image)",
            missing_value=PNG_MISSING, **common,
            description=f"{product['field']} at {product['elevation_degrees']} degrees, palette PNG."))
    polar, grid = manifest["volumes"]
    reflectivity = next(a for a in polar["arrays"] if a["name"] == "reflectivity")
    records.append(record(
        cycle_dir / polar["filename"], product="polar volume", field="reflectivity",
        acquisition_time=polar["sweeps"][0]["sweep_start"], units="dBZ",
        crs="radar-centred polar: azimuth degrees clockwise from true north, elevation degrees, slant range m; "
            "radar at WGS84 latitude/longitude, altitude m above mean sea level",
        dimensions=reflectivity["shape"], axis_order="ray (sweeps in ascending elevation, end to end), gate",
        missing_value=VOLUME_MISSING, **common,
        description=f"Cleaned reflectivity, {len(polar['sweeps'])} sweeps, the source of the grid."))
    records.append(record(
        cycle_dir / grid["filename"], product="grid volume", field="reflectivity",
        acquisition_time=polar["sweeps"][0]["sweep_start"], units="dBZ", crs=grid["crs"]["proj4"] +
        "; z metres above mean sea level", dimensions=grid["arrays"][0]["shape"],
        axis_order="z (bottom up), y (south to north), x (west to east)",
        missing_value=VOLUME_MISSING, **common,
        description="Cartesian grid made from the polar volume in the same cycle; includes no-coverage, "
                    "no-echo, and clutter cells."))
    return records


def synthetic_volumes(directory, generated_at):
    """A tiny polar volume with known features, and the grid made from it."""
    import numpy as np
    import volume
    codes = np.full((720, 100), volume.NO_ECHO, dtype=np.uint8)
    azimuth = np.tile(np.arange(360, dtype=np.float32) + 0.5, 2)
    elevation = np.repeat(np.array([0.5, 1.5], dtype=np.float32), 360)
    for start in (0, 360):
        codes[start + 85:start + 95, 20:30] = volume.encode(np.array([40.0]))[0]
        codes[start + 268:start + 272, 24:28] = volume.CLUTTER
    sweeps = [(0.5, 0, 360), (1.5, 360, 360)]
    levels = np.array([1000.0, 5000.0])
    grid, axis = volume.grid_from_polar(codes, azimuth, sweeps, 500.0, 1000.0, 400.0, 2000.0, levels, 60000.0)
    radar = {"latitude": 37.6545, "longitude": -97.4431, "altitude_m": 400.0, "altitude_datum": "mean sea level"}
    polar_data, polar = volume.pack([
        ("reflectivity", codes, {"axes": ["ray", "gate"], "units": "dBZ"}),
        ("azimuth", azimuth, {"axes": ["ray"], "units": "degrees clockwise from north"}),
        ("elevation", elevation, {"axes": ["ray"], "units": "degrees above horizontal"})])
    grid_data, packed = volume.pack([("reflectivity", grid, {"axes": ["z", "y", "x"], "units": "dBZ"})])
    (directory / "volume_polar_reflectivity.bin.gz").write_bytes(polar_data)
    (directory / "volume_grid_reflectivity.bin.gz").write_bytes(grid_data)
    horizontal = {"first_m": float(axis[0]), "spacing_m": 2000.0, "count": int(axis.size)}
    centre = int(axis.size // 2)
    east, west = centre + 12, centre - 13  # 24 km east, 26 km west

    def feature(description, z, y, x):
        return {"description": description, "array": "grid", "index_zyx": [z, y, x], "code": int(grid[z, y, x]),
                "dbz": float(volume.decode(grid[z, y, x])) if grid[z, y, x] >= volume.FIRST_VALUE else None}
    manifest = {
        "synthetic": True, "schema_version": app.SCHEMA_VERSION, "generated_at": generated_at,
        "description": "Two sweeps (0.5 and 1.5 degrees) with a 40 dBZ echo 20-30 km east at azimuth 85-95, "
                       "clutter 24-28 km west, and no echo elsewhere. Not radar data.",
        "volumes": [
            {"kind": "polar", "field": "reflectivity", "available": True, "filename": "volume_polar_reflectivity.bin.gz",
             "units": "dBZ", "values": volume.ENCODING, "radar": radar,
             "range": {"first_gate_m": 500.0, "gate_spacing_m": 1000.0, "gates": 100,
                       "measured_along": "slant range to the gate centre"},
             "sweeps": [{"elevation_degrees": a, "ray_start": s, "rays": n} for a, s, n in sweeps], **polar},
            {"kind": "grid", "field": "reflectivity", "available": True, "filename": "volume_grid_reflectivity.bin.gz",
             "derived_from": "volume_polar_reflectivity.bin.gz", "units": "dBZ", "values": volume.ENCODING,
             "radar": radar, "crs": {"proj4": f"+proj=aeqd +lat_0={radar['latitude']} +lon_0={radar['longitude']} "
                                              "+datum=WGS84 +units=m +no_defs"},
             "x": {**horizontal, "direction": "east"}, "y": {**horizontal, "direction": "north"},
             "z": {"first_m": 1000.0, "spacing_m": 4000.0, "count": 2, "datum": "mean sea level"}, **packed}],
        "expected_features": [
            feature("Echo 24 km east at 1000 m, between the two beams", 0, centre, east),
            feature("Clutter 26 km west at 1000 m", 0, centre, west),
            feature("No echo 24 km north at 1000 m", 0, centre + 12, centre),
            feature("No coverage 24 km east at 5000 m, above the highest beam", 1, centre, east),
            feature("No coverage at the grid corner, beyond range", 0, 0, 0),
        ],
    }
    app.atomic_json(directory / "volumes.json", manifest)
    return manifest


def invalid_payloads(directory, real_cycle, real_index, synthetic):
    """Broken copies of real and synthetic payloads, each with the rejection it must cause."""
    made = []
    grid = synthetic / "volume_grid_reflectivity.bin.gz"
    data = grid.read_bytes()
    (directory / "truncated_grid.bin.gz").write_bytes(data[:len(data) // 2])
    made.append(("truncated_grid.bin.gz", "synthetic/volume_grid_reflectivity.bin.gz cut to half its length, "
                 "as by an interrupted transfer", "sha256 does not match the manifest's (grid descriptor in "
                 "synthetic/volumes.json); without a checksum, not a complete gzip stream"))
    text = real_cycle.read_text()
    (directory / "truncated_cycle.json").write_text(text[:len(text) // 3])
    made.append(("truncated_cycle.json", "real cycle.json cut to a third", "not valid JSON"))
    index = json.loads(real_index.read_text())
    (directory / "unknown_schema_index.json").write_text(json.dumps({**index, "schema_version": 99}, indent=2) + "\n")
    made.append(("unknown_schema_index.json", "real site index with schema_version 99",
                 "unsupported schema_version 99"))
    manifest = json.loads(real_cycle.read_text())
    product = next(p for p in manifest["products"] if p["available"])
    for key in ("sweep_start", "bounds"):
        product.pop(key)
    (directory / "missing_fields_cycle.json").write_text(json.dumps(manifest, indent=2) + "\n")
    made.append(("missing_fields_cycle.json", f"real cycle.json; {product['filename']} lacks sweep_start and bounds",
                 f"available product {product['filename']} is missing sweep_start, bounds"))
    top = json.loads((real_index.parent.parent / "index.json").read_text())
    top["sites"] = top["sites"] + ["../KICT"]
    (directory / "bad_site_index.json").write_text(json.dumps(top, indent=2) + "\n")
    made.append(("bad_site_index.json", "real top-level index with the site '../KICT' added",
                 "invalid site identifier ../KICT"))
    (directory / "empty_grid.bin.gz").write_bytes(gzip.compress(b"", mtime=0))
    made.append(("empty_grid.bin.gz", "a complete but empty gzip stream in place of a grid",
                 "sha256 does not match the manifest's; without a checksum, 0 bytes uncompressed"))
    return made


def debris_gates(source, manifest, search):
    """Lowest-sweep gates of the polar volume inside ``search`` whose Level II CC and
    reflectivity mark tornado debris. CC is not in the fixtures, so it is read here."""
    import numpy as np
    import pyart
    polar = manifest["volumes"][0]
    sweep = polar["sweeps"][0]
    radar = pyart.io.read_nexrad_archive(str(source), include_fields=["reflectivity", "cross_correlation_ratio"])
    cut = radar.extract_sweeps([sweep["sweep_index"]])
    gates = polar["range"]["gates"]
    lat, lon = (a[:, :gates] for a in cut.get_gate_lat_lon_alt(0)[:2])
    dbz = np.ma.filled(cut.fields["reflectivity"]["data"][:, :gates].astype(float), np.nan)
    cc = np.ma.filled(cut.fields["cross_correlation_ratio"]["data"][:, :gates].astype(float), np.nan)
    north = (lat - search["latitude"]) * 111.2
    east = (lon - search["longitude"]) * 111.2 * np.cos(np.radians(search["latitude"]))
    with np.errstate(invalid="ignore"):
        found = (np.hypot(north, east) <= search["radius_km"]) & (cc < search["max_correlation"]) & (dbz >= search["min_dbz"])
    rays, columns = np.nonzero(found)
    return {
        "synthetic": False, "site": manifest["site"], "id": manifest["id"], "search": search,
        "description": "Tornado debris signature: gates of the polar volume's lowest sweep (ray and gate indexes, "
                       "ray counted from that sweep's ray_start) where the Level II source has CC below "
                       "max_correlation and reflectivity of at least min_dbz within radius_km of the search point. "
                       "CC is not part of the fixtures; it was read from the source by tools/make_fixtures.py.",
        "sweep": {"elevation_degrees": sweep["elevation_degrees"], "sweep_start": sweep["sweep_start"],
                  "ray_start": sweep["ray_start"]},
        "count": int(found.sum()), "max_dbz": float(np.nanmax(dbz[found])), "min_correlation": float(np.nanmin(cc[found])),
        "centroid": [round(float(lat[found].mean()), 4), round(float(lon[found].mean()), 4)],
        "gates": [[int(r), int(g)] for r, g in zip(rays, columns)],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, help="Holds the archive files named in fixtures/sources.json")
    args = parser.parse_args()
    import volume  # noqa: F401  (fail early without the dependencies)
    catalogue = json.loads((FIXTURES / "sources.json").read_text())
    sources = []
    for entry in catalogue["sources"]:
        path = args.directory / entry["key"].rsplit("/", 1)[-1]
        if not path.exists() or path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise SystemExit(f"{path} is missing or is not the archive object in sources.json")
        site = entry["key"].split("/")[3]
        sources.append((entry, path, site, app.volume_id(entry["key"], site)))
    for sub in ("real", "revised", "synthetic", "invalid", "cases"):
        shutil.rmtree(FIXTURES / sub, ignore_errors=True)
        (FIXTURES / sub).mkdir(parents=True)

    class Archive:
        path = None

        def download_file(self, bucket, key, path):
            shutil.copyfile(self.path, path)

    archive = Archive()
    options = {**SETTINGS["options"], "storm_motion": lambda *_: None}  # no archived winds aloft for these dates
    processing = {"version": app.PROCESSING_VERSION, "pyart": app.library_version("arm_pyart")}
    records, revised_records, cases = [], [], []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for entry, path, site, stamp in sources:
            archive.path = path
            obj = {"id": stamp, "key": entry["key"], "size": entry["bytes"], "published_at": entry["published_at"]}
            app.process_volume(root, site, obj, archive, SETTINGS["image_size"], SETTINGS["range_km"],
                               SETTINGS["min_reflectivity"], options)
            state = {"checked_at": app.utcnow().isoformat(), "error": None, "latest_available": stamp}
            app.publish_index(root, site, 10, state)
            shutil.copytree(root / "public" / site, FIXTURES / "real" / site)
            manifest = json.loads((FIXTURES / "real" / site / stamp / "cycle.json").read_text())
            common = {"synthetic": False, "site": site, "retrieval_time": entry["retrieved_at"],
                      "generated_at": manifest["rendered_at"], "processing_version": processing,
                      "source": {"bucket": catalogue["bucket"], "key": entry["key"], "bytes": entry["bytes"],
                                 "sha256": entry["sha256"], "published_at": entry["published_at"]},
                      "settings": manifest["processing"]["settings"]}
            records += real_records(FIXTURES / "real", manifest, common, "revision 1")
            if entry.get("debris_search"):
                case = debris_gates(path, manifest, entry["debris_search"])
                case_path = FIXTURES / "cases" / f"{site}_{stamp}_debris.json"
                app.atomic_json(case_path, case)
                records.append(record(case_path, product="expected features", field="reflectivity",
                                      acquisition_time=case["sweep"]["sweep_start"], **JSON_FIELDS,
                                      **{**common, "generated_at": app.utcnow().isoformat()},
                                      description=f"{case['count']} debris-signature gates in the {site} polar volume."))
            if not entry.get("revised_fixture"):
                continue
            # Simulated republication: the same object with a later LastModified.
            republished = {**obj, "published_at": "2025-07-16T03:00:00+00:00"}
            app.process_volume(root, site, republished, archive, SETTINGS["image_size"], SETTINGS["range_km"],
                               SETTINGS["min_reflectivity"], options)
            app.publish_index(root, site, 10, {**state, "checked_at": app.utcnow().isoformat()})
            (FIXTURES / "revised" / site / stamp).mkdir(parents=True)
            shutil.copyfile(root / "public" / site / "index.json", FIXTURES / "revised" / site / "index.json")
            shutil.copyfile(root / "public" / site / stamp / "cycle.json", FIXTURES / "revised" / site / stamp / "cycle.json")
            revised = json.loads((FIXTURES / "revised" / site / stamp / "cycle.json").read_text())
            revised_records = real_records(FIXTURES / "revised", revised,
                                           {**common, "synthetic": True, "generated_at": revised["rendered_at"],
                                            "note": "Real source; the republication (a later LastModified) is "
                                                    "simulated. Its files are identical to real/ and are not repeated."},
                                           "revision 2")
            primary = (site, stamp)
        sites = [site for _, _, site, _ in sources]
        app.publish_sites(root, sites, 10, SETTINGS["image_size"], SETTINGS["range_km"])
        shutil.copyfile(root / "public" / "index.json", FIXTURES / "real" / "index.json")
    records.insert(0, record(FIXTURES / "real/index.json", product="top-level index", field=None, acquisition_time=None,
                             **JSON_FIELDS, **{**common, "site": None, "source": None},
                             description="Configured sites and site index paths."))
    records += revised_records

    generated_at = datetime.now(timezone.utc).isoformat()
    synthetic = synthetic_volumes(FIXTURES / "synthetic", generated_at)
    shape = {v["kind"]: v["arrays"][0]["shape"] for v in synthetic["volumes"]}
    synthetic_common = {"synthetic": True, "site": None, "field": "reflectivity", "acquisition_time": None,
                        "retrieval_time": None, "generated_at": generated_at, "processing_version": processing,
                        "units": "dBZ", "missing_value": VOLUME_MISSING, "source": None}
    records.append(record(FIXTURES / "synthetic/volumes.json", product="volume descriptors", **{
        **synthetic_common, "units": None, "missing_value": JSON_MISSING}, crs=None, dimensions=None, axis_order=None,
        description="Descriptors of the synthetic pair and the features expected in the grid."))
    records.append(record(FIXTURES / "synthetic/volume_polar_reflectivity.bin.gz", product="polar volume",
                          crs="radar-centred polar, as the real polar volume", dimensions=shape["polar"],
                          axis_order="ray, gate", **synthetic_common, description=synthetic["description"]))
    records.append(record(FIXTURES / "synthetic/volume_grid_reflectivity.bin.gz", product="grid volume",
                          crs=synthetic["volumes"][1]["crs"]["proj4"] + "; z metres above mean sea level",
                          dimensions=shape["grid"], axis_order="z, y (south to north), x (west to east)",
                          **synthetic_common, description="Grid made from the synthetic polar volume."))
    site, stamp = primary
    for filename, description, rejection in invalid_payloads(
            FIXTURES / "invalid", FIXTURES / "real" / site / stamp / "cycle.json",
            FIXTURES / "real" / site / "index.json", FIXTURES / "synthetic"):
        records.append(record(FIXTURES / "invalid" / filename, product="invalid payload", synthetic=True,
                              site=None, field=None, acquisition_time=None, retrieval_time=None,
                              generated_at=generated_at, processing_version=processing, units=None, crs=None,
                              dimensions=None, axis_order=None, missing_value=None, source=None,
                              description=description, expected_rejection=rejection))
    app.atomic_json(FIXTURES / "provenance.json", {"schema_version": 1, "generated_at": generated_at,
                                                   "fixtures": records})
    total = sum(r["bytes"] for r in records)
    print(f"{len(records)} fixtures, {total / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
