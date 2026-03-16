# WiFi CSI Backend (Python)

Python backend for the WiFi CSI people tracking system. Runs on a Raspberry Pi 4, processing real-time CSI data from ESP32-S3 boards via MQTT and serving tracking results over WebSocket.

## Prerequisites

- Python 3.10+
- Mosquitto MQTT broker running on the same host (or reachable via network)
- `config/house.yaml` and `config/sensors.yaml` configured for your deployment

## Quick Start

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Run with MQTT listener (production)
python -m backend.main --log-level INFO

# Run with simulated CSI data (no hardware needed)
python -m backend.main --simulate

# Or use uvicorn directly (pipeline starts automatically via lifespan)
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

The WebSocket endpoint is available at `ws://localhost:8000/ws/tracking` and broadcasts tracking frames at 10 Hz.

## Architecture

```
MQTT (csi/#)
    │
    ▼
collector/           Parse binary CSI packets from ESP32 boards
    │
    ▼
processor/           Signal conditioning pipeline
    │                  ├── Phase sanitization (SpotFi)
    │                  ├── Butterworth bandpass filtering
    │                  ├── Hampel outlier removal
    │                  ├── Subcarrier selection (top-K by variance)
    │                  └── Feature vector extraction
    │
    ├──────────────────┐
    ▼                  ▼
tracker/           vitals/
    │                  │
    │  KNN + particle  │  Breathing: 0.1–0.5 Hz FFT
    │  filter → (x,y)  │  Heart rate: 0.8–2.0 Hz CWT
    │  Floor detection  │  Motion classification
    │  NMF occupancy   │
    │                  │
    └──────┬───────────┘
           ▼
server/              FastAPI + WebSocket broadcast at 10 Hz
           │
           ▼
        Dashboard
```

## Module Overview

### `collector/`
- **`csi_packet.py`** — `CsiPacket` dataclass. Parses the 478-byte binary format from ESP32 boards (timestamp, MACs, RSSI, floor ID, 114 I/Q pairs). Converts raw I/Q to amplitude and phase arrays.
- **`mqtt_listener.py`** — Subscribes to `csi/#` topics on Mosquitto, deserializes binary payloads into `CsiPacket` objects, and dispatches them to the processing pipeline.

### `processor/`
- **`phase_sanitizer.py`** — SpotFi linear offset removal. Fits `phase[k] = a·k + b` across subcarriers and subtracts to remove clock offset artifacts.
- **`amplitude_filter.py`** — Butterworth bandpass filter (breathing: 0.1–0.5 Hz, heartrate: 0.8–2.0 Hz) and Hampel outlier filter for amplitude cleaning.
- **`subcarrier_selector.py`** — Selects top-K subcarriers by variance. Noisy or dead subcarriers are discarded.
- **`feature_extractor.py`** — Builds fingerprint feature vectors from processed CSI data for localization.

### `tracker/`
- **`fingerprint_db.py`** — Multi-floor fingerprint database backed by `.npz` files. Stores calibration CSI profiles mapped to (x, y, floor) positions. Supports KNN queries via cosine distance.
- **`localization.py`** — Weighted KNN (K=5, cosine distance) position estimation against the fingerprint database.
- **`particle_filter.py`** — Sequential Monte Carlo tracker (200 particles). Velocity-constrained random walk prediction, Gaussian likelihood update, systematic resampling. Produces smooth trajectories.
- **`floor_detector.py`** — Detects which floor a person is on by comparing CSI energy from each floor's TX (separate WiFi channels 1, 6, 11). Uses hysteresis to prevent noisy floor flips.
- **`occupancy.py`** — Multi-person detection via Non-negative Matrix Factorization (NMF). Decomposes CSI amplitude matrix into independent sources, estimates count via residual-ratio elbow test.

### `vitals/`
- **`motion_detector.py`** — Stationary vs. moving classification from CSI amplitude variance. Tracks continuous stationary duration (used as a gate for heart rate).
- **`breathing.py`** — Breathing rate extraction: bandpass → top-K subcarrier averaging → FFT peak detection. Accuracy: ±1–2 bpm.
- **`heartrate.py`** — Heart rate extraction: breathing harmonic removal → bandpass → CWT (Morlet wavelet). Display-gated: requires position confidence > 0.6, stationary > 30s, and sufficient SNR. Accuracy: ±8–10 bpm.
- **`windowed_fft.py`** — Shared FFT/CWT utilities, peak detection, and SNR calculation.

### `server/`
- **`app.py`** — FastAPI application with REST endpoints (`/health`, `/config`, `/calibration`) and the WebSocket endpoint (`/ws/tracking`).
- **`ws_manager.py`** — WebSocket connection manager. Handles connect/disconnect and broadcasts tracking frames to all connected clients.
- **`schemas.py`** — Pydantic models for the WebSocket payload: `TrackingFrame`, `PersonPosition`, `BreathingData`, `HeartrateData`.

### `calibration/`
- **`collector.py`** — Guided walk calibration: collects CSI data at known positions.
- **`builder.py`** — Builds fingerprint databases from calibration data.

### `config/`
- **`house.yaml`** — Floor dimensions, room layout, sensor positions, transition zones (stairwells).
- **`sensors.yaml`** — Sensor MAC addresses and roles (TX/RX per floor).

### `main.py`
Pipeline entry point. Wires collector → processor → tracker → vitals → WebSocket broadcast. Supports `--simulate` mode for development without hardware.

## Configuration

All deployment-specific settings are in `config/house.yaml` and `config/sensors.yaml`. Key settings:

| File | Key | Description |
|------|-----|-------------|
| `house.yaml` | `floors[n].dimensions` | Floor width/depth in meters |
| `house.yaml` | `floors[n].sensor_positions` | TX/RX board placement coordinates |
| `house.yaml` | `transition_zones` | Stairwell bounding boxes for floor change detection |
| `sensors.yaml` | `sensors[n].mac` | ESP32 MAC address |
| `sensors.yaml` | `sensors[n].role` | `tx` or `rx` |
| `sensors.yaml` | `sensors[n].floor` | Floor assignment (0, 1, 2) |

## Testing

```bash
# Run all tests
pytest backend/tests/ -v

# Run with coverage
pytest backend/tests/ --cov=backend --cov-report=term-missing
```

Tests use synthetic CSI data and fixtures in `tests/fixtures/`. No hardware required.

## Development Notes

- The pipeline runs at 100 Hz CSI input → 10 Hz WebSocket output. Processing is batched per-floor.
- All signal processing uses NumPy/SciPy for performance. The particle filter and NMF are the most CPU-intensive components.
- The `--simulate` flag generates synthetic CSI packets internally, bypassing MQTT entirely.
- Type hints are used throughout. Run `mypy backend/` for static type checking.
