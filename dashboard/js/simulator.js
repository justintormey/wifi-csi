/**
 * WiFi CSI Simulator — Core Engine
 *
 * Generates realistic people tracking and vital signs data at 10Hz,
 * outputting WebSocket-compatible JSON payloads matching the schema in PLAN.md.
 *
 * Modes:
 *   - Demo mode: runs scripted scenarios (morning_routine, family_evening, full_house)
 *   - Random mode: generates random movement patterns for 1-4 people
 *
 * Usage:
 *   import { Simulator } from './simulator.js';
 *   const sim = new Simulator({ mode: 'demo', scenario: 'morning_routine' });
 *   sim.onPayload = (payload) => { ... };
 *   sim.start();
 */

import { CONFIG, DEMO_SCENARIOS } from './config.js';

// ── Constants ──────────────────────────────────────────────────

const WALK_SPEED_MS = 1.2;          // meters per second
const TICK_INTERVAL_MS = 100;       // 10Hz base rate
const BREATHING_MIN = 12;
const BREATHING_MAX = 20;
const HEARTRATE_MIN = 58;
const HEARTRATE_MAX = 100;
const STATIONARY_THRESHOLD_S = 30;  // seconds before heartrate becomes available
const HR_SNR_THRESHOLD = 0.6;       // minimum zone signal quality for HR display
const POSITION_CONFIDENCE_MIN = 0.15;

// Activity-specific breathing rate ranges (bpm)
const BREATHING_RANGES = {
  sleeping:  { min: 12, max: 14 },
  sitting:   { min: 14, max: 17 },
  standing:  { min: 15, max: 18 },
  walking:   { min: 16, max: 20 },
};

// Activity-specific heartrate ranges (bpm)
const HEARTRATE_RANGES = {
  sleeping:  { min: 58, max: 68 },
  sitting:   { min: 62, max: 78 },
  standing:  { min: 70, max: 85 },
};

// ── Utilities ──────────────────────────────────────────────────

/** Gaussian random (Box-Muller) with mean and stddev */
function gaussRandom(mean = 0, std = 1) {
  const u1 = Math.random();
  const u2 = Math.random();
  return mean + std * Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

/** Clamp value to [min, max] */
function clamp(val, min, max) {
  return Math.max(min, Math.min(max, val));
}

/** Linear interpolation */
function lerp(a, b, t) {
  return a + (b - a) * t;
}

/** Distance between two points */
function dist(a, b) {
  return Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);
}

/**
 * Smooth random drift using Ornstein-Uhlenbeck process.
 * This is the core stochastic model for all continuous values in the simulator
 * (breathing rate, heart rate, signal quality). It produces mean-reverting
 * random walks — values drift naturally but always pull back toward a target,
 * avoiding both jarring jumps (pure random) and artificial smoothness (pure lerp).
 * theta = mean-reversion speed, sigma = volatility/noise amplitude.
 */
function ouStep(current, mean, theta, sigma, dt) {
  return current + theta * (mean - current) * dt + sigma * Math.sqrt(dt) * gaussRandom();
}

/** Pick a random element from an array */
function pickRandom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

/** Pick a random float in [min, max] */
function randRange(min, max) {
  return min + Math.random() * (max - min);
}

// ── Pathfinding (BFS on waypoint graph) ────────────────────────
// BFS guarantees shortest path (fewest waypoints) on the unweighted graph.
// This produces realistic room-to-room navigation through doorways rather
// than straight-line teleportation. The waypoint graph must be fully
// connected — disconnected subgraphs will cause the fallback direct path
// which looks like teleportation.

function findPath(waypoints, fromId, toId) {
  if (fromId === toId) return [fromId];

  const queue = [[fromId]];
  const visited = new Set([fromId]);

  while (queue.length > 0) {
    const path = queue.shift();
    const current = path[path.length - 1];
    const wp = waypoints[current];
    if (!wp) continue;

    for (const neighbor of wp.connections) {
      if (neighbor === toId) return [...path, neighbor];
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        queue.push([...path, neighbor]);
      }
    }
  }

  // Fallback: direct path (shouldn't happen with connected graph)
  return [fromId, toId];
}

// ── Room lookup ────────────────────────────────────────────────

