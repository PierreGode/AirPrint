#!/usr/bin/env python3
"""AirPrint: WiFi signal visualizer for Raspberry Pi + Waveshare e-paper."""

from __future__ import annotations

import argparse
import collections
import hashlib
import inspect
import logging
import math
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


from PIL import Image, ImageDraw, ImageFont
from scapy.all import Dot11, Dot11Beacon, Dot11Elt, Dot11ProbeReq, RadioTap, sniff  # type: ignore


VIEW_RADAR = 0
VIEW_LIST = 1
VIEW_STATS = 2
VIEW_COUNT = 3

RSSI_HISTORY_LEN = 12

# ---- OUI vendor lookup (top ~120 prefixes cover >95% of consumer devices) ----

OUI_TABLE: Dict[str, str] = {
    "00:03:7f": "Atheros",    "00:0c:e7": "MediaTek",  "00:0d:93": "Apple",
    "00:0f:b5": "Netgear",    "00:14:22": "Dell",      "00:17:88": "Philips",
    "00:18:e7": "CameoComm",  "00:1a:2b": "Ayecom",    "00:1b:63": "Apple",
    "00:1c:b3": "Apple",      "00:1e:c2": "Apple",     "00:21:e9": "Apple",
    "00:22:41": "Apple",      "00:23:12": "Apple",     "00:23:32": "Apple",
    "00:24:36": "Apple",      "00:25:00": "Apple",     "00:25:bc": "Apple",
    "00:26:08": "Apple",      "00:26:b0": "Apple",     "00:26:bb": "Apple",
    "00:30:65": "Apple",      "00:3e:e1": "Apple",     "00:50:e4": "Apple",
    "00:56:cd": "Apple",      "00:88:65": "Apple",     "00:b0:d0": "Dell",
    "00:c6:10": "Apple",      "00:cd:fe": "Apple",     "00:db:70": "Apple",
    "00:f4:b9": "Apple",      "00:f7:6f": "Apple",
    "04:0c:ce": "Apple",      "04:15:52": "Apple",     "04:26:65": "Apple",
    "04:db:56": "Apple",      "04:e5:36": "Apple",     "04:f1:28": "Apple",
    "08:00:27": "VBox",       "08:66:98": "Apple",     "08:6d:41": "Apple",
    "0c:30:21": "Apple",      "0c:3e:9f": "Apple",     "0c:74:c2": "Apple",
    "0c:bc:9f": "Apple",
    "10:40:f3": "Apple",      "10:93:97": "Apple",     "10:94:bb": "Apple",
    "10:dd:b1": "Apple",
    "14:10:9f": "Apple",      "14:5a:05": "Apple",     "14:8f:c6": "Apple",
    "14:99:e2": "Apple",
    "18:af:61": "Apple",      "18:e7:f4": "Apple",
    "1c:36:bb": "Apple",      "1c:91:80": "Apple",
    "20:3c:ae": "Apple",      "20:78:f0": "Apple",     "20:ab:37": "Apple",
    "24:a0:74": "Apple",      "24:f0:94": "Apple",
    "28:37:37": "Apple",      "28:6a:ba": "Apple",     "28:cf:da": "Apple",
    "28:cf:e9": "Apple",
    "2c:be:08": "Apple",      "2c:f0:ee": "Apple",
    "30:10:b3": "Liteon",     "30:63:6b": "Apple",     "30:f7:72": "Apple",
    "34:08:bc": "Apple",      "34:12:98": "Apple",     "34:15:9e": "Apple",
    "34:36:3b": "Apple",      "34:c0:59": "Apple",
    "38:0f:4a": "Apple",      "38:48:4c": "Apple",     "38:b5:4d": "Apple",
    "38:c9:86": "Apple",      "38:ca:da": "Apple",
    "3c:06:30": "Apple",      "3c:15:c2": "Apple",     "3c:22:fb": "Apple",
    "3c:a6:f6": "Apple",
    "40:30:04": "Apple",      "40:33:1a": "Apple",     "40:4d:7f": "Apple",
    "40:6c:8f": "Apple",      "40:a6:d9": "Apple",     "40:b3:95": "Apple",
    "40:cb:c0": "Apple",
    "44:2a:60": "Apple",      "44:d8:84": "Apple",
    "48:43:7c": "Apple",      "48:60:bc": "Apple",     "48:a1:95": "Apple",
    "48:e9:f1": "Apple",
    "4c:32:75": "Apple",      "4c:57:ca": "Apple",     "4c:74:bf": "Apple",
    "4c:8d:79": "Apple",      "4c:b1:99": "Apple",
    "50:32:37": "Apple",      "50:7a:55": "Apple",     "50:bc:96": "Apple",
    "50:ed:3c": "Apple",
    "54:26:96": "Apple",      "54:33:cb": "Apple",     "54:72:4f": "Apple",
    "54:ae:27": "Apple",      "54:ea:a8": "Apple",
    "58:1f:aa": "Apple",      "58:40:4e": "Apple",     "58:55:ca": "Apple",
    "58:b0:35": "Apple",
    "5c:59:48": "Apple",      "5c:95:ae": "Apple",     "5c:96:9d": "Apple",
    "5c:f7:e6": "Apple",
    "60:03:08": "Apple",      "60:33:4b": "Apple",     "60:69:44": "Apple",
    "60:8c:4a": "Apple",      "60:a3:7d": "Apple",     "60:c5:47": "Apple",
    "60:d9:c7": "Apple",      "60:f8:1d": "Apple",     "60:fa:cd": "Apple",
    "64:20:0c": "Apple",      "64:70:02": "Apple",     "64:76:ba": "Apple",
    "64:a3:cb": "Apple",      "64:b0:a6": "Apple",     "64:e6:82": "Apple",
    "68:5b:35": "Apple",      "68:96:7b": "Apple",     "68:a8:6d": "Apple",
    "68:ab:1e": "Apple",      "68:d9:3c": "Apple",     "68:db:ca": "Apple",
    "68:fe:f7": "Apple",
    "6c:19:c0": "Apple",      "6c:3e:6d": "Apple",     "6c:40:08": "Apple",
    "6c:70:9f": "Apple",      "6c:94:66": "Apple",     "6c:96:cf": "Apple",
    "6c:c2:6b": "Apple",
    "70:11:24": "Apple",      "70:3e:ac": "Apple",     "70:56:81": "Apple",
    "70:73:cb": "Apple",      "70:81:eb": "Apple",     "70:a2:b3": "Apple",
    "70:cd:60": "Apple",      "70:de:e2": "Apple",     "70:ec:e4": "Apple",
    "74:e1:b6": "Apple",      "74:e2:f5": "Apple",
    "78:31:c1": "Apple",      "78:3a:84": "Apple",     "78:67:d7": "Apple",
    "78:7e:61": "Apple",      "78:88:6d": "Apple",     "78:9f:70": "Apple",
    "78:a3:e4": "Apple",      "78:ca:39": "Apple",     "78:d7:5f": "Apple",
    "78:fd:94": "Apple",
    "7c:01:0a": "Apple",      "7c:04:d0": "Apple",     "7c:11:be": "Apple",
    "7c:50:49": "Apple",      "7c:6d:62": "Apple",     "7c:6d:f8": "Apple",
    "7c:c3:a1": "Apple",      "7c:c5:37": "Apple",     "7c:d1:c3": "Apple",
    "7c:f0:5f": "Apple",      "7c:fa:df": "Apple",
    "80:00:6e": "Apple",      "80:49:71": "Apple",     "80:82:23": "Apple",
    "80:92:9f": "Apple",      "80:be:05": "Apple",     "80:e6:50": "Apple",
    "80:ea:96": "Apple",      "80:ed:2c": "Apple",
    "84:38:35": "Apple",      "84:78:8b": "Apple",     "84:85:06": "Apple",
    "84:89:ad": "Apple",      "84:a1:34": "Apple",     "84:b1:53": "Apple",
    "84:fc:ac": "Apple",      "84:fc:fe": "Apple",
    # Samsung
    "00:12:fb": "Samsung",    "00:15:99": "Samsung",   "00:16:32": "Samsung",
    "00:17:d5": "Samsung",    "00:18:af": "Samsung",   "00:1a:8a": "Samsung",
    "00:1b:98": "Samsung",    "00:1c:43": "Samsung",   "00:1d:25": "Samsung",
    "00:1e:e1": "Samsung",    "00:1e:e2": "Samsung",   "00:21:19": "Samsung",
    "00:21:d1": "Samsung",    "00:21:d2": "Samsung",   "00:23:39": "Samsung",
    "00:23:3a": "Samsung",    "00:23:99": "Samsung",   "00:23:d6": "Samsung",
    "00:23:d7": "Samsung",    "00:24:54": "Samsung",   "00:24:90": "Samsung",
    "00:24:91": "Samsung",    "00:25:66": "Samsung",   "00:25:67": "Samsung",
    "00:26:37": "Samsung",    "00:e0:64": "Samsung",
    "08:08:c2": "Samsung",    "08:37:3d": "Samsung",   "08:d4:2b": "Samsung",
    "0c:14:20": "Samsung",    "10:1d:c0": "Samsung",   "14:49:e0": "Samsung",
    "14:89:fd": "Samsung",    "18:22:7e": "Samsung",   "1c:62:b8": "Samsung",
    "20:13:e0": "Samsung",    "24:4b:03": "Samsung",   "28:98:7b": "Samsung",
    "2c:ae:2b": "Samsung",    "30:07:4d": "Samsung",   "34:23:ba": "Samsung",
    "38:01:97": "Samsung",    "3c:5a:37": "Samsung",   "40:16:3b": "Samsung",
    "44:6d:6c": "Samsung",    "48:44:f7": "Samsung",   "4c:3c:16": "Samsung",
    "50:01:bb": "Samsung",    "54:40:ad": "Samsung",   "58:c3:8b": "Samsung",
    "5c:3c:27": "Samsung",    "60:af:6d": "Samsung",   "64:77:91": "Samsung",
    "6c:f3:73": "Samsung",    "78:47:1d": "Samsung",   "78:52:1a": "Samsung",
    "80:65:6d": "Samsung",    "84:25:db": "Samsung",   "84:55:a5": "Samsung",
    "88:32:9b": "Samsung",    "8c:77:12": "Samsung",   "90:18:7c": "Samsung",
    "94:01:c2": "Samsung",    "94:35:0a": "Samsung",   "98:52:b1": "Samsung",
    "a0:82:1f": "Samsung",    "a4:08:ea": "Samsung",   "a8:06:00": "Samsung",
    "ac:36:13": "Samsung",    "b0:47:bf": "Samsung",   "b4:3a:28": "Samsung",
    "b8:5a:73": "Samsung",    "bc:14:ef": "Samsung",   "bc:44:86": "Samsung",
    "c0:bd:d1": "Samsung",    "c4:73:1e": "Samsung",   "c8:ba:94": "Samsung",
    "cc:07:ab": "Samsung",    "d0:22:be": "Samsung",   "d0:25:98": "Samsung",
    "d0:87:e2": "Samsung",    "d4:88:90": "Samsung",   "d8:90:e8": "Samsung",
    "e4:7c:f9": "Samsung",    "e4:e0:c5": "Samsung",   "ec:1f:72": "Samsung",
    "f0:25:b7": "Samsung",    "f4:42:8f": "Samsung",   "f8:04:2e": "Samsung",
    "fc:a1:3e": "Samsung",
    # Intel
    "00:02:b3": "Intel",      "00:03:47": "Intel",     "00:04:23": "Intel",
    "00:0c:f1": "Intel",      "00:0e:35": "Intel",     "00:11:11": "Intel",
    "00:12:f0": "Intel",      "00:13:02": "Intel",     "00:13:20": "Intel",
    "00:13:ce": "Intel",      "00:13:e8": "Intel",     "00:15:00": "Intel",
    "00:15:17": "Intel",      "00:16:6f": "Intel",     "00:16:76": "Intel",
    "00:16:ea": "Intel",      "00:16:eb": "Intel",     "00:18:de": "Intel",
    "00:19:d1": "Intel",      "00:19:d2": "Intel",     "00:1b:21": "Intel",
    "00:1b:77": "Intel",      "00:1c:bf": "Intel",     "00:1c:c0": "Intel",
    "00:1d:e0": "Intel",      "00:1d:e1": "Intel",     "00:1e:64": "Intel",
    "00:1e:65": "Intel",      "00:1f:3b": "Intel",     "00:1f:3c": "Intel",
    "00:20:7b": "Intel",      "00:21:5c": "Intel",     "00:21:5d": "Intel",
    "00:21:6a": "Intel",      "00:21:6b": "Intel",     "00:22:fa": "Intel",
    "00:22:fb": "Intel",      "00:23:14": "Intel",     "00:23:15": "Intel",
    "00:24:d6": "Intel",      "00:24:d7": "Intel",     "00:27:10": "Intel",
    "3c:a9:f4": "Intel",      "3c:f8:62": "Intel",     "40:a6:b7": "Intel",
    "48:51:b7": "Intel",      "4c:34:88": "Intel",     "58:91:cf": "Intel",
    "5c:87:9c": "Intel",      "60:57:18": "Intel",     "68:05:ca": "Intel",
    "6c:29:95": "Intel",      "7c:b2:7d": "Intel",     "80:19:34": "Intel",
    "84:3a:4b": "Intel",      "8c:8d:28": "Intel",     "94:65:9c": "Intel",
    "a4:34:d9": "Intel",      "a4:c4:94": "Intel",     "b4:d5:bd": "Intel",
    "b8:08:cf": "Intel",      "c8:5b:76": "Intel",     "d4:3b:04": "Intel",
    "dc:1b:a1": "Intel",      "e8:b1:fc": "Intel",     "f4:06:69": "Intel",
    "f8:16:54": "Intel",
    # Common routers / networking
    "00:14:bf": "Linksys",    "00:18:39": "Cisco",     "00:1a:a2": "Cisco",
    "00:23:69": "Cisco",      "00:24:c3": "Cisco",     "00:26:cb": "Cisco",
    "00:0c:41": "Cisco",      "00:18:74": "Cisco",
    "00:1e:58": "D-Link",     "00:22:b0": "D-Link",    "00:26:5a": "D-Link",
    "1c:7e:e5": "D-Link",     "28:10:7b": "D-Link",    "34:08:04": "D-Link",
    "f0:7d:68": "D-Link",
    "00:14:6c": "Netgear",    "00:1b:2f": "Netgear",   "00:1e:2a": "Netgear",
    "00:1f:33": "Netgear",    "00:22:3f": "Netgear",   "00:24:b2": "Netgear",
    "00:26:f2": "Netgear",    "20:4e:7f": "Netgear",   "2c:b0:5d": "Netgear",
    "30:46:9a": "Netgear",    "44:94:fc": "Netgear",
    "04:d9:f5": "ASUS",       "08:60:6e": "ASUS",      "10:bf:48": "ASUS",
    "14:dd:a9": "ASUS",       "1c:87:2c": "ASUS",      "2c:4d:54": "ASUS",
    "2c:56:dc": "ASUS",       "30:5a:3a": "ASUS",      "30:85:a9": "ASUS",
    "38:d5:47": "ASUS",       "40:b0:76": "ASUS",      "50:46:5d": "ASUS",
    "54:04:a6": "ASUS",       "60:45:cb": "ASUS",      "74:d0:2b": "ASUS",
    "ac:22:0b": "ASUS",       "b0:6e:bf": "ASUS",
    "14:cc:20": "TP-Link",    "30:b5:c2": "TP-Link",   "50:c7:bf": "TP-Link",
    "54:c8:0f": "TP-Link",    "60:e3:27": "TP-Link",   "64:56:01": "TP-Link",
    "6c:5a:b0": "TP-Link",    "70:4f:57": "TP-Link",   "78:44:76": "TP-Link",
    "90:f6:52": "TP-Link",    "a4:2b:b0": "TP-Link",   "b0:4e:26": "TP-Link",
    "b0:95:75": "TP-Link",    "c0:25:e9": "TP-Link",   "c0:4a:00": "TP-Link",
    "c4:e9:84": "TP-Link",    "d8:07:b6": "TP-Link",   "ec:08:6b": "TP-Link",
    "f4:f2:6d": "TP-Link",    "f8:d1:11": "TP-Link",
    # Google / Nest
    "18:d6:c7": "Google",     "30:fd:38": "Google",    "48:d6:d5": "Google",
    "54:60:09": "Google",     "94:eb:2c": "Google",    "a4:77:33": "Google",
    "f4:f5:d8": "Google",     "f4:f5:e8": "Google",
    # Huawei
    "00:18:82": "Huawei",     "00:1e:10": "Huawei",    "00:25:68": "Huawei",
    "00:25:9e": "Huawei",     "00:46:4b": "Huawei",    "04:c0:6f": "Huawei",
    "04:f9:38": "Huawei",     "08:19:a6": "Huawei",    "0c:37:dc": "Huawei",
    "10:1b:54": "Huawei",     "10:44:00": "Huawei",    "14:b9:68": "Huawei",
    "20:a6:80": "Huawei",     "24:09:95": "Huawei",    "28:31:52": "Huawei",
    "28:6e:d4": "Huawei",     "30:d1:7e": "Huawei",    "34:6b:d3": "Huawei",
    "38:f8:89": "Huawei",     "40:4d:8e": "Huawei",    "48:46:fb": "Huawei",
    "48:ad:08": "Huawei",     "4c:1f:cc": "Huawei",    "54:a5:1b": "Huawei",
    "58:2a:f7": "Huawei",     "5c:c3:07": "Huawei",    "60:de:44": "Huawei",
    "70:72:3c": "Huawei",     "74:88:2a": "Huawei",    "78:f5:fd": "Huawei",
    "80:b6:55": "Huawei",     "84:a8:e4": "Huawei",    "88:28:b3": "Huawei",
    "88:53:d4": "Huawei",     "8c:34:fd": "Huawei",    "94:77:2b": "Huawei",
    "ac:cf:85": "Huawei",     "b4:15:13": "Huawei",    "c8:d1:5e": "Huawei",
    "cc:a2:23": "Huawei",     "d4:6a:a8": "Huawei",    "dc:d2:fc": "Huawei",
    "e0:24:7f": "Huawei",     "e4:68:a3": "Huawei",    "e8:cd:2d": "Huawei",
    "f4:63:1f": "Huawei",     "f4:c7:14": "Huawei",    "f8:01:13": "Huawei",
    "fc:48:ef": "Huawei",
    # Raspberry Pi
    "b8:27:eb": "RPi",        "dc:a6:32": "RPi",       "e4:5f:01": "RPi",
    "d8:3a:dd": "RPi",        "28:cd:c1": "RPi",
    # Broadcom
    "00:10:18": "Broadcom",   "00:0a:f7": "Broadcom",
    # Qualcomm
    "00:03:7a": "Qualcomm",   "00:a0:c6": "Qualcomm",
    # Microsoft / Xbox
    "28:18:78": "Microsoft",  "7c:1e:52": "Microsoft", "00:50:f2": "Microsoft",
    # Amazon
    "00:fc:8b": "Amazon",     "0c:47:c9": "Amazon",    "10:ce:a9": "Amazon",
    "18:74:2e": "Amazon",     "34:d2:70": "Amazon",    "38:f7:3d": "Amazon",
    "40:b4:cd": "Amazon",     "44:65:0d": "Amazon",    "4c:ef:c0": "Amazon",
    "50:dc:e7": "Amazon",     "54:95:a0": "Amazon",    "58:28:ca": "Amazon",
    "68:54:fd": "Amazon",     "68:9c:e2": "Amazon",    "6c:56:97": "Amazon",
    "74:75:48": "Amazon",     "74:c2:46": "Amazon",    "84:d6:d0": "Amazon",
    "a0:02:dc": "Amazon",     "ac:63:be": "Amazon",    "b4:7c:9c": "Amazon",
    "b4:a9:fc": "Amazon",     "c8:2b:96": "Amazon",    "fc:65:de": "Amazon",
    "fc:a1:83": "Amazon",
    # Sonos
    "00:0e:58": "Sonos",      "34:7e:5c": "Sonos",     "48:a6:b8": "Sonos",
    "54:2a:1b": "Sonos",      "5c:aa:fd": "Sonos",     "78:28:ca": "Sonos",
    "94:9f:3e": "Sonos",      "b8:e9:37": "Sonos",
    # Xiaomi
    "00:9e:c8": "Xiaomi",     "04:cf:8c": "Xiaomi",    "0c:1d:af": "Xiaomi",
    "10:2a:b3": "Xiaomi",     "14:f6:5a": "Xiaomi",    "18:59:36": "Xiaomi",
    "20:47:da": "Xiaomi",     "28:6c:07": "Xiaomi",    "2c:f0:a2": "Xiaomi",
    "34:80:b3": "Xiaomi",     "38:a4:ed": "Xiaomi",    "3c:bd:3e": "Xiaomi",
    "50:64:2b": "Xiaomi",     "58:44:98": "Xiaomi",    "64:b4:73": "Xiaomi",
    "64:cc:2e": "Xiaomi",     "74:23:44": "Xiaomi",    "7c:8b:b5": "Xiaomi",
    "8c:de:f9": "Xiaomi",     "98:fa:e3": "Xiaomi",    "9c:99:a0": "Xiaomi",
    "a4:77:58": "Xiaomi",     "b0:e2:35": "Xiaomi",    "c4:0b:cb": "Xiaomi",
    "d4:97:0b": "Xiaomi",     "f0:b4:29": "Xiaomi",    "f8:a4:5f": "Xiaomi",
    "fc:64:ba": "Xiaomi",
    # OnePlus / Oppo
    "94:65:2d": "OnePlus",    "c0:ee:fb": "OnePlus",
    "1c:48:f9": "OPPO",       "2c:5b:e1": "OPPO",
    # Sony
    "00:13:a9": "Sony",       "00:1a:80": "Sony",      "00:24:be": "Sony",
    "28:0d:fc": "Sony",       "30:17:c8": "Sony",      "40:b8:37": "Sony",
    "78:84:3c": "Sony",       "ac:9b:0a": "Sony",      "d8:d4:3c": "Sony",
    "fc:f1:52": "Sony",
    # LG
    "00:1c:62": "LG",         "00:1e:75": "LG",        "00:22:a9": "LG",
    "00:26:e2": "LG",         "10:68:3f": "LG",        "20:3d:bd": "LG",
    "2c:54:cf": "LG",         "34:fc:ef": "LG",        "40:b0:fa": "LG",
    "58:a2:b5": "LG",         "64:89:9a": "LG",        "6c:d6:8a": "LG",
    "78:f8:82": "LG",         "88:c9:d0": "LG",        "a8:16:d0": "LG",
    "c4:43:8f": "LG",         "cc:fa:00": "LG",        "f8:0c:f3": "LG",
    # Espressif (IoT)
    "24:0a:c4": "ESP",        "24:62:ab": "ESP",       "24:6f:28": "ESP",
    "30:ae:a4": "ESP",        "3c:61:05": "ESP",       "3c:71:bf": "ESP",
    "4c:11:ae": "ESP",        "4c:75:25": "ESP",       "5c:cf:7f": "ESP",
    "68:c6:3a": "ESP",        "84:0d:8e": "ESP",       "84:cc:a8": "ESP",
    "8c:aa:b5": "ESP",        "90:97:d5": "ESP",       "94:b5:55": "ESP",
    "94:b9:7e": "ESP",        "a0:20:a6": "ESP",       "a4:cf:12": "ESP",
    "a4:e5:7c": "ESP",        "ac:67:b2": "ESP",       "b4:e6:2d": "ESP",
    "bc:dd:c2": "ESP",        "c4:4f:33": "ESP",       "c4:de:e2": "ESP",
    "cc:50:e3": "ESP",        "d8:a0:1d": "ESP",       "d8:bf:c0": "ESP",
    "dc:4f:22": "ESP",        "e0:98:06": "ESP",       "e8:db:84": "ESP",
    "ec:fa:bc": "ESP",        "f0:08:d1": "ESP",       "f4:cf:a2": "ESP",
    # Motorola
    "00:0b:06": "Motorola",   "00:11:1a": "Motorola",  "00:14:04": "Motorola",
    # HP
    "00:1b:78": "HP",         "00:21:5a": "HP",        "00:25:b3": "HP",
    "3c:d9:2b": "HP",         "80:c1:6e": "HP",
    # Lenovo
    "00:06:1b": "Lenovo",     "28:d2:44": "Lenovo",    "50:7b:9d": "Lenovo",
    "54:ee:75": "Lenovo",     "70:5a:0f": "Lenovo",    "98:fa:9b": "Lenovo",
    "c8:21:58": "Lenovo",     "e8:2a:44": "Lenovo",
}


