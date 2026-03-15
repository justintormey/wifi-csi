/**
 * WiFi CSI Dashboard — Vitals Panel
 *
 * Side panel showing vital signs for tracked people:
 *   - Breathing rate (always shown when person detected) with sparkline
 *   - Heart rate (conditional, shown only when heartrate.display === true)
 *   - Confidence indicators (signal bars)
 *   - Person selector for multi-person tracking
 *   - Occupancy estimate with confidence
 *   - Stationary duration indicator
 *
 * Sci-fi HUD theme: dark bg, cyan/green accents, monospace text, glow effects.
 *
 * Usage:
 *   import { VitalsPanel } from './vitals-panel.js';
 *   const panel = new VitalsPanel(containerElement);
 *   // On each WebSocket/simulator payload:
 *   panel.update(payload);
 */

// ── Constants ──────────────────────────────────────────────────

const SPARKLINE_POINTS = 60;          // 60 data points = 60s at 1Hz (downsampled from 10Hz)
const SPARKLINE_DOWNSAMPLE = 10;      // record every 10th payload (10Hz → 1Hz)
const HR_FADE_DURATION_MS = 800;      // fade in/out duration for heart rate
const CONFIDENCE_BARS = 5;            // number of signal-strength bars
const MAX_PEOPLE_DISPLAY = 4;

// Sci-fi color palette
const COLORS = {
  cyan:       '#00d4ff',
  cyanDim:    'rgba(0, 212, 255, 0.4)',
  cyanGlow:   'rgba(0, 212, 255, 0.15)',
  green:      '#00ff88',
  greenDim:   'rgba(0, 255, 136, 0.4)',
  amber:      '#ffaa00',
  red:        '#ff3366',
  textPrimary:'#e0f0ff',
  textDim:    'rgba(224, 240, 255, 0.5)',
  bgPanel:    'rgba(8, 12, 24, 0.92)',
  bgCard:     'rgba(15, 25, 45, 0.85)',
  border:     'rgba(0, 212, 255, 0.2)',
};

// ── Sparkline Data Buffer ──────────────────────────────────────
// Stores up to 60 data points = 60 seconds of history. Input is
// downsampled 10:1 (10Hz payload → 1Hz recording) to produce a
// readable time series without overwhelming the chart.

class SparklineBuffer {
  constructor(maxPoints = SPARKLINE_POINTS) {
    this.maxPoints = maxPoints;
    this.points = [];
  }

  push(value) {
    this.points.push(value);
    if (this.points.length > this.maxPoints) {
      this.points.shift();
    }
  }

  clear() {
    this.points = [];
  }

  get length() {
    return this.points.length;
  }
}

// ── Person Vitals State ────────────────────────────────────────

class PersonVitals {
  constructor(id) {
    this.id = id;
    this.breathingSpark = new SparklineBuffer();
    this.heartrateSpark = new SparklineBuffer();
    this.tickCount = 0;

    // Latest payload values
    this.breathing = { rate_bpm: 0, confidence: 0 };
    this.heartrate = { rate_bpm: 0, confidence: 0, display: false };
    this.isStationary = false;
    this.stationaryDurationS = 0;
    this.positionConfidence = 0;

    // HR fade state
    this.hrVisible = false;
    this.hrOpacity = 0;
    this.hrFadeStart = 0;
    this.hrFadeDirection = 0; // 1 = fading in, -1 = fading out, 0 = stable
  }

  updateFromPayload(person) {
    this.breathing = person.breathing || this.breathing;
    this.heartrate = person.heartrate || this.heartrate;
    this.isStationary = person.is_stationary || false;
    this.stationaryDurationS = person.stationary_duration_s || 0;
    this.positionConfidence = person.position_confidence || 0;

    // Downsample for sparklines
    this.tickCount++;
    if (this.tickCount % SPARKLINE_DOWNSAMPLE === 0) {
      this.breathingSpark.push(this.breathing.rate_bpm);
      if (this.heartrate.display) {
        this.heartrateSpark.push(this.heartrate.rate_bpm);
      }
    }

    // HR fade logic
    this._updateHrFade();
  }

