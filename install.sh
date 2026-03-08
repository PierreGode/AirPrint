#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/airprint"
SERVICE_FILE="/etc/systemd/system/airprint.service"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo ./install.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apt-get update
apt-get install -y \
  python3 python3-pip python3-pil python3-spidev python3-dev \
  libatlas-base-dev git iw wireless-tools aircrack-ng

python3 -m pip install --upgrade pip
python3 -m pip install --upgrade scapy RPi.GPIO spidev

if [[ ! -d /tmp/e-Paper ]]; then
  git clone https://github.com/waveshare/e-Paper.git /tmp/e-Paper
fi
python3 -m pip install /tmp/e-Paper/RaspberryPi_JetsonNano/python/

mkdir -p "$PROJECT_DIR"
cp "$SCRIPT_DIR/airprint.py" "$PROJECT_DIR/airprint.py"
chmod +x "$PROJECT_DIR/airprint.py"

cat >/usr/local/bin/airprint-monitor-mode <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

for IFACE in wlan1 wlan2; do
  if ip link show "$IFACE" >/dev/null 2>&1; then
    ip link set "$IFACE" down
    iw "$IFACE" set monitor none || true
    ip link set "$IFACE" up
  fi
done
EOF
chmod +x /usr/local/bin/airprint-monitor-mode

cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=AirPrint WiFi e-paper visualizer
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStartPre=/usr/local/bin/airprint-monitor-mode
ExecStart=/usr/bin/python3 $PROJECT_DIR/airprint.py --interface wlan1 --refresh 30 --scan-time 12
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable airprint.service
systemctl restart airprint.service

echo "AirPrint installed. Check logs: journalctl -u airprint.service -f"