def _is_random_mac(mac: str) -> bool:
    """Check if MAC is locally administered (randomized)."""
    try:
        first_byte = int(mac[:2], 16)
        return bool(first_byte & 0x02)
    except (ValueError, IndexError):
        return False


def oui_vendor(mac: str) -> str:
    """Return short vendor name from MAC OUI prefix."""
    if _is_random_mac(mac):
        # Show last 4 chars so randomized devices are distinguishable
        return "~" + mac[-5:].replace(":", "")
    prefix = mac[:8].lower()
    return OUI_TABLE.get(prefix, mac[-5:].replace(":", ""))


# ---- Channel hopping ----

# Common 2.4 GHz and 5 GHz channels to hop through
CHANNELS_24GHZ = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
CHANNELS_5GHZ = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 149, 153, 157, 161, 165]


def _hop_channels(iface: str, channels: List[int], interval: float, stop_event: threading.Event) -> None:
    """Hop the given interface across channels in a background thread."""
    idx = 0
    while not stop_event.is_set():
        ch = channels[idx % len(channels)]
        try:
            subprocess.run(
                ["iw", "dev", iface, "set", "channel", str(ch)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except Exception:
            pass
        idx += 1
        stop_event.wait(interval)


@dataclass
class DeviceObservation:
    """State for a discovered WiFi transmitter."""

    mac: str
    channel: int
    rssi: int
    last_seen: float
    kind: str
    ssid: str = ""
    vendor: str = ""
    probed_ssids: List[str] = field(default_factory=list)
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
    ssid: str = ""
    vendor: str = ""
    probed_ssids: List[str] = field(default_factory=list)
    rssi_ant1: Optional[int] = None
    rssi_ant2: Optional[int] = None
    rssi_history: List[int] = field(default_factory=list)
    angle: float = 0.0
    angle_confidence: float = 0.0
    antenna_nudge: float = 0.0
    rssi_trend: float = 0.0
    # Smoothed RSSI (exponential moving average)
    rssi_smooth: float = -80.0


class ButtonListener:
    """Listen to the 4 GPIO buttons on the Waveshare 2.7" e-paper HAT."""

    KEY_PINS = {"key1": 5, "key2": 6, "key3": 13, "key4": 19}

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
            "key1": self._on_key1, "key2": self._on_key2,
            "key3": self._on_key3, "key4": self._on_key4,
        }
        for name, pin in self.KEY_PINS.items():
            try:
                btn = Button(pin, pull_up=True, bounce_time=0.3)
                btn.when_pressed = handlers[name]
                self._buttons.append(btn)
            except Exception as exc:
                logging.warning("Failed to register button %s: %s", name, exc)

    def _on_key1(self) -> None:
        logging.info("KEY1: Force scan"); self.app.force_scan = True

    def _on_key2(self) -> None:
        logging.info("KEY2: Flip screen")
        self.app.screen_flipped = not self.app.screen_flipped
        self.app.redraw_needed = True

    def _on_key3(self) -> None:
        self.app.current_view = (self.app.current_view + 1) % VIEW_COUNT
        logging.info("KEY3: View -> %s", ["radar", "list", "stats"][self.app.current_view])
        self.app.redraw_needed = True

    def _on_key4(self) -> None:
        logging.info("KEY4: Clear & exit")
        self.app.clear_and_exit = True; self.app.running = False


class AirPrint:
    def __init__(
        self, interface: str, interface2: Optional[str],
        refresh_seconds: int, scan_seconds: int, state_ttl_seconds: int,
        output_path: Optional[Path], epd_model: str, channel_hop: bool,
    ) -> None:
        self.interface = interface
        self.interface2 = interface2
        self.refresh_seconds = refresh_seconds
        self.scan_seconds = scan_seconds
        self.state_ttl_seconds = state_ttl_seconds
        self.output_path = output_path
        self.epd_model = epd_model
        self.channel_hop = channel_hop
        self.devices: Dict[str, DeviceObservation] = {}
        self.tracks: Dict[str, DeviceTrack] = {}
        self.running = True
        self.epd: Optional[object] = None
        self._partial_supported = False
        self._frame_count = 0
        self._full_refresh_interval = 10
        self.force_scan = False
        self.screen_flipped = False
        self.redraw_needed = False
        self.current_view = VIEW_RADAR
        self.clear_and_exit = False
        self.last_frame: Optional[Image.Image] = None
        self.last_frame_time: float = 0
        # Device count history for sparkline
        self._count_history: List[int] = []

    def stop(self, *_: object) -> None:
        if not self.running:
            logging.info("Force quit"); sys.exit(1)
        logging.info("Shutting down AirPrint loop")
        self.running = False

    # ---- WiFi scanning ----

    def _sniff_interface(self, iface: str) -> Dict[str, DeviceObservation]:
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

            # Determine kind and extract SSID
            ssid = ""
            probed: List[str] = []
            if packet.haslayer(Dot11Beacon):
                kind = "ap"
                ssid = self._extract_ssid(packet)
            elif packet.haslayer(Dot11ProbeReq):
                kind = "device"
                probe_ssid = self._extract_ssid(packet)
                if probe_ssid:
                    probed = [probe_ssid]
            else:
                kind = "device"

            vendor = oui_vendor(src)

            prev = found.get(src)
            if prev is None or rssi > prev.rssi:
                found[src] = DeviceObservation(
                    mac=src, channel=channel, rssi=rssi,
                    last_seen=time.time(), kind=kind,
                    ssid=ssid, vendor=vendor, probed_ssids=probed,
                )
            elif probed and prev is not None:
                # Merge probed SSIDs
                for s in probed:
                    if s not in prev.probed_ssids:
                        prev.probed_ssids.append(s)

        sniff(
            iface=iface, timeout=self.scan_seconds,
            prn=process_packet, store=False, monitor=True,
        )
        return found

    @staticmethod
    def _extract_ssid(packet: object) -> str:
        """Extract SSID from beacon or probe request."""
        if packet.haslayer(Dot11Elt):
            elt = packet.getlayer(Dot11Elt)
            while elt is not None:
                if elt.ID == 0 and elt.info:
                    try:
                        ssid = elt.info.decode("utf-8", errors="replace").strip()
                        if ssid and ssid != "\x00" * len(ssid):
                            return ssid
                    except Exception:
                        pass
                    break
                elt = elt.payload.getlayer(Dot11Elt)
        return ""

    def scan_wifi(self) -> Dict[str, DeviceObservation]:
        logging.info("Scanning %s for %ss", self.interface, self.scan_seconds)

        # Start channel hopping if enabled
        hop_stop = threading.Event()
        hop_thread = None
        if self.channel_hop:
            channels = CHANNELS_24GHZ + CHANNELS_5GHZ
            hop_interval = max(0.15, self.scan_seconds / len(channels))
            hop_thread = threading.Thread(
                target=_hop_channels,
                args=(self.interface, channels, hop_interval, hop_stop),
                daemon=True,
            )
            hop_thread.start()
            logging.debug("Channel hopping started (%.2fs/ch)", hop_interval)

        try:
            if self.interface2:
                result2: Dict[str, DeviceObservation] = {}

                def scan_ant2() -> None:
                    nonlocal result2
                    result2 = self._sniff_interface(self.interface2)

                t = threading.Thread(target=scan_ant2, daemon=True)
                t.start()
                result1 = self._sniff_interface(self.interface)
                t.join(timeout=self.scan_seconds + 5)

                merged: Dict[str, DeviceObservation] = {}
                all_macs = set(result1.keys()) | set(result2.keys())
                for mac in all_macs:
                    obs1 = result1.get(mac)
                    obs2 = result2.get(mac)
                    if obs1 and obs2:
                        obs1.rssi_ant1 = obs1.rssi
                        obs1.rssi_ant2 = obs2.rssi
                        # Merge probed SSIDs from both
                        for s in obs2.probed_ssids:
                            if s not in obs1.probed_ssids:
                                obs1.probed_ssids.append(s)
                        if not obs1.ssid and obs2.ssid:
                            obs1.ssid = obs2.ssid
                        merged[mac] = obs1
                    elif obs1:
                        obs1.rssi_ant1 = obs1.rssi
                        merged[mac] = obs1
                    elif obs2:
                        obs2.rssi_ant2 = obs2.rssi
                        obs2.rssi_ant1 = None
                        merged[mac] = obs2

                logging.debug(
                    "Dual-antenna: %d ant1, %d ant2, %d merged",
                    len(result1), len(result2), len(merged),
                )
                return merged
            else:
                return self._sniff_interface(self.interface)
        finally:
            if hop_thread:
                hop_stop.set()
                hop_thread.join(timeout=2)

    @staticmethod
    def extract_rssi(packet: object) -> Optional[int]:
        if not packet.haslayer(RadioTap):
            return None
        value = getattr(packet[RadioTap], "dBm_AntSignal", None)
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
            freq = getattr(packet[RadioTap], "ChannelFrequency", None)
            if freq:
                return AirPrint.freq_to_channel(int(freq))
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

    # ---- Device tracking ----

    def merge_devices(self, observed: Dict[str, DeviceObservation]) -> None:
        now = time.time()
        for mac, obs in observed.items():
            # Merge probed SSIDs with existing
            prev = self.devices.get(mac)
            if prev is not None:
                for s in prev.probed_ssids:
                    if s not in obs.probed_ssids:
                        obs.probed_ssids.append(s)
                if not obs.ssid and prev.ssid:
                    obs.ssid = prev.ssid
                if not obs.vendor and prev.vendor:
                    obs.vendor = prev.vendor
            self.devices[mac] = obs
            self._update_track(mac, obs)

        stale = [m for m, d in self.devices.items() if now - d.last_seen > self.state_ttl_seconds]
        for mac in stale:
            del self.devices[mac]
            self.tracks.pop(mac, None)

        # Update count history for sparkline
        self._count_history.append(len(self.devices))
        if len(self._count_history) > 60:
            self._count_history = self._count_history[-60:]

    def _update_track(self, mac: str, obs: DeviceObservation) -> None:
        track = self.tracks.get(mac)
        if track is None:
            track = DeviceTrack(
                mac=mac, channel=obs.channel, rssi=obs.rssi,
                last_seen=obs.last_seen, kind=obs.kind,
                ssid=obs.ssid, vendor=obs.vendor,
                probed_ssids=list(obs.probed_ssids),
                angle=self.hash_to_unit(mac) * 2 * math.pi,
                rssi_smooth=float(obs.rssi),
            )
            self.tracks[mac] = track

        track.rssi = obs.rssi
        track.last_seen = obs.last_seen
        track.channel = obs.channel
        track.kind = obs.kind
        track.rssi_ant1 = obs.rssi_ant1
        track.rssi_ant2 = obs.rssi_ant2
        track.ssid = obs.ssid or track.ssid
        track.vendor = obs.vendor or track.vendor
        track.probed_ssids = obs.probed_ssids

        # EMA smoothing (alpha=0.3)
        track.rssi_smooth = track.rssi_smooth * 0.7 + obs.rssi * 0.3

        track.rssi_history.append(obs.rssi)
        if len(track.rssi_history) > RSSI_HISTORY_LEN:
            track.rssi_history = track.rssi_history[-RSSI_HISTORY_LEN:]

        track.rssi_trend = self._compute_trend(track.rssi_history)

        if obs.rssi_ant1 is not None and obs.rssi_ant2 is not None:
            delta = obs.rssi_ant1 - obs.rssi_ant2
            nudge = delta * 0.02
            track.antenna_nudge = track.antenna_nudge * 0.7 + nudge * 0.3
            track.angle += track.antenna_nudge * 0.1
            track.angle_confidence = min(1.0, track.angle_confidence + 0.05)

        if len(track.rssi_history) >= 3 and track.rssi_trend != 0:
            track.angle += track.rssi_trend * 0.005
            track.angle_confidence = min(1.0, track.angle_confidence + 0.02)

        track.angle = track.angle % (2 * math.pi)

    @staticmethod
    def _compute_trend(history: List[int]) -> float:
        n = len(history)
        if n < 3:
            return 0.0
        sx = sy = sxx = sxy = 0.0
        for i, val in enumerate(history):
            sx += i; sy += val; sxx += i * i; sxy += i * val
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
            angle = track.angle if track else self.hash_to_unit(mac) * 2 * math.pi
            rssi = track.rssi_smooth if track else float(device.rssi)
            distance = self.rssi_to_radius(rssi, max_radius)
            x = center[0] + math.cos(angle) * distance
            y = center[1] + math.sin(angle) * distance
            dot_r = self.recency_radius(now - device.last_seen)

            # Trend tail
            if track and abs(track.rssi_trend) > 0.3:
                tail_len = min(8, abs(track.rssi_trend) * 3)
                sign = 1 if track.rssi_trend > 0 else -1
                tx = x + sign * math.cos(angle) * tail_len
                ty = y + sign * math.sin(angle) * tail_len
                draw.line((int(x), int(y), int(tx), int(ty)), fill=0, width=1)

            # AP = square, device = circle
            if device.kind == "ap":
                draw.rectangle(
                    (x - dot_r, y - dot_r, x + dot_r, y + dot_r), fill=0,
                )
            else:
                draw.ellipse(
                    (x - dot_r, y - dot_r, x + dot_r, y + dot_r), fill=0,
                )

        stamp = datetime.now().strftime("%H:%M")
        count = str(len(self.devices))
        draw.text((4, height - 14), stamp, fill=0, font=font)
        draw.text((width - len(count) * 6 - 4, height - 14), count, fill=0, font=font)
        return image

    def render_list(self, width: int, height: int) -> Image.Image:
        image = Image.new("1", (width, height), 255)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()

        draw.text((4, 2), "VENDOR   RSSI Ch NAME", fill=0, font=font)
        draw.line((0, 14, width, 14), fill=0)

        sorted_devs = sorted(self.devices.values(), key=lambda d: d.rssi, reverse=True)
        y = 18
        line_h = 12
        max_lines = (height - 32) // line_h
        for dev in sorted_devs[:max_lines]:
            vendor = dev.vendor[:8] if dev.vendor else dev.mac[-8:]
            kind_marker = "*" if dev.kind == "ap" else " "
            name = dev.ssid[:8] if dev.ssid else ""
            if not name and dev.probed_ssids:
                name = ">" + dev.probed_ssids[0][:7]
            line = f"{vendor:<8}{kind_marker}{dev.rssi:>4} {dev.channel:>2} {name}"
            # Truncate to fit display width
            max_chars = width // 6
            draw.text((4, y), line[:max_chars], fill=0, font=font)
            y += line_h

        stamp = datetime.now().strftime("%H:%M")
        count = str(len(self.devices))
        draw.text((4, height - 14), stamp, fill=0, font=font)
        draw.text((width - len(count) * 6 - 4, height - 14), count, fill=0, font=font)
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
        vendors: Dict[str, int] = {}
        for dev in self.devices.values():
            channels[dev.channel] = channels.get(dev.channel, 0) + 1
            rssi_values.append(dev.rssi)
            v = dev.vendor or "?"
            vendors[v] = vendors.get(v, 0) + 1

        y = 4
        line_h = 13
        draw.text((4, y), f"Total:   {total}", fill=0, font=font); y += line_h
        draw.text((4, y), f"APs:     {aps}", fill=0, font=font); y += line_h
        draw.text((4, y), f"Clients: {clients}", fill=0, font=font); y += line_h

        if rssi_values:
            avg_rssi = sum(rssi_values) // len(rssi_values)
            draw.text((4, y), f"RSSI:{min(rssi_values)}/{avg_rssi}/{max(rssi_values)}", fill=0, font=font)
            y += line_h

        # Top vendors
        if vendors:
            top_v = sorted(vendors.items(), key=lambda kv: kv[1], reverse=True)[:3]
            vstr = " ".join(f"{v}:{c}" for v, c in top_v)
            draw.text((4, y), vstr[:width // 6], fill=0, font=font); y += line_h

        # Top channels
        if channels:
            top_ch = sorted(channels.items(), key=lambda kv: kv[1], reverse=True)[:4]
            chstr = " ".join(f"ch{ch}:{cnt}" for ch, cnt in top_ch)
            draw.text((4, y), chstr[:width // 6], fill=0, font=font); y += line_h

        # Sparkline: device count over time
        if len(self._count_history) >= 2:
            y += 4
            draw.text((4, y), "Activity:", fill=0, font=font); y += 12
            self._draw_sparkline(draw, 4, y, width - 8, 20, self._count_history)
            y += 24

        stamp = datetime.now().strftime("%H:%M")
        draw.text((4, height - 14), stamp, fill=0, font=font)
        return image

    @staticmethod
    def _draw_sparkline(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, data: List[int]) -> None:
        """Draw a small line graph."""
        if not data:
            return
        lo = min(data)
        hi = max(data)
        span = hi - lo if hi != lo else 1
        n = len(data)
        step = max(1, w / max(n - 1, 1))
        points = []
        for i, val in enumerate(data):
            px = x + int(i * step)
            py = y + h - int((val - lo) / span * h)
            points.append((px, py))
        if len(points) >= 2:
            draw.line(points, fill=0, width=1)
        # Draw baseline
        draw.line((x, y + h, x + w, y + h), fill=0, width=1)

    @staticmethod
    def draw_rings(draw: ImageDraw.ImageDraw, center: tuple[int, int], max_radius: float) -> None:
        for frac in (0.25, 0.5, 0.75, 1.0):
            r = int(max_radius * frac)
            draw.ellipse(
                (center[0] - r, center[1] - r, center[0] + r, center[1] + r),
                outline=0, width=1,
            )

    @staticmethod
    def rssi_to_radius(rssi: float, max_radius: float) -> float:
        clamped = max(-95.0, min(-30.0, rssi))
        norm = (clamped + 95) / 65
        return max_radius - (norm * (max_radius - 12))

    @staticmethod
    def recency_radius(age_seconds: float) -> int:
        if age_seconds < 45: return 5
        if age_seconds < 90: return 4
        if age_seconds < 180: return 3
        return 2

    @staticmethod
    def hash_to_unit(mac: str) -> float:
        h = hashlib.sha256(mac.encode("utf-8")).hexdigest()[:8]
        return int(h, 16) / 0xFFFFFFFF

    def get_display_size(self) -> tuple[int, int]:
        if self.epd is not None:
            return (getattr(self.epd, "width", 800), getattr(self.epd, "height", 480))
        if self.epd_model in self.EPD_DRIVERS:
            _, w, h = self.EPD_DRIVERS[self.epd_model]
            return (w, h)
        return (800, 480)

    # ---- EPD display ----

    def _init_epd(self) -> None:
        self.epd = self.create_epd()
        self.epd.init()
        self.epd.Clear(0xFF)
        self._partial_supported = (
            hasattr(self.epd, "display_Partial") or hasattr(self.epd, "displayPartial")
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
        "epd2in7_V2", "epd2in7", "epd2in9_V2", "epd3in7",
        "epd7in5_V2", "epd7in5",
    ]

    def create_epd(self) -> object:
        import importlib
        if self.epd_model != "auto":
            if self.epd_model not in self.EPD_DRIVERS:
                raise RuntimeError(f"Unknown EPD model: {self.epd_model}")
            mod_name = self.EPD_DRIVERS[self.epd_model][0]
            mod = importlib.import_module(f"waveshare_epd.{mod_name}")
            logging.debug("Using e-paper driver %s", mod_name)
            return mod.EPD()
        for name in self.AUTO_DETECT_ORDER:
            mod_name = self.EPD_DRIVERS[name][0]
            try:
                mod = importlib.import_module(f"waveshare_epd.{mod_name}")
                epd = mod.EPD(); epd.init(); epd.sleep()
                logging.debug("Auto-selected e-paper driver %s", mod_name)
                return epd
            except Exception:
                logging.debug("Driver %s failed, trying next", mod_name)
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

        ButtonListener(self).start()

        if self.interface2:
            logging.info("Dual-antenna: ant1=%s ant2=%s", self.interface, self.interface2)
        if self.channel_hop:
            logging.info("Channel hopping enabled on %s", self.interface)

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
    parser.add_argument("--interface2", default=None, help="Second monitor-mode interface for dual-antenna tracking")
    parser.add_argument("--refresh", type=int, default=30, help="Refresh interval in seconds")
    parser.add_argument("--scan-time", type=int, default=12, help="Packet sniff duration per cycle")
    parser.add_argument("--state-ttl", type=int, default=300, help="How long to keep unseen devices")
    parser.add_argument("--output", type=Path, default=None, help="Save frame to file instead of EPD")
    parser.add_argument("--debug", action="store_true", help="Enable debug logs")
    parser.add_argument(
        "--channel-hop", action="store_true",
        help="Hop across 2.4/5GHz channels during scan (sees more devices)",
    )
    valid_models = ["auto"] + sorted(AirPrint.EPD_DRIVERS.keys())
    parser.add_argument("--epd-model", choices=valid_models, default="auto", help="Waveshare e-paper driver")
    parser.add_argument("--web-port", type=int, default=0, help="Start web UI on this port (e.g. 5007)")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    app = AirPrint(
        interface=args.interface, interface2=args.interface2,
        refresh_seconds=args.refresh, scan_seconds=args.scan_time,
        state_ttl_seconds=args.state_ttl, output_path=args.output,
        epd_model=args.epd_model, channel_hop=args.channel_hop,
    )

    if args.web_port:
        from web_ui import start_web_server
        start_web_server(app, args.web_port)

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
