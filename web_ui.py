"""Lightweight web UI for AirPrint — mirrors e-paper and exposes settings."""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from airprint import AirPrint

_app: AirPrint | None = None

VIEW_NAMES = ["radar", "list", "stats"]


def _frame_to_base64(app: AirPrint | None) -> str:
    """Return the current e-paper frame as a base64-encoded PNG string."""
    from PIL import Image, ImageDraw, ImageFont

    if app is not None and app.last_frame is not None:
        img = app.last_frame.convert("L")
    else:
        w, h = (176, 264) if app is None else app.get_display_size()
        img = Image.new("L", (w, h), 255)
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        draw.text((w // 2 - 40, h // 2 - 6), "waiting...", fill=0, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        logging.debug("web: %s", fmt % args)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/":
            self._serve_html()
        elif path == "/api/state":
            self._serve_state()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/api/action":
            self._handle_action()
        elif self.path == "/api/settings":
            self._handle_settings()
        else:
            self.send_error(404)

    def _serve_state(self) -> None:
        app = _app
        if app is None:
            self.send_error(503)
            return
        w, h = app.get_display_size()
        devices = []
        for d in app.devices.values():
            track = app.tracks.get(d.mac)
            dev_info = {
                "mac": d.mac,
                "rssi": d.rssi,
                "channel": d.channel,
                "kind": d.kind,
                "last_seen": d.last_seen,
                "rssi_ant1": d.rssi_ant1,
                "rssi_ant2": d.rssi_ant2,
                "vendor": d.vendor,
                "ssid": d.ssid,
                "probed_ssids": d.probed_ssids[:5],
            }
            if track:
                dev_info["trend"] = round(track.rssi_trend, 2)
                dev_info["confidence"] = round(track.angle_confidence, 2)
            devices.append(dev_info)
        state = {
            "image": _frame_to_base64(app),
            "epd_model": app.epd_model,
            "dual_antenna": app.interface2 is not None,
            "display_size": [w, h],
            "supported_models": sorted(app.EPD_DRIVERS.keys()),
            "current_view": VIEW_NAMES[app.current_view],
            "screen_flipped": app.screen_flipped,
            "refresh_seconds": app.refresh_seconds,
            "scan_seconds": app.scan_seconds,
            "state_ttl_seconds": app.state_ttl_seconds,
            "full_refresh_interval": app._full_refresh_interval,
            "frame_count": app._frame_count,
            "device_count": len(app.devices),
            "devices": devices,
        }
        body = json.dumps(state).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _handle_action(self) -> None:
        app = _app
        if app is None:
            self.send_error(503)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        action = body.get("action", "")

        if action == "scan":
            app.force_scan = True
        elif action == "flip":
            app.screen_flipped = not app.screen_flipped
            app.redraw_needed = True
        elif action == "next_view":
            from airprint import VIEW_COUNT
            app.current_view = (app.current_view + 1) % VIEW_COUNT
            app.redraw_needed = True
        elif action == "set_view":
            view = body.get("view", "radar")
            if view in VIEW_NAMES:
                app.current_view = VIEW_NAMES.index(view)
                app.redraw_needed = True
        elif action == "clear_exit":
            app.clear_and_exit = True
            app.running = False
        else:
            self._json_response(400, {"error": f"unknown action: {action}"})
            return

        self._json_response(200, {"ok": True, "action": action})

    def _handle_settings(self) -> None:
        app = _app
        if app is None:
            self.send_error(503)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        restart_required = False

        if "epd_model" in body:
            new_model = body["epd_model"]
            if new_model != app.epd_model and (new_model == "auto" or new_model in app.EPD_DRIVERS):
                app.epd_model = new_model
                if app.epd is not None:
                    try:
                        app.epd.sleep()
                    except Exception:
                        pass
                    app.epd = None
                app._partial_supported = False
                app._frame_count = 0
                app.redraw_needed = True
                restart_required = True
                logging.info("EPD model changed to %s via web UI", new_model)

        if "refresh_seconds" in body:
            app.refresh_seconds = max(10, int(body["refresh_seconds"]))
        if "scan_seconds" in body:
            app.scan_seconds = max(3, int(body["scan_seconds"]))
        if "state_ttl_seconds" in body:
            app.state_ttl_seconds = max(30, int(body["state_ttl_seconds"]))
        if "full_refresh_interval" in body:
            app._full_refresh_interval = max(1, int(body["full_refresh_interval"]))

        self._json_response(200, {"ok": True, "restart_required": restart_required})

    def _json_response(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self) -> None:
        html = HTML_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


def start_web_server(app: AirPrint, port: int) -> None:
    global _app
    _app = app
    server = HTTPServer(("0.0.0.0", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logging.info("Web UI running on http://0.0.0.0:%d", port)


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AirPrint</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
    background: #0a0a0a; color: #e0e0e0;
    min-height: 100vh;
  }
  header {
    background: #111; border-bottom: 1px solid #222;
    padding: 12px 20px; display: flex; align-items: center; gap: 16px;
  }
  header h1 { font-size: 16px; color: #fff; font-weight: 600; }
  header .status { font-size: 12px; color: #666; }
  header .status .dot {
    display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; background: #2a2; margin-right: 4px; vertical-align: middle;
  }
  .container { display: flex; gap: 0; min-height: calc(100vh - 49px); }

  .epd-panel {
    flex: 0 0 auto; background: #111; border-right: 1px solid #222;
    display: flex; flex-direction: column; align-items: center;
    padding: 20px;
  }
  .epd-frame {
    background: #d4d4d0; border-radius: 6px; padding: 10px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.6);
  }
  .epd-frame img {
    display: block;
    image-rendering: crisp-edges;
    image-rendering: -webkit-crisp-edges;
    image-rendering: pixelated;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  .epd-frame img.size-md { height: 300px; width: auto; }
  .epd-frame img.size-lg { height: 500px; width: auto; }
  .epd-frame img.size-xl { height: 800px; width: auto; }
  .epd-label {
    font-size: 11px; color: #555; margin-top: 12px; text-align: center;
    cursor: pointer;
  }

  .controls {
    display: flex; gap: 6px; margin-top: 16px; flex-wrap: wrap;
    justify-content: center;
  }
  .btn {
    background: #1a1a1a; border: 1px solid #333; color: #ccc;
    padding: 6px 14px; border-radius: 4px; cursor: pointer;
    font-family: inherit; font-size: 12px; transition: all 0.15s;
  }
  .btn:hover { background: #252525; border-color: #555; color: #fff; }
  .btn:active { background: #333; }
  .btn.danger { border-color: #522; color: #c66; }
  .btn.danger:hover { background: #2a1515; border-color: #844; }

  .sidebar {
    flex: 1; padding: 20px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 16px;
  }
  .card {
    background: #111; border: 1px solid #222; border-radius: 6px;
    padding: 14px;
  }
  .card h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
    color: #666; margin-bottom: 10px;
  }
  .setting-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 0; border-bottom: 1px solid #1a1a1a;
    gap: 12px;
  }
  .setting-row:last-child { border-bottom: none; }
  .setting-row label { font-size: 13px; color: #aaa; white-space: nowrap; }
  .setting-row input {
    background: #0a0a0a; border: 1px solid #333; color: #e0e0e0;
    padding: 4px 8px; border-radius: 3px; font-family: inherit;
    font-size: 13px; width: 80px; text-align: right;
  }
  .setting-row select {
    background: #0a0a0a; border: 1px solid #333; color: #e0e0e0;
    padding: 4px 8px; border-radius: 3px; font-family: inherit;
    font-size: 13px; width: 240px; text-align: left;
    cursor: pointer;
    -webkit-appearance: menulist;
    appearance: menulist;
  }
  .setting-row input:focus, .setting-row select:focus {
    outline: none; border-color: #555;
  }

  .device-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .device-table th {
    text-align: left; color: #666; font-weight: 500; padding: 4px 8px;
    border-bottom: 1px solid #222;
  }
  .device-table td {
    padding: 4px 8px; color: #aaa; border-bottom: 1px solid #151515;
    font-variant-numeric: tabular-nums;
  }
  .device-table tr:hover td { color: #e0e0e0; background: #151515; }
  .kind-ap { color: #6a6; }
  .kind-device { color: #888; }

  .view-tabs { display: flex; gap: 0; }
  .view-tab {
    background: #0a0a0a; border: 1px solid #222; color: #666;
    padding: 5px 12px; cursor: pointer; font-family: inherit;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    border-right: none; transition: all 0.15s;
  }
  .view-tab:first-child { border-radius: 4px 0 0 4px; }
  .view-tab:last-child { border-radius: 0 4px 4px 0; border-right: 1px solid #222; }
  .view-tab.active { background: #1a1a1a; color: #fff; border-color: #444; }
  .view-tab:hover:not(.active) { color: #aaa; }

  .toast {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #1a1a1a; border: 1px solid #444; color: #ccc;
    padding: 8px 20px; border-radius: 4px; font-size: 12px;
    opacity: 0; transition: opacity 0.3s; pointer-events: none;
    z-index: 100;
  }
  .toast.show { opacity: 1; }

  @media (max-width: 700px) {
    .container { flex-direction: column; }
    .epd-panel { border-right: none; border-bottom: 1px solid #222; }
    .sidebar { padding: 12px; }
    .setting-row select { width: 180px; }
  }
</style>
</head>
<body>
<header>
  <h1>AirPrint</h1>
  <span class="status">
    <span class="dot" id="statusDot"></span>
    <span id="statusText">connecting...</span>
  </span>
</header>
<div class="container">
  <div class="epd-panel">
    <div class="epd-frame">
      <img id="epdImg" class="size-lg" alt="e-paper display" onclick="toggleSize()">
    </div>
    <div class="epd-label" id="epdLabel" onclick="toggleSize()">loading...</div>
    <div class="controls">
      <button class="btn" onclick="doAction('scan')">Scan Now</button>
      <button class="btn" onclick="doAction('flip')">Flip</button>
      <button class="btn danger" onclick="doAction('clear_exit')">Clear &amp; Exit</button>
    </div>
    <div class="view-tabs" style="margin-top:10px">
      <button class="view-tab active" data-view="radar" onclick="setView('radar')">Radar</button>
      <button class="view-tab" data-view="list" onclick="setView('list')">List</button>
      <button class="view-tab" data-view="stats" onclick="setView('stats')">Stats</button>
    </div>
  </div>
  <div class="sidebar">
    <div class="card">
      <h2>Settings</h2>
      <div class="setting-row">
        <label>EPD Model</label>
        <select id="epdModel"></select>
      </div>
      <div class="setting-row">
        <label>Refresh interval (s)</label>
        <input id="setRefresh" type="number" min="10" max="300" value="30">
      </div>
      <div class="setting-row">
        <label>Scan duration (s)</label>
        <input id="setScan" type="number" min="3" max="60" value="12">
      </div>
      <div class="setting-row">
        <label>Device TTL (s)</label>
        <input id="setTTL" type="number" min="30" max="3600" value="300">
      </div>
      <div class="setting-row">
        <label>Full refresh every N</label>
        <input id="setFullRefresh" type="number" min="1" max="100" value="10">
      </div>
      <div style="margin-top:8px;text-align:right">
        <button class="btn" onclick="saveSettings()">Apply</button>
      </div>
    </div>
    <div class="card">
      <h2>Devices (<span id="devCount">0</span>)</h2>
      <table class="device-table">
        <thead id="devHead"><tr><th>MAC</th><th>RSSI</th><th>Ch</th><th>Type</th></tr></thead>
        <tbody id="devBody"></tbody>
      </table>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
const SIZES = ['md', 'lg', 'xl'];
const SIZE_LABELS = ['medium', 'large', 'x-large'];
let sizeIdx = 1;
let modelsPopulated = false;
let userInteracting = false;

const EPD_LABELS = {
  'epd2in13':    '2.13" e-Paper (122x250)',
  'epd2in13_V2': '2.13" e-Paper V2 (122x250)',
  'epd2in13_V3': '2.13" e-Paper V3 (122x250)',
  'epd2in13_V4': '2.13" e-Paper V4 (122x250)',
  'epd2in7':     '2.7" e-Paper (176x264)',
  'epd2in7_V2':  '2.7" e-Paper V2 (176x264)',
  'epd2in9_V2':  '2.9" e-Paper V2 (128x296)',
  'epd3in7':     '3.7" e-Paper (280x480)',
  'epd7in5':     '7.5" e-Paper (800x480)',
  'epd7in5_V2':  '7.5" e-Paper V2 (800x480)',
};

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

function toggleSize() {
  sizeIdx = (sizeIdx + 1) % SIZES.length;
  document.getElementById('epdImg').className = 'size-' + SIZES[sizeIdx];
  toast('Display size: ' + SIZE_LABELS[sizeIdx]);
}

// Track focus on inputs/selects to pause overwriting values
document.addEventListener('focusin', function(e) {
  if (e.target.matches('.setting-row input, .setting-row select')) {
    userInteracting = true;
  }
});
document.addEventListener('focusout', function(e) {
  if (e.target.matches('.setting-row input, .setting-row select')) {
    userInteracting = false;
  }
});

async function fetchState() {
  try {
    const r = await fetch('/api/state');
    const s = await r.json();

    document.getElementById('statusDot').style.background = '#2a2';
    document.getElementById('statusText').textContent =
      s.device_count + ' devices | frame #' + s.frame_count +
      ' | ' + s.display_size[0] + 'x' + s.display_size[1];
    document.getElementById('epdLabel').textContent =
      (EPD_LABELS[s.epd_model] || s.epd_model) +
      ' — click to resize (' + SIZE_LABELS[sizeIdx] + ')';
    document.getElementById('devCount').textContent = s.device_count;

    // Update image
    if (s.image) {
      document.getElementById('epdImg').src = 'data:image/png;base64,' + s.image;
    }

    // Populate EPD model dropdown once
    const sel = document.getElementById('epdModel');
    if (!modelsPopulated && s.supported_models) {
      sel.innerHTML = '';
      var opt = document.createElement('option');
      opt.value = 'auto';
      opt.textContent = 'Auto-detect';
      sel.appendChild(opt);
      for (var i = 0; i < s.supported_models.length; i++) {
        var m = s.supported_models[i];
        opt = document.createElement('option');
        opt.value = m;
        opt.textContent = EPD_LABELS[m] || m;
        sel.appendChild(opt);
      }
      modelsPopulated = true;
    }

    // Only update form values when user is NOT interacting
    if (!userInteracting) {
      sel.value = s.epd_model;
      document.getElementById('setRefresh').value = s.refresh_seconds;
      document.getElementById('setScan').value = s.scan_seconds;
      document.getElementById('setTTL').value = s.state_ttl_seconds;
      document.getElementById('setFullRefresh').value = s.full_refresh_interval;
    }

    // View tabs
    var tabs = document.querySelectorAll('.view-tab');
    for (var j = 0; j < tabs.length; j++) {
      var active = tabs[j].getAttribute('data-view') === s.current_view;
      if (active) tabs[j].classList.add('active');
      else tabs[j].classList.remove('active');
    }

    // Device table — escaped
    // Update table header based on dual-antenna mode
    var thead = document.getElementById('devHead');
    if (s.dual_antenna) {
      thead.innerHTML = '<tr><th>Vendor</th><th>RSSI</th><th>A1</th><th>A2</th><th>Ch</th><th>Name</th><th></th></tr>';
    } else {
      thead.innerHTML = '<tr><th>Vendor</th><th>RSSI</th><th>Ch</th><th>Name</th><th></th></tr>';
    }

    var tbody = document.getElementById('devBody');
    var devs = s.devices.slice().sort(function(a, b) { return b.rssi - a.rssi; });
    var rows = '';
    for (var k = 0; k < devs.length; k++) {
      var d = devs[k];
      var trend = d.trend || 0;
      var trendIcon = trend > 0.3 ? '&uarr;' : (trend < -0.3 ? '&darr;' : '&ndash;');
      var trendColor = trend > 0.3 ? '#6a6' : (trend < -0.3 ? '#a66' : '#666');
      var vendorLabel = d.vendor ? esc(d.vendor) : esc(d.mac.slice(-8));
      var kindDot = d.kind === 'ap' ? '<span style="color:#6a6">&#9632;</span>' : '<span style="color:#888">&#9679;</span>';
      var name = d.ssid ? esc(d.ssid) : (d.probed_ssids && d.probed_ssids.length ? '<span style="color:#666">' + esc(d.probed_ssids[0]) + '</span>' : '');
      if (s.dual_antenna) {
        var a1 = d.rssi_ant1 != null ? d.rssi_ant1 : '-';
        var a2 = d.rssi_ant2 != null ? d.rssi_ant2 : '-';
        rows += '<tr>' +
          '<td>' + kindDot + ' ' + vendorLabel + '</td>' +
          '<td>' + d.rssi + '</td>' +
          '<td>' + a1 + '</td>' +
          '<td>' + a2 + '</td>' +
          '<td>' + d.channel + '</td>' +
          '<td>' + name + '</td>' +
          '<td style="color:' + trendColor + '">' + trendIcon + '</td>' +
          '</tr>';
      } else {
        rows += '<tr>' +
          '<td>' + kindDot + ' ' + vendorLabel + '</td>' +
          '<td>' + d.rssi + '</td>' +
          '<td>' + d.channel + '</td>' +
          '<td>' + name + '</td>' +
          '<td style="color:' + trendColor + '">' + trendIcon + '</td>' +
          '</tr>';
      }
    }
    tbody.innerHTML = rows;
  } catch (e) {
    document.getElementById('statusDot').style.background = '#a22';
    document.getElementById('statusText').textContent = 'disconnected';
  }
}

async function doAction(action) {
  try {
    await fetch('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: action})
    });
    toast('Action: ' + action);
  } catch (e) {
    toast('Failed: ' + action);
  }
  setTimeout(fetchState, 500);
}

async function setView(view) {
  try {
    await fetch('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'set_view', view: view})
    });
  } catch (e) {
    toast('Failed to switch view');
  }
  setTimeout(fetchState, 500);
}

async function saveSettings() {
  try {
    var r = await fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        epd_model: document.getElementById('epdModel').value,
        refresh_seconds: parseInt(document.getElementById('setRefresh').value),
        scan_seconds: parseInt(document.getElementById('setScan').value),
        state_ttl_seconds: parseInt(document.getElementById('setTTL').value),
        full_refresh_interval: parseInt(document.getElementById('setFullRefresh').value)
      })
    });
    var result = await r.json();
    if (result.restart_required) {
      toast('EPD model changed — reinitializing...');
      document.getElementById('statusDot').style.background = '#aa2';
      document.getElementById('statusText').textContent = 'reinitializing display...';
    } else {
      toast('Settings applied');
    }
  } catch (e) {
    toast('Failed to save settings');
  }
  setTimeout(fetchState, 2000);
}

fetchState();
setInterval(fetchState, 5000);
</script>
</body>
</html>
"""
