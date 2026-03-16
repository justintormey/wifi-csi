/**
 * Demo Mode & Simulator Tests
 *
 * Tests:
 * - Simulator produces valid payloads in demo and random modes
 * - Demo scenarios (morning_routine, family_evening, full_house) run correctly
 * - Person pathfinding via waypoint graph
 * - Vitals simulation (breathing, heart rate conditions)
 * - Heart rate display gate (4 conditions)
 * - Occupancy uncertainty when people are close together
 * - Speed multiplier works correctly
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { Simulator } from '../js/simulator.js';
import { CONFIG, DEMO_SCENARIOS } from '../js/config.js';

describe('Simulator', () => {
  describe('payload generation', () => {
    it('should generate a valid payload structure', () => {
      const sim = new Simulator({ mode: 'random', personCount: 2 });
      const payload = sim.generatePayload();

      expect(payload).toHaveProperty('timestamp');
      expect(payload).toHaveProperty('floor');
      expect(payload).toHaveProperty('people');
      expect(payload).toHaveProperty('occupancy_estimate');
      expect(payload).toHaveProperty('occupancy_confidence');
      expect(payload).toHaveProperty('zone_signal_quality');
      expect(payload.floor).toBe(1);
      expect(payload.people).toHaveLength(2);
    });

    it('should generate valid person entries', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const payload = sim.generatePayload();
      const person = payload.people[0];

      expect(person).toHaveProperty('id');
      expect(person).toHaveProperty('x');
      expect(person).toHaveProperty('y');
      expect(person).toHaveProperty('position_confidence');
      expect(person).toHaveProperty('uncertainty_radius_m');
      expect(person).toHaveProperty('is_stationary');
      expect(person).toHaveProperty('stationary_duration_s');
      expect(person).toHaveProperty('breathing');
      expect(person).toHaveProperty('heartrate');

      expect(person.breathing).toHaveProperty('rate_bpm');
      expect(person.breathing).toHaveProperty('confidence');
      expect(person.heartrate).toHaveProperty('rate_bpm');
      expect(person.heartrate).toHaveProperty('confidence');
      expect(person.heartrate).toHaveProperty('display');
    });

    it('should have position confidence between 0.15 and 0.98', () => {
      const sim = new Simulator({ mode: 'random', personCount: 4 });
      for (let i = 0; i < 50; i++) {
        const payload = sim.generatePayload();
        for (const person of payload.people) {
          expect(person.position_confidence).toBeGreaterThanOrEqual(0.15);
          expect(person.position_confidence).toBeLessThanOrEqual(0.98);
        }
      }
    });

    it('should have zone signal qualities between 0.1 and 0.98', () => {
      const sim = new Simulator({ mode: 'random' });
      const payload = sim.generatePayload();
      for (const [, quality] of Object.entries(payload.zone_signal_quality)) {
        expect(quality).toBeGreaterThanOrEqual(0.1);
        expect(quality).toBeLessThanOrEqual(0.98);
      }
    });
  });

  describe('demo mode scenarios', () => {
    it('should initialize morning_routine with 1 person', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'morning_routine' });
      expect(sim.people).toHaveLength(1);
      expect(sim.people[0].id).toBe('p1');
    });

    it('should initialize family_evening with 2 people', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'family_evening' });
      expect(sim.people).toHaveLength(2);
      expect(sim.people[0].id).toBe('p1');
      expect(sim.people[1].id).toBe('p2');
    });

    it('should initialize full_house with 4 people', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'full_house' });
      expect(sim.people).toHaveLength(4);
    });

    it('should place people at correct starting waypoints', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'family_evening' });
      const floor = CONFIG.floors[0];

      // p1 starts at kitchen_center
      const kitchenCenter = floor.waypoints.kitchen_center;
      expect(sim.people[0].x).toBe(kitchenCenter.x);
      expect(sim.people[0].y).toBe(kitchenCenter.y);

      // p2 starts at living_center
      const livingCenter = floor.waypoints.living_center;
      expect(sim.people[1].x).toBe(livingCenter.x);
      expect(sim.people[1].y).toBe(livingCenter.y);
    });

    it('should fall back to random mode for unknown scenario', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'nonexistent_scenario' });
      expect(sim.mode).toBe('random');
      expect(sim.people.length).toBeGreaterThan(0);
    });
  });

  describe('random mode', () => {
    it('should create specified number of people', () => {
      for (let count = 1; count <= 4; count++) {
        const sim = new Simulator({ mode: 'random', personCount: count });
        expect(sim.people).toHaveLength(count);
      }
    });

    it('should clamp person count to [1, 4]', () => {
      const simLow = new Simulator({ mode: 'random', personCount: 0 });
      expect(simLow.people.length).toBeGreaterThanOrEqual(1);

      const simHigh = new Simulator({ mode: 'random', personCount: 10 });
      expect(simHigh.people.length).toBeLessThanOrEqual(4);
    });
  });

  describe('heart rate display gate', () => {
    // HR display requires ALL 4 conditions:
    // 1. is_stationary: true
    // 2. stationary_duration_s >= 30
    // 3. heartrate.confidence > 0.15
    // 4. position_confidence > 0.6

    it('should not display heart rate while moving', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const person = sim.people[0];

      // Force moving state
      person.isMoving = true;
      person.stationaryTime = 60;
      person.heartrateConfidence = 0.5;

      const payload = person.toPayload({ living_room: 0.88 });
      expect(payload.heartrate.display).toBe(false);
    });

    it('should not display heart rate when stationary < 30s', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const person = sim.people[0];

      person.isMoving = false;
      person.stationaryTime = 20; // < 30s
      person.heartrateConfidence = 0.5;

      const payload = person.toPayload({ living_room: 0.88 });
      expect(payload.heartrate.display).toBe(false);
    });

    it('should not display heart rate when HR confidence <= 0.15', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const person = sim.people[0];

      person.isMoving = false;
      person.stationaryTime = 60;
      person.heartrateConfidence = 0.10; // <= 0.15

      const payload = person.toPayload({ living_room: 0.88 });
      expect(payload.heartrate.display).toBe(false);
    });

    it('should not display heart rate when position confidence <= 0.6', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const person = sim.people[0];

      person.isMoving = false;
      person.stationaryTime = 60;
      person.heartrateConfidence = 0.5;

      // Place in a low-quality zone (garage: 0.45)
      // This produces position confidence = zone quality ≈ 0.45 + noise
      // Need to ensure posConf <= 0.6 by using a bad zone
      const payload = person.toPayload({ garage: 0.30 });
      // Position confidence will be ~0.30 + noise, likely < 0.6
      expect(payload.position_confidence).toBeLessThanOrEqual(0.6);
      expect(payload.heartrate.display).toBe(false);
    });

    it('should display heart rate when all 4 conditions met', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const person = sim.people[0];

      person.isMoving = false;
      person.stationaryTime = 60;
      person.heartrateConfidence = 0.5;
      // Place in high-quality zone
      person.x = 3.5;
      person.y = 2.75;

      const payload = person.toPayload({ living_room: 0.90 });
      // Position confidence should be ~0.90 + noise > 0.6
      if (payload.position_confidence > 0.6 && payload.heartrate.confidence > 0.15) {
        expect(payload.heartrate.display).toBe(true);
      }
    });
  });

  describe('occupancy estimation', () => {
    it('should estimate correct count for well-separated people', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'full_house' });
      const payload = sim.generatePayload();

      // With 4 people placed at different waypoints, they should be well-separated
      expect(payload.occupancy_estimate).toBeGreaterThanOrEqual(3);
      expect(payload.occupancy_estimate).toBeLessThanOrEqual(5);
    });

    it('should have lower confidence when people are close together', () => {
      const sim = new Simulator({ mode: 'random', personCount: 2 });

      // Force both people to the same position
      sim.people[0].x = 5.0;
      sim.people[0].y = 3.0;
      sim.people[1].x = 5.5;
      sim.people[1].y = 3.0;

      // Generate many payloads and check confidence
      let avgConf = 0;
      const trials = 20;
      for (let i = 0; i < trials; i++) {
        const payload = sim.generatePayload();
        avgConf += payload.occupancy_confidence;
      }
      avgConf /= trials;

      // Should be noticeably reduced (baseline is ~0.9, close people reduce by 0.15)
      expect(avgConf).toBeLessThan(0.85);
    });
  });

  describe('simulation lifecycle', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should emit payloads at ~10Hz when started', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const payloads = [];
      sim.onPayload = (p) => payloads.push(p);

      sim.start();
      vi.advanceTimersByTime(1000); // 1 second = ~10 ticks
      sim.stop();

      expect(payloads.length).toBeGreaterThanOrEqual(9);
      expect(payloads.length).toBeLessThanOrEqual(11);
    });

    it('should not emit payloads when stopped', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const payloads = [];
      sim.onPayload = (p) => payloads.push(p);

      sim.start();
      sim.stop();

      vi.advanceTimersByTime(1000);
      expect(payloads).toHaveLength(0);
    });

    it('should reset correctly', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'morning_routine' });
      sim.start();
      vi.advanceTimersByTime(500);
      sim.reset({ mode: 'random', personCount: 3 });

      expect(sim.mode).toBe('random');
      expect(sim.people).toHaveLength(3);
      expect(sim.running).toBe(false);
    });

    it('should adjust speed multiplier', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      const payloads = [];
      sim.onPayload = (p) => payloads.push(p);

      sim.start();
      sim.setSpeed(2.0); // 2x speed → 20Hz effective → 50ms interval
      vi.advanceTimersByTime(1000);
      sim.stop();

      // At 2x speed, interval is 50ms, so ~20 ticks per second
      expect(payloads.length).toBeGreaterThanOrEqual(15);
    });

    it('should switch modes correctly', () => {
      const sim = new Simulator({ mode: 'demo', scenario: 'morning_routine' });
      expect(sim.mode).toBe('demo');
      expect(sim.people).toHaveLength(1);

      sim.setMode('random');
      expect(sim.mode).toBe('random');
      expect(sim.people.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('vital signs simulation', () => {
    it('should always produce breathing data', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });
      for (let i = 0; i < 20; i++) {
        const payload = sim.generatePayload();
        const person = payload.people[0];
        expect(person.breathing.rate_bpm).toBeGreaterThanOrEqual(12);
        expect(person.breathing.rate_bpm).toBeLessThanOrEqual(20);
        expect(person.breathing.confidence).toBeGreaterThanOrEqual(0.1);
      }
    });

    it('should clamp breathing confidence between 0.1 and 0.95', () => {
      const sim = new Simulator({ mode: 'random', personCount: 1 });

      // Tick many times with forced conditions
      for (let i = 0; i < 100; i++) {
        sim._tick();
        const payload = sim.generatePayload();
        const person = payload.people[0];
        expect(person.breathing.confidence).toBeGreaterThanOrEqual(0.1);
        expect(person.breathing.confidence).toBeLessThanOrEqual(0.95);
      }
    });
  });
});