function findRoomForPoint(rooms, x, y) {
  for (const [name, r] of Object.entries(rooms)) {
    if (x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) {
      return name;
    }
  }
  return 'hallway'; // default if between rooms
}

// ── Person State Machine ───────────────────────────────────────

class SimulatedPerson {
  constructor(id, startWaypoint, floor) {
    this.id = id;
    this.floor = floor;
    const wp = floor.waypoints[startWaypoint];
    this.x = wp ? wp.x : 5;
    this.y = wp ? wp.y : 5;
    this.currentWaypoint = startWaypoint;

    // Movement state
    this.activity = 'standing';  // walking, sitting, standing, sleeping
    this.path = [];              // waypoint IDs for current route
    this.pathIndex = 0;
    this.segmentProgress = 0;    // 0-1 interpolation along current segment
    this.segmentFrom = { x: this.x, y: this.y };
    this.segmentTo = { x: this.x, y: this.y };
    this.segmentLength = 0;

    // Timing
    this.stationaryTime = 0;     // seconds stationary
    this.idleRemaining = 0;      // seconds of idle to burn through
    this.isMoving = false;

    // Vitals (smoothed values)
    this.breathingRate = randRange(BREATHING_RANGES.standing.min, BREATHING_RANGES.standing.max);
    this.heartRate = randRange(HEARTRATE_RANGES.standing.min, HEARTRATE_RANGES.standing.max);
    this.breathingConfidence = 0.7;
    this.heartrateConfidence = 0.0;

    // Action queue (for demo mode)
    this.actionQueue = [];
    this.currentAction = null;
  }

  /** Set a scripted action queue (demo mode) */
  setActions(actions) {
    this.actionQueue = [...actions];
    this.currentAction = null;
    this._advanceAction();
  }

  /** Start random behavior (random mode) */
  startRandom() {
    this._pickRandomAction();
  }

  _advanceAction() {
    if (this.actionQueue.length === 0) {
      // Loop: restart the scenario
      if (this._originalActions) {
        this.actionQueue = [...this._originalActions];
      } else {
        this._pickRandomAction();
        return;
      }
    }

    this.currentAction = this.actionQueue.shift();

    if (this.currentAction.type === 'idle') {
      this.activity = this.currentAction.activity || 'standing';
      this.idleRemaining = this.currentAction.duration;
      this.isMoving = false;
    } else if (this.currentAction.type === 'move') {
      this._navigateTo(this.currentAction.to);
    }
  }

  _pickRandomAction() {
    // Random behavior: idle for a while, then walk to a random waypoint
    const waypointIds = Object.keys(this.floor.waypoints);
    const activities = ['sitting', 'standing', 'sleeping'];

    if (Math.random() < 0.6 || this.isMoving) {
      // Idle
      this.activity = pickRandom(activities);
      this.idleRemaining = randRange(5, 40);
      this.isMoving = false;
      this.currentAction = { type: 'idle' };
    } else {
      // Move to random waypoint
      const target = pickRandom(waypointIds.filter(id => id !== this.currentWaypoint));
      this._navigateTo(target);
    }
  }

  _navigateTo(targetWaypointId) {
    this.path = findPath(this.floor.waypoints, this.currentWaypoint, targetWaypointId);
    this.pathIndex = 0;
    this.isMoving = true;
    this.activity = 'walking';
    this.stationaryTime = 0;
    this._startNextSegment();
  }

  _startNextSegment() {
    if (this.pathIndex >= this.path.length - 1) {
      // Arrived at destination
      this.currentWaypoint = this.path[this.path.length - 1];
      const wp = this.floor.waypoints[this.currentWaypoint];
      if (wp) { this.x = wp.x; this.y = wp.y; }
      this.isMoving = false;
      this.activity = 'standing';
      this.path = [];
      this._advanceAction();
      return;
    }

    const fromId = this.path[this.pathIndex];
    const toId = this.path[this.pathIndex + 1];
    const fromWp = this.floor.waypoints[fromId];
    const toWp = this.floor.waypoints[toId];

    this.segmentFrom = { x: fromWp.x, y: fromWp.y };
    this.segmentTo = { x: toWp.x, y: toWp.y };
    this.segmentLength = dist(this.segmentFrom, this.segmentTo);
    this.segmentProgress = 0;
  }

