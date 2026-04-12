# Installation Guide

Complete setup instructions for the WiFi CSI People Tracking system — from a blank Raspberry Pi and unflashed ESP32-S3 boards to a running system.

**Audience:** Someone setting up from scratch with no prior ESP-IDF or Raspberry Pi experience.

**Time estimate:** ~2 hours for single-floor setup (Phase 1).

---

## Prerequisites

Before starting, ensure you have:

- [ ] 4 x ESP32-S3-DevKitC-1 (N16R8) boards — see [`hardware-bom.md`](hardware-bom.md)
- [ ] 1 x Raspberry Pi 4 (4GB RAM minimum) with MicroSD card (32GB+, A1-rated)
- [ ] USB-C cables for each ESP32 board (3m max recommended)
- [ ] USB-C power adapters (5V/2A) for each board
- [ ] A development machine (macOS, Linux, or Windows with WSL) for flashing firmware
- [ ] Your house WiFi SSID and password (2.4GHz band)
- [ ] The Raspberry Pi's IP address (static IP or DHCP reservation recommended)

---

## Part 1: Raspberry Pi Setup

The RPi serves as the central hub — it runs the Mosquitto MQTT broker (receives CSI data from ESP32 boards) and the Python backend (signal processing, tracking, and WebSocket server).

### 1.1 Install Raspberry Pi OS

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Flash **Raspberry Pi OS Lite (64-bit, Bookworm)** to your MicroSD card
3. In the imager's settings (gear icon), configure:
   - Hostname: `csi-hub`
   - Enable SSH (use password or SSH key)
   - Set your WiFi credentials (or use Ethernet — preferred for reliability)
   - Set locale/timezone
4. Insert the MicroSD card into the RPi and boot it
5. SSH in: `ssh pi@csi-hub.local` (or use the IP address)

### 1.2 Run the Setup Script

The automated setup script installs everything needed. It is idempotent and safe to re-run.

```bash
# Option A: Clone the repo on the RPi (if you have internet access)
git clone https://github.com/justintormey/wifi-csi.git
cd wifi-csi/deploy
sudo ./setup-rpi.sh

# Option B: Copy the repo from your dev machine
# (from your dev machine)
scp -r /path/to/wifi-csi pi@csi-hub.local:~/wifi-csi
# (on the RPi)
cd ~/wifi-csi/deploy
sudo ./setup-rpi.sh
```

The script performs 8 steps:

| Step | What It Does |
|------|-------------|
| 1. System packages | Installs Mosquitto, Python 3, numpy/scipy ARM math libraries (`libatlas-base-dev`, `libopenblas-dev`), monitoring tools |
| 2. Service user | Creates a `csi` system user (no login shell) to run the backend |
| 3. Repo install | Copies (or clones) the project to `/opt/wifi-csi` |
| 4. Python venv | Creates `/opt/wifi-csi/venv` and installs `backend/requirements.txt` |
| 5. Mosquitto config | Deploys MQTT broker config to `/etc/mosquitto/conf.d/wifi-csi.conf` |
| 6. mDNS | Sets hostname to `csi-hub`, advertises MQTT (port 1883) and HTTP (port 8000) via Avahi |
| 7. systemd + logrotate | Installs `wifi-csi-backend.service` (enabled but **not started**) and daily log rotation |
| 8. Backup cron | Schedules daily fingerprint DB backups at 3 AM |

### 1.3 Verify the Installation

```bash
# Check Mosquitto is running
sudo systemctl status mosquitto
# Expected: active (running)

# Check mDNS is advertising
avahi-browse -art | grep csi
# Expected: csi-hub, _mqtt._tcp, _http._tcp

# Test MQTT broker (in two terminals)
# Terminal 1: subscribe
mosquitto_sub -t 'test/#' -v
# Terminal 2: publish
mosquitto_pub -t 'test/hello' -m 'it works'
# Terminal 1 should show: test/hello it works

# Check the backend service exists (not started yet — that's intentional)
sudo systemctl status wifi-csi-backend
# Expected: loaded, enabled, inactive (dead)
```

> **Why isn't the backend started?** The setup script intentionally leaves the backend stopped. Start it only after ESP32 boards are flashed and publishing CSI data, or use `--simulate` mode for testing without hardware.

