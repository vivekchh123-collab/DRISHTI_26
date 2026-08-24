"""District registry.

Sourced facts held here: name, state, geographic centroid, district headquarters
town, geographic area (km^2), Census 2011 population, documented elevation range,
IMD district rainfall normal, and the dominant flood mechanism recorded for the
district in NDMA / state disaster-management hazard atlases.

Boundary polygons are NOT stored here. Real boundaries load from
``data/boundaries/districts.geojson`` when present (see
``scripts/fetch_boundaries.py``). When absent the system synthesises an
*approximate envelope* from centroid and area, and every API response carries
``boundary_source: "approximate-envelope"`` so no consumer mistakes it for a
surveyed boundary.

Population for 2025 is a projection, not a census figure, and is labelled as an
estimate wherever it surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# Terrain archetype selects DEM synthesis parameters in demo mode and
# hydrological parameters in both demo and live mode.
FLOODPLAIN = "alluvial-floodplain"
HILL = "steep-mountain"
COASTAL = "coastal-deltaic"
URBAN = "urban-lowland"

# Dominant flood-generating mechanism. Drives which model leads the assessment.
RIVERINE = "riverine"    # trunk river overtops / embankment breaches
FLASH = "flash"          # short, intense, steep catchment response
PLUVIAL = "pluvial"      # rainfall exceeds drainage capacity in place
SURGE = "surge"          # cyclone storm surge drives seawater inland


@dataclass(frozen=True)
class District:
    code: str
    name: str
    state: str
    lat: float               # geographic centroid
    lon: float
    area_km2: float
    population_2011: int
    terrain: str
    flood_driver: str
    hazards: Tuple[str, ...]
    drainage: str            # principal river / basin or coastline
    rainfall_normal_mm: int  # IMD district normal, 1981-2010 series
    reference_event: str     # historic event the demo scenario is calibrated to
    urban_fraction: float    # Census 2011 urban share -> imperviousness
    cropland_fraction: float # approximate net sown area share
    elev_min_m: float        # documented minimum elevation
    elev_max_m: float        # documented maximum elevation
    hq_name: str             # district headquarters / principal town
    hq_lat: float
    hq_lon: float
    peak_months: Tuple[int, ...]  # calendar months of peak flood risk

    @property
    def population_2025(self) -> int:
        """Planning estimate: Census 2011 carried forward at 1.6%/yr for 14 years."""
        return int(self.population_2011 * (1.016 ** 14))

    @property
    def density(self) -> float:
        return self.population_2025 / self.area_km2

    @property
    def seed(self) -> int:
        """Stable per-district seed, so a district looks identical every run."""
        h = 2166136261
        for ch in self.code:
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
        return h % 99991


D = District
DISTRICTS: List[District] = [
    # ---------------- Brahmaputra valley: annual riverine flooding ----------------
    D("AS-DHE", "Dhemaji", "Assam", 27.5500, 94.6500, 3237, 686133, FLOODPLAIN, RIVERINE,
      ("flood", "erosion"), "Brahmaputra - Subansiri confluence", 3200,
      "Assam floods, July 2020", 0.07, 0.52, 90, 130, "Dhemaji", 27.4833, 94.5833, (5, 6, 7, 8, 9)),
    D("AS-BAR", "Barpeta", "Assam", 26.4500, 90.9800, 2282, 1693622, FLOODPLAIN, RIVERINE,
      ("flood", "erosion"), "Brahmaputra - Beki / Manas", 2100,
      "Assam floods, June 2022", 0.09, 0.61, 28, 60, "Barpeta", 26.3220, 91.0000, (5, 6, 7, 8, 9)),
    D("AS-MOR", "Morigaon", "Assam", 26.3000, 92.3500, 1704, 957423, FLOODPLAIN, RIVERINE,
      ("flood",), "Brahmaputra - Kopili", 1800,
      "Assam floods, May 2022", 0.06, 0.58, 45, 90, "Morigaon", 26.2500, 92.3400, (5, 6, 7, 8)),
    D("AS-KAM", "Kamrup Metropolitan", "Assam", 26.1500, 91.7800, 955, 1253938, URBAN, PLUVIAL,
      ("urban-flood", "landslide"), "Bharalu - Brahmaputra", 1750,
      "Guwahati urban flash floods, 2023", 0.83, 0.11, 45, 350, "Guwahati", 26.1445, 91.7362, (5, 6, 7, 8, 9)),

    # ---------------- North Bihar: Bagmati / Burhi Gandak / Kosi ----------------
    D("BR-DAR", "Darbhanga", "Bihar", 26.1500, 85.9000, 2279, 3937385, FLOODPLAIN, RIVERINE,
      ("flood", "waterlogging"), "Bagmati - Adhwara group", 1150,
      "North Bihar floods, 2019", 0.10, 0.72, 44, 56, "Darbhanga", 26.1542, 85.8918, (6, 7, 8, 9)),
    D("BR-SIT", "Sitamarhi", "Bihar", 26.6000, 85.5000, 2199, 3423574, FLOODPLAIN, RIVERINE,
      ("flood",), "Bagmati - Lakhandei", 1200,
      "North Bihar floods, 2017", 0.06, 0.74, 52, 78, "Sitamarhi", 26.5950, 85.4800, (6, 7, 8, 9)),
    D("BR-MUZ", "Muzaffarpur", "Bihar", 26.1000, 85.4000, 3172, 4801062, FLOODPLAIN, RIVERINE,
      ("flood", "waterlogging"), "Burhi Gandak - Bagmati", 1180,
      "North Bihar floods, 2020", 0.11, 0.70, 44, 62, "Muzaffarpur", 26.1209, 85.3647, (6, 7, 8, 9)),

    # ---------------- Ghats / Himalaya: flash flood in steep catchments ----------
    D("KL-WAY", "Wayanad", "Kerala", 11.7000, 76.1300, 2131, 817420, HILL, FLASH,
      ("flash-flood", "landslide"), "Kabini headwaters", 3000,
      "Chooralmala - Mundakkai landslides, 30 July 2024", 0.04, 0.31, 700, 2100,
      "Kalpetta", 11.6087, 76.0834, (6, 7, 8)),
    D("KL-IDU", "Idukki", "Kerala", 9.9000, 77.0000, 4358, 1108974, HILL, FLASH,
      ("flash-flood", "landslide", "dam-risk"), "Periyar - Idukki reservoir", 3200,
      "Kerala floods and landslides, August 2018", 0.05, 0.36, 100, 2695,
      "Painavu", 9.8497, 76.9681, (6, 7, 8, 9)),
    D("UK-RUD", "Rudraprayag", "Uttarakhand", 30.4000, 79.0000, 1984, 242285, HILL, FLASH,
      ("flash-flood", "landslide", "glof"), "Alaknanda - Mandakini", 1450,
      "Kedarnath disaster, June 2013", 0.03, 0.12, 600, 3600,
      "Rudraprayag", 30.2844, 78.9811, (6, 7, 8)),
    D("UK-CHA", "Chamoli", "Uttarakhand", 30.5500, 79.6000, 8030, 391605, HILL, FLASH,
      ("flash-flood", "glof", "landslide"), "Alaknanda - Dhauliganga", 1300,
      "Rishiganga flash flood, 7 February 2021", 0.04, 0.08, 800, 6500,
      "Gopeshwar", 30.4083, 79.3200, (6, 7, 8)),
    D("HP-KUL", "Kullu", "Himachal Pradesh", 32.0500, 77.3000, 5503, 437903, HILL, FLASH,
      ("flash-flood", "landslide"), "Beas", 1100,
      "Himachal monsoon disaster, July 2023", 0.07, 0.10, 1000, 5800,
      "Kullu", 31.9578, 77.1092, (7, 8)),
    D("HP-MAN", "Mandi", "Himachal Pradesh", 31.7500, 76.9500, 3950, 999777, HILL, FLASH,
      ("flash-flood", "landslide"), "Beas - Uhl", 1350,
      "Mandi landslides, August 2023", 0.06, 0.14, 600, 4000,
      "Mandi", 31.7080, 76.9320, (7, 8)),

    # ---------------- Bay of Bengal coast: cyclone storm surge ----------------
    D("OD-PUR", "Puri", "Odisha", 19.9500, 85.9200, 3479, 1698730, COASTAL, SURGE,
      ("cyclone", "storm-surge", "flood"), "Bay of Bengal - Chilika", 1450,
      "Cyclone Fani, 3 May 2019", 0.16, 0.55, 0, 30, "Puri", 19.8135, 85.8312, (4, 5, 10, 11)),
    D("OD-KEN", "Kendrapara", "Odisha", 20.5200, 86.6000, 2644, 1440361, COASTAL, SURGE,
      ("cyclone", "storm-surge", "flood"), "Mahanadi delta - Bay of Bengal", 1550,
      "Cyclone Yaas, 26 May 2021", 0.05, 0.63, 0, 15, "Kendrapara", 20.5000, 86.4200, (5, 10, 11)),
    D("OD-GAN", "Ganjam", "Odisha", 19.5500, 84.7200, 8206, 3529031, COASTAL, SURGE,
      ("cyclone", "storm-surge"), "Rushikulya - Bay of Bengal", 1300,
      "Cyclone Phailin, 12 October 2013", 0.18, 0.48, 0, 1000,
      "Chhatrapur", 19.3550, 84.9800, (5, 10, 11)),
    D("OD-BAL", "Balasore", "Odisha", 21.5500, 86.8500, 3806, 2320529, COASTAL, SURGE,
      ("cyclone", "storm-surge", "flood"), "Subarnarekha - Budhabalanga", 1570,
      "Cyclone Yaas, 26 May 2021", 0.12, 0.60, 0, 100, "Balasore", 21.4934, 86.9335, (5, 6, 10, 11)),
    D("WB-S24", "South 24 Parganas", "West Bengal", 22.0500, 88.4500, 9960, 8161961, COASTAL, SURGE,
      ("cyclone", "storm-surge", "flood"), "Sundarbans delta - Hooghly", 1750,
      "Cyclone Amphan, 20 May 2020", 0.26, 0.51, 0, 12,
      "Diamond Harbour", 22.1910, 88.1900, (5, 6, 10, 11)),
    D("AP-NEL", "Nellore", "Andhra Pradesh", 14.6000, 79.6500, 13076, 2963557, COASTAL, SURGE,
      ("cyclone", "storm-surge", "flood"), "Penna delta - Pulicat", 1000,
      "Cyclone Michaung, 5 December 2023", 0.21, 0.44, 0, 250,
      "Nellore", 14.4426, 79.9865, (10, 11, 12)),
    D("TN-CUD", "Cuddalore", "Tamil Nadu", 11.6500, 79.5500, 3678, 2605914, COASTAL, SURGE,
      ("cyclone", "flood"), "Gadilam - Kollidam", 1250,
      "Cyclone Gaja, 16 November 2018", 0.29, 0.52, 0, 70,
      "Cuddalore", 11.7480, 79.7714, (10, 11, 12)),

    # ---------------- Metropolitan: pluvial / urban flooding ----------------
    D("MH-MSU", "Mumbai Suburban", "Maharashtra", 19.1700, 72.8700, 446, 9356962, URBAN, PLUVIAL,
      ("urban-flood", "landslide"), "Mithi - Oshiwara - Poisar", 2400,
      "Mumbai deluge, 26 July 2005", 1.00, 0.01, 0, 450, "Bandra", 19.0596, 72.8295, (6, 7, 8, 9)),
    D("TN-CHN", "Chennai", "Tamil Nadu", 13.0827, 80.2500, 426, 4646732, URBAN, PLUVIAL,
      ("urban-flood", "cyclone"), "Adyar - Cooum - Buckingham Canal", 1400,
      "Chennai floods, December 2015", 1.00, 0.01, 0, 30, "Chennai", 13.0827, 80.2707, (10, 11, 12)),
]
del D

BY_CODE: Dict[str, District] = {d.code: d for d in DISTRICTS}


def get(code: str) -> District:
    try:
        return BY_CODE[code]
    except KeyError:
        raise KeyError("unknown district code %r" % (code,)) from None


def search(q: str) -> List[District]:
    q = (q or "").strip().lower()
    if not q:
        return list(DISTRICTS)
    return [d for d in DISTRICTS
            if q in d.name.lower() or q in d.state.lower() or q in d.code.lower()]


def states() -> List[str]:
    return sorted({d.state for d in DISTRICTS})
