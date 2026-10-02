import json
import os
from pathlib import Path
import tempfile
import functools
import threading
import urllib.request
import urllib.error
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from render import select_sweeps


class CacheTests(unittest.TestCase):
    def test_one_site_failure_does_not_block_remaining_sites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def listing(client, site, now, keep):
                if site == "KICT":
                    raise OSError("upstream unavailable")
                return [{"id": "20260926_120000", "key": "source"}]
            def process(root, site, obj, client, size, range_km, min_reflectivity=None, render_options=None):
                app.atomic_json(root / "public" / site / obj["id"] / "cycle.json", {"id": obj["id"]})
            env = {"RADAR_SITES": "KICT,KVNX,KDDC,KTWX,KGLD,KEAX", "MIN_FREE_GB": "0"}
            with patch.dict(os.environ, env), patch("app.PublicS3"), patch("app.list_recent", side_effect=listing), patch("app.process_volume", side_effect=process), self.assertLogs("radar", level="ERROR"):
                app.worker(root, once=True)
            self.assertIn("upstream unavailable", json.loads((root / "public/KICT/index.json").read_text())["state"]["error"])
            for site in ("KVNX", "KDDC", "KTWX", "KGLD", "KEAX"):
                self.assertEqual(len(json.loads((root / "public" / site / "index.json").read_text())["cycles"]), 1)
            self.assertTrue((root / "heartbeat").exists())

    def test_http_serves_only_public_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app.atomic_json(root / "public/index.json", {"sites": ["KICT"]})
            (root / "raw").mkdir()
            (root / "raw/secret").write_text("not public")
            handler = functools.partial(app.Handler, directory=str(root / "public"))
            server = app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(base + "/") as response:
                    self.assertEqual(json.load(response), {"sites": ["KICT"]})
                with urllib.request.urlopen(base + "/healthz") as response:
                    self.assertEqual(response.status, 200)
                for path in ("/raw/secret", "/../raw/secret", "/%2e%2e/raw/secret"):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(base + path)
                    self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_midnight_and_metadata_files(self):
        class FakeClient:
            prefixes = []
            def get_paginator(self, name):
                return self
            def paginate(self, Bucket, Prefix):
                self.prefixes.append(Prefix)
                names = ["KICT20260925_235500_V06", "KICT20260925_235500_MDM"] if "25/" in Prefix else ["KICT20260926_000100_V06"]
                yield {"Contents": [{"Key": Prefix + name, "Size": 100, "LastModified": datetime.now(timezone.utc)} for name in names]}
        client = FakeClient()
        result = app.list_recent(client, "KICT", datetime(2026, 9, 26, tzinfo=timezone.utc), 10)
        self.assertEqual([v["id"] for v in result], ["20260926_000100", "20260925_235500"])
        self.assertEqual(len(client.prefixes), 2)

    def test_retention_preserves_latest_ten_and_other_sites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for i in range(12):
                stamp = f"20260926_{i:02d}0000"
                app.atomic_json(root / "public/KICT" / stamp / "cycle.json", {"id": stamp})
                (root / "raw/KICT").mkdir(parents=True, exist_ok=True)
                (root / "raw/KICT" / stamp).write_bytes(b"raw")
            other = root / "raw/KEAX/keep"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"safe")
            app.publish_index(root, "KICT", 10, {})
            index = json.loads((root / "public/KICT/index.json").read_text())
            self.assertEqual(len(index["cycles"]), 10)
            self.assertEqual(index["cycles"][0]["id"], "20260926_110000")
            self.assertEqual(len(list((root / "raw/KICT").iterdir())), 10)
            self.assertTrue(other.exists())
            self.assertFalse((root / "public/KICT/20260926_000000").exists())

    def test_rejects_invalid_site_and_accepts_seven(self):
        with patch.dict(os.environ, {"RADAR_SITES": "KICT,KVNX,KDDC,KTWX,KGLD,KEAX,KUEX"}):
            self.assertEqual(len(app.sites_from_env()), 7)
        too_many = ",".join(f"K{chr(65 + i)}AA" for i in range(app.MAX_SITES + 1))
        with patch.dict(os.environ, {"RADAR_SITES": too_many}), self.assertRaises(ValueError):
            app.sites_from_env()
        with patch.dict(os.environ, {"RADAR_SITES": "../../escape"}):
            with self.assertRaises(ValueError):
                app.sites_from_env()

    def test_split_cuts_and_repeat_sweeps(self):
        class Mask:
            def __init__(self, n): self.n = n
            def count(self): return self.n
        class Radar:
            fixed_angle = {"data": [0.5, 0.5, 0.9, 0.9, 1.3, 1.8, 0.5]}
            fields = {"reflectivity": {}, "velocity": {}}
            def get_field(self, sweep, field):
                valid = {"reflectivity": {0, 2, 4, 5, 6}, "velocity": {1, 3, 4, 5}}
                return Mask(100 if sweep in valid[field] else 0)
        result = select_sweeps(Radar())
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["fields"], {"reflectivity": 6, "velocity": 1})
        self.assertEqual(result[1]["fields"], {"reflectivity": 2, "velocity": 3})

    def test_failed_render_does_not_replace_published_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app.atomic_json(root / "public/KICT/20260926_120000/cycle.json", {"id": "20260926_120000"})
            class FakeClient:
                def download_file(self, bucket, key, path): Path(path).write_bytes(b"raw")
            obj = {"id": "20260926_120500", "key": "test", "size": 3}
            with patch("render.render_volume", side_effect=ValueError("bad data")):
                with self.assertRaises(ValueError):
                    app.process_volume(root, "KICT", obj, FakeClient(), 512, 230)
            self.assertTrue((root / "public/KICT/20260926_120000/cycle.json").exists())
            self.assertFalse((root / "public/KICT/20260926_120500").exists())
            self.assertFalse((root / "tmp/KICT").exists())


