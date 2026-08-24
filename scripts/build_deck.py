"""Generate the SIH idea-submission deck.

    python scripts/build_deck.py

Six slides in the standard SIH section structure, for problem statement
**SIH26191** (Ministry of Home Affairs / NDRF, DM Division).

Layout is measurement-driven: line counts come from the real Windows font
metrics, so cards size themselves to their content and nothing overflows. The
alternative — guessing a line height and hoping — is what produced three silent
overflows the first time this deck was built.
"""

from __future__ import annotations

import os
import sys

from PIL import ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor as C
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches as I
from pptx.util import Pt

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# The deck is frequently open in PowerPoint, which holds an exclusive lock and
# turns a rebuild into a PermissionError several hundred lines in. An override
# lets the build still produce a file rather than losing the run.
OUT = os.environ.get("DRISHTI_DECK_OUT") or os.path.join(
    ROOT, "docs", "SIH26191_DRISHTI_Idea_Submission.pptx")

# ---- font metrics -----------------------------------------------------------
_FONTFILE = {("Calibri", False): r"C:\Windows\Fonts\calibri.ttf",
             ("Calibri", True):  r"C:\Windows\Fonts\calibrib.ttf",
             ("Cambria", False): r"C:\Windows\Fonts\cambria.ttc",
             ("Cambria", True):  r"C:\Windows\Fonts\cambriab.ttf"}
_fcache = {}


def _font(name, bold, pt):
    k = (name, bold, round(pt))
    if k not in _fcache:
        _fcache[k] = ImageFont.truetype(_FONTFILE[(name, bold)], int(round(pt * 4)))
    return _fcache[k]


def nlines(text, width_in, pt, name="Calibri", bold=False):
    f = _font(name, bold, pt)
    limit = width_in * 72.0
    total = 0
    for hard in text.split("\n"):
        line, n = "", 1
        for w in hard.split(" "):
            trial = w if not line else line + " " + w
            if f.getlength(trial) / 4.0 <= limit or not line:
                line = trial
            else:
                n += 1
                line = w
        total += n
    return total


def height_in(text, width_in, pt, name="Calibri", bold=False, spacing=1.16):
    return nlines(text, width_in, pt, name, bold) * pt * 1.20 * spacing / 72.0


# ---- palette: IMD warning code over a night operations ground ---------------
NAVY, SLATE, STEEL = "0B1B2B", "17324A", "24485F"
ICE, WHITE = "EDF2F6", "FFFFFF"
AMBER, RED, GREEN, YELLOW = "E07B39", "C4362C", "2E8B57", "E3B23C"
MUTED_D, MUTED_L = "9FB4C7", "5A7186"
HEAD, BODY = "Cambria", "Calibri"

W, H, M = 13.333, 7.5, 0.6
RIGHT = W - M

prs = Presentation()
prs.slide_width, prs.slide_height = I(W), I(H)
BLANK = prs.slide_layouts[6]


def slide(bg):
    s = prs.slides.add_slide(BLANK)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = C.from_string(bg)
    return s


def rect(s, l, t, w, h, fill, shape=MSO_SHAPE.RECTANGLE, line=None, lw=1):
    sh = s.shapes.add_shape(shape, I(l), I(t), I(w), I(h))
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = C.from_string(fill)
    if line:
        sh.line.color.rgb = C.from_string(line)
        sh.line.width = Pt(lw)
    else:
        sh.line.fill.background()
    sh.shadow.inherit = False
    return sh


