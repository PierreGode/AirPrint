# AirPrint

AirPrint turns nearby WiFi activity into a minimalist radar-style map on a Waveshare e-paper display.

## Hardware

- Raspberry Pi 4 or Raspberry Pi 5
- Waveshare e-paper display (SPI) — supported models:
  - 2.7" (176x264) — `epd2in7`, `epd2in7_V2`
  - 7.5" (800x480) — `epd7in5`, `epd7in5_V2`
- USB WiFi adapter with monitor mode support (`wlan1`)
- Built-in Pi WiFi (`wlan0`) for SSH/network access

## Wiring (Waveshare e-paper HAT → Raspberry Pi 40-pin)

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
  - each device gets a stable angle based on its MAC address
  - recent devices have bigger dots
- Automatically sizes the image to match the display resolution.
- Refreshes every 30 seconds.
- Pushes frame to Waveshare EPD.

## Run manually

```bash
sudo python3 airprint.py --interface wlan1 --refresh 30 --scan-time 12 --debug
```

Specify your display model explicitly (recommended):

```bash
sudo python3 airprint.py --interface wlan1 --epd-model epd2in7_V2 --refresh 30 --scan-time 12 --debug
```

Available `--epd-model` values: `auto` (default), `epd2in7`, `epd2in7_V2`, `epd7in5`, `epd7in5_V2`.

Render to a local image file (debug on non-EPD host):

```bash
sudo python3 airprint.py --interface wlan1 --output frame.png --refresh 30
```

## Buttons (2.7" HAT)

The Waveshare 2.7" e-paper HAT has 4 physical buttons. AirPrint uses them as follows:

| Button | GPIO | Function |
|--------|------|----------|
| KEY1 | 5 | Force an immediate scan (skips the wait timer) |
| KEY2 | 6 | Flip the screen 180 degrees |
| KEY3 | 13 | Cycle view: radar → device list → stats → radar |
| KEY4 | 19 | Clear the display and exit cleanly |

The three views:

- **Radar** — default circular map with signal-strength rings.
- **List** — sorted table of MAC addresses, RSSI, and channel. APs are marked with `*`.
- **Stats** — summary: total devices, AP/client count, RSSI min/avg/max, busiest channels.

Buttons require `gpiozero` (installed by `install.sh`). If the library is not available, AirPrint runs normally without button support.

## Service management

```bash
sudo systemctl status airprint.service
sudo journalctl -u airprint.service -f
sudo systemctl restart airprint.service
```

## Notes

- Packet capture requires root.
- WiFi cannot detect the physical direction of devices — angular placement on the radar is a stable visual spread, not a real bearing.
- If your adapter names differ, edit `/etc/systemd/system/airprint.service` and `/usr/local/bin/airprint-monitor-mode`.
- E-paper updates are intentionally slow and should not be refreshed too frequently.
- Press Ctrl+C once to stop gracefully, twice to force quit.
