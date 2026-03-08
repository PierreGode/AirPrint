# AirPrint

AirPrint turns nearby WiFi activity into a minimalist radar-style map on a Waveshare 7.5" e-paper display.

## Hardware

- Raspberry Pi 4 or Raspberry Pi 5
- Waveshare 7.5" e-paper display (SPI)
- 2x USB WiFi adapters with monitor mode support (`wlan1`, `wlan2`)
- Built-in Pi WiFi (`wlan0`) for SSH/network access

## Wiring (Waveshare 7.5" HAT → Raspberry Pi 40-pin)

| Waveshare Pin | Pi Pin |
|---|---|
| VCC | 3.3V (Pin 1) |
| GND | GND (Pin 6) |
| DIN | MOSI / GPIO10 (Pin 19) |
| CLK | SCLK / GPIO11 (Pin 23) |
| CS | CE0 / GPIO8 (Pin 24) |
| DC | GPIO25 (Pin 22) |
| RST | GPIO17 (Pin 11) |
| BUSY | GPIO24 (Pin 18) |

Enable SPI before install:

```bash
sudo raspi-config
# Interface Options -> SPI -> Enable
```

## Install

```bash
chmod +x install.sh
sudo ./install.sh
```

`install.sh` will:

1. Install apt dependencies.
2. Install Python dependencies from `requirements.txt` with `pip --break-system-packages` (`scapy`, `Pillow`) plus Waveshare EPD lib.
3. Install Raspberry Pi GPIO/SPI bindings from apt (`python3-rpi-lgpio` or `python3-rpi.gpio`, and `python3-spidev`) for better Raspberry Pi 5 compatibility.
4. Copy `airprint.py` to `/opt/airprint`.
5. Create `/usr/local/bin/airprint-monitor-mode` to force `wlan1`/`wlan2` to monitor mode.
6. Install and start `airprint.service`.

## How it works

- Scans WiFi traffic on `wlan1` with Scapy in monitor mode.
- Parses RSSI and channel from 802.11 frames.
- Renders a black/white image with Pillow:
  - center dot = Raspberry Pi
  - surrounding dots = observed transmitters
  - stronger RSSI = closer to center
  - channel influences angular placement
  - recent devices have bigger dots
- Refreshes every 30 seconds.
- Pushes frame to Waveshare EPD.

## Run manually

```bash
sudo python3 airprint.py --interface wlan1 --refresh 30 --scan-time 12 --debug
```

Render to a local image file (debug on non-EPD host):

```bash
sudo python3 airprint.py --interface wlan1 --output frame.png --refresh 30
```

## Service management

```bash
sudo systemctl status airprint.service
sudo journalctl -u airprint.service -f
sudo systemctl restart airprint.service
```

## Notes

- Packet capture requires root.
- If your adapter names differ, edit `/etc/systemd/system/airprint.service` and `/usr/local/bin/airprint-monitor-mode`.
- E-paper updates are intentionally slow and should not be refreshed too frequently.
