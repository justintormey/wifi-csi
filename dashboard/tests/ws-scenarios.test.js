/**
 * WebSocket Scenario Tests — Scripted multi-message integration tests
 *
 * These tests replay pre-recorded WebSocket message sequences through the
 * full pipeline: WebSocketClient → TrackerOverlay + NoiseOverlay.
 *
 * Scenarios cover:
 * - Person walk-through with confidence transitions
 * - Signal quality degradation and recovery
 * - WebSocket disconnect/reconnect with state continuity
 * - Floor switching mid-stream
 * - Demo mode scenario replay
 * - Sustained 10Hz message load (60fps frame budget)
 *
 * HAL-168: Dashboard tests — Mock WebSocket scenarios
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { MockWebSocket, makePayload, makePerson } from './setup.js';
import { WebSocketClient } from '../js/websocket-client.js';
import { TrackerOverlay } from '../js/tracker-overlay.js';
import { NoiseOverlay } from '../js/noise-overlay.js';
import { CONFIG } from '../js/config.js';

// ── Helpers ──────────────────────────────────────────────────────

/** Build a sequence of payloads simulating a person walking across zones */
function walkSequence(personId, waypoints, opts = {}) {
  const {
    stepsPerLeg = 5,
    baseConf = 0.85,
    confNoise = 0.05,
    baseZoneQuality = 0.80,
  } = opts;

  const payloads = [];
  const t0 = Date.now() / 1000;

  for (let leg = 0; leg < waypoints.length - 1; leg++) {
    const [x0, y0] = waypoints[leg];
    const [x1, y1] = waypoints[leg + 1];

    for (let step = 0; step < stepsPerLeg; step++) {
      const frac = step / stepsPerLeg;
      const x = x0 + (x1 - x0) * frac;
      const y = y0 + (y1 - y0) * frac;
      const conf = Math.max(0.15, Math.min(0.98,
        baseConf + (Math.random() - 0.5) * confNoise * 2));

      payloads.push(makePayload({
        timestamp: t0 + payloads.length * 0.1,
        people: [makePerson(personId, {
          x, y,
          position_confidence: conf,
          uncertainty_radius_m: 0.5 + (1 - conf) * 3,
          is_stationary: false,
          stationary_duration_s: 0,
          breathing: { rate_bpm: 18, confidence: 0.45 },
          heartrate: { rate_bpm: 0, confidence: 0, display: false },
        })],
        occupancy_estimate: 1,
      }));
    }
  }

  return payloads;
}

/** Build payloads with degrading then recovering signal quality */
function signalDegradationSequence(steps = 20) {
  const payloads = [];
  const t0 = Date.now() / 1000;

  for (let i = 0; i < steps; i++) {
    const frac = i / (steps - 1);
    // V-shaped: quality drops to 0.1 at midpoint, then recovers
    const quality = frac < 0.5
      ? 0.88 - frac * 2 * 0.78   // 0.88 → 0.10
      : 0.10 + (frac - 0.5) * 2 * 0.78;  // 0.10 → 0.88

    const zoneQuality = {};
    for (const zone of Object.keys(CONFIG.floors[0].rooms)) {
      zoneQuality[zone] = Math.max(0.1, Math.min(0.98, quality + (Math.random() - 0.5) * 0.05));
    }

    payloads.push(makePayload({
      timestamp: t0 + i * 0.1,
      zone_signal_quality: zoneQuality,
      people: [makePerson('p1', {
        x: 3.5, y: 2.75,
        position_confidence: quality,
        uncertainty_radius_m: 0.5 + (1 - quality) * 4,
      })],
    }));
  }

  return payloads;
}

/** Replay an array of payloads through a WebSocket mock */
async function replayPayloads(ws, payloads) {
  for (const payload of payloads) {
    ws.triggerMessage(payload);
  }
}

// ── Pipeline harness: WS → TrackerOverlay + NoiseOverlay ─────────

