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
/* Labels are short because the axis is dense: T-6h and T+6h sit twelve hours
 * apart on a 168-hour track, which is about eighty pixels at demonstration
 * width. Two full phrases cannot both fit there however they are stacked, and
 * a truncated label is worse than a terse one. The long form is on the tooltip
 * and, more usefully, in the sidebar heading that each marker opens. */
export const EVENTS = [
  { t: -72, id: "standing", label: "Standing",  full: "Standing risk, before any event", tone: "watch"  },
  { t: -24, id: "buildup",  label: "Cloud",     full: "Cloud column building", tone: "alert"  },
  { t: -6,  id: "saturate", label: "Soil",      full: "Soil saturated", tone: "alert"  },
  { t: 0,   id: "strike",   label: "LANDSLIDE", full: "Landslide, Chooralmala-Mundakkai", tone: "severe" },
  { t: 6,   id: "rescue",   label: "Rescue",    full: "Rescue mission underway", tone: "alert"  },
  // t+33, not a round number: the flood series peaks at hour 32, and the
  // rescue phase maps hour = t - 1. A marker labelled "peak" has to sit on the
  // actual maximum or it is decoration.
  { t: 33,  id: "peak",     label: "Peak",      full: "Flood peak", tone: "severe" },
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
          ${EVENTS.map((e, i) => `
            <button class="story-mark" data-t="${e.t}" data-tone="${e.tone}"
                    data-row="0" style="left:${pct(e.t)}%" title="${e.full || e.label}">
              <span class="story-mark-label">${e.label}</span>
              <span class="story-mark-dot"></span>
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

    this._layoutMarks();
    // Label widths depend on the font actually in use, so re-run once the web
    // font has settled and again whenever the width changes.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => this._layoutMarks());
    }
    window.addEventListener("resize", () => this._layoutMarks());
  }

  /** Stagger event labels onto two rows so none of them overlap.
   *
   *  Index parity is not enough: it cannot know that "Soil saturated" at T-6h
   *  and "Rescue underway" at T+6h render twelve hours apart on a 168-hour
   *  axis and collide, while two labels far apart would not have. So the rows
   *  are assigned by measuring — walk the markers left to right and drop each
   *  onto the first row whose last label has already ended.
   */
  _layoutMarks() {
    const marks = [...this.root.querySelectorAll(".story-mark")];
    if (!marks.length) return;
    marks.forEach((m) => { m.dataset.row = "0"; });

    const gap = 8;                       // breathing room between two labels
    const rowEnds = [-Infinity, -Infinity];
    marks
      .map((m) => ({ m, box: m.getBoundingClientRect() }))
      .sort((a, b) => a.box.left - b.box.left)
      .forEach(({ m, box }) => {
        const row = box.left >= rowEnds[0] + gap ? 0
                  : box.left >= rowEnds[1] + gap ? 1
                  : 1;                   // both occupied: second row is the
                                         // lesser evil, and the leader line
                                         // still ties it to its position
        m.dataset.row = String(row);
        rowEnds[row] = box.right;
      });
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

