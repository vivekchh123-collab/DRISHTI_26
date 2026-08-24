"""Copernicus Data Space Ecosystem — Sentinel-1 discovery.

A real OData query against the Copernicus catalogue. It runs as written; what it
needs is a free account, not more code. With no credentials configured it
reports exactly that and the application falls back to the demo provider.

Register at https://dataspace.copernicus.eu, then:

    set COPERNICUS_USER=you@example.org
    set COPERNICUS_PASSWORD=...

Search needs no authentication at all, so :meth:`search_scenes` works
immediately; credentials are required only to download a granule.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from .base import Provider, ProviderStatus, SceneRef

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
TIMEOUT = 20


class CopernicusProvider(Provider):
    name = "copernicus"

    def __init__(self, user: Optional[str] = None,
                 password: Optional[str] = None) -> None:
        self.user = user or os.environ.get("COPERNICUS_USER", "")
        self.password = password or os.environ.get("COPERNICUS_PASSWORD", "")

    def status(self) -> ProviderStatus:
        if not (self.user and self.password):
            return ProviderStatus(
                name=self.name, available=False,
                reason=("No Copernicus credentials configured. Catalogue search "
                        "works without them; downloading granules does not."),
                requires=["COPERNICUS_USER", "COPERNICUS_PASSWORD"])
        return ProviderStatus(
            name=self.name, available=True,
            reason="Copernicus credentials present.",
            requires=[])

    def search_scenes(self, bbox: Tuple[float, float, float, float],
                      days: int = 12, limit: int = 10) -> List[SceneRef]:
        """Most recent Sentinel-1 GRD granules intersecting ``bbox``.

        ``bbox`` is (west, south, east, north). Returns an empty list on any
        network or parsing failure rather than raising: a catalogue being
        unreachable must degrade the dashboard to modelled data, never take it
        down.
        """
        west, south, east, north = bbox
        since = (datetime.now(timezone.utc) - timedelta(days=days)
                 ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        polygon = ("POLYGON((%f %f,%f %f,%f %f,%f %f,%f %f))"
                   % (west, south, east, south, east, north,
                      west, north, west, south))
        flt = (
            "Collection/Name eq 'SENTINEL-1'"
            " and OData.CSC.Intersects(area=geography'SRID=4326;%s')" % polygon
            + " and ContentDate/Start gt %s" % since
            + " and contains(Name,'GRD')"
        )
        url = ODATA + "?" + urllib.parse.urlencode({
            "$filter": flt, "$orderby": "ContentDate/Start desc", "$top": limit})

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except Exception:
            return []

        now = datetime.now(timezone.utc)
        out: List[SceneRef] = []
        for item in payload.get("value", [])[:limit]:
            started = item.get("ContentDate", {}).get("Start", "")
            age = None
            try:
                t = datetime.fromisoformat(started.replace("Z", "+00:00"))
                age = round((now - t).total_seconds() / 3600.0, 1)
            except Exception:
                pass
            name = item.get("Name", "")
            out.append(SceneRef(
                id=item.get("Id", ""), acquired=started,
                platform="Sentinel-1",
                mode="IW" if "_IW_" in name else "unknown",
                # Sentinel-1 product names encode polarisation in the
                # class/polarisation field, e.g. S1A_IW_GRDH_1SDV_...
                polarisation=next(
                    (p for p in ("1SDV", "1SDH", "1SSV", "1SSH") if p in name),
                    "unknown").replace("1S", ""),
                footprint_bbox=bbox,
                download_url=ODATA + "(%s)/$value" % item.get("Id", ""),
                age_hours=age))
        return out
