"""Render native radar gates onto transparent EPSG:3857 images, and write the
numeric reflectivity volumes (see ``volume``).

No vertical interpolation in the images: each is an actual antenna elevation
sweep. The numeric grid is the only interpolated product, and says so.

Products per elevation: reflectivity, velocity, storm-relative velocity, and
correlation coefficient (CC). Storm-relative velocity subtracts the storm's motion
(from ``storm_motion``, a callable returning winds-aloft storm motion) along each
radar beam; it is skipped for a volume when no storm motion is available.

Cleanup, each optional:
- Reflectivity weaker than ``min_reflectivity`` dBZ is hidden.
- Non-meteorological echoes (ground clutter, birds, insects) are hidden using
  dual-polarization: a gate with CC below ``min_correlation`` *and* reflectivity
  below ``clutter_max_dbz`` is hidden. Strong low-CC returns (hail cores, tornado
  debris) are always kept.
- Velocity is dealiased (unfolded) with Py-ART's region-based method, then hidden
  where there is no echo or where the gate is classified as clutter. Low split
  cuts carry velocity in a separate Doppler sweep without CC; CC is taken from
  the surveillance sweep at the same angle, matched by nearest azimuth.
"""
import gc
from datetime import timezone

CC_FIELD = "cross_correlation_ratio"


def select_sweeps(radar, count=4):
    """Group split cuts/repeated low tilts, then select the sweep for each field,
    for the lowest ``count`` angles (``None``: every angle).

    Velocity: the latest sweep with velocity. Reflectivity: the latest sweep that
    also carries CC (the surveillance cut), else the latest with reflectivity.
    CC: the latest sweep with CC.
    """
    groups = []
    for sweep, angle in sorted(enumerate(radar.fixed_angle["data"]), key=lambda item: float(item[1])):
        angle = float(angle)
        group = next((g for g in groups if abs(g["angle"] - angle) <= 0.15), None)
        if group is None:
            group = {"angle": angle, "sweeps": []}
            groups.append(group)
        group["sweeps"].append(sweep)

    def latest(sweeps, field, also=None):
        if field not in radar.fields or (also and also not in radar.fields):
            return None
        for sweep in sorted(sweeps, reverse=True):
            if radar.get_field(sweep, field).count() and (also is None or radar.get_field(sweep, also).count()):
                return sweep
        return None

    selected = []
    for group in groups:
        fields = {}
        reflectivity = latest(group["sweeps"], "reflectivity", CC_FIELD)
        if reflectivity is None:
            reflectivity = latest(group["sweeps"], "reflectivity")
        for field, sweep in (("reflectivity", reflectivity), ("velocity", latest(group["sweeps"], "velocity")),
                             (CC_FIELD, latest(group["sweeps"], CC_FIELD))):
            if sweep is not None:
                fields[field] = sweep
        if "reflectivity" in fields or "velocity" in fields:
            selected.append({"nominal_angle": group["angle"], "fields": fields})
        if len(selected) == count:
            break
    return selected


def mask_below(values, minimum):
    """Hide weak returns below ``minimum`` dBZ (clear-air echoes, ground clutter,
    birds and insects near the radar). ``None`` keeps everything."""
    if minimum is None:
        return values
    import numpy as np
    return np.ma.masked_less(values, minimum)


def nearest_rays(target_azimuths, source_azimuths):
    """Index of the source ray nearest in azimuth (degrees, wrapping at 360) to each target ray."""
    import numpy as np
    target = np.asarray(target_azimuths, dtype=float) % 360
    source = np.asarray(source_azimuths, dtype=float) % 360
    order = np.argsort(source)
    ordered = source[order]
    # Extend by one ray on each side so the nearest ray across 0°/360° is found.
    padded = np.concatenate(([ordered[-1] - 360], ordered, [ordered[0] + 360]))
    positions = np.searchsorted(padded, target)
    below = np.clip(positions - 1, 0, len(padded) - 1)
    above = np.clip(positions, 0, len(padded) - 1)
    choose_above = np.abs(padded[above] - target) < np.abs(target - padded[below])
    padded_index = np.where(choose_above, above, below)
    return order[(padded_index - 1) % len(ordered)]


def clutter_mask(reflectivity, correlation, min_correlation, clutter_max_dbz):
    """True where a gate is non-meteorological: low CC and weak reflectivity.

    Gates without a CC value are never called clutter. Strong returns are kept
    whatever their CC, so hail cores and tornado debris stay visible.
    """
    import numpy as np
    shape = np.shape(reflectivity)
    if min_correlation is None or correlation is None:
        return np.zeros(shape, dtype=bool)
    cc = np.ma.filled(np.ma.masked_invalid(correlation).astype(float), np.nan)
    dbz = np.ma.filled(np.ma.masked_invalid(reflectivity).astype(float), np.nan)
    with np.errstate(invalid="ignore"):
        return (cc < min_correlation) & (np.isnan(dbz) | (dbz < clutter_max_dbz))


