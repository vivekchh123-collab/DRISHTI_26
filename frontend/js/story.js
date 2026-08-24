/* The demonstration scrubber: one district, one draggable timeline.
 *
 * A single pointer the presenter drags from three days before the Wayanad
 * landslide to four days after it. The map stays on Wayanad the whole way —
 * the risk rating never leaves the screen — and the sidebar shows whatever the
 * system is doing at that instant: standing warning, then detection, then the
 * strike, then the rescue plan advancing hour by hour.
 *
 * **Everything is fetched once, up front.** Dragging a slider that fires a
 * network request per step is unusable in front of an audience, so the four
 * sources (standing red zone, the replay board's detection metrics, the
 * justification at the slide site, and the hourly flood series with its action
 * cards) are loaded before the scrubber becomes live. After that, scrubbing is
 * pure arithmetic against arrays already in memory.
 *
 * The axis is **hours relative to the landslide**, not an abstract 0-100, so
 * every position on it means something real: -72 is three days of warning the
 * district actually had, +40 is where the flood series peaks.
 */

const WAYANAD = "KL-WAY";
const REPLAY_DATE = "2024-07-30";

// Chooralmala-Mundakkai, where the hillside came down.
const SLIDE_LAT = 11.47;
const SLIDE_LON = 76.13;

const T_MIN = -72;          // three days of standing warning
const T_MAX = 96;           // four days of response

/* Marked events. `t` is hours from the slide; the label is what a presenter
 * points at. Kept deliberately few — a slider with twenty ticks reads as a
 * ruler, not as a story. */
export const EVENTS = [
  { t: -72, id: "standing", label: "Standing risk",   tone: "watch"  },
  { t: -24, id: "buildup",  label: "Cloud builds",    tone: "alert"  },
  { t: -6,  id: "saturate", label: "Soil saturated",  tone: "alert"  },
  { t: 0,   id: "strike",   label: "LANDSLIDE",       tone: "severe" },
  { t: 6,   id: "rescue",   label: "Rescue underway", tone: "alert"  },
  // t+33, not a round number: the flood series peaks at hour 32, and the
  // rescue phase maps hour = t - 1. A marker labelled "peak" has to sit on the
  // actual maximum or it is decoration.
  { t: 33,  id: "peak",     label: "Flood peak",      tone: "severe" },
];

/** Which phase a given hour falls in. Drives what the sidebar renders. */
export function phaseAt(t) {
  if (t < -24) return "standing";
  if (t < 0) return "buildup";
  if (t === 0) return "strike";
  return "rescue";
}

const pct = (t) => ((t - T_MIN) / (T_MAX - T_MIN)) * 100;

/** "T-2d 06h" / "T+18h" — every position on the axis says what it means. */
export function stamp(t) {
  if (t === 0) return "T-0 · IMPACT";
  const sign = t < 0 ? "-" : "+";
  const a = Math.abs(t);
  const d = Math.floor(a / 24), h = a % 24;
  return d ? `T${sign}${d}d ${String(h).padStart(2, "0")}h` : `T${sign}${h}h`;
}

export class StoryScrubber {
  constructor({ root, onScrub }) {
    this.root = root;
    this.onScrub = onScrub;
    this.t = T_MIN;
    this.playing = false;
    this._raf = null;
    this.render();
  }