function createPipeline() {
  const trackerCanvas = document.createElement('canvas');
  trackerCanvas.width = 900;
  trackerCanvas.height = 525;
  const noiseCanvas = document.createElement('canvas');
  noiseCanvas.width = 900;
  noiseCanvas.height = 525;

  const tracker = new TrackerOverlay(trackerCanvas, CONFIG.floors[0]);
  const noise = new NoiseOverlay(noiseCanvas, CONFIG.floors[0]);

  const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
  const receivedPayloads = [];

  client.onPayload = (payload) => {
    receivedPayloads.push(payload);
    tracker.update(payload.people);
    noise.update(payload.zone_signal_quality);
  };

  return { client, tracker, noise, trackerCanvas, noiseCanvas, receivedPayloads };
}

// ── Tests ────────────────────────────────────────────────────────

describe('WebSocket Scenario: Person walk-through with confidence transitions', () => {
  let pipeline;

  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
    pipeline = createPipeline();
  });

  afterEach(() => {
    pipeline.client.disconnect();
    vi.useRealTimers();
  });

  it('should track a person walking across the floor plan', async () => {
    const { client, tracker, receivedPayloads } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);

    const floor = CONFIG.floors[0];
    const payloads = walkSequence('p1', [
      [floor.waypoints.parlor_door.x, floor.waypoints.parlor_door.y],
      [floor.waypoints.hall_mid.x, floor.waypoints.hall_mid.y],
      [floor.waypoints.family_center.x, floor.waypoints.family_center.y],
    ]);

    replayPayloads(MockWebSocket.latest, payloads);

    expect(receivedPayloads).toHaveLength(payloads.length);
    expect(tracker.people.size).toBe(1);

    const person = tracker.people.get('p1');
    expect(person.trail.length).toBeGreaterThan(1);
    // Final target position should be heading toward the last waypoint
    // (walkSequence interpolates between waypoints but doesn't reach the final one)
    const lastPayload = payloads[payloads.length - 1].people[0];
    expect(person.targetX).toBe(lastPayload.x);
    expect(person.targetY).toBe(lastPayload.y);
  });

  it('should transition confidence tiers as person moves through zones', async () => {
    const { client, tracker } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    // High confidence in good zone
    ws.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 3.5, y: 2.75, position_confidence: 0.92 })],
    }));
    expect(tracker.people.get('p1').tier).toBe('high');

    // Walk into medium zone
    ws.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 8.0, y: 4.0, position_confidence: 0.55 })],
    }));

    // Tick until confidence interpolates down
    for (let i = 0; i < 300; i++) tracker.people.get('p1').tick(0.016);
    expect(tracker.people.get('p1').tier).toBe('medium');

    // Walk into poor coverage (garage)
    ws.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 2.0, y: 8.0, position_confidence: 0.25 })],
    }));

    for (let i = 0; i < 300; i++) tracker.people.get('p1').tick(0.016);
    expect(tracker.people.get('p1').tier).toBe('low');

    // Walk back to good zone
    ws.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 3.5, y: 2.75, position_confidence: 0.90 })],
    }));

    for (let i = 0; i < 300; i++) tracker.people.get('p1').tick(0.016);
    expect(tracker.people.get('p1').tier).toBe('high');
  });

  it('should handle multi-person scenarios with independent confidence', async () => {
    const { client, tracker } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    // Two people in different zones
    ws.triggerMessage(makePayload({
      people: [
        makePerson('p1', { x: 3.5, y: 2.75, position_confidence: 0.90 }),
        makePerson('p2', { x: 2.0, y: 8.0, position_confidence: 0.30 }),
      ],
      occupancy_estimate: 2,
    }));

    expect(tracker.people.size).toBe(2);
    expect(tracker.people.get('p1').tier).toBe('high');
    expect(tracker.people.get('p2').tier).toBe('low');

    // p1 degrades, p2 improves
    ws.triggerMessage(makePayload({
      people: [
        makePerson('p1', { x: 3.5, y: 2.75, position_confidence: 0.35 }),
        makePerson('p2', { x: 5.0, y: 3.0, position_confidence: 0.85 }),
      ],
    }));

    for (let i = 0; i < 300; i++) {
      tracker.people.get('p1').tick(0.016);
      tracker.people.get('p2').tick(0.016);
    }

    expect(tracker.people.get('p1').tier).toBe('low');
    expect(tracker.people.get('p2').tier).toBe('high');
  });
});

