/**
 * Confidence-Driven Tracking Visualization Overlay
 *
 * Renders tracked people on a canvas layer above the floor plan SVG.
 * Visual encoding varies by position_confidence:
 *   High (>0.8):  Sharp dot, tight cyan glow, small uncertainty circle, solid trail
 *   Medium (0.4-0.8): Soft-edged, diffuse, medium circle, dashed trail
 *   Low (<0.4):   Blurred, ghostly, pulsing opacity, large circle, no trail, "?" label
 *
 * Usage:
 *   import { TrackerOverlay } from './tracker-overlay.js';
 *   const tracker = new TrackerOverlay(canvas, floorConfig);
 *   tracker.start();
 *   // On each payload:
 *   tracker.update(payload.people);
 */

import { CONFIG } from './config.js';

// ── Constants ──────────────────────────────────────────────────

const TRAIL_MAX_LENGTH = 10;
const TRAIL_FADE_ALPHA = 0.08;         // alpha decrement per trail point age
const LABEL_FONT = '11px "SF Mono", "Fira Code", monospace';
const LABEL_OFFSET_Y = -18;            // pixels above blob center

// Confidence thresholds
const CONF_HIGH = 0.8;
const CONF_LOW = 0.4;

// Blob rendering
const BLOB_BASE_RADIUS_PX = 6;
const GLOW_RADIUS_MULTIPLIER = 3.0;
const PULSE_SPEED = 3.0;              // radians/sec for low-conf pulsing
const PULSE_AMPLITUDE = 0.35;

// Uncertainty circle
const UNCERTAINTY_LINE_WIDTH = 1.5;
const UNCERTAINTY_DASH_PATTERN = [6, 4];

// Colors
const COLOR_CYAN = { r: 0, g: 220, b: 255 };
const COLOR_CYAN_DIM = { r: 0, g: 150, b: 200 };
const COLOR_GHOST = { r: 100, g: 140, b: 180 };

// Smooth interpolation speed (units/sec convergence rate)
const POSITION_LERP_SPEED = 8.0;
const CONFIDENCE_LERP_SPEED = 4.0;

// ── Utility ────────────────────────────────────────────────────

function lerp(a, b, t) {
  return a + (b - a) * Math.min(1, Math.max(0, t));
}

function rgba(color, alpha) {
  return `rgba(${color.r}, ${color.g}, ${color.b}, ${Math.max(0, Math.min(1, alpha))})`;
}

// ── Tracked Person State ───────────────────────────────────────
// Each person smoothly interpolates position and confidence between
// payload updates (10Hz) at animation frame rate (~60fps). This prevents
// choppy movement on high-refresh displays. Trail points are only added
// when movement exceeds 0.15m to avoid cluttering the display with
// jitter-induced micro-movements.

class TrackedPerson {
  constructor(id) {
    this.id = id;

    // Display position (smoothly interpolated)
    this.displayX = 0;
    this.displayY = 0;
    this.displayConf = 0.5;

    // Target values from latest payload
    this.targetX = 0;
    this.targetY = 0;
    this.targetConf = 0.5;
    this.uncertaintyRadius = 1.0;

    // Trail history (in meters)
    this.trail = [];

    // Pulse phase for low-confidence animation
    this.pulsePhase = Math.random() * Math.PI * 2;

    // Freshness: time since last update (seconds)
    this.lastUpdateAge = 0;
    this.initialized = false;
  }

  /** Update from a payload person entry */
  applyPayload(person) {
    this.targetX = person.x;
    this.targetY = person.y;
    this.targetConf = person.position_confidence;
    this.uncertaintyRadius = person.uncertainty_radius_m || 1.0;
    this.lastUpdateAge = 0;

    if (!this.initialized) {
      // Snap to position on first update (no interpolation)
      this.displayX = this.targetX;
      this.displayY = this.targetY;
      this.displayConf = this.targetConf;
      this.initialized = true;
    }

    // Push trail point only if moved meaningfully (>0.15m)
    const lastTrail = this.trail[this.trail.length - 1];
    if (!lastTrail || Math.hypot(this.targetX - lastTrail.x, this.targetY - lastTrail.y) > 0.15) {
      this.trail.push({
        x: this.targetX,
        y: this.targetY,
        conf: this.targetConf,
        age: 0,
      });
      if (this.trail.length > TRAIL_MAX_LENGTH) {
        this.trail.shift();
      }
    }
  }

