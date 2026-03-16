/**
 * Multiple Simultaneous Confidence Level Tests
 *
 * Tests that the system correctly handles multiple people with different
 * confidence levels at the same time, ensuring:
 * - Each person's confidence tier is independent
 * - Mixed tiers render correctly together
 * - Confidence changes for one person don't affect others
 * - All visual encodings are applied per-person
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { TrackerOverlay } from '../js/tracker-overlay.js';
import { CONFIG } from '../js/config.js';
import {
  makeHighConfPerson,
  makeMedConfPerson,
  makeLowConfPerson,
  makeMovingPerson,
} from './setup.js';

describe('Multiple simultaneous confidence levels', () => {
  let canvas, overlay;

  beforeEach(() => {
    canvas = document.createElement('canvas');
    canvas.width = 900;
    canvas.height = 525;
    overlay = new TrackerOverlay(canvas, CONFIG.floors[0]);
  });

  it('should track people at all 3 tiers simultaneously', () => {
    overlay.update([
      makeHighConfPerson('p1'),  // 0.90 → high
      makeMedConfPerson('p2'),   // 0.60 → medium
      makeLowConfPerson('p3'),   // 0.30 → low
    ]);

    expect(overlay.people.size).toBe(3);
    expect(overlay.people.get('p1').tier).toBe('high');
    expect(overlay.people.get('p2').tier).toBe('medium');
    expect(overlay.people.get('p3').tier).toBe('low');
  });

  it('should maintain independent confidence for each person', () => {
    overlay.update([
      makeHighConfPerson('p1'),
      makeMedConfPerson('p2'),
    ]);

    // Change p1 to low confidence — p2 should be unaffected
    overlay.update([
      { ...makeLowConfPerson('p1'), position_confidence: 0.20 },
      makeMedConfPerson('p2'),
    ]);

    const p1 = overlay.people.get('p1');
    const p2 = overlay.people.get('p2');

    expect(p1.targetConf).toBe(0.20);
    expect(p2.targetConf).toBe(0.60); // unchanged
  });

  it('should render all 3 tiers together without errors', () => {
    overlay.update([
      makeHighConfPerson('p1'),
      makeMedConfPerson('p2'),
      makeLowConfPerson('p3'),
    ]);

    // Draw should not throw
    expect(() => overlay._draw()).not.toThrow();

    // Should have draw calls for all people
    const ctx = canvas._mockCtx;
    expect(ctx.calls.length).toBeGreaterThan(0);
  });

  it('should render 4 people at all different confidence levels', () => {
    overlay.update([
      { ...makeHighConfPerson('p1'), position_confidence: 0.95 },
      { ...makeMedConfPerson('p2'), position_confidence: 0.65 },
      { ...makeMedConfPerson('p3'), position_confidence: 0.45 },
      { ...makeLowConfPerson('p4'), position_confidence: 0.20 },
    ]);

    expect(overlay.people.get('p1').tier).toBe('high');
    expect(overlay.people.get('p2').tier).toBe('medium');
    expect(overlay.people.get('p3').tier).toBe('medium');
    expect(overlay.people.get('p4').tier).toBe('low');

    expect(() => overlay._draw()).not.toThrow();
  });

  it('should handle simultaneous tier transitions', () => {
    // Start: p1=high, p2=medium, p3=low
    overlay.update([
      makeHighConfPerson('p1'),
      makeMedConfPerson('p2'),
      makeLowConfPerson('p3'),
    ]);

    // Swap: p1→low, p2→high, p3→medium
    overlay.update([
      { ...makeLowConfPerson('p1'), position_confidence: 0.20 },
      { ...makeHighConfPerson('p2'), position_confidence: 0.95 },
      { ...makeMedConfPerson('p3'), position_confidence: 0.60 },
    ]);

    // Targets should update immediately
    expect(overlay.people.get('p1').targetConf).toBe(0.20);
    expect(overlay.people.get('p2').targetConf).toBe(0.95);
    expect(overlay.people.get('p3').targetConf).toBe(0.60);

    // After sufficient interpolation, tiers should match new targets
    for (let i = 0; i < 300; i++) {
      for (const person of overlay.people.values()) {
        person.tick(0.016);
      }
    }

    expect(overlay.people.get('p1').tier).toBe('low');
    expect(overlay.people.get('p2').tier).toBe('high');
    expect(overlay.people.get('p3').tier).toBe('medium');
  });

  it('should handle person appearing and disappearing from the mix', () => {
    // Start with 2 people
    overlay.update([
      makeHighConfPerson('p1'),
      makeLowConfPerson('p2'),
    ]);
    expect(overlay.people.size).toBe(2);

    // Add a third
    overlay.update([
      makeHighConfPerson('p1'),
      makeLowConfPerson('p2'),
      makeMedConfPerson('p3'),
    ]);
    expect(overlay.people.size).toBe(3);

    // Remove p2 (but it's not stale yet, so it lingers)
    overlay.update([
      makeHighConfPerson('p1'),
      makeMedConfPerson('p3'),
    ]);

    // p2 still exists (not stale)
    expect(overlay.people.has('p2')).toBe(true);

    // Age p2 past staleness
    const p2 = overlay.people.get('p2');
    for (let i = 0; i < 350; i++) p2.tick(0.016);

    // Now update without p2 — it should be removed
    overlay.update([
      makeHighConfPerson('p1'),
      makeMedConfPerson('p3'),
    ]);
    expect(overlay.people.has('p2')).toBe(false);
    expect(overlay.people.size).toBe(2);
  });

  it('should handle rapid oscillation between confidence levels', () => {
    // Simulate jittery confidence values
    for (let i = 0; i < 50; i++) {
      const conf = i % 2 === 0 ? 0.90 : 0.30;
      overlay.update([
        { ...makeHighConfPerson('p1'), position_confidence: conf },
        { ...makeMedConfPerson('p2'), position_confidence: 0.60 }, // stable
      ]);
    }

    // p1's display should be somewhere between extremes due to smoothing
    const p1 = overlay.people.get('p1');
    // Target should be 0.30 (last update was odd index)
    expect(p1.targetConf).toBe(0.30);
    // Display should still be interpolating (not yet converged)
    // Since we haven't ticked, display is whatever it was set to

    // p2 should be stable
    const p2 = overlay.people.get('p2');
    expect(p2.targetConf).toBe(0.60);
  });

  it('should produce correct label text per tier', () => {
    overlay.update([
      makeHighConfPerson('p1'),   // high → "p1" label
      makeMedConfPerson('p2'),    // medium → "~p2" label
      makeLowConfPerson('p3'),    // low → "?" label
    ]);

    // Verify tier determines label behavior
    expect(overlay.people.get('p1').tier).toBe('high');
    expect(overlay.people.get('p2').tier).toBe('medium');
    expect(overlay.people.get('p3').tier).toBe('low');

    // The label text is drawn by _drawLabel:
    // high → person.id, medium → ~person.id, low → "?"
    // This is verified via the canvas mock calls
    const ctx = canvas._mockCtx;
    ctx.reset();
    overlay._draw();

    // Look for fillText calls
    const textCalls = ctx.calls.filter(c => c[0] === 'fillText');
    const texts = textCalls.map(c => c[1]);

    // Should include labels for all 3 people (each has 2 fillText calls: shadow + text)
    expect(texts).toContain('p1');     // high confidence label
    expect(texts).toContain('~p2');    // medium confidence label
    expect(texts).toContain('?');      // low confidence label
  });
});
