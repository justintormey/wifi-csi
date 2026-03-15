#!/usr/bin/env bash
# setup-rpi.sh — Set up Raspberry Pi 4 as WiFi CSI compute hub.
#
# Installs and configures:
#   1. Mosquitto MQTT broker
#   2. Python 3.11 virtual environment with backend deps
#   3. systemd service for auto-start
#   4. Logrotate for log management
#   5. Avahi/mDNS for ESP32 discovery (csi-hub.local)
#   6. Monitoring tools (htop, vnstat, iotop)
#   7. Data directories and backup cron
#
# Usage:
#   curl -sSL <raw-url>/deploy/setup-rpi.sh | sudo bash
#   # or:
#   sudo ./setup-rpi.sh
#
# Tested on: Raspberry Pi OS (Bookworm, 64-bit)

set -euo pipefail

# ── Colours ──
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[CSI]${NC} $*"; }
warn() { echo -e "${RED}[CSI]${NC} $*" >&2; }
step() { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }

# ── Preflight ──
if [ "$(id -u)" -ne 0 ]; then
    warn "This script must be run as root (sudo)."
    exit 1
fi

INSTALL_DIR="/opt/wifi-csi"
DATA_DIR="$INSTALL_DIR/data"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_USER="csi"
REPO_URL="https://github.com/justintormey/wifi-csi.git"

# Where are we running from?  If inside the repo, use local files.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/mosquitto.conf" ]; then
    DEPLOY_SRC="$SCRIPT_DIR"
else
    DEPLOY_SRC=""
fi

# ── 1. System packages ──
step "1/8  Installing system packages"
apt-get update -qq
apt-get install -y -qq \
    mosquitto \
    mosquitto-clients \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    libatlas-base-dev \
    libopenblas-dev \
    avahi-daemon \
    avahi-utils \
    git \
    htop \
    vnstat \
    iotop \
    logrotate \
    jq
log "System packages installed."

# ── 2. Create service user ──
step "2/8  Creating service user"
if id "$SERVICE_USER" &>/dev/null; then
    log "User '$SERVICE_USER' already exists."
else
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    log "Created system user '$SERVICE_USER'."
fi

