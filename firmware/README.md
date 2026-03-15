# WiFi CSI Firmware (ESP32-S3)

ESP-IDF firmware for the WiFi CSI people tracking system. Each ESP32-S3 board runs in either **TX** (transmitter) or **RX** (receiver) mode.

- **TX boards** send UDP frames at 100Hz to stimulate CSI on receivers.
- **RX boards** extract CSI from received frames (114 subcarriers, HT40) and publish binary packets via MQTT.

## Prerequisites

- [ESP-IDF v5.x](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/) installed and sourced
- ESP32-S3-DevKitC-1 (N16R8 recommended)
- USB cable for flashing

## Quick Start

```bash
# Source ESP-IDF environment
. $HOME/esp/esp-idf/export.sh

# Build with defaults (RX mode, floor 0, channel 1)
cd firmware
idf.py set-target esp32s3
idf.py build

# Flash and monitor
idf.py -p /dev/ttyUSB0 flash monitor
```

## Configuration

All settings are configurable via `idf.py menuconfig` under **WiFi CSI Configuration**:

| Setting | Default | Description |
|---------|---------|-------------|
| Board role | RX | `TX` or `RX` — set per board |
| WiFi SSID | `MyHomeWiFi` | House WiFi network |
| WiFi Password | `changeme` | House WiFi password |
| WiFi Channel | `1` | Floor channel: 1, 6, or 11 |
| Floor ID | `0` | 0 = Ground, 1 = Second, 2 = Third |
| MQTT Broker IP | `192.168.1.100` | Raspberry Pi IP |
| MQTT Broker Port | `1883` | Mosquitto default |
| TX Rate | `100` Hz | UDP frame rate (TX boards only) |
| Board ID | `board-01` | Human-readable label |
| TX Target IP | `255.255.255.255` | UDP destination (TX boards) |
| TX UDP Port | `5500` | UDP port (TX boards) |

### Per-Board Flashing

Each board needs its own config. The fastest workflow:

```bash
# Board 1: Floor 1 TX
idf.py menuconfig   # Set role=TX, floor=0, channel=1, board-id=f1-tx
idf.py build flash -p /dev/ttyUSB0

# Board 2: Floor 1 RX-A
idf.py menuconfig   # Set role=RX, floor=0, channel=1, board-id=f1-rx-a
idf.py build flash -p /dev/ttyUSB1
```

Alternatively, override settings without menuconfig by editing `sdkconfig.defaults` and running `idf.py fullclean && idf.py build`.

## Binary Packet Format

RX boards publish 478-byte binary packets to MQTT topic `csi/{floor_id}/{rx_mac}`:

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 8 | uint64_t | Timestamp (microseconds) |
| 8 | 6 | uint8_t[6] | TX MAC address |
| 14 | 6 | uint8_t[6] | RX MAC address |
| 20 | 1 | int8_t | RSSI (dBm) |
| 21 | 1 | uint8_t | Floor ID |
| 22 | 456 | int16_t[228] | I/Q pairs (114 subcarriers × 2) |

All values are little-endian. Matches `backend/collector/csi_packet.py`.

## Project Structure

```
firmware/
├── CMakeLists.txt          # ESP-IDF project file
├── sdkconfig.defaults      # Default build config
├── README.md               # This file
└── main/
    ├── CMakeLists.txt      # Component registration
    ├── Kconfig.projbuild   # Menuconfig definitions
    ├── config.h            # Compile-time constants from Kconfig
    ├── main.c              # WiFi init, role dispatch (TX/RX)
    ├── csi_handler.c/h     # CSI callback + binary serialization
    └── mqtt_client.c       # MQTT publish (QoS 0)
    └── mqtt_client_csi.h   # MQTT client header
```
