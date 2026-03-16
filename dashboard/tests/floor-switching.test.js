/**
 * Floor Switching Tests
 *
 * Tests:
 * - Payload filtering by current floor
 * - TrackerOverlay handles floor changes (people reset)
 * - NoiseOverlay handles floor config swap
 * - Floor config validation
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { TrackerOverlay } from '../js/tracker-overlay.js';
import { NoiseOverlay } from '../js/noise-overlay.js';
import { CONFIG } from '../js/config.js';
import {
  makePayload,
  makeHighConfPerson,
  makeMedConfPerson,
} from './setup.js';

describe('Floor switching', () => {
  describe('payload floor filtering', () => {
    // This replicates the filtering logic from app.js handlePayload()
    // which skips rendering if payload.floor !== appState.currentFloor

    it('should only process payloads matching current floor', () => {
      let currentFloor = 1;
      const rendered = [];

      function handlePayload(payload) {
        if (payload.floor !== currentFloor) return;
        rendered.push(payload);
      }

      // Floor 1 payload → should render
      handlePayload(makePayload({ floor: 1 }));
      expect(rendered).toHaveLength(1);

      // Floor 2 payload → should be skipped
      handlePayload(makePayload({ floor: 2 }));
      expect(rendered).toHaveLength(1);

      // Switch to floor 2
      currentFloor = 2;
      handlePayload(makePayload({ floor: 2 }));
      expect(rendered).toHaveLength(2);
    });
  });

  describe('TrackerOverlay floor transition', () => {
    it('should clear tracked people when creating new overlay for different floor', () => {
      const canvas = document.createElement('canvas');
      canvas.width = 900;
      canvas.height = 525;

      // Create overlay with floor 1 data
      const overlay1 = new TrackerOverlay(canvas, CONFIG.floors[0]);
      overlay1.update([makeHighConfPerson('p1'), makeMedConfPerson('p2')]);
      expect(overlay1.people.size).toBe(2);

      // Simulate floor switch: create new overlay (as the app would)
      const overlay2 = new TrackerOverlay(canvas, CONFIG.floors[0]);
      expect(overlay2.people.size).toBe(0);
    });

    it('should start fresh with no trails after floor switch', () => {
      const canvas = document.createElement('canvas');
      canvas.width = 900;
      canvas.height = 525;

      const overlay = new TrackerOverlay(canvas, CONFIG.floors[0]);

      // Add people and trail data
      for (let i = 0; i < 5; i++) {
        overlay.update([{
          ...makeHighConfPerson('p1'),
          x: 3.5 + i * 0.5,
          y: 2.75,
        }]);
      }
      expect(overlay.people.get('p1').trail.length).toBeGreaterThan(1);

      // Clear (simulating floor switch)
      overlay.people.clear();
      expect(overlay.people.size).toBe(0);
    });
  });

  describe('NoiseOverlay floor config', () => {
    it('should use the correct floor room definitions', () => {
      const canvas = document.createElement('canvas');
      canvas.width = 900;
      canvas.height = 525;

      const overlay = new NoiseOverlay(canvas, CONFIG.floors[0]);
      const zoneNames = Object.keys(overlay.clouds);

      // Should match floor 1 rooms
      expect(zoneNames).toContain('family_room');
      expect(zoneNames).toContain('kitchen');
      expect(zoneNames).toContain('garage');
      expect(zoneNames).toContain('hallway');
      expect(zoneNames).toHaveLength(Object.keys(CONFIG.floors[0].rooms).length);
    });

    it('should use base signal quality for initial display', () => {
      const canvas = document.createElement('canvas');
      canvas.width = 900;
      canvas.height = 525;

      const overlay = new NoiseOverlay(canvas, CONFIG.floors[0]);

      expect(overlay.displayQualities.family_room).toBe(0.85);
      expect(overlay.displayQualities.garage).toBe(0.45);
    });
  });

  describe('floor config validation', () => {
    it('should have all required properties on floor 1', () => {
      const floor = CONFIG.floors[0];
      expect(floor).toBeDefined();
      expect(floor.id).toBe(1);
      expect(floor.name).toBeDefined();
      expect(floor.width).toBeGreaterThan(0);
      expect(floor.height).toBeGreaterThan(0);
      expect(floor.svgPath).toBeDefined();
      expect(floor.rooms).toBeDefined();
      expect(floor.waypoints).toBeDefined();
      expect(floor.baseSignalQuality).toBeDefined();
    });

    it('should have matching rooms and base signal quality keys', () => {
      const floor = CONFIG.floors[0];
      const roomNames = Object.keys(floor.rooms);
      const signalKeys = Object.keys(floor.baseSignalQuality);

      for (const room of roomNames) {
        expect(signalKeys).toContain(room);
      }
    });

    it('should have connected waypoint graph (all connections reference valid waypoints)', () => {
      const floor = CONFIG.floors[0];
      for (const [id, wp] of Object.entries(floor.waypoints)) {
        expect(wp.x).toBeDefined();
        expect(wp.y).toBeDefined();
        expect(wp.connections).toBeInstanceOf(Array);

        for (const connId of wp.connections) {
          expect(floor.waypoints[connId]).toBeDefined();
          // Connection should be bidirectional
          expect(floor.waypoints[connId].connections).toContain(id);
        }
      }
    });

    it('should have waypoints within floor bounds', () => {
      const floor = CONFIG.floors[0];
      for (const [, wp] of Object.entries(floor.waypoints)) {
        expect(wp.x).toBeGreaterThanOrEqual(0);
        expect(wp.x).toBeLessThanOrEqual(floor.width);
        expect(wp.y).toBeGreaterThanOrEqual(0);
        expect(wp.y).toBeLessThanOrEqual(floor.height);
      }
    });
  });
});