  /** Advance simulation by dt seconds */
  tick(dt) {
    if (this.isMoving) {
      this._tickMovement(dt);
    } else {
      this._tickIdle(dt);
    }
    this._tickVitals(dt);
  }

  _tickMovement(dt) {
    if (this.segmentLength <= 0) {
      this.pathIndex++;
      this._startNextSegment();
      return;
    }

    const progressDelta = (WALK_SPEED_MS * dt) / this.segmentLength;
    this.segmentProgress += progressDelta;

    if (this.segmentProgress >= 1.0) {
      // Reached next waypoint
      this.x = this.segmentTo.x;
      this.y = this.segmentTo.y;
      this.pathIndex++;
      this._startNextSegment();
    } else {
      // Add slight random wobble to simulate tracking noise
      const wobble = 0.05;
      this.x = lerp(this.segmentFrom.x, this.segmentTo.x, this.segmentProgress) + gaussRandom(0, wobble);
      this.y = lerp(this.segmentFrom.y, this.segmentTo.y, this.segmentProgress) + gaussRandom(0, wobble);
    }

    this.stationaryTime = 0;
  }

  _tickIdle(dt) {
    this.stationaryTime += dt;
    this.idleRemaining -= dt;

    // Add tiny position jitter (CSI noise)
    this.x += gaussRandom(0, 0.02);
    this.y += gaussRandom(0, 0.02);

    if (this.idleRemaining <= 0 && !this.isMoving) {
      this._advanceAction();
    }
  }

  // Vitals simulation mirrors the real system's constraints:
  // - Breathing is always detectable (large chest displacement ~1-5mm)
  // - Heart rate requires stillness + good SNR (tiny displacement ~0.1mm)
  // - Confidence values model real-world degradation from motion and signal quality
  _tickVitals(dt) {
    const room = findRoomForPoint(this.floor.rooms, this.x, this.y);
    const zoneQuality = this.floor.baseSignalQuality[room] || 0.5;

    // Breathing: always present, rate depends on activity
    const bRange = BREATHING_RANGES[this.activity] || BREATHING_RANGES.standing;
    const targetBreathing = randRange(bRange.min, bRange.max);
    this.breathingRate = ouStep(this.breathingRate, targetBreathing, 0.3, 0.5, dt);
    this.breathingRate = clamp(this.breathingRate, BREATHING_MIN, BREATHING_MAX);
    this.breathingConfidence = clamp(
      ouStep(this.breathingConfidence, this.isMoving ? 0.4 : 0.8, 0.5, 0.1, dt),
      0.1, 0.95
    );

    // Heart rate: only available when stationary >30s and zone quality sufficient
    if (!this.isMoving && this.stationaryTime >= STATIONARY_THRESHOLD_S && zoneQuality >= HR_SNR_THRESHOLD) {
      const hrRange = HEARTRATE_RANGES[this.activity] || HEARTRATE_RANGES.standing;
      const targetHR = randRange(hrRange.min, hrRange.max);
      this.heartRate = ouStep(this.heartRate, targetHR, 0.2, 1.0, dt);
      this.heartRate = clamp(this.heartRate, HEARTRATE_MIN, HEARTRATE_MAX);

      // Confidence ramps up over time
      const timeOverThreshold = this.stationaryTime - STATIONARY_THRESHOLD_S;
      const maxConf = zoneQuality * 0.8;  // zone quality caps HR confidence
      const targetConf = Math.min(maxConf, 0.3 + timeOverThreshold * 0.02);
      this.heartrateConfidence = ouStep(this.heartrateConfidence, targetConf, 0.3, 0.05, dt);
      this.heartrateConfidence = clamp(this.heartrateConfidence, 0, 0.85);
    } else {
      // Decay confidence when moving or conditions not met
      this.heartrateConfidence = ouStep(this.heartrateConfidence, 0, 0.8, 0.05, dt);
      this.heartrateConfidence = clamp(this.heartrateConfidence, 0, 0.85);
    }
  }