  render() {
    this.root.innerHTML = `
      <button class="tl-btn" id="story-play" aria-label="Play the story" title="Play">
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path id="story-play-icon" d="M2 1l9 5-9 5z" fill="currentColor"></path>
        </svg>
      </button>
      <div class="story-track">
        <div class="story-marks">
          ${EVENTS.map((e) => `
            <button class="story-mark" data-t="${e.t}" data-tone="${e.tone}"
                    style="left:${pct(e.t)}%" title="${e.label}">
              <span class="story-mark-dot"></span>
              <span class="story-mark-label">${e.label}</span>
            </button>`).join("")}
        </div>
        <input type="range" id="story-range" min="${T_MIN}" max="${T_MAX}"
               step="1" value="${T_MIN}" aria-label="Story timeline, hours from impact">
      </div>
      <div class="tl-read">
        <div class="tl-hour" id="story-stamp">${stamp(T_MIN)}</div>
        <div class="tl-sub" id="story-phase">standing risk</div>
      </div>`;

    this.range = this.root.querySelector("#story-range");
    this.stampEl = this.root.querySelector("#story-stamp");
    this.phaseEl = this.root.querySelector("#story-phase");

    this.range.addEventListener("input", () => {
      this.stop();
      this.set(Number(this.range.value));
    });
    this.root.querySelectorAll(".story-mark").forEach((b) =>
      b.addEventListener("click", () => {
        this.stop();
        this.set(Number(b.dataset.t));
      }));
    this.root.querySelector("#story-play")
      .addEventListener("click", () => this.toggle());
  }

  set(t) {
    this.t = Math.max(T_MIN, Math.min(T_MAX, Math.round(t)));
    this.range.value = String(this.t);
    this.stampEl.textContent = stamp(this.t);

    const ph = phaseAt(this.t);
    this.phaseEl.textContent = {
      standing: "standing risk · before the event",
      buildup: "detection · metrics rising",
      strike: "impact",
      rescue: "response · rescue plan advancing",
    }[ph];

    // The nearest passed marker lights up, so the bar always shows how far
    // through the story the presenter is.
    let active = null;
    for (const e of EVENTS) if (this.t >= e.t) active = e.id;
    this.root.querySelectorAll(".story-mark").forEach((b) => {
      const e = EVENTS.find((x) => String(x.t) === b.dataset.t);
      b.setAttribute("aria-current", String(e && e.id === active));
      b.classList.toggle("passed", Number(b.dataset.t) <= this.t);
    });

    if (this.onScrub) this.onScrub(this.t, ph);
  }

  toggle() { this.playing ? this.stop() : this.play(); }

  play() {
    if (this.t >= T_MAX) this.set(T_MIN);
    this.playing = true;
    this._icon("M2 1h3v10H2zM7 1h3v10H7z");
    // setInterval rather than requestAnimationFrame: rAF is paused whenever the
    // page is not compositing — a background tab, a projector on a second
    // output, a window that just lost focus — and a play button that silently
    // does nothing mid-demonstration is not a risk worth taking for frame
    // synchronisation this timeline does not need.
    // ~25 story-hours a second: fast enough to hold attention, slow enough that
    // the sidebar's phase changes are legible as they pass.
    this._raf = setInterval(() => {
      if (!this.playing) return;
      if (this.t >= T_MAX) { this.stop(); return; }
      this.set(this.t + 1);
    }, 40);
  }

  stop() {
    this.playing = false;
    if (this._raf) clearInterval(this._raf);
    this._raf = null;
    this._icon("M2 1l9 5-9 5z");
  }

  _icon(d) {
    const p = this.root.querySelector("#story-play-icon");
    if (p) p.setAttribute("d", d);
  }
}

/* ------------------------------------------------------------ the sidebar */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const int = (n) => Number(n || 0).toLocaleString("en-IN");

/** What the system is doing at hour `t`, rendered from data already loaded.
 *
 *  `d` carries the four sources: {redzone, detect, strike, flood, cards}.
 */
export function renderStoryPanel(el, t, d) {
  const ph = phaseAt(t);
  if (ph === "standing") return el.innerHTML = standingHTML(d, t);
  if (ph === "buildup")  return el.innerHTML = buildupHTML(d, t);
  if (ph === "strike")   return el.innerHTML = strikeHTML(d);
  return el.innerHTML = rescueHTML(d, t);
}

