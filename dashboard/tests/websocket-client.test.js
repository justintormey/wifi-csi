/**
 * WebSocket Client Tests — Connection, Reconnection, Heartbeat, Simulator Fallback
 *
 * Tests the WebSocketClient class with mock WebSocket scenarios:
 * - Connection lifecycle (connect → connected → disconnect)
 * - Reconnection with exponential backoff
 * - Simulator fallback after connection failure
 * - Heartbeat detection and stale connection handling
 * - Seamless upgrade from simulator to live WebSocket
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { MockWebSocket, makePayload } from './setup.js';

// Import the module under test
import { WebSocketClient, bindStatusIndicator } from '../js/websocket-client.js';

describe('WebSocketClient', () => {
  beforeEach(() => {
    MockWebSocket.reset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('connection lifecycle', () => {
    it('should start in disconnected state', () => {
      const client = new WebSocketClient({ url: 'ws://test:8080' });
      expect(client.status).toBe('disconnected');
      expect(client.isActive).toBe(false);
      expect(client.isSimulating).toBe(false);
    });

    it('should transition to connecting then connected', async () => {
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      const statusChanges = [];
      client.onStatusChange = (s) => statusChanges.push(s);

      client.connect();
      expect(client.status).toBe('connecting');

      // Let the auto-open microtask fire
      await vi.advanceTimersByTimeAsync(0);

      expect(client.status).toBe('connected');
      expect(client.isActive).toBe(true);
      expect(statusChanges).toEqual(['connecting', 'connected']);

      client.disconnect();
    });

    it('should disconnect cleanly', async () => {
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      client.connect();
      await vi.advanceTimersByTimeAsync(0);
      expect(client.status).toBe('connected');

      client.disconnect();
      expect(client.status).toBe('disconnected');
      expect(client.isActive).toBe(false);
    });

    it('should deliver parsed JSON payloads', async () => {
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      const payloads = [];
      client.onPayload = (p) => payloads.push(p);
      client.connect();
      await vi.advanceTimersByTimeAsync(0);

      const testPayload = makePayload({ floor: 1, occupancy_estimate: 2 });
      MockWebSocket.latest.triggerMessage(testPayload);

      expect(payloads).toHaveLength(1);
      expect(payloads[0].floor).toBe(1);
      expect(payloads[0].occupancy_estimate).toBe(2);

      client.disconnect();
    });

    it('should ignore malformed JSON messages', async () => {
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      const payloads = [];
      client.onPayload = (p) => payloads.push(p);
      client.connect();
      await vi.advanceTimersByTimeAsync(0);

      MockWebSocket.latest.triggerMessage('not valid json {{{');
      expect(payloads).toHaveLength(0);

      client.disconnect();
    });
  });

  describe('reconnection with exponential backoff', () => {
    it('should reconnect after connection loss', async () => {
      MockWebSocket.nextBehavior = 'open';
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      client.connect();
      await vi.advanceTimersByTimeAsync(0);
      expect(client.status).toBe('connected');

      // Simulate connection drop
      const ws = MockWebSocket.latest;
      ws.readyState = MockWebSocket.CLOSED;
      ws.onclose({ code: 1006, reason: 'Connection lost', wasClean: false });

      expect(client.status).toBe('reconnecting');

      // First retry at 1s
      MockWebSocket.nextBehavior = 'open';
      await vi.advanceTimersByTimeAsync(1000);
      await vi.advanceTimersByTimeAsync(0); // let microtask fire

      expect(client.status).toBe('connected');
      expect(MockWebSocket.instances).toHaveLength(2);

      client.disconnect();
    });

    it('should use exponential backoff: 1s, 2s, 4s, 8s...', async () => {
      MockWebSocket.nextBehavior = 'error';
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      client.connect();
      await vi.advanceTimersByTimeAsync(0); // first attempt fails

      expect(client.status).toBe('reconnecting');

      // Attempt 2 at 1s
      await vi.advanceTimersByTimeAsync(1000);
      await vi.advanceTimersByTimeAsync(0);
      expect(MockWebSocket.instances).toHaveLength(2);

      // Attempt 3 at 2s
      await vi.advanceTimersByTimeAsync(2000);
      await vi.advanceTimersByTimeAsync(0);
      expect(MockWebSocket.instances).toHaveLength(3);

      // Attempt 4 at 4s
      await vi.advanceTimersByTimeAsync(4000);
      await vi.advanceTimersByTimeAsync(0);
      expect(MockWebSocket.instances).toHaveLength(4);

      client.disconnect();
    });

    it('should cap backoff at 30s', async () => {
      MockWebSocket.nextBehavior = 'error';
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      client.connect();

      // Run through many retry cycles: 1s, 2s, 4s, 8s, 16s, 30s, 30s...
      for (let i = 0; i < 8; i++) {
        await vi.advanceTimersByTimeAsync(0);
        const delay = Math.min(1000 * Math.pow(2, i), 30000);
        await vi.advanceTimersByTimeAsync(delay);
      }

      // Should still be reconnecting, not stuck
      expect(client.status).toBe('reconnecting');

      client.disconnect();
    });
  });

  describe('simulator fallback', () => {
    it('should fall back to simulator after 3s of failed connections', async () => {
      // Use 'error' for the initial connection, then 'hang' so reconnect attempts
      // don't immediately error and interfere with the fallback timer.
      MockWebSocket.nextBehavior = 'error';
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: true });
      const statusChanges = [];
      client.onStatusChange = (s) => statusChanges.push(s);

      client.connect();
      await vi.advanceTimersByTimeAsync(0); // first attempt fails (error + close microtasks)

      expect(client.status).toBe('reconnecting');

      // Switch to 'hang' so the 1s reconnect attempt doesn't immediately error
      MockWebSocket.nextBehavior = 'hang';

      // Wait for fallback timer (3s from first failure)
      await vi.advanceTimersByTimeAsync(3000);
      expect(client.status).toBe('simulator');
      expect(client.isSimulating).toBe(true);
      expect(client.isActive).toBe(true);

      client.disconnect();
    });

    it('should deliver simulated payloads during fallback', async () => {
      MockWebSocket.nextBehavior = 'error';
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: true });
      const payloads = [];
      client.onPayload = (p) => payloads.push(p);

      client.connect();
      await vi.advanceTimersByTimeAsync(0);

      // Hang subsequent attempts so fallback timer fires cleanly
      MockWebSocket.nextBehavior = 'hang';
      await vi.advanceTimersByTimeAsync(3000); // trigger fallback

      expect(client.status).toBe('simulator');

      // Let simulator tick (100ms intervals)
      await vi.advanceTimersByTimeAsync(500);
      expect(payloads.length).toBeGreaterThan(0);
      expect(payloads[0]._simulated).toBe(true);

      client.disconnect();
    });

    it('should upgrade from simulator to live WebSocket seamlessly', async () => {
      MockWebSocket.nextBehavior = 'error';
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: true });

      client.connect();
      await vi.advanceTimersByTimeAsync(0); // first attempt errors

      MockWebSocket.nextBehavior = 'hang';
      await vi.advanceTimersByTimeAsync(3000); // trigger fallback

      expect(client.status).toBe('simulator');

      // At this point, reconnect attempts continue in the background.
      // The hanging WS from the 1s reconnect is still open.
      // Close it to trigger the next reconnect cycle.
      const hangingWs = MockWebSocket.latest;
      MockWebSocket.nextBehavior = 'open';
      hangingWs.triggerClose(1006, 'Connection failed');

      // Wait for the next reconnect timer + auto-open microtask
      await vi.advanceTimersByTimeAsync(5000);
      await vi.advanceTimersByTimeAsync(0);

      expect(client.status).toBe('connected');
      expect(client.isSimulating).toBe(false);

      client.disconnect();
    });

    it('should not fall back when autoFallback is false', async () => {
      MockWebSocket.nextBehavior = 'error';
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });

      client.connect();
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(5000);

      expect(client.status).toBe('reconnecting');
      expect(client.isSimulating).toBe(false);

      client.disconnect();
    });
  });

  describe('manual simulator control', () => {
    it('should start simulator on demand', () => {
      const client = new WebSocketClient({ url: 'ws://test:8080' });
      const payloads = [];
      client.onPayload = (p) => payloads.push(p);

      client.startSimulator('demo', 'morning_routine');
      expect(client.status).toBe('simulator');
      expect(client.isSimulating).toBe(true);

      // Let it tick
      vi.advanceTimersByTime(200);
      expect(payloads.length).toBeGreaterThan(0);

      client.disconnect();
    });

    it('should stop simulator and reconnect on stopSimulator()', async () => {
      const client = new WebSocketClient({ url: 'ws://test:8080' });
      client.startSimulator('demo', 'morning_routine');
      expect(client.status).toBe('simulator');

      MockWebSocket.nextBehavior = 'open';
      client.stopSimulator();

      // Should be attempting to connect
      expect(client.status).toBe('connecting');
      await vi.advanceTimersByTimeAsync(0);
      expect(client.status).toBe('connected');

      client.disconnect();
    });
  });

  describe('heartbeat / stale connection detection', () => {
    it('should send ping every 15 seconds when connected', async () => {
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      client.connect();
      await vi.advanceTimersByTimeAsync(0);

      const ws = MockWebSocket.latest;
      expect(ws.sentMessages).toHaveLength(0);

      // Advance 15s
      vi.advanceTimersByTime(15000);
      expect(ws.sentMessages).toHaveLength(1);

      const ping = JSON.parse(ws.sentMessages[0]);
      expect(ping.type).toBe('ping');
      expect(ping.timestamp).toBeDefined();

      client.disconnect();
    });

    it('should close connection if no response within 10s of ping', async () => {
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      client.connect();
      await vi.advanceTimersByTimeAsync(0);

      const ws = MockWebSocket.latest;

      // Send ping at 15s
      await vi.advanceTimersByTimeAsync(15000);
      expect(ws.sentMessages).toHaveLength(1);

      // No response for 10s — heartbeat timeout fires, calls ws.close()
      await vi.advanceTimersByTimeAsync(10000);

      // ws.close() sets CLOSING, then microtask sets CLOSED
      await vi.advanceTimersByTimeAsync(0);

      expect(ws.readyState).toBe(MockWebSocket.CLOSED);

      client.disconnect();
    });

    it('should reset heartbeat timeout on any incoming message', async () => {
      const client = new WebSocketClient({ url: 'ws://test:8080', autoFallback: false });
      client.connect();
      await vi.advanceTimersByTimeAsync(0);

      const ws = MockWebSocket.latest;

      // Send ping at 15s
      vi.advanceTimersByTime(15000);

      // Receive a message at 20s (5s after ping, within 10s timeout)
      vi.advanceTimersByTime(5000);
      ws.triggerMessage(makePayload());

      // Wait another 10s — should NOT have closed
      vi.advanceTimersByTime(10000);
      expect(ws.readyState).toBe(MockWebSocket.OPEN);

      client.disconnect();
    });
  });

  describe('bindStatusIndicator', () => {
    it('should update element text and class on status changes', () => {
      const client = new WebSocketClient({ url: 'ws://test:8080' });
      const el = document.createElement('div');

      const unbind = bindStatusIndicator(client, el);

      // Initial state
      expect(el.textContent).toBe('OFFLINE');
      expect(el.classList.contains('status-offline')).toBe(true);

      // Simulate status change
      client._setStatus('connected');
      expect(el.textContent).toBe('LIVE');
      expect(el.classList.contains('status-live')).toBe(true);
      expect(el.classList.contains('status-offline')).toBe(false);

      client._setStatus('simulator');
      expect(el.textContent).toBe('DEMO');
      expect(el.classList.contains('status-demo')).toBe(true);

      // Unbind
      unbind();
      client._setStatus('disconnected');
      // Should NOT update after unbind
      expect(el.textContent).toBe('DEMO');
    });
  });
});
