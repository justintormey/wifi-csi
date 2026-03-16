/**
 * Performance Tests
 *
 * Tests:
 * - Rendering completes within time budget with max tracked people
 * - TrackerOverlay handles 4 people with full trails at 60fps budget
 * - NoiseOverlay handles all zones with active clouds and ripples
 * - Payload processing throughput at 10Hz
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { TrackerOverlay } from '../js/tracker-overlay.js';
import { NoiseOverlay } from '../js/noise-overlay.js';
import { Simulator } from '../js/simulator.js';
import { CONFIG } from '../js/config.js';
import { makeHighConfPerson } from './setup.js';

describe('Performance', () => {
  const FRAME_BUDGET_MS = 16.67; // 60fps = 16.67ms per frame

  describe('TrackerOverlay with max people', () => {
    let canvas, overlay;

    beforeEach(() => {
      canvas = document.createElement('canvas');
      canvas.width = 1920;
      canvas.height = 1080;
      overlay = new TrackerOverlay(canvas, CONFIG.floors[0]);
    });

    it('should handle 4 people with full trails without error', () => {
      // Build up trails for 4 people
      for (let frame = 0; frame < 40; frame++) {
        const people = [];
        for (let i = 0; i < 4; i++) {
          people.push({
            ...makeHighConfPerson(`p${i + 1}`),
            x: 3.0 + i * 3.0 + frame * 0.05,
            y: 2.0 + Math.sin(frame * 0.1) * 2,
            position_confidence: 0.5 + Math.random() * 0.4,
          });
        }
        overlay.update(people);
      }

      expect(overlay.people.size).toBe(4);

      // Verify all people have trails
      for (const person of overlay.people.values()) {
        expect(person.trail.length).toBeGreaterThan(0);
      }

      // Draw should complete without errors
      expect(() => overlay._draw()).not.toThrow();
    });

    it('should complete tick+draw cycle within frame budget', () => {
      // Set up 4 people with trails
      for (let frame = 0; frame < 20; frame++) {
        const people = [];
        for (let i = 0; i < 4; i++) {
          people.push({
            ...makeHighConfPerson(`p${i + 1}`),
            x: 3.0 + i * 3.0 + frame * 0.1,
            y: 2.0 + Math.sin(frame * 0.15) * 2,
          });
        }
        overlay.update(people);
      }

      // Benchmark tick + draw
      const start = performance.now();
      const iterations = 100;
      for (let i = 0; i < iterations; i++) {
        overlay._tick(0.016);
        overlay._draw();
      }
      const elapsed = performance.now() - start;
      const avgFrameTime = elapsed / iterations;

      // Mock canvas doesn't have real draw cost, but we verify
      // the logic completes quickly (< 5ms in mocked environment)
      expect(avgFrameTime).toBeLessThan(5);
    });
  });

  describe('NoiseOverlay with worst-case signal quality', () => {
    let canvas, overlay;

    beforeEach(() => {
      canvas = document.createElement('canvas');
      canvas.width = 1920;
      canvas.height = 1080;
      overlay = new NoiseOverlay(canvas, CONFIG.floors[0]);
    });

    it('should handle all zones at minimum quality with max ripples', () => {
      // Set all zones to very poor quality
      const poorQuality = {};
      for (const zone of Object.keys(CONFIG.floors[0].rooms)) {
        poorQuality[zone] = 0.05;
      }
      overlay.update(poorQuality);

      // Force display qualities to match
      for (const zone of Object.keys(overlay.displayQualities)) {
        overlay.displayQualities[zone] = 0.05;
      }

      // Spawn max ripples
      for (let i = 0; i < 50; i++) {
        overlay._spawnRipples();
      }
      expect(overlay.ripples.length).toBeLessThanOrEqual(12);

      // Should render without errors
      expect(() => overlay._draw()).not.toThrow();
    });

    it('should complete tick+draw within frame budget', () => {
      const start = performance.now();
      const iterations = 100;
      for (let i = 0; i < iterations; i++) {
        overlay._tick(0.016);
        overlay._draw();
      }
      const elapsed = performance.now() - start;
      const avgFrameTime = elapsed / iterations;

      expect(avgFrameTime).toBeLessThan(5);
    });
  });

  describe('Simulator throughput', () => {
    it('should generate payloads in < 1ms per tick for 4 people', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'full_house' });

      const start = performance.now();
      const iterations = 1000;
      for (let i = 0; i < iterations; i++) {
        sim.generatePayload();
      }
      const elapsed = performance.now() - start;
      const avgMs = elapsed / iterations;

      expect(avgMs).toBeLessThan(1.0);
    });

    it('should handle rapid mode switches without leaks', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'morning_routine' });

      for (let i = 0; i < 50; i++) {
        sim.reset({ mode: i % 2 === 0 ? 'demo' : 'random', scenario: 'family_evening' });
        sim.generatePayload();
      }

      // Should still work correctly
      const payload = sim.generatePayload();
      expect(payload.people.length).toBeGreaterThan(0);
    });
  });

  describe('combined rendering pipeline', () => {
    it('should handle full frame with all overlays', () => {
      const trackerCanvas = document.createElement('canvas');
      trackerCanvas.width = 1920;
      trackerCanvas.height = 1080;
      const noiseCanvas = document.createElement('canvas');
      noiseCanvas.width = 1920;
      noiseCanvas.height = 1080;

      const tracker = new TrackerOverlay(trackerCanvas, CONFIG.floors[0]);
      const noise = new NoiseOverlay(noiseCanvas, CONFIG.floors[0]);
      const sim = new Simulator({ mode: 'demo', scenario: 'full_house' });

      // Simulate 100 frames of the full pipeline
      const start = performance.now();
      for (let frame = 0; frame < 100; frame++) {
        const payload = sim.generatePayload();
        tracker.update(payload.people);
        noise.update(payload.zone_signal_quality);
        tracker._tick(0.016);
        tracker._draw();
        noise._tick(0.016);
        noise._draw();
      }
      const elapsed = performance.now() - start;
      const avgFrameTime = elapsed / 100;

      // Full pipeline should complete within a generous budget for mocked env
      expect(avgFrameTime).toBeLessThan(10);
    });
  });
});
