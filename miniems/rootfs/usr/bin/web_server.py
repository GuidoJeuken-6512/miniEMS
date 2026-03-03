"""FastAPI ingress dashboard for miniEMS."""
import asyncio
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, BaseLoader

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inline Jinja2 template (no extra static files needed)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>miniEMS Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #0d1117; color: #e6edf3; min-height: 100vh; padding: 1.5rem; }
    h1 { font-size: 1.5rem; margin-bottom: 1.5rem; color: #58a6ff; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
    .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; }
    .card .label { font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; }
    .card .value { font-size: 1.75rem; font-weight: 700; margin-top: 0.25rem; }
    .card .unit { font-size: 0.8rem; color: #8b949e; }
    .mode-badge { display: inline-block; padding: 0.4rem 1rem; border-radius: 999px; font-weight: 600; font-size: 0.9rem; margin-bottom: 1.5rem; }
    .mode-IDLE { background: #21262d; color: #8b949e; }
    .mode-PV { background: #1a4a1a; color: #3fb950; }
    .mode-GRID { background: #1a2a4a; color: #58a6ff; }
    .mode-PROTECT { background: #4a1a1a; color: #f85149; }
    .section-title { font-size: 0.9rem; color: #8b949e; margin-bottom: 0.75rem; }
    .savings-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
    .savings-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; }
    .savings-card .slabel { font-size: 0.75rem; color: #8b949e; }
    .savings-card .svalue { font-size: 1.25rem; font-weight: 700; color: #3fb950; margin-top: 0.25rem; }
    .cheap { color: #3fb950; }
    .expensive { color: #f85149; }
    footer { font-size: 0.75rem; color: #8b949e; margin-top: 2rem; }
    .refresh-hint { font-size: 0.75rem; color: #8b949e; margin-bottom: 1rem; }
  </style>
  <script>
    async function refresh() {
      try {
        const r = await fetch('api/status');
        const d = await r.json();
        document.getElementById('dashboard').innerHTML = renderDashboard(d);
      } catch(e) { console.error(e); }
    }
    function fmt(v, dec=1) { return v !== null && v !== undefined ? Number(v).toFixed(dec) : '–'; }
    function fmtMode(m) {
      if (!m) return '';
      if (m.includes('PV')) return 'mode-PV';
      if (m.includes('Grid')) return 'mode-GRID';
      if (m.includes('Protect')) return 'mode-PROTECT';
      return 'mode-IDLE';
    }
    function renderDashboard(d) {
      return `
        <div class="mode-badge ${fmtMode(d.mode)}">${d.mode || 'Unknown'}</div>
        <div class="grid">
          <div class="card"><div class="label">PV Power</div><div class="value">${fmt(d.pv_power_w,0)}</div><div class="unit">W</div></div>
          <div class="card"><div class="label">Load Power</div><div class="value">${fmt(d.load_power_w,0)}</div><div class="unit">W</div></div>
          <div class="card"><div class="label">Grid Power</div><div class="value">${fmt(d.grid_power_w,0)}</div><div class="unit">W</div></div>
          <div class="card"><div class="label">Battery SoC</div><div class="value">${fmt(d.battery_soc_pct,0)}</div><div class="unit">%</div></div>
          <div class="card"><div class="label">Battery Power</div><div class="value">${fmt(d.battery_power_w,0)}</div><div class="unit">W</div></div>
          <div class="card"><div class="label">Electricity Price</div>
            <div class="value ${d.is_cheap_rate ? 'cheap' : 'expensive'}">${fmt(d.electricity_price_eur,4)}</div>
            <div class="unit">€/kWh ${d.is_cheap_rate ? '✓ cheap' : ''}</div>
          </div>
        </div>
        <div class="section-title">Cost &amp; Savings</div>
        <div class="savings-grid">
          <div class="savings-card"><div class="slabel">Saved Today (PV)</div><div class="svalue">€${fmt(d.today_pv_saved_eur,4)}</div></div>
          <div class="savings-card"><div class="slabel">Grid Cost Today</div><div class="svalue" style="color:#f85149">€${fmt(d.today_grid_cost_eur,4)}</div></div>
          <div class="savings-card"><div class="slabel">PV Used Today</div><div class="svalue">${fmt(d.today_pv_used_kwh,3)} kWh</div></div>
          <div class="savings-card"><div class="slabel">Grid Import Today</div><div class="svalue" style="color:#8b949e">${fmt(d.today_grid_import_kwh,3)} kWh</div></div>
          <div class="savings-card"><div class="slabel">Saved This Week</div><div class="svalue">€${fmt(d.week_pv_saved_eur,4)}</div></div>
          <div class="savings-card"><div class="slabel">Grid Cost This Week</div><div class="svalue" style="color:#f85149">€${fmt(d.week_grid_cost_eur,4)}</div></div>
        </div>
      `;
    }
    setInterval(refresh, 5000);
    window.onload = refresh;
  </script>
</head>
<body>
  <h1>&#9889; miniEMS Dashboard</h1>
  <p class="refresh-hint">Auto-refreshes every 5 seconds</p>
  <div id="dashboard">Loading…</div>
  <footer>miniEMS v0.1.0 &middot; Home Assistant Add-on</footer>
</body>
</html>
"""


def create_app(status_store: dict[str, Any]) -> FastAPI:
    """Create FastAPI app; status_store is a shared dict updated by EMS loop."""
    app = FastAPI(title="miniEMS", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return HTMLResponse(content=_DASHBOARD_HTML)

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return status_store

    return app