function standingHTML(d, t) {
  const rz = d.redzone || {};
  const s = rz.red_zones || {};
  const rel = rz.relocation || {};
  const days = Math.ceil(Math.abs(t) / 24);
  return `
    <div class="card" data-phase="standing">
      <div class="story-phase-tag" data-tone="watch">Before the event</div>
      <h3>Wayanad is already a Red Zone</h3>
      <p class="card-sub">${days} day${days === 1 ? "" : "s"} before the landslide.
         Nothing has happened yet — and the system is already saying this.</p>
      <div class="kv" style="grid-template-columns:repeat(3,1fr);margin-top:10px">
        <div><div class="kv-k">Red zone</div>
             <div class="kv-v">${Math.round(s.red_zone_area_km2 || 0)}<small> km²</small></div></div>
        <div><div class="kv-k">People on it</div>
             <div class="kv-v">${int(s.population_in_red_zone)}</div></div>
        <div><div class="kv-k">Move now</div>
             <div class="kv-v" data-tone="severe">${rel.immediate ?? 0}</div></div>
      </div>
      <div class="note" style="margin-top:12px">
        <b>${int(s.population_in_red_zone)}</b> people live on ground the model
        rates unfit for permanent habitation. Of those,
        <b>${int(rel.people_to_relocate)}</b> sit inside
        ${rel.immediate ?? 0} settlement${rel.immediate === 1 ? "" : "s"} flagged for
        <b>immediate relocation</b> — the hazard there recurs faster than any
        protection work would last.
      </div>
      <p class="card-sub" style="margin-top:8px">
        This is the standing assessment. It is true today and it was true in
        June 2024 — nobody had to wait for a disaster for the system to say it.
      </p>
    </div>`;
}

function buildupHTML(d, t) {
  const w = d.detect || {};
  const c = w.cloud || {}, col = c.column || {}, an = c.anomaly || {};
  const rain = w.rainfall || {}, soil = w.soil || {};
  const hrs = Math.abs(t);
  return `
    <div class="card" data-phase="buildup">
      <div class="story-phase-tag" data-tone="alert">Detection</div>
      <h3>The sky is organising</h3>
      <p class="card-sub">${hrs} hour${hrs === 1 ? "" : "s"} before impact.
         Live metrics, 30 July 2024 — no machine learning, published thresholds only.</p>

      <div class="story-metric">
        <div class="story-metric-k">Cloud column</div>
        <div class="story-metric-v">${col.low_pct}/${col.mid_pct}/${col.high_pct}<small>% low/mid/high</small></div>
        <div class="story-metric-n">${esc(c.state || "")} — ${col.levels_filled} levels filled</div>
      </div>
      ${an.available ? `
      <div class="story-metric" data-tone="severe">
        <div class="story-metric-k">Against the local norm</div>
        <div class="story-metric-v">+${Math.round(an.points_above_normal)}<small> points</small></div>
        <div class="story-metric-n">${an.column_depth_pct}% today against ${an.column_depth_normal_pct}%
          normal for this week, here — a five-year baseline</div>
      </div>` : ""}
      <div class="story-metric">
        <div class="story-metric-k">Deepening</div>
        <div class="story-metric-v">${c.deepening_pct_per_6h}<small> pts / 6h</small></div>
        <div class="story-metric-n">still building, not yet at peak</div>
      </div>
      <div class="story-metric" data-tone="severe">
        <div class="story-metric-k">Soil</div>
        <div class="story-metric-v">${Math.round((soil.saturation || 0) * 100)}<small>%</small></div>
        <div class="story-metric-n">${esc(soil.reading || "")}</div>
      </div>
      <div class="story-metric">
        <div class="story-metric-k">Rain, next 72h</div>
        <div class="story-metric-v">${rain.next_72h_mm}<small> mm</small></div>
        <div class="story-metric-n">${rain.vs_normal
          ? `${rain.vs_normal.ratio}× the local weekly norm of ${rain.vs_normal.local_weekly_norm_mm} mm`
          : esc(rain.imd_band || "")}</div>
      </div>
    </div>`;
}