  /** Advance interpolation and animations */
  tick(dt) {
    this.lastUpdateAge += dt;
    this.pulsePhase += PULSE_SPEED * dt;

    // Smooth position interpolation
    const posT = POSITION_LERP_SPEED * dt;
    this.displayX = lerp(this.displayX, this.targetX, posT);
    this.displayY = lerp(this.displayY, this.targetY, posT);
    this.displayConf = lerp(this.displayConf, this.targetConf, CONFIDENCE_LERP_SPEED * dt);

    // Age trail points
    for (const pt of this.trail) {
      pt.age += dt;
    }
  }

  /** Whether this person is stale (no updates for >5s) */
  get isStale() {
    return this.lastUpdateAge > 5.0;
  }

  /** Confidence tier: 'high', 'medium', or 'low' */
  get tier() {
    if (this.displayConf >= CONF_HIGH) return 'high';
    if (this.displayConf >= CONF_LOW) return 'medium';
    return 'low';
  }
}

// ── Main Overlay ───────────────────────────────────────────────

export class TrackerOverlay {
  /**
   * @param {HTMLCanvasElement} canvas - Canvas element sized to match floor plan
   * @param {Object} [floorConfig] - Floor config from CONFIG.floors[n]
   */
  constructor(canvas, floorConfig) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.floor = floorConfig || CONFIG.floors[0];

    /** @type {Map<string, TrackedPerson>} */
    this.people = new Map();

    // Animation state
    this.running = false;
    this.rafId = null;
    this.lastTime = 0;

