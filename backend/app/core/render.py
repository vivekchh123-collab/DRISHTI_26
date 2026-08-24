"""Raster rendering: arrays in, PNG bytes out.

Contains a small, complete PNG encoder built on ``zlib`` and ``struct`` from the
standard library. Pillow is deliberately not a runtime dependency — the whole point
of this stack is that it installs and runs anywhere, including an offline district
server with no wheels available.

Also holds the cartography: a Horn hillshade and the colour ramps. The hillshade is
what lets the map draw with no network and no external tile server, which is the
difference between a demo that works in the hall and one that does not.
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

RGB = Tuple[int, int, int]


# ---------------------------------------------------------------------------
# PNG encoding
# ---------------------------------------------------------------------------

def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(rgba: np.ndarray, level: int = 6) -> bytes:
    """Encode an ``(h, w, 4)`` uint8 array as an RGBA PNG.

    Every scanline is written with filter type 0 (None). Real encoders try the
    five filters per row and keep the cheapest; for the smooth, low-entropy
    rasters here the gain does not pay for the extra passes.
    """
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("expected an (h, w, 4) RGBA array, got %r" % (rgba.shape,))
    arr = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w = arr.shape[:2]

    # prepend the per-scanline filter byte in one allocation
    raw = np.zeros((h, w * 4 + 1), np.uint8)
    raw[:, 1:] = arr.reshape(h, w * 4)

    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw.tobytes(), level))
            + _chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# resampling
# ---------------------------------------------------------------------------

def upscale(rgba: np.ndarray, factor: int, smooth: bool = True) -> np.ndarray:
    """Enlarge an RGBA raster.

    A 160x160 analysis grid stretched across a full-width map is visibly blocky,
    so overlays are enlarged before encoding. Continuous fields (depth, hillshade)
    interpolate; discrete masks must not, or the edges develop a halo of
    part-transparent pixels that reads as a rendering bug.
    """
    if factor <= 1:
        return rgba
    h, w = rgba.shape[:2]
    H, W = h * factor, w * factor
    if not smooth:
        return np.repeat(np.repeat(rgba, factor, axis=0), factor, axis=1)

    # bilinear on cell centres, edge-clamped
    sy = (np.arange(H) + 0.5) / factor - 0.5
    sx = (np.arange(W) + 0.5) / factor - 0.5
    y0 = np.clip(np.floor(sy), 0, h - 1).astype(int)
    x0 = np.clip(np.floor(sx), 0, w - 1).astype(int)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    ty = np.clip(sy - y0, 0, 1).astype(np.float32)[:, None, None]
    tx = np.clip(sx - x0, 0, 1).astype(np.float32)[None, :, None]

    a = rgba.astype(np.float32)
    top = a[y0][:, x0] * (1 - tx) + a[y0][:, x1] * tx
    bot = a[y1][:, x0] * (1 - tx) + a[y1][:, x1] * tx
    return np.clip(top * (1 - ty) + bot * ty, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# colour ramps
# ---------------------------------------------------------------------------

def _hex(s: str) -> RGB:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# Flood depth. One sequential blue ramp, reused as the map legend and as the
# accent motif in the UI, so the same colour always means the same depth.
DEPTH_STOPS = ["#CBE3F0", "#8FC4E2", "#4E9BC9", "#2A72A8", "#164C7D", "#0B2E52"]

# Height above nearest drainage: low is dangerous, so the ramp runs hot-to-cool.
HAND_STOPS = ["#8C2D18", "#C4562A", "#E0A34A", "#A9BFA0", "#5E8C6A", "#2F5D46"]

# Topographic wetness / waterlogging susceptibility.
WET_STOPS = ["#F4EFE4", "#D8D2A8", "#9DBE8E", "#5A9E86", "#2E7183", "#1B3F5C"]

# Sentinel-1 backscatter, rendered as the greyscale a SAR analyst expects.
SAR_STOPS = ["#000000", "#2B2B2B", "#575757", "#8A8A8A", "#C2C2C2", "#FFFFFF"]

# IMD's warning colour code. Domain-standard in India; used for all severity.
RISK_STOPS = ["#2E8B57", "#9FBF3B", "#E3B23C", "#E07B39", "#C4362C", "#7E1F17"]

RAMPS = {
    "depth": DEPTH_STOPS, "hand": HAND_STOPS, "wetness": WET_STOPS,
    "sar": SAR_STOPS, "risk": RISK_STOPS,
}


def ramp_lut(stops: Sequence[str], n: int = 256) -> np.ndarray:
    """Expand colour stops into an ``(n, 3)`` uint8 lookup table."""
    cols = np.array([_hex(s) for s in stops], np.float32)
    pos = np.linspace(0, n - 1, len(cols))
    idx = np.arange(n, dtype=np.float32)
    out = np.empty((n, 3), np.float32)
    for c in range(3):
        out[:, c] = np.interp(idx, pos, cols[:, c])
    return np.clip(out, 0, 255).astype(np.uint8)


_LUT_CACHE: dict = {}


def lut(name: str) -> np.ndarray:
    if name not in _LUT_CACHE:
        _LUT_CACHE[name] = ramp_lut(RAMPS[name])
    return _LUT_CACHE[name]


def colorize(values: np.ndarray, name: str, vmin: float, vmax: float,
             alpha: Optional[np.ndarray] = None,
             mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Map a scalar field through a ramp into RGBA.

    ``mask`` selects the cells to draw at all — everything else is fully
    transparent. ``alpha`` (0..1) modulates opacity within the drawn area.
    """
    v = np.clip((values - vmin) / max(vmax - vmin, 1e-9), 0, 1)
    table = lut(name)
    rgb = table[(v * 255).astype(np.uint8)]

    out = np.zeros(values.shape + (4,), np.uint8)
    out[..., :3] = rgb
    if alpha is None:
        out[..., 3] = 255
    else:
        out[..., 3] = np.clip(alpha, 0, 1) * 255
    if mask is not None:
        out[..., 3] = np.where(mask, out[..., 3], 0)
    return out


