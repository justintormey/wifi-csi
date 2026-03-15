/**
 * Noise & Signal Quality Visualization Overlay
 *
 * Renders three visual effects on a <canvas> layer between the floor plan and tracking overlay:
 *   1. Noise clouds — translucent fog patches in zones with poor signal quality
 *   2. Wave distortion lines — ripple animations radiating from high-variance areas
 *   3. Zone confidence overlay — rooms dim/brighten based on aggregate signal quality
 *
 * All effects are driven by `zone_signal_quality` from the WebSocket/simulator payload.
 * Targets 60fps using requestAnimationFrame with minimal per-frame allocation.
 *
 * Usage:
 *   import { NoiseOverlay } from './noise-overlay.js';
 *   const overlay = new NoiseOverlay(canvas, floorConfig);
 *   overlay.start();
 *   // On each WebSocket/simulator payload:
 *   overlay.update(payload.zone_signal_quality);
 */

import { CONFIG } from './config.js';

// ── Constants ──────────────────────────────────────────────────

const CLOUD_COUNT_PER_ZONE = 3;
const CLOUD_DRIFT_SPEED = 0.15;       // meters/sec drift
const CLOUD_MIN_RADIUS = 0.6;         // meters
const CLOUD_MAX_RADIUS = 2.0;
const CLOUD_BASE_ALPHA = 0.35;        // max opacity at quality=0

const RIPPLE_MAX_COUNT = 12;
const RIPPLE_EXPAND_SPEED = 1.5;      // meters/sec
const RIPPLE_MAX_RADIUS = 3.0;
const RIPPLE_SPAWN_INTERVAL_S = 0.8;
const RIPPLE_LINE_WIDTH_PX = 1.5;

const ZONE_DIM_ALPHA = 0.45;          // max dimming at quality=0
const QUALITY_LERP_SPEED = 2.0;       // how fast displayed quality tracks actual

// ── Utility ────────────────────────────────────────────────────

function lerp(a, b, t) {
  return a + (b - a) * Math.min(1, Math.max(0, t));
}

function randRange(min, max) {
  return min + Math.random() * (max - min);
}

// ── Cloud Particle ─────────────────────────────────────────────

class CloudParticle {
  constructor(zone) {
    this.zone = zone;
    this.reset(zone);
  }

  reset(zone) {
    // Random position within zone bounds
    this.x = zone.x + randRange(0.3, zone.w - 0.3);
    this.y = zone.y + randRange(0.3, zone.h - 0.3);
    this.radius = randRange(CLOUD_MIN_RADIUS, CLOUD_MAX_RADIUS);
    // Drift direction (slow random walk)
    this.dx = randRange(-1, 1) * CLOUD_DRIFT_SPEED;
    this.dy = randRange(-1, 1) * CLOUD_DRIFT_SPEED;
    // Phase offset for pulsing
    this.phase = Math.random() * Math.PI * 2;
  }

  tick(dt) {
    this.x += this.dx * dt;
    this.y += this.dy * dt;
    this.phase += dt * 0.5;

    // Bounce off zone walls
    const z = this.zone;
    if (this.x - this.radius < z.x || this.x + this.radius > z.x + z.w) {
      this.dx = -this.dx;
      this.x = Math.max(z.x + this.radius, Math.min(z.x + z.w - this.radius, this.x));
    }
    if (this.y - this.radius < z.y || this.y + this.radius > z.y + z.h) {
      this.dy = -this.dy;
      this.y = Math.max(z.y + this.radius, Math.min(z.y + z.h - this.radius, this.y));
    }
  }
}

// ── Ripple ──────────────────────────────────────────────────────

class Ripple {
  constructor(x, y) {
    this.x = x;
    this.y = y;
    this.radius = 0;
    this.alive = true;
  }

  tick(dt) {
    this.radius += RIPPLE_EXPAND_SPEED * dt;
    if (this.radius > RIPPLE_MAX_RADIUS) {
      this.alive = false;
    }
  }

