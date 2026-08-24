"""Algorithm correctness against cases where the answer is known by hand.

These are the tests that matter under questioning. Each one constructs a surface
or a histogram whose correct answer can be worked out with arithmetic, and
checks the implementation reproduces it. They do not test that the model is a
good model of a flood — they test that the published algorithms are implemented
correctly, which is a different and checkable claim.
"""

import math

import numpy as np
import pytest

from app.core import sar
from app.core.grid import Grid
from app.core.terrain import (drainage_pits, fill_depressions, flow_network,
                              hand, resolve_flats_iter, slope_aspect, twi)


# ---------------------------------------------------------------------------
# slope
# ---------------------------------------------------------------------------

def test_slope_of_a_known_plane():
    """A uniform 10% gradient must read atan(0.1) = 5.7106 degrees."""
    cell = 10.0    # 1 m rise per 10 m cell
    dem = np.tile(np.arange(20, dtype=np.float32) * 1.0, (20, 1))
    slope, _ = slope_aspect(dem, cell)
    interior = slope[2:-2, 2:-2]
    assert np.allclose(interior, math.degrees(math.atan(0.1)), atol=1e-3)


def test_slope_of_flat_ground_is_zero():
    dem = np.full((16, 16), 42.0, np.float32)
    slope, _ = slope_aspect(dem, 30.0)
    assert np.allclose(slope, 0.0, atol=1e-6)


# ---------------------------------------------------------------------------
# depression filling and flat resolution
# ---------------------------------------------------------------------------

def test_priority_flood_raises_a_known_pit_to_its_spill_level():
    """A pit fills to the lowest saddle on a *connected path* to the edge.

    The spill level is the minimum, over all escape routes, of the highest point
    along that route — not simply the lowest point on the rim. Here a 6 m
    channel runs from the pit to the boundary, so the pit fills to 6 m; without
    that channel it would fill to 10 m, because every route out would have to
    cross the plateau.
    """
    dem = np.full((11, 11), 10.0, np.float32)
    dem[0:5, 5] = 6.0          # escape channel from the pit to the north edge
    dem[5, 5] = 2.0            # the pit
    filled = fill_depressions(dem)
    assert filled[5, 5] == pytest.approx(6.0, abs=1e-6)
    # Ground that was never below its surroundings must not move.
    assert filled[3, 1] == pytest.approx(10.0, abs=1e-6)


def test_a_pit_with_no_low_route_out_fills_to_the_plateau():
    """The counterpart: an isolated notch in the rim does not lower the spill."""
    dem = np.full((11, 11), 10.0, np.float32)
    dem[5, 5] = 2.0
    dem[0, 5] = 6.0            # low, but walled off behind the 10 m plateau
    filled = fill_depressions(dem)
    assert filled[5, 5] == pytest.approx(10.0, abs=1e-6)


def test_every_interior_cell_can_drain_after_flat_resolution():
    """The post-condition of the terrain pipeline, on a deliberately awful DEM.

    A large exactly-flat plateau with one notch is the worst case for D8: with
    no gradient it has to invent one, and any cell it leaves without a lower
    neighbour becomes a dead end that fragments the stream network.
    """
    dem = np.full((40, 40), 50.0, np.float32)
    dem[10:30, 10:30] = 30.0       # a wide flat basin
    dem[0, 20] = 10.0              # one way out
    resolved = resolve_flats_iter(fill_depressions(dem))
    assert int(drainage_pits(resolved).sum()) == 0


# ---------------------------------------------------------------------------
# D8 routing
# ---------------------------------------------------------------------------

def test_d8_routes_straight_downhill_on_a_tilted_plane():
    """On a plane tilted south, every cell must drain to the cell below it."""
    n = 24
    dem = np.tile(np.arange(n, dtype=np.float32)[:, None] * -2.0, (1, n))
    net = flow_network(dem, 30.0)
    recv = net.receiver.reshape(n, n)
    for r in range(1, n - 2):
        for c in range(2, n - 2):
            rr, _ = divmod(int(recv[r, c]), n)
            assert rr == r + 1, "cell (%d,%d) did not drain downslope" % (r, c)


def test_flow_accumulation_conserves_cells():
    """Everything that enters the grid must leave it.

    Total accumulation at the outlets equals the number of cells, because each
    cell contributes exactly one unit of itself.
    """
    n = 20
    dem = np.tile(np.arange(n, dtype=np.float32)[:, None] * -1.5, (1, n))
    net = flow_network(dem, 30.0)
    outlets = net.receiver < 0
    assert net.accum.ravel()[outlets].sum() == pytest.approx(n * n, rel=1e-6)