    this._updateTransform();
  }

  // ── Public API ─────────────────────────────────────────────

  /**
   * Update tracked people from a WebSocket/simulator payload.
   * @param {Array} peoplePayload - Array of person objects from payload.people
   */
  update(peoplePayload) {
    if (!peoplePayload) return;

    const seenIds = new Set();

    for (const person of peoplePayload) {
      seenIds.add(person.id);

      let tracked = this.people.get(person.id);
      if (!tracked) {
        tracked = new TrackedPerson(person.id);
        this.people.set(person.id, tracked);
      }
      tracked.applyPayload(person);
    }

    // Remove people no longer in payload after they go stale
    for (const [id, tracked] of this.people) {
      if (!seenIds.has(id) && tracked.isStale) {
        this.people.delete(id);
      }
    }
  }

  /** Start the render loop */
  start() {
    if (this.running) return;
    this.running = true;
    this.lastTime = performance.now();
    this._loop();
  }

  /** Stop the render loop */
  stop() {
    this.running = false;
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }

  /** Call when canvas is resized */
  resize() {
    this._updateTransform();
  }

  // ── Coordinate Transform ───────────────────────────────────

  _updateTransform() {
    this.scaleX = this.canvas.width / this.floor.width;
    this.scaleY = this.canvas.height / this.floor.height;
    this.avgScale = (this.scaleX + this.scaleY) * 0.5;
  }

  _px(meters) { return meters * this.scaleX; }
  _py(meters) { return meters * this.scaleY; }
  _pr(meters) { return meters * this.avgScale; }

  // ── Render Loop ────────────────────────────────────────────

  _loop() {
    if (!this.running) return;
    this.rafId = requestAnimationFrame((now) => {
      const dt = Math.min((now - this.lastTime) / 1000, 0.1);
      this.lastTime = now;
      this._tick(dt);
      this._draw();
      this._loop();
    });
  }

  _tick(dt) {
    for (const person of this.people.values()) {
      person.tick(dt);
    }
  }

  // ── Drawing ────────────────────────────────────────────────

  _draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // Draw all instances of each layer before the next layer. This ensures
    // blobs are never occluded by another person's trail or uncertainty circle.
    // Order: trails (back) → uncertainty circles → blobs → labels (front)

    for (const person of this.people.values()) {
      this._drawTrail(ctx, person);
    }
    for (const person of this.people.values()) {
      this._drawUncertaintyCircle(ctx, person);
    }
    for (const person of this.people.values()) {
      this._drawBlob(ctx, person);
    }
    for (const person of this.people.values()) {
      this._drawLabel(ctx, person);
    }
  }

  // ── Trail Rendering ────────────────────────────────────────

  _drawTrail(ctx, person) {
    const trail = person.trail;
    if (trail.length < 2) return;

    const tier = person.tier;
    if (tier === 'low') return; // No trail for low confidence

    ctx.save();

    for (let i = 1; i < trail.length; i++) {
      const prev = trail[i - 1];
      const curr = trail[i];

      // Fade based on age (older = more transparent)
      const ageFactor = Math.max(0, 1 - curr.age * TRAIL_FADE_ALPHA);
      const segmentAlpha = ageFactor * (tier === 'high' ? 0.7 : 0.35);

      if (segmentAlpha < 0.01) continue;

      const x0 = this._px(prev.x);
      const y0 = this._py(prev.y);
      const x1 = this._px(curr.x);
      const y1 = this._py(curr.y);

      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);

      if (tier === 'high') {
        ctx.strokeStyle = rgba(COLOR_CYAN, segmentAlpha);
        ctx.lineWidth = 2;
        ctx.setLineDash([]);
      } else {
        // Medium: dashed, dimmer
        ctx.strokeStyle = rgba(COLOR_CYAN_DIM, segmentAlpha);
        ctx.lineWidth = 1.5;
        ctx.setLineDash(UNCERTAINTY_DASH_PATTERN);
      }

      ctx.stroke();
    }

    ctx.setLineDash([]);
    ctx.restore();
  }

  // ── Uncertainty Circle ─────────────────────────────────────

  _drawUncertaintyCircle(ctx, person) {
    const cx = this._px(person.displayX);
    const cy = this._py(person.displayY);
    const radius = this._pr(person.uncertaintyRadius);
    const tier = person.tier;

    ctx.save();

    let alpha, dashPattern;
    if (tier === 'high') {
      alpha = 0.3;
      dashPattern = [];
    } else if (tier === 'medium') {
      alpha = 0.25;
      dashPattern = UNCERTAINTY_DASH_PATTERN;
    } else {
      // Low: large, pulsing
      const pulse = 0.5 + PULSE_AMPLITUDE * Math.sin(person.pulsePhase);
      alpha = 0.2 * pulse;
      dashPattern = [4, 6];
    }

    ctx.strokeStyle = rgba(COLOR_CYAN_DIM, alpha);
    ctx.lineWidth = UNCERTAINTY_LINE_WIDTH;
    ctx.setLineDash(dashPattern);

    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();

    // Subtle fill for low confidence
    if (tier === 'low') {
      ctx.fillStyle = rgba(COLOR_GHOST, 0.04);
      ctx.fill();
    }

    ctx.setLineDash([]);
    ctx.restore();
  }

  // ── Blob Rendering ─────────────────────────────────────────

  _drawBlob(ctx, person) {
    const cx = this._px(person.displayX);
    const cy = this._py(person.displayY);
    const tier = person.tier;
    const conf = person.displayConf;

    ctx.save();

    if (tier === 'high') {
      this._drawHighConfBlob(ctx, cx, cy, conf);
    } else if (tier === 'medium') {
      this._drawMedConfBlob(ctx, cx, cy, conf);
    } else {
      this._drawLowConfBlob(ctx, cx, cy, conf, person.pulsePhase);
    }

    ctx.restore();
  }

  _drawHighConfBlob(ctx, cx, cy, conf) {
    const r = BLOB_BASE_RADIUS_PX;
    const glowR = r * GLOW_RADIUS_MULTIPLIER;

    // Outer glow
    const glow = ctx.createRadialGradient(cx, cy, r * 0.5, cx, cy, glowR);
    glow.addColorStop(0, rgba(COLOR_CYAN, 0.4));
    glow.addColorStop(0.4, rgba(COLOR_CYAN, 0.15));
    glow.addColorStop(1, rgba(COLOR_CYAN, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, glowR, 0, Math.PI * 2);
    ctx.fill();

    // Core dot — bright, crisp
    ctx.fillStyle = rgba(COLOR_CYAN, 0.95);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();

    // Inner highlight
    ctx.fillStyle = 'rgba(255, 255, 255, 0.5)';
    ctx.beginPath();
    ctx.arc(cx - r * 0.25, cy - r * 0.25, r * 0.35, 0, Math.PI * 2);
    ctx.fill();
  }

  _drawMedConfBlob(ctx, cx, cy, conf) {
    const r = BLOB_BASE_RADIUS_PX * 1.2;
    const glowR = r * GLOW_RADIUS_MULTIPLIER * 1.2;

    // Softer, more diffuse glow
    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowR);
    glow.addColorStop(0, rgba(COLOR_CYAN_DIM, 0.35));
    glow.addColorStop(0.3, rgba(COLOR_CYAN_DIM, 0.15));
    glow.addColorStop(1, rgba(COLOR_CYAN_DIM, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, glowR, 0, Math.PI * 2);
    ctx.fill();

    // Core dot — slightly diffuse
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    core.addColorStop(0, rgba(COLOR_CYAN_DIM, 0.7));
    core.addColorStop(1, rgba(COLOR_CYAN_DIM, 0.2));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
  }

  _drawLowConfBlob(ctx, cx, cy, conf, pulsePhase) {
    const pulse = 0.5 + PULSE_AMPLITUDE * Math.sin(pulsePhase);
    const r = BLOB_BASE_RADIUS_PX * 1.5;
    const glowR = r * GLOW_RADIUS_MULTIPLIER * 1.5;

    // Ghostly, pulsing glow
    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowR);
    glow.addColorStop(0, rgba(COLOR_GHOST, 0.25 * pulse));
    glow.addColorStop(0.4, rgba(COLOR_GHOST, 0.08 * pulse));
    glow.addColorStop(1, rgba(COLOR_GHOST, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, glowR, 0, Math.PI * 2);
    ctx.fill();

    // Blurred core
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    core.addColorStop(0, rgba(COLOR_GHOST, 0.4 * pulse));
    core.addColorStop(1, rgba(COLOR_GHOST, 0));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
  }

  // ── Label Rendering ────────────────────────────────────────

  _drawLabel(ctx, person) {
    const cx = this._px(person.displayX);
    const cy = this._py(person.displayY);
    const tier = person.tier;

    ctx.save();
    ctx.font = LABEL_FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';

    let label, color, alpha;

    if (tier === 'high') {
      label = person.id;
      color = COLOR_CYAN;
      alpha = 0.9;
    } else if (tier === 'medium') {
      label = `~${person.id}`;
      color = COLOR_CYAN_DIM;
      alpha = 0.6;
    } else {
      label = '?';
      color = COLOR_GHOST;
      const pulse = 0.5 + PULSE_AMPLITUDE * Math.sin(person.pulsePhase);
      alpha = 0.5 * pulse;
    }

    // Text shadow for readability
    ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
    ctx.fillText(label, cx + 1, cy + LABEL_OFFSET_Y + 1);

    // Label text
    ctx.fillStyle = rgba(color, alpha);
    ctx.fillText(label, cx, cy + LABEL_OFFSET_Y);

    ctx.restore();
  }
}