describe('WebSocket Scenario: Signal quality degradation and recovery', () => {
  let pipeline;

  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
    pipeline = createPipeline();
  });

  afterEach(() => {
    pipeline.client.disconnect();
    vi.useRealTimers();
  });

  it('should track zone quality through V-shaped degradation/recovery', async () => {
    const { client, noise } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);

    const payloads = signalDegradationSequence(20);
    replayPayloads(MockWebSocket.latest, payloads);

    // Target should now be near the recovered value (last payload)
    const lastQuality = payloads[payloads.length - 1].zone_signal_quality.family_room;
    expect(noise.targetQualities.family_room).toBeCloseTo(lastQuality, 1);

    // Tick to let display catch up
    for (let i = 0; i < 200; i++) noise._tick(0.016);

    expect(noise.displayQualities.family_room).toBeCloseTo(lastQuality, 1);
  });

  it('should produce more ripples during low-quality phase', async () => {
    const { client, noise } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    // Send low quality
    ws.triggerMessage(makePayload({
      zone_signal_quality: {
        family_room: 0.10, kitchen: 0.10, dining: 0.10, hallway: 0.10,
        garage: 0.10, utility: 0.10, office: 0.10, parlor: 0.10,
      },
    }));

    // Force display to match
    for (const zone of Object.keys(noise.displayQualities)) {
      noise.displayQualities[zone] = 0.10;
    }

    noise.ripples = [];
    for (let i = 0; i < 50; i++) noise._spawnRipples();
    const lowQualityRipples = noise.ripples.length;

    // Now send high quality
    ws.triggerMessage(makePayload({
      zone_signal_quality: {
        family_room: 0.95, kitchen: 0.95, dining: 0.95, hallway: 0.95,
        garage: 0.95, utility: 0.95, office: 0.95, parlor: 0.95,
      },
    }));

    for (const zone of Object.keys(noise.displayQualities)) {
      noise.displayQualities[zone] = 0.95;
    }

    noise.ripples = [];
    for (let i = 0; i < 50; i++) noise._spawnRipples();
    const highQualityRipples = noise.ripples.length;

    expect(lowQualityRipples).toBeGreaterThan(highQualityRipples);
  });

  it('should render correctly through full degradation sequence', async () => {
    const { client, tracker, noise } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);

    const payloads = signalDegradationSequence(20);

    for (const payload of payloads) {
      MockWebSocket.latest.triggerMessage(payload);
      // Simulate a render frame between each payload
      tracker._tick(0.016);
      tracker._draw();
      noise._tick(0.016);
      noise._draw();
    }

    // Should complete without errors and have valid state
    expect(tracker.people.size).toBe(1);
    expect(() => {
      tracker._draw();
      noise._draw();
    }).not.toThrow();
  });
});