def matched_correlation(cut, sweep, cc_cut, cc_sweep):
    """CC for a sweep's gates: its own, or the surveillance cut's matched by nearest
    azimuth over identical range gates. None when there is no usable CC."""
    import numpy as np
    if cc_cut is None:
        return None
    if sweep == cc_sweep:
        return cc_cut.fields[CC_FIELD]["data"]
    if cut.ngates == cc_cut.ngates and np.allclose(cut.range["data"], cc_cut.range["data"]):
        return cc_cut.fields[CC_FIELD]["data"][nearest_rays(cut.azimuth["data"], cc_cut.azimuth["data"])]
    return None


def storm_relative(velocity, azimuth, elevation, u, v):
    """Radial velocity minus the storm motion's component along each beam.

    ``azimuth``/``elevation`` are per ray in degrees; (u, v) is the storm motion
    in m/s (east, north). Positive is away from the radar, as for velocity.
    """
    import numpy as np
    az = np.radians(np.asarray(azimuth, dtype=float))[:, None]
    el = np.radians(np.asarray(elevation, dtype=float))[:, None]
    return velocity - (u * np.sin(az) + v * np.cos(az)) * np.cos(el)


def to_palette_png(path):
    """Rewrite an RGBA PNG as a palette PNG when it has at most 256 colors.

    Radar images use few colors and fully opaque or fully transparent pixels, so
    this is exact (verified pixel for pixel) and about half the size. Images
    with more colors are left as they are. Returns True when rewritten.
    """
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"))
    packed = rgba.reshape(-1, 4).astype(np.uint32)
    keys = (packed[:, 0] << 24) | (packed[:, 1] << 16) | (packed[:, 2] << 8) | packed[:, 3]
    colors, inverse = np.unique(keys, return_inverse=True)
    if len(colors) > 256:
        return False
    table = np.stack([(colors >> 24) & 255, (colors >> 16) & 255, (colors >> 8) & 255, colors & 255], axis=1).astype(np.uint8)
    indexed = Image.fromarray(inverse.reshape(rgba.shape[:2]).astype(np.uint8), mode="P")
    indexed.putpalette(table[:, :3].flatten().tolist())
    staged = path.with_suffix(".palette.png")
    indexed.save(staged, optimize=True, transparency=bytes(table[:, 3].tolist()))
    with Image.open(staged) as check:
        if not np.array_equal(np.asarray(check.convert("RGBA")), rgba):
            staged.unlink()
            return False
    staged.replace(path)
    return True


def reflectivity_style(palette):
    """(colormap name, minimum, maximum, units) for reflectivity: "NWSRef" (classic NWS,
    -20 to 80 dBZ) or "Standard" (an approximate RadarScope Standard scale, -32 to 95 dBZ)."""
    if palette == "Standard":
        import matplotlib
        from palettes import STANDARD_MAX_DBZ, STANDARD_MIN_DBZ, standard_colormap
        if "Standard" not in matplotlib.colormaps:
            matplotlib.colormaps.register(standard_colormap())
        return ("Standard", STANDARD_MIN_DBZ, STANDARD_MAX_DBZ, "dBZ")
    if palette != "NWSRef":
        raise ValueError(f"Unknown reflectivity palette: {palette}")
    return ("NWSRef", -20, 80, "dBZ")


POLAR_FILENAME = "volume_polar_reflectivity.bin.gz"
GRID_FILENAME = "volume_grid_reflectivity.bin.gz"


