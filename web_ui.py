"""Lightweight web UI for AirPrint — mirrors e-paper and exposes settings."""

from __future__ import annotations

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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        logging.debug("web: %s", fmt % args)

    def do_GET(self) -> None:
        if self.path == "/":
            self._serve_html()
        elif self.path == "/frame.png":
            self._serve_frame()
        elif self.path == "/api/state":
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

    def _serve_frame(self) -> None:
        app = _app
        if app is None or app.last_frame is None:
            self.send_error(204, "No frame yet")
            return
        buf = io.BytesIO()
        img = app.last_frame.convert("L")
        img.save(buf, format="PNG")
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_state(self) -> None:
        app = _app
        if app is None:
            self.send_error(503)
            return
        w, h = app.get_display_size()
        devices = []
        for d in app.devices.values():
            devices.append({
                "mac": d.mac,
                "rssi": d.rssi,
                "channel": d.channel,
                "kind": d.kind,
                "last_seen": d.last_seen,
            })
        state = {
            "epd_model": app.epd_model,
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

        if "refresh_seconds" in body:
            app.refresh_seconds = max(10, int(body["refresh_seconds"]))
        if "scan_seconds" in body:
            app.scan_seconds = max(3, int(body["scan_seconds"]))
        if "state_ttl_seconds" in body:
            app.state_ttl_seconds = max(30, int(body["state_ttl_seconds"]))
        if "full_refresh_interval" in body:
            app._full_refresh_interval = max(1, int(body["full_refresh_interval"]))

        self._json_response(200, {"ok": True})

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
  header .status .dot { display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; background: #2a2; margin-right: 4px; vertical-align: middle; }
  .container { display: flex; gap: 0; min-height: calc(100vh - 49px); }

  /* E-paper mirror panel */
  .epd-panel {
    flex: 0 0 auto; background: #111; border-right: 1px solid #222;
    display: flex; flex-direction: column; align-items: center;
    padding: 20px;
  }
  .epd-frame {
    background: #fff; border-radius: 4px; padding: 8px;
    box-shadow: 0 2px 16px rgba(0,0,0,0.5);
  }
  .epd-frame img {
    display: block; image-rendering: pixelated;
  }
  .epd-label { font-size: 11px; color: #555; margin-top: 8px; text-align: center; }

  /* Controls */
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

  /* Right sidebar */
  .sidebar {
    flex: 1; padding: 20px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 16px;
  }
  .card {
    background: #111; border: 1px solid #222; border-radius: 6px;
    padding: 14px;
  }
  .card h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
    color: #666; margin-bottom: 10px; }
  .setting-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 0; border-bottom: 1px solid #1a1a1a;
  }
  .setting-row:last-child { border-bottom: none; }
  .setting-row label { font-size: 13px; color: #aaa; }
  .setting-row input, .setting-row select {
    background: #0a0a0a; border: 1px solid #333; color: #e0e0e0;
    padding: 4px 8px; border-radius: 3px; font-family: inherit;
    font-size: 13px; width: 100px; text-align: right;
  }
  .setting-row select { width: 140px; text-align: left; }
  .setting-row input:focus, .setting-row select:focus {
    outline: none; border-color: #555;
  }

  /* Device list */
  .device-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .device-table th {
    text-align: left; color: #666; font-weight: 500; padding: 4px 8px;
    border-bottom: 1px solid #222;
  }
  .device-table td { padding: 4px 8px; color: #aaa; border-bottom: 1px solid #151515; }
  .device-table tr:hover td { color: #e0e0e0; background: #151515; }
  .kind-ap { color: #6a6; }
  .kind-device { color: #888; }

  /* View tabs */
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

  @media (max-width: 700px) {
    .container { flex-direction: column; }
    .epd-panel { border-right: none; border-bottom: 1px solid #222; }
    .sidebar { padding: 12px; }
  }
</style>
</head>
<body>
<header>
  <h1>AirPrint</h1>
  <span class="status"><span class="dot" id="statusDot"></span><span id="statusText">connecting...</span></span>
</header>
<div class="container">
  <div class="epd-panel">
    <div class="epd-frame">
      <img id="epdImg" src="/frame.png" alt="e-paper">
    </div>
    <div class="epd-label" id="epdLabel">loading...</div>
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
        <select id="epdModel" disabled><option>-</option></select>
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
        <thead><tr><th>MAC</th><th>RSSI</th><th>Ch</th><th>Type</th></tr></thead>
        <tbody id="devBody"></tbody>
      </table>
    </div>
  </div>
</div>
<script>
let refreshTimer;

async function fetchState() {
  try {
    const r = await fetch('/api/state');
    const s = await r.json();
    document.getElementById('statusDot').style.background = '#2a2';
    document.getElementById('statusText').textContent =
      `${s.device_count} devices | frame #${s.frame_count} | ${s.display_size[0]}x${s.display_size[1]}`;
    document.getElementById('epdLabel').textContent =
      `${s.epd_model} (${s.display_size[0]}x${s.display_size[1]})`;
    document.getElementById('devCount').textContent = s.device_count;
    document.getElementById('setRefresh').value = s.refresh_seconds;
    document.getElementById('setScan').value = s.scan_seconds;
    document.getElementById('setTTL').value = s.state_ttl_seconds;
    document.getElementById('setFullRefresh').value = s.full_refresh_interval;

    // EPD model selector
    const sel = document.getElementById('epdModel');
    if (sel.options.length <= 1) {
      sel.innerHTML = '';
      for (const m of s.supported_models) {
        const o = document.createElement('option');
        o.value = m; o.textContent = m;
        if (m === s.epd_model) o.selected = true;
        sel.appendChild(o);
      }
    }

    // View tabs
    document.querySelectorAll('.view-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.view === s.current_view);
    });

    // Device table
    const body = document.getElementById('devBody');
    const sorted = s.devices.sort((a, b) => b.rssi - a.rssi);
    body.innerHTML = sorted.map(d =>
      `<tr><td>${d.mac}</td><td>${d.rssi}</td><td>${d.channel}</td>` +
      `<td class="kind-${d.kind}">${d.kind}</td></tr>`
    ).join('');

    // Refresh image
    document.getElementById('epdImg').src = '/frame.png?' + Date.now();
  } catch (e) {
    document.getElementById('statusDot').style.background = '#a22';
    document.getElementById('statusText').textContent = 'disconnected';
  }
}

async function doAction(action) {
  await fetch('/api/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action})
  });
  setTimeout(fetchState, 500);
}

async function setView(view) {
  await fetch('/api/action', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'set_view', view})
  });
  setTimeout(fetchState, 500);
}

async function saveSettings() {
  await fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      refresh_seconds: parseInt(document.getElementById('setRefresh').value),
      scan_seconds: parseInt(document.getElementById('setScan').value),
      state_ttl_seconds: parseInt(document.getElementById('setTTL').value),
      full_refresh_interval: parseInt(document.getElementById('setFullRefresh').value),
    })
  });
  fetchState();
}

fetchState();
refreshTimer = setInterval(fetchState, 5000);
</script>
</body>
</html>
"""
