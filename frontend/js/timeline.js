/* The event time slider.
 *
 * The sparkline behind the slider is not decoration: it shows flooded area and
 * rainfall through the event, so the shape of the flood is legible before the
 * user drags anything. Dragging it is the single most persuasive thing in a
 * live demonstration, because motion communicates a rising and receding flood in
 * a way that no static extent map does.
 */

import { fmt } from "./api.js";

export class Timeline {
  constructor({ root, slider, canvas, hourEl, subEl, playBtn, playIcon }) {
    this.root = root;
    this.slider = slider;
    this.canvas = canvas;
    this.hourEl = hourEl;
    this.subEl = subEl;
    this.playBtn = playBtn;
    this.playIcon = playIcon;
    this.data = null;
    this.hour = 0;
    this.playing = false;
    this._raf = null;
    this._last = 0;
    this.onChange = null;

    this.slider.addEventListener("input", () => {
      this.stop();
      this.setHour(Number(this.slider.value));
    });
    this.playBtn.addEventListener("click", () => this.toggle());
    window.addEventListener("resize", () => this.draw());
  }

  load(series) {
    this.data = series;
    this.slider.max = String(series.hours - 1);
    this.root.hidden = false;
    this.setHour(series.peak_hour ?? 0);
    this.draw();
  }

  clear() {
    this.stop();
    this.root.hidden = true;
    this.data = null;
  }

  setHour(h) {
    if (!this.data) return;
    this.hour = Math.max(0, Math.min(this.data.hours - 1, Math.round(h)));
    this.slider.value = String(this.hour);

    const day = Math.floor(this.hour / 24) + 1;
    const hh = String(this.hour % 24).padStart(2, "0");
    this.hourEl.textContent = `Day ${day}, ${hh}:00`;
    const affected = this.data.population_affected[this.hour] ?? 0;
    const area = this.data.area_km2[this.hour] ?? 0;
    const peak = this.hour === this.data.peak_hour ? " · PEAK" : "";
    this.subEl.textContent =
      `${fmt.compact(affected)} affected · ${fmt.num(area, 0)} km²${peak}`;

    this.draw();
    if (this.onChange) this.onChange(this.hour);
  }

  toggle() { this.playing ? this.stop() : this.play(); }

  play() {
    if (!this.data) return;
    if (this.hour >= this.data.hours - 1) this.setHour(0);
    this.playing = true;
    this.playIcon.setAttribute("d", "M2 1h3v10H2zM7 1h3v10H7z");   // pause
    this.playBtn.setAttribute("aria-label", "Pause event timeline");
    this._last = performance.now();
    const step = (now) => {
      if (!this.playing) return;
      // Fixed 14 hours per second regardless of frame rate, so the animation
      // reads the same on any machine in the room.
      const dt = (now - this._last) / 1000;
      this._last = now;
      const next = this.hour + dt * 14;
      if (next >= this.data.hours - 1) { this.setHour(this.data.hours - 1); this.stop(); return; }
      this.setHour(next);
      this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }

  stop() {
    this.playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this.playIcon.setAttribute("d", "M2 1l9 5-9 5z");              // play
    this.playBtn.setAttribute("aria-label", "Play event timeline");
  }

  draw() {
    const c = this.canvas, d = this.data;
    if (!c || !d) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = c.clientWidth || 400, h = 34;
    c.width = w * dpr; c.height = h * dpr;
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const css = (n) => getComputedStyle(document.documentElement)
      .getPropertyValue(n).trim();
    const n = d.hours;
    const x = (i) => (i / (n - 1)) * w;

    // rainfall, drawn as bars behind the area curve
    const rmax = Math.max(...d.rainfall_mm, 0.001);
    ctx.fillStyle = css("--surface-3");
    for (let i = 0; i < n; i++) {
      const bh = (d.rainfall_mm[i] / rmax) * (h * 0.55);
      ctx.fillRect(x(i), h - bh, Math.max(w / n - 0.5, 0.6), bh);
    }

    // flooded area curve
    const amax = Math.max(...d.area_km2, 0.001);
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let i = 0; i < n; i++) ctx.lineTo(x(i), h - (d.area_km2[i] / amax) * (h - 3));
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = css("--accent-bg");
    ctx.fill();
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const px = x(i), py = h - (d.area_km2[i] / amax) * (h - 3);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    }
    ctx.strokeStyle = css("--accent");
    ctx.lineWidth = 1.4;
    ctx.stroke();

    // playhead
    const px = x(this.hour);
    ctx.strokeStyle = css("--text");
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke();
  }
}