### 1.4 Test Without Hardware (Optional)

You can verify the full backend pipeline using simulation mode:

```bash
# Start the backend with synthetic CSI data
sudo -u csi /opt/wifi-csi/venv/bin/python -m backend.main --simulate --host 0.0.0.0 --port 8000
```

Open `http://csi-hub.local:8000` from a browser on your network to confirm the WebSocket endpoint is reachable. Press `Ctrl+C` to stop.

---

## Part 2: ESP32 Firmware Flashing

Each ESP32-S3 board runs firmware in either **TX** or **RX** mode. You need to flash each board individually with its specific configuration.

### 2.1 Install ESP-IDF

ESP-IDF is Espressif's official development framework. You need version 5.x.

```bash
# Install prerequisites (Ubuntu/Debian)
sudo apt-get install git wget flex bison gperf python3 python3-pip \
    python3-venv cmake ninja-build ccache libffi-dev libssl-dev \
    dfu-util libusb-1.0-0

# macOS (with Homebrew)
brew install cmake ninja dfu-util python3

# Clone ESP-IDF (stable release)
mkdir -p ~/esp
cd ~/esp
git clone -b v5.4 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32s3

# Source the environment (required every terminal session)
. ~/esp/esp-idf/export.sh
```

> **Tip:** Add `. ~/esp/esp-idf/export.sh` to your shell profile to auto-source in new terminals.

For detailed instructions, see the [official ESP-IDF Getting Started Guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/).

### 2.2 Understand the Board Roles

Each floor uses 4 boards:

| Board | Role | What It Does | WiFi Mode |
|-------|------|-------------|-----------|
| **TX** (1 per floor) | Transmitter | Sends UDP frames at 100Hz to stimulate CSI on receivers | STA (connects to house WiFi) |
| **RX** (3 per floor) | Receiver | Extracts CSI from received WiFi frames and publishes via MQTT | STA (connects to house WiFi) |

Channel assignment per floor (non-overlapping to avoid interference):

| Floor | WiFi Channel | Firmware Floor ID |
|-------|-------------|-------------------|
| Ground (Floor 1) | **1** | **0** |
| Second (Floor 2) | **6** | **1** |
| Third (Floor 3) | **11** | **2** |

> **Important:** The firmware uses 0-based floor IDs (0, 1, 2) while the backend uses 1-based (1, 2, 3). The backend handles this conversion automatically.

### 2.3 Flash Each Board

For each board, you configure it via `menuconfig`, build, and flash. Connect the board to your dev machine via USB-C.

```bash
# Source ESP-IDF (if not already done)
. ~/esp/esp-idf/export.sh

# Navigate to the firmware directory
cd /path/to/wifi-csi/firmware

# Set the target chip (first time only)
idf.py set-target esp32s3
```

#### Configure via menuconfig

```bash
idf.py menuconfig
```

Navigate to **WiFi CSI Configuration** and set the following for each board:

| Setting | Floor 1 TX | Floor 1 RX-A | Floor 1 RX-B | Floor 1 RX-C |
|---------|-----------|-------------|-------------|-------------|
| Board role | **TX** | RX | RX | RX |
| WiFi SSID | `YourSSID` | `YourSSID` | `YourSSID` | `YourSSID` |
| WiFi Password | `YourPass` | `YourPass` | `YourPass` | `YourPass` |
| WiFi channel | **1** | **1** | **1** | **1** |
| Floor ID | **0** | **0** | **0** | **0** |
| MQTT Broker IP | `192.168.1.x` | `192.168.1.x` | `192.168.1.x` | `192.168.1.x` |
| Board identifier | `f1-tx` | `f1-rx-a` | `f1-rx-b` | `f1-rx-c` |

> Replace `192.168.1.x` with your Raspberry Pi's actual IP address. You can also use `csi-hub.local` if your network supports mDNS resolution from ESP32 boards.

Save and exit menuconfig (`S` to save, `Q` to quit).

#### Build and flash

```bash
# Build the firmware
idf.py build

# Flash to the connected board and open serial monitor
idf.py -p /dev/ttyUSB0 flash monitor
```

> **Port name varies by OS:**
> - Linux: `/dev/ttyUSB0` or `/dev/ttyACM0`
> - macOS: `/dev/cu.usbmodem*` or `/dev/cu.SLAB_USBtoUART`
> - Windows (WSL): `/dev/ttyS*` (map from COM port)

