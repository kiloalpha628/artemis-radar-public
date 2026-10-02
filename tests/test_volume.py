import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import numpy as np
    import volume
except ImportError:
    np = volume = None


def synthetic_polar(lower_dbz=40.0, upper_dbz=40.0):
    """Two sweeps (0.5 and 1.5 degrees), 360 rays, 1 km gates from 0.5 km. An echo
    east of the radar, 20-30 km out; clutter 24-28 km west; no echo elsewhere."""
    codes = np.full((720, 100), volume.NO_ECHO, dtype=np.uint8)
    azimuth = np.tile(np.arange(360, dtype=np.float32) + 0.5, 2)
    for start, dbz in ((0, lower_dbz), (360, upper_dbz)):
        codes[start + 85:start + 95, 20:30] = volume.encode(np.array([dbz]))[0]
        codes[start + 268:start + 272, 24:28] = volume.CLUTTER
    sweeps = [(0.5, 0, 360), (1.5, 360, 360)]
    return codes, azimuth, sweeps


@unittest.skipIf(np is None, "numpy not installed")
class EncodingTests(unittest.TestCase):
    def test_codes_round_trip_and_flags(self):
        dbz = np.ma.masked_invalid(np.array([-31.5, 0.0, 10.5, 94.5, 120.0, -40.0, np.nan, 20.0]))
        clutter = np.array([False] * 6 + [True, True])
        codes = volume.encode(dbz, clutter)
        # Out-of-range values saturate; clutter only replaces a gate that had a value.
        self.assertEqual(codes.tolist(), [3, 66, 87, 255, 255, 3, volume.NO_ECHO, volume.CLUTTER])
        self.assertEqual(volume.decode(codes[:4]).tolist(), [-31.5, 0.0, 10.5, 94.5])
        self.assertTrue(np.isnan(volume.decode(np.array([0, 1, 2]))).all())

    def test_beam_geometry_inverts_the_standard_beam_equation(self):
        radius = volume.EFFECTIVE_EARTH_RADIUS_M
        for elevation in (0.5, 3.0, 19.5):
            for slant in (10e3, 120e3, 230e3):
                theta = np.radians(elevation)
                height = np.sqrt(slant ** 2 + radius ** 2 + 2 * slant * radius * np.sin(theta)) - radius
                ground = radius * np.arcsin(slant * np.cos(theta) / (radius + height))
                back_slant, back_height = volume.beam_geometry(ground, elevation)
                self.assertAlmostEqual(float(back_slant), slant, delta=0.01)
                self.assertAlmostEqual(float(back_height), height, delta=0.01)


@unittest.skipIf(np is None, "numpy not installed")
class GridTests(unittest.TestCase):
    def grid(self, codes, azimuth, sweeps, levels, altitude=400.0):
        return volume.grid_from_polar(codes, azimuth, sweeps, 500.0, 1000.0, altitude, 2000.0, levels, 60000.0)

    def at(self, grid, axis, x_km, y_km, level):
        return int(grid[level, int(np.searchsorted(axis, y_km * 1000)), int(np.searchsorted(axis, x_km * 1000))])

    def test_features_land_at_their_position_and_height(self):
        codes, azimuth, sweeps = synthetic_polar()
        # At 24 km the 0.5 and 1.5 degree beams are near 0.64 and 1.06 km above a
        # 400 m antenna; 1.0 km lies between them.
        grid, axis = self.grid(codes, azimuth, sweeps, [1000.0, 5000.0])
        self.assertEqual(grid.shape, (2, 61, 61))
        self.assertEqual(axis[30], 0.0)
        self.assertEqual(volume.decode(self.at(grid, axis, 24, 0, 0)), 40.0)  # east: x is the last axis
        self.assertEqual(self.at(grid, axis, 0, 24, 0), volume.NO_ECHO)       # north: y rows, south first
        self.assertEqual(self.at(grid, axis, -26, 0, 0), volume.CLUTTER)      # the west clutter gate
        self.assertEqual(self.at(grid, axis, 24, 0, 1), volume.NO_COVERAGE)   # 5 km: above the top beam
        self.assertEqual(self.at(grid, axis, 60, 60, 0), volume.NO_COVERAGE)  # corner: beyond range

    def test_interpolates_in_height_only_between_two_values(self):
        codes, azimuth, sweeps = synthetic_polar(lower_dbz=30.0, upper_dbz=50.0)
        low = float(volume.beam_geometry(24000, 0.5)[1]) + 400
        high = float(volume.beam_geometry(24000, 1.5)[1]) + 400
        grid, axis = self.grid(codes, azimuth, sweeps, [(low + high) / 2, low + 0.25 * (high - low)])
        self.assertEqual(volume.decode(self.at(grid, axis, 24, 0, 0)), 40.0)
        self.assertEqual(volume.decode(self.at(grid, axis, 24, 0, 1)), 35.0)
        codes[360 + 85:360 + 95, 20:30] = volume.NO_ECHO
        grid, axis = self.grid(codes, azimuth, sweeps, [low + 0.25 * (high - low), low + 0.75 * (high - low)])
        # No echo aloft: nearest beam, never a value blended with a missing one.
        self.assertEqual(volume.decode(self.at(grid, axis, 24, 0, 0)), 30.0)
        self.assertEqual(self.at(grid, axis, 24, 0, 1), volume.NO_ECHO)

    def test_below_the_lowest_beam_is_no_coverage(self):
        codes, azimuth, sweeps = synthetic_polar()
        codes[:, :] = volume.encode(np.array([20.0]))[0]
        # 58 km out, the 0.5 degree beam's lower edge is ~200 m above a 0 m antenna.
        grid, axis = self.grid(codes, azimuth, sweeps, [100.0, 700.0], altitude=0.0)
        self.assertEqual(self.at(grid, axis, 58, 0, 0), volume.NO_COVERAGE)
        self.assertEqual(volume.decode(self.at(grid, axis, 58, 0, 1)), 20.0)


@unittest.skipIf(np is None, "numpy not installed")
class PackTests(unittest.TestCase):
    def test_round_trip_and_rejections(self):
        codes = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
        angles = np.array([0.5, 1.5], dtype=np.float32)
        data, descriptor = volume.pack([("reflectivity", codes, {"axes": ["z", "y", "x"]}), ("angle", angles, {})])
        descriptor["filename"] = "test.bin.gz"
        arrays = volume.unpack(descriptor, data)
        self.assertTrue(np.array_equal(arrays["reflectivity"], codes))
        self.assertEqual(arrays["angle"].dtype.str, "<f4")
        self.assertEqual(descriptor["arrays"][1]["offset"], 24)
        self.assertEqual(volume.pack([("reflectivity", codes, {})])[0], volume.pack([("reflectivity", codes, {})])[0])
        with self.assertRaisesRegex(ValueError, "sha256"):
            volume.unpack(descriptor, data[:-5])
        with self.assertRaisesRegex(ValueError, "not a complete gzip stream"):
            volume.unpack({**descriptor, "sha256": None}, data[:-5])
        with self.assertRaisesRegex(ValueError, "uncompressed"):
            volume.unpack({**descriptor, "uncompressed_bytes": 10}, data)
        wrong = {**descriptor, "arrays": [{**descriptor["arrays"][0], "shape": [2, 3, 5]}]}
        with self.assertRaisesRegex(ValueError, "does not fit"):
            volume.unpack(wrong, data)


if __name__ == "__main__":
    unittest.main()