describe('WebSocket Scenario: Disconnect/reconnect with state continuity', () => {
  let pipeline;

  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
    pipeline = createPipeline();
  });

  afterEach(() => {
    pipeline.client.disconnect();
    vi.useRealTimers();
  });

  it('should preserve tracker state across reconnect', async () => {
    const { client, tracker, receivedPayloads } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);

    // Establish tracking data
    const ws1 = MockWebSocket.latest;
    ws1.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 3.5, y: 2.75, position_confidence: 0.90 })],
    }));
    expect(tracker.people.size).toBe(1);
    const payloadsBefore = receivedPayloads.length;

    // Simulate connection drop
    ws1.readyState = MockWebSocket.CLOSED;
    ws1.onclose({ code: 1006, reason: 'Connection lost', wasClean: false });

    expect(client.status).toBe('reconnecting');
    // Tracker state should be preserved (person not removed)
    expect(tracker.people.size).toBe(1);
    expect(tracker.people.get('p1').tier).toBe('high');

    // Reconnect after 1s backoff
    MockWebSocket.nextBehavior = 'open';
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);

    expect(client.status).toBe('connected');

    // Resume sending data — same person, slightly moved
    MockWebSocket.latest.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 4.0, y: 3.0, position_confidence: 0.88 })],
    }));

    expect(receivedPayloads.length).toBe(payloadsBefore + 1);
    expect(tracker.people.get('p1').targetX).toBe(4.0);
  });

  it('should handle data gap gracefully (person stays during reconnect)', async () => {
    const { client, tracker } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);

    // Establish person
    MockWebSocket.latest.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 3.5, y: 2.75 })],
    }));

    // Drop connection
    const ws1 = MockWebSocket.latest;
    ws1.readyState = MockWebSocket.CLOSED;
    ws1.onclose({ code: 1006, reason: '', wasClean: false });

    // Age tracker during reconnect gap (simulate 3s of no updates)
    for (let i = 0; i < 180; i++) {
      tracker.people.get('p1').tick(0.016);
    }

    // Person is ~2.9s old, not yet stale (5s threshold)
    expect(tracker.people.get('p1').isStale).toBe(false);

    // Reconnect
    MockWebSocket.nextBehavior = 'open';
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);

    // Resume with same person — age resets
    MockWebSocket.latest.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 3.5, y: 2.75 })],
    }));

    expect(tracker.people.get('p1').lastUpdateAge).toBe(0);
    expect(tracker.people.get('p1').isStale).toBe(false);
  });

  it('should stale-out a person if reconnect takes too long', async () => {
    const { client, tracker } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);

    // Establish person
    MockWebSocket.latest.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 3.5, y: 2.75 })],
    }));

    // Drop connection
    const ws1 = MockWebSocket.latest;
    ws1.readyState = MockWebSocket.CLOSED;
    ws1.onclose({ code: 1006, reason: '', wasClean: false });

    // Age past staleness threshold (>5s)
    for (let i = 0; i < 350; i++) {
      tracker.people.get('p1').tick(0.016);
    }

    expect(tracker.people.get('p1').isStale).toBe(true);

    // Reconnect with different person — stale p1 should be cleaned up
    MockWebSocket.nextBehavior = 'open';
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);

    MockWebSocket.latest.triggerMessage(makePayload({
      people: [makePerson('p2', { x: 5.0, y: 3.0 })],
    }));

    // p1 was stale and not in payload → removed
    expect(tracker.people.has('p1')).toBe(false);
    expect(tracker.people.has('p2')).toBe(true);
  });

  it('should track status transitions through full disconnect/reconnect cycle', async () => {
    const { client } = pipeline;
    const statuses = [];
    client.onStatusChange = (s) => statuses.push(s);

    client.connect();
    await vi.advanceTimersByTimeAsync(0); // connected

    const ws = MockWebSocket.latest;
    ws.readyState = MockWebSocket.CLOSED;
    ws.onclose({ code: 1006, reason: '', wasClean: false });

    MockWebSocket.nextBehavior = 'open';
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(0);

    expect(statuses).toEqual(['connecting', 'connected', 'reconnecting', 'connected']);
  });
});