def solid(mask: np.ndarray, colour: str, alpha: float = 1.0) -> np.ndarray:
    """A single-colour RGBA layer wherever ``mask`` is true."""
    r, g, b = _hex(colour)
    out = np.zeros(mask.shape + (4,), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = r, g, b
    out[..., 3] = np.where(mask, int(np.clip(alpha, 0, 1) * 255), 0)
    return out


def over(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    """Porter-Duff source-over compositing of two RGBA uint8 layers."""
    b = base.astype(np.float32) / 255.0
    t = top.astype(np.float32) / 255.0
    ta = t[..., 3:4]
    ba = b[..., 3:4]
    out_a = ta + ba * (1 - ta)
    safe = np.maximum(out_a, 1e-6)
    out_rgb = (t[..., :3] * ta + b[..., :3] * ba * (1 - ta)) / safe
    out = np.concatenate([out_rgb, out_a], axis=-1)
    return np.clip(out * 255, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# cartography
# ---------------------------------------------------------------------------

def hillshade(dem: np.ndarray, cell_m: float, azimuth: float = 315.0,
              altitude: float = 42.0, z_factor: float = 1.0) -> np.ndarray:
    """Horn (1981) analytical hillshade, returned in 0..1.

    Standard cartographic convention: light from the north-west at 42 degrees.
    ``z_factor`` exaggerates relief — floodplains need a large value or they
    render as a flat grey field with no readable structure.
    """
    p = np.pad(dem.astype(np.float32) * z_factor, 1, mode="edge")
    z1, z2, z3 = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    z4, z6 = p[1:-1, :-2], p[1:-1, 2:]
    z7, z8, z9 = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8.0 * cell_m)
    dzdy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8.0 * cell_m)

    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    az = np.radians(360.0 - azimuth + 90.0)
    alt = np.radians(altitude)

    shade = (np.sin(alt) * np.cos(slope)
             + np.cos(alt) * np.sin(slope) * np.cos(az - aspect))
    return np.clip(shade, 0, 1).astype(np.float32)


# Hypsometric tint: the elevation colours of a survey sheet, tuned dark so the
# UI's dark theme stays the dominant surface and the flood overlay reads on top.
_LAND_STOPS = ["#20303A", "#2A3D42", "#36474A", "#455551", "#57655C", "#6E7A6C",
               "#8A9184", "#A8AC9E"]
_SEA_STOPS = ["#0A1B2A", "#123047", "#194461"]


def dilate(mask: np.ndarray) -> np.ndarray:
    """One-cell binary dilation, 4-connected."""
    out = mask.copy()
    out[:-1, :] |= mask[1:, :]
    out[1:, :] |= mask[:-1, :]
    out[:, :-1] |= mask[:, 1:]
    out[:, 1:] |= mask[:, :-1]
    return out


def drainage_tiers(accum: np.ndarray, threshold: float,
                   major_factor: float = 8.0) -> Tuple[np.ndarray, np.ndarray]:
    """Split the stream network into minor and major channels.

    Cartographic convention, and it carries real information: line weight tracks
    contributing area, so the trunk river reads as the trunk river at a glance
    instead of every channel being one indistinguishable width.
    """
    minor = accum >= threshold
    major = accum >= threshold * major_factor
    return minor & ~major, major


def basemap(dem: np.ndarray, cell_m: float, streams: np.ndarray,
            sea: Optional[np.ndarray] = None,
            z_factor: float = 1.0) -> np.ndarray:
    """Self-contained relief basemap: hypsometric tint × hillshade + drainage.

    This is what removes the dependency on an external tile server. It renders
    from the DEM we have already computed, so the map draws with the network
    unplugged.
    """
    land = dem if sea is None else np.where(sea, np.nan, dem)
    finite = np.isfinite(land)
    if finite.any():
        lo = float(np.nanpercentile(land[finite], 1))
        hi = float(np.nanpercentile(land[finite], 99))
    else:
        lo, hi = 0.0, 1.0
    t = np.clip((np.nan_to_num(land, nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1)

    tint = ramp_lut(_LAND_STOPS)[(t * 255).astype(np.uint8)].astype(np.float32)

    hs = hillshade(dem, cell_m, z_factor=z_factor)
    # Bias the shade around its own mean so flat terrain keeps mid-tone contrast
    # instead of washing out to a single value.
    hs = np.clip(0.55 + 1.35 * (hs - float(hs.mean())), 0.18, 1.35)
    rgb = np.clip(tint * hs[..., None], 0, 255)

    out = np.zeros(dem.shape + (4,), np.uint8)
    out[..., :3] = rgb.astype(np.uint8)
    out[..., 3] = 255

    if sea is not None and sea.any():
        depth_t = np.clip((-dem) / max(float(np.abs(dem[sea]).max()), 1.0), 0, 1)
        sea_rgb = ramp_lut(_SEA_STOPS)[(depth_t * 255).astype(np.uint8)]
        out[..., :3] = np.where(sea[..., None], sea_rgb, out[..., :3])

    return out


def compose_basemap(dem: np.ndarray, cell_m: float, accum: np.ndarray,
                    threshold: float, sea: Optional[np.ndarray] = None,
                    z_factor: float = 1.0, scale: int = 4) -> np.ndarray:
    """Full relief basemap at ``scale``x, with each layer resampled correctly.

    Relief is a continuous field and interpolates. The drainage network is a
    one-cell-wide mask and must not: bilinear upscaling spreads a single-cell
    line across four pixels at a quarter of its alpha, which renders a perfectly
    connected network as a scatter of faint dashes. Masks go up by nearest
    neighbour, then composite over the smooth relief.
    """
    sea_mask = sea if sea is not None else np.zeros(dem.shape, bool)
    relief = upscale(basemap(dem, cell_m, np.zeros(dem.shape, bool),
                             sea, z_factor), scale, smooth=True)

    minor, major = drainage_tiers(accum, threshold)
    minor &= ~sea_mask
    major &= ~sea_mask

    # Bridge on the upscaled mask, not the grid. A D8 network mostly advances
    # diagonally, and diagonal cells blown up by nearest neighbour touch only at
    # their corners — so a fully connected network renders as a scatter of
    # dashes. One pixel of dilation at output resolution closes the corner while
    # keeping the line one cell wide; the trunk gets more so it reads heavier.
    def _up(m: np.ndarray) -> np.ndarray:
        return np.repeat(np.repeat(m, scale, axis=0), scale, axis=1)

    minor_px = dilate(_up(minor))
    major_px = dilate(dilate(_up(major)))

    out = over(relief, solid(minor_px, "#4A88A8", 0.55))
    out = over(out, solid(major_px, "#2E6B8F", 0.92))
    return out


def png_layer(rgba: np.ndarray, scale: int = 4, smooth: bool = True) -> bytes:
    """Upscale and encode in one step — the common path for every map overlay."""
    return encode_png(upscale(rgba, scale, smooth=smooth))


def png_bytes(rgba: np.ndarray) -> bytes:
    """Encode an already-scaled raster."""
    return encode_png(rgba)


def legend(name: str, vmin: float, vmax: float, unit: str,
           steps: int = 6) -> List[dict]:
    """Legend entries for a ramp, for the UI to render as swatches."""
    table = lut(name)
    out = []
    for i in range(steps):
        f = i / (steps - 1) if steps > 1 else 0.0
        r, g, b = table[int(f * 255)]
        out.append({"color": "#%02X%02X%02X" % (r, g, b),
                    "value": round(vmin + f * (vmax - vmin), 2),
                    "unit": unit})
    return out