def txt(s, l, t, w, h, runs, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    tb = s.shapes.add_textbox(I(l), I(t), I(w), I(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, (text, size, bold, col, font, sp) in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(sp)
        p.line_spacing = 1.16
        r = p.add_run()
        r.text = text
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = C.from_string(col)
        r.font.name = font
    return tb


def alertmark(s, l, t, sz=0.135, gap=0.035):
    """IMD's green/yellow/orange/red warning code, 2x2. The deck's one motif."""
    for i, col in enumerate((GREEN, YELLOW, AMBER, RED)):
        rect(s, l + (i % 2) * (sz + gap), t + (i // 2) * (sz + gap), sz, sz, col)


def eyebrow(s, text, l=M, t=0.52):
    alertmark(s, l, t + 0.02)
    txt(s, l + 0.42, t, 8.6, 0.3, [(text.upper(), 11.5, True, AMBER, BODY, 0)])


def title(s, text, sub=None):
    txt(s, M, 0.95, 10.4, 0.7, [(text, 30, True, NAVY, HEAD, 0)])
    if sub:
        txt(s, M, 1.62, 11.6, 0.42, [(sub, 13.5, False, MUTED_L, BODY, 0)])


def card(s, l, t, w, h, fill=WHITE, line="D5DFE7"):
    return rect(s, l, t, w, h, fill, line=line, lw=0.75)


# =============================================================================
# 1 — Title
# =============================================================================
s = slide(NAVY)
alertmark(s, M, 0.6, 0.16, 0.04)
txt(s, M + 0.48, 0.58, 7.4, 0.3,
    [("SMART INDIA HACKATHON 2026", 12.5, True, AMBER, BODY, 0)])

txt(s, M - 0.06, 1.5, 8.0, 1.4, [("DRISHTI", 74, True, WHITE, HEAD, 0)])
txt(s, M, 2.72, 7.7, 0.5,
    [("District Risk Intelligence & Satellite Hazard Tracking Interface",
      13.5, False, MUTED_D, BODY, 0)])
rect(s, M, 3.34, 1.5, 0.028, AMBER)
txt(s, M, 3.62, 7.6, 1.15,
    [("We keep rebuilding villages", 22, True, WHITE, HEAD, 3),
     ("in the same death trap.", 22, True, AMBER, HEAD, 0)])
txt(s, M, 5.22, 7.7, 1.1,
    [("A GIS decision-support platform that identifies multi-hazard Red Zones "
      "unsuitable for permanent habitation, assesses the carrying capacity of "
      "safer sites, and ranks vulnerable habitations for relocation.",
      12.5, False, MUTED_D, BODY, 0)])

cx, cw = 8.55, RIGHT - 8.55
card(s, cx, 1.5, cw, 4.9, fill=SLATE, line=STEEL)
rows = [("PROBLEM STATEMENT ID", "SIH26191"),
        ("PROBLEM STATEMENT TITLE", "Hazard-Based Red Zones, Carrying "
                                    "Capacity & Relocation Needs"),
        ("ORGANISATION", "Ministry of Home Affairs"),
        ("DEPARTMENT", "NDRF, DM Division"),
        ("THEME", "Disaster Management"),
        ("PS CATEGORY", "Software"),
        ("TEAM ID / NAME", "<< enter Team ID and Name >>")]
y = 1.82
for k, v in rows:
    vh = height_in(v, cw - 0.84, 12.5, bold=True)
    txt(s, cx + 0.42, y, cw - 0.84, 0.22, [(k, 9, True, AMBER, BODY, 0)])
    txt(s, cx + 0.42, y + 0.22, cw - 0.84, vh, [(v, 12.5, True, WHITE, BODY, 0)])
    y += 0.24 + vh + 0.18
s.notes_slide.notes_text_frame.text = (
    "Fill in the team ID and name before submitting. Everything else is fixed "
    "by the problem statement.")

# =============================================================================
# 2 — Proposed Solution
# =============================================================================
s = slide(ICE)
eyebrow(s, "Idea and approach details")
title(s, "Proposed Solution",
      "Not where the water is today — whether anyone should be living there at all.")

lx, lw_ = M, 6.55
body1 = ("DRISHTI runs the hazard model at the 5-, 25- and 100-year event and "
         "records, for every 160 m cell, the shortest return period at which "
         "any of five hazards renders it uninhabitable. That single number — "
         "years between events that make a place unliveable — is the Red Zone "
         "index. It is physically meaningful, and it maps directly onto a "
         "relocation horizon: immediate, short-term or medium-term.")
h1 = height_in(body1, lw_ - 0.6, 12.5)
card(s, lx, 2.25, lw_, h1 + 0.72)
txt(s, lx + 0.3, 2.48, lw_ - 0.6, 0.28,
    [("HOW IT WORKS", 10, True, AMBER, BODY, 0)])
txt(s, lx + 0.3, 2.78, lw_ - 0.6, h1, [(body1, 12.5, False, "20364A", BODY, 0)])

body2 = ("Current relocation is reactive — it starts after a disaster, when the "
         "evidence is a body count. Severity of one event tells you almost "
         "nothing: a place flooded chest-deep once a century is a place you "
         "protect; the same depth every fifth monsoon is a place you move. "
         "Recurrence is what separates the two, and it is what no existing "
         "portal reports.")
y2 = 2.25 + h1 + 0.72 + 0.22
h2 = height_in(body2, lw_ - 0.6, 12.5)
card(s, lx, y2, lw_, h2 + 0.72)
txt(s, lx + 0.3, y2 + 0.23, lw_ - 0.6, 0.28,
    [("HOW IT ADDRESSES THE PROBLEM", 10, True, AMBER, BODY, 0)])
txt(s, lx + 0.3, y2 + 0.53, lw_ - 0.6, h2, [(body2, 12.5, False, "20364A", BODY, 0)])

rx, rw = 7.45, RIGHT - 7.45
txt(s, rx, 2.25, rw, 0.3,
    [("INNOVATION AND UNIQUENESS", 10, True, AMBER, BODY, 0)])
inno = [("Recurrence, not severity",
         "The return period at which land becomes uninhabitable — the number a "
         "relocation decision actually needs."),
        ("The Indian standard, implemented",
         "Landslide hazard follows BIS IS 14496 (Part 2), the LHEF scheme GSI "
         "uses — not an invented overlay."),
        ("Five hazards, one index",
         "Flood, landslide, waterlogging, coastal erosion and cloudburst "
         "combined by dominance, with the governing hazard named."),
        ("Capacity, not just safety",
         "Sites are scored on safety, buildability and room — and the allocator "
         "respects capacity rather than assuming it."),
        ("Distance is a cost, not a footnote",
         "Resettlement fails when people are moved from their fields. Mean "
         "displacement is minimised and reported.")]
y = 2.62
for h_, d in inno:
    dh = height_in(d, rw - 0.26, 10.5)
    rect(s, rx, y + 0.08, 0.09, 0.09, AMBER)
    txt(s, rx + 0.26, y, rw - 0.26, 0.26, [(h_, 12, True, NAVY, BODY, 0)])
    txt(s, rx + 0.26, y + 0.24, rw - 0.26, dh, [(d, 10.5, False, MUTED_L, BODY, 0)])
    y += 0.24 + dh + 0.2

# =============================================================================
# 3 — Technical Approach
# =============================================================================
s = slide(ICE)
eyebrow(s, "Technical approach")
title(s, "Technologies and Methodology",
      "Every step is a published method. Physics first, because a relocation "
      "order has to survive an inquiry.")

txt(s, M, 2.22, 11.6, 0.28, [("PROCESS FLOW", 10, True, AMBER, BODY, 0)])
stages = [
    ("TERRAIN", "DEM to HAND,\nslope, wetness,\nD8 drainage"),
    ("HAZARD", "Flood · landslide\nwaterlogging ·\nerosion, x3 events"),
    ("RECURRENCE", "Return period at\nwhich land turns\nuninhabitable"),
    ("EXPOSE", "Dasymetric\npopulation, roads,\nfacilities"),
    ("RELOCATE", "Site capacity,\nranked habitations,\nallocation"),
]
cw2, gap2 = 2.30, 0.155
x = M
for i, (h_, d) in enumerate(stages):
    ch = rect(s, x, 2.58, cw2, 0.72, NAVY if i < 3 else AMBER, shape=MSO_SHAPE.CHEVRON)
    tf = ch.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = I(0.1)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = h_
    r.font.size = Pt(13)
    r.font.bold = True
    r.font.color.rgb = C.from_string(WHITE)
    r.font.name = BODY
    txt(s, x + 0.16, 3.46, cw2 - 0.2, 0.9, [(d, 10.5, False, MUTED_L, BODY, 0)])
    x += cw2 + gap2

txt(s, M, 4.58, 11.6, 0.28,
    [("TECHNOLOGIES USED", 10, True, AMBER, BODY, 0)])
groups = [
    ("Languages, frameworks, hardware",
     "Python 3.10 \u00b7 NumPy 2.2 \u00b7 FastAPI 0.141 \u00b7 Uvicorn \u00b7 "
     "scikit-learn 1.7 \u00b7 pytest (287 tests). Front end is vanilla ES "
     "modules with Leaflet 1.9 vendored locally \u2014 no bundler, no build "
     "step. Ships as a Windows installer: PyInstaller 6.22 freezes the backend, "
     "Electron 33 provides the window. "
     "Hardware: commodity x86, 4 GB RAM, no GPU, no GDAL, runs fully offline."),
    ("Algorithms and national standards",
     "Landslide: BIS IS 14496 (Part 2) LHEF rating scheme \u2014 the Geological "
     "Survey of India method. Trigger: Caine (1980) intensity-duration "
     "threshold. Terrain: Horn slope, Wang & Liu priority-flood, Garbrecht & "
     "Martz flat resolution, D8 routing, HAND (Nobre 2011), Beven-Kirkby wetness "
     "index. Flood: SCS Curve Number, Manning compound channel, FwDET depth. "
     "Erosion: Bruun (1962). Radar: Lee filter, Otsu threshold."),
    ("Data sources and machine learning",
     "Live with no credentials: Open-Meteo (rainfall, soil moisture, CAPE) and "
     "the Copernicus Sentinel-1 catalogue. Connectors written for ISRO "
     "Bhoonidhi CartoDEM, Bhuvan, CWC gauges and GSI lithology. "
     "ML: random forests for radar water detection (F1 0.96 against a 0.66 "
     "physics baseline) and as a national red-zone surrogate (R\u00b2 0.77), "
     "both validated on held-out districts."),
]
cw3 = (RIGHT - M - 2 * 0.28) / 3
x = M
for h_, d in groups:
    dh = height_in(d, cw3 - 0.52, 9.8)
    card(s, x, 4.86, cw3, dh + 0.74)
    txt(s, x + 0.26, 5.06, cw3 - 0.52, 0.3, [(h_, 11.5, True, NAVY, BODY, 0)])
    txt(s, x + 0.26, 5.38, cw3 - 0.52, dh, [(d, 9.8, False, MUTED_L, BODY, 0)])
    x += cw3 + 0.28

# =============================================================================
# 4 — Feasibility and Viability
# =============================================================================
s = slide(ICE)
eyebrow(s, "Feasibility and viability")
title(s, "What is built, and what could break it",
      "Every risk is stated with its mitigation. A pitch with no weaknesses has "
      "not been tested.")

cols = [
    ("ALREADY BUILT AND TESTED", GREEN, [
        "All 735 districts \u2014 22 modelled on real 30 m Copernicus terrain, "
        "713 screened on live weather, and never shown alike.",
        "Five hazards, Census vulnerability, 41 cited events, and GHSL "
        "encroachment inside Red Zones.",
        "287 automated tests, including the safety invariant that no relocation "
        "site may lie inside a Red Zone.",
        "Median 1.5 s per district on a laptop. No GPU, no GDAL, no build step.",
        "Runs fully offline: relief basemap rendered from our own elevation model.",
    ]),
    ("RISKS", RED, [
        "Lithology and structure need a Geological Survey of India map; two of "
        "six BIS LHEF factors are held neutral without it.",
        "HAND assumes a level water surface and cannot see embankments finer "
        "than the DEM — the largest source of extent error.",
        "Population is a Census 2011 projection, not a live count.",
        "Erosion retreat over decades is sub-pixel at 160 m resolution.",
        "Demo-mode input rasters are modelled, not observed.",
    ]),
    ("MITIGATION", AMBER, [
        "Geology raster loads through a documented interface and promotes the "
        "assessment to a complete LHEF rating.",
        "Embankment barriers enter as a mask once digitised; the limitation is "
        "published rather than hidden.",
        "WorldPop and real boundaries drop in without touching any model.",
        "Retreat RATE is reported as the meaningful output; area is flagged as "
        "resolution-limited.",
        "Permanent provenance badge on every screen and in every API response.",
    ]),
]
cw4 = (RIGHT - M - 2 * 0.3) / 3
tw4 = cw4 - 0.78
GAP4 = 0.30
heights = [sum(height_in(it, tw4, 11) + GAP4 for it in its) for _, _, its in cols]
card_h = 0.72 + max(heights) + 0.06
x = M
for h_, col, items in cols:
    card(s, x, 2.28, cw4, card_h)
    rect(s, x + 0.26, 2.56, 0.16, 0.16, col)
    txt(s, x + 0.55, 2.52, cw4 - 0.8, 0.3, [(h_, 10.5, True, col, BODY, 0)])
    y = 3.0
    for it in items:
        ih = height_in(it, tw4, 11)
        rect(s, x + 0.26, y + 0.07, 0.08, 0.08, col)
        txt(s, x + 0.5, y - 0.02, tw4, ih, [(it, 11, False, "20364A", BODY, 0)])
        y += ih + GAP4
    x += cw4 + 0.3

# =============================================================================
# 5 — Impact and Benefits
# =============================================================================
s = slide(ICE)
eyebrow(s, "Impact and benefits")
title(s, "Who it serves, and what changes",
      "Primary user: State Disaster Management Authorities and District "
      "Magistrates planning proactive relocation.")

stats = [("946 km²", "of Darbhanga modelled as Red Zone\n— 42% of the district"),
         ("every 5 yr", "the interval at which the worst\nhabitations become unliveable"),
         ("4", "hazards assessed together:\nflood, landslide, waterlog, erosion"),
         ("19%", "of at-risk people placeable at\nassembled sites — the real gap")]
cw5 = (RIGHT - M - 3 * 0.26) / 4
x = M
for big, lab in stats:
    card(s, x, 2.28, cw5, 1.52)
    txt(s, x + 0.24, 2.46, cw5 - 0.48, 0.55, [(big, 27, True, NAVY, HEAD, 0)])
    txt(s, x + 0.24, 3.02, cw5 - 0.48, 0.6, [(lab, 10.5, False, MUTED_L, BODY, 0)])
    x += cw5 + 0.26

bens = [("SOCIAL", GREEN,
         "Relocation becomes proactive and evidence-based instead of a response "
         "to the last disaster. Households are ranked by how many people are "
         "exposed and how often, so scarce resettlement funding reaches the "
         "villages that flood every fifth year rather than the loudest ones."),
        ("ECONOMIC", AMBER,
         "Ends the cycle of rebuilding the same assets in the same hazard zone. "
         "Identifies whether a district's binding constraint is land or site "
         "assembly — a distinction that changes whether the answer is "
         "acquisition, densification, or an inter-district decision."),
        ("GOVERNANCE", STEEL,
         "Every ranking publishes its weights and every hazard cites its "
         "standard, so a relocation order can be defended in an inquiry. The "
         "system states what it does not know — two BIS factors unmapped, "
         "population projected — rather than presenting an estimate as fact.")]
y = 4.06
for h_, col, d in bens:
    dh = height_in(d, RIGHT - M - 3.4, 11)
    card(s, M, y, RIGHT - M, max(0.84, dh + 0.34))
    rect(s, M + 0.26, y + 0.29, 0.14, 0.14, col)
    txt(s, M + 0.52, y + 0.23, 2.6, 0.3, [(h_, 10.5, True, col, BODY, 0)])
    txt(s, M + 3.1, y + 0.17, RIGHT - M - 3.4, dh, [(d, 11, False, "20364A", BODY, 0)])
    y += max(0.84, dh + 0.34) + 0.1

# Roadmap footer. Everything proposed rather than built lives here, labelled by
# phase, so a reviewer can see at a glance which claims are demonstrable today.
# Kept to a single line: slide 4 has no vertical room and this reads better as a
# footer than as another row of cards.
rd_y = 6.94
txt(s, M, rd_y, 1.05, 0.24, [("ROADMAP", 9.5, True, AMBER, BODY, 0)])
txt(s, M + 1.02, rd_y, RIGHT - M - 1.02, 0.24,
    [("NOW  built and tested   ·   P2  GSI lithology + Census housing   ·   "
      "P3  CWC gauges, promote screened districts   ·   P4  citizen "
      "(IVR + LGD) as ground truth   ·   P5  short code + Sachet CAP",
      9.5, False, MUTED_L, BODY, 0)])

# =============================================================================
# 6 — Research and References
# =============================================================================
s = slide(NAVY)
alertmark(s, M, 0.56, 0.15, 0.04)
txt(s, M + 0.46, 0.54, 8.6, 0.3,
    [("RESEARCH AND REFERENCES", 11.5, True, AMBER, BODY, 0)])
txt(s, M, 0.98, 10.4, 0.6,
    [("Every step traces to a published method", 29, True, WHITE, HEAD, 0)])
txt(s, M, 1.62, 11.6, 0.4,
    [("Nothing here is invented. Standards first, and Indian standards where "
      "they exist.", 13, False, MUTED_D, BODY, 0)])

lx2, cwid = M, 6.15
card(s, lx2, 2.3, cwid, 4.5, fill=SLATE, line=STEEL)
txt(s, lx2 + 0.3, 2.55, cwid - 0.6, 0.3,
    [("STANDARDS AND LITERATURE", 10, True, AMBER, BODY, 0)])
refs = [
    "BIS IS 14496 (Part 2): 1998 — Landslide Hazard Zonation, LHEF scheme.",
    "NDMA. National Disaster Management Plan; Guidelines on Floods.",
    "Caine, N. (1980). Rainfall intensity-duration control of shallow landslides.",
    "Bruun, P. (1962). Sea level rise as a cause of shore erosion.",
    "Renno (2008); Nobre et al. (2011). Height Above Nearest Drainage.",
    "Beven & Kirkby (1979). Variable contributing area model of basin hydrology.",
    "Wang & Liu (2006). Efficient depression filling for flow routing.",
    "Garbrecht & Martz (1997). Drainage direction over flat surfaces.",
    "Cohen et al. (2018). FwDET floodwater depth estimation.",
    "Otsu (1979); Lee (1980). Thresholding and speckle suppression.",
    "Sphere Handbook. Shelter and settlement standards.",
]
y = 2.9
for r_ in refs:
    rh = height_in(r_, cwid - 0.85, 10)
    rect(s, lx2 + 0.3, y + 0.06, 0.07, 0.07, AMBER)
    txt(s, lx2 + 0.52, y - 0.02, cwid - 0.85, rh, [(r_, 10, False, "C7D6E2", BODY, 0)])
    y += rh + 0.15

rx2 = lx2 + cwid + 0.3
rwid = RIGHT - rx2
card(s, rx2, 2.3, rwid, 4.5, fill=SLATE, line=STEEL)
txt(s, rx2 + 0.3, 2.55, rwid - 0.6, 0.3,
    [("DATA SOURCES", 10, True, AMBER, BODY, 0)])
srcs = [
    ("Geological Survey of India", "Lithology and structure for complete LHEF rating"),
    ("ISRO Bhoonidhi / NRSC", "bhoonidhi.nrsc.gov.in — CartoDEM, Resourcesat, EOS-04"),
    ("Copernicus Data Space", "dataspace.copernicus.eu — Sentinel-1 (connector live)"),
    ("ISRO Bhuvan", "bhuvan.nrsc.gov.in — NRSC hazard and flood layers"),
    ("National Centre for Coastal Research", "Shoreline change rates for the Indian coast"),
    ("Census of India / WorldPop", "Habitation population and settlement distribution"),
    ("India Meteorological Department", "Rainfall normals and extreme-value statistics"),
    ("Survey of India / LGD", "District and habitation boundaries"),
]
y = 2.9
for nme, url in srcs:
    uh = height_in(url, rwid - 0.85, 9.5)
    rect(s, rx2 + 0.3, y + 0.06, 0.07, 0.07, AMBER)
    txt(s, rx2 + 0.52, y - 0.02, rwid - 0.85, 0.24, [(nme, 10.5, True, WHITE, BODY, 0)])
    txt(s, rx2 + 0.52, y + 0.2, rwid - 0.85, uh, [(url, 9.5, False, MUTED_D, BODY, 0)])
    y += 0.22 + uh + 0.13

prs.save(OUT)
print("saved:", OUT)
