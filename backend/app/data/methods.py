"""The citation table.

Served machine-readably at ``/api/methods`` and rendered in the UI. Every
algorithm the system uses appears here with its source, its role, and — as
importantly — its stated limitation.

This exists because the first question a technical reviewer asks is "how does it
actually work", and the second is "what does it get wrong". Having both answers
in one place, generated from the same table the code is documented against, is
worth more than any amount of assertion.
"""

from __future__ import annotations

from typing import Dict, List

METHODS: List[Dict[str, str]] = [
    {
        "step": "Slope and aspect",
        "method": "Horn 3x3 finite difference",
        "source": "Horn, B.K.P. (1981). Hill shading and the reflectance map.",
        "module": "core/terrain.py:slope_aspect",
        "limitation": "Smooths genuine breaks of slope at the cell scale.",
    },
    {
        "step": "Depression filling",
        "method": "Priority-flood",
        "source": "Wang, L. & Liu, H. (2006). An efficient method for identifying "
                  "and filling surface depressions.",
        "module": "core/terrain.py:fill_depressions",
        "limitation": "Removes real closed basins, which on a karst or "
                      "endorheic landscape are not artefacts.",
    },
    {
        "step": "Flat resolution",
        "method": "Combined outlet and high-edge gradients",
        "source": "Garbrecht, J. & Martz, L. (1997). The assignment of drainage "
                  "direction over flat surfaces.",
        "module": "core/terrain.py:resolve_flats",
        "limitation": "Imposes a drainage pattern on ground that genuinely has "
                      "none; the pattern is plausible, not observed.",
    },
    {
        "step": "Flow routing",
        "method": "D8 steepest descent",
        "source": "O'Callaghan, J. & Mark, D. (1984). The extraction of drainage "
                  "networks from digital elevation data.",
        "module": "core/terrain.py:flow_network",
        "limitation": "Single-direction routing cannot represent divergent flow "
                      "on an alluvial fan or a braided reach.",
    },
    {
        "step": "Flooding order",
        "method": "HAND — Height Above Nearest Drainage",
        "source": "Renno, C. et al. (2008); Nobre, A. et al. (2011). "
                  "Height Above the Nearest Drainage.",
        "module": "core/terrain.py:hand",
        "limitation": "Assumes a level water surface normal to the channel; "
                      "ignores embankments finer than the DEM.",
    },
    {
        "step": "Waterlogging susceptibility",
        "method": "Topographic wetness index in a weighted overlay",
        "source": "Beven, K. & Kirkby, M. (1979). A physically based, variable "
                  "contributing area model of basin hydrology.",
        "module": "core/waterlogging.py",
        "limitation": "Steady-state index; takes no account of soil storage "
                      "already used up earlier in the season.",
    },
    {
        "step": "Rainfall to runoff",
        "method": "SCS Curve Number with antecedent moisture adjustment",
        "source": "USDA-NRCS, National Engineering Handbook, Part 630.",
        "module": "core/hydrology.py:runoff_depth",
        "limitation": "Event-based and lumped; curve numbers are inferred from "
                      "land cover rather than measured.",
    },
    {
        "step": "Catchment lag",
        "method": "Gamma unit hydrograph, area-scaled time to peak",
        "source": "USDA-NRCS dimensionless unit hydrograph.",
        "module": "core/hydrology.py:unit_hydrograph",
        "limitation": "One lag per area class, so timing within a class is "
                      "identical.",
    },
    {
        "step": "Channel geometry",
        "method": "Downstream hydraulic geometry",
        "source": "Leopold, L. & Maddock, T. (1953). The hydraulic geometry of "
                  "stream channels.",
        "module": "core/hydrology.py:channel_geometry",
        "limitation": "Regional averages; a specific reach may differ several-fold.",
    },
    {
        "step": "Stage from discharge",
        "method": "Manning's equation, compound section, solved by bisection",
        "source": "Chow, V.T. (1959). Open-Channel Hydraulics.",
        "module": "core/hydrology.py:stage_compound",
        "limitation": "Quasi-steady and one-dimensional; no backwater, no "
                      "momentum, no unsteady wave routing.",
    },
    {
        "step": "Inundation depth",
        "method": "Water-surface minus terrain, referenced through HAND",
        "source": "Cohen, S. et al. (2018). Estimating floodwater depths from "
                  "flood inundation maps and topography (FwDET).",
        "module": "core/flood.py:inundation_depth",
        "limitation": "Does not conserve volume; a hollow can be wetted with no "
                      "physical path for water to reach it.",
    },
    {
        "step": "Water detection from radar",
        "method": "Automatic histogram thresholding on backscatter",
        "source": "Otsu, N. (1979). A threshold selection method from gray-level "
                  "histograms.",
        "module": "core/sar.py",
        "limitation": "Dry sand, smooth tarmac and radar shadow are "
                      "indistinguishable from water without the terrain masks.",
    },
    {
        "step": "Radar speckle suppression",
        "method": "Adaptive local-statistics filter",
        "source": "Lee, J.-S. (1980). Digital image enhancement and noise "
                  "filtering by use of local statistics.",
        "module": "core/sar.py",
        "limitation": "Trades spatial resolution for radiometric stability.",
    },
    {
        "step": "Population distribution",
        "method": "Dasymetric allocation weighted by built-up intensity",
        "source": "Standard dasymetric mapping; cf. WorldPop methodology.",
        "module": "core/exposure.py:allocate_population",
        "limitation": "Preserves the district total exactly but the within-"
                      "district distribution is modelled, not surveyed.",
    },
    {
        "step": "Landslide hazard zonation",
        "method": "LHEF rating scheme — six-factor Total Estimated Hazard",
        "source": "BIS IS 14496 (Part 2): 1998 — Preparation of Landslide "
                  "Hazard Zonation Maps in Mountainous Terrains (the scheme "
                  "used by the Geological Survey of India).",
        "module": "core/landslide.py:assess",
        "limitation": "Lithology and structure require a GSI map and cannot be "
                      "derived from elevation; they are held at a neutral "
                      "rating, so absolute BIS zones are conservative until a "
                      "geology raster is supplied.",
    },
    {
        "step": "Landslide triggering",
        "method": "Rainfall intensity-duration threshold, wet-antecedent adjusted",
        "source": "Caine, N. (1980). The rainfall intensity-duration control of "
                  "shallow landslides and debris flows.",
        "module": "core/landslide.py:rainfall_trigger",
        "limitation": "A global relation, conservative for the Indian monsoon. "
                      "Local calibration needs a regional landslide inventory.",
    },
    {
        "step": "Landslide runout",
        "method": "Angle of reach along D8 flow paths",
        "source": "Corominas, J. (1996). The angle of reach as a mobility index.",
        "module": "core/landslide.py:runout",
        "limitation": "Fixed 22-degree reach suits small shallow slides; large "
                      "rock avalanches travel considerably further.",
    },
    {
        "step": "Coastal erosion",
        "method": "Shoreline retreat from sea-level rise and profile slope",
        "source": "Bruun, P. (1962). Sea level rise as a cause of shore erosion.",
        "module": "core/erosion.py:assess",
        "limitation": "First-order and one-dimensional; ignores longshore "
                      "transport and engineered defences. Decadal retreat is "
                      "sub-pixel at this resolution, so the rate is the "
                      "meaningful output rather than the area.",
    },
    {
        "step": "Red zone identification",
        "method": "Multi-hazard recurrence — shortest return period at which "
                  "any hazard renders land uninhabitable",
        "source": "NDMA National Disaster Management Plan, which frames "
                  "relocation of habitations in hazard-prone zones as an "
                  "evidence-based mitigation measure.",
        "module": "core/redzone.py:build",
        "limitation": "Quantised to the three modelled events, so a return "
                      "period can only be 5, 25, 100 years or never.",
    },
    {
        "step": "Relocation site capacity",
        "method": "Net land area at a sustainable settlement density",
        "source": "Sphere Handbook shelter and settlement standards (45 m2 per "
                  "person of site area as an absolute floor).",
        "module": "core/relocation.py",
        "limitation": "Physical capacity only. Land acquisition, tenure, "
                      "consent and compensation are outside the model.",
    },
    {
        "step": "Response actions",
        "method": "Rule table over depth, isolation, duration and exposure",
        "source": "NDMA National Guidelines on Management of Floods; Incident "
                  "Response System; IPHS; Sphere Handbook.",
        "module": "data/sops.py",
        "limitation": "Encodes national doctrine; a district's own plan may "
                      "differ and should take precedence.",
    },
]


def table() -> List[Dict[str, str]]:
    return list(METHODS)
