/**
 * WiFi CSI Dashboard — WebSocket Client
 *
 * Auto-reconnecting WebSocket client with exponential backoff,
 * heartbeat detection, and simulator fallback for demo mode.
 *
 * Usage:
 *   import { WebSocketClient } from './websocket-client.js';
 *   const ws = new WebSocketClient({ url: 'ws://localhost:8080/ws/tracking' });
 *   ws.onPayload = (payload) => { ... };
 *   ws.onStatusChange = (status) => { ... };
 *   ws.connect();
 */

import { CONFIG } from './config.js';
import { Simulator } from './simulator.js';

// ── Constants ──────────────────────────────────────────────────

const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 30000;
const HEARTBEAT_INTERVAL_MS = 15000;  // send ping every 15s
const HEARTBEAT_TIMEOUT_MS = 10000;   // wait 10s for pong before declaring stale
const SIMULATOR_FALLBACK_DELAY_MS = 3000; // wait before falling back to simulator

/**
 * Connection status values:
 *   'disconnected' — not connected, not trying
 *   'connecting'   — WebSocket opening
 *   'connected'    — WebSocket open and receiving data
 *   'reconnecting' — connection lost, backing off before retry
 *   'simulator'    — using local simulator as data source
 */

// ── WebSocket Client ──────────────────────────────────────────

export class WebSocketClient {
  /**
   * @param {Object} options
   * @param {string} [options.url] - WebSocket URL (default from CONFIG.wsUrl)
   * @param {boolean} [options.autoFallback] - Fall back to simulator on failure (default true)
   * @param {string} [options.simulatorMode] - Simulator mode when falling back ('demo'|'random')
   * @param {string} [options.simulatorScenario] - Demo scenario name for fallback
   */
  constructor(options = {}) {
    this.url = options.url || CONFIG.wsUrl;
    this.autoFallback = options.autoFallback !== false;
    this.simulatorMode = options.simulatorMode || 'random';
    this.simulatorScenario = options.simulatorScenario || 'morning_routine';

    this._ws = null;
    this._status = 'disconnected';
    this._reconnectAttempts = 0;
    this._reconnectTimer = null;
    this._heartbeatTimer = null;
    this._heartbeatTimeoutTimer = null;
    this._simulator = null;
    this._intentionallyClosed = false;
    this._fallbackTimer = null;

    /** @type {function|null} Called with each parsed payload object */
    this.onPayload = null;

    /** @type {function|null} Called when connection status changes */
    this.onStatusChange = null;
  }

  /** Current connection status */
  get status() {
    return this._status;
  }

  /** Whether data is flowing (from WebSocket or simulator) */
  get isActive() {
    return this._status === 'connected' || this._status === 'simulator';
  }

  /** Whether using simulator fallback */
  get isSimulating() {
    return this._status === 'simulator';
  }

  /** Start connecting to the WebSocket server */
  connect() {
    this._intentionallyClosed = false;
    this._reconnectAttempts = 0;
    this._setStatus('connecting');
    this._open();
  }

  /** Disconnect and stop all activity */
  disconnect() {
    this._intentionallyClosed = true;
    this._clearTimers();
    this._stopSimulator();

    if (this._ws) {
      this._ws.onopen = null;
      this._ws.onclose = null;
      this._ws.onerror = null;
      this._ws.onmessage = null;
      this._ws.close();
      this._ws = null;
    }

    this._setStatus('disconnected');
  }

  /** Force switch to simulator mode (e.g., user toggles demo mode) */
  startSimulator(mode, scenario) {
    this._clearTimers();
    if (this._ws) {
      this._ws.onopen = null;
      this._ws.onclose = null;
      this._ws.onerror = null;
      this._ws.onmessage = null;
      this._ws.close();
      this._ws = null;
    }
    this.simulatorMode = mode || this.simulatorMode;
    this.simulatorScenario = scenario || this.simulatorScenario;
    this._activateSimulator();
  }

  /** Switch back from simulator to live WebSocket */
  stopSimulator() {
    this._stopSimulator();
    this.connect();
  }

  // ── Internal: WebSocket lifecycle ───────────────────────────

  _open() {
    try {
      this._ws = new WebSocket(this.url);
    } catch (err) {
      this._handleConnectionFailure();
      return;
    }

    this._ws.onopen = () => {
      this._reconnectAttempts = 0;
      this._clearFallbackTimer();
      this._stopSimulator();
      this._setStatus('connected');
      this._startHeartbeat();
    };

    this._ws.onmessage = (event) => {
      this._onPong();
      this._handleMessage(event.data);
    };

    this._ws.onerror = () => {
      // onerror is always followed by onclose, so just log
    };

    this._ws.onclose = (event) => {
      this._stopHeartbeat();
      this._ws = null;

      if (this._intentionallyClosed) return;

      this._handleConnectionFailure();
    };
  }