  // Heart rate fades in/out over 800ms rather than appearing/disappearing
  // instantly. This communicates that HR detection is gradual — the system
  // needs time to extract a weak signal from CSI data, and losing the
  // reading is similarly transitional rather than binary.
  _updateHrFade() {
    const shouldShow = this.heartrate.display;
    const now = performance.now();

    if (shouldShow && !this.hrVisible) {
      this.hrVisible = true;
      this.hrFadeStart = now;
      this.hrFadeDirection = 1;
    } else if (!shouldShow && this.hrVisible) {
      this.hrFadeStart = now;
      this.hrFadeDirection = -1;
    }

    if (this.hrFadeDirection !== 0) {
      const elapsed = now - this.hrFadeStart;
      const progress = Math.min(elapsed / HR_FADE_DURATION_MS, 1);

      if (this.hrFadeDirection === 1) {
        this.hrOpacity = progress;
      } else {
        this.hrOpacity = 1 - progress;
      }

      if (progress >= 1) {
        this.hrFadeDirection = 0;
        if (!shouldShow) {
          this.hrVisible = false;
          this.hrOpacity = 0;
        }
      }
    }
  }
}

// ── Main Panel ─────────────────────────────────────────────────

export class VitalsPanel {
  /**
   * @param {HTMLElement} container - Container element for the vitals panel
   */
  constructor(container) {
    this.container = container;
    this.people = new Map(); // id → PersonVitals
    this.selectedPersonId = null;
    this.occupancyEstimate = 0;
    this.occupancyConfidence = 0;

    this._build();
  }

  // ── Public API ─────────────────────────────────────────────

  /** Update panel with a WebSocket/simulator payload */
  update(payload) {
    if (!payload) return;

    // Update occupancy
    this.occupancyEstimate = payload.occupancy_estimate || 0;
    this.occupancyConfidence = payload.occupancy_confidence || 0;

    // Track which person IDs are in this payload
    const activeIds = new Set();

    for (const person of (payload.people || [])) {
      activeIds.add(person.id);

      if (!this.people.has(person.id)) {
        this.people.set(person.id, new PersonVitals(person.id));
      }
      this.people.get(person.id).updateFromPayload(person);
    }

    // Remove stale people
    for (const id of this.people.keys()) {
      if (!activeIds.has(id)) {
        this.people.delete(id);
      }
    }

    // Auto-select first person if none selected
    if (!this.selectedPersonId || !this.people.has(this.selectedPersonId)) {
      this.selectedPersonId = this.people.size > 0
        ? this.people.keys().next().value
        : null;
    }

    this._render();
  }

  /** Select a person by ID */
  selectPerson(id) {
    if (this.people.has(id)) {
      this.selectedPersonId = id;
      this._render();
    }
  }

  // ── DOM Construction ───────────────────────────────────────

  _build() {
    this.container.innerHTML = '';
    this.container.classList.add('vitals-panel');

    // Inject scoped styles
    if (!document.getElementById('vitals-panel-styles')) {
      const style = document.createElement('style');
      style.id = 'vitals-panel-styles';
      style.textContent = this._getStyles();
      document.head.appendChild(style);
    }

    // Create panel structure
    this._elOccupancy = this._createElement('div', 'vp-occupancy');
    this._elPersonSelector = this._createElement('div', 'vp-person-selector');
    this._elVitalsArea = this._createElement('div', 'vp-vitals-area');

    this.container.appendChild(this._elOccupancy);
    this.container.appendChild(this._elPersonSelector);
    this.container.appendChild(this._elVitalsArea);
  }

