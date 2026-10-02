"""Reflectivity color scales.

STANDARD approximates the RadarScope "Standard (low-filter)" reflectivity scale,
read from screenshots of its color bar (2026-09-27): the bar
spans -32 to 95 dBZ, calibrated with the app's own readings at 20, 30, 40.5, 50,
70, 80, and 90 dBZ. Each 10 dBZ band starts bright and darkens toward the next;
below 20 dBZ is a dim blue-grey, and below about 0 dBZ is not drawn. 254 colors,
one per 0.5 dBZ, so images stay palette PNGs.
"""

STANDARD_MIN_DBZ = -32.0
STANDARD_MAX_DBZ = 95.0
# Below this the scale is black: drawn transparent instead.
STANDARD_TRANSPARENT_BELOW_DBZ = 0.5

STANDARD_COLORS = [
    "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000",
    "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000",
    "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000",
    "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000",
    "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000",
    "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000",
    "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000",
    "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000", "#000000",
    "#000000", "#070808", "#070808", "#0a0b0c", "#0d0e10", "#121417", "#121417", "#14171b",
    "#181c22", "#181c22", "#191e26", "#1b222d", "#1b222d", "#1c2330", "#1c2537", "#1c2537",
    "#1c263a", "#1b2741", "#1b2741", "#1d2b45", "#202f4a", "#22344f", "#253954", "#283e5a",
    "#2f4965", "#2f4965", "#324f6a", "#395b76", "#395b76", "#3b617c", "#436f88", "#436f88",
    "#47768e", "#4b7d95", "#4f859b", "#538ca2", "#5895a9", "#5a99ab", "#5c9dae", "#4ca58e",
    "#2db64e", "#2db64e", "#2aaf48", "#25a33e", "#25a33e", "#229c38", "#1d902e", "#1d902e",
    "#1a8928", "#188323", "#157d1e", "#127618", "#107013", "#0d6a0e", "#0b6409", "#237308",
    "#3b8207", "#539106", "#6ba105", "#9bbf03", "#9bbf03", "#b3cf02", "#cbde01", "#e3ed00",
    "#fcfd00", "#f8f800", "#f4f300", "#f1ee00", "#ede900", "#e9e400", "#e6df00", "#e2da00",
    "#ded500", "#dbd000", "#d7cb00", "#d0c100", "#d0c100", "#ccbc00", "#c8b700", "#c5b300",
    "#fa9400", "#f69000", "#f28d01", "#ee8a01", "#ea8702", "#e78403", "#e38103", "#df7e04",
    "#db7b05", "#d77805", "#d47406", "#d07106", "#cc6e07", "#c86b08", "#c46808", "#c16509",
    "#bd620a", "#b95f0a", "#b55c0b", "#b2590c", "#f9230b", "#f2230c", "#ec230d", "#e6230e",
    "#df240f", "#d92410", "#d32411", "#cd2412", "#c62513", "#c02514", "#ba2516", "#b42517",
    "#ad2618", "#a72619", "#a1261a", "#9b261b", "#94271c", "#8e271d", "#88271e", "#822820",
    "#ca99b4", "#c992b0", "#c98bad", "#c885a9", "#c87ea6", "#c778a2", "#c7719f", "#c76a9b",
    "#c66498", "#c65d94", "#c55791", "#c5508d", "#c44a8a", "#c44386", "#c43c83", "#c3367f",
    "#c32f7c", "#c22978", "#c22275", "#c21c72", "#9a24e0", "#9522db", "#9021d7", "#8b20d2",
    "#861fce", "#811ec9", "#7c1dc5", "#781cc1", "#731bbc", "#6e1ab8", "#6918b3", "#6417af",
    "#5f16aa", "#5b15a6", "#5614a2", "#51139d", "#4c1299", "#471194", "#421090", "#3e0f8c",
    "#84fdff", "#80f5f9", "#7deef3", "#79e7ed", "#76e0e7", "#73d9e1", "#6fd2db", "#6ccbd5",
    "#69c4cf", "#65bdc9", "#62b5c4", "#5eaebe", "#5ba7b8", "#58a0b2", "#5499ac", "#5192a6",
    "#4e8ba0", "#4a849a", "#477d94", "#44768f", "#a16549", "#9b5a41", "#965038", "#914630",
    "#8c3c28", "#873220", "#822818", "#7d1e10", "#781408", "#730a01",
]


def standard_colormap():
    """The Standard scale as a matplotlib colormap over STANDARD_MIN_DBZ..STANDARD_MAX_DBZ."""
    from matplotlib.colors import ListedColormap, to_rgba

    step = (STANDARD_MAX_DBZ - STANDARD_MIN_DBZ) / len(STANDARD_COLORS)
    rgba = []
    for index, color in enumerate(STANDARD_COLORS):
        top = STANDARD_MIN_DBZ + (index + 1) * step
        rgba.append((0, 0, 0, 0) if top <= STANDARD_TRANSPARENT_BELOW_DBZ else to_rgba(color))
    cmap = ListedColormap(rgba, name="Standard")
    cmap.set_under((0, 0, 0, 0))
    cmap.set_bad((0, 0, 0, 0))
    return cmap