# ---------------------------------------------------------------------------
# HAND
# ---------------------------------------------------------------------------

def test_hand_on_a_constructed_ramp():
    """HAND is elevation above the drainage cell drained into — check by hand.

    A V-shaped valley with the channel in column 10: a cell k columns away sits
    k metres above the channel, so its HAND must be exactly k.
    """
    n = 21
    cols = np.abs(np.arange(n) - 10).astype(np.float32)
    dem = np.tile(cols, (n, 1))
    # Tilt very gently down-valley so flow has somewhere to go.
    dem = dem + np.arange(n, dtype=np.float32)[:, None] * -0.01
    net = flow_network(dem, 50.0)
    streams = np.zeros((n, n), bool)
    streams[:, 10] = True
    h, nearest = hand(dem, net, streams)

    mid = h[n // 2]
    for k in (1, 2, 3, 4):
        assert mid[10 + k] == pytest.approx(float(k), abs=0.2)
        assert mid[10 - k] == pytest.approx(float(k), abs=0.2)
    assert mid[10] == pytest.approx(0.0, abs=1e-6)


def test_hand_is_never_negative():
    grid = Grid.for_district(26.0, 85.0, 2000.0, n=64)
    dem = (np.random.default_rng(1).random((64, 64)).astype(np.float32) * 40.0
           + np.arange(64, dtype=np.float32)[:, None] * -0.4)
    net = flow_network(dem, grid.cell_m)
    streams = net.accum >= 40
    h, _ = hand(dem, net, streams)
    assert (h >= 0).all()


def test_hand_nearest_always_points_at_a_stream_cell():
    grid = Grid.for_district(26.0, 85.0, 2000.0, n=64)
    dem = (np.random.default_rng(7).random((64, 64)).astype(np.float32) * 30.0
           + np.arange(64, dtype=np.float32)[:, None] * -0.5)
    net = flow_network(dem, grid.cell_m)
    streams = net.accum >= 40
    _, nearest = hand(dem, net, streams)
    resolved = nearest.ravel()[nearest.ravel() >= 0]
    assert streams.ravel()[resolved].all()


# ---------------------------------------------------------------------------
# Otsu
# ---------------------------------------------------------------------------

def test_otsu_finds_the_trough_of_a_known_bimodal_histogram():
    """Two well-separated Gaussians at -20 and -8: the cut belongs between."""
    rng = np.random.default_rng(3)
    values = np.concatenate([rng.normal(-20.0, 1.0, 4000),
                             rng.normal(-8.0, 1.0, 6000)])
    t, sep = sar.otsu_threshold(values)
    assert -17.0 < t < -11.0
    assert sep > 0.7, "well-separated classes must score high separability"


def test_otsu_separability_of_a_single_gaussian_is_the_known_constant():
    """A unimodal Gaussian has separability 0.637 — and that is the floor.

    Splitting a normal distribution at its median gives two half-normals with
    means +/- sigma*sqrt(2/pi) = 0.7979 sigma and equal weights, so

        eta = w0*w1*(mu0-mu1)^2 / sigma^2 = 0.25 * 1.5958^2 = 0.6366

    This is why separability alone cannot be trusted to detect the absence of
    water: a scene with no flood in it still scores 0.64. The confidence bands
    in :mod:`app.core.sar` are set above this floor for exactly that reason, and
    the physical threshold ceiling is what actually rejects a dry scene.
    """
    values = np.random.default_rng(4).normal(-10.0, 1.5, 20000)
    _, sep = sar.otsu_threshold(values)
    assert sep == pytest.approx(0.6366, abs=0.02)
    assert sep < sar.SEPARABILITY_HIGH, "unimodal must not reach high confidence"


def test_lee_filter_preserves_the_mean_and_reduces_variance():
    """Speckle suppression must not introduce a radiometric bias."""
    rng = np.random.default_rng(5)
    truth = np.full((64, 64), -10.0, np.float32)
    noisy = truth + rng.normal(0, 1.6, (64, 64)).astype(np.float32)
    out = sar.lee_filter(noisy)
    assert out.mean() == pytest.approx(noisy.mean(), abs=0.05)
    assert out.var() < noisy.var()
