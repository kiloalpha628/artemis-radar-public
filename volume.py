"""Numeric reflectivity volumes: native polar sweeps and a Cartesian grid made from them.

The polar volume is the cleaned Level II reflectivity at every elevation angle of
the scan, one sweep per angle, on the radar's own rays and range gates. The grid is
made from that polar volume and nothing else, so the pair always agrees.

Both store one byte per value, at Level II's own 0.5 dB resolution:

    0        no coverage: outside every beam, or beyond range
    1        no echo: covered, but Level II has no value there (below the radar's
             signal threshold, or range-folded)
    2        removed as non-meteorological by the dual-polarization clutter filter
    3-255    reflectivity, dBZ = code * 0.5 - 33 (-31.5 to 94.5 dBZ)

No display threshold (MIN_REFLECTIVITY_DBZ) is applied; clients threshold weak
echoes themselves. A missing value is never written as a reflectivity.

A volume file is one gzip stream of little-endian arrays placed end to end; its
descriptor in the cycle manifest gives each array's name, dtype, shape, and offset.
"""
import gzip
import hashlib
import math

import numpy as np

NO_COVERAGE, NO_ECHO, CLUTTER, FIRST_VALUE = 0, 1, 2, 3
ENCODING = {
    "dtype": "uint8", "scale": 0.5, "offset": -33.0, "first_value_code": FIRST_VALUE,
    "formula": "dBZ = code * 0.5 - 33, for code >= 3",
    "flags": {"0": "no_coverage", "1": "no_echo", "2": "clutter"},
}
# 4/3 effective earth radius beam propagation (Doviak and Zrnic), as Py-ART uses.
EFFECTIVE_EARTH_RADIUS_M = 6371000.0 * 4 / 3
# NEXRAD's beam is about 0.95 degrees wide.
HALF_BEAMWIDTH_DEGREES = 0.5
GRID_TOP_M = 15000.0
ALLOWED_DTYPES = {"|u1", "<f4", "<f8", "<i4"}


def encode(reflectivity, clutter=None):
    """uint8 codes for dBZ values; masked/NaN gates are no echo, ``clutter`` gates with a value are clutter."""
    values = np.ma.filled(np.ma.masked_invalid(reflectivity).astype(float), np.nan)
    codes = np.full(values.shape, NO_ECHO, dtype=np.uint8)
    valid = ~np.isnan(values)
    codes[valid] = np.clip(np.round(values[valid] * 2) + 66, FIRST_VALUE, 255)
    if clutter is not None:
        codes[valid & np.asarray(clutter, dtype=bool)] = CLUTTER
    return codes


def decode(codes):
    """dBZ as float, NaN wherever the code is a flag."""
    codes = np.asarray(codes)
    return np.where(codes >= FIRST_VALUE, codes * 0.5 - 33.0, np.nan)


def beam_geometry(ground_m, elevation_degrees):
    """(slant range, height above the antenna) in metres where a beam at this
    elevation passes over a point ``ground_m`` along the ground from the radar."""
    phi = np.asarray(ground_m, dtype=float) / EFFECTIVE_EARTH_RADIUS_M
    theta = np.radians(elevation_degrees)
    denominator = np.cos(theta + phi)
    return (EFFECTIVE_EARTH_RADIUS_M * np.sin(phi) / denominator,
            EFFECTIVE_EARTH_RADIUS_M * np.cos(theta) / denominator - EFFECTIVE_EARTH_RADIUS_M)