describe('WebSocket Scenario: Floor switching mid-stream', () => {
  let pipeline;

  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
    pipeline = createPipeline();
  });

  afterEach(() => {
    pipeline.client.disconnect();
    vi.useRealTimers();
  });

  it('should filter payloads from non-current floor', async () => {
    const { client } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);

    let currentFloor = 1;
    const renderedPayloads = [];

    // Override onPayload with floor filter (like app.js does)
    client.onPayload = (payload) => {
      if (payload.floor === currentFloor) {
        renderedPayloads.push(payload);
      }
    };

    const ws = MockWebSocket.latest;
    ws.triggerMessage(makePayload({ floor: 1 }));
    expect(renderedPayloads).toHaveLength(1);

    ws.triggerMessage(makePayload({ floor: 2 }));
    expect(renderedPayloads).toHaveLength(1); // filtered

    currentFloor = 2;
    ws.triggerMessage(makePayload({ floor: 2 }));
    expect(renderedPayloads).toHaveLength(2);
  });

  it('should clear and rebuild tracker state on floor switch', async () => {
    const { client, tracker, trackerCanvas } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    // Establish people on floor 1
    ws.triggerMessage(makePayload({
      floor: 1,
      people: [
        makePerson('p1', { x: 3.5, y: 2.75 }),
        makePerson('p2', { x: 5.0, y: 3.0 }),
      ],
    }));
    expect(tracker.people.size).toBe(2);

    // Simulate floor switch: create new tracker (as the app would)
    const newTracker = new TrackerOverlay(trackerCanvas, CONFIG.floors[0]);
    expect(newTracker.people.size).toBe(0);

    // New payload on new floor
    newTracker.update([makePerson('p3', { x: 4.0, y: 4.0 })]);
    expect(newTracker.people.size).toBe(1);
    expect(newTracker.people.has('p3')).toBe(true);
  });

  it('should rebuild noise overlay with correct zone config on floor switch', async () => {
    const { client, noiseCanvas } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);

    // Simulate floor switch — new overlay with same floor config
    const newNoise = new NoiseOverlay(noiseCanvas, CONFIG.floors[0]);
    expect(Object.keys(newNoise.clouds)).toHaveLength(Object.keys(CONFIG.floors[0].rooms).length);

    // Quality resets to base
    expect(newNoise.displayQualities.family_room).toBe(0.85);
    expect(newNoise.displayQualities.garage).toBe(0.45);
  });
});

describe('WebSocket Scenario: Demo mode scenario replay', () => {
  let pipeline;

  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
    pipeline = createPipeline();
  });

  afterEach(() => {
    pipeline.client.disconnect();
    vi.useRealTimers();
  });

  it('should produce correct person count per scenario', () => {
    const { client, tracker } = pipeline;

    // morning_routine: 1 person
    client.startSimulator('demo', 'morning_routine');
    vi.advanceTimersByTime(200);

    const payloads = [];
    client.onPayload = (p) => {
      payloads.push(p);
      tracker.update(p.people);
    };

    vi.advanceTimersByTime(500);
    expect(payloads.length).toBeGreaterThan(0);
    expect(payloads[0].people).toHaveLength(1);
    expect(payloads[0]._simulated).toBe(true);
  });

  it('should produce valid vitals data in demo mode', () => {
    const { client } = pipeline;
    const payloads = [];
    client.onPayload = (p) => payloads.push(p);

    client.startSimulator('demo', 'family_evening');
    vi.advanceTimersByTime(2000); // 2s of sim data

    expect(payloads.length).toBeGreaterThan(10);

    for (const payload of payloads) {
      for (const person of payload.people) {
        expect(person.breathing.rate_bpm).toBeGreaterThanOrEqual(12);
        expect(person.breathing.rate_bpm).toBeLessThanOrEqual(20);
        expect(person.position_confidence).toBeGreaterThanOrEqual(0.15);
        expect(person.position_confidence).toBeLessThanOrEqual(0.98);
      }
    }
  });

  it('should seamlessly switch from demo to live WebSocket data', async () => {
    const { client, tracker } = pipeline;
    const payloads = [];
    client.onPayload = (p) => {
      payloads.push(p);
      tracker.update(p.people);
    };

    // Start in demo mode
    client.startSimulator('demo', 'morning_routine');
    vi.advanceTimersByTime(500);

    const simPayloadCount = payloads.length;
    expect(simPayloadCount).toBeGreaterThan(0);
    expect(payloads[payloads.length - 1]._simulated).toBe(true);

    // Switch to live
    MockWebSocket.nextBehavior = 'open';
    client.stopSimulator();
    await vi.advanceTimersByTimeAsync(0);

    expect(client.status).toBe('connected');
    expect(client.isSimulating).toBe(false);

    // Send live payload
    MockWebSocket.latest.triggerMessage(makePayload({
      people: [makePerson('p1', { x: 5.0, y: 3.0, position_confidence: 0.92 })],
    }));

    expect(payloads.length).toBe(simPayloadCount + 1);
    expect(payloads[payloads.length - 1]._simulated).toBeUndefined();
  });

  it('should fall back to simulator and deliver data continuously', async () => {
    const { client } = pipeline;
    const payloads = [];
    client.onPayload = (p) => payloads.push(p);

    // Enable auto-fallback
    const autoClient = new WebSocketClient({
      url: 'ws://test:8080',
      autoFallback: true,
    });
    autoClient.onPayload = (p) => payloads.push(p);

    MockWebSocket.nextBehavior = 'error';
    autoClient.connect();
    await vi.advanceTimersByTimeAsync(0);

    MockWebSocket.nextBehavior = 'hang';
    await vi.advanceTimersByTimeAsync(3000); // trigger fallback

    expect(autoClient.status).toBe('simulator');

    // Should be receiving simulated data
    await vi.advanceTimersByTimeAsync(1000);
    expect(payloads.length).toBeGreaterThan(5);

    autoClient.disconnect();
  });
});