class ProvenanceTests(unittest.TestCase):
    OBJ = {"id": "20260926_120500", "key": "2026/09/26/KICT/KICT20260926_120500_V06", "size": 3,
           "published_at": "2026-09-26T12:10:00+00:00"}

    @staticmethod
    def fake_render(marker):
        def render(source, destination, *args, **kwargs):
            (destination / "e0_reflectivity.png").write_bytes(marker)
            (destination / "volume_grid_reflectivity.bin.gz").write_bytes(marker * 2)
            return {"products": [{"field": "reflectivity", "elevation_index": 0, "available": True,
                                  "filename": "e0_reflectivity.png"}],
                    "volumes": [{"kind": "grid", "available": True, "filename": "volume_grid_reflectivity.bin.gz"}]}
        return render

    class Client:
        def download_file(self, bucket, key, path): Path(path).write_bytes(b"raw")

    def test_manifest_records_versions_checksums_and_times(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("render.render_volume", side_effect=self.fake_render(b"png")):
                app.process_volume(root, "KICT", self.OBJ, self.Client(), 512, 230, 10.0, {"dealias": True})
            manifest = json.loads((root / "public/KICT/20260926_120500/cycle.json").read_text())
            self.assertEqual((manifest["schema_version"], manifest["revision"]), (app.SCHEMA_VERSION, 1))
            self.assertEqual(manifest["source_sha256"], hashlib.sha256(b"raw").hexdigest())
            self.assertEqual(manifest["processing"]["version"], app.PROCESSING_VERSION)
            self.assertEqual(manifest["processing"]["settings"]["min_reflectivity_dbz"], 10.0)
            self.assertFalse(manifest["processing"]["settings"]["storm_relative_velocity"])
            self.assertLessEqual(manifest["downloaded_at"], manifest["rendered_at"])
            product, grid = manifest["products"][0], manifest["volumes"][0]
            self.assertEqual((product["sha256"], product["bytes"]), (hashlib.sha256(b"png").hexdigest(), 3))
            self.assertEqual((grid["url"], grid["bytes"]), ("/KICT/20260926_120500/volume_grid_reflectivity.bin.gz", 6))
            self.assertEqual(manifest["volume_bytes"], 6)

    def test_revised_volume_replaces_the_cycle_as_a_new_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cycle = root / "public/KICT/20260926_120500"
            with patch("render.render_volume", side_effect=self.fake_render(b"old")):
                app.process_volume(root, "KICT", self.OBJ, self.Client(), 512, 230)
            (cycle / "stale.png").write_bytes(b"from revision 1")
            revised = {**self.OBJ, "published_at": "2026-09-26T13:00:00+00:00"}
            manifest = json.loads((cycle / "cycle.json").read_text())
            self.assertFalse(app.is_revised(manifest, self.OBJ))
            self.assertTrue(app.is_revised(manifest, revised))
            self.assertTrue(app.is_revised(manifest, {**self.OBJ, "size": 4}))
            with patch("render.render_volume", side_effect=self.fake_render(b"new")):
                app.process_volume(root, "KICT", revised, self.Client(), 512, 230)
            manifest = json.loads((cycle / "cycle.json").read_text())
            self.assertEqual((manifest["revision"], manifest["published_at"]), (2, revised["published_at"]))
            self.assertEqual((cycle / "e0_reflectivity.png").read_bytes(), b"new")
            self.assertFalse((cycle / "stale.png").exists())
            self.assertFalse((root / "tmp/KICT").exists())

    def test_worker_reprocesses_only_revised_volumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app.atomic_json(root / "public/KICT" / self.OBJ["id"] / "cycle.json",
                            {"id": self.OBJ["id"], "published_at": self.OBJ["published_at"], "source_bytes": 3})
            for listed, calls in ((self.OBJ, 0), ({**self.OBJ, "published_at": "2026-09-26T13:00:00+00:00"}, 1)):
                env = {"RADAR_SITES": "KICT", "MIN_FREE_GB": "0", "STORM_RELATIVE_VELOCITY": "false"}
                with patch.dict(os.environ, env), patch("app.PublicS3"), \
                        patch("app.list_recent", return_value=[listed]), patch("app.process_volume") as process:
                    app.worker(root, once=True)
                self.assertEqual(process.call_count, calls)
            index = json.loads((root / "public/index.json").read_text())
            self.assertEqual(index["schema_version"], app.SCHEMA_VERSION)
            self.assertEqual(json.loads((root / "public/KICT/index.json").read_text())["schema_version"], app.SCHEMA_VERSION)


class ThresholdTests(unittest.TestCase):
    def test_min_reflectivity_setting(self):
        with patch.dict(os.environ, {"MIN_REFLECTIVITY_DBZ": "5"}):
            self.assertEqual(app.min_reflectivity_from_env(), 5.0)
        with patch.dict(os.environ, {"MIN_REFLECTIVITY_DBZ": ""}):
            self.assertIsNone(app.min_reflectivity_from_env())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MIN_REFLECTIVITY_DBZ", None)
            self.assertEqual(app.min_reflectivity_from_env(), 10.0)
        with patch.dict(os.environ, {"MIN_REFLECTIVITY_DBZ": "90"}), self.assertRaises(ValueError):
            app.min_reflectivity_from_env()

    def test_threshold_reaches_the_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class FakeClient:
                def download_file(self, bucket, key, path): Path(path).write_bytes(b"raw")
            obj = {"id": "20260926_120500", "key": "test", "size": 3}
            with patch("render.render_volume", side_effect=ValueError("stop")) as render:
                with self.assertRaises(ValueError):
                    app.process_volume(root, "KICT", obj, FakeClient(), 512, 230, 10.0)
            self.assertEqual(render.call_args.args[4], 10.0)

    def test_mask_below_hides_weak_returns(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        from render import mask_below
        values = np.ma.masked_invalid(np.array([[-5.0, 9.9, 10.0, 45.0, np.nan]]))
        masked = mask_below(values, 10.0)
        self.assertEqual(masked.mask.tolist(), [[True, True, False, False, True]])
        self.assertIs(mask_below(values, None), values)


class DualPolTests(unittest.TestCase):
    def test_reflectivity_prefers_the_surveillance_cut_with_cc(self):
        class Mask:
            def __init__(self, n): self.n = n
            def count(self): return self.n
        class Radar:
            # 0.5° split cut (surveillance 0, Doppler 1), then a repeated surveillance 2.
            fixed_angle = {"data": [0.5, 0.5, 0.5, 0.9, 0.9]}
            fields = {"reflectivity": {}, "velocity": {}, "cross_correlation_ratio": {}}
            def get_field(self, sweep, field):
                valid = {"reflectivity": {0, 1, 2, 3, 4}, "velocity": {1, 4}, "cross_correlation_ratio": {0, 2, 3}}
                return Mask(100 if sweep in valid[field] else 0)
        result = select_sweeps(Radar())
        self.assertEqual(result[0]["fields"], {"reflectivity": 2, "velocity": 1, "cross_correlation_ratio": 2})
        self.assertEqual(result[1]["fields"], {"reflectivity": 3, "velocity": 4, "cross_correlation_ratio": 3})

    def test_render_options_from_env(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in ("MIN_CORRELATION", "CLUTTER_MAX_DBZ", "DEALIAS_VELOCITY", "STORM_RELATIVE_VELOCITY"):
                os.environ.pop(key, None)
            options = app.render_options_from_env()
            self.assertEqual({k: options[k] for k in ("min_correlation", "clutter_max_dbz", "dealias")},
                             {"min_correlation": 0.85, "clutter_max_dbz": 35.0, "dealias": True})
            self.assertTrue(callable(options["storm_motion"]))
        with patch.dict(os.environ, {"MIN_CORRELATION": "", "DEALIAS_VELOCITY": "false", "STORM_RELATIVE_VELOCITY": "false"}):
            options = app.render_options_from_env()
            self.assertEqual((options["min_correlation"], options["dealias"], "storm_motion" in options), (None, False, False))
        with patch.dict(os.environ, {"MIN_CORRELATION": "1.5"}), self.assertRaises(ValueError):
            app.render_options_from_env()

    def test_volume_options_from_env(self):
        with patch.dict(os.environ, {"STORM_RELATIVE_VELOCITY": "false"}):
            for key in ("VOLUME_PRODUCTS", "GRID_SPACING_KM", "GRID_LEVEL_M"):
                os.environ.pop(key, None)
            options = app.render_options_from_env()
            self.assertEqual((options["volume_products"], options["grid_spacing_km"], options["grid_level_m"]),
                             (True, 1.0, 500.0))
        with patch.dict(os.environ, {"STORM_RELATIVE_VELOCITY": "false", "VOLUME_PRODUCTS": "false"}):
            self.assertFalse(app.render_options_from_env()["volume_products"])
        for key, value in (("GRID_SPACING_KM", "0.25"), ("GRID_LEVEL_M", "50")):
            with patch.dict(os.environ, {"STORM_RELATIVE_VELOCITY": "false", key: value}), self.assertRaises(ValueError):
                app.render_options_from_env()

    def test_options_reach_the_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class FakeClient:
                def download_file(self, bucket, key, path): Path(path).write_bytes(b"raw")
            obj = {"id": "20260926_120500", "key": "test", "size": 3}
            options = {"min_correlation": 0.9, "clutter_max_dbz": 30.0, "dealias": True}
            with patch("render.render_volume", side_effect=ValueError("stop")) as render:
                with self.assertRaises(ValueError):
                    app.process_volume(root, "KICT", obj, FakeClient(), 512, 230, 10.0, options)
            self.assertEqual(render.call_args.kwargs, options)

    def test_nearest_rays_wrap_at_north(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        from render import nearest_rays
        source = np.array([0.26, 90.25, 180.24, 270.26, 359.76])
        self.assertEqual(nearest_rays([0.2, 359.9, 91.0, 269.9, 180.0], source).tolist(), [0, 4, 1, 3, 2])

    def test_clutter_mask_keeps_strong_low_cc_returns(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        from render import clutter_mask
        dbz = np.ma.masked_invalid(np.array([[12.0, 12.0, 50.0, 12.0, np.nan]]))
        cc = np.ma.masked_invalid(np.array([[0.60, 0.97, 0.60, np.nan, 0.50]]))
        # Weak + low CC is clutter; clean echo, strong debris/hail, and missing CC are kept.
        self.assertEqual(clutter_mask(dbz, cc, 0.85, 35.0).tolist(), [[True, False, False, False, True]])
        self.assertFalse(clutter_mask(dbz, cc, None, 35.0).any())
        self.assertFalse(clutter_mask(dbz, None, 0.85, 35.0).any())


class ImageContentsTests(unittest.TestCase):
    def test_dockerfile_copies_every_application_module(self):
        root = Path(__file__).resolve().parent.parent
        copied = set()
        for line in (root / "Dockerfile").read_text().splitlines():
            if line.startswith("COPY "):
                copied.update(line.split()[1:-1])
        modules = {path.name for path in root.glob("*.py")}
        self.assertEqual(modules - copied, set())


class PaletteTests(unittest.TestCase):
    def test_standard_scale_matches_the_readings_it_was_calibrated_with(self):
        from palettes import STANDARD_COLORS, STANDARD_MAX_DBZ, STANDARD_MIN_DBZ
        self.assertEqual(len(STANDARD_COLORS), 254)
        step = (STANDARD_MAX_DBZ - STANDARD_MIN_DBZ) / len(STANDARD_COLORS)
        color = lambda dbz: STANDARD_COLORS[int((dbz - STANDARD_MIN_DBZ) / step)]
        # RadarScope's own readings: 20 green, 40.5 orange, 50 red, 70 purple, 80 cyan, 90 brown.
        self.assertEqual(color(20.3), "#2db64e")
        self.assertEqual(color(40.5), "#f69000")
        self.assertEqual(color(50.2), "#f9230b")
        self.assertEqual(color(70.2), "#9a24e0")
        self.assertEqual(color(80.2), "#84fdff")
        self.assertEqual(color(90.2), "#a16549")

    def test_styles_and_setting(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib not installed")
        from render import reflectivity_style
        self.assertEqual(reflectivity_style("NWSRef"), ("NWSRef", -20, 80, "dBZ"))
        name, low, high, units = reflectivity_style("Standard")
        self.assertEqual((name, low, high, units), ("Standard", -32.0, 95.0, "dBZ"))
        import matplotlib as mpl
        cmap = mpl.colormaps["Standard"]
        self.assertEqual(cmap(0)[3], 0.0)  # below 0 dBZ: not drawn
        self.assertEqual(cmap.N, 254)
        with self.assertRaises(ValueError):
            reflectivity_style("Rainbow")
        with patch.dict(os.environ, {"REFLECTIVITY_PALETTE": ""}):
            self.assertEqual(app.render_options_from_env()["reflectivity_palette"], "Standard")
        with patch.dict(os.environ, {"REFLECTIVITY_PALETTE": "NWSRef"}):
            self.assertEqual(app.render_options_from_env()["reflectivity_palette"], "NWSRef")
        with patch.dict(os.environ, {"REFLECTIVITY_PALETTE": "Pink"}):
            with self.assertRaises(ValueError):
                app.render_options_from_env()


class StormRelativeTests(unittest.TestCase):
    def test_bunkers_right_mover(self):
        from storm_motion import bunkers_right, from_uv, to_uv
        # Westerly shear: 5 m/s from the south at the ground to 25 m/s from the west aloft.
        winds = [to_uv(5, 180), to_uv(15, 225), to_uv(25, 270)]
        u, v = bunkers_right(winds)
        mean_u = sum(w[0] for w in winds) / 3
        mean_v = sum(w[1] for w in winds) / 3
        # Deviation of 7.5 m/s, to the right of the shear (southward here).
        self.assertAlmostEqual(((u - mean_u) ** 2 + (v - mean_v) ** 2) ** 0.5, 7.5, places=6)
        self.assertLess(v, mean_v)
        speed, direction = from_uv(*to_uv(10, 250))
        self.assertAlmostEqual(speed, 10)
        self.assertAlmostEqual(direction, 250)
        # Weak shear: the mean wind.
        self.assertEqual(bunkers_right([to_uv(10, 270), to_uv(10.2, 270)]), tuple(sum(c) / 2 for c in zip(to_uv(10, 270), to_uv(10.2, 270))))

    def test_winds_between_ground_and_six_km(self):
        from datetime import datetime, timezone
        from storm_motion import winds_from_forecast
        hourly = {"time": ["2026-09-26T18:00", "2026-09-26T19:00"], "wind_speed_10m": [3, 4], "wind_direction_10m": [180, 190]}
        for level, height in ((925, 750), (850, 1500), (700, 3100), (600, 4400), (500, 5800), (400, 7400)):
            hourly[f"wind_speed_{level}hPa"] = [10, 11]
            hourly[f"wind_direction_{level}hPa"] = [250, 255]
            hourly[f"geopotential_height_{level}hPa"] = [height, height]
        document = {"elevation": 1100, "hourly": hourly}
        winds = winds_from_forecast(document, datetime(2026, 9, 26, 18, 50, tzinfo=timezone.utc))
        # 10 m, 850, 700 and 500 hPa: 925 hPa (750 m) is below this 1100 m site,
        # 400 hPa (7400 m) is more than 6 km above it, and 600 hPa is not requested.
        self.assertEqual(len(winds), 4)
        with self.assertRaises(ValueError):
            winds_from_forecast(document, datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc))

    def test_storm_motion_keeps_last_forecast_and_retries_later(self):
        from datetime import datetime, timezone
        from storm_motion import REFRESH, RETRY, StormMotion
        hourly = {"time": ["2026-09-26T18:00", "2026-09-26T19:00"], "wind_speed_10m": [3, 3], "wind_direction_10m": [180, 180]}
        for level, height in ((850, 1500), (700, 3100), (500, 5800)):
            hourly[f"wind_speed_{level}hPa"] = [20, 20]
            hourly[f"wind_direction_{level}hPa"] = [250, 250]
            hourly[f"geopotential_height_{level}hPa"] = [height, height]
        calls, clock, online = [], [0.0], [False]
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return {"elevation": 400, "hourly": hourly}
        class Session:
            def get(self, url, timeout, params):
                calls.append(params)
                if not online[0]:
                    raise OSError("offline")
                return Response()
        motion = StormMotion(Session(), monotonic=lambda: clock[0])
        when = datetime(2026, 9, 26, 18, 50, tzinfo=timezone.utc)
        # Offline with nothing kept: None, and no new attempt until RETRY has passed.
        self.assertIsNone(motion(37.6545, -97.443, when))
        clock[0] = RETRY - 1
        self.assertIsNone(motion(37.6545, -97.443, when))
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["latitude"], calls[0]["longitude"]), (37.65, -97.44))
        self.assertNotIn("wind_speed_600hPa", calls[0]["hourly"])
        online[0] = True
        clock[0] = RETRY
        first = motion(37.6545, -97.443, when)
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(first)
        # Within the hour: no fetch. After it, a failed refresh keeps the last forecast.
        clock[0] = RETRY + REFRESH - 1
        self.assertEqual(motion(37.6545, -97.443, when), first)
        self.assertEqual(len(calls), 2)
        online[0] = False
        clock[0] = RETRY + REFRESH
        self.assertEqual(motion(37.6545, -97.443, when), first)
        self.assertEqual(len(calls), 3)

    def test_storm_relative_subtracts_motion_along_the_beam(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        from render import storm_relative
        velocity = np.zeros((4, 2))
        # Storm moving east at 10 m/s: +10 looking east, -10 looking west, 0 north/south (at 0° elevation).
        result = storm_relative(velocity, [0, 90, 180, 270], [0, 0, 0, 0], 10.0, 0.0)
        self.assertEqual(np.round(result[:, 0], 6).tolist(), [0.0, -10.0, 0.0, 10.0])

    def test_palette_png_is_exact(self):
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            self.skipTest("numpy/Pillow not installed")
        from render import to_palette_png
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            rgba = np.zeros((32, 32, 4), dtype=np.uint8)
            rgba[4:20, 4:20] = [0, 200, 0, 255]
            rgba[10:30, 12:30] = [250, 0, 0, 255]
            Image.fromarray(rgba, "RGBA").save(path)
            self.assertTrue(to_palette_png(path))
            with Image.open(path) as image:
                self.assertEqual(image.mode, "P")
                self.assertTrue(np.array_equal(np.asarray(image.convert("RGBA")), rgba))
            many = np.random.default_rng(1).integers(0, 255, (32, 32, 4), dtype=np.uint8)
            many[..., 3] = 255
            Image.fromarray(many, "RGBA").save(path)
            self.assertFalse(to_palette_png(path))


if __name__ == "__main__":
    unittest.main()