def grid_from_polar(codes, azimuth, sweeps, first_gate_m, gate_spacing_m, antenna_altitude_m,
                    spacing_m, levels_m, range_m):
    """Grid polar codes onto (z, y, x): x east and y north of the radar, both
    ``-range_m``..``range_m`` every ``spacing_m``; z the altitudes ``levels_m``.

    ``sweeps`` is [(elevation degrees, first ray, ray count)]. Each column samples
    every sweep at the gate over it (nearest ray and gate). A level between two
    sweeps' beams is interpolated linearly in height when both have reflectivity,
    otherwise it takes the nearer sweep's code. Below the lowest beam or above the
    highest, only the half beamwidth is covered; beyond that and beyond ``range_m``
    is no coverage. There is no terrain blocking.
    """
    from render import nearest_rays
    sweeps = sorted(sweeps)
    count = int(range_m // spacing_m)
    axis = np.arange(-count, count + 1) * float(spacing_m)
    x, y = np.meshgrid(axis, axis)
    ground = np.hypot(x, y).ravel()
    bearing = np.degrees(np.arctan2(x, y)).ravel() % 360
    inside = ground <= range_m
    gates = codes.shape[1]
    values = np.empty((len(sweeps), ground.size), dtype=np.uint8)
    heights = np.empty((len(sweeps), ground.size))
    for k, (angle, start, rays) in enumerate(sweeps):
        slant, height = beam_geometry(ground, angle)
        gate = np.rint((slant - first_gate_m) / gate_spacing_m).astype(int)
        ok = inside & (gate >= 0) & (gate < gates)
        ray = start + nearest_rays(bearing, azimuth[start:start + rays])
        values[k] = np.where(ok, codes[ray, np.clip(gate, 0, gates - 1)], NO_COVERAGE)
        heights[k] = height + antenna_altitude_m
    bottom = beam_geometry(ground, sweeps[0][0] - HALF_BEAMWIDTH_DEGREES)[1] + antenna_altitude_m
    top = beam_geometry(ground, sweeps[-1][0] + HALF_BEAMWIDTH_DEGREES)[1] + antenna_altitude_m

    grid = np.empty((len(levels_m), ground.size), dtype=np.uint8)
    last = len(sweeps) - 1
    for level, z in enumerate(levels_m):
        above = (heights < z).sum(axis=0)  # index of the first beam above z
        lower = np.clip(above - 1, 0, last)[None]
        upper = np.clip(above, 0, last)[None]
        low_code = np.take_along_axis(values, lower, 0)[0]
        high_code = np.take_along_axis(values, upper, 0)[0]
        low_height = np.take_along_axis(heights, lower, 0)[0]
        high_height = np.take_along_axis(heights, upper, 0)[0]
        with np.errstate(invalid="ignore", divide="ignore"):
            weight = np.clip((z - low_height) / (high_height - low_height), 0, 1)
        blended = encode(decode(low_code) * (1 - weight) + decode(high_code) * weight)
        both = (low_code >= FIRST_VALUE) & (high_code >= FIRST_VALUE)
        result = np.where(both, blended, np.where(weight < 0.5, low_code, high_code))
        result = np.where(above == 0, np.where(z >= bottom, values[0], NO_COVERAGE), result)
        result = np.where(above > last, np.where(z <= top, values[last], NO_COVERAGE), result)
        grid[level] = np.where(inside, result, NO_COVERAGE)
    side = axis.size
    return grid.reshape(len(levels_m), side, side), axis


def pack(arrays):
    """Gzip arrays end to end. ``arrays`` is [(name, array, extra metadata)].

    Returns (data, descriptor): the descriptor lists each array's dtype, shape, and
    byte offset in the uncompressed stream, with the stream's size and checksum.
    """
    chunks, described, offset = [], [], 0
    for name, array, extra in arrays:
        array = np.ascontiguousarray(array)
        array = array.astype(array.dtype.newbyteorder("<"), copy=False)
        raw = array.tobytes()
        described.append({"name": name, "dtype": array.dtype.str, "shape": list(array.shape),
                          "offset": offset, "bytes": len(raw), **extra})
        chunks.append(raw)
        offset += len(raw)
    data = gzip.compress(b"".join(chunks), compresslevel=6, mtime=0)
    return data, {"encoding": "gzip", "byte_order": "little", "bytes": len(data),
                  "uncompressed_bytes": offset, "sha256": hashlib.sha256(data).hexdigest(),
                  "arrays": described}


def unpack(descriptor, data):
    """Arrays of a volume file by name. Raises ValueError for any file that does
    not match its descriptor (truncated, corrupted, or a different revision)."""
    name = descriptor.get("filename", "volume")
    expected = descriptor.get("sha256")
    actual = hashlib.sha256(data).hexdigest()
    if expected and actual != expected:
        raise ValueError(f"{name}: sha256 {actual} does not match the manifest's {expected}")
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError) as exc:
        raise ValueError(f"{name}: not a complete gzip stream ({exc})") from None
    if len(raw) != descriptor.get("uncompressed_bytes"):
        raise ValueError(f"{name}: {len(raw)} bytes uncompressed, manifest says {descriptor.get('uncompressed_bytes')}")
    result = {}
    for array in descriptor.get("arrays", []):
        dtype = np.dtype(array["dtype"])
        if dtype.str not in ALLOWED_DTYPES:
            raise ValueError(f"{name}: array {array['name']} has unsupported dtype {array['dtype']}")
        size = math.prod(array["shape"])
        if array["bytes"] != size * dtype.itemsize or array["offset"] + array["bytes"] > len(raw):
            raise ValueError(f"{name}: array {array['name']} does not fit its shape or the file")
        result[array["name"]] = np.frombuffer(raw, dtype, size, array["offset"]).reshape(array["shape"])
    return result
