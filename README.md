# AirPrint

AirPrint turns nearby WiFi activity into a minimalist radar-style map on a Waveshare e-paper display.

## Hardware

- Raspberry Pi 4 or Raspberry Pi 5
- Waveshare e-paper display (SPI) — supported models:
  - 2.13" (122x250) — `epd2in13`, `epd2in13_V2`, `epd2in13_V3`, `epd2in13_V4`
  - 2.7" (176x264) — `epd2in7`, `epd2in7_V2`
  - 2.9" (128x296) — `epd2in9_V2`
  - 3.7" (280x480) — `epd3in7`
  - 7.5" (800x480) — `epd7in5`, `epd7in5_V2`
- USB WiFi adapter with monitor mode support (`wlan1`)
- Optional: second USB WiFi adapter (`wlan2`) for dual-antenna tracking
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

- Scans WiFi traffic with Scapy in monitor mode.
- **Channel hopping** (`--channel-hop`) — cycles through all 2.4 GHz and 5 GHz channels during each scan to discover devices across the full spectrum.
- Parses RSSI, channel, and SSID from 802.11 beacons and probe requests.
- **OUI vendor lookup** — identifies device manufacturer (Apple, Samsung, Intel, etc.) from the MAC prefix. Embedded database covers ~95% of consumer devices.
- **Probe request sniffing** — captures SSIDs that devices are actively searching for, revealing previously connected networks.
- **RSSI smoothing** — exponential moving average reduces jitter so dots stay stable.
- Renders a black/white image with Pillow:
  - center dot = Raspberry Pi
  - **squares** = access points, **circles** = client devices
  - stronger RSSI = closer to center
  - recent devices have bigger dots
  - small tail lines show movement trend (approaching / receding)
- Automatically sizes the image to match the display resolution.
- Uses partial refresh (no full-screen flash) on supported displays, with periodic full refresh to clear ghosting.
- Refreshes every 30 seconds.
- Pushes frame to Waveshare EPD.

### Dual-antenna tracking

With two WiFi adapters in monitor mode (`--interface2`), AirPrint scans both simultaneously and compares RSSI per device to estimate a left/right bias. As you walk around with the device, RSSI trends over time refine the angular placement of each device on the radar. The longer you move, the more accurate the positions become.

This is not true triangulation — WiFi RSSI is too noisy for precise bearings — but it gives a meaningful spatial spread that improves over time instead of random placement.

## Run manually

```bash
sudo python3 airprint.py --interface wlan1 --refresh 30 --scan-time 12 --debug
```

With channel hopping (recommended — sees 3-5x more devices):

```bash
sudo python3 airprint.py --interface wlan1 --channel-hop --refresh 30 --scan-time 12 --debug
```

With dual-antenna tracking:

```bash
sudo python3 airprint.py --interface wlan1 --interface2 wlan2 --channel-hop --refresh 30 --scan-time 12 --debug
```

Specify your display model explicitly (recommended):

```bash
sudo python3 airprint.py --interface wlan1 --interface2 wlan2 --channel-hop --epd-model epd2in7_V2 --refresh 30 --scan-time 12 --debug
```

Available `--epd-model` values: `auto`, `epd2in13`, `epd2in13_V2`, `epd2in13_V3`, `epd2in13_V4`, `epd2in7`, `epd2in7_V2`, `epd2in9_V2`, `epd3in7`, `epd7in5`, `epd7in5_V2`.

Render to a local image file (debug on non-EPD host):

```bash
sudo python3 airprint.py --interface wlan1 --output frame.png --refresh 30
```

## Web UI

Start with `--web-port` to enable a browser dashboard that mirrors the e-paper display:

```bash
sudo python3 airprint.py --interface wlan1 --epd-model epd2in7_V2 --web-port 5007 --debug
```

Then open `http://<pi-ip>:5007` in a browser. The web UI provides:

- **Live e-paper mirror** — pixel-perfect replica of the display, click to cycle size (medium / large / x-large).
- **EPD model selector** — switch between all supported e-paper displays live without restarting the service.
- **View switching** — toggle between radar, list, and stats views.
- **Controls** — force scan, flip screen, clear display & exit.
- **Settings** — adjust refresh interval, scan duration, device TTL, and full-refresh frequency. Changes apply immediately.
- **Device table** — vendor name, RSSI, channel, SSID/probed networks, and trend arrows. In dual-antenna mode, shows per-antenna RSSI values.

The web UI is enabled by default when installed via `install.sh` (port 5007). No extra dependencies required.

## Buttons (2.7" HAT)

The Waveshare 2.7" e-paper HAT has 4 physical buttons. AirPrint uses them as follows:

| Button | GPIO | Function |
|--------|------|----------|
| KEY1 | 5 | Force an immediate scan (skips the wait timer) |
| KEY2 | 6 | Flip the screen 180 degrees |
| KEY3 | 13 | Cycle view: radar → device list → stats → radar |
| KEY4 | 19 | Clear the display and exit cleanly |

The three views:

- **Radar** — circular map with signal-strength rings. Squares = APs, circles = clients. Tail lines show RSSI trend.
- **List** — sorted by signal strength. Shows vendor name, RSSI, channel, and network name (SSID for APs, probed SSID for clients).
- **Stats** — device counts, RSSI min/avg/max, top vendors, top channels, and a device count sparkline showing activity over time.

Buttons require `gpiozero` (installed by `install.sh`). If the library is not available, AirPrint runs normally without button support.

## Service management

```bash
sudo systemctl status airprint.service
sudo journalctl -u airprint.service -f
sudo systemctl restart airprint.service
```

## Notes

- Packet capture requires root.
- Angular placement starts as a visual spread based on MAC hash. With dual-antenna mode and movement, angles refine over time but are still approximate — this is not a precision direction finder.
- If your adapter names differ, edit `/etc/systemd/system/airprint.service` and `/usr/local/bin/airprint-monitor-mode`.
- E-paper updates are intentionally slow and should not be refreshed too frequently.
- Press Ctrl+C once to stop gracefully, twice to force quit.