describe('WebSocket Scenario: Sustained 10Hz load and frame budget', () => {
  let pipeline;

  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
    pipeline = createPipeline();
  });

  afterEach(() => {
    pipeline.client.disconnect();
    vi.useRealTimers();
  });

  it('should handle 10 seconds of continuous 10Hz data without errors', async () => {
    const { client, tracker, noise } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    const t0 = Date.now() / 1000;

    // 100 payloads at 10Hz = 10 seconds of data
    for (let i = 0; i < 100; i++) {
      const angle = (i / 100) * Math.PI * 2;
      ws.triggerMessage(makePayload({
        timestamp: t0 + i * 0.1,
        people: [
          makePerson('p1', {
            x: 5.0 + Math.cos(angle) * 2,
            y: 3.5 + Math.sin(angle) * 1.5,
            position_confidence: 0.5 + Math.sin(angle * 2) * 0.35,
            uncertainty_radius_m: 0.5 + (1 - (0.5 + Math.sin(angle * 2) * 0.35)) * 3,
            is_stationary: false,
          }),
          makePerson('p2', {
            x: 3.0 + Math.sin(angle * 0.7) * 1.5,
            y: 4.0 + Math.cos(angle * 0.7) * 1.0,
            position_confidence: 0.75,
          }),
        ],
        occupancy_estimate: 2,
        zone_signal_quality: {
          family_room: 0.80 + Math.sin(angle) * 0.1,
          kitchen: 0.75 + Math.cos(angle) * 0.1,
          dining: 0.70, hallway: 0.55, garage: 0.40,
          utility: 0.55, office: 0.45, parlor: 0.50,
        },
      }));

      // Simulate render frame interleaved with payloads
      tracker._tick(0.016);
      tracker._draw();
      noise._tick(0.016);
      noise._draw();
    }

    expect(tracker.people.size).toBe(2);
    expect(tracker.people.get('p1').trail.length).toBeGreaterThan(5);
    expect(() => {
      tracker._draw();
      noise._draw();
    }).not.toThrow();
  });

  it('should complete full pipeline frame within performance budget', async () => {
    const { client, tracker, noise } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    // Warm up with some data
    for (let i = 0; i < 20; i++) {
      ws.triggerMessage(makePayload({
        people: [
          makePerson('p1', { x: 3.0 + i * 0.2, y: 2.5 }),
          makePerson('p2', { x: 6.0 + i * 0.1, y: 4.0 }),
          makePerson('p3', { x: 2.0, y: 5.0 + i * 0.15 }),
          makePerson('p4', { x: 8.0 - i * 0.1, y: 2.0 }),
        ],
      }));
    }

    // Benchmark
    vi.useRealTimers();
    const start = performance.now();
    const iterations = 200;
    for (let i = 0; i < iterations; i++) {
      tracker._tick(0.016);
      tracker._draw();
      noise._tick(0.016);
      noise._draw();
    }
    const elapsed = performance.now() - start;
    const avgFrameTime = elapsed / iterations;

    // With mock canvas, logic should complete in < 5ms per frame
    expect(avgFrameTime).toBeLessThan(5);
    vi.useFakeTimers(); // restore for afterEach
  });

  it('should handle rapid person add/remove churn', async () => {
    const { client, tracker } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    // Simulate people appearing and disappearing rapidly
    for (let i = 0; i < 30; i++) {
      const people = [];
      // Add 1-4 people based on cycle
      const count = (i % 4) + 1;
      for (let j = 0; j < count; j++) {
        people.push(makePerson(`p${j + 1}`, {
          x: 3.0 + j * 2.0,
          y: 3.0 + Math.random() * 2,
        }));
      }
      ws.triggerMessage(makePayload({ people, occupancy_estimate: count }));

      // Tick to age non-updated people
      for (const person of tracker.people.values()) {
        person.tick(0.1); // 100ms
      }
    }

    // All current people should still be tracked
    expect(tracker.people.size).toBeGreaterThanOrEqual(1);
    expect(tracker.people.size).toBeLessThanOrEqual(4);
  });
});

