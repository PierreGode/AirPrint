#!/usr/bin/env python3
"""AirPrint: WiFi signal visualizer for Raspberry Pi + Waveshare e-paper."""

from __future__ import annotations

import argparse
import collections
import hashlib
import inspect
import logging
import math
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


from PIL import Image, ImageDraw, ImageFont
from scapy.all import Dot11, Dot11Beacon, Dot11Elt, RadioTap, sniff  # type: ignore


VIEW_RADAR = 0
VIEW_LIST = 1
VIEW_STATS = 2
VIEW_COUNT = 3

# How many RSSI history samples to keep per device per antenna
RSSI_HISTORY_LEN = 12


@dataclass
class DeviceObservation:
    """State for a discovered WiFi transmitter."""

    mac: str
    channel: int
    rssi: int
    last_seen: float
    kind: str
    # Dual-antenna RSSI: None if second antenna not available
    rssi_ant1: Optional[int] = None
    rssi_ant2: Optional[int] = None


@dataclass
class DeviceTrack:
    """Accumulated tracking data for a single device."""

    mac: str
    channel: int
    rssi: int
    last_seen: float
    kind: str
    # Dual-antenna
    rssi_ant1: Optional[int] = None
    rssi_ant2: Optional[int] = None
    # RSSI history for motion-based angle estimation
    rssi_history: List[int] = field(default_factory=list)
    # Estimated angle (radians), starts from MAC hash, refines over time
    angle: float = 0.0
    # Confidence in the angle estimate (0.0 = hash only, 1.0 = high confidence)
    angle_confidence: float = 0.0
    # Accumulated angular nudge from antenna delta
    antenna_nudge: float = 0.0
    # RSSI trend: positive = getting closer, negative = getting further
    rssi_trend: float = 0.0


class ButtonListener:
    """Listen to the 4 GPIO buttons on the Waveshare 2.7" e-paper HAT."""

    KEY_PINS = {
        "key1": 5,
        "key2": 6,
        "key3": 13,
        "key4": 19,
    }

    def __init__(self, app: AirPrint) -> None:
        self.app = app
        self._buttons: list[object] = []

    def start(self) -> None:
        try:
            from gpiozero import Button  # type: ignore
        except ImportError:
            logging.warning("gpiozero not available — buttons disabled")
            return

        handlers = {
            "key1": self._on_key1,
            "key2": self._on_key2,
            "key3": self._on_key3,
            "key4": self._on_key4,
        }

        for name, pin in self.KEY_PINS.items():
            try:
                btn = Button(pin, pull_up=True, bounce_time=0.3)
                btn.when_pressed = handlers[name]
                self._buttons.append(btn)
                logging.debug("Button %s (GPIO %d) registered", name, pin)
            except Exception as exc:
                logging.warning("Failed to register button %s: %s", name, exc)

    def _on_key1(self) -> None:
        logging.info("KEY1: Force scan")
        self.app.force_scan = True

    def _on_key2(self) -> None:
        logging.info("KEY2: Flip screen")
        self.app.screen_flipped = not self.app.screen_flipped
        self.app.redraw_needed = True

    def _on_key3(self) -> None:
        self.app.current_view = (self.app.current_view + 1) % VIEW_COUNT
        logging.info("KEY3: View -> %s", ["radar", "list", "stats"][self.app.current_view])
        self.app.redraw_needed = True

    def _on_key4(self) -> None:
        logging.info("KEY4: Clear display & exit")
        self.app.clear_and_exit = True
        self.app.running = False


