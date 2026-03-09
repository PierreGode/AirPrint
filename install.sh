#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/airprint"
SERVICE_FILE="/etc/systemd/system/airprint.service"
EPAPER_REPO_DIR="/tmp/e-Paper"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo ./install.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/requirements.txt" ]]; then
  echo "requirements.txt not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing apt dependencies..."
apt-get update

choose_package() {
  for pkg in "$@"; do
    if apt-cache policy "$pkg" 2>/dev/null | grep -q 'Candidate:' && \
       ! apt-cache policy "$pkg" 2>/dev/null | grep -q 'Candidate: (none)'; then
      echo "$pkg"
      return 0
    fi
  done
  return 1
}

TIFF_PKG="$(choose_package libtiff5 libtiff6 libtiff-dev || true)"
ATLAS_PKG="$(choose_package libatlas-base-dev libatlas-dev libopenblas-dev || true)"
GPIO_PKG="$(choose_package python3-rpi-lgpio python3-rpi.gpio || true)"
SPIDEV_PKG="$(choose_package python3-spidev || true)"

OPTIONAL_PACKAGES=()
if [[ -n "$TIFF_PKG" ]]; then
  OPTIONAL_PACKAGES+=("$TIFF_PKG")
else
  echo "Warning: no compatible TIFF package found (tried libtiff5/libtiff6/libtiff-dev)."
fi

if [[ -n "$ATLAS_PKG" ]]; then
  OPTIONAL_PACKAGES+=("$ATLAS_PKG")
else
  echo "Warning: no compatible ATLAS package found (tried libatlas-base-dev/libatlas-dev)."
fi

if [[ -n "$GPIO_PKG" ]]; then
  OPTIONAL_PACKAGES+=("$GPIO_PKG")
else
  echo "Warning: no compatible Raspberry Pi GPIO package found (tried python3-rpi-lgpio/python3-rpi.gpio)."
fi

if [[ -n "$SPIDEV_PKG" ]]; then
  OPTIONAL_PACKAGES+=("$SPIDEV_PKG")
else
  echo "Warning: python3-spidev package not found; pip install may be required on this distro."
fi

GPIOZERO_PKG="$(choose_package python3-gpiozero || true)"
if [[ -n "$GPIOZERO_PKG" ]]; then
  OPTIONAL_PACKAGES+=("$GPIOZERO_PKG")
else
  echo "Warning: python3-gpiozero not found; HAT buttons will be unavailable."
fi

apt-get install -y \
  python3 python3-pip python3-dev python3-pil \
  libjpeg-dev zlib1g-dev libopenjp2-7 \
  libgpiod-dev git iw wireless-tools aircrack-ng \
  "${OPTIONAL_PACKAGES[@]}"

echo "Installing Python dependencies from requirements.txt..."
python3 -m pip install --upgrade pip --break-system-packages
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages

echo "Installing Waveshare e-paper Python library..."
if [[ ! -d "$EPAPER_REPO_DIR/.git" ]]; then
  rm -rf "$EPAPER_REPO_DIR"
  git clone --depth=1 --filter=blob:none --sparse https://github.com/waveshareteam/e-Paper.git "$EPAPER_REPO_DIR"
else
  git -C "$EPAPER_REPO_DIR" pull --ff-only
fi

git -C "$EPAPER_REPO_DIR" sparse-checkout set RaspberryPi_JetsonNano/python
python3 -m pip install "$EPAPER_REPO_DIR/RaspberryPi_JetsonNano/python" --break-system-packages

mkdir -p "$PROJECT_DIR"
cp "$SCRIPT_DIR/airprint.py" "$PROJECT_DIR/airprint.py"
cp "$SCRIPT_DIR/web_ui.py" "$PROJECT_DIR/web_ui.py"
chmod +x "$PROJECT_DIR/airprint.py"

cat >/usr/local/bin/airprint-monitor-mode <<'MONITOR_EOF'
#!/usr/bin/env bash
set -euo pipefail

for IFACE in wlan1 wlan2; do
  if ip link show "$IFACE" >/dev/null 2>&1; then
    ip link set "$IFACE" down
    iw "$IFACE" set monitor none || true
    ip link set "$IFACE" up
  fi
done
MONITOR_EOF
chmod +x /usr/local/bin/airprint-monitor-mode

cat >"$SERVICE_FILE" <<SERVICE_EOF
[Unit]
Description=AirPrint WiFi e-paper visualizer
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStartPre=/usr/local/bin/airprint-monitor-mode
ExecStart=/usr/bin/python3 $PROJECT_DIR/airprint.py --interface wlan1 --refresh 30 --scan-time 12 --web-port 5007
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

systemctl daemon-reload
systemctl enable airprint.service
systemctl restart airprint.service

echo "AirPrint installed. Check logs: journalctl -u airprint.service -f"
