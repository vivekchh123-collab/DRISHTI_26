"""Bake the local cloud and rainfall norms every anomaly is measured against.

    python scripts/fetch_climatology.py

An anomaly needs a normal. A troposphere-deep cloud column over Cherrapunji in
July is a Tuesday; the same column over Jaisalmer is an emergency. Without a
per-location, per-week baseline the live screen fires on every day of the
monsoon, and a screen that fires every day is one nobody reads by June.

So for each modelled district this fetches five years of hourly cloud cover at
three levels, plus rainfall, from the Open-Meteo archive (free, no account),
reduces it to a mean and a 90th percentile **per calendar week**, and writes a
small JSON file.

Baked rather than fetched live for the same reason the elevation is: a five-year
normal does not change between refreshes, and Open-Meteo bills per coordinate.
Twenty-two archive calls on every cycle would spend the rate limit that the live
forecast actually needs.

Re-run it once a year. Nothing breaks if it is stale by a season — the norms
move slowly — and if the file is missing entirely the cloud read still works and
simply reports that it has no baseline, which is honest rather than silent.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.data import districts as districts_data  # noqa: E402

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OUT = os.path.join(os.path.dirname(__file__), "..", "backend", "app", "data",
                   "climatology", "cloud_norms.json")

YEARS = 5
# Cloud at all three levels, plus rainfall.
#
# **CAPE is deliberately absent.** Open-Meteo's ERA5 archive does not serve it —
# a request returns an empty column, not an error — so there is no five-year CAPE
# baseline to be had here. Baking a column of zeros and dividing by it would
# manufacture an anomaly ratio out of nothing. CAPE is still read live and
# reported against the standard instability bands, which are absolute and need
# no local baseline; what gets an anomaly is the cloud structure.
HOURLY = ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
          "precipitation")

# Open-Meteo's archive is generous but not unlimited, and a 429 halfway through
# leaves a half-baked file. One district at a time with a pause between is slow
# and finishes.
PAUSE_S = 1.5


def fetch(url: str, params: dict, timeout: float = 120.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + q,
                                 headers={"User-Agent": "drishti/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read())


def percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def weekly_norms(times, series: dict) -> dict:
    """Reduce five years of hourly values to a per-calendar-week summary."""
    buckets = defaultdict(lambda: defaultdict(list))
    for i, t in enumerate(times):
        # "2021-07-14T13:00" -> week number
        try:
            tm = time.strptime(t[:10], "%Y-%m-%d")
        except Exception:
            continue
        wk = int(time.strftime("%W", tm))
        for name, arr in series.items():
            v = arr[i] if i < len(arr) else None
            if v is not None:
                buckets[wk][name].append(float(v))

    def mean(v):
        return round(sum(v) / len(v), 1) if v else 0.0

    out = {}
    for wk, cols in buckets.items():
        high = cols.get("cloud_cover_high", [])
        mid = cols.get("cloud_cover_mid", [])
        low = cols.get("cloud_cover_low", [])
        rain = cols.get("precipitation", [])
        if not high and not low:
            continue
        levels = [v for v in (mean(low), mean(mid), mean(high)) if v]
        out[str(wk)] = {
            "cloud_low_mean": mean(low),
            "cloud_mid_mean": mean(mid),
            "cloud_high_mean": mean(high),
            # Mean cover across the three levels: the baseline "how deep is the
            # column normally, here, this week" that a live reading is called
            # anomalous against.
            "column_depth_mean": round(sum(levels) / len(levels), 1) if levels else 0.0,
            "column_depth_p90": round(percentile(
                [(a + b + c) / 3.0 for a, b, c in zip(low, mid, high)], 90), 1)
            if (low and mid and high) else 0.0,
            "rain_weekly_mean_mm": round(sum(rain) / max(YEARS, 1), 1) if rain else 0.0,
            "hours": len(high) or len(low),
        }
    return out


def main() -> int:
    end = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * 6))
    start = time.strftime("%Y-%m-%d",
                          time.gmtime(time.time() - 86400 * 365 * YEARS))
    print("Cloud and rainfall norms — Open-Meteo archive, %s to %s" % (start, end))

    locations, failed = {}, []
    for i, d in enumerate(districts_data.DISTRICTS, 1):
        key = "%.2f,%.2f" % (d.lat, d.lon)
        try:
            blob = fetch(ARCHIVE, {
                "latitude": "%.4f" % d.lat, "longitude": "%.4f" % d.lon,
                "start_date": start, "end_date": end,
                "hourly": ",".join(HOURLY), "timezone": "UTC"})
        except Exception as exc:
            print("  %-22s FAILED (%s)" % (d.name, exc))
            failed.append(d.code)
            time.sleep(PAUSE_S * 2)
            continue

        h = blob.get("hourly") or {}
        times = h.get("time") or []
        wk = weekly_norms(times, {k: h.get(k) or [] for k in HOURLY})
        locations[key] = {"district": d.code, "name": d.name, "weekly": wk}
        print("  %2d/%d  %-22s %d weeks from %d hours"
              % (i, len(districts_data.DISTRICTS), d.name, len(wk), len(times)))
        time.sleep(PAUSE_S)

    if not locations:
        print("\nnothing fetched — leaving any existing file alone")
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({
            "source": "Open-Meteo ERA5 archive (no API key)",
            "source_url": "https://open-meteo.com/en/docs/historical-weather-api",
            "years": YEARS,
            "window": [start, end],
            "baked_at": time.strftime("%Y-%m-%d", time.gmtime()),
            "note": ("Per-calendar-week means of cloud cover at three levels "
                     "and of rainfall - the baseline every live cloud reading "
                     "is called anomalous against. CAPE is absent because the "
                     "ERA5 archive does not serve it; it is read live and "
                     "judged against absolute instability bands instead. "
                     "Missing weeks simply report no baseline."),
            "locations": locations,
        }, fh, separators=(",", ":"))

    print("\n  %d locations, %d failed, %.2f MB"
          % (len(locations), len(failed), os.path.getsize(OUT) / 1e6))
    if failed:
        print("  failed: %s — re-run to fill them in" % ", ".join(failed))
    print("  -> %s" % os.path.abspath(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