  /** Opacity fades as ripple expands */
  get alpha() {
    return Math.max(0, 1 - this.radius / RIPPLE_MAX_RADIUS) * 0.5;
  }
}

// ── Main Overlay ───────────────────────────────────────────────

export class NoiseOverlay {
  /**
   * @param {HTMLCanvasElement} canvas - Canvas element sized to match the floor plan container
   * @param {Object} [floorConfig] - Floor config from CONFIG.floors[n]. Defaults to floor 0.
   */
  constructor(canvas, floorConfig) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.floor = floorConfig || CONFIG.floors[0];

    // Current and display-smoothed zone qualities
    this.targetQualities = {};
    this.displayQualities = {};
    for (const [name, zone] of Object.entries(this.floor.rooms)) {
      const base = this.floor.baseSignalQuality[name] || 0.5;
      this.targetQualities[name] = base;
      this.displayQualities[name] = base;
    }

    // Cloud particles per zone
    this.clouds = {};
    for (const [name, zone] of Object.entries(this.floor.rooms)) {
      this.clouds[name] = [];
      for (let i = 0; i < CLOUD_COUNT_PER_ZONE; i++) {
        this.clouds[name].push(new CloudParticle(zone));
      }
    }

    // Ripple pool (reused to avoid GC)
    this.ripples = [];
    this.rippleTimer = 0;

    // Variance tracking for ripple spawning
    this.prevQualities = { ...this.targetQualities };

    // Animation state
    this.running = false;
    this.rafId = null;
    this.lastTime = 0;