  /**
   * Generate the person payload for a WebSocket frame.
   * Position confidence derives from zone signal quality, with a 25% penalty
   * for moving targets (harder to localize). Uncertainty radius is inversely
   * proportional to confidence: 0.5/conf meters, clamped to [0.5, 8.0].
   * Heart rate display is gated by four conditions — the `display` boolean
   * is the authoritative flag that UI consumers should check.
   */
  toPayload(zoneQualities) {
    const room = findRoomForPoint(this.floor.rooms, this.x, this.y);
    const zoneQuality = zoneQualities[room] || 0.5;

    // Position confidence: base from zone quality, penalized by motion, with noise
    let posConf = zoneQuality;
    if (this.isMoving) posConf *= 0.75;  // moving targets harder to localize
    posConf += gaussRandom(0, 0.05);
    posConf = clamp(posConf, POSITION_CONFIDENCE_MIN, 0.98);

    // Uncertainty radius inversely proportional to confidence
    const uncertaintyRadius = clamp(0.5 / posConf, 0.5, 8.0);

    const isStationary = !this.isMoving;
    const hrDisplay = isStationary
      && this.stationaryTime >= STATIONARY_THRESHOLD_S
      && this.heartrateConfidence > 0.15
      && posConf > 0.6;

    return {
      id: this.id,
      x: Math.round(this.x * 100) / 100,
      y: Math.round(this.y * 100) / 100,
      position_confidence: Math.round(posConf * 100) / 100,
      uncertainty_radius_m: Math.round(uncertaintyRadius * 10) / 10,
      is_stationary: isStationary,
      stationary_duration_s: Math.round(isStationary ? this.stationaryTime * 10 : 0) / 10,
      breathing: {
        rate_bpm: Math.round(this.breathingRate),
        confidence: Math.round(this.breathingConfidence * 100) / 100,
      },
      heartrate: {
        rate_bpm: Math.round(this.heartRate),
        confidence: Math.round(this.heartrateConfidence * 100) / 100,
        display: hrDisplay,
      },
    };
  }
}

// ── Zone Signal Quality Simulator ──────────────────────────────

class ZoneSignalSimulator {
  constructor(baseQualities) {
    this.baseQualities = { ...baseQualities };
    this.currentQualities = { ...baseQualities };
    // Each zone has its own slow-drift offset
    this.offsets = {};
    for (const zone of Object.keys(baseQualities)) {
      this.offsets[zone] = 0;
    }
  }

  tick(dt) {
    for (const zone of Object.keys(this.baseQualities)) {
      // Slow Ornstein-Uhlenbeck drift around base quality
      this.offsets[zone] = ouStep(this.offsets[zone], 0, 0.1, 0.03, dt);
      this.currentQualities[zone] = clamp(
        this.baseQualities[zone] + this.offsets[zone],
        0.1,
        0.98
      );
    }
    return this.currentQualities;
  }

  getQualities() {
    const result = {};
    for (const [zone, val] of Object.entries(this.currentQualities)) {
      result[zone] = Math.round(val * 100) / 100;
    }
    return result;
  }
}

// ── Main Simulator ─────────────────────────────────────────────

export class Simulator {
  /**
   * @param {Object} options
   * @param {'demo'|'random'} options.mode - Simulation mode
   * @param {string} [options.scenario] - Demo scenario name (from DEMO_SCENARIOS)
   * @param {number} [options.personCount] - Number of people (random mode, 1-4)
   * @param {number} [options.speedMultiplier] - Time multiplier (default 1.0)
   * @param {number} [options.floor] - Floor index (default 0)
   */
  constructor(options = {}) {
    this.mode = options.mode || 'random';
    this.scenarioName = options.scenario || 'morning_routine';
    this.speedMultiplier = options.speedMultiplier || CONFIG.simulation.speedMultiplier;
    this.floorIndex = options.floor || 0;
    this.floor = CONFIG.floors[this.floorIndex];

    this.people = [];
    this.zoneSimulator = new ZoneSignalSimulator(this.floor.baseSignalQuality);

    this.running = false;
    this.tickTimer = null;
    this.simTime = 0;

    /** @type {function|null} Called each tick with the generated payload */
    this.onPayload = null;

    this._initPeople(options.personCount);
  }