  _handleMessage(data) {
    try {
      const payload = JSON.parse(data);
      if (this.onPayload) {
        this.onPayload(payload);
      }
    } catch (err) {
      // Ignore malformed messages
    }
  }

  // ── Internal: Reconnection with exponential backoff ─────────

  _handleConnectionFailure() {
    if (this._intentionallyClosed) return;

    this._reconnectAttempts++;
    const delay = Math.min(
      BACKOFF_BASE_MS * Math.pow(2, this._reconnectAttempts - 1),
      BACKOFF_MAX_MS
    );

    this._setStatus('reconnecting');

    // Start fallback timer on first failure if auto-fallback enabled
    if (this.autoFallback && this._reconnectAttempts === 1) {
      this._fallbackTimer = setTimeout(() => {
        if (this._status === 'reconnecting') {
          this._activateSimulator();
        }
      }, SIMULATOR_FALLBACK_DELAY_MS);
    }

    this._reconnectTimer = setTimeout(() => {
      if (!this._intentionallyClosed) {
        this._open();
      }
    }, delay);
  }

  // ── Internal: Heartbeat / stale connection detection ────────

  _startHeartbeat() {
    this._stopHeartbeat();
    this._heartbeatTimer = setInterval(() => {
      if (this._ws && this._ws.readyState === WebSocket.OPEN) {
        // Send a ping frame (text-based for compatibility)
        try {
          this._ws.send(JSON.stringify({ type: 'ping', timestamp: Date.now() }));
        } catch (err) {
          // If send fails, connection is dead
          this._ws.close();
          return;
        }

        // Start timeout waiting for any message back
        this._heartbeatTimeoutTimer = setTimeout(() => {
          // No response — connection is stale
          if (this._ws) {
            this._ws.close();
          }
        }, HEARTBEAT_TIMEOUT_MS);
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  _stopHeartbeat() {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
    if (this._heartbeatTimeoutTimer) {
      clearTimeout(this._heartbeatTimeoutTimer);
      this._heartbeatTimeoutTimer = null;
    }
  }

  _onPong() {
    // Any incoming message counts as a "pong" — reset the stale timer
    if (this._heartbeatTimeoutTimer) {
      clearTimeout(this._heartbeatTimeoutTimer);
      this._heartbeatTimeoutTimer = null;
    }
  }

  // ── Internal: Simulator fallback ────────────────────────────

  _activateSimulator() {
    if (this._simulator) return;

    this._simulator = new Simulator({
      mode: this.simulatorMode,
      scenario: this.simulatorScenario,
    });

    this._simulator.onPayload = (payload) => {
      if (this.onPayload) {
        // Tag payload so consumers know it's simulated
        payload._simulated = true;
        this.onPayload(payload);
      }
    };

    this._simulator.start();
    this._setStatus('simulator');
  }

  _stopSimulator() {
    if (this._simulator) {
      this._simulator.stop();
      this._simulator = null;
    }
  }

  // ── Internal: Status management ─────────────────────────────

  _setStatus(newStatus) {
    if (this._status === newStatus) return;
    const oldStatus = this._status;
    this._status = newStatus;
    if (this.onStatusChange) {
      this.onStatusChange(newStatus, oldStatus);
    }
  }

  _clearFallbackTimer() {
    if (this._fallbackTimer) {
      clearTimeout(this._fallbackTimer);
      this._fallbackTimer = null;
    }
  }

  _clearTimers() {
    this._stopHeartbeat();
    this._clearFallbackTimer();
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }
}

// ── HUD Status Indicator Helper ─────────────────────────────

/**
 * Binds a WebSocketClient to a DOM element to show connection status.
 * Adds CSS classes and text content matching the current status.
 *
 * @param {WebSocketClient} client
 * @param {HTMLElement} element - Status indicator element
 * @returns {function} Unbind function
 */
export function bindStatusIndicator(client, element) {
  const STATUS_LABELS = {
    disconnected: 'OFFLINE',
    connecting:   'CONNECTING',
    connected:    'LIVE',
    reconnecting: 'RECONNECTING',
    simulator:    'DEMO',
  };

  const STATUS_CLASSES = {
    disconnected: 'status-offline',
    connecting:   'status-connecting',
    connected:    'status-live',
    reconnecting: 'status-reconnecting',
    simulator:    'status-demo',
  };

  function update(status) {
    // Remove all status classes
    for (const cls of Object.values(STATUS_CLASSES)) {
      element.classList.remove(cls);
    }
    element.classList.add(STATUS_CLASSES[status] || 'status-offline');
    element.textContent = STATUS_LABELS[status] || 'UNKNOWN';
  }

  const handler = (newStatus) => update(newStatus);
  client.onStatusChange = handler;

  // Set initial state
  update(client.status);

  // Return unbind function
  return () => {
    if (client.onStatusChange === handler) {
      client.onStatusChange = null;
    }
  };
}