  _createElement(tag, className) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    return el;
  }

  // ── Rendering ──────────────────────────────────────────────

  _render() {
    this._renderOccupancy();
    this._renderPersonSelector();
    this._renderVitals();
  }

  _renderOccupancy() {
    const conf = this.occupancyConfidence;
    const confColor = conf > 0.7 ? COLORS.cyan : conf > 0.4 ? COLORS.amber : COLORS.red;

    this._elOccupancy.innerHTML = `
      <div class="vp-section-label">OCCUPANCY</div>
      <div class="vp-occupancy-row">
        <span class="vp-occupancy-count">${this.occupancyEstimate}</span>
        <span class="vp-occupancy-label">${this.occupancyEstimate === 1 ? 'person' : 'people'}</span>
        <span class="vp-occupancy-conf" style="color: ${confColor}">
          ${this._renderSignalBarsHTML(conf)}
        </span>
      </div>
    `;
  }

  _renderPersonSelector() {
    if (this.people.size <= 1) {
      this._elPersonSelector.innerHTML = '';
      return;
    }

    let i = 0;
    const btnContainer = document.createElement('div');
    btnContainer.className = 'vp-person-btns';
    for (const [id] of this.people) {
      if (i >= MAX_PEOPLE_DISPLAY) break;
      const active = id === this.selectedPersonId;
      const btn = document.createElement('button');
      btn.className = 'vp-person-btn' + (active ? ' active' : '');
      btn.dataset.personId = id;
      const dot = document.createElement('span');
      dot.className = 'vp-person-dot';
      dot.style.background = active ? COLORS.cyan : COLORS.textDim;
      btn.appendChild(dot);
      btn.appendChild(document.createTextNode(' ' + id.toUpperCase()));
      btnContainer.appendChild(btn);
      i++;
    }

    this._elPersonSelector.innerHTML = '';
    const label = document.createElement('div');
    label.className = 'vp-section-label';
    label.textContent = 'TRACKED';
    this._elPersonSelector.appendChild(label);
    this._elPersonSelector.appendChild(btnContainer);

    // Bind click handlers
    for (const btn of this._elPersonSelector.querySelectorAll('.vp-person-btn')) {
      btn.addEventListener('click', () => {
        this.selectPerson(btn.dataset.personId);
      });
    }
  }

  _renderVitals() {
    if (!this.selectedPersonId || !this.people.has(this.selectedPersonId)) {
      this._elVitalsArea.innerHTML = `
        <div class="vp-no-data">
          <div class="vp-no-data-icon">◌</div>
          <div>NO SIGNAL</div>
        </div>
      `;
      return;
    }

    const p = this.people.get(this.selectedPersonId);

    // Stationary indicator
    const stationaryHTML = p.isStationary
      ? `<div class="vp-stationary">
           <span class="vp-stationary-dot"></span>
           STATIONARY ${this._formatDuration(p.stationaryDurationS)}
         </div>`
      : `<div class="vp-stationary vp-moving">
           <span class="vp-moving-dot"></span>
           MOVING
         </div>`;

    // Breathing section (always shown)
    const breathingConfColor = p.breathing.confidence > 0.6 ? COLORS.green
      : p.breathing.confidence > 0.3 ? COLORS.amber : COLORS.red;

    const breathingHTML = `
      <div class="vp-vital-card">
        <div class="vp-vital-header">
          <span class="vp-vital-label">BREATHING</span>
          <span class="vp-vital-conf" style="color: ${breathingConfColor}">
            ${this._renderSignalBarsHTML(p.breathing.confidence)}
          </span>
        </div>
        <div class="vp-vital-value">
          <span class="vp-bpm">${p.breathing.rate_bpm}</span>
          <span class="vp-bpm-unit">bpm</span>
        </div>
        <canvas class="vp-sparkline" data-sparkline="breathing" width="200" height="40"></canvas>
      </div>
    `;

    // Heart rate section (conditional with fade)
    let heartrateHTML = '';
    if (p.hrVisible || p.hrOpacity > 0) {
      const hrConfColor = p.heartrate.confidence > 0.5 ? COLORS.cyan
        : p.heartrate.confidence > 0.25 ? COLORS.amber : COLORS.red;

      heartrateHTML = `
        <div class="vp-vital-card vp-hr-card" style="opacity: ${p.hrOpacity.toFixed(2)}">
          <div class="vp-vital-header">
            <span class="vp-vital-label vp-hr-label">HEART RATE</span>
            <span class="vp-vital-conf" style="color: ${hrConfColor}">
              ${this._renderSignalBarsHTML(p.heartrate.confidence)}
            </span>
          </div>
          <div class="vp-vital-value vp-hr-value">
            <span class="vp-bpm">${p.heartrate.rate_bpm ?? '--'}</span>
            <span class="vp-bpm-unit">bpm</span>
          </div>
          <canvas class="vp-sparkline" data-sparkline="heartrate" width="200" height="40"></canvas>
        </div>
      `;
    }

    this._elVitalsArea.innerHTML = `
      ${stationaryHTML}
      ${breathingHTML}
      ${heartrateHTML}
    `;

    // Draw sparklines onto canvases
    this._drawSparkline(
      this._elVitalsArea.querySelector('[data-sparkline="breathing"]'),
      p.breathingSpark,
      COLORS.green,
      8, 24
    );

    if (p.hrVisible || p.hrOpacity > 0) {
      this._drawSparkline(
        this._elVitalsArea.querySelector('[data-sparkline="heartrate"]'),
        p.heartrateSpark,
        COLORS.cyan,
        50, 110
      );
    }
  }

  // ── Sparkline Drawing ──────────────────────────────────────

  _drawSparkline(canvas, buffer, color, minVal, maxVal) {
    if (!canvas || buffer.length < 2) return;

    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    const pad = 2;
    const drawW = w - pad * 2;
    const drawH = h - pad * 2;

    ctx.clearRect(0, 0, w, h);

    const points = buffer.points;
    const range = maxVal - minVal || 1;

    // Build path
    ctx.beginPath();
    for (let i = 0; i < points.length; i++) {
      const x = pad + (i / (SPARKLINE_POINTS - 1)) * drawW;
      const normalized = (points[i] - minVal) / range;
      const y = pad + drawH - normalized * drawH;

      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }

    // Glow effect
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = color;
    ctx.shadowBlur = 4;
    ctx.stroke();

    // Fill under curve
    ctx.shadowBlur = 0;
    const lastX = pad + ((points.length - 1) / (SPARKLINE_POINTS - 1)) * drawW;
    const firstX = pad;
    ctx.lineTo(lastX, pad + drawH);
    ctx.lineTo(firstX, pad + drawH);
    ctx.closePath();
    ctx.fillStyle = this._colorToAlpha(color, 0.08);
    ctx.fill();
  }

  // ── Helpers ────────────────────────────────────────────────

  _renderSignalBarsHTML(confidence) {
    const filledBars = Math.round(confidence * CONFIDENCE_BARS);
    let html = '<span class="vp-signal-bars">';
    for (let i = 0; i < CONFIDENCE_BARS; i++) {
      const height = 4 + i * 3;
      const filled = i < filledBars;
      html += `<span class="vp-bar${filled ? ' filled' : ''}" style="height:${height}px"></span>`;
    }
    html += '</span>';
    return html;
  }

  _colorToAlpha(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  _formatDuration(seconds) {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return `${m}m ${s}s`;
  }

  // ── Styles ─────────────────────────────────────────────────

  _getStyles() {
    return `
      .vitals-panel {
        font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
        color: ${COLORS.textPrimary};
        background: ${COLORS.bgPanel};
        border-left: 1px solid ${COLORS.border};
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 16px;
        overflow-y: auto;
        user-select: none;
      }

      .vp-section-label {
        font-size: 10px;
        letter-spacing: 2px;
        color: ${COLORS.textDim};
        margin-bottom: 6px;
      }

      /* ── Occupancy ── */
      .vp-occupancy-row {
        display: flex;
        align-items: baseline;
        gap: 8px;
      }
      .vp-occupancy-count {
        font-size: 32px;
        font-weight: 700;
        color: ${COLORS.cyan};
        text-shadow: 0 0 12px ${COLORS.cyanGlow};
        line-height: 1;
      }
      .vp-occupancy-label {
        font-size: 12px;
        color: ${COLORS.textDim};
      }
      .vp-occupancy-conf {
        margin-left: auto;
      }

      /* ── Person Selector ── */
      .vp-person-btns {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
      }
      .vp-person-btn {
        background: ${COLORS.bgCard};
        border: 1px solid ${COLORS.border};
        color: ${COLORS.textDim};
        font-family: inherit;
        font-size: 11px;
        padding: 4px 10px;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 5px;
        transition: all 0.2s;
      }
      .vp-person-btn:hover {
        border-color: ${COLORS.cyanDim};
        color: ${COLORS.textPrimary};
      }
      .vp-person-btn.active {
        border-color: ${COLORS.cyan};
        color: ${COLORS.cyan};
        box-shadow: 0 0 8px ${COLORS.cyanGlow};
      }
      .vp-person-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        display: inline-block;
      }

      /* ── Stationary Indicator ── */
      .vp-stationary {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 11px;
        letter-spacing: 1px;
        color: ${COLORS.green};
        padding: 6px 10px;
        background: rgba(0, 255, 136, 0.06);
        border: 1px solid rgba(0, 255, 136, 0.15);
      }
      .vp-stationary.vp-moving {
        color: ${COLORS.amber};
        background: rgba(255, 170, 0, 0.06);
        border-color: rgba(255, 170, 0, 0.15);
      }
      .vp-stationary-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: ${COLORS.green};
        box-shadow: 0 0 6px ${COLORS.green};
      }
      .vp-moving-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: ${COLORS.amber};
        box-shadow: 0 0 6px ${COLORS.amber};
        animation: vp-pulse 1s ease-in-out infinite;
      }

      /* ── Vital Card ── */
      .vp-vital-card {
        background: ${COLORS.bgCard};
        border: 1px solid ${COLORS.border};
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      .vp-vital-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
      .vp-vital-label {
        font-size: 10px;
        letter-spacing: 2px;
        color: ${COLORS.greenDim};
      }
      .vp-hr-label {
        color: ${COLORS.cyanDim};
      }
      .vp-vital-value {
        display: flex;
        align-items: baseline;
        gap: 4px;
      }
      .vp-bpm {
        font-size: 28px;
        font-weight: 700;
        color: ${COLORS.green};
        text-shadow: 0 0 10px rgba(0, 255, 136, 0.3);
        line-height: 1;
      }
      .vp-hr-value .vp-bpm {
        font-size: 22px;
        color: ${COLORS.cyan};
        text-shadow: 0 0 10px ${COLORS.cyanGlow};
      }
      .vp-bpm-unit {
        font-size: 11px;
        color: ${COLORS.textDim};
      }
      .vp-hr-card {
        transition: opacity ${HR_FADE_DURATION_MS}ms ease;
      }

      /* ── Sparkline ── */
      .vp-sparkline {
        width: 100%;
        height: 40px;
        display: block;
      }

      /* ── Signal Bars ── */
      .vp-signal-bars {
        display: inline-flex;
        align-items: flex-end;
        gap: 2px;
        height: 16px;
      }
      .vp-bar {
        width: 3px;
        background: rgba(255, 255, 255, 0.12);
        transition: background 0.3s;
      }
      .vp-bar.filled {
        background: currentColor;
      }
      .vp-vital-conf {
        display: flex;
        align-items: center;
      }

      /* ── No Data State ── */
      .vp-no-data {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 8px;
        padding: 40px 0;
        color: ${COLORS.textDim};
        font-size: 12px;
        letter-spacing: 2px;
      }
      .vp-no-data-icon {
        font-size: 32px;
        animation: vp-pulse 2s ease-in-out infinite;
      }

      @keyframes vp-pulse {
        0%, 100% { opacity: 0.4; }
        50% { opacity: 1; }
      }
    `;
  }
}