class AirPrint:
    def __init__(
        self,
        interface: str,
        interface2: Optional[str],
        refresh_seconds: int,
        scan_seconds: int,
        state_ttl_seconds: int,
        output_path: Optional[Path],
        epd_model: str,
    ) -> None:
        self.interface = interface
        self.interface2 = interface2
        self.refresh_seconds = refresh_seconds
        self.scan_seconds = scan_seconds
        self.state_ttl_seconds = state_ttl_seconds
        self.output_path = output_path
        self.epd_model = epd_model
        self.devices: Dict[str, DeviceObservation] = {}
        self.tracks: Dict[str, DeviceTrack] = {}
        self.running = True
        self.epd: Optional[object] = None
        self._partial_supported = False
        self._frame_count = 0
        self._full_refresh_interval = 10
        # Button state
        self.force_scan = False
        self.screen_flipped = False
        self.redraw_needed = False
        self.current_view = VIEW_RADAR
        self.clear_and_exit = False
        self.last_frame: Optional[Image.Image] = None
        self.last_frame_time: float = 0

    def stop(self, *_: object) -> None:
        if not self.running:
            logging.info("Force quit")
            sys.exit(1)
        logging.info("Shutting down AirPrint loop")
        self.running = False

    # ---- WiFi scanning ----

    def _sniff_interface(self, iface: str) -> Dict[str, DeviceObservation]:
        """Sniff a single interface, return observations keyed by MAC."""
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
            kind = "ap" if packet.haslayer(Dot11Beacon) else "device"
            prev = found.get(src)
            if prev is None or rssi > prev.rssi:
                found[src] = DeviceObservation(
                    mac=src, channel=channel, rssi=rssi,
                    last_seen=time.time(), kind=kind,
                )

        sniff(
            iface=iface, timeout=self.scan_seconds,
            prn=process_packet, store=False, monitor=True,
        )
        return found

    def scan_wifi(self) -> Dict[str, DeviceObservation]:
        """Scan one or two interfaces and merge results."""
        logging.info("Scanning %s for %ss", self.interface, self.scan_seconds)

        if self.interface2:
            # Scan both antennas in parallel
            result2: Dict[str, DeviceObservation] = {}

            def scan_ant2() -> None:
                nonlocal result2
                result2 = self._sniff_interface(self.interface2)

            t = threading.Thread(target=scan_ant2, daemon=True)
            t.start()
            result1 = self._sniff_interface(self.interface)
            t.join(timeout=self.scan_seconds + 5)

            # Merge: use antenna 1 as primary, add antenna 2 RSSI
            merged: Dict[str, DeviceObservation] = {}
            all_macs = set(result1.keys()) | set(result2.keys())
            for mac in all_macs:
                obs1 = result1.get(mac)
                obs2 = result2.get(mac)
                if obs1 and obs2:
                    obs1.rssi_ant1 = obs1.rssi
                    obs1.rssi_ant2 = obs2.rssi
                    merged[mac] = obs1
                elif obs1:
                    obs1.rssi_ant1 = obs1.rssi
                    merged[mac] = obs1
                elif obs2:
                    obs2.rssi_ant2 = obs2.rssi
                    obs2.rssi_ant1 = None
                    merged[mac] = obs2

            logging.debug(
                "Dual-antenna scan: %d on ant1, %d on ant2, %d merged",
                len(result1), len(result2), len(merged),
            )
            return merged
        else:
            return self._sniff_interface(self.interface)

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

    # ---- Device tracking with angle estimation ----

    def merge_devices(self, observed: Dict[str, DeviceObservation]) -> None:
        now = time.time()
        for mac, obs in observed.items():
            self.devices[mac] = obs
            self._update_track(mac, obs)

        # Expire stale
        stale = [m for m, d in self.devices.items() if now - d.last_seen > self.state_ttl_seconds]
        for mac in stale:
            del self.devices[mac]
            self.tracks.pop(mac, None)

    def _update_track(self, mac: str, obs: DeviceObservation) -> None:
        """Update or create a DeviceTrack with new observation data."""
        track = self.tracks.get(mac)
        if track is None:
            # New device: start with MAC-hash angle
            track = DeviceTrack(
                mac=mac, channel=obs.channel, rssi=obs.rssi,
                last_seen=obs.last_seen, kind=obs.kind,
                angle=self.hash_to_unit(mac) * 2 * math.pi,
            )
            self.tracks[mac] = track

        track.rssi = obs.rssi
        track.last_seen = obs.last_seen
        track.channel = obs.channel
        track.kind = obs.kind
        track.rssi_ant1 = obs.rssi_ant1
        track.rssi_ant2 = obs.rssi_ant2

        # Append to RSSI history
        track.rssi_history.append(obs.rssi)
        if len(track.rssi_history) > RSSI_HISTORY_LEN:
            track.rssi_history = track.rssi_history[-RSSI_HISTORY_LEN:]

        # Compute RSSI trend (positive = getting closer)
        track.rssi_trend = self._compute_trend(track.rssi_history)

        # Antenna delta nudge: if we have both antennas, bias the angle
        if obs.rssi_ant1 is not None and obs.rssi_ant2 is not None:
            delta = obs.rssi_ant1 - obs.rssi_ant2  # positive = closer to ant1
            # Nudge angle toward 0 (ant1 side) or pi (ant2 side)
            # Use a small factor — RSSI is noisy
            nudge = delta * 0.02  # ~1 degree per dBm difference
            # Exponential moving average to smooth
            track.antenna_nudge = track.antenna_nudge * 0.7 + nudge * 0.3
            # Apply nudge to angle
            track.angle += track.antenna_nudge * 0.1
            track.angle_confidence = min(1.0, track.angle_confidence + 0.05)

        # Motion-based refinement: devices getting closer should drift
        # toward the "forward" direction, devices getting further drift away.
        # Since we don't know which way we're facing, we use relative motion
        # between devices to separate them angularly.
        if len(track.rssi_history) >= 3 and track.rssi_trend != 0:
            # Approaching devices cluster together, receding ones push apart
            # This creates natural grouping over time
            trend_nudge = track.rssi_trend * 0.005
            track.angle += trend_nudge
            track.angle_confidence = min(1.0, track.angle_confidence + 0.02)

        # Normalize angle to [0, 2*pi)
        track.angle = track.angle % (2 * math.pi)

    @staticmethod
    def _compute_trend(history: List[int]) -> float:
        """Compute linear RSSI trend. Positive = signal getting stronger."""
        n = len(history)
        if n < 3:
            return 0.0
        # Simple linear regression slope
        sx = sy = sxx = sxy = 0.0
        for i, val in enumerate(history):
            sx += i
            sy += val
            sxx += i * i
            sxy += i * val
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-9:
            return 0.0
        return (n * sxy - sx * sy) / denom

    # ---- View renderers ----

    def render_frame(self) -> Image.Image:
        width, height = self.get_display_size()
        if self.current_view == VIEW_LIST:
            image = self.render_list(width, height)
        elif self.current_view == VIEW_STATS:
            image = self.render_stats(width, height)
        else:
            image = self.render_radar(width, height)

        if self.screen_flipped:
            image = image.rotate(180)
        self.last_frame = image
        self.last_frame_time = time.time()
        return image

    def render_radar(self, width: int, height: int) -> Image.Image:
        image = Image.new("1", (width, height), 255)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        center = (width // 2, height // 2)
        max_radius = min(width, height) * 0.45

        self.draw_rings(draw, center, max_radius)
        draw.ellipse((center[0] - 6, center[1] - 6, center[0] + 6, center[1] + 6), fill=0)

        now = time.time()
        for mac, device in self.devices.items():
            track = self.tracks.get(mac)
            if track:
                angle = track.angle
            else:
                angle = self.hash_to_unit(mac) * 2 * math.pi
            distance = self.rssi_to_radius(device.rssi, max_radius)
            x = center[0] + math.cos(angle) * distance
            y = center[1] + math.sin(angle) * distance
            dot_r = self.recency_radius(now - device.last_seen)

            # Draw trend indicator: small tail showing direction of movement
            if track and abs(track.rssi_trend) > 0.3:
                tail_len = min(8, abs(track.rssi_trend) * 3)
                # Trend toward center = getting closer, away = getting further
                if track.rssi_trend > 0:
                    # Getting closer: tail points outward (came from there)
                    tx = x + math.cos(angle) * tail_len
                    ty = y + math.sin(angle) * tail_len
                else:
                    # Getting further: tail points inward (moving away)
                    tx = x - math.cos(angle) * tail_len
                    ty = y - math.sin(angle) * tail_len
                draw.line((int(x), int(y), int(tx), int(ty)), fill=0, width=1)

            draw.ellipse((x - dot_r, y - dot_r, x + dot_r, y + dot_r), fill=0)

        stamp = datetime.now().strftime("%H:%M:%S")
        draw.text((4, height - 14), stamp, fill=0, font=font)
        info = f"n={len(self.devices)}"
        if self.interface2:
            info += " 2ant"
        draw.text((width - 90, height - 14), info, fill=0, font=font)
        return image

    def render_list(self, width: int, height: int) -> Image.Image:
        image = Image.new("1", (width, height), 255)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        if self.interface2:
            draw.text((4, 2), "MAC       RSSI A1  A2 Ch", fill=0, font=font)
        else:
            draw.text((4, 2), "MAC              RSSI Ch", fill=0, font=font)
        draw.line((0, 14, width, 14), fill=0)

        sorted_devs = sorted(self.devices.values(), key=lambda d: d.rssi, reverse=True)
        y = 18
        line_h = 12
        max_lines = (height - 32) // line_h
        for dev in sorted_devs[:max_lines]:
            short_mac = dev.mac[-8:]
            kind_marker = "*" if dev.kind == "ap" else " "
            if self.interface2 and dev.rssi_ant1 is not None:
                a1 = f"{dev.rssi_ant1:>4}" if dev.rssi_ant1 is not None else "   -"
                a2 = f"{dev.rssi_ant2:>4}" if dev.rssi_ant2 is not None else "   -"
                line = f"{short_mac}{kind_marker}{a1} {a2} {dev.channel:>2}"
            else:
                line = f"{short_mac}{kind_marker} {dev.rssi:>4}  {dev.channel:>2}"
            draw.text((4, y), line, fill=0, font=font)
            y += line_h

        stamp = datetime.now().strftime("%H:%M:%S")
        draw.text((4, height - 14), stamp, fill=0, font=font)
        draw.text((width - 80, height - 14), f"n={len(self.devices)}", fill=0, font=font)
        return image

    def render_stats(self, width: int, height: int) -> Image.Image:
        image = Image.new("1", (width, height), 255)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        total = len(self.devices)
        aps = sum(1 for d in self.devices.values() if d.kind == "ap")
        clients = total - aps

        channels: Dict[int, int] = {}
        rssi_values: list[int] = []
        for dev in self.devices.values():
            channels[dev.channel] = channels.get(dev.channel, 0) + 1
            rssi_values.append(dev.rssi)

        y = 4
        line_h = 14
        draw.text((4, y), f"Total:   {total}", fill=0, font=font); y += line_h
        draw.text((4, y), f"APs:     {aps}", fill=0, font=font); y += line_h
        draw.text((4, y), f"Clients: {clients}", fill=0, font=font); y += line_h

        if rssi_values:
            avg_rssi = sum(rssi_values) // len(rssi_values)
            best = max(rssi_values)
            worst = min(rssi_values)
            draw.text((4, y), f"RSSI avg:{avg_rssi} dBm", fill=0, font=font); y += line_h
            draw.text((4, y), f"  best:  {best} dBm", fill=0, font=font); y += line_h
            draw.text((4, y), f"  worst: {worst} dBm", fill=0, font=font); y += line_h

        if self.interface2:
            tracked = sum(1 for t in self.tracks.values() if t.angle_confidence > 0.1)
            draw.text((4, y), f"Tracked: {tracked}", fill=0, font=font); y += line_h

        y += 4
        if channels:
            top = sorted(channels.items(), key=lambda kv: kv[1], reverse=True)[:5]
            draw.text((4, y), "Top channels:", fill=0, font=font); y += line_h
            for ch, count in top:
                draw.text((4, y), f"  ch {ch:>3}: {count}", fill=0, font=font); y += line_h

        stamp = datetime.now().strftime("%H:%M:%S")
        draw.text((4, height - 14), stamp, fill=0, font=font)
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

    def get_display_size(self) -> tuple[int, int]:
        if self.epd is not None:
            w = getattr(self.epd, "width", 800)
            h = getattr(self.epd, "height", 480)
            return (w, h)
        if self.epd_model in self.EPD_DRIVERS:
            _, w, h = self.EPD_DRIVERS[self.epd_model]
            return (w, h)
        return (800, 480)

    def _init_epd(self) -> None:
        self.epd = self.create_epd()
        self.epd.init()
        self.epd.Clear(0xFF)
        self._partial_supported = (
            hasattr(self.epd, "display_Partial")
            or hasattr(self.epd, "displayPartial")
        )
        if self._partial_supported:
            if hasattr(self.epd, "PART_UPDATE"):
                self.epd.init(self.epd.PART_UPDATE)
            elif hasattr(self.epd, "lut_partial_update"):
                self.epd.init(self.epd.lut_partial_update)
            logging.debug("Partial refresh enabled")
        self._frame_count = 0

    def _display_partial(self, image: Image.Image) -> None:
        buf = self.epd.getbuffer(image)
        w, h = self.get_display_size()
        if hasattr(self.epd, "display_Partial"):
            sig = inspect.signature(self.epd.display_Partial)
            if len(sig.parameters) >= 5:
                self.epd.display_Partial(buf, 0, 0, w, h)
            else:
                self.epd.display_Partial(buf)
        elif hasattr(self.epd, "displayPartial"):
            self.epd.displayPartial(buf)

    def _display_full(self, image: Image.Image) -> None:
        if hasattr(self.epd, "FULL_UPDATE"):
            self.epd.init(self.epd.FULL_UPDATE)
        elif hasattr(self.epd, "lut_full_update"):
            self.epd.init(self.epd.lut_full_update)
        else:
            self.epd.init()
        self.epd.display(self.epd.getbuffer(image))
        if self._partial_supported:
            if hasattr(self.epd, "PART_UPDATE"):
                self.epd.init(self.epd.PART_UPDATE)
            elif hasattr(self.epd, "lut_partial_update"):
                self.epd.init(self.epd.lut_partial_update)
        logging.debug("Full refresh (ghosting cleanup)")

    def display_image(self, image: Image.Image) -> None:
        if self.output_path:
            image.save(self.output_path)
            logging.info("Saved rendered frame to %s", self.output_path)
            return
        if self.epd is None:
            self._init_epd()
        self._frame_count += 1
        if self._partial_supported and self._frame_count % self._full_refresh_interval != 1:
            self._display_partial(image)
        else:
            self._display_full(image)

    EPD_DRIVERS = {
        "epd2in13": ("epd2in13", 122, 250),
        "epd2in13_V2": ("epd2in13_V2", 122, 250),
        "epd2in13_V3": ("epd2in13_V3", 122, 250),
        "epd2in13_V4": ("epd2in13_V4", 122, 250),
        "epd2in7": ("epd2in7", 176, 264),
        "epd2in7_V2": ("epd2in7_V2", 176, 264),
        "epd2in9_V2": ("epd2in9_V2", 128, 296),
        "epd3in7": ("epd3in7", 280, 480),
        "epd7in5": ("epd7in5", 800, 480),
        "epd7in5_V2": ("epd7in5_V2", 800, 480),
    }

    AUTO_DETECT_ORDER = [
        "epd2in13_V4", "epd2in13_V3", "epd2in13_V2", "epd2in13",
        "epd2in7_V2", "epd2in7",
        "epd2in9_V2",
        "epd3in7",
        "epd7in5_V2", "epd7in5",
    ]

    def create_epd(self) -> object:
        import importlib
        if self.epd_model != "auto":
            if self.epd_model not in self.EPD_DRIVERS:
                raise RuntimeError(f"Unknown EPD model: {self.epd_model}")
            module_name = self.EPD_DRIVERS[self.epd_model][0]
            mod = importlib.import_module(f"waveshare_epd.{module_name}")
            logging.debug("Using e-paper driver %s", module_name)
            return mod.EPD()
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

    def clear_display(self) -> None:
        if self.epd is None:
            return
        try:
            if hasattr(self.epd, "FULL_UPDATE"):
                self.epd.init(self.epd.FULL_UPDATE)
            else:
                self.epd.init()
            self.epd.Clear(0xFF)
            logging.info("Display cleared")
        except Exception as exc:
            logging.debug("Failed to clear display: %s", exc)

    def shutdown_display(self) -> None:
        if self.epd is None:
            return
        if self.clear_and_exit:
            self.clear_display()
        try:
            self.epd.sleep()
        except Exception as exc:
            logging.debug("Failed to put e-paper into sleep mode: %s", exc)

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        buttons = ButtonListener(self)
        buttons.start()

        if self.interface2:
            logging.info(
                "Dual-antenna mode: ant1=%s ant2=%s",
                self.interface, self.interface2,
            )

        try:
            while self.running:
                started = time.time()
                try:
                    observed = self.scan_wifi()
                    self.merge_devices(observed)
                    frame = self.render_frame()
                    self.display_image(frame)
                    logging.info("Frame rendered with %d active devices", len(self.devices))
                except Exception as exc:
                    logging.exception("AirPrint cycle failed: %s", exc)

                self.force_scan = False
                self.redraw_needed = False

                elapsed = time.time() - started
                sleep_seconds = max(1, self.refresh_seconds - int(elapsed))
                end = time.time() + sleep_seconds
                while self.running and time.time() < end:
                    if self.force_scan or self.redraw_needed:
                        break
                    time.sleep(0.5)

                if self.redraw_needed and not self.force_scan and self.running:
                    try:
                        frame = self.render_frame()
                        self.display_image(frame)
                        logging.info("Redraw (view change / flip)")
                    except Exception as exc:
                        logging.exception("Redraw failed: %s", exc)
                    self.redraw_needed = False
        finally:
            self.shutdown_display()


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AirPrint WiFi visualizer")
    parser.add_argument("--interface", default="wlan1", help="Primary monitor-mode interface")
    parser.add_argument(
        "--interface2", default=None,
        help="Second monitor-mode interface for dual-antenna tracking (e.g. wlan0)",
    )
    parser.add_argument("--refresh", type=int, default=30, help="Refresh interval in seconds")
    parser.add_argument("--scan-time", type=int, default=12, help="Packet sniff duration per cycle")
    parser.add_argument("--state-ttl", type=int, default=300, help="How long to keep unseen devices")
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Save frame to file instead of writing to the EPD",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logs")
    valid_models = ["auto"] + sorted(AirPrint.EPD_DRIVERS.keys())
    parser.add_argument(
        "--epd-model", choices=valid_models, default="auto",
        help="Waveshare e-paper driver (e.g. epd2in7_V2 for 2.7in v2)",
    )
    parser.add_argument(
        "--web-port", type=int, default=0,
        help="Start web UI on this port (e.g. 5007). Disabled by default.",
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
        interface2=args.interface2,
        refresh_seconds=args.refresh,
        scan_seconds=args.scan_time,
        state_ttl_seconds=args.state_ttl,
        output_path=args.output,
        epd_model=args.epd_model,
    )

    if args.web_port:
        from web_ui import start_web_server
        start_web_server(app, args.web_port)

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