  _initPeople(personCount) {
    this.people = [];

    if (this.mode === 'demo') {
      const scenario = DEMO_SCENARIOS[this.scenarioName];
      if (!scenario) {
        console.warn(`Unknown scenario "${this.scenarioName}", falling back to random mode`);
        this.mode = 'random';
        this._initPeople(personCount);
        return;
      }

      for (const personDef of scenario.people) {
        const person = new SimulatedPerson(personDef.id, personDef.startWaypoint, this.floor);
        person._originalActions = [...personDef.actions];
        person.setActions(personDef.actions);
        this.people.push(person);
      }
    } else {
      // Random mode
      const count = clamp(personCount || CONFIG.simulation.defaultPersonCount, 1, CONFIG.simulation.maxPersonCount);
      const waypointIds = Object.keys(this.floor.waypoints);

      for (let i = 0; i < count; i++) {
        const startWp = waypointIds[i % waypointIds.length];
        const person = new SimulatedPerson(`p${i + 1}`, startWp, this.floor);
        person.startRandom();
        this.people.push(person);
      }
    }
  }

  /** Start the simulation loop */
  start() {
    if (this.running) return;
    this.running = true;
    this.simTime = 0;

    const tickMs = TICK_INTERVAL_MS / this.speedMultiplier;
    this.tickTimer = setInterval(() => this._tick(), Math.max(10, tickMs));
  }

  /** Stop the simulation loop */
  stop() {
    this.running = false;
    if (this.tickTimer) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
  }

  /** Reset and optionally reconfigure */
  reset(options = {}) {
    this.stop();
    if (options.mode) this.mode = options.mode;
    if (options.scenario) this.scenarioName = options.scenario;
    if (options.speedMultiplier) this.speedMultiplier = options.speedMultiplier;

    this.simTime = 0;
    this.zoneSimulator = new ZoneSignalSimulator(this.floor.baseSignalQuality);
    this._initPeople(options.personCount);
  }

  /** Set simulation speed multiplier (1.0 = realtime) */
  setSpeed(multiplier) {
    this.speedMultiplier = clamp(multiplier, 0.1, 10);
    if (this.running) {
      this.stop();
      this.start();
    }
  }

  /** Switch between demo and random modes */
  setMode(mode, scenario) {
    this.reset({ mode, scenario });
  }

  /** Generate a single payload without running the loop (for testing) */
  generatePayload() {
    return this._buildPayload();
  }

  // ── Internal ───────────────────────────────────────────────

  _tick() {
    const dt = (TICK_INTERVAL_MS / 1000) * this.speedMultiplier;
    this.simTime += dt;

    // Update zone signal qualities
    this.zoneSimulator.tick(dt);

    // Update each person
    for (const person of this.people) {
      person.tick(dt);
    }

    // Build and emit payload
    const payload = this._buildPayload();
    if (this.onPayload) {
      this.onPayload(payload);
    }
  }

  _buildPayload() {
    const zoneQualities = this.zoneSimulator.getQualities();

    const peoplePayloads = this.people.map(p => p.toPayload(zoneQualities));

    // Occupancy estimate: actual count with some noise
    const actualCount = this.people.length;
    let occupancyEstimate = actualCount;
    let occupancyConfidence = 0.9;

    // When people are close together, occupancy becomes uncertain
    for (let i = 0; i < this.people.length; i++) {
      for (let j = i + 1; j < this.people.length; j++) {
        const d = dist(this.people[i], this.people[j]);
        if (d < 2.0) {
          // Nearby people reduce occupancy confidence
          occupancyConfidence -= 0.15;
          // Small chance of miscounting
          if (Math.random() < 0.1) {
            occupancyEstimate += Math.random() < 0.5 ? -1 : 1;
          }
        }
      }
    }
    occupancyEstimate = clamp(occupancyEstimate, 1, actualCount + 1);
    occupancyConfidence = clamp(occupancyConfidence + gaussRandom(0, 0.03), 0.3, 0.98);
    occupancyConfidence = Math.round(occupancyConfidence * 100) / 100;

    return {
      timestamp: Date.now() / 1000,
      floor: this.floor.id,
      people: peoplePayloads,
      occupancy_estimate: occupancyEstimate,
      occupancy_confidence: occupancyConfidence,
      zone_signal_quality: zoneQualities,
    };
  }
}