describe('WebSocket Scenario: Noise overlay responds to live signal quality', () => {
  let pipeline;

  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
    pipeline = createPipeline();
  });

  afterEach(() => {
    pipeline.client.disconnect();
    vi.useRealTimers();
  });

  it('should update zone qualities from WebSocket messages', async () => {
    const { client, noise } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    ws.triggerMessage(makePayload({
      zone_signal_quality: {
        family_room: 0.30, kitchen: 0.95, dining: 0.75,
        hallway: 0.60, garage: 0.10, utility: 0.55,
        office: 0.50, parlor: 0.48,
      },
    }));

    expect(noise.targetQualities.family_room).toBe(0.30);
    expect(noise.targetQualities.kitchen).toBe(0.95);
    expect(noise.targetQualities.garage).toBe(0.10);
  });

  it('should smoothly animate quality changes over multiple messages', async () => {
    const { client, noise } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    // Send high quality
    ws.triggerMessage(makePayload({
      zone_signal_quality: { ...makePayload().zone_signal_quality, garage: 0.90 },
    }));

    // Force display to match
    noise.displayQualities.garage = 0.90;

    // Send low quality
    ws.triggerMessage(makePayload({
      zone_signal_quality: { ...makePayload().zone_signal_quality, garage: 0.15 },
    }));

    // Display should lag behind target
    expect(noise.targetQualities.garage).toBe(0.15);
    expect(noise.displayQualities.garage).toBe(0.90); // hasn't caught up yet

    // Tick to let it converge
    for (let i = 0; i < 200; i++) noise._tick(0.016);
    expect(noise.displayQualities.garage).toBeCloseTo(0.15, 1);
  });

  it('should render without errors through quality oscillation', async () => {
    const { client, noise } = pipeline;
    client.connect();
    await vi.advanceTimersByTimeAsync(0);
    const ws = MockWebSocket.latest;

    // Oscillate quality rapidly
    for (let i = 0; i < 20; i++) {
      const quality = i % 2 === 0 ? 0.10 : 0.95;
      ws.triggerMessage(makePayload({
        zone_signal_quality: Object.fromEntries(
          Object.keys(CONFIG.floors[0].rooms).map(z => [z, quality])
        ),
      }));
      noise._tick(0.016);
      noise._draw();
    }

    expect(() => noise._draw()).not.toThrow();
  });
});
