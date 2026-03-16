/**
 * Test setup — Mock browser APIs not provided by jsdom.
 *
 * Mocks: WebSocket, Canvas2D context, requestAnimationFrame, performance.now, fetch
 */

// ── Mock WebSocket ────────────────────────────────────────────
// Simulates the WebSocket API with controllable open/close/message events.
// Tests call mockWs.triggerOpen(), mockWs.triggerMessage(data), etc.

export class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  static instances = [];
  static nextBehavior = 'open'; // 'open' | 'error' | 'hang'

  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.CONNECTING;
    this.onopen = null;
    this.onclose = null;
    this.onerror = null;
    this.onmessage = null;
    this.sentMessages = [];
    MockWebSocket.instances.push(this);

    if (MockWebSocket.nextBehavior === 'open') {
      // Auto-open on next microtask (like real WebSocket)
      Promise.resolve().then(() => {
        if (this.readyState === MockWebSocket.CONNECTING) {
          this.triggerOpen();
        }
      });
    } else if (MockWebSocket.nextBehavior === 'error') {
      Promise.resolve().then(() => {
        this.triggerError();
        this.triggerClose(1006, 'Connection failed');
      });
    }
    // 'hang' = do nothing (stays CONNECTING)
  }

  send(data) {
    if (this.readyState !== MockWebSocket.OPEN) {
      throw new Error('WebSocket is not open');
    }
    this.sentMessages.push(data);
  }

  close(code, reason) {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.CLOSING;
    // Trigger close on next microtask
    Promise.resolve().then(() => {
      this.readyState = MockWebSocket.CLOSED;
      if (this.onclose) {
        this.onclose({ code: code || 1000, reason: reason || '', wasClean: true });
      }
    });
  }

  // ── Test helpers ──────────────────────────────────────────

  triggerOpen() {
    this.readyState = MockWebSocket.OPEN;
    if (this.onopen) this.onopen({});
  }

  triggerClose(code = 1000, reason = '') {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) this.onclose({ code, reason, wasClean: code === 1000 });
  }

  triggerError() {
    if (this.onerror) this.onerror(new Event('error'));
  }

  triggerMessage(data) {
    if (this.onmessage) {
      this.onmessage({ data: typeof data === 'string' ? data : JSON.stringify(data) });
    }
  }

  static reset() {
    MockWebSocket.instances = [];
    MockWebSocket.nextBehavior = 'open';
  }

  static get latest() {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }
}

// Install globally
globalThis.WebSocket = MockWebSocket;
// Expose constants on the class (matches native WebSocket)
WebSocket.CONNECTING = MockWebSocket.CONNECTING;
WebSocket.OPEN = MockWebSocket.OPEN;
WebSocket.CLOSING = MockWebSocket.CLOSING;
WebSocket.CLOSED = MockWebSocket.CLOSED;

// ── Mock Canvas 2D Context ────────────────────────────────────
// Returns a no-op context that records method calls for assertions.

class MockCanvasContext {
  constructor() {
    this.calls = [];
    this.lineWidth = 1;
    this.fillStyle = '';
    this.strokeStyle = '';
    this.font = '';
    this.textAlign = '';
    this.textBaseline = '';
  }

  clearRect(...args) { this.calls.push(['clearRect', ...args]); }
  fillRect(...args) { this.calls.push(['fillRect', ...args]); }
  beginPath() { this.calls.push(['beginPath']); }
  arc(...args) { this.calls.push(['arc', ...args]); }
  fill() { this.calls.push(['fill']); }
  stroke() { this.calls.push(['stroke']); }
  moveTo(...args) { this.calls.push(['moveTo', ...args]); }
  lineTo(...args) { this.calls.push(['lineTo', ...args]); }
  rect(...args) { this.calls.push(['rect', ...args]); }
  clip() { this.calls.push(['clip']); }
  save() { this.calls.push(['save']); }
  restore() { this.calls.push(['restore']); }
  setLineDash(pattern) { this.calls.push(['setLineDash', pattern]); }
  fillText(...args) { this.calls.push(['fillText', ...args]); }
  createRadialGradient(...args) {
    this.calls.push(['createRadialGradient', ...args]);
    return { addColorStop() {} };
  }

  reset() { this.calls = []; }
}

// Patch HTMLCanvasElement.prototype.getContext
const origGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function (type) {
  if (type === '2d') {
    if (!this._mockCtx) {
      this._mockCtx = new MockCanvasContext();
    }
    return this._mockCtx;
  }
  return origGetContext.call(this, type);
};

// ── Mock requestAnimationFrame / cancelAnimationFrame ─────────

let rafId = 0;
const rafCallbacks = new Map();

globalThis.requestAnimationFrame = (cb) => {
  const id = ++rafId;
  rafCallbacks.set(id, cb);
  return id;
};

globalThis.cancelAnimationFrame = (id) => {
  rafCallbacks.delete(id);
};

/** Flush all pending rAF callbacks with a given timestamp */
globalThis.__flushRAF = (timestamp = performance.now()) => {
  const cbs = [...rafCallbacks.entries()];
  rafCallbacks.clear();
  for (const [, cb] of cbs) {
    cb(timestamp);
  }
};

// ── Mock fetch (for SVG floor plan loading) ───────────────────

globalThis.fetch = vi.fn(() =>
  Promise.resolve({
    ok: true,
    text: () => Promise.resolve('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1800 1050"></svg>'),
  })
);

// ── Mock performance.now if not available ─────────────────────

if (typeof performance === 'undefined') {
  globalThis.performance = { now: () => Date.now() };
}

// ── Payload factory helpers ───────────────────────────────────

export function makePayload(overrides = {}) {
  return {
    timestamp: Date.now() / 1000,
    floor: 1,
    people: [],
    occupancy_estimate: 0,
    occupancy_confidence: 0.9,
    zone_signal_quality: {
      garage: 0.45,
      family_room: 0.85,
      kitchen: 0.70,
      hallway: 0.88,
      dining: 0.55,
      utility: 0.60,
      office: 0.50,
      parlor: 0.48,
    },
    ...overrides,
  };
}

export function makePerson(id, overrides = {}) {
  return {
    id,
    x: 3.5,
    y: 2.75,
    position_confidence: 0.85,
    uncertainty_radius_m: 0.6,
    is_stationary: true,
    stationary_duration_s: 45.0,
    breathing: { rate_bpm: 16, confidence: 0.78 },
    heartrate: { rate_bpm: 72, confidence: 0.55, display: true },
    ...overrides,
  };
}

export function makeHighConfPerson(id = 'p1') {
  return makePerson(id, {
    position_confidence: 0.90,
    uncertainty_radius_m: 0.6,
  });
}

export function makeMedConfPerson(id = 'p2') {
  return makePerson(id, {
    position_confidence: 0.60,
    uncertainty_radius_m: 0.8,
  });
}

export function makeLowConfPerson(id = 'p3') {
  return makePerson(id, {
    position_confidence: 0.30,
    uncertainty_radius_m: 1.7,
    heartrate: { rate_bpm: 0, confidence: 0.0, display: false },
  });
}

export function makeMovingPerson(id = 'p1', x = 5.0, y = 3.0) {
  return makePerson(id, {
    x,
    y,
    position_confidence: 0.65,
    uncertainty_radius_m: 0.8,
    is_stationary: false,
    stationary_duration_s: 0,
    breathing: { rate_bpm: 18, confidence: 0.45 },
    heartrate: { rate_bpm: 80, confidence: 0.0, display: false },
  });
}
