"""Single-writer radar cache and a read-only HTTP endpoint. No AWS credentials needed."""
import argparse
import functools
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("radar")
BUCKET = "unidata-nexrad-level2"
STOP = threading.Event()
# One worker renders every site in turn, so the list is bounded.
MAX_SITES = 10
# Layout of index.json, site index.json and cycle.json; changes only when a
# client could misread the new layout.
SCHEMA_VERSION = 1
# Change when rendered images, volumes, or their metadata change for the same source.
PROCESSING_VERSION = "2026.09.27"


class PublicS3:
    """Anonymous HTTPS access; avoids AWS credentials and extra SDK dependencies."""
    def __init__(self):
        import requests
        self.session = requests.Session()

    def get_paginator(self, _operation):
        return self

    def paginate(self, Bucket, Prefix):
        params = {"list-type": "2", "prefix": Prefix}
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        while True:
            response = self.session.get(f"https://{Bucket}.s3.amazonaws.com/", params=params, timeout=(10, 30))
            response.raise_for_status()
            tree = ET.fromstring(response.content)
            yield {"Contents": [{
                "Key": item.findtext("s:Key", namespaces=ns),
                "Size": int(item.findtext("s:Size", namespaces=ns)),
                "LastModified": datetime.fromisoformat(item.findtext("s:LastModified", namespaces=ns).replace("Z", "+00:00")),
            } for item in tree.findall("s:Contents", ns)]}
            token = tree.findtext("s:NextContinuationToken", namespaces=ns)
            if not token:
                break
            params["continuation-token"] = token

    def download_file(self, bucket, key, filename):
        from urllib.parse import quote
        with self.session.get(f"https://{bucket}.s3.amazonaws.com/{quote(key, safe='/')}",
                              stream=True, timeout=(10, 30)) as response:
            response.raise_for_status()
            written = 0
            with open(filename, "wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    written += len(chunk)
                    if written > 100 * 1024 * 1024:
                        raise ValueError("Source volume exceeds 100 MiB download limit")
                    output.write(chunk)


def utcnow():
    return datetime.now(timezone.utc)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    os.replace(tmp, path)


def sites_from_env():
    sites = list(dict.fromkeys(s.strip().upper() for s in os.environ.get("RADAR_SITES", "").split(",") if s.strip()))
    if not sites or len(sites) > MAX_SITES or any(not re.fullmatch(r"[A-Z][A-Z0-9]{3}", s) for s in sites):
        raise ValueError(f"RADAR_SITES must contain 1–{MAX_SITES} four-character radar IDs")
    return sites


def min_reflectivity_from_env():
    """Weakest reflectivity drawn, in dBZ (default 10); empty draws everything."""
    raw = os.environ.get("MIN_REFLECTIVITY_DBZ", "10").strip()
    if not raw:
        return None
    value = float(raw)
    if not -30 <= value <= 40:
        raise ValueError("MIN_REFLECTIVITY_DBZ must be between -30 and 40, or empty")
    return value


def render_options_from_env():
    """Dual-polarization cleanup and velocity dealiasing settings.

    MIN_CORRELATION (default 0.85; empty turns the clutter filter off) and
    CLUTTER_MAX_DBZ (default 35): a gate below both is hidden as clutter.
    DEALIAS_VELOCITY (default true) unfolds velocity. STORM_RELATIVE_VELOCITY
    (default true) adds storm-relative velocity using winds aloft from Open-Meteo.
    REFLECTIVITY_PALETTE (default Standard) picks the reflectivity colors:
    Standard (an approximate RadarScope Standard scale) or NWSRef (classic NWS).
    VOLUME_PRODUCTS (default true) adds the numeric polar reflectivity volume and
    the grid made from it, GRID_SPACING_KM (default 1) apart horizontally and
    GRID_LEVEL_M (default 500) apart vertically.
    """
    raw = os.environ.get("MIN_CORRELATION", "0.85").strip()
    min_correlation = float(raw) if raw else None
    clutter_max_dbz = float(os.environ.get("CLUTTER_MAX_DBZ", "35"))
    dealias = os.environ.get("DEALIAS_VELOCITY", "true").strip().lower() in ("1", "true", "yes", "on")
    storm_relative = os.environ.get("STORM_RELATIVE_VELOCITY", "true").strip().lower() in ("1", "true", "yes", "on")
    palette = os.environ.get("REFLECTIVITY_PALETTE", "Standard").strip() or "Standard"
    if palette not in ("Standard", "NWSRef"):
        raise ValueError("REFLECTIVITY_PALETTE must be Standard or NWSRef")
    if min_correlation is not None and not 0 < min_correlation < 1:
        raise ValueError("MIN_CORRELATION must be between 0 and 1, or empty")
    if not -30 <= clutter_max_dbz <= 80:
        raise ValueError("CLUTTER_MAX_DBZ must be between -30 and 80")
    volume_products = os.environ.get("VOLUME_PRODUCTS", "true").strip().lower() in ("1", "true", "yes", "on")
    grid_spacing_km = float(os.environ.get("GRID_SPACING_KM", "1").strip() or "1")
    grid_level_m = float(os.environ.get("GRID_LEVEL_M", "500").strip() or "500")
    if not 0.5 <= grid_spacing_km <= 10:
        raise ValueError("GRID_SPACING_KM must be between 0.5 and 10")
    if not 100 <= grid_level_m <= 5000:
        raise ValueError("GRID_LEVEL_M must be between 100 and 5000")
    options = {"min_correlation": min_correlation, "clutter_max_dbz": clutter_max_dbz, "dealias": dealias,
               "reflectivity_palette": palette, "volume_products": volume_products,
               "grid_spacing_km": grid_spacing_km, "grid_level_m": grid_level_m}
    if storm_relative:
        from storm_motion import StormMotion
        options["storm_motion"] = StormMotion()
    return options


def volume_id(key, site):
    name = key.rsplit("/", 1)[-1]
    match = re.fullmatch(re.escape(site) + r"(\d{8}_\d{6})_V\d{2}(?:\.gz)?", name)
    return match.group(1) if match else None


def list_recent(client, site, now, keep):
    """Check both UTC days so midnight never clears the current history."""
    found = {}
    for day in (now - timedelta(days=1), now):
        prefix = day.strftime("%Y/%m/%d/") + site + "/"
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                stamp = volume_id(obj["Key"], site)
                if stamp and 0 < obj["Size"] <= 100 * 1024 * 1024:
                    # Excludes metadata-only *_MDM files and empty objects.
                    found[stamp] = {"id": stamp, "key": obj["Key"], "size": obj["Size"],
                                    "published_at": obj["LastModified"].isoformat()}
    return [found[k] for k in sorted(found, reverse=True)[:keep]]


def cycle_manifests(root, site):
    result = []
    for file in (root / "public" / site).glob("*/cycle.json"):
        try:
            result.append(json.loads(file.read_text()))
        except (OSError, ValueError):
            LOG.warning("Ignoring unreadable manifest %s", file)
    return sorted(result, key=lambda c: c["id"], reverse=True)


def publish_index(root, site, keep, state):
    cycles = cycle_manifests(root, site)
    kept = cycles[:keep]
    atomic_json(root / "public" / site / "index.json", {
        "schema_version": SCHEMA_VERSION, "site": site, "updated_at": utcnow().isoformat(),
        "state": state, "cycles": kept,
    })
    # Publish references first. Old clients may need to refresh a pruned URL.
    for cycle in cycles[keep:]:
        stamp = cycle["id"]
        if not re.fullmatch(r"\d{8}_\d{6}", stamp):
            continue
        shutil.rmtree(root / "public" / site / stamp, ignore_errors=True)
        (root / "raw" / site / stamp).unlink(missing_ok=True)
    return kept


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def library_version(name):
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def is_revised(manifest, obj):
    """True when the archive now holds a different object for an already published volume."""
    return manifest.get("published_at") != obj["published_at"] or manifest.get("source_bytes") != obj["size"]


def process_volume(root, site, obj, client, image_size, range_km, min_reflectivity=None, render_options=None):
    from render import render_volume
    started = time.monotonic()
    stamp = obj["id"]
    stage = root / "tmp" / site
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    raw = stage / "source"
    destination = root / "public" / site / stamp
    try:
        client.download_file(BUCKET, obj["key"], str(raw))
        if raw.stat().st_size != obj["size"]:
            raise RuntimeError("Downloaded size does not match S3 object")
        downloaded_at = utcnow()
        downloaded = time.monotonic()
        target = stage / "images"
        target.mkdir()
        options = dict(render_options or {})
        rendered = render_volume(raw, target, image_size, range_km, min_reflectivity, **options)
        products, volumes = rendered["products"], rendered["volumes"]
        finished = utcnow()
        try:
            revision = json.loads((destination / "cycle.json").read_text()).get("revision", 1) + 1
        except (OSError, ValueError, AttributeError):
            revision = 1
        settings = {"image_size": image_size, "range_km": range_km, "min_reflectivity_dbz": min_reflectivity,
                    **{k: v for k, v in options.items() if k != "storm_motion"},
                    "storm_relative_velocity": options.get("storm_motion") is not None}
        manifest = {
            "schema_version": SCHEMA_VERSION, "site": site, "id": stamp, "revision": revision,
            "source_key": obj["key"], "source_sha256": sha256_file(raw),
            "volume_start": datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc).isoformat(),
            "published_at": obj["published_at"], "downloaded_at": downloaded_at.isoformat(),
            "rendered_at": finished.isoformat(),
            "processing": {"version": PROCESSING_VERSION, "pyart": library_version("arm_pyart"), "settings": settings},
            "source_bytes": raw.stat().st_size,
            "image_bytes": sum(p.stat().st_size for p in target.glob("*.png")),
            "volume_bytes": sum(p.stat().st_size for p in target.glob("*.bin.gz")),
            "download_seconds": round(downloaded - started, 3),
            "render_seconds": round(time.monotonic() - downloaded, 3),
            "publication_to_ready_seconds": round((finished - datetime.fromisoformat(obj["published_at"])).total_seconds(), 3),
            "products": products,
            "volumes": volumes,
        }
        for product in products + volumes:
            if product.get("filename"):
                product["url"] = f"/{site}/{stamp}/{product['filename']}"
                product["bytes"] = (target / product["filename"]).stat().st_size
                product["sha256"] = sha256_file(target / product["filename"])
        atomic_json(target / "cycle.json", manifest)
        raw_dir = root / "raw" / site
        raw_dir.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(raw, raw_dir / stamp)
        if destination.exists():
            # A revised volume: swap the whole cycle so files of two revisions never mix.
            os.replace(destination, stage / "previous")
        os.replace(target, destination)
        LOG.info("%s %s: %.1fs download, %.1fs render, %.2f MB images", site, stamp,
                 manifest["download_seconds"], manifest["render_seconds"], manifest["image_bytes"] / 1e6)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def worker(root, once=False):
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    # Refuse a second writer, including a concurrent --once invocation.
    with (root / ".worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _worker(root, once)


def _worker(root, once=False):
    sites = sites_from_env()
    keep = int(os.environ.get("KEEP_CYCLES", "10"))
    size = int(os.environ.get("IMAGE_SIZE", "1536"))
    range_km = float(os.environ.get("RANGE_KM", "230"))
    poll = max(10, int(os.environ.get("POLL_SECONDS", "20")))
    reserve = float(os.environ.get("MIN_FREE_GB", "2")) * 1e9
    min_reflectivity = min_reflectivity_from_env()
    render_options = render_options_from_env()
    if not 1 <= keep <= 100 or not 256 <= size <= 4096 or not 10 <= range_km <= 460 or reserve < 0:
        raise ValueError("Invalid KEEP_CYCLES, IMAGE_SIZE, RANGE_KM, or MIN_FREE_GB")
    root.mkdir(parents=True, exist_ok=True)
    for site in sites:
        shutil.rmtree(root / "tmp" / site, ignore_errors=True)
        # Recover source files left by a crash between source/image publication.
        for raw in (root / "raw" / site).glob("*"):
            if re.fullmatch(r"\d{8}_\d{6}", raw.name) and not (root / "public" / site / raw.name / "cycle.json").exists():
                raw.unlink()
    client = PublicS3()
    failures = {}
    while not STOP.is_set():
        loop_started = time.monotonic()
        for site in sites:
            if STOP.is_set():
                break
            (root / "heartbeat").touch()
            state = {"checked_at": utcnow().isoformat(), "error": None}
            try:
                candidates = list_recent(client, site, utcnow(), keep)
                active = {obj["id"] for obj in candidates}
                failures = {key: value for key, value in failures.items() if key[0] != site or key[1] in active}
                existing = {c["id"]: c for c in cycle_manifests(root, site)}
                state["latest_available"] = candidates[0]["id"] if candidates else None
                if not candidates:
                    state["error"] = "No volumes found in today's or yesterday's UTC archive"
                for obj in candidates:
                    token = (site, obj["id"])
                    known = existing.get(obj["id"])
                    if (known and not is_revised(known, obj)) or failures.get(token, 0) > time.monotonic():
                        continue
                    if shutil.disk_usage(root).free < reserve:
                        raise RuntimeError("Scratch drive free space below MIN_FREE_GB; preserving existing cache")
                    try:
                        process_volume(root, site, obj, client, size, range_km, min_reflectivity, render_options)
                        failures.pop(token, None)
                    except Exception:
                        failures[token] = time.monotonic() + 300
                        raise
                    # One volume per site per pass: newest first, then fill history.
                    break
            except Exception as exc:
                state["error"] = str(exc)
                LOG.exception("%s update failed", site)
            publish_index(root, site, keep, state)
        publish_sites(root, sites, keep, size, range_km)
        (root / "heartbeat").touch()
        if once:
            break
        STOP.wait(max(0, poll - (time.monotonic() - loop_started)))


def publish_sites(root, sites, keep, size, range_km):
    atomic_json(root / "public" / "index.json", {
        "schema_version": SCHEMA_VERSION, "processing_version": PROCESSING_VERSION,
        "updated_at": utcnow().isoformat(), "sites": sites,
        "keep_cycles": keep, "image_size": size, "range_km": range_km,
        "site_indexes": {site: f"/{site}/index.json" for site in sites},
    })


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path == "/":
            self.path = "/index.json"
        super().do_GET()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["worker", "serve", "health"])
    parser.add_argument("--once", action="store_true", help="Process at most one new volume per site and exit")
    args = parser.parse_args()
    root = Path(os.environ.get("DATA_DIR", "/data"))
    if args.command == "health":
        try:
            healthy = time.time() - (root / "heartbeat").stat().st_mtime < 600
        except FileNotFoundError:
            healthy = False
        raise SystemExit(0 if healthy else 1)
    if args.command == "serve":
        server = ThreadingHTTPServer(("0.0.0.0", 8080), functools.partial(Handler, directory=str(root / "public")))
        server.serve_forever()
    else:
        signal.signal(signal.SIGTERM, lambda *_: STOP.set())
        signal.signal(signal.SIGINT, lambda *_: STOP.set())
        worker(root, args.once)


if __name__ == "__main__":
    main()