    // Coordinate transform: meters → canvas pixels
    this._updateTransform();
  }

  // ── Public API ─────────────────────────────────────────────

  /** Update zone signal qualities from a WebSocket/simulator payload */
  update(zoneSignalQuality) {
    if (!zoneSignalQuality) return;
    for (const [zone, quality] of Object.entries(zoneSignalQuality)) {
      if (zone in this.targetQualities) {
        this.targetQualities[zone] = quality;
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
  }

  /** Convert meters to canvas pixels (x) */
  _px(meters) {
    return meters * this.scaleX;
  }

  /** Convert meters to canvas pixels (y) */
  _py(meters) {
    return meters * this.scaleY;
  }

  /** Average of scaleX/scaleY for radius */
  _pr(meters) {
    return meters * (this.scaleX + this.scaleY) * 0.5;
  }

  // ── Render Loop ────────────────────────────────────────────

  _loop() {
    if (!this.running) return;
    this.rafId = requestAnimationFrame((now) => {
      const dt = Math.min((now - this.lastTime) / 1000, 0.1); // cap at 100ms
      this.lastTime = now;
      this._tick(dt);
      this._draw();
      this._loop();
    });
  }

  _tick(dt) {
    // Smooth displayed qualities toward target
    for (const zone of Object.keys(this.displayQualities)) {
      this.displayQualities[zone] = lerp(
        this.displayQualities[zone],
        this.targetQualities[zone],
        QUALITY_LERP_SPEED * dt
      );
    }

    // Tick cloud particles
    for (const name of Object.keys(this.clouds)) {
      for (const cloud of this.clouds[name]) {
        cloud.tick(dt);
      }
    }

    // Spawn ripples in high-variance zones
    this.rippleTimer += dt;
    if (this.rippleTimer >= RIPPLE_SPAWN_INTERVAL_S) {
      this.rippleTimer = 0;
      this._spawnRipples();
    }

    // Tick ripples
    for (let i = this.ripples.length - 1; i >= 0; i--) {
      this.ripples[i].tick(dt);
      if (!this.ripples[i].alive) {
        this.ripples.splice(i, 1);
      }
    }

    // Store previous qualities for variance detection
    for (const zone of Object.keys(this.targetQualities)) {
      this.prevQualities[zone] = this.targetQualities[zone];
    }
  }

  _spawnRipples() {
    // Spawn ripples in zones with low quality (high noise) or high variance
    for (const [name, zone] of Object.entries(this.floor.rooms)) {
      const quality = this.displayQualities[name] || 0.5;
      // Lower quality = higher chance of ripple
      const spawnChance = Math.pow(1 - quality, 2);

      if (Math.random() < spawnChance && this.ripples.length < RIPPLE_MAX_COUNT) {
        const rx = zone.x + randRange(0.5, zone.w - 0.5);
        const ry = zone.y + randRange(0.5, zone.h - 0.5);
        this.ripples.push(new Ripple(rx, ry));
      }
    }
  }

  // ── Drawing ────────────────────────────────────────────────

  _draw() {
    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;

    ctx.clearRect(0, 0, w, h);

    // Layer 1: Zone confidence dimming (bottom)
    this._drawZoneDimming(ctx);

    // Layer 2: Noise clouds (middle)
    this._drawNoiseClouds(ctx);

    // Layer 3: Wave distortion ripples (top)
    this._drawRipples(ctx);
  }

  _drawZoneDimming(ctx) {
    for (const [name, zone] of Object.entries(this.floor.rooms)) {
      const quality = this.displayQualities[name] || 0.5;
      // Poor quality → higher dimming
      const dimAlpha = (1 - quality) * ZONE_DIM_ALPHA;

      if (dimAlpha < 0.01) continue;

      const x = this._px(zone.x);
      const y = this._py(zone.y);
      const w = this._px(zone.w);
      const h = this._py(zone.h);

      // Grey overlay — desaturates the zone
      ctx.fillStyle = `rgba(30, 35, 50, ${dimAlpha})`;
      ctx.fillRect(x, y, w, h);
    }
  }

  _drawNoiseClouds(ctx) {
    for (const [name, clouds] of Object.entries(this.clouds)) {
      const quality = this.displayQualities[name] || 0.5;
      // Opacity scales with (1 - quality)
      const baseAlpha = (1 - quality) * CLOUD_BASE_ALPHA;

      if (baseAlpha < 0.01) continue;

      const zone = this.floor.rooms[name];

      // Clip to zone bounds
      ctx.save();
      ctx.beginPath();
      ctx.rect(this._px(zone.x), this._py(zone.y), this._px(zone.w), this._py(zone.h));
      ctx.clip();

      for (const cloud of clouds) {
        const pulseFactor = 0.8 + 0.2 * Math.sin(cloud.phase);
        const alpha = baseAlpha * pulseFactor;
        const cx = this._px(cloud.x);
        const cy = this._py(cloud.y);
        const r = this._pr(cloud.radius);

        // Radial gradient: fog-of-war aesthetic
        const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        gradient.addColorStop(0, `rgba(100, 120, 160, ${alpha})`);
        gradient.addColorStop(0.5, `rgba(70, 85, 120, ${alpha * 0.6})`);
        gradient.addColorStop(1, `rgba(50, 60, 90, 0)`);

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.restore();
    }
  }

  _drawRipples(ctx) {
    if (this.ripples.length === 0) return;

    ctx.lineWidth = RIPPLE_LINE_WIDTH_PX;

    for (const ripple of this.ripples) {
      const cx = this._px(ripple.x);
      const cy = this._py(ripple.y);
      const r = this._pr(ripple.radius);
      const alpha = ripple.alpha;

      if (alpha < 0.01) continue;

      // Cyan-tinted ripple ring
      ctx.strokeStyle = `rgba(0, 200, 255, ${alpha})`;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.stroke();

      // Second fainter inner ring for depth
      if (ripple.radius > 0.5) {
        const innerR = this._pr(ripple.radius * 0.6);
        ctx.strokeStyle = `rgba(0, 200, 255, ${alpha * 0.4})`;
        ctx.beginPath();
        ctx.arc(cx, cy, innerR, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }
}
