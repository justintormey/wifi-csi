/**
 * Noise Overlay Tests
 *
 * Tests signal quality visualization:
 * - Zone dimming proportional to (1-quality)
 * - Cloud particles stay within zone bounds
 * - Ripple spawn rate proportional to (1-quality)²
 * - Quality smoothing (lerp toward target)
 * - Rendering at various signal quality levels
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { NoiseOverlay } from '../js/noise-overlay.js';
import { CONFIG } from '../js/config.js';

describe('NoiseOverlay', () => {
  let canvas, overlay;

  beforeEach(() => {
    canvas = document.createElement('canvas');
    canvas.width = 900;
    canvas.height = 525;
    overlay = new NoiseOverlay(canvas, CONFIG.floors[0]);
  });

  describe('initialization', () => {
    it('should initialize with base signal qualities from config', () => {
      expect(overlay.targetQualities.family_room).toBe(0.85);
      expect(overlay.targetQualities.garage).toBe(0.45);
      expect(overlay.displayQualities.family_room).toBe(0.85);
    });

    it('should create cloud particles for each zone', () => {
      const zoneNames = Object.keys(CONFIG.floors[0].rooms);
      for (const zone of zoneNames) {
        expect(overlay.clouds[zone]).toBeDefined();
        expect(overlay.clouds[zone]).toHaveLength(3); // CLOUD_COUNT_PER_ZONE = 3
      }
    });

    it('should initialize coordinate transforms', () => {
      expect(overlay.scaleX).toBe(canvas.width / CONFIG.floors[0].width);
      expect(overlay.scaleY).toBe(canvas.height / CONFIG.floors[0].height);
    });
  });

  describe('quality updates', () => {
    it('should update target qualities from payload data', () => {
      overlay.update({
        family_room: 0.30,
        kitchen: 0.95,
        garage: 0.10,
      });

      expect(overlay.targetQualities.family_room).toBe(0.30);
      expect(overlay.targetQualities.kitchen).toBe(0.95);
      expect(overlay.targetQualities.garage).toBe(0.10);
    });

    it('should ignore unknown zone names', () => {
      const beforeKeys = Object.keys(overlay.targetQualities);
      overlay.update({ nonexistent_room: 0.5 });
      expect(Object.keys(overlay.targetQualities)).toEqual(beforeKeys);
    });

    it('should handle null/undefined input gracefully', () => {
      expect(() => overlay.update(null)).not.toThrow();
      expect(() => overlay.update(undefined)).not.toThrow();
    });

    it('should smooth display qualities toward targets over time', () => {
      overlay.update({ family_room: 0.20 }); // big drop from 0.85

      // Display should still be near original
      expect(overlay.displayQualities.family_room).toBe(0.85);

      // Tick several times
      for (let i = 0; i < 100; i++) {
        overlay._tick(0.016);
      }

      // Should be close to target now
      expect(overlay.displayQualities.family_room).toBeCloseTo(0.20, 1);
    });
  });

  describe('cloud particle behavior', () => {
    it('should keep clouds within zone bounds after many ticks', () => {
      // Tick for a long time to test boundary bouncing
      for (let i = 0; i < 1000; i++) {
        overlay._tick(0.05);
      }

      for (const [name, clouds] of Object.entries(overlay.clouds)) {
        const zone = CONFIG.floors[0].rooms[name];
        for (const cloud of clouds) {
          // Cloud center should be within zone (with some radius margin)
          expect(cloud.x).toBeGreaterThanOrEqual(zone.x - cloud.radius);
          expect(cloud.x).toBeLessThanOrEqual(zone.x + zone.w + cloud.radius);
          expect(cloud.y).toBeGreaterThanOrEqual(zone.y - cloud.radius);
          expect(cloud.y).toBeLessThanOrEqual(zone.y + zone.h + cloud.radius);
        }
      }
    });
  });

  describe('ripple spawning', () => {
    it('should spawn ripples more frequently in low-quality zones', () => {
      // Set garage to very poor quality, family room to very good
      overlay.update({ garage: 0.10, family_room: 0.95 });
      for (let i = 0; i < 50; i++) overlay._tick(0.016);

      // Spawn ripples many times and count how many are near garage vs family room
      let garageRipples = 0;
      let familyRipples = 0;
      const garageZone = CONFIG.floors[0].rooms.garage;
      const familyZone = CONFIG.floors[0].rooms.family_room;

      for (let i = 0; i < 200; i++) {
        overlay._spawnRipples();
      }

      for (const ripple of overlay.ripples) {
        if (ripple.x >= garageZone.x && ripple.x <= garageZone.x + garageZone.w &&
            ripple.y >= garageZone.y && ripple.y <= garageZone.y + garageZone.h) {
          garageRipples++;
        }
        if (ripple.x >= familyZone.x && ripple.x <= familyZone.x + familyZone.w &&
            ripple.y >= familyZone.y && ripple.y <= familyZone.y + familyZone.h) {
          familyRipples++;
        }
      }

      // Garage (quality ~0.10) should have far more ripples than family room (quality ~0.95)
      expect(garageRipples).toBeGreaterThan(familyRipples);
    });

    it('should cap ripple count at RIPPLE_MAX_COUNT (12)', () => {
      overlay.update({
        garage: 0.05, family_room: 0.05, kitchen: 0.05, hallway: 0.05,
        dining: 0.05, utility: 0.05, office: 0.05, parlor: 0.05,
      });

      // Spam spawn ripples
      for (let i = 0; i < 100; i++) {
        overlay._spawnRipples();
      }

      expect(overlay.ripples.length).toBeLessThanOrEqual(12);
    });

    it('should remove expired ripples', () => {
      overlay.update({ garage: 0.05 });
      for (let i = 0; i < 50; i++) overlay._spawnRipples();

      const initialCount = overlay.ripples.length;
      expect(initialCount).toBeGreaterThan(0);

      // Tick until all ripples expire (max radius / expand speed = 3.0/1.5 = 2s)
      for (let i = 0; i < 200; i++) {
        overlay._tick(0.016);
      }

      expect(overlay.ripples.length).toBeLessThan(initialCount);
    });

    it('should have ripple alpha that fades with expansion', () => {
      overlay.update({ garage: 0.05 });
      overlay._spawnRipples();

      if (overlay.ripples.length > 0) {
        const ripple = overlay.ripples[0];
        const initialAlpha = ripple.alpha;

        // Expand
        ripple.tick(1.0);
        expect(ripple.alpha).toBeLessThan(initialAlpha);
      }
    });
  });

  describe('rendering at various quality levels', () => {
    it('should render without errors at all quality levels', () => {
      const levels = [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0];
      for (const q of levels) {
        overlay.update({
          garage: q, family_room: q, kitchen: q, hallway: q,
          dining: q, utility: q, office: q, parlor: q,
        });

        // Force display to match target
        for (const zone of Object.keys(overlay.displayQualities)) {
          overlay.displayQualities[zone] = q;
        }

        expect(() => overlay._draw()).not.toThrow();
      }
    });

    it('should produce minimal draw calls for high-quality zones', () => {
      // All zones at 0.98 — almost no dimming, clouds, or ripples
      for (const zone of Object.keys(overlay.displayQualities)) {
        overlay.displayQualities[zone] = 0.98;
      }
      overlay.ripples = [];

      const ctx = canvas._mockCtx;
      ctx.reset();
      overlay._draw();

      // Should have clearRect + minimal dimming rectangles (alpha < 0.01 → skipped)
      const fillRects = ctx.calls.filter(c => c[0] === 'fillRect');
      // High quality zones have (1-0.98)*0.45 = 0.009 alpha → below 0.01 threshold → skipped
      expect(fillRects).toHaveLength(0);
    });

    it('should produce dimming draw calls for low-quality zones', () => {
      // All zones at 0.20 — heavy dimming
      for (const zone of Object.keys(overlay.displayQualities)) {
        overlay.displayQualities[zone] = 0.20;
      }
      overlay.ripples = [];

      const ctx = canvas._mockCtx;
      ctx.reset();
      overlay._draw();

      const fillRects = ctx.calls.filter(c => c[0] === 'fillRect');
      // Each zone should get a dimming rect
      expect(fillRects.length).toBe(Object.keys(CONFIG.floors[0].rooms).length);
    });
  });

  describe('animation lifecycle', () => {
    it('should start and stop cleanly', () => {
      expect(overlay.running).toBe(false);

      overlay.start();
      expect(overlay.running).toBe(true);

      overlay.stop();
      expect(overlay.running).toBe(false);
      expect(overlay.rafId).toBeNull();
    });

    it('should not start twice', () => {
      overlay.start();
      const firstRafId = overlay.rafId;
      overlay.start(); // should be no-op
      expect(overlay.rafId).toBe(firstRafId);
      overlay.stop();
    });
  });
});