# ── 3. Clone/update repository ──
step "3/8  Setting up application directory"
if [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing installation..."
    git -C "$INSTALL_DIR" pull --ff-only || warn "Git pull failed — continuing with existing code."
elif [ -n "$DEPLOY_SRC" ] && [ -d "$DEPLOY_SRC/../backend" ]; then
    # Running from within the repo — copy instead of clone
    log "Copying from local repo..."
    mkdir -p "$INSTALL_DIR"
    rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
        "$DEPLOY_SRC/../" "$INSTALL_DIR/"
else
    log "Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# Create data directories
mkdir -p "$DATA_DIR"/{fingerprints,backups,logs}
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
log "Data directories created at $DATA_DIR."

# ── 4. Python virtual environment ──
step "4/8  Setting up Python environment"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    log "Virtual environment created."
fi

"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$INSTALL_DIR/backend/requirements.txt"
log "Python dependencies installed."

# Verify Python version
PY_VER=$("$VENV_DIR/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
log "Python version: $PY_VER"

# ── 5. Mosquitto configuration ──
step "5/8  Configuring Mosquitto MQTT broker"
if [ -n "$DEPLOY_SRC" ] && [ -f "$DEPLOY_SRC/mosquitto.conf" ]; then
    cp "$DEPLOY_SRC/mosquitto.conf" /etc/mosquitto/conf.d/wifi-csi.conf
else
    cp "$INSTALL_DIR/deploy/mosquitto.conf" /etc/mosquitto/conf.d/wifi-csi.conf
fi
systemctl enable mosquitto
systemctl restart mosquitto
log "Mosquitto configured and running."

# Verify MQTT broker
if mosquitto_sub -t '$SYS/broker/version' -C 1 -W 3 2>/dev/null; then
    log "MQTT broker responding."
else
    warn "MQTT broker may not be ready yet — check 'systemctl status mosquitto'."
fi

# ── 6. Avahi/mDNS ──
step "6/8  Configuring mDNS (csi-hub.local)"
cat > /etc/avahi/services/wifi-csi.service <<'AVAHI'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>WiFi CSI Hub</name>
  <service>
    <type>_mqtt._tcp</type>
    <port>1883</port>
    <txt-record>project=wifi-csi</txt-record>
  </service>
  <service>
    <type>_http._tcp</type>
    <port>8000</port>
    <txt-record>project=wifi-csi</txt-record>
  </service>
</service-group>
AVAHI

# Set hostname to csi-hub for easy discovery
CURRENT_HOSTNAME=$(hostnamectl --static 2>/dev/null || cat /etc/hostname)
if [ "$CURRENT_HOSTNAME" != "csi-hub" ]; then
    hostnamectl set-hostname csi-hub 2>/dev/null || echo "csi-hub" > /etc/hostname
    log "Hostname set to 'csi-hub' (accessible as csi-hub.local)."
else
    log "Hostname already set to 'csi-hub'."
fi

systemctl enable avahi-daemon
systemctl restart avahi-daemon
log "mDNS configured — ESP32 boards can find this RPi at csi-hub.local."

# ── 7. systemd service + logrotate ──
step "7/8  Installing systemd service and logrotate"
if [ -n "$DEPLOY_SRC" ]; then
    cp "$DEPLOY_SRC/wifi-csi-backend.service" /etc/systemd/system/
    cp "$DEPLOY_SRC/wifi-csi.logrotate" /etc/logrotate.d/wifi-csi
    cp "$DEPLOY_SRC/backup-fingerprints.sh" "$INSTALL_DIR/deploy/backup-fingerprints.sh" 2>/dev/null || true
else
    cp "$INSTALL_DIR/deploy/wifi-csi-backend.service" /etc/systemd/system/
    cp "$INSTALL_DIR/deploy/wifi-csi.logrotate" /etc/logrotate.d/wifi-csi
fi
chmod +x "$INSTALL_DIR/deploy/backup-fingerprints.sh"

systemctl daemon-reload
systemctl enable wifi-csi-backend
log "Backend service installed (wifi-csi-backend.service)."

# ── 8. Cron for fingerprint backups ──
step "8/8  Setting up backup cron and monitoring"

# Daily fingerprint backup at 3 AM
CRON_LINE="0 3 * * * $INSTALL_DIR/deploy/backup-fingerprints.sh backup >> $DATA_DIR/logs/backup.log 2>&1"
(crontab -u "$SERVICE_USER" -l 2>/dev/null | grep -v "backup-fingerprints" || true; echo "$CRON_LINE") | crontab -u "$SERVICE_USER" -
log "Daily fingerprint backup scheduled (3 AM)."

# Enable vnstat network monitoring
systemctl enable vnstat
systemctl start vnstat
log "Network monitoring (vnstat) enabled."

# ── Set ownership ──
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# ── Summary ──
step "Setup complete!"
echo ""
log "Installation directory:  $INSTALL_DIR"
log "Data directory:          $DATA_DIR"
log "Python venv:             $VENV_DIR"
log "Service user:            $SERVICE_USER"
log "mDNS hostname:           csi-hub.local"
log "MQTT broker:             localhost:1883"
log "Backend API:             http://csi-hub.local:8000"
log "WebSocket:               ws://csi-hub.local:8000/ws/tracking"
echo ""
log "Commands:"
log "  sudo systemctl start wifi-csi-backend   # Start backend"
log "  sudo systemctl status wifi-csi-backend   # Check status"
log "  journalctl -u wifi-csi-backend -f        # Tail logs"
log "  mosquitto_sub -t 'csi/#' -v              # Monitor MQTT traffic"
log "  htop                                     # System resources"
log "  vnstat                                   # Network usage"
log "  $INSTALL_DIR/deploy/backup-fingerprints.sh list  # List backups"
echo ""
warn "Note: Backend is installed but NOT started. Start it after ESP32 boards are flashed:"
warn "  sudo systemctl start wifi-csi-backend"
