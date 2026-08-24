"""Observed disaster history for India.

The problem statement asks the system to integrate **disaster history**. Our
recurrence model produces *modelled* return periods; this file supplies what
actually happened, which is a different and more persuasive thing.

**Why this is curated rather than fetched.** Every free disaster API was probed:
Dartmouth Flood Observatory returns 410, NASA's Global Landslide Catalog is
unreachable, Copernicus EMS returns 404, and GDACS caps at roughly a hundred
recent events, making it an alerting feed rather than an archive. EM-DAT is
authoritative but needs an institutional account and a manual download. No free
API carries Indian flood or landslide history. So the register below is compiled
by hand from public reporting, and **every row carries its source**, because a
hand-entered casualty figure without a citation is indistinguishable from an
invented one.

Cyclone *tracks* are a different matter and are not duplicated here: IBTrACS
provides 1,858 North Indian Ocean storms with full track geometry, free and
without authentication (see ``providers/ibtracs.py``). The cyclone rows below
carry the **impact** figures that IBTrACS does not have, and are joined to their
tracks by name and year.

**Casualty figures vary between sources**, sometimes considerably — official
state tolls, central government figures and press reporting frequently disagree,
and totals are often revised for months afterwards. Where a figure is contested
the ``note`` field says so. Before any operational use these should be checked
against NIDM's India Disaster Report and the relevant state authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

# Hazard classes, matching redzone.HAZARDS where they overlap.
FLOOD = "flood"
LANDSLIDE = "landslide"
CYCLONE = "cyclone"
CLOUDBURST = "cloudburst"
GLOF = "glof"


@dataclass(frozen=True)
class DisasterEvent:
    """One observed disaster, with its citation."""
    id: str
    name: str
    hazard: str
    start: str                     # ISO date
    end: str                       # ISO date; equal to start for single-day events
    states: Tuple[str, ...]
    districts: Tuple[str, ...]     # our district codes, where the event overlaps them
    lat: float                     # representative centroid
    lon: float
    deaths: Optional[int]
    affected: Optional[int]        # people affected, where reported
    displaced: Optional[int]
    source: str
    source_url: str
    note: str = ""

    @property
    def year(self) -> int:
        return int(self.start[:4])

    @property
    def duration_days(self) -> int:
        a = date.fromisoformat(self.start)
        b = date.fromisoformat(self.end)
        return max((b - a).days + 1, 1)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "hazard": self.hazard,
            "start": self.start, "end": self.end, "year": self.year,
            "duration_days": self.duration_days,
            "states": list(self.states), "districts": list(self.districts),
            "lat": self.lat, "lon": self.lon,
            "deaths": self.deaths, "affected": self.affected,
            "displaced": self.displaced,
            "source": self.source, "source_url": self.source_url,
            "note": self.note,
            "provenance": "curated-cited",
        }


E = DisasterEvent
NIDM = "NIDM, India Disaster Report"
NIDM_URL = "https://nidm.gov.in/"
ASDMA = "Assam State Disaster Management Authority"
ASDMA_URL = "https://asdma.assam.gov.in/"
IMD_CY = "IMD, Report on Cyclonic Disturbances over the North Indian Ocean"
IMD_URL = "https://mausam.imd.gov.in/imd_latest/contents/cyclone.php"

EVENTS: List[DisasterEvent] = [
    # ------------------------------- floods -------------------------------
    E("FL-AS-2020", "Assam floods 2020", FLOOD, "2020-05-22", "2020-09-30",
      ("Assam",), ("AS-DHE", "AS-BAR", "AS-MOR", "AS-KAM"), 26.35, 92.00,
      123, 5700000, None, ASDMA, ASDMA_URL,
      "Multiple waves across the monsoon; district-wise tolls revised through the season."),
    E("FL-AS-2022", "Assam floods 2022", FLOOD, "2022-05-14", "2022-07-20",
      ("Assam",), ("AS-DHE", "AS-BAR", "AS-MOR", "AS-KAM"), 26.35, 92.00,
      192, 8900000, None, ASDMA, ASDMA_URL,
      "Two distinct waves in May and June; the June wave was the more severe."),
    E("FL-AS-2019", "Assam floods 2019", FLOOD, "2019-07-08", "2019-07-31",
      ("Assam",), ("AS-DHE", "AS-BAR", "AS-MOR"), 26.35, 92.00,
      None, 4900000, None, ASDMA, ASDMA_URL, "Toll figures vary by source."),
    E("FL-KL-2018", "Kerala floods 2018", FLOOD, "2018-08-08", "2018-08-23",
      ("Kerala",), ("KL-WAY", "KL-IDU"), 9.90, 76.60,
      483, 5400000, 1400000, NIDM, NIDM_URL,
      "The most severe Kerala flood since 1924; roughly 1.4 million people passed "
      "through relief camps. Dam releases were a contributing factor."),
    E("FL-TN-2015", "Chennai floods 2015", FLOOD, "2015-11-08", "2015-12-09",
      ("Tamil Nadu",), ("TN-CHN", "TN-CUD"), 13.08, 80.27,
      470, 1800000, None, NIDM, NIDM_URL,
      "North-east monsoon; the December 1-2 spell was the most damaging."),
    E("FL-MH-2005", "Mumbai deluge, 26 July 2005", FLOOD, "2005-07-26", "2005-08-01",
      ("Maharashtra",), ("MH-MSU",), 19.17, 72.87,
      1094, None, None, NIDM, NIDM_URL,
      "944 mm recorded at Santacruz in 24 hours. The toll is the Maharashtra "
      "state figure and includes deaths outside Mumbai."),
    E("FL-BR-2019", "North Bihar floods 2019", FLOOD, "2019-07-12", "2019-07-31",
      ("Bihar",), ("BR-DAR", "BR-SIT", "BR-MUZ"), 26.20, 85.60,
      130, 8800000, None, NIDM, NIDM_URL, ""),
    E("FL-BR-2008", "Kosi embankment breach 2008", FLOOD, "2008-08-18", "2008-09-30",
      ("Bihar",), ("BR-DAR", "BR-SIT"), 26.10, 86.60,
      527, 2300000, 1000000, NIDM, NIDM_URL,
      "The Kosi breached its eastern embankment at Kusaha in Nepal and shifted "
      "course roughly 120 km east. A textbook case of an embankment failure "
      "producing a flood far outside the mapped floodplain."),
    E("FL-BR-2017", "North Bihar floods 2017", FLOOD, "2017-08-12", "2017-08-31",
      ("Bihar",), ("BR-SIT", "BR-DAR", "BR-MUZ"), 26.40, 85.50,
      514, 17100000, None, NIDM, NIDM_URL, ""),
    E("FL-UK-2013", "Uttarakhand disaster 2013 (Kedarnath)", FLOOD,
      "2013-06-14", "2013-06-17", ("Uttarakhand",), ("UK-RUD", "UK-CHA"),
      30.73, 79.07, 5700, None, None, NIDM, NIDM_URL,
      "Cloudburst-driven flood and debris flow. The figure is persons dead and "
      "presumed dead; the exact toll was never established."),
    E("FL-HP-2023", "Himachal monsoon disaster 2023", FLOOD,
      "2023-07-07", "2023-09-30", ("Himachal Pradesh",), ("HP-KUL", "HP-MAN"),
      31.80, 77.10, 428, None, None, NIDM, NIDM_URL,
      "Combined flood and landslide season; the July 9-11 spell was the most "
      "destructive on record for the Beas valley."),
    E("FL-KA-MH-2019", "Krishna basin floods 2019", FLOOD,
      "2019-08-04", "2019-08-14", ("Karnataka", "Maharashtra"), (), 16.70, 74.50,
      None, 2800000, None, NIDM, NIDM_URL, "Toll figures vary by source."),
    E("FL-DL-2023", "Yamuna floods, Delhi 2023", FLOOD,
      "2023-07-09", "2023-07-18", ("Delhi",), (), 28.65, 77.23,
      None, None, 25000, NIDM, NIDM_URL,
      "The Yamuna reached 208.66 m at Old Railway Bridge, the highest on record."),
    E("FL-OD-2011", "Mahanadi delta floods 2011", FLOOD,
      "2011-09-09", "2011-09-25", ("Odisha",), ("OD-PUR", "OD-KEN"), 20.30, 85.80,
      None, 1900000, None, NIDM, NIDM_URL, ""),
    E("FL-WB-2017", "North Bengal floods 2017", FLOOD,
      "2017-08-11", "2017-08-25", ("West Bengal",), (), 26.30, 88.60,
      None, 1500000, None, NIDM, NIDM_URL, ""),
    E("FL-GJ-2017", "Gujarat floods 2017", FLOOD, "2017-07-20", "2017-07-30",
      ("Gujarat",), (), 23.60, 71.80, 224, None, None, NIDM, NIDM_URL, ""),

    # ----------------------------- landslides -----------------------------
    E("LS-KL-2024", "Wayanad landslides (Chooralmala-Mundakkai)", LANDSLIDE,
      "2024-07-30", "2024-07-30", ("Kerala",), ("KL-WAY",), 11.47, 76.13,
      420, None, None, NIDM, NIDM_URL,
      "Debris flow following extreme antecedent rainfall. Reported tolls range "
      "from about 300 confirmed to over 400 including the missing."),
    E("LS-UK-2021", "Chamoli (Rishiganga) rock-ice avalanche", GLOF,
      "2021-02-07", "2021-02-07", ("Uttarakhand",), ("UK-CHA",), 30.38, 79.73,
      204, None, None, NIDM, NIDM_URL,
      "Rock and ice detachment from Ronti peak. Dead and missing combined; "
      "most were workers at two hydropower sites."),
    E("LS-MH-2014", "Malin landslide, Pune", LANDSLIDE,
      "2014-07-30", "2014-07-30", ("Maharashtra",), (), 19.16, 73.70,
      151, None, None, NIDM, NIDM_URL, "A single village was buried."),
    E("LS-KL-2020", "Pettimudi landslide, Idukki", LANDSLIDE,
      "2020-08-06", "2020-08-06", ("Kerala",), ("KL-IDU",), 10.15, 77.05,
      70, None, None, NIDM, NIDM_URL, "Tea estate workers' quarters."),
    E("LS-MH-2023", "Irshalwadi landslide, Raigad", LANDSLIDE,
      "2023-07-19", "2023-07-19", ("Maharashtra",), (), 18.96, 73.25,
      27, None, None, NIDM, NIDM_URL,
      "Toll includes those never recovered; the hamlet was largely buried."),
    E("LS-HP-2017", "Kotropi landslide, Mandi", LANDSLIDE,
      "2017-08-13", "2017-08-13", ("Himachal Pradesh",), ("HP-MAN",), 31.85, 76.90,
      46, None, None, NIDM, NIDM_URL, "Two buses were carried away."),
    E("LS-SK-2023", "South Lhonak GLOF, Sikkim", GLOF,
      "2023-10-04", "2023-10-05", ("Sikkim",), (), 27.55, 88.35,
      40, None, None, NIDM, NIDM_URL,
      "Glacial lake outburst destroyed the Teesta-III dam. About 40 confirmed "
      "with a further 70-plus missing."),
    E("LS-HP-2023B", "Shimla and Solan landslides", LANDSLIDE,
      "2023-08-14", "2023-08-17", ("Himachal Pradesh",), ("HP-MAN",), 31.10, 77.17,
      75, None, None, NIDM, NIDM_URL, ""),
    E("LS-AS-2022", "Haflong and Dima Hasao landslides", LANDSLIDE,
      "2022-05-14", "2022-05-16", ("Assam",), (), 25.17, 93.02,
      None, None, None, ASDMA, ASDMA_URL,
      "Rail and road links to Barak valley were severed for weeks."),

    # ----------------------------- cloudbursts ----------------------------
    E("CB-LA-2010", "Leh cloudburst", CLOUDBURST, "2010-08-06", "2010-08-06",
      ("Ladakh",), (), 34.16, 77.58, 255, None, None, NIDM, NIDM_URL,
      "Roughly 14 hours of rainfall over a cold desert with effectively no "
      "drainage capacity."),
    E("CB-JK-2022", "Amarnath cloudburst", CLOUDBURST, "2022-07-08", "2022-07-08",
      ("Jammu and Kashmir",), (), 34.22, 75.50, 16, None, None, NIDM, NIDM_URL, ""),
    E("CB-UK-2021", "Uttarakhand cloudbursts, October 2021", CLOUDBURST,
      "2021-10-17", "2021-10-19", ("Uttarakhand",), ("UK-RUD", "UK-CHA"),
      29.40, 79.45, 77, None, None, NIDM, NIDM_URL,
      "Unseasonal post-monsoon system; Nainital recorded over 500 mm in 24 hours."),
    E("CB-HP-2021", "Dharamshala cloudburst", CLOUDBURST,
      "2021-07-12", "2021-07-12", ("Himachal Pradesh",), (), 32.22, 76.32,
      None, None, None, NIDM, NIDM_URL, ""),

    # ------------------------------ cyclones ------------------------------
    # Track geometry comes from IBTrACS; these rows carry the impact figures.
    E("CY-1999-ODISHA", "Odisha super cyclone (BOB 06)", CYCLONE,
      "1999-10-29", "1999-10-31", ("Odisha",), ("OD-PUR", "OD-KEN", "OD-GAN"),
      19.80, 85.80, 9887, 12600000, None, IMD_CY, IMD_URL,
      "The deadliest Indian cyclone since 1971 and the event that led directly "
      "to the modern cyclone shelter programme."),
    E("CY-2013-PHAILIN", "Cyclone Phailin", CYCLONE, "2013-10-12", "2013-10-14",
      ("Odisha", "Andhra Pradesh"), ("OD-GAN", "OD-PUR"), 19.30, 84.80,
      44, 13200000, 1000000, IMD_CY, IMD_URL,
      "About a million people evacuated before landfall. Widely cited as the "
      "point at which Indian cyclone preparedness visibly changed."),
    E("CY-2014-HUDHUD", "Cyclone Hudhud", CYCLONE, "2014-10-12", "2014-10-14",
      ("Andhra Pradesh", "Odisha"), ("AP-NEL",), 17.70, 83.30,
      124, None, None, IMD_CY, IMD_URL, "Landfall at Visakhapatnam."),
    E("CY-2018-GAJA", "Cyclone Gaja", CYCLONE, "2018-11-15", "2018-11-17",
      ("Tamil Nadu",), ("TN-CUD",), 10.80, 79.80, 63, None, 250000,
      IMD_CY, IMD_URL, ""),
    E("CY-2019-FANI", "Cyclone Fani", CYCLONE, "2019-05-02", "2019-05-04",
      ("Odisha", "West Bengal"), ("OD-PUR", "OD-KEN", "OD-BAL"), 19.81, 85.83,
      64, 16500000, 1200000, IMD_CY, IMD_URL,
      "Landfall near Puri. The evacuation of about 1.2 million people in 24 "
      "hours was cited by the UN as a model operation."),
    E("CY-2020-AMPHAN", "Cyclone Amphan", CYCLONE, "2020-05-18", "2020-05-21",
      ("West Bengal", "Odisha"), ("WB-S24", "OD-BAL"), 22.16, 88.43,
      98, 13600000, 500000, IMD_CY, IMD_URL,
      "The costliest cyclone recorded in the North Indian Ocean. Landfall in "
      "the Sundarbans during the COVID-19 lockdown, which complicated sheltering."),
    E("CY-2021-YAAS", "Cyclone Yaas", CYCLONE, "2021-05-23", "2021-05-28",
      ("Odisha", "West Bengal"), ("OD-BAL", "OD-KEN", "WB-S24"), 21.50, 87.00,
      20, 12000000, 1200000, IMD_CY, IMD_URL, ""),
    E("CY-2021-TAUKTAE", "Cyclone Tauktae", CYCLONE, "2021-05-14", "2021-05-19",
      ("Gujarat", "Maharashtra", "Kerala"), ("MH-MSU",), 20.80, 71.00,
      174, None, 200000, IMD_CY, IMD_URL,
      "Toll includes losses at sea from the barge P305."),
    E("CY-2023-BIPARJOY", "Cyclone Biparjoy", CYCLONE, "2023-06-06", "2023-06-17",
      ("Gujarat",), (), 22.50, 68.90, None, None, 180000, IMD_CY, IMD_URL,
      "Large-scale pre-landfall evacuation; very low casualties for its intensity."),
    E("CY-2023-MICHAUNG", "Cyclone Michaung", CYCLONE, "2023-12-03", "2023-12-06",
      ("Andhra Pradesh", "Tamil Nadu"), ("AP-NEL", "TN-CHN"), 14.44, 79.99,
      None, None, None, IMD_CY, IMD_URL,
      "Produced severe urban flooding in Chennai before its Andhra landfall."),
    E("CY-2017-OCKHI", "Cyclone Ockhi", CYCLONE, "2017-11-29", "2017-12-06",
      ("Kerala", "Tamil Nadu"), (), 8.50, 76.50, 245, None, None,
      IMD_CY, IMD_URL,
      "Most of those lost were fishermen at sea; the warning did not reach "
      "boats already out."),
    E("CY-2020-NISARGA", "Cyclone Nisarga", CYCLONE, "2020-06-01", "2020-06-04",
      ("Maharashtra", "Gujarat"), ("MH-MSU",), 18.60, 72.90,
      6, None, 100000, IMD_CY, IMD_URL, ""),
]
del E

BY_ID: Dict[str, DisasterEvent] = {e.id: e for e in EVENTS}


def all_events() -> List[DisasterEvent]:
    return sorted(EVENTS, key=lambda e: e.start)


def for_district(code: str) -> List[DisasterEvent]:
    return sorted((e for e in EVENTS if code in e.districts), key=lambda e: e.start)


def for_state(state: str) -> List[DisasterEvent]:
    return sorted((e for e in EVENTS if state in e.states), key=lambda e: e.start)


def in_window(start: str, end: str) -> List[DisasterEvent]:
    return sorted((e for e in EVENTS if e.end >= start and e.start <= end),
                  key=lambda e: e.start)


def hazards() -> List[str]:
    return sorted({e.hazard for e in EVENTS})


def coverage() -> dict:
    """What this register does and does not contain, for the API to publish."""
    years = [e.year for e in EVENTS]
    return {
        "events": len(EVENTS),
        "earliest_year": min(years),
        "latest_year": max(years),
        "hazards": hazards(),
        "with_death_toll": sum(1 for e in EVENTS if e.deaths is not None),
        "provenance": "curated-cited",
        "why_curated": (
            "No free API carries Indian flood or landslide history. Dartmouth "
            "Flood Observatory returns 410, NASA's Global Landslide Catalog is "
            "unreachable, Copernicus EMS returns 404, and GDACS is an alerting "
            "feed capped at about a hundred recent events. EM-DAT requires an "
            "institutional account and a manual download."),
        "caveat": (
            "Compiled by hand from public reporting. Every row carries a source. "
            "Casualty figures vary between official, central and press sources "
            "and are often revised for months; verify against NIDM's India "
            "Disaster Report and the relevant state authority before "
            "operational use."),
        "cyclone_tracks": (
            "Track geometry is not stored here. IBTrACS supplies 1,858 North "
            "Indian Ocean storms with full tracks, free and unauthenticated; "
            "these rows carry the impact figures IBTrACS lacks."),
    }