#### Record the MAC address

When the board boots, the serial monitor will display the board's MAC address:

```
I (xxx) wifi_csi: MAC address: aa:bb:cc:dd:ee:ff
```

**Write this down.** You will need it for `sensors.yaml` configuration (see [Part 3](#part-3-post-flash-configuration)).

#### Verify the board is working

Watch the serial monitor output:

**TX board — expected output:**
```
I (xxx) wifi_csi: Board role: TX
I (xxx) wifi_csi: Starting TX mode on channel 1
I (xxx) tx_rate: TX rate: 100.0 pps
```

**RX board — expected output:**
```
I (xxx) wifi_csi: Board role: RX
I (xxx) csi_handler: CSI callback registered (HT40, 114 subcarriers)
I (xxx) mqtt_client: Connected to mqtt://192.168.1.x:1883
I (xxx) csi_handler: Publishing CSI packets...
```

The **status LED** (GPIO 2, built-in blue LED) indicates connection state:
- Fast blink (200ms): Connecting to WiFi
- Solid on: Connected and operational
- Triple flash: Error — check serial monitor

Press `Ctrl+]` to exit the serial monitor.

#### Repeat for each board

Disconnect the board, connect the next one, and repeat the menuconfig → build → flash cycle. For boards on the same floor with the same role, you can skip the full rebuild:

```bash
# Same role, just changed board-id:
idf.py menuconfig   # Change only the board identifier
idf.py build flash -p /dev/ttyUSB0
```

For a different role (TX → RX) or different floor:

```bash
# Clean build recommended when changing role or floor
idf.py fullclean
idf.py menuconfig   # Set new role, floor, channel
idf.py build flash -p /dev/ttyUSB0
```

### 2.4 Alternative: Edit sdkconfig.defaults Directly

If you prefer scripting over the interactive menuconfig UI, you can edit `sdkconfig.defaults` directly:

```bash
# Example: configure as Floor 2 TX
sed -i 's/CONFIG_CSI_BOARD_ROLE_RX=y/# CONFIG_CSI_BOARD_ROLE_RX is not set/' sdkconfig.defaults
echo 'CONFIG_CSI_BOARD_ROLE_TX=y' >> sdkconfig.defaults
sed -i 's/CONFIG_CSI_WIFI_CHANNEL=1/CONFIG_CSI_WIFI_CHANNEL=6/' sdkconfig.defaults
sed -i 's/CONFIG_CSI_FLOOR_ID=0/CONFIG_CSI_FLOOR_ID=1/' sdkconfig.defaults
# etc.

# Clean and rebuild
idf.py fullclean && idf.py build && idf.py -p /dev/ttyUSB0 flash
```

---

## Part 3: Post-Flash Configuration

### 3.1 Update sensors.yaml with Real MAC Addresses

The file `backend/config/sensors.yaml` ships with placeholder MAC addresses (`aa:bb:cc:dd:xx:xx`). After flashing each board, you recorded its actual MAC address from the serial monitor. Update the file:

```bash
# On the RPi (or edit locally and re-deploy)
sudo nano /opt/wifi-csi/backend/config/sensors.yaml
```

Replace each placeholder MAC with the real one:

```yaml
sensors:
  # ── Floor 1: Ground Floor (Channel 1) ──
  - mac: "a4:cf:12:xx:xx:xx"   # ← Replace with actual MAC from f1-tx serial output
    role: tx
    floor: 1
    channel: 1
    position:
      x: 7.5
      y: 6.0
    label: "Floor 1 TX (ceiling center)"
  # ... repeat for each board
```

> **This step is critical.** The backend uses MAC addresses to correlate incoming MQTT packets with known sensor positions. With placeholder MACs, the tracker cannot map CSI data to physical locations.

### 3.2 Verify Board Positions in house.yaml

Review `backend/config/house.yaml` and adjust floor dimensions and room definitions to match your actual house layout. The default configuration assumes a ~15m x 12m floor plan.

```bash
sudo nano /opt/wifi-csi/backend/config/house.yaml
```

Key fields to check:
- `floors[].dimensions` — width and depth in meters
- `floors[].rooms[]` — room boundaries (for zone-level tracking labels)
- `stairwell_zones[]` — bounding boxes where floor transitions occur

### 3.3 Mount the Boards

See [`hardware-bom.md`](hardware-bom.md) for detailed placement diagrams and mounting instructions. Summary:

- **TX boards:** Ceiling mount, center of floor, antenna pointing down
- **RX boards:** Wall mount at ~1.5m (chest height), antenna facing into room, triangle arrangement
- Route USB-C cables along walls/ceiling edges with cable clips
- Keep boards at least 30cm from corners and 3m from microwave ovens

---

## Part 4: Start the System

### 4.1 Verify MQTT Data Flow

Before starting the backend, confirm that ESP32 boards are publishing CSI data to the broker:

```bash
# On the RPi — subscribe to all CSI topics
mosquitto_sub -t 'csi/#' -v
```

You should see a stream of binary data on topics like `csi/0/a4:cf:12:xx:xx:xx` (one topic per RX board). If nothing appears:

- Check that the boards are powered and the status LED is solid
- Verify the MQTT broker IP in the firmware matches the RPi's actual IP
- Check Mosquitto logs: `journalctl -u mosquitto -f`
- Check board status heartbeats: `mosquitto_sub -t 'status/#' -v` (JSON every 10 seconds)

### 4.2 Start the Backend Service

```bash
sudo systemctl start wifi-csi-backend
```

Verify it's running:

```bash
sudo systemctl status wifi-csi-backend
# Expected: active (running)

# Tail the logs
journalctl -u wifi-csi-backend -f
```

The backend starts a FastAPI server on port 8000 with a WebSocket endpoint at `ws://csi-hub.local:8000/ws/tracking`. It broadcasts tracking data at 10Hz.

### 4.3 Open the Dashboard

The dashboard is a static web app. Serve it from any machine on the network:

```bash
cd /path/to/wifi-csi/dashboard
python3 -m http.server 8080
```

Open `http://localhost:8080` in a browser. The dashboard will auto-connect to the backend WebSocket. If the backend is unreachable, the dashboard falls back to its built-in JavaScript simulator with demo scenarios.

> **Production deployment:** The dashboard can be hosted on S3 or any static file server. See the GitHub Actions workflow at `.github/workflows/deploy-dashboard.yml`.

---

## Part 5: Multi-Floor Configuration

If you're deploying across multiple floors, this section covers the additional configuration needed beyond the single-floor setup.

### 5.1 Flash Boards for Each Floor

Each floor needs 4 boards (1 TX + 3 RX) flashed with floor-specific settings. The key differences per floor are the **WiFi channel** and **floor ID**:

| Floor | Board IDs | WiFi Channel | Firmware Floor ID | menuconfig Notes |
|-------|-----------|-------------|-------------------|-----------------|
| Ground (Floor 1) | `f1-tx`, `f1-rx-a/b/c` | **1** | **0** | — |
| Second (Floor 2) | `f2-tx`, `f2-rx-a/b/c` | **6** | **1** | Change channel and floor ID |
| Basement (Floor 3) | `f3-tx`, `f3-rx-a/b/c` | **11** | **2** | Change channel and floor ID |

When switching between floors in menuconfig, do a **clean build** to avoid stale config:

```bash
idf.py fullclean
idf.py menuconfig   # Set new channel, floor ID, board identifier
idf.py build flash -p /dev/ttyUSB0
```

### 5.2 Update sensors.yaml for All Floors

After flashing all 12 boards, update `backend/config/sensors.yaml` with the real MAC addresses for every board. The file ships with all 3 floors pre-configured — you only need to replace the placeholder MACs.

```bash
sudo nano /opt/wifi-csi/backend/config/sensors.yaml
```

Verify the structure covers all floors:

```yaml
sensors:
  # ── Floor 1: Ground Floor (Channel 1) ──
  - mac: "xx:xx:xx:xx:xx:xx"    # f1-tx
    role: tx
    floor: 1
    channel: 1
    # ...
  # ... 3 more for Floor 1 RX boards

  # ── Floor 2: Second Floor (Channel 6) ──
  - mac: "xx:xx:xx:xx:xx:xx"    # f2-tx
    role: tx
    floor: 2
    channel: 6
    # ...
  # ... 3 more for Floor 2 RX boards

  # ── Floor 3: Basement (Channel 11) ──
  - mac: "xx:xx:xx:xx:xx:xx"    # f3-tx
    role: tx
    floor: 3
    channel: 11
    # ...
  # ... 3 more for Floor 3 RX boards
```

### 5.3 Configure house.yaml

Review `backend/config/house.yaml` for multi-floor-specific settings:

- **Floor dimensions:** Each floor has its own `width_m`, `depth_m`, and `height_m`. The default config uses 18.0m × 10.5m for all floors with 2.7m ceilings (2.4m for the basement).
- **Room definitions:** Rooms are defined per floor for zone-level tracking labels.
- **Transition zones:** Stairwell bounding boxes that connect adjacent floors. The floor detector uses these to allow rapid floor transitions. Measure your actual stairwell locations and update the coordinates:

```yaml
transition_zones:
  - name: "Main Stairwell (1st→2nd)"
    floors: [1, 2]
    x_min: 4.0    # meters from left wall
    x_max: 6.5
    y_min: 3.5    # meters from front wall
    y_max: 6.5
```

- **Attenuation model:** The `per_floor_db` value (default: 12 dB) models signal loss through floor/ceiling construction. Adjust if your building has unusually thick or thin floors.

### 5.4 Verify Multi-Floor MQTT Data

After mounting and powering all boards, confirm data arrives from all floors:

```bash
# Check each floor's MQTT topics
mosquitto_sub -t 'csi/0/#' -v   # Floor 1 data (firmware floor ID 0)
mosquitto_sub -t 'csi/1/#' -v   # Floor 2 data (firmware floor ID 1)
mosquitto_sub -t 'csi/2/#' -v   # Floor 3 data (firmware floor ID 2)

# Count active boards via heartbeat (should see 12 distinct MACs)
mosquitto_sub -t 'status/#' -v
```

### 5.5 Dashboard Floor Switching

The dashboard supports floor tabs (Floor 1, Floor 2, Floor 3). Each floor has its own SVG floor plan and room/waypoint configuration in `dashboard/js/config.js`. The WebSocket payload includes a `floor` field, and the dashboard renders tracking data for the currently selected floor.

---

## First Run Checklist

After completing setup, walk through this checklist:

- [ ] **RPi setup script completed** without errors
- [ ] **Mosquitto** is running: `sudo systemctl status mosquitto`
- [ ] **mDNS** resolves: `ping csi-hub.local` from another device
- [ ] **All boards flashed** with correct role, floor, channel, and MQTT broker IP
- [ ] **MAC addresses recorded** and updated in `sensors.yaml`
- [ ] **Board positions** in `sensors.yaml` match physical mounting locations
- [ ] **House dimensions** in `house.yaml` match your floor plan
- [ ] **MQTT data flowing**: `mosquitto_sub -t 'csi/#' -v` shows binary data
- [ ] **Board heartbeats**: `mosquitto_sub -t 'status/#' -v` shows JSON every 10s
- [ ] **Backend service running**: `sudo systemctl status wifi-csi-backend`
- [ ] **Dashboard connects** and shows tracking data

### Multi-Floor Additional Checks (if deploying all 3 floors)

- [ ] **All 12 boards flashed** with correct floor-specific channel and floor ID
- [ ] **MQTT data from all floors**: `mosquitto_sub -t 'csi/0/#'`, `csi/1/#`, `csi/2/#` all show data
- [ ] **12 board heartbeats** visible in `mosquitto_sub -t 'status/#' -v`
- [ ] **Transition zones** in `house.yaml` match physical stairwell locations
- [ ] **Floor switching** works in dashboard (Floor 1/2/3 tabs)
- [ ] **Floor detection** transitions correctly when walking between floors via stairwells

---

## Troubleshooting

### ESP32 Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Board won't flash | Wrong USB port or missing driver | Try a different USB cable; install [CP210x driver](https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers) if needed |
| "Failed to connect" during flash | Board not in download mode | Hold BOOT button, press RESET, release BOOT, then retry flash |
| Status LED fast-blinking continuously | Can't connect to WiFi | Verify SSID/password in menuconfig; ensure 2.4GHz network is active |
| Status LED triple-flash | MQTT connection failed | Check broker IP; verify Mosquitto is running on RPi |
| No CSI data in MQTT | RX board not receiving TX frames | Ensure TX and RX are on the same WiFi channel; check TX is powered and running |
| Board reboots every 30 seconds | WiFi watchdog triggered | WiFi signal too weak or SSID incorrect; move board closer to router temporarily |

### Raspberry Pi Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `setup-rpi.sh` fails at pip install | Missing ARM math libraries | Re-run the script — it installs `libatlas-base-dev` and `libopenblas-dev` first |
| Backend won't start | Mosquitto not running | `sudo systemctl start mosquitto` — the backend requires Mosquitto |
| Backend crashes with `MemoryError` | RPi has <4GB RAM | Use RPi 4 with 4GB+ RAM; the backend is capped at 512MB but numpy/scipy need headroom |
| `csi-hub.local` doesn't resolve | Avahi not running or client doesn't support mDNS | `sudo systemctl restart avahi-daemon`; on Windows, install [Bonjour](https://support.apple.com/bonjour) |
| No tracking data in dashboard | Backend in simulate fallback | Check `journalctl -u wifi-csi-backend` for "falling back to simulator" — means no MQTT data arriving |

### Network Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| ESP32 boards can't reach broker | Wrong IP or network isolation | Verify RPi IP hasn't changed (use static IP/DHCP reservation); check boards and RPi are on the same subnet |
| Intermittent MQTT disconnects | WiFi congestion or weak signal | Move affected boards closer to the router; migrate other devices to 5GHz |
| High latency in dashboard | Network bottleneck | Use Ethernet for the RPi; check `vnstat -l` for bandwidth saturation |

---

## Service Management Reference

```bash
# Start / stop / restart the backend
sudo systemctl start wifi-csi-backend
sudo systemctl stop wifi-csi-backend
sudo systemctl restart wifi-csi-backend

# View live logs
journalctl -u wifi-csi-backend -f

# View recent logs
journalctl -u wifi-csi-backend --since "1 hour ago"

# Check Mosquitto
sudo systemctl status mosquitto
journalctl -u mosquitto -f

# MQTT debugging
mosquitto_sub -t 'csi/#' -v          # All CSI traffic
mosquitto_sub -t 'csi/0/#' -v        # Floor 1 only (firmware floor ID 0)
mosquitto_sub -t 'status/#' -v       # Board heartbeats (JSON)

# Fingerprint backups
/opt/wifi-csi/deploy/backup-fingerprints.sh list
/opt/wifi-csi/deploy/backup-fingerprints.sh backup
/opt/wifi-csi/deploy/backup-fingerprints.sh restore <archive-name>

# System monitoring
htop                                  # CPU / memory
vnstat -l                             # Live network rate
iotop                                 # Disk I/O
```

---

## Updating the System

### Update the Backend

```bash
cd /opt/wifi-csi
sudo git pull
sudo /opt/wifi-csi/venv/bin/pip install -r backend/requirements.txt
sudo systemctl restart wifi-csi-backend
```

Or re-run the setup script (idempotent):

```bash
cd /opt/wifi-csi/deploy
sudo ./setup-rpi.sh
```

### Update Firmware

Re-flash each board with the new firmware. The process is the same as the initial flash — the board's menuconfig settings are preserved in `sdkconfig` (not `sdkconfig.defaults`), so you only need to rebuild and flash:

```bash
cd /path/to/wifi-csi/firmware
. ~/esp/esp-idf/export.sh
idf.py build flash -p /dev/ttyUSB0 monitor
```

---

## Security Notes

The default configuration is designed for **LAN-only deployment**:

- MQTT allows anonymous connections (no authentication)
- The backend listens on all interfaces (`0.0.0.0`)
- No TLS/SSL encryption on MQTT or WebSocket connections

**For production hardening:**

1. Enable Mosquitto password authentication (`password_file` in mosquitto.conf)
2. Add TLS to Mosquitto (see [Mosquitto TLS docs](https://mosquitto.org/man/mosquitto-tls-7.html))
3. Restrict the backend to listen on the LAN interface only
4. Use a firewall (`ufw`) to block external access to ports 1883 and 8000

The backend service runs with security hardening via systemd: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`, and a dedicated `csi` user with no login shell.