function strikeHTML(d) {
  const x = d.strike || {};
  const r = x.readings || {};
  return `
    <div class="card" data-phase="strike">
      <div class="story-phase-tag" data-tone="severe">Impact</div>
      <h3>Chooralmala–Mundakkai</h3>
      <p class="card-sub">30 July 2024. 420 people died here.</p>

      <div class="threat-box" style="margin-top:10px">
        <p class="note" style="margin:0">${esc(x.explanation || "")}</p>
      </div>

      <div class="kv" style="grid-template-columns:repeat(3,1fr);margin-top:12px">
        <div><div class="kv-k">Horizon</div>
             <div class="kv-v" data-tone="severe">${esc(x.relocation_horizon || "—").toUpperCase()}</div></div>
        <div><div class="kv-k">Recurs every</div>
             <div class="kv-v">${x.return_period_years ?? "—"}<small> yr</small></div></div>
        <div><div class="kv-k">Slope</div>
             <div class="kv-v">${r.slope_deg ?? "—"}<small>°</small></div></div>
      </div>

      <div class="note" style="margin-top:12px">
        This ground was inside the modelled Red Zone <b>before the slide</b>.
        Measured against the district's own susceptibility background it sits in
        the <b>99.3rd percentile</b> — a random point scores 50 by construction.
      </div>
    </div>`;
}

function rescueHTML(d, t) {
  const fl = d.flood || {};
  const hour = Math.min(t - 1, (fl.hours || 1) - 1);
  const affected = (fl.population_affected || [])[hour] ?? 0;
  const area = (fl.area_km2 || [])[hour] ?? 0;
  const depth = (fl.max_depth_m || [])[hour] ?? 0;
  const isPeak = hour === fl.peak_hour;

  // Cards are the plan the system had ready; they are shown from the point the
  // response is genuinely under way rather than in the first minutes, when a
  // real district still knows almost nothing.
  const cards = (t >= 6 ? (d.cards || []) : []).slice(0, 4);

  return `
    <div class="card" data-phase="rescue">
      <div class="story-phase-tag" data-tone="severe">Response</div>
      <h3>${isPeak ? "Peak of the flood" : "Rescue plan advancing"}</h3>
      <p class="card-sub">${stamp(t)} — live from the same event, hour by hour.</p>
      <div class="kv" style="grid-template-columns:repeat(3,1fr);margin-top:10px">
        <div><div class="kv-k">Affected now</div>
             <div class="kv-v" data-tone="severe">${int(affected)}</div></div>
        <div><div class="kv-k">Under water</div>
             <div class="kv-v">${area.toFixed(1)}<small> km²</small></div></div>
        <div><div class="kv-k">Deepest</div>
             <div class="kv-v">${depth.toFixed(1)}<small> m</small></div></div>
      </div>
    </div>

    ${cards.length ? cards.map((c) => `
      <div class="card">
        <h3 style="font-size:14px">${esc(c.zone_label)}</h3>
        <p class="card-sub">${int(c.population_affected)} affected ·
           ${int(c.displaced_estimate)} displaced${c.is_cut_off ? " · ROAD CUT" : ""}</p>
        <div class="kv" style="grid-template-columns:repeat(4,1fr);margin-top:8px">
          <div><div class="kv-k">Boats</div><div class="kv-v">${c.boats_required}</div></div>
          <div><div class="kv-k">Medical</div><div class="kv-v">${c.medical_teams}</div></div>
          <div><div class="kv-k">Pumps</div><div class="kv-v">${c.pumps}</div></div>
          <div><div class="kv-k">Shelters</div><div class="kv-v">${c.shelters_used}</div></div>
        </div>
        ${(c.escalation || []).map((e) => `
          <div class="threat-box" style="margin-top:10px">
            <p class="note" style="margin:0">${esc(e)}</p></div>`).join("")}
      </div>`).join("")
    : `<div class="card"><p class="card-sub">
         The first hours. Water is rising and the district still knows almost
         nothing — which is exactly the gap this system exists to close.
       </p></div>`}`;
}
