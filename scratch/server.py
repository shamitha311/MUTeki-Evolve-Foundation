"""scratch/server.py — Mock Muteki SSE server for UI development and demo.

Streams a pre-scripted, authentic Muteki event sequence on:
  GET /api/runs/{run_id}/events   (SSE stream)
  POST /api/runs                  (mint a new run_id)
  POST /api/runs/{run_id}/start   (accept + log a start payload)
  GET  /api/runs/{run_id}/status  (simple status endpoint)
  GET  /                          (self-contained dashboard HTML)

This server does NOT require a live Muteki backend, Docker, or LLM.
It is intended solely for frontend development and live-data path testing.

Usage:
    python scratch/server.py              # default port 8080
    SCRATCH_PORT=9000 python scratch/server.py

Then open http://127.0.0.1:8080 in your browser to see the live dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PORT = int(os.environ.get("SCRATCH_PORT", "8080"))


# ---------------------------------------------------------------------------
# Authentic Muteki SSE event sequence
# ---------------------------------------------------------------------------

RUN_ID = f"ev-demo-{uuid.uuid4().hex[:8]}"

def _make_sse_events(run_id: str) -> list[dict[str, Any]]:
    """Build a realistic Muteki SSE event sequence for testphp.vulnweb.com."""
    now = time.time()
    return [
        {
            "seq": 1, "event_type": "run.started", "ts": now,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": None, "payload": {"target": "http://testphp.vulnweb.com"},
        },
        {
            "seq": 2, "event_type": "worker.status", "ts": now + 0.5,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {"online": True, "engine": "grok", "reason": "grok worker spawned with XAI_API_KEY"},
        },
        {
            "seq": 3, "event_type": "blackboard.delta", "ts": now + 1.2,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {
                "kind": "fact_added",
                "fact": "Target http://testphp.vulnweb.com active: HTTP 200 OK | PHP/8.1, Nginx | Form endpoints at /login.php and /userinfo.php.",
            },
        },
        {
            "seq": 4, "event_type": "blackboard.delta", "ts": now + 2.1,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {
                "kind": "intent_proposed",
                "goal": "Test SQL injection vector at http://testphp.vulnweb.com/userinfo.php?uname=",
            },
        },
        {
            "seq": 5, "event_type": "blackboard.delta", "ts": now + 2.8,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {
                "kind": "intent_claimed",
                "goal": "Test SQL injection vector at http://testphp.vulnweb.com/userinfo.php?uname=",
            },
        },
        {
            "seq": 6, "event_type": "tool.result", "ts": now + 4.0,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {
                "tool": "http_request",
                "result": (
                    "GET http://testphp.vulnweb.com/userinfo.php?uname=admin'-- → 200 OK\n"
                    "Database response: UNION SELECT NULL,uname,pass FROM users → user 'test' hash returned."
                ),
            },
        },
        {
            "seq": 7, "event_type": "blackboard.delta", "ts": now + 5.3,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {
                "kind": "fact_added",
                "fact": "SQL injection verified on testphp.vulnweb.com: database columns extracted successfully.",
            },
        },
        {
            "seq": 8, "event_type": "blackboard.delta", "ts": now + 6.0,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {
                "kind": "flag_found",
                "flag": "flag{testphp_vulnweb_sql_injection_verified}",
            },
        },
        {
            "seq": 9, "event_type": "flag.accepted", "ts": now + 6.5,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {
                "flag": "flag{testphp_vulnweb_sql_injection_verified}",
                "verified": True,
            },
        },
        {
            "seq": 10, "event_type": "run.finished", "ts": now + 7.0,
            "run_id": run_id, "challenge_id": "vulnweb-testphp",
            "solver_id": "grok-local-1",
            "payload": {
                "solved": True,
                "flags": ["flag{testphp_vulnweb_sql_injection_verified}"],
                "reason": "Target http://testphp.vulnweb.com assessment completed successfully (Score 100/100)",
            },
        },
    ]


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MUTeki-Evolve — Live Investigation Dashboard</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

    :root {
      --bg: #0a0e1a;
      --surface: #111827;
      --surface2: #1a2235;
      --border: #1e2d45;
      --accent: #3b82f6;
      --accent2: #8b5cf6;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --text: #e2e8f0;
      --text-muted: #64748b;
      --text-dim: #94a3b8;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', sans-serif;
      min-height: 100vh;
      padding: 0;
    }

    /* Header */
    .header {
      background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%);
      border-bottom: 1px solid var(--border);
      padding: 20px 32px;
      display: flex;
      align-items: center;
      gap: 16px;
    }

    .header-logo {
      width: 40px; height: 40px;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      font-size: 20px; font-weight: 700; color: #fff;
    }

    .header-title { font-size: 20px; font-weight: 700; }
    .header-subtitle { font-size: 13px; color: var(--text-muted); margin-top: 2px; }

    .header-status {
      margin-left: auto;
      display: flex; align-items: center; gap: 8px;
      font-size: 13px; color: var(--text-dim);
    }

    .status-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--text-muted);
      transition: background 0.3s;
    }
    .status-dot.live { background: var(--success); animation: pulse 2s infinite; }
    .status-dot.solved { background: var(--success); }
    .status-dot.error { background: var(--danger); }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    /* Layout */
    .main { display: grid; grid-template-columns: 1fr 340px; gap: 0; height: calc(100vh - 81px); }

    /* Timeline panel */
    .timeline-panel {
      padding: 24px;
      overflow-y: auto;
      border-right: 1px solid var(--border);
    }

    .panel-header {
      font-size: 12px; font-weight: 600; letter-spacing: 0.08em;
      text-transform: uppercase; color: var(--text-muted);
      margin-bottom: 16px;
      display: flex; align-items: center; gap: 8px;
    }

    .event-count {
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 2px 8px; font-size: 11px;
      color: var(--text-dim);
    }

    /* Event cards */
    .event-list { display: flex; flex-direction: column; gap: 8px; }

    .event-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 12px;
      animation: slideIn 0.3s ease;
    }

    @keyframes slideIn {
      from { opacity: 0; transform: translateY(-8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .event-icon {
      width: 32px; height: 32px; border-radius: 8px;
      display: flex; align-items: center; justify-content: center;
      font-size: 15px; flex-shrink: 0;
    }

    .event-body {}
    .event-type {
      font-size: 11px; font-weight: 600; letter-spacing: 0.05em;
      font-family: 'JetBrains Mono', monospace;
      color: var(--text-muted);
      margin-bottom: 4px;
    }
    .event-summary { font-size: 13px; line-height: 1.5; color: var(--text); }
    .event-meta {
      font-size: 11px; color: var(--text-muted); margin-top: 6px;
      display: flex; gap: 12px;
    }

    /* Event type colors */
    .ev-run-started    { background: rgba(59,130,246,0.15); }
    .ev-worker-status  { background: rgba(99,102,241,0.15); }
    .ev-blackboard     { background: rgba(139,92,246,0.15); }
    .ev-tool           { background: rgba(20,184,166,0.15); }
    .ev-flag-accepted  { background: rgba(251,191,36,0.15); }
    .ev-run-finished   { background: rgba(16,185,129,0.15); }
    .ev-default        { background: rgba(100,116,139,0.15); }

    /* Side panel */
    .side-panel { padding: 24px; overflow-y: auto; }

    /* Score card */
    .score-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 20px;
    }

    .score-label { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 12px; }

    .score-flag {
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--warning);
      word-break: break-all;
      margin-bottom: 10px;
      display: none;
    }

    .score-flag.visible { display: block; animation: slideIn 0.4s ease; }

    .solved-badge {
      display: none;
      background: linear-gradient(135deg, rgba(16,185,129,0.2), rgba(5,150,105,0.1));
      border: 1px solid rgba(16,185,129,0.4);
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 13px; font-weight: 600; color: var(--success);
      text-align: center;
    }
    .solved-badge.visible { display: block; animation: slideIn 0.4s ease; }

    /* Target info */
    .target-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 20px;
    }

    .target-url {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px; color: var(--accent);
      margin-top: 8px;
    }

    .info-row {
      display: flex; justify-content: space-between;
      font-size: 12px; color: var(--text-dim);
      margin-top: 8px;
    }

    .info-val { color: var(--text); font-weight: 500; }

    /* Run button */
    .run-btn {
      width: 100%;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      color: #fff; border: none;
      border-radius: 10px; padding: 12px;
      font-size: 14px; font-weight: 600; cursor: pointer;
      transition: opacity 0.2s, transform 0.1s;
      margin-bottom: 16px;
    }
    .run-btn:hover { opacity: 0.9; transform: translateY(-1px); }
    .run-btn:active { transform: translateY(0); }
    .run-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  </style>
</head>
<body>
  <div class="header">
    <div class="header-logo">M</div>
    <div>
      <div class="header-title">MUTeki-Evolve</div>
      <div class="header-subtitle">Codex Swarm Investigation Dashboard</div>
    </div>
    <div class="header-status">
      <div class="status-dot" id="statusDot"></div>
      <span id="statusText">Idle</span>
    </div>
  </div>

  <div class="main">
    <div class="timeline-panel">
      <div class="panel-header">
        Investigation Timeline
        <span class="event-count" id="eventCount">0 events</span>
      </div>
      <div class="event-list" id="eventList"></div>
    </div>

    <div class="side-panel">
      <div class="target-card">
        <div class="score-label">Target</div>
        <div class="target-url">http://testphp.vulnweb.com</div>
        <div class="info-row"><span>Engine</span><span class="info-val">codex</span></div>
        <div class="info-row"><span>Run ID</span><span class="info-val" id="runIdDisplay">—</span></div>
        <div class="info-row"><span>Elapsed</span><span class="info-val" id="elapsedDisplay">0s</span></div>
      </div>

      <button class="run-btn" id="runBtn" onclick="startRun()">▶ Start Investigation</button>

      <div class="score-card">
        <div class="score-label">Results</div>
        <div class="score-flag" id="flagDisplay"></div>
        <div class="solved-badge" id="solvedBadge">✓ Investigation Solved</div>
      </div>
    </div>
  </div>

  <script>
    const EVENT_ICONS = {
      'run.started':       { icon: '🚀', cls: 'ev-run-started' },
      'worker.status':     { icon: '🤖', cls: 'ev-worker-status' },
      'blackboard.delta':  { icon: '📋', cls: 'ev-blackboard' },
      'tool.result':       { icon: '🔧', cls: 'ev-tool' },
      'tool.start':        { icon: '🔧', cls: 'ev-tool' },
      'flag.accepted':     { icon: '🚩', cls: 'ev-flag-accepted' },
      'run.finished':      { icon: '✅', cls: 'ev-run-finished' },
    };

    let startTime = null;
    let elapsedTimer = null;
    let eventCount = 0;
    let currentRunId = null;

    function formatElapsed(ms) {
      const s = Math.floor(ms / 1000);
      return s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`;
    }

    function setStatus(state, text) {
      const dot = document.getElementById('statusDot');
      const statusText = document.getElementById('statusText');
      dot.className = 'status-dot' + (state ? ' ' + state : '');
      statusText.textContent = text;
    }

    function summarizePayload(eventType, payload) {
      if (!payload) return '';
      if (eventType === 'run.started') return 'Investigation run started';
      if (eventType === 'worker.status') {
        const eng = payload.engine || '';
        const online = payload.online;
        return online ? `Worker online: ${eng}` : `Worker offline: ${eng}`;
      }
      if (eventType === 'blackboard.delta') {
        const kind = payload.kind || '';
        if (kind === 'fact_added' && payload.fact) return `Fact: ${payload.fact.substring(0, 120)}`;
        if (kind === 'intent_proposed' && payload.goal) return `Direction proposed: ${payload.goal.substring(0, 120)}`;
        if (kind === 'intent_claimed' && payload.goal) return `Worker claimed: ${payload.goal.substring(0, 120)}`;
        if (kind === 'flag_found' && payload.flag) return `Flag recorded: ${payload.flag}`;
        return `Blackboard update: ${kind}`;
      }
      if (eventType === 'tool.result') {
        return `Tool: ${payload.tool || 'unknown'} → ${(payload.result || '').substring(0, 100)}`;
      }
      if (eventType === 'flag.accepted') return `Flag accepted: ${payload.flag || ''}`;
      if (eventType === 'run.finished') {
        return payload.solved
          ? `Investigation solved — ${payload.reason || ''}`
          : `Finished: ${payload.reason || 'no flag captured'}`;
      }
      return JSON.stringify(payload).substring(0, 100);
    }

    function addEventCard(data) {
      const list = document.getElementById('eventList');
      const card = document.createElement('div');
      card.className = 'event-card';

      const et = data.event_type || 'unknown';
      const iconInfo = EVENT_ICONS[et] || { icon: '📡', cls: 'ev-default' };
      const summary = summarizePayload(et, data.payload);
      const ts = data.ts ? new Date(data.ts * 1000).toLocaleTimeString() : '';

      card.innerHTML = `
        <div class="event-icon ${iconInfo.cls}">${iconInfo.icon}</div>
        <div class="event-body">
          <div class="event-type">${et}</div>
          <div class="event-summary">${summary}</div>
          <div class="event-meta">
            <span>seq ${data.seq || '?'}</span>
            ${data.solver_id ? `<span>${data.solver_id}</span>` : ''}
            <span>${ts}</span>
          </div>
        </div>`;

      list.appendChild(card);
      card.scrollIntoView({ behavior: 'smooth', block: 'end' });

      eventCount++;
      document.getElementById('eventCount').textContent = `${eventCount} event${eventCount !== 1 ? 's' : ''}`;

      // Handle flag.accepted
      if (et === 'flag.accepted' && data.payload && data.payload.flag) {
        const flagEl = document.getElementById('flagDisplay');
        flagEl.textContent = data.payload.flag;
        flagEl.classList.add('visible');
      }

      // Handle run.finished
      if (et === 'run.finished') {
        if (data.payload && data.payload.solved) {
          document.getElementById('solvedBadge').classList.add('visible');
          setStatus('solved', 'Solved');
        } else {
          setStatus('', 'Finished');
        }
        document.getElementById('runBtn').disabled = false;
        if (elapsedTimer) clearInterval(elapsedTimer);
      }
    }

    async function startRun() {
      // Reset UI
      document.getElementById('eventList').innerHTML = '';
      document.getElementById('flagDisplay').classList.remove('visible');
      document.getElementById('flagDisplay').textContent = '';
      document.getElementById('solvedBadge').classList.remove('visible');
      eventCount = 0;
      document.getElementById('eventCount').textContent = '0 events';
      document.getElementById('runBtn').disabled = true;
      setStatus('live', 'Running');

      // Mint run ID
      const mintResp = await fetch('/api/runs', { method: 'POST' });
      const mintData = await mintResp.json();
      currentRunId = mintData.run_id;
      document.getElementById('runIdDisplay').textContent = currentRunId.substring(0, 14) + '…';

      // Start run
      await fetch(`/api/runs/${currentRunId}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ engines: ['codex'], challenge: { id: currentRunId } }),
      });

      // Track elapsed
      startTime = Date.now();
      if (elapsedTimer) clearInterval(elapsedTimer);
      elapsedTimer = setInterval(() => {
        document.getElementById('elapsedDisplay').textContent = formatElapsed(Date.now() - startTime);
      }, 500);

      // Connect SSE
      const es = new EventSource(`/api/runs/${currentRunId}/events`);
      es.onmessage = (e) => {
        try { addEventCard(JSON.parse(e.data)); } catch {}
      };
      es.onerror = () => {
        es.close();
        setStatus('', 'Stream closed');
        document.getElementById('runBtn').disabled = false;
        if (elapsedTimer) clearInterval(elapsedTimer);
      };
    }
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
        LOG.info(fmt % args)

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/":
            self._send_html(_DASHBOARD_HTML)
            return

        if path.endswith("/events"):
            # SSE stream
            parts = path.strip("/").split("/")
            run_id = parts[2] if len(parts) >= 3 else RUN_ID
            self._stream_events(run_id)
            return

        if path.endswith("/status"):
            parts = path.strip("/").split("/")
            run_id = parts[2] if len(parts) >= 3 else RUN_ID
            self._send_json({"run_id": run_id, "status": "running"})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/api/runs":
            run_id = f"ev-demo-{uuid.uuid4().hex[:8]}"
            LOG.info("Minted run_id: %s", run_id)
            self._send_json({"run_id": run_id})
            return

        if path.endswith("/start"):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                payload = json.loads(body)
                engines = payload.get("engines", [])
                LOG.info("Run start request engines=%s", engines)
            except json.JSONDecodeError:
                pass
            self._send_json({"status": "started"})
            return

        self.send_response(404)
        self.end_headers()

    def _stream_events(self, run_id: str) -> None:
        """Stream authentic Muteki SSE event frames with realistic timing."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        events = _make_sse_events(run_id)
        delays = [0.0, 0.5, 1.2, 2.1, 2.8, 4.0, 5.3, 6.0, 6.5, 7.0]

        prev = 0.0
        for event, ts_abs in zip(events, delays):
            delay = ts_abs - prev
            prev = ts_abs
            if delay > 0:
                time.sleep(delay)
            try:
                data_json = json.dumps(event)
                frame = (
                    f"id: {event['seq']}\n"
                    f"event: {event['event_type']}\n"
                    f"data: {data_json}\n\n"
                )
                self.wfile.write(frame.encode())
                self.wfile.flush()
                LOG.info("SSE → seq=%d event=%s", event["seq"], event["event_type"])
            except BrokenPipeError:
                LOG.info("Client disconnected during SSE stream")
                return


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    LOG.info("Mock Muteki SSE server running at http://127.0.0.1:%d", PORT)
    LOG.info("Open http://127.0.0.1:%d in your browser to see the live dashboard", PORT)
    LOG.info("SSE endpoint: GET http://127.0.0.1:%d/api/runs/<run_id>/events", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Server stopped.")


if __name__ == "__main__":
    main()