def write_volumes(radar, destination, range_km, min_correlation=None, clutter_max_dbz=35.0,
                  grid_spacing_km=1.0, grid_level_m=500.0):
    """Write the numeric polar reflectivity volume and the grid made from it (see
    ``volume``); return their descriptors for the cycle manifest.

    One sweep per elevation angle, chosen as for the images (the surveillance cut,
    which carries CC), gates out to ``range_km``, clutter filter applied, and no
    display threshold.
    """
    import numpy as np
    from netCDF4 import num2date
    from pyproj import Transformer
    import volume

    groups = [g for g in select_sweeps(radar, count=None) if "reflectivity" in g["fields"]]
    if not groups:
        raise ValueError("Volume has no reflectivity sweeps")
    ranges = np.asarray(radar.range["data"], dtype=float)
    gates = int(np.searchsorted(ranges, range_km * 1000, side="right"))
    spacing = float(ranges[1] - ranges[0]) if len(ranges) > 1 else 0.0
    if gates < 2 or not np.allclose(np.diff(ranges[:gates]), spacing, atol=1.0):
        raise ValueError("Range gates are too few or unevenly spaced")
    units = radar.time["units"]
    reference = num2date(0.0, units, only_use_cftime_datetimes=False).isoformat() + "Z"
    codes, azimuth, elevation, times, sweeps, first_ray = [], [], [], [], [], 0
    for group in groups:
        sweep = group["fields"]["reflectivity"]
        cc_sweep = group["fields"].get(CC_FIELD)
        cut = radar.extract_sweeps([sweep])
        cc_cut = cut if cc_sweep == sweep else radar.extract_sweeps([cc_sweep]) if cc_sweep is not None else None
        correlation = matched_correlation(cut, sweep, cc_cut, cc_sweep)
        reflectivity = cut.fields["reflectivity"]["data"]
        clutter = clutter_mask(reflectivity, correlation, min_correlation, clutter_max_dbz)
        codes.append(volume.encode(reflectivity[:, :gates], clutter[:, :gates]))
        azimuth.append(cut.azimuth["data"])
        elevation.append(cut.elevation["data"])
        times.append(cut.time["data"])
        rays = int(cut.nrays)
        start, end = num2date([float(cut.time["data"][0]), float(cut.time["data"][-1])], units,
                              only_use_cftime_datetimes=False)
        sweeps.append({"elevation_degrees": round(float(cut.fixed_angle["data"][0]), 3), "sweep_index": sweep,
                       "ray_start": first_ray, "rays": rays,
                       "sweep_start": start.isoformat() + "Z", "sweep_end": end.isoformat() + "Z",
                       "clutter_filtered": min_correlation is not None and correlation is not None})
        first_ray += rays
        del cut, cc_cut
    codes = np.concatenate(codes)
    azimuth = np.concatenate(azimuth).astype(np.float32)
    elevation = np.concatenate(elevation).astype(np.float32)
    lat = float(radar.latitude["data"][0])
    lon = float(radar.longitude["data"][0])
    altitude = float(radar.altitude["data"][0])
    site = {"latitude": lat, "longitude": lon, "altitude_m": altitude, "altitude_datum": "mean sea level"}
    data, packed = volume.pack([
        ("reflectivity", codes, {"axes": ["ray", "gate"], "units": "dBZ"}),
        ("azimuth", azimuth, {"axes": ["ray"], "units": "degrees clockwise from north"}),
        ("elevation", elevation, {"axes": ["ray"], "units": "degrees above horizontal"}),
        ("time", np.concatenate(times).astype(np.float64), {"axes": ["ray"], "units": f"seconds since {reference}"}),
    ])
    (destination / POLAR_FILENAME).write_bytes(data)
    polar = {"kind": "polar", "field": "reflectivity", "available": True, "filename": POLAR_FILENAME,
             "units": "dBZ", "values": volume.ENCODING, "radar": site, "time_reference": reference,
             "range": {"first_gate_m": float(ranges[0]), "gate_spacing_m": spacing, "gates": gates,
                       "measured_along": "slant range to the gate centre"},
             "sweeps": sweeps, "minimum_shown": None,
             "clutter_filter": ({"min_correlation": min_correlation, "clutter_max_dbz": clutter_max_dbz}
                                if min_correlation is not None else None),
             **packed}

    # Each sweep is placed at the median elevation its rays actually had.
    levels = np.arange(grid_level_m, volume.GRID_TOP_M + 1, grid_level_m)
    placed = [(float(np.median(elevation[s["ray_start"]:s["ray_start"] + s["rays"]])), s["ray_start"], s["rays"])
              for s in sweeps]
    grid, axis = volume.grid_from_polar(codes, azimuth, placed, float(ranges[0]), spacing, altitude,
                                        grid_spacing_km * 1000, levels, range_km * 1000)
    data, packed = volume.pack([("reflectivity", grid, {"axes": ["z", "y", "x"], "units": "dBZ"})])
    (destination / GRID_FILENAME).write_bytes(data)
    proj4 = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs"
    to_geographic = Transformer.from_crs(proj4, "EPSG:4326", always_xy=True)
    corner_lon, corner_lat = to_geographic.transform([axis[0], axis[-1], axis[-1], axis[0]],
                                                     [axis[0], axis[0], axis[-1], axis[-1]])
    horizontal = {"first_m": float(axis[0]), "spacing_m": float(grid_spacing_km * 1000), "count": int(axis.size)}
    grid_descriptor = {
        "kind": "grid", "field": "reflectivity", "available": True, "filename": GRID_FILENAME,
        "derived_from": POLAR_FILENAME, "units": "dBZ", "values": volume.ENCODING, "radar": site,
        "time_reference": reference,
        "crs": {"proj4": proj4, "origin": "radar location, x east and y north, metres"},
        "x": {**horizontal, "direction": "east"}, "y": {**horizontal, "direction": "north"},
        "z": {"first_m": float(levels[0]), "spacing_m": float(grid_level_m), "count": int(levels.size),
              "datum": "mean sea level"},
        "bounds": [[min(corner_lat), min(corner_lon)], [max(corner_lat), max(corner_lon)]],
        "interpolation": ("Nearest ray and gate of each sweep over the column, 4/3 effective earth radius beam "
                          "heights; linear in height between adjacent sweeps when both have reflectivity, "
                          "otherwise the nearer sweep's code; half a beamwidth (0.5 degrees) covered below the "
                          "lowest and above the highest sweep; no terrain blocking."),
        "minimum_shown": None, "clutter_filter": polar["clutter_filter"],
        **packed}
    return [polar, grid_descriptor]


