"""The committed fixtures agree with their provenance and with the documented contract."""
import hashlib
import json
import re
import struct
import unittest
from datetime import datetime, timedelta
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
try:
    import numpy as np
    import volume
except ImportError:
    np = volume = None

FIXTURES = ROOT / "fixtures"
REAL = FIXTURES / "real"
STAMP = "20240519_235810"
FIELDS = ("reflectivity", "velocity", "storm_relative_velocity", "correlation_coefficient")
RECORD_KEYS = {"path", "bytes", "sha256", "synthetic", "site", "field", "product", "acquisition_time",
               "retrieval_time", "processing_version", "units", "crs", "dimensions", "axis_order",
               "missing_value", "description"}
PRODUCT_KEYS = ("filename", "url", "width", "height", "elevation_degrees", "sweep_start", "sweep_end",
                "bounds", "crs", "units", "color_scale", "sha256")


def load(path):
    return json.loads(Path(path).read_text())


def check_index(document):
    """The checks a client should make before trusting an index."""
    if document.get("schema_version") != app.SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {document.get('schema_version')}")
    for site in document.get("sites", [document.get("site")]):
        if not isinstance(site, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{3}", site):
            raise ValueError(f"invalid site identifier {site}")


def check_cycle(manifest):
    check_index({"schema_version": manifest.get("schema_version"), "site": manifest.get("site")})
    for product in manifest["products"]:
        missing = [key for key in PRODUCT_KEYS if key not in product]
        if product.get("available") and missing:
            raise ValueError(f"available product {product.get('filename')} is missing {', '.join(missing)}")
        if not product.get("available") and not product.get("reason"):
            raise ValueError("unavailable product without a reason")


class ProvenanceTests(unittest.TestCase):
    def test_every_fixture_has_complete_matching_provenance(self):
        records = load(FIXTURES / "provenance.json")["fixtures"]
        paths = [r["path"] for r in records]
        on_disk = sorted(p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob("*")
                         if p.is_file() and p.name not in ("README.md", "provenance.json", "sources.json", ".DS_Store"))
        self.assertEqual(sorted(paths), on_disk)
        self.assertEqual(len(paths), len(set(paths)))
        for record in records:
            self.assertEqual(RECORD_KEYS - record.keys(), set(), record["path"])
            data = (FIXTURES / record["path"]).read_bytes()
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (record["bytes"], record["sha256"]),
                             record["path"])
            if record["source"]:
                manifest = next(REAL.glob(f"{record['site']}/*/cycle.json"))
                self.assertEqual(record["source"]["sha256"], load(manifest)["source_sha256"])
        sources = {s["key"]: s for s in load(FIXTURES / "sources.json")["sources"]}
        for record in records:
            if record["source"]:
                source = sources[record["source"]["key"]]
                self.assertEqual((source["sha256"], source["retrieved_at"]),
                                 (record["source"]["sha256"], record["retrieval_time"]))
        # Small enough for Git.
        self.assertLess(sum(r["bytes"] for r in records), 3_000_000)


class RealFixtureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load(REAL / "KICT" / STAMP / "cycle.json")

    def test_indexes(self):
        top, site = load(REAL / "index.json"), load(REAL / "KICT/index.json")
        check_index(top)
        check_index(site)
        self.assertEqual(top["site_indexes"], {"KICT": "/KICT/index.json", "KTLX": "/KTLX/index.json"})
        ids = [cycle["id"] for cycle in site["cycles"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(site["cycles"][0], self.manifest)
        self.assertIsNone(site["state"]["error"])

    def test_cycle_products_and_images(self):
        check_cycle(self.manifest)
        self.assertEqual(self.manifest["volume_start"], "2024-05-19T23:58:10+00:00")
        products = {(p["field"], p["elevation_index"]): p for p in self.manifest["products"]}
        self.assertEqual(set(products), {(f, level) for f in FIELDS for level in range(4)})
        angles = []
        volume_start = datetime.fromisoformat(self.manifest["volume_start"])
        for (field, level), product in products.items():
            if not product["available"]:
                self.assertEqual(field, "storm_relative_velocity")  # no archived winds aloft for 2024
                continue
            self.assertEqual(product["filename"], f"e{level}_{field}.png")
            self.assertEqual(product["url"], f"/KICT/{STAMP}/{product['filename']}")
            data = (REAL / "KICT" / STAMP / product["filename"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), product["sha256"])
            self.assertEqual(struct.unpack(">II", data[16:24]), (product["width"], product["height"]))
            (south, west), (north, east) = product["bounds"]
            self.assertTrue(south < 37.6545 < north and west < -97.4431 < east)
            # Sweeps start after the volume and within it, but a repeated low tilt (SAILS) can start after
            # higher ones: here e0 starts at 00:00:50 on May 20, e1 at 23:58:50 on May 19.
            start = datetime.fromisoformat(product["sweep_start"].replace("Z", "+00:00"))
            self.assertTrue(volume_start <= start < volume_start + timedelta(minutes=10), product["sweep_start"])
            if field == "reflectivity":
                angles.append(product["elevation_degrees"])
                self.assertEqual(product["color_scale"], {"colormap": "Standard", "minimum": -32.0, "maximum": 95.0})
        self.assertEqual(angles, sorted(angles))
        self.assertGreater(products[("reflectivity", 0)]["sweep_start"], products[("reflectivity", 1)]["sweep_start"])

    @unittest.skipIf(np is None, "numpy not installed")
    def test_grid_is_made_from_the_polar_volume(self):
        polar, grid = self.manifest["volumes"]
        folder = REAL / "KICT" / STAMP
        self.assertEqual(grid["derived_from"], polar["filename"])
        p = volume.unpack(polar, (folder / polar["filename"]).read_bytes())
        g = volume.unpack(grid, (folder / grid["filename"]).read_bytes())["reflectivity"]
        self.assertEqual(sum(s["rays"] for s in polar["sweeps"]), p["reflectivity"].shape[0])
        self.assertEqual(g.shape, (grid["z"]["count"], grid["y"]["count"], grid["x"]["count"]))
        # The polar volume covers every gate it stores; the grid has all four kinds of cell.
        self.assertEqual(int((p["reflectivity"] == volume.NO_COVERAGE).sum()), 0)
        for code in (volume.NO_COVERAGE, volume.NO_ECHO, volume.CLUTTER):
            self.assertGreater(int((g == code).sum()), 0)
        self.assertGreater(float(np.nanmax(volume.decode(g))), 60)
        placed = [(float(np.median(p["elevation"][s["ray_start"]:s["ray_start"] + s["rays"]])), s["ray_start"], s["rays"])
                  for s in polar["sweeps"]]
        levels = grid["z"]["first_m"] + grid["z"]["spacing_m"] * np.arange(grid["z"]["count"])
        remade, _ = volume.grid_from_polar(p["reflectivity"], p["azimuth"], placed, polar["range"]["first_gate_m"],
                                           polar["range"]["gate_spacing_m"], polar["radar"]["altitude_m"],
                                           grid["x"]["spacing_m"], levels, -grid["x"]["first_m"])
        self.assertTrue(np.array_equal(remade, g))

    def test_revised_volume_keeps_its_identity(self):
        revised = load(FIXTURES / "revised/KICT" / STAMP / "cycle.json")
        self.assertEqual((revised["id"], revised["revision"], self.manifest["revision"]), (STAMP, 2, 1))
        self.assertGreater(revised["published_at"], self.manifest["published_at"])
        self.assertEqual([p.get("sha256") for p in revised["products"] + revised["volumes"]],
                         [p.get("sha256") for p in self.manifest["products"] + self.manifest["volumes"]])
        self.assertEqual(load(FIXTURES / "revised/KICT/index.json")["cycles"][0], revised)


class MooreTornadoTests(unittest.TestCase):
    """KTLX, May 20, 2013, 20:20:58 UTC: the EF5 tornado crossing central Moore, Oklahoma."""
    FOLDER = REAL / "KTLX/20130520_202058"

    def setUp(self):
        self.manifest = load(self.FOLDER / "cycle.json")
        self.case = load(FIXTURES / "cases/KTLX_20130520_202058_debris.json")

    def test_cycle(self):
        check_cycle(self.manifest)
        self.assertEqual(self.manifest["volume_start"], "2013-05-20T20:20:58+00:00")
        self.assertTrue(self.manifest["source_key"].endswith("KTLX20130520_202058_V06.gz"))
        self.assertEqual(self.case["sweep"]["sweep_start"][:16], "2013-05-20T20:20")
        # The debris lies in central Moore, about 20 km west of the radar.
        self.assertLess(abs(self.case["centroid"][0] - 35.34) + abs(self.case["centroid"][1] + 97.48), 0.05)

    @unittest.skipIf(np is None, "numpy not installed")
    def test_clutter_filter_keeps_the_debris_signature(self):
        polar, grid = self.manifest["volumes"]
        self.assertEqual(polar["clutter_filter"], {"min_correlation": 0.85, "clutter_max_dbz": 35.0})
        self.assertTrue(polar["sweeps"][0]["clutter_filtered"])
        self.assertGreater(self.case["count"], 100)
        self.assertLess(self.case["min_correlation"], 0.5)
        codes = volume.unpack(polar, (self.FOLDER / polar["filename"]).read_bytes())["reflectivity"]
        start = self.case["sweep"]["ray_start"]
        rays, gates = np.array(self.case["gates"]).T
        debris = codes[start + rays, gates]
        # Low CC with strong reflectivity is debris, not clutter: every gate keeps its value.
        self.assertEqual(int((debris < volume.FIRST_VALUE).sum()), 0)
        self.assertGreaterEqual(float(volume.decode(debris).min()), 35.0)
        self.assertEqual(float(volume.decode(debris).max()), self.case["max_dbz"])

        # The grid's lowest covered level over the debris has strong echo.
        g = volume.unpack(grid, (self.FOLDER / grid["filename"]).read_bytes())["reflectivity"]
        lat0, lon0 = grid["radar"]["latitude"], grid["radar"]["longitude"]
        lat, lon = self.case["centroid"]
        x = (lon - lon0) * 111.2e3 * np.cos(np.radians(lat0))
        y = (lat - lat0) * 111.2e3
        column = lambda first, spacing, value: int(round((value - first) / spacing))
        i = column(grid["x"]["first_m"], grid["x"]["spacing_m"], x)
        j = column(grid["y"]["first_m"], grid["y"]["spacing_m"], y)
        self.assertTrue(-24e3 < x < -18e3 and abs(y) < 3e3)
        near = g[:, j - 1:j + 2, i - 1:i + 2]
        lowest = next(level for level in near if (level > volume.NO_COVERAGE).any())
        self.assertGreaterEqual(float(np.nanmax(volume.decode(lowest))), 50.0)

    @unittest.skipIf(np is None, "numpy not installed")
    def test_images_show_the_debris(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        import math
        lat, lon = self.case["centroid"]
        mercator = lambda la: math.log(math.tan(math.pi / 4 + math.radians(la) / 2))
        for field in ("reflectivity", "correlation_coefficient"):
            product = next(p for p in self.manifest["products"] if p["field"] == field and p["elevation_index"] == 0)
            (south, west), (north, east) = product["bounds"]
            column = int((lon - west) / (east - west) * product["width"])
            row = int((mercator(north) - mercator(lat)) / (mercator(north) - mercator(south)) * product["height"])
            with Image.open(self.FOLDER / product["filename"]) as image:
                alpha = np.asarray(image.convert("RGBA"))[row - 2:row + 3, column - 2:column + 3, 3]
            self.assertGreater(int((alpha == 255).sum()), 10, field)


@unittest.skipIf(np is None, "numpy not installed")
class SyntheticFixtureTests(unittest.TestCase):
    def test_expected_features(self):
        document = load(FIXTURES / "synthetic/volumes.json")
        self.assertTrue(document["synthetic"])
        polar, grid = document["volumes"]
        volume.unpack(polar, (FIXTURES / "synthetic" / polar["filename"]).read_bytes())
        g = volume.unpack(grid, (FIXTURES / "synthetic" / grid["filename"]).read_bytes())["reflectivity"]
        self.assertEqual(document["expected_features"][0]["dbz"], 40.0)
        self.assertEqual([f["code"] for f in document["expected_features"]],
                         [int(volume.encode(np.array([40.0]))[0]), volume.CLUTTER, volume.NO_ECHO,
                          volume.NO_COVERAGE, volume.NO_COVERAGE])
        for feature in document["expected_features"]:
            self.assertEqual(int(g[tuple(feature["index_zyx"])]), feature["code"], feature["description"])


class InvalidFixtureTests(unittest.TestCase):
    def test_each_payload_is_rejected_with_its_documented_reason(self):
        records = {r["path"]: r for r in load(FIXTURES / "provenance.json")["fixtures"] if r["product"] == "invalid payload"}
        self.assertEqual(len(records), 6)
        invalid = FIXTURES / "invalid"
        with self.assertRaises(json.JSONDecodeError):
            load(invalid / "truncated_cycle.json")
        with self.assertRaisesRegex(ValueError, "unsupported schema_version 99"):
            check_index(load(invalid / "unknown_schema_index.json"))
        with self.assertRaisesRegex(ValueError, re.escape(records["invalid/bad_site_index.json"]["expected_rejection"])):
            check_index(load(invalid / "bad_site_index.json"))
        with self.assertRaisesRegex(ValueError, re.escape(records["invalid/missing_fields_cycle.json"]["expected_rejection"])):
            check_cycle(load(invalid / "missing_fields_cycle.json"))
        if volume is None:
            return
        grid = load(FIXTURES / "synthetic/volumes.json")["volumes"][1]
        for name, unchecked in (("truncated_grid.bin.gz", "not a complete gzip stream"),
                                ("empty_grid.bin.gz", "0 bytes uncompressed")):
            data = (invalid / name).read_bytes()
            with self.assertRaisesRegex(ValueError, "sha256 .* does not match the manifest's"):
                volume.unpack(grid, data)
            with self.assertRaisesRegex(ValueError, unchecked):
                volume.unpack({**grid, "sha256": None}, data)


if __name__ == "__main__":
    unittest.main()
