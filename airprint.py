#!/usr/bin/env python3
"""AirPrint: WiFi signal visualizer for Raspberry Pi + Waveshare e-paper."""

from __future__ import annotations

import argparse
import hashlib
import logging
import math
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

from PIL import Image, ImageDraw, ImageFont
from scapy.all import Dot11, Dot11Beacon, Dot11Elt, RadioTap, sniff  # type: ignore


@dataclass
class DeviceObservation:
    """State for a discovered WiFi transmitter."""

    mac: str
    channel: int
    rssi: int
    last_seen: float
    kind: str


class AirPrint:
    def __init__(
        self,
        interface: str,
        refresh_seconds: int,
        scan_seconds: int,
        state_ttl_seconds: int,
        output_path: Optional[Path],
        epd_model: str,
    ) -> None:
        self.interface = interface
        self.refresh_seconds = refresh_seconds
        self.scan_seconds = scan_seconds
        self.state_ttl_seconds = state_ttl_seconds
        self.output_path = output_path
        self.epd_model = epd_model
        self.devices: Dict[str, DeviceObservation] = {}
        self.running = True
        self.epd: Optional[object] = None
        self._force_quit = False

    def stop(self, *_: object) -> None:
        if not self.running:
            logging.info("Force quit")
            sys.exit(1)
        logging.info("Shutting down AirPrint loop")
        self.running = False

    def scan_wifi(self) -> Dict[str, DeviceObservation]:
        """Scan for 802.11 frames and return latest state by MAC."""
        found: Dict[str, DeviceObservation] = {}

        def process_packet(packet: object) -> None:
            if not packet.haslayer(Dot11):
                return

            dot11 = packet[Dot11]
            src = dot11.addr2
            if not src:
                return

            rssi = self.extract_rssi(packet)
            if rssi is None:
                return

            channel = self.extract_channel(packet)
            if channel is None:
                channel = 1

            kind = "device"
            if packet.haslayer(Dot11Beacon):
                kind = "ap"

            found[src] = DeviceObservation(
                mac=src,
                channel=channel,
                rssi=rssi,
                last_seen=time.time(),
                kind=kind,
            )

        logging.info("Scanning %s for %ss", self.interface, self.scan_seconds)
        sniff(
            iface=self.interface,
            timeout=self.scan_seconds,
            prn=process_packet,
            store=False,
            monitor=True,
        )
        return found

    @staticmethod
    def extract_rssi(packet: object) -> Optional[int]:
        if not packet.haslayer(RadioTap):
            return None

        radiotap = packet[RadioTap]
        value = getattr(radiotap, "dBm_AntSignal", None)
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def extract_channel(packet: object) -> Optional[int]:
        if packet.haslayer(Dot11Elt):
            elt = packet.getlayer(Dot11Elt)
            while elt is not None:
                if elt.ID == 3 and elt.info:
                    return int(elt.info[0])
                elt = elt.payload.getlayer(Dot11Elt)

        if packet.haslayer(RadioTap):
            channel_freq = getattr(packet[RadioTap], "ChannelFrequency", None)
            if channel_freq:
                return AirPrint.freq_to_channel(int(channel_freq))
        return None

    @staticmethod
    def freq_to_channel(freq_mhz: int) -> Optional[int]:
        if 2412 <= freq_mhz <= 2472:
            return (freq_mhz - 2407) // 5
        if freq_mhz == 2484:
            return 14
        if 5000 <= freq_mhz <= 5895:
            return (freq_mhz - 5000) // 5
        return None

    def merge_devices(self, observed: Dict[str, DeviceObservation]) -> None:
        now = time.time()
        for mac, data in observed.items():
            self.devices[mac] = data

        stale = [mac for mac, d in self.devices.items() if now - d.last_seen > self.state_ttl_seconds]
        for mac in stale:
            del self.devices[mac]

    def render_image(self, width: int = 0, height: int = 0) -> Image.Image:
        if width == 0 or height == 0:
            width, height = self.get_display_size()
        image = Image.new("1", (width, height), 255)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        center = (width // 2, height // 2)
        max_radius = min(width, height) * 0.45

        self.draw_rings(draw, center, max_radius)
        draw.ellipse((center[0] - 6, center[1] - 6, center[0] + 6, center[1] + 6), fill=0)

        now = time.time()
        for device in self.devices.values():
            angle = self.device_angle(device)
            distance = self.rssi_to_radius(device.rssi, max_radius)
            x = center[0] + math.cos(angle) * distance
            y = center[1] + math.sin(angle) * distance
            radius = self.recency_radius(now - device.last_seen)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=0)

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        draw.text((12, height - 28), stamp, fill=0, font=font)
        draw.text((width - 150, height - 28), f"devices: {len(self.devices)}", fill=0, font=font)
        return image

    @staticmethod
    def draw_rings(draw: ImageDraw.ImageDraw, center: tuple[int, int], max_radius: float) -> None:
        for frac in (0.25, 0.5, 0.75, 1.0):
            radius = int(max_radius * frac)
            draw.ellipse(
                (
                    center[0] - radius,
                    center[1] - radius,
                    center[0] + radius,
                    center[1] + radius,
                ),
                outline=0,
                width=1,
            )

    @staticmethod
    def rssi_to_radius(rssi: int, max_radius: float) -> float:
        # map RSSI from [-95, -30] dBm to [max_radius, 12] px
        clamped = max(-95, min(-30, rssi))
        norm = (clamped + 95) / 65
        return max_radius - (norm * (max_radius - 12))

    @staticmethod
    def recency_radius(age_seconds: float) -> int:
        if age_seconds < 45:
            return 5
        if age_seconds < 90:
            return 4
        if age_seconds < 180:
            return 3
        return 2

    @staticmethod
    def hash_to_unit(mac: str) -> float:
        h = hashlib.sha256(mac.encode("utf-8")).hexdigest()[:8]
        return int(h, 16) / 0xFFFFFFFF

    def device_angle(self, device: DeviceObservation) -> float:
        # Channel lays out the major sector; MAC hash spreads points in-sector.
        base = (device.channel % 14) / 14
        jitter = self.hash_to_unit(device.mac) * (1 / 14)
        return (base + jitter) * 2 * math.pi

    def get_display_size(self) -> tuple[int, int]:
        """Return (width, height) for the active EPD driver."""
        if self.epd is not None:
            w = getattr(self.epd, "width", 800)
            h = getattr(self.epd, "height", 480)
            return (w, h)
        if self.epd_model in self.EPD_DRIVERS:
            _, w, h = self.EPD_DRIVERS[self.epd_model]
            return (w, h)
        return (800, 480)

    def display_image(self, image: Image.Image) -> None:
        if self.output_path:
            image.save(self.output_path)
            logging.info("Saved rendered frame to %s", self.output_path)
            return

        if self.epd is None:
            self.epd = self.create_epd()
            self.epd.init()

        self.epd.display(self.epd.getbuffer(image))

    # Map of known driver names to (module_name, width, height)
    EPD_DRIVERS = {
        "epd2in7": ("epd2in7", 176, 264),
        "epd2in7_V2": ("epd2in7_V2", 176, 264),
        "epd7in5_V2": ("epd7in5_V2", 800, 480),
        "epd7in5": ("epd7in5", 800, 480),
    }

    # Order for auto-detection: try each driver, first successful init wins
    AUTO_DETECT_ORDER = ["epd2in7_V2", "epd2in7", "epd7in5_V2", "epd7in5"]

    def create_epd(self) -> object:
        import importlib

        if self.epd_model != "auto":
            if self.epd_model not in self.EPD_DRIVERS:
                raise RuntimeError(f"Unknown EPD model: {self.epd_model}")
            module_name = self.EPD_DRIVERS[self.epd_model][0]
            mod = importlib.import_module(f"waveshare_epd.{module_name}")
            logging.debug("Using e-paper driver %s", module_name)
            return mod.EPD()

        # Auto-detect: try drivers in order
        for name in self.AUTO_DETECT_ORDER:
            module_name = self.EPD_DRIVERS[name][0]
            try:
                mod = importlib.import_module(f"waveshare_epd.{module_name}")
                epd = mod.EPD()
                epd.init()
                epd.sleep()
                logging.debug("Auto-selected e-paper driver %s", module_name)
                return epd
            except Exception:
                logging.debug("Driver %s failed, trying next", module_name)
                continue
        raise RuntimeError("No compatible e-paper driver found")

    def shutdown_display(self) -> None:
        if self.epd is None:
            return

        try:
            self.epd.sleep()
        except Exception as exc:
            logging.debug("Failed to put e-paper into sleep mode: %s", exc)

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        try:
            while self.running:
                started = time.time()
                try:
                    observed = self.scan_wifi()
                    self.merge_devices(observed)
                    frame = self.render_image()
                    self.display_image(frame)
                    logging.info("Frame rendered with %d active devices", len(self.devices))
                except Exception as exc:
                    logging.exception("AirPrint cycle failed: %s", exc)

                elapsed = time.time() - started
                sleep_seconds = max(1, self.refresh_seconds - int(elapsed))
                # Sleep in short intervals so Ctrl+C is responsive
                end = time.time() + sleep_seconds
                while self.running and time.time() < end:
                    time.sleep(0.5)
        finally:
            self.shutdown_display()


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AirPrint WiFi visualizer")
    parser.add_argument("--interface", default="wlan1", help="Monitor-mode interface")
    parser.add_argument("--refresh", type=int, default=30, help="Refresh interval in seconds")
    parser.add_argument("--scan-time", type=int, default=12, help="Packet sniff duration per cycle")
    parser.add_argument("--state-ttl", type=int, default=300, help="How long to keep unseen devices")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Save frame to file instead of writing to the EPD",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logs")
    parser.add_argument(
        "--epd-model",
        choices=("auto", "epd2in7", "epd2in7_V2", "epd7in5_V2", "epd7in5"),
        default="auto",
        help="Waveshare e-paper driver (e.g. epd2in7 for 2.7in, epd7in5_V2 for 7.5in v2)",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    app = AirPrint(
        interface=args.interface,
        refresh_seconds=args.refresh,
        scan_seconds=args.scan_time,
        state_ttl_seconds=args.state_ttl,
        output_path=args.output,
        epd_model=args.epd_model,
    )
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