def render_volume(source, destination, size=1536, range_km=230, min_reflectivity=None,
                  min_correlation=None, clutter_max_dbz=35.0, dealias=False, storm_motion=None,
                  reflectivity_palette="NWSRef", volume_products=False, grid_spacing_km=1.0, grid_level_m=500.0):
    """Render the image products and, with ``volume_products``, the numeric volumes.

    Returns {"products": [...], "volumes": [...]}. A volume that cannot be made is an
    unavailable entry with a ``reason``; the images are unaffected.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pyart
    from netCDF4 import num2date
    from pyproj import Transformer

    radar = pyart.io.read_nexrad_archive(str(source), include_fields=["reflectivity", "velocity", CC_FIELD])
    lat = float(radar.latitude["data"][0])
    lon = float(radar.longitude["data"][0])
    forward = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    inverse = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    cx, cy = forward.transform(lon, lat)
    half = range_km * 1000 / np.cos(np.radians(lat))
    extent = (cx - half, cx + half, cy - half, cy + half)
    west, south = inverse.transform(extent[0], extent[2])
    east, north = inverse.transform(extent[1], extent[3])
    products = []
    motion = None
    if storm_motion is not None:
        start = num2date(float(radar.time["data"][0]), radar.time["units"], only_use_cftime_datetimes=False)
        motion = storm_motion(lat, lon, start.replace(tzinfo=timezone.utc))
    selected = select_sweeps(radar)
    if not selected:
        raise ValueError("Volume has no usable reflectivity or velocity sweeps")

    styles = {
        "reflectivity": reflectivity_style(reflectivity_palette),
        "velocity": ("NWSVel", -70, 70, "m/s"),
        "correlation_coefficient": ("HomeyerRainbow", 0.2, 1.05, "unitless"),
        "storm_relative_velocity": ("NWSVel", -70, 70, "m/s"),
    }
    sources = {"reflectivity": "reflectivity", "velocity": "velocity", "correlation_coefficient": CC_FIELD,
               "storm_relative_velocity": "velocity"}
    fields_out = ("reflectivity", "velocity", "correlation_coefficient") + (("storm_relative_velocity",) if storm_motion else ())

    for level in range(4):
        group = selected[level] if level < len(selected) else None
        cc_sweep = group["fields"].get(CC_FIELD) if group else None
        cc_cut = radar.extract_sweeps([cc_sweep]) if cc_sweep is not None else None
        unfolded = {}
        for field in fields_out:
            sweep = group["fields"].get(sources[field]) if group else None
            if field == "storm_relative_velocity" and sweep is not None and motion is None:
                products.append({"elevation_index": level, "field": field, "available": False,
                                 "reason": "No storm motion: winds aloft unavailable"})
                continue
            if sweep is None:
                products.append({"elevation_index": level, "field": field, "available": False,
                                 "reason": "Field/elevation not present in this scan strategy"})
                continue
            # Extract one sweep to avoid materializing coordinates for the whole volume.
            cut = cc_cut if sweep == cc_sweep else radar.extract_sweeps([sweep])
            values = np.ma.masked_invalid(cut.fields[sources[field]]["data"])
            dealiased = None
            reason = None

            correlation = matched_correlation(cut, sweep, cc_cut, cc_sweep)
            own_reflectivity = cut.fields["reflectivity"]["data"] if "reflectivity" in cut.fields else None

            is_velocity = field in ("velocity", "storm_relative_velocity")
            if is_velocity and dealias:
                if sweep not in unfolded:
                    try:
                        corrected = pyart.correct.dealias_region_based(cut, vel_field="velocity")
                        unfolded[sweep] = (np.ma.masked_invalid(corrected["data"]), True, None)
                    except Exception as exc:  # keep the measured velocity rather than fail the volume
                        unfolded[sweep] = (values, False, f"Dealiasing failed: {type(exc).__name__}")
                values, dealiased, reason = unfolded[sweep]
            elif is_velocity:
                dealiased = False
            if field == "storm_relative_velocity":
                values = storm_relative(values, cut.azimuth["data"], cut.elevation["data"], motion["u"], motion["v"])

            if own_reflectivity is not None:
                if field != "correlation_coefficient":
                    # The CC image keeps low-CC areas: showing them (debris, hail) is its purpose.
                    clutter = clutter_mask(own_reflectivity, correlation, min_correlation, clutter_max_dbz)
                    values = np.ma.masked_where(clutter, values)
                if field != "reflectivity" and min_reflectivity is not None:
                    # Velocity and CC only where there is an echo worth showing.
                    weak = np.ma.filled(np.ma.masked_invalid(own_reflectivity).astype(float), np.nan)
                    with np.errstate(invalid="ignore"):
                        values = np.ma.masked_where(np.isnan(weak) | (weak < min_reflectivity), values)
            if field == "reflectivity":
                values = mask_below(values, min_reflectivity)

            gate_count = int(np.searchsorted(cut.range["data"], range_km * 1000, side="right"))
            if gate_count < 2:
                raise ValueError("Insufficient range gates")
            x, y, _ = pyart.core.antenna_vectors_to_cartesian(
                cut.range["data"][:gate_count], cut.azimuth["data"], cut.elevation["data"], edges=True)
            glon, glat = pyart.core.cartesian_to_geographic_aeqd(x, y, lon, lat)
            mx, my = forward.transform(glon, glat)
            filename = f"e{level}_{field}.png"
            cmap, vmin, vmax, units = styles[field]
            fig = plt.figure(figsize=(size / 100, size / 100), dpi=100, frameon=False)
            try:
                ax = fig.add_axes([0, 0, 1, 1])
                ax.pcolormesh(mx, my, values[:, :gate_count], cmap=cmap,
                              vmin=vmin, vmax=vmax, shading="flat", rasterized=True,
                              antialiased=False)
                ax.set_xlim(extent[:2])
                ax.set_ylim(extent[2:])
                ax.set_axis_off()
                fig.savefig(destination / filename, dpi=100, transparent=True, pad_inches=0)
            finally:
                plt.close(fig)
            to_palette_png(destination / filename)
            times = num2date([float(cut.time["data"][0]), float(cut.time["data"][-1])],
                             cut.time["units"], only_use_cftime_datetimes=False)
            product = {
                "elevation_index": level, "elevation_degrees": round(float(cut.fixed_angle["data"][0]), 3),
                "sweep_index": sweep, "field": field, "available": True,
                "filename": filename, "width": size, "height": size,
                "sweep_start": times[0].isoformat() + "Z", "sweep_end": times[1].isoformat() + "Z",
                "bounds": [[south, west], [north, east]], "crs": "EPSG:3857",
                "range_km": range_km, "units": units,
                "color_scale": {"colormap": cmap, "minimum": vmin, "maximum": vmax},
                "minimum_shown": min_reflectivity if field == "reflectivity" else None,
                "clutter_filter": ({"min_correlation": min_correlation, "clutter_max_dbz": clutter_max_dbz}
                                   if min_correlation is not None and correlation is not None
                                   and field != "correlation_coefficient" else None),
                "velocity_dealiased": dealiased,
            }
            if reason:
                product["note"] = reason
            if field == "storm_relative_velocity":
                product["storm_motion"] = motion
            products.append(product)
            del values, x, y, glon, glat, mx, my
            if cut is not cc_cut:
                del cut
        del cc_cut
    volumes = []
    if volume_products:
        try:
            volumes = write_volumes(radar, destination, range_km, min_correlation, clutter_max_dbz,
                                    grid_spacing_km, grid_level_m)
        except Exception as exc:  # keep the images rather than fail the volume
            for name in (POLAR_FILENAME, GRID_FILENAME):
                (destination / name).unlink(missing_ok=True)
            volumes = [{"kind": kind, "field": "reflectivity", "available": False,
                        "reason": f"Volume export failed: {type(exc).__name__}: {exc}"} for kind in ("polar", "grid")]
    del radar
    gc.collect()
    return {"products": products, "volumes": volumes}
