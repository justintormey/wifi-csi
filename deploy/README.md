# Raspberry Pi Deployment

Set up a Raspberry Pi 4 as the WiFi CSI compute hub (MQTT broker + Python backend).

## Quick Start

```bash
# On the Raspberry Pi (Raspberry Pi OS Bookworm, 64-bit):
git clone https://github.com/justintormey/wifi-csi.git
cd wifi-csi/deploy
sudo ./setup-rpi.sh
```

The script installs everything non-destructively and is safe to re-run.

## What Gets Installed

| Component | Details |
|-----------|---------|
| **Mosquitto** | MQTT broker on port 1883, configured for high-throughput CSI data |
| **Python venv** | `/opt/wifi-csi/venv` with all backend dependencies |
| **systemd service** | `wifi-csi-backend.service` — auto-starts after Mosquitto, restarts on failure |
| **Avahi/mDNS** | Hostname set to `csi-hub` — ESP32 boards connect to `csi-hub.local` |
| **Logrotate** | Daily rotation of `/opt/wifi-csi/data/logs/*.log`, 7-day retention |
| **Monitoring** | htop, vnstat (network), iotop (disk I/O) |
| **Backup cron** | Daily fingerprint DB backup at 3 AM, keeps last 10 |

## Directory Layout

```
/opt/wifi-csi/
├── backend/            # Python backend (cloned from repo)
├── deploy/             # These deployment files
├── venv/               # Python virtual environment
└── data/
    ├── fingerprints/   # Calibration fingerprint .npz files
    ├── backups/        # Timestamped fingerprint backups
    └── logs/           # Application logs
```

## Service Management

```bash
sudo systemctl start wifi-csi-backend    # Start
sudo systemctl stop wifi-csi-backend     # Stop
sudo systemctl restart wifi-csi-backend  # Restart
sudo systemctl status wifi-csi-backend   # Status
journalctl -u wifi-csi-backend -f        # Tail logs
journalctl -u wifi-csi-backend --since "1 hour ago"  # Recent logs
```

## MQTT Debugging

```bash
# Watch all CSI traffic
mosquitto_sub -t 'csi/#' -v

# Watch a specific floor
mosquitto_sub -t 'csi/1/#' -v

# Publish a test message
mosquitto_pub -t 'csi/test' -m 'hello'

# Check broker status
mosquitto_sub -t '$SYS/#' -v -C 5
```

## Network / mDNS

The RPi advertises itself as `csi-hub.local` via Avahi. ESP32 firmware should resolve this hostname for MQTT broker and UDP target addresses.

```bash
# Verify mDNS is working (from another machine on the LAN)
ping csi-hub.local
avahi-browse -art   # List all mDNS services on the network
```

## Fingerprint Backup & Restore

```bash
# Manual backup
/opt/wifi-csi/deploy/backup-fingerprints.sh backup

# List backups
/opt/wifi-csi/deploy/backup-fingerprints.sh list

# Restore (prompts for confirmation, auto-backs-up current first)
/opt/wifi-csi/deploy/backup-fingerprints.sh restore fingerprints-20260315-030000.tar.gz
```

Automated daily backups run at 3 AM via cron (`crontab -u csi -l` to verify).

## Performance Monitoring

```bash
htop                  # CPU, memory, process tree
vnstat                # Network traffic summary
vnstat -l             # Live network rate
iotop                 # Disk I/O per process
journalctl --disk-usage  # Journal storage used
```

The systemd service enforces resource limits: 512 MB RAM max, 80% CPU quota.

## Updating

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

## Security Notes

- MQTT allows anonymous connections (LAN-only deployment).
- The backend service runs as a dedicated `csi` user with `NoNewPrivileges`, `ProtectSystem=strict`, and `ProtectHome=true`.
- Only `/opt/wifi-csi/data` is writable by the service.
- For production hardening, add Mosquitto TLS and password authentication.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Backend won't start | `journalctl -u wifi-csi-backend -e` for errors |
| No MQTT data | `mosquitto_sub -t 'csi/#' -v` — if empty, ESP32 boards aren't publishing |
| ESP32 can't find broker | Verify `ping csi-hub.local` from ESP32 network; check Avahi: `systemctl status avahi-daemon` |
| High CPU | `htop` — check if particle filter or CWT is dominating; may need to reduce sample rate |
| Out of memory | Backend is capped at 512 MB; check for fingerprint DB size or memory leaks |
