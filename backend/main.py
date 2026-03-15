"""WiFi CSI pipeline entry point.

Wires together: MQTT collector → signal processing → tracking → vitals →
WebSocket broadcast via the FastAPI server.

Run with:
    python -m backend.main [--simulate] [--log-level INFO]

Or use uvicorn directly (main.py patches the app lifespan to start the pipeline):
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import numpy as np
import yaml

from backend.collector.csi_packet import CsiPacket, NUM_SUBCARRIERS
from backend.collector.mqtt_listener import MqttListener
from backend.processor.phase_sanitizer import sanitize_phase
from backend.processor.subcarrier_selector import select_top_k
from backend.processor.feature_extractor import extract_features
from backend.server.app import get_app_state, get_ws_manager, app as _app
from backend.server.schemas import (
    BreathingData,
    HeartrateData,
    PersonPosition,
    TrackingFrame,
)
from backend.tracker.floor_detector import (
    FloorDetector,
    TransitionZone,
    get_floor_ids,
    load_transition_zones,
)
from backend.tracker.fingerprint_db import FloorDB
from backend.tracker.localization import localize
from backend.tracker.occupancy import OccupancyDetector
from backend.tracker.particle_filter import FloorBounds, ParticleFilter
from backend.vitals.breathing import BreathingExtractor
from backend.vitals.heartrate import HeartRateExtractor
from backend.vitals.motion_detector import MotionDetector

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────

CONFIG_DIR = Path(__file__).resolve().parent / "config"
CSI_SAMPLE_RATE = 100.0  # Hz
BROADCAST_RATE = 10.0  # Hz — WebSocket broadcast rate
BROADCAST_INTERVAL = 1.0 / BROADCAST_RATE
FRAMES_PER_BROADCAST = int(CSI_SAMPLE_RATE / BROADCAST_RATE)  # 10

# Health monitoring
SENSOR_TIMEOUT_S = 5.0  # seconds without data → sensor considered offline
STALE_DATA_WARN_S = 2.0  # warn if no data for this long


def _load_config(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    logger.warning("Config file not found: %s", path)
    return {}


# ── Per-floor processing state ────────────────────────────────────


class FloorPipeline:
    """Signal processing and tracking state for a single floor.

    Maintains rolling buffers, particle filters, and vitals extractors
    for all detected people on this floor.
    """

    def __init__(
        self,
        floor_id: int,
        house_config: dict[str, Any],
        floor_db: Optional[FloorDB] = None,
    ) -> None:
        self.floor_id = floor_id
        self.floor_db = floor_db
        self.bounds = FloorBounds.from_house_config(house_config, floor_id)

        # Per-person state (keyed by person ID string)
        self.particle_filters: dict[str, ParticleFilter] = {}
        self.motion_detectors: dict[str, MotionDetector] = {}
        self.breathing_extractors: dict[str, BreathingExtractor] = {}
        self.heartrate_extractors: dict[str, HeartRateExtractor] = {}

        # Floor-level occupancy detector
        self.occupancy_detector = OccupancyDetector()

        # Latest results cache
        self.last_occupancy_estimate: int = 0
        self.last_occupancy_confidence: float = 0.0
        self.last_positions: dict[str, PersonPosition] = {}
        self.last_update_time: float = 0.0

        # Amplitude buffer for feature extraction (most recent window)
        self._amplitude_buffer: list[np.ndarray] = []
        self._phase_buffer: list[np.ndarray] = []
        self._frame_count: int = 0

        # Room signal quality tracking
        self._rooms = house_config.get("floors", {}).get(
            floor_id, house_config.get("floors", {}).get(str(floor_id), {})
        ).get("rooms", [])

    def _get_or_create_person(self, person_id: str) -> None:
        """Ensure per-person stateful modules exist."""
        if person_id not in self.particle_filters:
            self.particle_filters[person_id] = ParticleFilter(bounds=self.bounds)
        if person_id not in self.motion_detectors:
            self.motion_detectors[person_id] = MotionDetector()
        if person_id not in self.breathing_extractors:
            self.breathing_extractors[person_id] = BreathingExtractor()
        if person_id not in self.heartrate_extractors:
            self.heartrate_extractors[person_id] = HeartRateExtractor()

    def process_packet(self, packet: CsiPacket) -> None:
        """Process a single CSI packet through the floor pipeline.

        Pushes amplitude data into all stateful extractors and updates
        occupancy detection.
        """
        now = time.time()
        self.last_update_time = now
        self._frame_count += 1

        amplitude = packet.amplitude_array
        phase = packet.phase_array

        # Buffer for feature extraction
        self._amplitude_buffer.append(amplitude)
        self._phase_buffer.append(phase)
        if len(self._amplitude_buffer) > 200:
            self._amplitude_buffer = self._amplitude_buffer[-200:]
            self._phase_buffer = self._phase_buffer[-200:]

        # Sanitize phase
        sanitized_phase = sanitize_phase(phase)

        # Occupancy detection (floor-level)
        occ_result = self.occupancy_detector.update(amplitude)
        if occ_result is not None:
            self.last_occupancy_estimate = occ_result.occupancy_estimate
            self.last_occupancy_confidence = occ_result.occupancy_confidence

        # Ensure we have person slots for detected occupants
        num_people = max(self.last_occupancy_estimate, 1)
        for i in range(num_people):
            pid = f"p{i + 1}"
            self._get_or_create_person(pid)

            # Push amplitude to motion detector
            motion_result = self.motion_detectors[pid].update(amplitude)

            # Push amplitude to breathing extractor
            breathing_result = self.breathing_extractors[pid].update(amplitude)

            # Push amplitude to heartrate extractor (needs motion context)
            breathing_freq = None
            if breathing_result is not None:
                breathing_freq = breathing_result.breathing_rate_bpm / 60.0

            position_confidence = 0.5  # default when uncalibrated
            is_stationary = False
            stationary_duration = 0.0
            if motion_result is not None:
                is_stationary = motion_result.is_stationary
                stationary_duration = motion_result.stationary_duration_s

            hr_result = self.heartrate_extractors[pid].update(
                amplitude,
                position_confidence=position_confidence,
                is_stationary=is_stationary,
                stationary_duration_s=stationary_duration,
                breathing_freq_hz=breathing_freq,
            )

    def build_tracking_frame(self) -> TrackingFrame:
        """Assemble current state into a TrackingFrame for WebSocket broadcast."""
        now = time.time()
        people: list[PersonPosition] = []

        for pid in sorted(self.particle_filters.keys()):
            pf = self.particle_filters[pid]
            md = self.motion_detectors[pid]
            br = self.breathing_extractors[pid]
            hr = self.heartrate_extractors[pid]

            # Position: from particle filter if initialized, else center of floor
            if pf.is_initialized:
                # Use latest particle filter estimate
                result = pf._compute_result()
                x, y = result.x, result.y
                pos_conf = result.convergence
                uncertainty = max(1.0 - result.convergence, 0.1) * 5.0
            else:
                # Uncalibrated: use localization if available, else estimate
                if (
                    self.floor_db is not None
                    and len(self._amplitude_buffer) >= 100
                ):
                    amp_matrix = np.vstack(self._amplitude_buffer[-100:])
                    phase_matrix = np.vstack(self._phase_buffer[-100:])
                    selection = select_top_k(amp_matrix, k=30)
                    selected_phase = phase_matrix[:, selection.indices]
                    features = extract_features(selection.data, selected_phase)
                    loc = localize(self.floor_db, features.vector)
                    x, y = loc.x, loc.y
                    pos_conf = loc.position_confidence
                    uncertainty = loc.uncertainty_radius_m

                    # Feed into particle filter
                    dt = 1.0 / BROADCAST_RATE
                    pf_result = pf.update(x, y, uncertainty, dt)
                    x, y = pf_result.x, pf_result.y
                    pos_conf = pf_result.convergence
                else:
                    # No calibration data — place at floor center
                    x = self.bounds.x_max / 2.0
                    y = self.bounds.y_max / 2.0
                    pos_conf = 0.1
                    uncertainty = 5.0

            # Motion state
            motion = md.classify() if md.is_ready else None
            is_stat = motion.is_stationary if motion else False
            stat_dur = motion.stationary_duration_s if motion else 0.0

            # Breathing
            br_est = br.estimate() if br.is_ready else None
            breathing = BreathingData(
                rate_bpm=int(round(br_est.breathing_rate_bpm)) if br_est else 0,
                confidence=br_est.breathing_confidence if br_est else 0.0,
            )

            # Heart rate
            hr_est = hr.estimate(
                position_confidence=pos_conf,
                is_stationary=is_stat,
                stationary_duration_s=stat_dur,
                breathing_freq_hz=(
                    br_est.breathing_rate_bpm / 60.0
                    if br_est
                    else None
                ),
            ) if hr.is_ready else None
            heartrate = HeartrateData(
                rate_bpm=int(round(hr_est.rate_bpm)) if (hr_est and hr_est.rate_bpm is not None) else 0,
                confidence=hr_est.confidence if hr_est else 0.0,
                display=hr_est.display if hr_est else False,
            )

            people.append(
                PersonPosition(
                    id=pid,
                    x=round(float(np.clip(x, -1.0, 16.0)), 2),
                    y=round(float(np.clip(y, -1.0, 13.0)), 2),
                    position_confidence=round(pos_conf, 3),
                    uncertainty_radius_m=round(min(uncertainty, 10.0), 2),
                    is_stationary=is_stat,
                    stationary_duration_s=round(stat_dur, 1),
                    breathing=breathing,
                    heartrate=heartrate,
                )
            )

        # Zone signal quality (placeholder — would be derived from per-room
        # subcarrier variance in a full deployment)
        zone_quality: dict[str, float] = {}
        for room in self._rooms:
            name = room.get("name", "Unknown")
            # Higher quality when we have recent data
            age = now - self.last_update_time if self.last_update_time > 0 else 999
            quality = max(0.0, min(1.0, 1.0 - age / SENSOR_TIMEOUT_S))
            zone_quality[name] = round(quality, 2)

        return TrackingFrame(
            timestamp=now,
            floor=self.floor_id,
            people=people,
            occupancy_estimate=self.last_occupancy_estimate,
            occupancy_confidence=round(self.last_occupancy_confidence, 3),
            zone_signal_quality=zone_quality,
        )


# ── Synthetic data generator (simulator fallback) ─────────────────


class SyntheticCSIGenerator:
    """Generates synthetic CSI packets when no MQTT hardware is available.

    Simulates 1-2 people walking randomly within a single floor, producing
    amplitude patterns with motion and breathing signatures.
    """

    def __init__(
        self,
        floor_id: int = 1,
        sample_rate: float = CSI_SAMPLE_RATE,
        seed: int = 42,
    ) -> None:
        self._floor_id = floor_id
        self._sample_rate = sample_rate
        self._rng = np.random.default_rng(seed)
        self._t = 0
        self._breathing_freq = 0.25  # Hz (15 bpm)
        self._base_amplitude = 50.0

    def generate(self) -> CsiPacket:
        """Generate a single synthetic CSI packet."""
        t_s = self._t / self._sample_rate
        self._t += 1

        # Base amplitude with slow drift
        base = self._base_amplitude + 5.0 * np.sin(0.01 * t_s)

        # Breathing modulation (0.25 Hz ≈ 15 bpm)
        breathing = 3.0 * np.sin(2.0 * math.pi * self._breathing_freq * t_s)

        # Heart rate modulation (1.2 Hz ≈ 72 bpm) — weak
        heartrate = 0.3 * np.sin(2.0 * math.pi * 1.2 * t_s)

        # Per-subcarrier variation + noise
        subcarrier_var = self._rng.normal(0, 2.0, NUM_SUBCARRIERS)
        noise = self._rng.normal(0, 1.5, NUM_SUBCARRIERS)

        amplitude = base + breathing + heartrate + subcarrier_var + noise
        amplitude = np.maximum(amplitude, 1.0)

        # Convert amplitude to I/Q (phase ≈ 0 for simplicity)
        phase = self._rng.uniform(-0.1, 0.1, NUM_SUBCARRIERS)
        I = (amplitude * np.cos(phase)).astype(np.int16)
        Q = (amplitude * np.sin(phase)).astype(np.int16)

        iq_pairs = []
        for i in range(NUM_SUBCARRIERS):
            iq_pairs.append(int(I[i]))
            iq_pairs.append(int(Q[i]))

        return CsiPacket(
            timestamp_us=int(t_s * 1e6),
            tx_mac="aa:bb:cc:dd:01:00",
            rx_mac="aa:bb:cc:dd:01:01",
            rssi=-40,
            floor_id=self._floor_id - 1,  # 0-based in packet
            iq_pairs=iq_pairs,
        )


# ── Pipeline orchestrator ─────────────────────────────────────────


class Pipeline:
    """Main CSI processing pipeline.

    Consumes CSI packets (from MQTT or simulator), routes them through
    per-floor processing, and broadcasts TrackingFrames via WebSocket.
    """

    def __init__(
        self,
        house_config: dict[str, Any],
        sensors_config: dict[str, Any],
        simulate: bool = False,
    ) -> None:
        self.house_config = house_config
        self.sensors_config = sensors_config
        self.simulate = simulate

        # Per-floor pipelines
        floor_ids = get_floor_ids(house_config)
        self.floor_pipelines: dict[int, FloorPipeline] = {}
        for fid in floor_ids:
            self.floor_pipelines[fid] = FloorPipeline(
                floor_id=fid,
                house_config=house_config,
            )

        # Floor detector (cross-floor)
        transition_zones = load_transition_zones(house_config)
        self.floor_detector = FloorDetector(
            floor_ids=floor_ids,
            transition_zones=transition_zones,
        )

        # MQTT listener
        mqtt_cfg = sensors_config.get("mqtt", {})
        self.mqtt_listener = MqttListener(
            broker_host=mqtt_cfg.get("broker_host", "localhost"),
            broker_port=mqtt_cfg.get("broker_port", 1883),
            subscribe_pattern=mqtt_cfg.get("subscribe_pattern", "csi/#"),
            keepalive=mqtt_cfg.get("keepalive_s", 60),
            qos=mqtt_cfg.get("qos", 0),
        )

        # Synthetic generator (fallback)
        self.synth_generator = SyntheticCSIGenerator(
            floor_id=floor_ids[0] if floor_ids else 1,
        )

        # Health monitoring
        self._last_packet_time: float = 0.0
        self._sensor_last_seen: dict[str, float] = {}
        self._running = False

        # Tasks
        self._pipeline_task: Optional[asyncio.Task] = None
        self._broadcast_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the pipeline: MQTT listener + processing loop + broadcast."""
        self._running = True
        loop = asyncio.get_event_loop()

        if not self.simulate:
            try:
                self.mqtt_listener.start(loop=loop)
                logger.info("MQTT listener started")
            except Exception as exc:
                logger.warning(
                    "MQTT connection failed (%s), falling back to simulator", exc
                )
                self.simulate = True

        if self.simulate:
            logger.info("Running in simulator mode (no MQTT)")

        self._pipeline_task = asyncio.create_task(self._pipeline_loop())
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        self._health_task = asyncio.create_task(self._health_monitor_loop())

        logger.info("Pipeline started (simulate=%s)", self.simulate)

    async def stop(self) -> None:
        """Gracefully shut down the pipeline."""
        self._running = False

        for task in (self._pipeline_task, self._broadcast_task, self._health_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self.mqtt_listener.stop()
        logger.info("Pipeline stopped")

    async def _pipeline_loop(self) -> None:
        """Main processing loop: consume CSI packets and route to floor pipelines."""
        while self._running:
            try:
                if self.simulate:
                    # Generate synthetic data at CSI_SAMPLE_RATE
                    packet = self.synth_generator.generate()
                    await asyncio.sleep(1.0 / CSI_SAMPLE_RATE)
                else:
                    # Consume from MQTT queue with timeout
                    try:
                        packet = await asyncio.wait_for(
                            self.mqtt_listener.queue.get(),
                            timeout=STALE_DATA_WARN_S,
                        )
                    except asyncio.TimeoutError:
                        # No data — check if we should fall back to simulator
                        elapsed = time.time() - self._last_packet_time
                        if elapsed > SENSOR_TIMEOUT_S and self._last_packet_time > 0:
                            logger.warning(
                                "No CSI data for %.1fs, switching to simulator",
                                elapsed,
                            )
                            self.simulate = True
                        continue

                self._last_packet_time = time.time()

                # Track sensor health
                self._sensor_last_seen[packet.rx_mac] = self._last_packet_time

                # Route to floor pipeline (floor_id in packet is 0-based)
                floor_id = packet.floor_id + 1  # convert to 1-based
                if floor_id in self.floor_pipelines:
                    self.floor_pipelines[floor_id].process_packet(packet)
                else:
                    # Default to first floor if unknown
                    first_floor = next(iter(self.floor_pipelines))
                    self.floor_pipelines[first_floor].process_packet(packet)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in pipeline loop")
                await asyncio.sleep(0.01)

    async def _broadcast_loop(self) -> None:
        """Broadcast TrackingFrames at BROADCAST_RATE Hz."""
        ws_manager = get_ws_manager()

        while self._running:
            try:
                await asyncio.sleep(BROADCAST_INTERVAL)

                for floor_id, fp in self.floor_pipelines.items():
                    if fp.last_update_time == 0:
                        continue  # no data yet for this floor

                    frame = fp.build_tracking_frame()
                    await ws_manager.broadcast_frame(frame)

                # Update FPS metric
                app_state = get_app_state()
                app_state.tracking_fps = BROADCAST_RATE

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in broadcast loop")

    async def _health_monitor_loop(self) -> None:
        """Periodic health checks: sensor dropout, stale data."""
        while self._running:
            try:
                await asyncio.sleep(5.0)
                now = time.time()

                # Check sensor health
                for mac, last_seen in self._sensor_last_seen.items():
                    age = now - last_seen
                    if age > SENSOR_TIMEOUT_S:
                        logger.warning(
                            "Sensor %s offline (no data for %.1fs)", mac, age
                        )

                # Log pipeline stats
                total_packets = self.mqtt_listener.packets_received
                if not self.simulate and total_packets > 0:
                    logger.debug(
                        "Pipeline stats: %d packets received, %d dropped, %d malformed",
                        total_packets,
                        self.mqtt_listener.packets_dropped,
                        self.mqtt_listener.malformed_packets,
                    )

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in health monitor")


# ── Global pipeline instance ──────────────────────────────────────

_pipeline: Optional[Pipeline] = None


def get_pipeline() -> Optional[Pipeline]:
    """Get the global pipeline instance."""
    return _pipeline


# ── FastAPI lifespan override ─────────────────────────────────────
# Wraps the existing app lifespan to also start/stop the pipeline.


@asynccontextmanager
async def lifespan_with_pipeline(app_instance):
    """Extended lifespan that starts the CSI pipeline alongside the server."""
    global _pipeline

    # Load configs
    house_config = _load_config("house.yaml")
    sensors_config = _load_config("sensors.yaml")

    # Initialize app state (mirrors the original lifespan)
    state = get_app_state()
    state.start_time = time.time()
    state.house_config = house_config
    for floor_id in house_config.get("floors", {}):
        state.calibrating[int(floor_id)] = False

    # Detect simulation mode
    simulate = getattr(app_instance, "_simulate_mode", False)

    # Start pipeline
    _pipeline = Pipeline(
        house_config=house_config,
        sensors_config=sensors_config,
        simulate=simulate,
    )
    await _pipeline.start()

    logger.info(
        "WiFi CSI server started with pipeline. Floors: %s",
        list(house_config.get("floors", {}).keys()),
    )

    yield  # ── application runs ──

    # Shutdown
    if _pipeline is not None:
        await _pipeline.stop()

    await state.ws_manager.close_all()
    logger.info("WiFi CSI server shut down.")


# Replace the app's lifespan with our extended version
app = _app
app.router.lifespan_context = lifespan_with_pipeline


# ── CLI entry point ───────────────────────────────────────────────


def main() -> None:
    """CLI entry point for running the server with pipeline."""
    parser = argparse.ArgumentParser(
        description="WiFi CSI tracking server with full pipeline integration"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run with synthetic CSI data (no MQTT hardware required)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Server bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server port (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Pass simulate flag to the lifespan via app attribute
    app._simulate_mode = args.simulate  # type: ignore[attr-defined]

    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower(),
    )


if __name__ == "__main__":
    main()
